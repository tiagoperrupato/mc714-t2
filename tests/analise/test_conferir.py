"""Testes da conferência de docs e das tabelas da análise.

Sintetizam CSVs de bateria pequenos num diretório temporário (sem Docker, sem
reexecutar Réplica), geram as tabelas da análise e verificam que:

- a tabela consolidada do Anel e a do 2PC batem com valores calculáveis à mão, e
  a do 2PC descarta o aquecimento e os ABORT.
- `conferir` PASSA quando a tabela colada no doc está em dia.
- `conferir` REPROVA (código != 0) quando a tabela do doc fica defasada.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import analisar as cli
from src.analise import anel_exp, bully_exp, dados, doispc_exp


def _escrever_csv(caminho: Path, colunas: list[str], linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as fluxo:
        escritor = csv.DictWriter(fluxo, fieldnames=colunas)
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow(linha)


def _csv_anel(caminho: Path) -> None:
    """Anel com dois níveis de Nós e Réplicas convergidas de tempo conhecido.

    num_nos=3: tempos 4 e 6 (média 5). num_nos=5: tempos 8 e 12 (média 10).
    """
    colunas = [
        "algoritmo", "parametro_variado", "semente", "num_nos", "taxa_falhas",
        "tempo_convergencia", "mensagens", "convergiu",
    ]
    linhas = [
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 1,
         "num_nos": 3, "taxa_falhas": 0.0, "tempo_convergencia": 4,
         "mensagens": 3, "convergiu": 1},
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 2,
         "num_nos": 3, "taxa_falhas": 0.0, "tempo_convergencia": 6,
         "mensagens": 3, "convergiu": 1},
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 3,
         "num_nos": 5, "taxa_falhas": 0.0, "tempo_convergencia": 8,
         "mensagens": 5, "convergiu": 1},
        {"algoritmo": "anel", "parametro_variado": "num_nos", "semente": 4,
         "num_nos": 5, "taxa_falhas": 0.0, "tempo_convergencia": 12,
         "mensagens": 5, "convergiu": 1},
    ]
    _escrever_csv(caminho, colunas, linhas)


def _csv_bully(caminho: Path) -> None:
    """Bully com os níveis e as Sementes do `_csv_anel`, sem perda e no pior caso n² − 1."""
    colunas = [
        "algoritmo", "parametro_variado", "semente", "num_nos", "taxa_falhas",
        "tempo_convergencia", "mensagens", "mensagens_total", "convergiu",
    ]
    linhas = [
        {"algoritmo": "bully", "parametro_variado": "num_nos", "semente": semente,
         "num_nos": num_nos, "taxa_falhas": 0.0, "tempo_convergencia": 2,
         "mensagens": mensagens, "mensagens_total": num_nos * num_nos - 1, "convergiu": 1}
        for semente, num_nos, mensagens in ((1, 3, 7), (2, 3, 7), (3, 5, 18), (4, 5, 18))
    ]
    _escrever_csv(caminho, colunas, linhas)


_COLUNAS_2PC_LATENCIA = [
    "algoritmo", "parametro_variado", "semente", "num_participantes", "latencia_rede",
    "ordem", "aquecimento", "transacao", "decisao", "latencia_commit_s", "mensagens",
]


def _linhas_2pc_latencia() -> list[dict]:
    """Duas Réplicas por nível, cada uma com uma transação de aquecimento e duas medidas.

    Nível 10: Réplica 1 mede 20 e 40 ms, Réplica 2 mede 60 e 80 ms, então a média das
    médias é 50 ms. O aquecimento de 500 ms e o ABORT do nível 25 ficam de fora.
    """
    medidas = {(10, 1): [0.5, 0.02, 0.04], (10, 2): [0.5, 0.06, 0.08],
               (25, 3): [0.5, 0.1, 0.2], (25, 4): [0.5, 0.3, 0.4]}
    linhas = []
    for (latencia, semente), valores in medidas.items():
        for ordem, valor in enumerate(valores):
            linhas.append(
                {"algoritmo": "2pc", "parametro_variado": "latencia_rede", "semente": semente,
                 "num_participantes": 3, "latencia_rede": latencia, "ordem": ordem,
                 "aquecimento": int(ordem == 0), "transacao": f"lat-{semente}-{ordem:03d}",
                 "decisao": "COMMIT", "latencia_commit_s": valor, "mensagens": 12}
            )
    linhas[-1]["decisao"] = "ABORT"
    return linhas


def _csv_2pc_latencia(caminho: Path) -> None:
    _escrever_csv(caminho, _COLUNAS_2PC_LATENCIA, _linhas_2pc_latencia())


def _csv_2pc_recuperacao(caminho: Path) -> None:
    """Duas Réplicas por nível, com tempos de recuperação de média conhecida."""
    colunas = [
        "algoritmo", "parametro_variado", "semente", "latencia_rede", "transacao",
        "decisao", "em_duvida", "tempo_recuperacao_s", "mensagens",
    ]
    tempos = {(10, 5): 1.0, (10, 6): 2.0, (25, 7): 1.5, (25, 8): 2.5}
    linhas = [
        {"algoritmo": "2pc", "parametro_variado": "latencia_rede", "semente": semente,
         "latencia_rede": latencia, "transacao": f"rec-{semente}", "decisao": "COMMIT",
         "em_duvida": 3, "tempo_recuperacao_s": tempo, "mensagens": 16}
        for (latencia, semente), tempo in tempos.items()
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
    assert (por_nos[3]["n_replicas"], por_nos[3]["n_convergiram"]) == (2, 2)


def test_anel_conta_so_as_convergidas_e_espera_2n_menos_1_com_perda() -> None:
    import pandas as pd

    tabela = anel_exp.consolidar(
        pd.DataFrame(
            [
                {"num_nos": 5, "taxa_falhas": 0.05, "tempo_convergencia": 9,
                 "mensagens": 9, "convergiu": 1},
                {"num_nos": 5, "taxa_falhas": 0.05, "tempo_convergencia": 11,
                 "mensagens": 11, "convergiu": 1},
                {"num_nos": 5, "taxa_falhas": 0.05, "tempo_convergencia": 99,
                 "mensagens": 99, "convergiu": 0},
            ]
        )
    )
    linha = tabela.iloc[0]
    assert (linha["n_replicas"], linha["n_convergiram"]) == (3, 2)
    assert linha["msgs_medio"] == pytest.approx(10.0)
    assert linha["msgs_esperado_sem_perda"] == 9
    assert linha["msgs_esperado_com_perda"] == pytest.approx(9 / 0.95)


def test_complexidade_do_2pc_compara_com_4n_e_2n_atrasos_de_ida() -> None:
    import pandas as pd

    def linha(latencia: int, semente: int, valor: float) -> dict:
        return {"latencia_rede": latencia, "semente": semente, "aquecimento": 0,
                "decisao": "COMMIT", "latencia_commit_s": valor, "mensagens": 12,
                "num_participantes": 3}

    tabela = doispc_exp.consolidar_complexidade(
        pd.DataFrame([linha(0, 1, 0.010), linha(0, 2, 0.010),
                      linha(10, 3, 0.085), linha(10, 4, 0.085)])
    )
    por_lat = {int(r["latencia_rede"]): r for _, r in tabela.iterrows()}
    assert por_lat[0]["mensagens_esperado"] == 12
    assert por_lat[10]["acrescimo_ms"] == pytest.approx(75.0)
    assert por_lat[10]["caminho_critico_ms"] == pytest.approx(60.0)
    assert por_lat[10]["razao"] == pytest.approx(1.25)
    assert pd.isna(por_lat[0]["razao"])


def test_consolidar_2pc_descarta_aquecimento_e_abort() -> None:
    import pandas as pd

    tabela = doispc_exp.consolidar_latencia(pd.DataFrame(_linhas_2pc_latencia()))
    por_lat = {int(r["latencia_rede"]): r for _, r in tabela.iterrows()}
    assert por_lat[10]["media_ms"] == pytest.approx(50.0)
    assert por_lat[10]["p50_ms"] == pytest.approx(50.0)  # mediana de [20, 40, 60, 80]
    assert (por_lat[10]["n_replicas"], por_lat[10]["n_transacoes"]) == (2, 4)
    # O ABORT de 400 ms sai, e a Réplica 4 fica só com 300 ms.
    assert por_lat[25]["n_transacoes"] == 3
    assert por_lat[25]["media_ms"] == pytest.approx((150.0 + 300.0) / 2)


def _preparar(tmp_path: Path) -> tuple:
    """CSVs sintéticos e diretórios de saída isolados para um teste de CLI."""
    anel_csv = tmp_path / "anel.csv"
    latencia_csv = tmp_path / "2pc_latencia.csv"
    recuperacao_csv = tmp_path / "2pc_recuperacao.csv"
    bully_csv = tmp_path / "bully.csv"
    _csv_anel(anel_csv)
    _csv_2pc_latencia(latencia_csv)
    _csv_2pc_recuperacao(recuperacao_csv)
    _csv_bully(bully_csv)
    return (
        anel_csv, latencia_csv, recuperacao_csv, bully_csv, tmp_path / "dados", tmp_path / "figs"
    )


def _args_comuns(
    anel_csv, latencia_csv, recuperacao_csv, bully_csv, dir_dados, dir_figs
) -> list[str]:
    return [
        "--anel-nos", str(anel_csv),
        "--doispc-latencia", str(latencia_csv),
        "--doispc-recuperacao", str(recuperacao_csv),
        "--bully-nos", str(bully_csv),
        "--dir-dados", str(dir_dados),
        "--dir-figuras", str(dir_figs),
    ]


def test_subcomandos_escrevem_tabelas_e_figuras(tmp_path: Path) -> None:
    comuns = _args_comuns(*_preparar(tmp_path))
    dir_dados, dir_figs = tmp_path / "dados", tmp_path / "figs"

    assert cli.main(["anel", *comuns]) == 0
    assert cli.main(["2pc-latencia", *comuns]) == 0
    assert cli.main(["2pc-recuperacao", *comuns]) == 0
    assert cli.main(["anel-complexidade", *comuns]) == 0
    assert cli.main(["2pc-complexidade", *comuns]) == 0
    assert cli.main(["bully", *comuns]) == 0
    assert cli.main(["bully-anel", *comuns]) == 0

    assert (dir_dados / "anel_complexidade.csv").exists()
    assert (dir_dados / "doispc_complexidade.csv").exists()
    assert (dir_dados / "anel_convergencia.csv").exists()
    assert (dir_figs / "anel_convergencia.png").exists()
    assert (dir_figs / "anel_convergencia.pdf").exists()
    assert (dir_dados / "doispc_latencia.csv").exists()
    assert (dir_figs / "doispc_latencia.png").exists()
    assert (dir_dados / "doispc_recuperacao.csv").exists()
    assert (dir_dados / "bully_complexidade.csv").exists()
    assert (dir_dados / "anel_bully.csv").exists()
    assert (dir_figs / "anel_bully.png").exists()


def test_conferir_passa_em_doc_fresco_e_reprova_em_doc_defasado(
    tmp_path: Path, monkeypatch
) -> None:
    preparados = _preparar(tmp_path)
    anel_csv, latencia_csv, recuperacao_csv, bully_csv, _, _ = preparados
    comuns = _args_comuns(*preparados)

    # Aponta o documento de cada tabela para o tmp, com o mesmo nome.
    doc_anel = tmp_path / "resultados-anel.md"
    doc_2pc = tmp_path / "resultados-2pc.md"
    doc_bully = tmp_path / "resultados-bully.md"
    monkeypatch.setattr(
        cli,
        "TABELAS",
        tuple(replace(t, documento=tmp_path / t.documento.name) for t in cli.TABELAS),
    )

    df_anel = dados.carregar_anel(anel_csv)

    # Documento FRESCO: cola exatamente as tabelas regeneradas.
    bloco_anel = anel_exp.como_markdown(
        anel_exp.consolidar(df_anel), cli.COMANDOS["anel"], [dados.relativo(anel_csv)]
    )
    bloco_anel_complexidade = anel_exp.complexidade_como_markdown(
        anel_exp.consolidar(df_anel),
        cli.COMANDOS["anel-complexidade"],
        [dados.relativo(anel_csv)],
    )
    bloco_2pc_complexidade = doispc_exp.complexidade_como_markdown(
        doispc_exp.consolidar_complexidade(dados.carregar_2pc_latencia(latencia_csv)),
        cli.COMANDOS["2pc-complexidade"],
        [dados.relativo(latencia_csv)],
    )
    bloco_lat = doispc_exp.latencia_como_markdown(
        doispc_exp.consolidar_latencia(dados.carregar_2pc_latencia(latencia_csv)),
        cli.COMANDOS["2pc-latencia"],
        [dados.relativo(latencia_csv)],
    )
    bloco_rec = doispc_exp.recuperacao_como_markdown(
        doispc_exp.consolidar_recuperacao(dados.carregar_2pc_recuperacao(recuperacao_csv)),
        cli.COMANDOS["2pc-recuperacao"],
        [dados.relativo(recuperacao_csv)],
    )
    df_bully = dados.carregar_bully(bully_csv)
    bloco_bully = bully_exp.complexidade_como_markdown(
        bully_exp.consolidar(df_bully), cli.COMANDOS["bully"], [dados.relativo(bully_csv)]
    )
    bloco_bully_anel = bully_exp.comparacao_como_markdown(
        bully_exp.comparar_com_anel(df_anel, df_bully),
        cli.COMANDOS["bully-anel"],
        [dados.relativo(anel_csv), dados.relativo(bully_csv)],
    )
    doc_anel.write_text(f"# Anel\n\n{bloco_anel}\n\n{bloco_anel_complexidade}\n", encoding="utf-8")
    doc_bully.write_text(f"# Bully\n\n{bloco_bully}\n\n{bloco_bully_anel}\n", encoding="utf-8")
    doc_2pc.write_text(
        f"# 2PC\n\n{bloco_lat}\n\n{bloco_2pc_complexidade}\n\n{bloco_rec}\n", encoding="utf-8"
    )

    assert cli.main(["conferir", *comuns]) == 0

    # Agora torna o doc do Anel DEFASADO, trocando um dígito da tabela.
    fresco = doc_anel.read_text(encoding="utf-8")
    defasado = fresco.replace("5,00", "9,99", 1)
    assert defasado != fresco
    doc_anel.write_text(defasado, encoding="utf-8")

    assert cli.main(["conferir", *comuns]) == 1

    # O mesmo vale para o documento do Bully, com o do Anel de novo em dia.
    doc_anel.write_text(fresco, encoding="utf-8")
    assert cli.main(["conferir", *comuns]) == 0
    defasado = doc_bully.read_text(encoding="utf-8").replace("24,00", "25,00", 1)
    assert defasado != doc_bully.read_text(encoding="utf-8")
    doc_bully.write_text(defasado, encoding="utf-8")

    assert cli.main(["conferir", *comuns]) == 1


def test_dados_ausente_reprova_com_mensagem(tmp_path: Path) -> None:
    codigo = cli.main(
        [
            "anel",
            "--anel-nos", str(tmp_path / "nao_existe.csv"),
            "--doispc-latencia", str(tmp_path / "tambem_nao.csv"),
        ]
    )
    assert codigo == 1
