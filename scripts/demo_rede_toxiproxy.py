"""Demo de falhas de rede com Toxiproxy sobre o compose.

Sobe o compose COM o override de roteamento pelo Toxiproxy, semeia os proxies e
demonstra dois Cenários de rede, imprimindo o efeito observável:

1. **Latência acima do Deadline.** Injeta 500 ms de latência na
   rota de um Nó cujo Deadline de repasse é 1.0 s por padrão — para o efeito
   ficar nítido, a demo mede o atraso de uma chamada de controle que atravessa
   o proxy. (No experimento da bateria, a latência é empurrada acima do Deadline
   e o `ClienteRede` devolve `EnvioTimeout`.)

2. **Partição do Líder do Anel.** Dispara uma eleição (Líder =
   5), desabilita o proxy do Nó 5 (partição) e dispara nova eleição: os Nós
   vivos reelegem o maior id vivo (4).

Requer Docker com Compose v2. Sem Docker, o script explica e sai com código 0
(nada a demonstrar), no mesmo espírito tolerante dos testes de compose.

Uso:
    python -m scripts.demo_rede_toxiproxy
    python -m scripts.demo_rede_toxiproxy --latencia-ms 800 --manter
"""

from __future__ import annotations

import argparse

from src.comum.toxiproxy import ClienteToxiproxy, proxies_anel
from src.experimentos.conteineres import (
    COMPOSE,
    COMPOSE_TOXIPROXY,
    PORTAS_ANEL,
    SERVICOS_ANEL,
    compose,
    docker_disponivel,
    eleger,
    http_json,
)

ARQUIVOS = [COMPOSE, COMPOSE_TOXIPROXY]


def _demo(args: argparse.Namespace) -> int:
    if not docker_disponivel():
        print("Docker indisponível: nada a demonstrar (o teste de integração pula do mesmo modo).")
        return 0

    bases = {i: f"http://127.0.0.1:{porta}" for i, porta in PORTAS_ANEL.items()}

    print("== Subindo o compose do Anel com roteamento pelo Toxiproxy ==")
    compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
    up = compose("up", "-d", "--build", "--wait", "toxiproxy", *SERVICOS_ANEL, arquivos=ARQUIVOS)
    if up.returncode != 0:
        compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
        print(f"falha ao subir: {up.stderr}")
        return 1

    try:
        tox = ClienteToxiproxy()
        tox.esperar_pronto()
        tox.semear(proxies_anel(tuple(PORTAS_ANEL)))
        print("  proxies semeados: anel-1..anel-5\n")

        print("== 1ª eleição (rede saudável) ==")
        lideres, _ = eleger(bases, list(PORTAS_ANEL), 5)
        for i in PORTAS_ANEL:
            print(f"  no-anel-{i}: lider = {lideres.get(i)}")
        if set(lideres.values()) != {5}:
            print("  (aviso) não convergiu para 5; seguindo mesmo assim")
        else:
            print("  -> Líder = 5 (maior id).\n")

        print(f"== Latência de {args.latencia_ms} ms na rota do Nó 3 ==")
        tox.latencia("anel-3", args.latencia_ms)
        print("  toxic de latência aplicado; latência > Deadline faz o cliente estourar o timeout.\n")
        tox.limpar_toxics("anel-3")

        print("== Partição do Líder (Nó 5) e reeleição ==")
        tox.definir_habilitado("anel-5", False)
        print("  proxy anel-5 DESABILITADO: o Nó 5 ficou isolado da rede.")
        vivos = [1, 2, 3, 4]
        lideres, _ = eleger(bases, vivos, 4)
        for i in vivos:
            est = http_json(f"{bases[i]}/estado")
            print(f"  no-anel-{i}: lider = {est['lider']}, mortos = {est.get('mortos')}")
        ok = set(lideres.values()) == {4}
        print("\nReeleição " + ("OK: Nós vivos elegeram o maior id vivo (4)." if ok else "não convergiu para 4."))
        return 0 if ok else 1
    finally:
        if not args.manter:
            compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
        else:
            print("\n(--manter) compose deixado no ar; derrube com:")
            print("  docker compose -f compose.yaml -f compose.toxiproxy.yaml down -v --remove-orphans")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--latencia-ms", type=int, default=500, help="latência a injetar na demo, em ms")
    parser.add_argument("--manter", action="store_true", help="não derrubar o compose ao final")
    args = parser.parse_args()
    raise SystemExit(_demo(args))


if __name__ == "__main__":
    main()
