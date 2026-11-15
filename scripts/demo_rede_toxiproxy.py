"""Demo de falhas de rede no Anel com o Toxiproxy, sobre o compose.

Sobe o Anel com o override `compose.toxiproxy.yaml`, semeia os proxies e mostra três efeitos:

1. Latência medida. De dentro do `no-anel-2`, cronometra GET /saude pela escuta do Nó 3 no
   Toxiproxy antes e depois de um toxic de `--latencia-ms` (300 ms por padrão, abaixo do
   Deadline de 1 s do compose). Uma eleição com o toxic converge sem suspeita.
2. Suspeita falsa. Com `--latencia-alta-ms` (1500 ms por padrão), acima do Deadline, o 2
   desiste do 3 e o salta, embora o 3 responda em /saude. A eleição converge para 5 assim
   mesmo, e sem o toxic a rodada seguinte zera a suspeita.
3. Partição do Líder. Desativa o proxy do Nó 5, e nenhuma Mensagem chega mais a ele. Uma
   eleição nova o salta e elege o maior id entre os outros, 4.

Sai 0 quando os três efeitos aparecem e 1 quando algum falta. Sem Docker, explica e sai 0.

Uso:
    python -m scripts.demo_rede_toxiproxy
    python -m scripts.demo_rede_toxiproxy --latencia-ms 500 --latencia-alta-ms 2000 --manter
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from src.comum.toxiproxy import PORTA_ANEL_BASE, ClienteToxiproxy, nome_proxy_anel, proxies_anel
from src.experimentos.conteineres import (
    COMPOSE,
    COMPOSE_TOXIPROXY,
    PORTAS_ANEL,
    SERVICOS_ANEL,
    SONDAGEM_S,
    compose,
    docker_disponivel,
    eleger,
    esperar_saude,
    http_json,
)

ARQUIVOS = [COMPOSE, COMPOSE_TOXIPROXY]
LIMPEZA = "docker compose -f compose.yaml -f compose.toxiproxy.yaml down -v --remove-orphans"
# O Nó 2 repassa ao 3 pela escuta do 3 no Toxiproxy, então o 2 observa e o 3 sofre o toxic.
OBSERVADOR, ALVO = 2, 3
# Dois Deadlines de repasse do compose (1 s cada) e uma folga. A suspeita só aparece depois de um
# Deadline sem resposta, e o repasse da LIDER ao 3 pode esperar outro Deadline na fila do 2.
JANELA_SUSPEITA_S = 2.5

_CODIGO_RTT = """\
import statistics, time, urllib.request
amostras = []
for _ in range({amostras}):
    inicio = time.monotonic()
    urllib.request.urlopen("http://toxiproxy:{porta}/saude", timeout=10).read()
    amostras.append((time.monotonic() - inicio) * 1000)
print(statistics.median(amostras))
"""


def _rtt_ms(origem: str, porta: int, amostras: int = 5) -> float:
    """Mediana, em ms, de GET /saude de dentro de `origem` pela escuta `porta` do Toxiproxy."""
    codigo = _CODIGO_RTT.format(amostras=amostras, porta=porta)
    r = compose("exec", "-T", origem, "python", "-c", codigo, arquivos=ARQUIVOS, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(
            f"docker compose exec {origem} falhou: {r.stderr.strip()}. Confira que o compose está "
            "no ar com o compose.toxiproxy.yaml e rode a demo de novo."
        )
    return float(r.stdout.strip())


def _eleger_e_ler(bases: dict[int, str]) -> dict:
    """Dispara uma eleição no Nó 1 e devolve o /estado do observador quando ele fecha a rodada.

    Esperar só o Líder não serve, porque os Nós já reportam 5 desde a primeira eleição.
    """
    antes = len(http_json(f"{bases[OBSERVADOR]}/estado")["rodadas_concluidas"])
    http_json(f"{bases[1]}/iniciar-eleicao", metodo="POST")
    limite = time.monotonic() + 30.0
    estado: dict = {}
    while time.monotonic() < limite:
        estado = http_json(f"{bases[OBSERVADOR]}/estado")
        if len(estado["rodadas_concluidas"]) > antes:
            return estado
        time.sleep(SONDAGEM_S)
    raise RuntimeError(
        f"o Nó {OBSERVADOR} não fechou a rodada em 30 s. Último /estado: {estado}. "
        f"Derrube a pilha com {LIMPEZA} e rode a demo de novo."
    )


def _mortos_depois_da_janela(bases: dict[int, str]) -> list[int]:
    """Os `mortos` do observador depois de a fila de envios dele ao alvo esvaziar.

    A espera é fixa porque confere também a ausência de suspeita, que nenhuma sondagem antecipa. Ela
    impede ainda que um envio pendente de uma etapa decida a leitura da etapa seguinte.
    """
    time.sleep(JANELA_SUSPEITA_S)
    return http_json(f"{bases[OBSERVADOR]}/estado")["mortos"]


def _demo(args: argparse.Namespace) -> int:
    if not docker_disponivel():
        print("Docker indisponível: nada a demonstrar (o teste de integração pula do mesmo modo).")
        return 0

    bases = {i: f"http://127.0.0.1:{porta}" for i, porta in PORTAS_ANEL.items()}
    proxy_alvo = nome_proxy_anel(ALVO)
    porta_alvo = PORTA_ANEL_BASE + ALVO
    falhas: list[str] = []

    print("== Subindo o compose do Anel com roteamento pelo Toxiproxy ==")
    compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
    up = compose("up", "-d", "--build", "--wait", "toxiproxy", *SERVICOS_ANEL, arquivos=ARQUIVOS)
    if up.returncode != 0:
        compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
        print(f"falha ao subir: {up.stderr}", file=sys.stderr)
        return 1

    try:
        tox = ClienteToxiproxy()
        tox.esperar_pronto()
        tox.semear(proxies_anel(tuple(PORTAS_ANEL)))
        print("  proxies semeados: anel-1..anel-5\n")

        print("== 1ª eleição (rede saudável) ==")
        lideres, _ = eleger(bases, list(PORTAS_ANEL), 5)
        print(f"  Líderes: {lideres}\n")
        if set(lideres.values()) != {5}:
            falhas.append(f"a primeira eleição não convergiu para 5: {lideres}")

        print(f"== Latência de {args.latencia_ms} ms nas respostas do Nó {ALVO} ==")
        rtt_antes = _rtt_ms(SERVICOS_ANEL[OBSERVADOR - 1], porta_alvo)
        tox.latencia(proxy_alvo, args.latencia_ms)
        rtt_com = _rtt_ms(SERVICOS_ANEL[OBSERVADOR - 1], porta_alvo)
        print(f"  GET /saude do {OBSERVADOR} ao {ALVO} pelo proxy: {rtt_antes:.1f} ms sem toxic, "
              f"{rtt_com:.1f} ms com toxic")
        if rtt_com < 0.9 * args.latencia_ms:
            falhas.append(f"o RTT com toxic ({rtt_com:.1f} ms) ficou abaixo de 90% de {args.latencia_ms} ms")
        _eleger_e_ler(bases)
        mortos = _mortos_depois_da_janela(bases)
        print(f"  eleição com o toxic: o {OBSERVADOR} suspeita de {mortos}, nenhuma suspeita esperada\n")
        if mortos != []:
            falhas.append(f"com {args.latencia_ms} ms, o {OBSERVADOR} suspeitou de {mortos}")

        print(f"== Latência de {args.latencia_alta_ms} ms, acima do Deadline ==")
        tox.limpar_toxics(proxy_alvo)
        tox.latencia(proxy_alvo, args.latencia_alta_ms)
        _eleger_e_ler(bases)
        mortos = _mortos_depois_da_janela(bases)
        vivo = esperar_saude(bases[ALVO], 5.0)
        print(f"  o {OBSERVADOR} suspeita de {mortos}, e o {ALVO} responde em /saude: {vivo}")
        if mortos == [ALVO] and vivo:
            print(f"  o {OBSERVADOR} suspeita do {ALVO}, que está vivo: suspeita falsa\n")
        else:
            falhas.append(
                f"com {args.latencia_alta_ms} ms, esperava o {OBSERVADOR} suspeitando só do {ALVO} vivo, "
                f"e veio mortos={mortos} e /saude do {ALVO} = {vivo}"
            )

        print("== Sem o toxic, a rodada seguinte zera a suspeita ==")
        tox.limpar_toxics(proxy_alvo)
        _eleger_e_ler(bases)
        mortos = _mortos_depois_da_janela(bases)
        print(f"  o {OBSERVADOR} suspeita de {mortos}\n")
        if mortos != []:
            falhas.append(f"sem toxic, o {OBSERVADOR} ainda suspeita de {mortos}")

        print("== Partição do Líder (Nó 5) e reeleição ==")
        tox.definir_habilitado(nome_proxy_anel(5), False)
        print("  proxy anel-5 desativado: nenhuma Mensagem chega mais ao 5.")
        vivos = [1, 2, 3, 4]
        lideres, _ = eleger(bases, vivos, 4)
        for i in vivos:
            est = http_json(f"{bases[i]}/estado")
            print(f"  no-anel-{i}: lider = {est['lider']}, mortos = {est.get('mortos')}")
        if set(lideres.values()) != {4}:
            falhas.append(f"com o 5 particionado, a reeleição não convergiu para 4: {lideres}")
    finally:
        if not args.manter:
            compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
        else:
            print(f"\n(--manter) compose deixado no ar. Derrube com:\n  {LIMPEZA}")

    if falhas:
        for falha in falhas:
            print(f"[demo] {falha}", file=sys.stderr)
        print("[demo] algum efeito não apareceu. Rode de novo: python -m scripts.demo_rede_toxiproxy",
              file=sys.stderr)
        return 1
    print("\n[demo] os três efeitos apareceram: latência medida, suspeita falsa e partição do Líder.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--latencia-ms", type=int, default=300, help="latência abaixo do Deadline, em ms")
    parser.add_argument("--latencia-alta-ms", type=int, default=1500, help="latência acima do Deadline, em ms")
    parser.add_argument("--manter", action="store_true", help="não derrubar o compose ao final")
    args = parser.parse_args()
    if args.latencia_alta_ms <= args.latencia_ms:
        parser.error("--latencia-alta-ms tem de ser maior que --latencia-ms")
    try:
        codigo = _demo(args)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as erro:
        print(f"[demo] {type(erro).__name__}: {erro}. Rode de novo: python -m scripts.demo_rede_toxiproxy",
              file=sys.stderr)
        codigo = 1
    raise SystemExit(codigo)


if __name__ == "__main__":
    main()
