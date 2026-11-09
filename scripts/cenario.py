"""CLI de cenário: sobe UM cenário reproduzível de um algoritmo sobre o compose.

Enquanto a bateria (`scripts/bateria.py`) roda muitas Réplicas, este script sobe
um cenário real sobre o Docker Compose, conduz o algoritmo pelos
endpoints de controle, coleta as métricas observadas **e os Logs_Estruturados e
Logs_Duraveis que os Nós reais deixaram** para `results/`, e por fim roda os
invariantes de correção sobre esses logs (`scripts/verificar.py`). É o
complemento "ponta a ponta" da orquestração: a bateria mede
tendência com reprodutibilidade; o cenário demonstra um caso único sobre a rede
real e prova sua correção pelos invariantes.

Cada cenário registra a **Semente** com que foi conduzido:
ela aparece no registro JSON coletado e, por vir do ambiente dos contêineres,
também no `inicio_replica` de cada Log_Estruturado. Com a mesma Semente e a
mesma configuração do compose, o Cenário de falha é repetível.

O registro guarda o desfecho esperado ao lado do observado. O script sai 1
quando eles diferem, mesmo que os invariantes passem, porque um `2pc commit`
que termina em ABORT não viola nenhuma propriedade de safety.

Cenários do Anel:

    python scripts/cenario.py anel normal
        Sobe os 5 Nós, dispara uma eleição e coleta o Líder e o
        Tempo_de_Convergencia observado. A eleição converge para o maior id (5).

    python scripts/cenario.py anel falha
        Idem, e então derruba o contêiner do Líder (Nó 5) e dispara nova
        eleição: os Nós vivos reelegem o maior id vivo (4).

Cenários do 2PC:

    python scripts/cenario.py 2pc commit        (alias: 2pc normal)
        Coordenador + 3 Participantes, todos votando SIM → COMMIT em todos.

    python scripts/cenario.py 2pc abort          (alias: 2pc falha)
        Participante 3 vota NAO → ABORT atômico em todos.

    python scripts/cenario.py 2pc crash-coord
        Todos votam SIM, e o processo do Coordenador morre logo depois de
        gravar a Decisao, antes de difundi-la (flag `DOISPC_CAIR_APOS_DECIDIR`).
        O cenário mostra os Participantes em Estado_Em_Duvida,
        reinicia o contêiner e mostra todos decidindo COMMIT depois que o
        Coordenador relê o Log_Duravel e difunde a Decisao gravada.

Requer Docker com Compose v2. Sem Docker, o script falha com uma mensagem clara
e código de saída 2, em vez de travar.

O script valida a configuração do cenário ANTES de subir qualquer contêiner ou
tocar `results/`, e sempre derruba o compose ao final
(`down -v --remove-orphans`), para não deixar contêiner nem volume órfão.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scripts import verificar  # noqa: E402
from src.experimentos.ambiente import coletar_ambiente  # noqa: E402
from src.experimentos.conteineres import (  # noqa: E402
    BASE_COORD,
    COMPOSE,
    PORTAS_PART,
    SERVICOS_2PC,
    SONDAGEM_S,
    coletar_logs,
    compose,
    decisoes,
    docker_disponivel,
    esperar_conclusao,
    esperar_saude,
    estados_participantes,
    http_json,
    override_2pc,
    reiniciar_coordenador,
)

RESULTS_PADRAO = _RAIZ / "results"

SERVICOS_ANEL = [f"no-anel-{i}" for i in range(1, 6)]
PORTAS_ANEL = {i: 8000 + i for i in range(1, 6)}  # 8001..8005

# Semente padrão dos cenários (casa com SEMENTE do compose). Registrada no
# resultado e sobreponível por --semente para reproduzir uma execução.
SEMENTE_PADRAO = 0

# Modos normalizados do 2PC. Mantemos os aliases `normal`/`falha` por
# compatibilidade (normal == commit, falha == abort).
_MODOS_2PC = {
    "normal": "commit",
    "commit": "commit",
    "falha": "abort",
    "abort": "abort",
    "crash-coord": "crash-coord",
    "crash_coord": "crash-coord",
}


# --------------------------------------------------------------------------- #
# Override do Anel (semente)
# --------------------------------------------------------------------------- #


def _override_anel(semente: int) -> str:
    """Override que fixa a Semente dos cinco Nós do Anel para o cenário."""
    sem = str(semente)
    linhas = ["services:"]
    for i in range(1, 6):
        linhas.append(f"  no-anel-{i}:")
        linhas.append("    environment:")
        linhas.append(f'      SEMENTE: "{sem}"')
    return "\n".join(linhas) + "\n"


# --------------------------------------------------------------------------- #
# Cenários do Anel
# --------------------------------------------------------------------------- #


def _esperar_lider(bases: dict[int, str], ids, alvo, timeout: float) -> dict[int, Any]:
    limite = time.monotonic() + timeout
    lideres: dict[int, Any] = {}
    while time.monotonic() < limite:
        try:
            lideres = {i: http_json(f"{bases[i]}/estado")["lider"] for i in ids}
        except (urllib.error.URLError, OSError):
            time.sleep(SONDAGEM_S)
            continue
        if all(v == alvo for v in lideres.values()):
            break
        time.sleep(SONDAGEM_S)
    return lideres


def _eleger(bases: dict[int, str], vivos: list[int], alvo: int) -> tuple[dict[int, Any], float]:
    """Dispara uma eleição no Nó 1 e espera os `vivos` reportarem `alvo`.

    Devolve o Líder que cada vivo reporta e o tempo até todos reportarem `alvo`,
    ou até o prazo de 30 s.
    """
    inicio = time.monotonic()
    http_json(f"{bases[1]}/iniciar-eleicao", metodo="POST")
    lideres = _esperar_lider(bases, vivos, alvo, 30.0)
    return lideres, time.monotonic() - inicio


def _cenario_anel(modo: str, semente: int, arquivos: list[Path]) -> dict[str, Any]:
    """Conduz um cenário do Anel sobre o compose e devolve as métricas observadas.

    - `normal`: dispara uma eleição com os cinco Nós vivos, que converge para o
      maior id (5).
    - `falha`: depois dessa eleição, derruba o contêiner do Líder com
      `docker compose kill` e dispara outra. Ela salta o 5 e
      converge para o maior id vivo (4).

    Cada eleição é uma rodada com id próprio, e os invariantes conferem cada uma
    contra os Nós vivos nela.
    """
    bases = {i: f"http://127.0.0.1:{porta}" for i, porta in PORTAS_ANEL.items()}
    todos = list(PORTAS_ANEL)
    lideres, tempo = _eleger(bases, todos, 5)
    registro: dict[str, Any] = {
        "cenario": f"anel_{modo}",
        "algoritmo": "anel",
        "modo": modo,
        "semente": semente,
        "maior_id_topologia": 5,
        "lideres_iniciais": lideres,
        "tempo_convergencia_s": round(tempo, 4),
        "esperado": {"lideres_iniciais": dict.fromkeys(todos, 5)},
    }
    if modo == "falha":
        compose("kill", "no-anel-5", arquivos=arquivos, timeout=60)
        vivos = [i for i in todos if i != 5]
        lideres, tempo = _eleger(bases, vivos, 4)
        registro.update(
            {
                "falha_injetada": "docker compose kill no-anel-5, depois da primeira eleição",
                "lideres_pos_falha": lideres,
                "tempo_reeleicao_s": round(tempo, 4),
            }
        )
        registro["esperado"]["lideres_pos_falha"] = dict.fromkeys(vivos, 4)
    return registro


# --------------------------------------------------------------------------- #
# Cenário do 2PC
# --------------------------------------------------------------------------- #


def _cenario_2pc(modo_norm: str, semente: int, arquivos: list[Path], erro) -> dict[str, Any]:
    """Conduz uma transação 2PC sobre o compose e devolve as métricas observadas.

    O voto dos Participantes e o crash do Coordenador vêm do override montado por
    `override_2pc`. A DECISAO sai da fila do Coordenador depois da resposta do
    `/transacao`, então o condutor espera cada Participante decidir, com prazo.
    Em `crash-coord` o `/transacao` não responde, porque o processo morre depois
    de gravar a Decisao. O condutor registra os Participantes em dúvida, reinicia
    o contêiner e mede o tempo até todos decidirem.
    """
    esperada = "ABORT" if modo_norm == "abort" else "COMMIT"
    esperado: dict[str, Any] = {
        "decisao": esperada,
        "decisoes_participantes": dict.fromkeys(PORTAS_PART, esperada),
    }
    esperar_saude(BASE_COORD, 30.0)
    inicio = time.monotonic()
    try:
        resposta = http_json(f"{BASE_COORD}/transacao", metodo="POST", timeout=30.0)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - rede do cenário
        resposta = {"erro": str(exc)}
    latencia = time.monotonic() - inicio

    if modo_norm != "crash-coord":
        return {
            "cenario": f"2pc_{modo_norm}",
            "algoritmo": "2pc",
            "modo": modo_norm,
            "semente": semente,
            "decisao": resposta.get("decisao"),
            "latencia_commit_s": round(latencia, 4),
            "participantes": resposta.get("participantes"),
            "decisoes_participantes": decisoes(estados_participantes(esperar_decisao=True)),
            "esperado": esperado,
        }

    em_duvida = sorted(
        pid
        for pid, est in estados_participantes(esperar_decisao=False).items()
        if est.get("em_duvida")
    )
    print(f"[cenario] Coordenador caiu ({resposta.get('erro')}). Em dúvida: {em_duvida}", file=erro)

    recuperacao, finais = reiniciar_coordenador(arquivos)
    print(f"[cenario] Coordenador reiniciado. Em {recuperacao:.2f} s: {finais}", file=erro)
    # Os logs só são coletados depois de todos os ACKs, para incluírem a retransmissão.
    esperar_saude(BASE_COORD, 30.0)
    estado_coord = esperar_conclusao()
    return {
        "cenario": "2pc_crash-coord",
        "algoritmo": "2pc",
        "modo": modo_norm,
        "semente": semente,
        "falha_injetada": "processo do Coordenador morre depois de gravar a Decisao "
        "(DOISPC_CAIR_APOS_DECIDIR) e o contêiner é reiniciado",
        "participantes_em_duvida": em_duvida,
        "decisao": estado_coord.get("decisao"),
        "concluido": estado_coord.get("concluido"),
        "tempo_recuperacao_s": round(recuperacao, 4),
        "decisoes_participantes": finais,
        "esperado": esperado | {"participantes_em_duvida": sorted(PORTAS_PART), "concluido": True},
    }


# --------------------------------------------------------------------------- #
# Coleta dos logs reais (JSONL estruturado + Log_Duravel) de dentro dos Nós
# --------------------------------------------------------------------------- #


def _coletar_stdout(servicos: list[str], destino: Path, arquivos: list[Path]) -> None:
    """Grava o stdout de contêiner de cada serviço (um .txt por Nó), para depuração."""
    destino.mkdir(parents=True, exist_ok=True)
    for servico in servicos:
        saida = compose("logs", "--no-color", servico, arquivos=arquivos, timeout=60)
        (destino / f"{servico}.txt").write_text(saida.stdout, encoding="utf-8")


def _divergencias(registro: dict[str, Any]) -> list[str]:
    """Os campos de `registro["esperado"]` que o desfecho observado não reproduz."""
    return [
        f"{campo}: esperado {valor}, obtido {registro.get(campo)}"
        for campo, valor in registro["esperado"].items()
        if registro.get(campo) != valor
    ]


def _gravar_registro(registro: dict[str, Any], destino: Path) -> None:
    """Grava o registro do cenário em JSON, carimbando o ambiente."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    registro = dict(registro)
    registro["ambiente"] = coletar_ambiente().como_colunas()
    destino.write_text(
        json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Orquestração: valida, sobe, conduz, coleta, verifica, derruba
# --------------------------------------------------------------------------- #


def _normalizar(algoritmo: str, modo: str) -> tuple[str, str]:
    """Valida e normaliza (algoritmo, modo) ANTES de qualquer efeito colateral.

    Devolve (modo_rotulo, modo_normalizado). Para o Anel, o rótulo é o próprio
    modo (normal/falha). Para o 2PC, o rótulo é o modo normalizado
    (commit/abort/crash-coord), resolvendo os aliases normal/falha.
    """
    if algoritmo == "anel":
        if modo not in ("normal", "falha"):
            raise ValueError(f"modo do Anel inválido: {modo!r} (use normal|falha)")
        return modo, modo
    norm = _MODOS_2PC.get(modo)
    if norm is None:
        raise ValueError(
            f"modo do 2PC inválido: {modo!r} (use commit|abort|crash-coord)"
        )
    return norm, norm


def executar_cenario(
    algoritmo: str,
    modo: str,
    results: Path,
    *,
    semente: int = SEMENTE_PADRAO,
    saida=sys.stdout,
    erro=sys.stderr,
) -> int:
    """Sobe o cenário, conduz o algoritmo, coleta logs, VERIFICA e derruba o compose.

    Devolve 0 em sucesso (avisos de invariante não reprovam), 1 em falha de
    execução, desfecho diferente do esperado ou reprovação de um invariante de
    safety, e 2 quando o Docker não está disponível. A configuração do cenário é
    validada antes de subir qualquer contêiner, e o compose é sempre derrubado ao
    final.
    """
    try:
        rotulo, modo_norm = _normalizar(algoritmo, modo)
    except ValueError as exc:
        print(f"[cenario] configuração inválida: {exc}", file=erro)
        return 1

    if not isinstance(semente, int) or isinstance(semente, bool) or semente < 0:
        print(f"[cenario] semente deve ser inteiro >= 0; recebido {semente!r}", file=erro)
        return 1

    if algoritmo == "anel":
        servicos = SERVICOS_ANEL
        override_texto = _override_anel(semente)
        conduzir = lambda arquivos: _cenario_anel(rotulo, semente, arquivos)  # noqa: E731
    else:
        servicos = SERVICOS_2PC
        override_texto = override_2pc(modo_norm, semente)
        conduzir = lambda arquivos: _cenario_2pc(modo_norm, semente, arquivos, erro)  # noqa: E731

    if not docker_disponivel():
        print(
            "[cenario] Docker (daemon + compose v2) indisponível. Este script "
            "sobe contêineres: inicie o daemon e confira com docker info.",
            file=erro,
        )
        return 2

    base = Path(results)
    rotulo_arq = f"{algoritmo}_{rotulo}"

    with tempfile.TemporaryDirectory() as tmp:
        override = Path(tmp) / "cenario-override.yaml"
        override.write_text(override_texto, encoding="utf-8")
        arquivos = [COMPOSE, override]

        compose("down", "-v", "--remove-orphans", arquivos=arquivos)
        up = compose("up", "-d", "--build", "--wait", *servicos, arquivos=arquivos, timeout=900)
        if up.returncode != 0:
            print(f"[cenario] falha ao subir o compose:\n{up.stdout}\n{up.stderr}", file=erro)
            compose("down", "-v", "--remove-orphans", arquivos=arquivos)
            return 1

        try:
            registro = conduzir(arquivos)
            registro["semente"] = semente
            divergencias = _divergencias(registro)
            registro["desfecho"] = {"ok": not divergencias, "divergencias": divergencias}

            logs_dir = base / f"cenario_{rotulo_arq}_logs"
            if logs_dir.exists():
                shutil.rmtree(logs_dir)
            coletar_logs(algoritmo, servicos, logs_dir, arquivos)
            _coletar_stdout(servicos, base / f"cenario_{rotulo_arq}_stdout", arquivos)

            # Roda os invariantes sobre os logs coletados deste cenário.
            linhas_verif: list[str] = []
            codigo_verif = verificar.verificar([logs_dir], imprimir=linhas_verif.append)
            registro["verificacao"] = {
                "codigo": codigo_verif,
                "ok": codigo_verif == 0,
                "relatorio": linhas_verif,
            }

            arquivo_registro = base / f"cenario_{rotulo_arq}.json"
            _gravar_registro(registro, arquivo_registro)
            print(
                f"[cenario] {algoritmo}/{rotulo} (semente={semente}): "
                f"{json.dumps({k: v for k, v in registro.items() if k != 'verificacao'}, ensure_ascii=False)}",
                file=saida,
            )
            print("\n".join(linhas_verif), file=saida)
            if divergencias:
                print(
                    f"[cenario] o desfecho de {algoritmo}/{rotulo} difere do esperado: "
                    f"{'; '.join(divergencias)}. Registro em {arquivo_registro}, "
                    f"logs dos Nós em {logs_dir}",
                    file=erro,
                )
            if codigo_verif != 0:
                print(
                    f"[cenario] VERIFICAÇÃO REPROVOU para {algoritmo}/{rotulo}",
                    file=erro,
                )
            return 1 if divergencias or codigo_verif != 0 else 0
        except Exception as exc:  # noqa: BLE001 - qualquer falha de condução
            print(f"[cenario] falha ao conduzir {algoritmo}/{rotulo}: {exc}", file=erro)
            return 1
        finally:
            compose("down", "-v", "--remove-orphans", arquivos=arquivos)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cenario",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("algoritmo", choices=["anel", "2pc"], help="algoritmo alvo")
    parser.add_argument(
        "modo",
        help="Anel: normal|falha. 2PC: commit|abort|crash-coord (normal=commit, falha=abort)",
    )
    parser.add_argument(
        "--semente",
        type=int,
        default=SEMENTE_PADRAO,
        help=f"Semente do cenário, registrada no resultado (default: {SEMENTE_PADRAO})",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS_PADRAO,
        help=f"diretório de saída (default: {RESULTS_PADRAO})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)
    return executar_cenario(
        args.algoritmo, args.modo, Path(args.results), semente=args.semente
    )


if __name__ == "__main__":
    raise SystemExit(main())
