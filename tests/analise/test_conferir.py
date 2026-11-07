"""Testes da conferência de docs e das tabelas da análise.

Sintetizam CSVs de bateria pequenos num diretório temporário (sem Docker, sem
reexecutar Réplica), geram as tabelas da análise e verificam que:

- a tabela consolidada do Anel e a do 2PC batem com valores calculáveis à mão;
- `conferir` PASSA quando a tabela colada no doc está em dia;
- `conferir` REPROVA (código != 0) quando a tabela do doc fica defasada.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

from src.analise import anel_exp, dados, doispc_exp

_RAIZ = Path(__file__).resolve().parents[2]


def _carregar_cli():
    """Carrega `scripts/analisar.py` como módulo (não é um pacote importável).

    Registra o módulo em `sys.modules` antes de executá-lo: o `analisar.py`
    define dataclasses com anotações por string (`from __future__ import
    annotations`), e o `dataclasses` resolve essas anotações via
    `sys.modules[cls.__module__]`, que precisa existir.
    """
    caminho = _RAIZ / "scripts" / "analisar.py"
    spec = importlib.util.spec_from_file_location("analisar_cli", caminho)
    modulo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["analisar_cli"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _escrever_csv(caminho: Path, colunas: list[str], linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as fluxo:
        escritor = csv.DictWriter(fluxo, fieldnames=colunas)
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow(linha)


def _csv_anel(caminho: Path) -> None:
    """Anel com dois níveis de Nós e Réplicas convergidas de tempo conhecido.

    num_nos=3: tempos 4 e 6 (média 5); num_nos=5: tempos 8 e 12 (média 10).
    """
    colunas = [
        "algoritmo", "parametro_variado", "semente", "num_nos", "taxa_falhas",
        "tempo_convergencia", "mensagens", "convergiu", "nos_vivos",
    ]
    linhas = [
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 1,
         "num_nos": 3, "taxa_falhas": 0.0, "tempo_convergencia": 4,
         "mensagens": 3, "convergiu": 1, "nos_vivos": 3},
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 2,
         "num_nos": 3, "taxa_falhas": 0.0, "tempo_convergencia": 6,
         "mensagens": 3, "convergiu": 1, "nos_vivos": 3},
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 3,
         "num_nos": 5, "taxa_falhas": 0.0, "tempo_convergencia": 8,
         "mensagens": 5, "convergiu": 1, "nos_vivos": 5},
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 4,
         "num_nos": 5, "taxa_falhas": 0.0, "tempo_convergencia": 12,
         "mensagens": 5, "convergiu": 1, "nos_vivos": 5},
    ]
    _escrever_csv(caminho, colunas, linhas)


def _csv_2pc(caminho: Path) -> None:
    """2PC com dois níveis de latência; latências de commit de média conhecida."""
    colunas = [
        "algoritmo", "parametro_variado", "semente", "num_participantes",
        "latencia_rede", "decisao", "latencia_commit", "vazao",
        "tempo_recuperacao", "atomico",
    ]
    linhas = [
        {"algoritmo": "2pc", "parametro_variado": "latencia_rede", "semente": 1,
         "num_participantes": 3, "latencia_rede": 1.0, "decisao": "COMMIT",
         "latencia_commit": 2.0, "vazao": 0.5, "tempo_recuperacao": 3.0,
         "atomico": 1},
        {"algoritmo": "2pc", "parametro_variado": "latencia_rede", "semente": 2,
         "num_participantes": 3, "latencia_rede": 1.0, "decisao": "COMMIT",
         "latencia_commit": 4.0, "vazao": 0.25, "tempo_recuperacao": 3.0,
         "atomico": 1},
        {"algoritmo": "2pc", "parametro_variado": "latencia_rede", "semente": 3,
         "num_participantes": 3, "latencia_rede": 5.0, "decisao": "COMMIT",
         "latencia_commit": 10.0, "vazao": 0.1, "tempo_recuperacao": 3.0,
         "atomico": 1},
        {"algoritmo": "2pc", "parametro_variado": "latencia_rede", "semente": 4,
         "num_participantes": 3, "latencia_rede": 5.0, "decisao": "COMMIT",
         "latencia_commit": 20.0, "vazao": 0.05, "tempo_recuperacao": 3.0,
         "atomico": 1},
    ]
    _escrever_csv(caminho, colunas, linhas)


def test_consolidar_anel_medias_conhecidas() -> None:
    import pandas as pd

    tabela = anel_exp.consolidar(
        pd.DataFrame(
            [
                {"num_nos": 3, "taxa_falhas": 0.0, "tempo_convergencia": 4,
                 "mensagens": 3, "convergiu": 1},
                {"num_nos": 3, "taxa_falhas": 0.0, "tempo_convergencia": 6,
                 "mensagens": 3, "convergiu": 1},
                {"num_nos": 5, "taxa_falhas": 0.0, "tempo_convergencia": 8,
                 "mensagens": 5, "convergiu": 1},
                {"num_nos": 5, "taxa_falhas": 0.0, "tempo_convergencia": 12,
                 "mensagens": 5, "convergiu": 1},
            ]
        )
    )
    por_nos = {int(r["num_nos"]): r for _, r in tabela.iterrows()}
    assert por_nos[3]["tempo_medio"] == pytest.approx(5.0)
    assert por_nos[5]["tempo_medio"] == pytest.approx(10.0)
    assert por_nos[3]["frac_convergiu"] == pytest.approx(1.0)


def test_consolidar_2pc_percentis_conhecidos() -> None:
    import pandas as pd

    tabela = doispc_exp.consolidar_latencia(
        pd.DataFrame(
            [
                {"latencia_rede": 1.0, "latencia_commit": 2.0},
                {"latencia_rede": 1.0, "latencia_commit": 4.0},
                {"latencia_rede": 5.0, "latencia_commit": 10.0},
                {"latencia_rede": 5.0, "latencia_commit": 20.0},
            ]
        )
    )
    por_lat = {float(r["latencia_rede"]): r for _, r in tabela.iterrows()}
    assert por_lat[1.0]["media"] == pytest.approx(3.0)
    assert por_lat[1.0]["p50"] == pytest.approx(3.0)  # mediana de [2,4]
    assert por_lat[5.0]["media"] == pytest.approx(15.0)


def _preparar(tmp_path: Path) -> tuple:
    """CSVs sintéticos e diretórios de saída isolados para um teste de CLI."""
    anel_csv = tmp_path / "anel.csv"
    doispc_csv = tmp_path / "2pc.csv"
    _csv_anel(anel_csv)
    _csv_2pc(doispc_csv)
    dir_dados = tmp_path / "dados"
    dir_figs = tmp_path / "figs"
    return anel_csv, doispc_csv, dir_dados, dir_figs


def _args_comuns(anel_csv, doispc_csv, dir_dados, dir_figs) -> list[str]:
    return [
        "--anel-nos", str(anel_csv),
        "--doispc-latencia", str(doispc_csv),
        "--dir-dados", str(dir_dados),
        "--dir-figuras", str(dir_figs),
    ]


def test_subcomandos_escrevem_tabelas_e_figuras(tmp_path: Path) -> None:
    cli = _carregar_cli()
    anel_csv, doispc_csv, dir_dados, dir_figs = _preparar(tmp_path)
    comuns = _args_comuns(anel_csv, doispc_csv, dir_dados, dir_figs)

    assert cli.main(["anel", *comuns]) == 0
    assert cli.main(["2pc-latencia", *comuns]) == 0
    assert cli.main(["2pc-recuperacao", *comuns]) == 0

    assert (dir_dados / "anel_convergencia.csv").exists()
    assert (dir_figs / "anel_convergencia.png").exists()
    assert (dir_figs / "anel_convergencia.pdf").exists()
    assert (dir_dados / "doispc_latencia.csv").exists()
    assert (dir_figs / "doispc_latencia.png").exists()
    assert (dir_dados / "doispc_recuperacao.csv").exists()


def test_conferir_passa_em_doc_fresco_e_reprova_em_doc_defasado(
    tmp_path: Path, monkeypatch
) -> None:
    cli = _carregar_cli()
    anel_csv, doispc_csv, dir_dados, dir_figs = _preparar(tmp_path)
    comuns = _args_comuns(anel_csv, doispc_csv, dir_dados, dir_figs)

    # Aponta os caminhos dos documentos para o tmp (eles são constantes de módulo).
    doc_anel = tmp_path / "resultados-anel.md"
    doc_2pc = tmp_path / "resultados-2pc.md"
    monkeypatch.setattr(cli, "DOC_ANEL", doc_anel)
    monkeypatch.setattr(cli, "DOC_2PC", doc_2pc)

    df_anel = dados.carregar_anel(anel_csv)
    df_2pc = dados.carregar_2pc(doispc_csv)

    # Documento FRESCO: cola exatamente as tabelas regeneradas.
    bloco_anel = anel_exp.como_markdown(
        anel_exp.consolidar(df_anel), cli.COMANDOS["anel"], [dados.relativo(anel_csv)]
    )
    bloco_lat = doispc_exp.latencia_como_markdown(
        doispc_exp.consolidar_latencia(df_2pc),
        cli.COMANDOS["2pc-latencia"],
        [dados.relativo(doispc_csv)],
    )
    bloco_rec = doispc_exp.recuperacao_como_markdown(
        doispc_exp.consolidar_recuperacao(df_2pc),
        cli.COMANDOS["2pc-recuperacao"],
        [dados.relativo(doispc_csv)],
    )
    doc_anel.write_text(f"# Anel\n\n{bloco_anel}\n", encoding="utf-8")
    doc_2pc.write_text(f"# 2PC\n\n{bloco_lat}\n\n{bloco_rec}\n", encoding="utf-8")

    assert cli.main(["conferir", *comuns]) == 0

    # Agora torna o doc do Anel DEFASADO, trocando um dígito da tabela.
    defasado = doc_anel.read_text(encoding="utf-8").replace("5,00", "9,99", 1)
    assert defasado != doc_anel.read_text(encoding="utf-8")
    doc_anel.write_text(defasado, encoding="utf-8")

    assert cli.main(["conferir", *comuns]) == 1


def test_dados_ausente_reprova_com_mensagem(tmp_path: Path) -> None:
    cli = _carregar_cli()
    codigo = cli.main(
        [
            "anel",
            "--anel-nos", str(tmp_path / "nao_existe.csv"),
            "--doispc-latencia", str(tmp_path / "tambem_nao.csv"),
        ]
    )
    assert codigo == 1
