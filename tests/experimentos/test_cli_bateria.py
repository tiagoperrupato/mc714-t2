"""Testes da CLI `scripts/bateria.py`, sem Docker.

O Anel e o Bully rodam em processo de ponta a ponta. No 2PC o condutor é trocado por um
fake, e o teste confere a ligação da CLI: os dois CSVs, as Sementes e o código
de saída sem Docker. Também cobre a validação de argumentos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import bateria as cli
from src.experimentos import condutor_doispc
from tests.apoio import ler_csv


def test_demo_anel_grava_csv_com_colunas_esperadas(tmp_path: Path) -> None:
    codigo = cli.main(["--algoritmo", "anel", "--replicas", "2", "--results", str(tmp_path)])
    assert codigo == 0
    linhas = ler_csv(tmp_path / "bateria_anel_num_nos.csv")
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


def test_bully_grava_csv_nas_mesmas_condicoes_do_anel(tmp_path: Path) -> None:
    argv = ["--replicas", "2", "--results", str(tmp_path)]
    assert cli.main(["--algoritmo", "bully", *argv]) == 0
    assert cli.main(["--algoritmo", "anel", *argv]) == 0
    bully = ler_csv(tmp_path / "bateria_bully_num_nos.csv")
    anel = ler_csv(tmp_path / "bateria_anel_num_nos.csv")
    assert {"mensagens", "mensagens_total", "tempo_convergencia", "amb_so"} <= set(bully[0])
    assert all(l["algoritmo"] == "bully" and l["convergiu"] == "1" for l in bully)
    chave = [(l["num_nos"], l["semente"], l["taxa_falhas"]) for l in bully]
    assert chave == [(l["num_nos"], l["semente"], l["taxa_falhas"]) for l in anel]


def test_2pc_liga_o_condutor_e_grava_os_dois_csvs(tmp_path: Path, monkeypatch) -> None:
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

    latencia = ler_csv(tmp_path / "bateria_2pc_latencia_rede.csv")
    recuperacao = ler_csv(tmp_path / "bateria_2pc_recuperacao.csv")
    niveis = len(cli.NIVEIS_2PC_LATENCIA)
    assert len(latencia) == niveis * 2 * 4
    assert len(recuperacao) == niveis * 2
    assert {l["latencia_rede"] for l in latencia} == {str(n) for n in cli.NIVEIS_2PC_LATENCIA}
    # Os ids de transação levam a Semente, então as duas baterias não podem repeti-la.
    assert not {l["semente"] for l in latencia} & {l["semente"] for l in recuperacao}
    assert all(l["amb_docker_versao"] == "0.0" for l in latencia + recuperacao)


def test_2pc_sem_docker_sai_com_codigo_2_sem_gravar(tmp_path: Path, monkeypatch) -> None:

    def sem_docker() -> dict[str, str]:
        raise RuntimeError("Docker indisponível")

    monkeypatch.setattr(condutor_doispc, "preparar", sem_docker)
    assert cli.main(["--algoritmo", "2pc", "--results", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_replicas_invalidas_recusadas() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--algoritmo", "anel", "--replicas", "0"])
