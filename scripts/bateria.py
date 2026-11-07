"""CLI da bateria de experimentos do T2 (tarefa 9, Requisito 10).

Executa Réplicas com Sementes independentes variando UM parâmetro em pelo menos
três níveis, gravando um CSV em `results/` com uma linha por Réplica.
A execução por Réplica roda em processo, dirigindo a
Máquina_de_Estados pura de cada algoritmo (sem Docker), de modo que a bateria
seja determinística por Semente e reproduzível.

Uso:

    python scripts/bateria.py --algoritmo anel
        Varia o número de Nós do Anel (3, 5, 7) e grava
        `results/bateria_anel_nos.csv`; métricas Tempo_de_Convergencia e número
        de Mensagens (Requisito 10.2).

    python scripts/bateria.py --algoritmo anel --variar taxa_falhas
        Varia a taxa de falhas do Anel (0.0, 0.1, 0.2).

    python scripts/bateria.py --algoritmo 2pc
        Varia o número de Participantes (3, 5, 7) e grava
        `results/bateria_2pc_participantes.csv`; métricas Latencia_de_Commit,
        vazão e Tempo_de_Recuperacao (Requisito 10.3).

    python scripts/bateria.py --algoritmo 2pc --variar latencia_rede
        Varia a latência de rede (lógica) do 2PC (1.0, 5.0, 10.0).

A bateria inteira é validada antes de sobrescrever resultados (Requisito 10.5);
a Semente e o ambiente (hardware, SO, versões) entram em cada linha (Requisitos
10.6, 13.6); uma Réplica que falha é registrada e a bateria prossegue
(Requisito 10.7).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping

# Torna `src` importável quando o script roda de qualquer diretório.
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from src.experimentos.bateria import (  # noqa: E402
    ItemReplica,
    executar_bateria,
    planejar_bateria,
)
from src.experimentos.runner_anel import executar_replica_anel  # noqa: E402
from src.experimentos.runner_doispc import executar_replica_doispc  # noqa: E402

RESULTS_PADRAO = _RAIZ / "results"
SEMENTE_BASE_PADRAO = 42
REPLICAS_PADRAO = 5

# Níveis padrão por parâmetro variado. Sempre >= 3 níveis (Requisito 10.1).
NIVEIS_ANEL_NOS: tuple[int, ...] = (3, 5, 7)
NIVEIS_ANEL_FALHAS: tuple[float, ...] = (0.0, 0.1, 0.2)
NIVEIS_2PC_PARTS: tuple[int, ...] = (3, 5, 7)
NIVEIS_2PC_LATENCIA: tuple[float, ...] = (1.0, 5.0, 10.0)

# Parâmetros fixos quando o outro é o variado.
TAXA_FALHAS_FIXA = 0.05
NUM_NOS_FIXO = 5
LATENCIA_FIXA = 5.0
NUM_PARTS_FIXO = 3


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


def _runner_2pc(item: ItemReplica) -> Mapping[str, Any]:
    """Executa uma Réplica do 2PC em processo e devolve as métricas (Requisito 10.3)."""
    p = item.parametros
    resultado = executar_replica_doispc(
        num_participantes=int(p["num_participantes"]),
        latencia_rede=float(p["latencia_rede"]),
        semente=item.semente,
        simular_recuperacao=True,
    )
    return {
        "decisao": resultado.decisao,
        "latencia_commit": round(resultado.latencia_commit, 6),
        "vazao": round(resultado.vazao, 6),
        "tempo_recuperacao": round(resultado.tempo_recuperacao, 6),
        "atomico": int(resultado.atomico),
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


def _planejar_2pc(variar: str, replicas: int, semente_base: int) -> list[ItemReplica]:
    if variar == "num_participantes":
        return planejar_bateria(
            parametro="num_participantes",
            niveis=NIVEIS_2PC_PARTS,
            replicas=replicas,
            semente_base=semente_base,
            fixos={"latencia_rede": LATENCIA_FIXA},
        )
    return planejar_bateria(
        parametro="latencia_rede",
        niveis=NIVEIS_2PC_LATENCIA,
        replicas=replicas,
        semente_base=semente_base,
        fixos={"num_participantes": NUM_PARTS_FIXO},
    )


def _nome_saida(algoritmo: str, variar: str) -> str:
    return f"bateria_{algoritmo}_{variar}.csv"


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bateria",
        description="Bateria de Réplicas do T2 (Anel e 2PC), em processo e por Semente.",
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
        choices=["num_nos", "taxa_falhas", "num_participantes", "latencia_rede"],
        help=(
            "parâmetro variado (default: num_nos para anel, num_participantes "
            "para 2pc)"
        ),
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
        "--results",
        type=Path,
        default=RESULTS_PADRAO,
        help=f"diretório de saída (default: {RESULTS_PADRAO})",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=None,
        help="caminho do CSV (default: results/bateria_<algoritmo>_<variar>.csv)",
    )
    return parser


def _variar_padrao(algoritmo: str, variar: str | None) -> str:
    if variar is not None:
        return variar
    return "num_nos" if algoritmo == "anel" else "num_participantes"


def _validar_variar(algoritmo: str, variar: str, parser: argparse.ArgumentParser) -> None:
    validos = {
        "anel": {"num_nos", "taxa_falhas"},
        "2pc": {"num_participantes", "latencia_rede"},
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

    variar = _variar_padrao(args.algoritmo, args.variar)
    _validar_variar(args.algoritmo, variar, parser)

    if args.algoritmo == "anel":
        itens = _planejar_anel(variar, args.replicas, args.semente_base)
        runner = _runner_anel
    else:
        itens = _planejar_2pc(variar, args.replicas, args.semente_base)
        runner = _runner_2pc

    destino = args.saida or (Path(args.results) / _nome_saida(args.algoritmo, variar))
    resultado = executar_bateria(
        itens,
        runner,
        Path(destino),
        meta={"algoritmo": args.algoritmo, "parametro_variado": variar},
    )
    return resultado.codigo_saida


if __name__ == "__main__":
    raise SystemExit(main())
