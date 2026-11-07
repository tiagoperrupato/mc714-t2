"""Testes da CLI `scripts/bateria.py` (tarefa 9).

Exercitam o ponto de entrada de ponta a ponta, sem Docker (os runners rodam em
processo): a demo do enunciado — `--algoritmo anel` e `--algoritmo 2pc` —
realmente grava os CSVs esperados, com uma linha por Réplica e o esquema de
colunas por algoritmo. Também cobre a validação de argumentos e a escolha do
parâmetro variado.
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]


def _carregar_cli():
    """Carrega `scripts/bateria.py` como módulo (não é um pacote importável)."""
    caminho = _RAIZ / "scripts" / "bateria.py"
    spec = importlib.util.spec_from_file_location("bateria_cli", caminho)
    modulo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modulo)
    return modulo


def _ler(caminho: Path) -> list[dict[str, str]]:
    with caminho.open(encoding="utf-8", newline="") as fluxo:
        return list(csv.DictReader(fluxo))


def test_demo_anel_grava_csv_com_colunas_esperadas(tmp_path: Path) -> None:
    cli = _carregar_cli()
    saida = tmp_path / "anel.csv"
    codigo = cli.main(["--algoritmo", "anel", "--replicas", "2", "--saida", str(saida)])
    assert codigo == 0
    linhas = _ler(saida)
    # 3 níveis padrão x 2 réplicas = 6 linhas, uma por Réplica.
    assert len(linhas) == 6
    colunas = set(linhas[0].keys())
    esperadas = {
        "algoritmo",
        "parametro_variado",
        "semente",
        "num_nos",
        "taxa_falhas",
        "tempo_convergencia",
        "mensagens",
        "amb_so",
    }
    assert esperadas <= colunas
    assert all(l["algoritmo"] == "anel" for l in linhas)


def test_demo_2pc_grava_csv_com_colunas_esperadas(tmp_path: Path) -> None:
    cli = _carregar_cli()
    saida = tmp_path / "doispc.csv"
    codigo = cli.main(["--algoritmo", "2pc", "--replicas", "2", "--saida", str(saida)])
    assert codigo == 0
    linhas = _ler(saida)
    assert len(linhas) == 6
    colunas = set(linhas[0].keys())
    esperadas = {
        "algoritmo",
        "parametro_variado",
        "semente",
        "num_participantes",
        "latencia_rede",
        "latencia_commit",
        "vazao",
        "tempo_recuperacao",
        "amb_so",
    }
    assert esperadas <= colunas
    assert all(l["algoritmo"] == "2pc" for l in linhas)


def test_variar_taxa_falhas_no_anel(tmp_path: Path) -> None:
    cli = _carregar_cli()
    saida = tmp_path / "anel_falhas.csv"
    codigo = cli.main(
        ["--algoritmo", "anel", "--variar", "taxa_falhas", "--replicas", "2", "--saida", str(saida)]
    )
    assert codigo == 0
    linhas = _ler(saida)
    niveis = sorted({l["taxa_falhas"] for l in linhas})
    assert len(niveis) == 3


def test_variar_incompativel_e_recusado() -> None:
    cli = _carregar_cli()
    # num_participantes não se aplica ao anel.
    with pytest.raises(SystemExit):
        cli.main(["--algoritmo", "anel", "--variar", "num_participantes"])


def test_replicas_invalidas_recusadas() -> None:
    cli = _carregar_cli()
    with pytest.raises(SystemExit):
        cli.main(["--algoritmo", "anel", "--replicas", "0"])
