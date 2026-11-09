"""CLI da bateria de experimentos do T2 (tarefa 9, Requisito 10).

Executa Réplicas com Sementes independentes variando UM parâmetro em pelo menos
três níveis, gravando CSVs em `results/`. A do
Anel roda em processo e é determinística por Semente. A do 2PC sobe o compose
com o Toxiproxy a cada Réplica e mede os nós reais.

Uso:

    python scripts/bateria.py --algoritmo anel
        Varia o número de Nós do Anel (3, 5, 7) e grava
        `results/bateria_anel_num_nos.csv`, com as métricas Tempo_de_Convergencia e
        número de Mensagens (Requisito 10.2).

    python scripts/bateria.py --algoritmo anel --variar taxa_falhas
        Varia a taxa de falhas do Anel (0.0, 0.1, 0.2).

    python scripts/bateria.py --algoritmo 2pc
        Varia a latência de rede do 2PC (0, 10, 25 e 50 ms em cada sentido). Grava
        `results/bateria_2pc_latencia_rede.csv`, uma linha por transação, e
        `results/bateria_2pc_recuperacao.csv`, uma linha por crash do Coordenador.
        Os logs dos nós ficam em `results/bateria_2pc_logs/`.

A bateria inteira é validada antes de sobrescrever resultados (Requisito 10.5);
a Semente e o ambiente (hardware, SO, versões) entram em cada linha (Requisitos
10.6, 13.6); uma Réplica que falha é registrada e a bateria prossegue
(Requisito 10.7).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

# Torna `src` importável quando o script roda de qualquer diretório.
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from src.analise.dados import BATERIA_2PC_LATENCIA, BATERIA_2PC_RECUPERACAO  # noqa: E402
from src.experimentos import condutor_doispc  # noqa: E402
from src.experimentos.bateria import (  # noqa: E402
    ItemReplica,
    executar_bateria,
    planejar_bateria,
)
from src.experimentos.runner_anel import executar_replica_anel  # noqa: E402

RESULTS_PADRAO = _RAIZ / "results"
SEMENTE_BASE_PADRAO = 42
REPLICAS_PADRAO = 5

# Níveis padrão por parâmetro variado. Sempre >= 3 níveis (Requisito 10.1).
NIVEIS_ANEL_NOS: tuple[int, ...] = (3, 5, 7)
NIVEIS_ANEL_FALHAS: tuple[float, ...] = (0.0, 0.1, 0.2)
# Atraso de ida em ms, aplicado em cada sentido de cada proxy do 2PC.
NIVEIS_2PC_LATENCIA: tuple[int, ...] = (0, 10, 25, 50)

# Parâmetros fixos quando o outro é o variado.
TAXA_FALHAS_FIXA = 0.05
NUM_NOS_FIXO = 5

# Transações por Réplica do 2PC. As de aquecimento ficam no CSV, marcadas, e a
# análise as descarta.
TRANSACOES_PADRAO = 40
AQUECIMENTO_PADRAO = 5

DIR_2PC_LOGS = "bateria_2pc_logs"


def _runner_anel(item: ItemReplica) -> Mapping[str, Any]:
    """Executa uma Réplica do Anel em processo e devolve as métricas (Requisito 10.2)."""
    p = item.parametros
    resultado = executar_replica_anel(
        num_nos=int(p["num_nos"]),
        taxa_falhas=float(p["taxa_falhas"]),
        semente=item.semente,
    )
    return {
        "tempo_convergencia": resultado.tempo_convergencia,
        "mensagens": resultado.mensagens,
        "convergiu": int(resultado.convergiu),
        "lider": resultado.lider if resultado.lider is not None else "",
        "nos_vivos": resultado.nos_vivos,
    }


def _planejar_anel(variar: str, replicas: int, semente_base: int) -> list[ItemReplica]:
    if variar == "num_nos":
        return planejar_bateria(
            parametro="num_nos",
            niveis=NIVEIS_ANEL_NOS,
            replicas=replicas,
            semente_base=semente_base,
            fixos={"taxa_falhas": TAXA_FALHAS_FIXA},
        )
    return planejar_bateria(
        parametro="taxa_falhas",
        niveis=NIVEIS_ANEL_FALHAS,
        replicas=replicas,
        semente_base=semente_base,
        fixos={"num_nos": NUM_NOS_FIXO},
    )


def _executar_anel(args: argparse.Namespace, variar: str) -> int:
    itens = _planejar_anel(variar, args.replicas, args.semente_base)
    destino = args.saida or (Path(args.results) / f"bateria_anel_{variar}.csv")
    resultado = executar_bateria(
        itens,
        _runner_anel,
        Path(destino),
        meta={"algoritmo": "anel", "parametro_variado": variar},
    )
    return resultado.codigo_saida


def _executar_2pc(args: argparse.Namespace) -> int:
    """Roda a bateria de latência e a de recuperação do 2PC, cada uma com suas Sementes."""
    fixos = {"num_participantes": len(condutor_doispc.PARTICIPANTES)}
    itens_latencia = planejar_bateria(
        parametro="latencia_rede",
        niveis=NIVEIS_2PC_LATENCIA,
        replicas=args.replicas,
        semente_base=args.semente_base,
        fixos=fixos,
    )
    # As Sementes da recuperação seguem as da latência, para que nenhum id de transação se repita.
    itens_recuperacao = planejar_bateria(
        parametro="latencia_rede",
        niveis=NIVEIS_2PC_LATENCIA,
        replicas=args.replicas,
        semente_base=args.semente_base + len(itens_latencia),
        fixos=fixos,
    )
    try:
        ambiente_docker = condutor_doispc.preparar()
    except RuntimeError as erro:
        print(f"[erro] {erro}", file=sys.stderr)
        return 2

    results = Path(args.results)
    dir_logs = results / DIR_2PC_LOGS
    if dir_logs.exists():
        shutil.rmtree(dir_logs)
    meta = {"algoritmo": "2pc", "parametro_variado": "latencia_rede"}
    latencia = executar_bateria(
        itens_latencia,
        lambda item: condutor_doispc.executar_replica_latencia(
            item, transacoes=args.transacoes, aquecimento=args.aquecimento, dir_logs=dir_logs
        ),
        results / BATERIA_2PC_LATENCIA.name,
        meta=meta,
        ambiente_extra=ambiente_docker,
        saida=sys.stderr,
    )
    recuperacao = executar_bateria(
        itens_recuperacao,
        lambda item: condutor_doispc.executar_replica_recuperacao(item, dir_logs=dir_logs),
        results / BATERIA_2PC_RECUPERACAO.name,
        meta=meta,
        ambiente_extra=ambiente_docker,
        saida=sys.stderr,
    )
    return max(latencia.codigo_saida, recuperacao.codigo_saida)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bateria",
        description="Bateria de Réplicas do T2: o Anel em processo e o 2PC no compose.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--algoritmo",
        required=True,
        choices=["anel", "2pc"],
        help="algoritmo a experimentar",
    )
    parser.add_argument(
        "--variar",
        default=None,
        choices=["num_nos", "taxa_falhas", "latencia_rede"],
        help="parâmetro variado (default: num_nos para anel, latencia_rede para 2pc)",
    )
    parser.add_argument(
        "--replicas",
        type=int,
        default=REPLICAS_PADRAO,
        help=f"Réplicas por nível (default: {REPLICAS_PADRAO})",
    )
    parser.add_argument(
        "--semente-base",
        type=int,
        default=SEMENTE_BASE_PADRAO,
        dest="semente_base",
        help=f"Semente base das Réplicas (default: {SEMENTE_BASE_PADRAO})",
    )
    parser.add_argument(
        "--transacoes",
        type=int,
        default=TRANSACOES_PADRAO,
        help=f"2pc: transações medidas por Réplica (default: {TRANSACOES_PADRAO})",
    )
    parser.add_argument(
        "--aquecimento",
        type=int,
        default=AQUECIMENTO_PADRAO,
        help=f"2pc: transações de aquecimento por Réplica (default: {AQUECIMENTO_PADRAO})",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS_PADRAO,
        help=f"diretório de saída (default: {RESULTS_PADRAO})",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=None,
        help="anel: caminho do CSV (default: results/bateria_anel_<variar>.csv)",
    )
    return parser


def _validar_variar(algoritmo: str, variar: str, parser: argparse.ArgumentParser) -> None:
    validos = {
        "anel": {"num_nos", "taxa_falhas"},
        "2pc": {"latencia_rede"},
    }
    if variar not in validos[algoritmo]:
        parser.error(
            f"--variar {variar!r} não se aplica ao algoritmo {algoritmo!r}; "
            f"válidos: {sorted(validos[algoritmo])}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)

    if args.replicas < 1:
        parser.error("o número de réplicas deve ser >= 1")
    if not (0 <= args.semente_base <= 2_147_483_647):
        parser.error("a semente base deve ser um inteiro em [0, 2147483647]")
    if args.transacoes < 1 or args.aquecimento < 0:
        parser.error("--transacoes deve ser >= 1 e --aquecimento >= 0")

    variar = args.variar or ("num_nos" if args.algoritmo == "anel" else "latencia_rede")
    _validar_variar(args.algoritmo, variar, parser)

    if args.algoritmo == "anel":
        return _executar_anel(args, variar)
    if args.saida is not None:
        parser.error("--saida não se aplica ao 2pc, que grava dois CSVs. Use --results.")
    return _executar_2pc(args)


if __name__ == "__main__":
    raise SystemExit(main())
