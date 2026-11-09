"""Testes da CLI `scripts/bateria.py` (tarefa 9), sem Docker.

O Anel roda em processo de ponta a ponta. No 2PC o condutor é trocado por um
fake, e o teste confere a ligação da CLI: os dois CSVs, as Sementes e o código
de saída sem Docker. Também cobre a validação de argumentos.
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from src.experimentos import condutor_doispc

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


def test_2pc_liga_o_condutor_e_grava_os_dois_csvs(tmp_path: Path, monkeypatch) -> None:
    cli = _carregar_cli()
    monkeypatch.setattr(condutor_doispc, "preparar", lambda: {"amb_docker_versao": "0.0"})
    monkeypatch.setattr(
        condutor_doispc,
        "executar_replica_latencia",
        lambda item, *, transacoes, aquecimento, dir_logs: [
            {"ordem": o, "aquecimento": int(o < aquecimento)} for o in range(aquecimento + transacoes)
        ],
    )
    monkeypatch.setattr(
        condutor_doispc, "executar_replica_recuperacao", lambda item, *, dir_logs: {"em_duvida": 3}
    )
    argv = ["--algoritmo", "2pc", "--replicas", "2", "--transacoes", "3", "--aquecimento", "1"]
    assert cli.main([*argv, "--results", str(tmp_path)]) == 0

    latencia = _ler(tmp_path / "bateria_2pc_latencia_rede.csv")
    recuperacao = _ler(tmp_path / "bateria_2pc_recuperacao.csv")
    niveis = len(cli.NIVEIS_2PC_LATENCIA)
    assert len(latencia) == niveis * 2 * 4
    assert len(recuperacao) == niveis * 2
    assert {l["latencia_rede"] for l in latencia} == {str(n) for n in cli.NIVEIS_2PC_LATENCIA}
    # Os ids de transação levam a Semente, então as duas baterias não podem repeti-la.
    assert not {l["semente"] for l in latencia} & {l["semente"] for l in recuperacao}
    assert all(l["amb_docker_versao"] == "0.0" for l in latencia + recuperacao)


def test_2pc_sem_docker_sai_com_codigo_2_sem_gravar(tmp_path: Path, monkeypatch) -> None:
    cli = _carregar_cli()

    def sem_docker() -> dict[str, str]:
        raise RuntimeError("Docker indisponível")

    monkeypatch.setattr(condutor_doispc, "preparar", sem_docker)
    assert cli.main(["--algoritmo", "2pc", "--results", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_saida_e_recusada_no_2pc() -> None:
    cli = _carregar_cli()
    with pytest.raises(SystemExit):
        cli.main(["--algoritmo", "2pc", "--saida", "x.csv"])


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
    # latencia_rede não se aplica ao anel.
    with pytest.raises(SystemExit):
        cli.main(["--algoritmo", "anel", "--variar", "latencia_rede"])


def test_replicas_invalidas_recusadas() -> None:
    cli = _carregar_cli()
    with pytest.raises(SystemExit):
        cli.main(["--algoritmo", "anel", "--replicas", "0"])
