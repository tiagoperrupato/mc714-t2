"""Análise dos experimentos da Eleição em Anel (Requisito 11.3).

Lê o CSV da bateria do Anel e consolida a convergência em função do número de
Nós: por nível, a média e o IC 95% do Tempo_de_Convergencia (em passos lógicos)
e do número de Mensagens, mais a fração de Réplicas que convergiram. Produz a
tabela markdown de `docs/` e a figura do Tempo_de_Convergencia × número de Nós.

Convenção de métrica: o Tempo_de_Convergencia é medido em passos lógicos de
entrega (ver `runner_anel`), e não em segundos de relógio, para ser reproduzível
dígito a dígito entre máquinas (limitação de ambiente do Requisito 13.7).
"""

from __future__ import annotations

import pandas as pd

from src.analise import formato, graficos
from src.analise.estatistica import resumir

# Pelo menos duas Réplicas por nível para que o IC 95% tenha graus de liberdade.
MINIMO_REPLICAS: int = 2


def consolidar(df: pd.DataFrame) -> pd.DataFrame:
    """Média e IC 95% de Tempo_de_Convergencia e Mensagens por número de Nós.

    Uma linha por nível de `num_nos`, ordenada pelo número de Nós. Inclui a
    fração de Réplicas convergidas, para que um nível instável não se esconda
    atrás de uma média de poucos sucessos.
    """
    linhas = []
    for num_nos, grupo in df.groupby("num_nos", sort=True):
        convergiu = grupo["convergiu"].astype(float)
        convergidas = grupo[convergiu > 0.5]
        if convergidas.empty:
            continue
        resumo_tempo = resumir(convergidas["tempo_convergencia"].to_numpy(dtype=float))
        resumo_msgs = resumir(convergidas["mensagens"].to_numpy(dtype=float))
        linhas.append(
            {
                "num_nos": int(num_nos),
                "n_replicas": int(len(grupo)),
                "frac_convergiu": float(convergiu.mean()),
                "tempo_medio": resumo_tempo.media,
                "tempo_ic95": resumo_tempo.ic95_meio,
                "msgs_medio": resumo_msgs.media,
                "msgs_ic95": resumo_msgs.ic95_meio,
            }
        )
    tabela = pd.DataFrame(linhas)
    if tabela.empty:
        return tabela
    return tabela.sort_values("num_nos").reset_index(drop=True)


def como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown da convergência do Anel por número de Nós."""
    linhas = [
        {
            "Nós": formato.inteiro(r["num_nos"]),
            "n": formato.inteiro(r["n_replicas"]),
            "Convergiu": formato.numero(r["frac_convergiu"] * 100.0, 1) + " %",
            "Tempo médio (passos)": formato.numero(r["tempo_medio"], 2),
            "IC 95% (±)": formato.numero(r["tempo_ic95"], 2),
            "Mensagens médias": formato.numero(r["msgs_medio"], 2),
            "IC 95% msgs (±)": formato.numero(r["msgs_ic95"], 2),
        }
        for _, r in tabela.iterrows()
    ]
    colunas = [
        "Nós",
        "n",
        "Convergiu",
        "Tempo médio (passos)",
        "IC 95% (±)",
        "Mensagens médias",
        "IC 95% msgs (±)",
    ]
    return (
        formato.tabela_markdown(linhas, colunas, colunas_texto=["Nós"])
        + "\n\n"
        + formato.rodape_reproducao(comando, fontes)
    )


def gerar_grafico(tabela: pd.DataFrame, destino) -> None:
    """Figura do Tempo_de_Convergencia × número de Nós, com barras de IC 95%."""
    fig, ax = graficos.nova_figura()
    ax.errorbar(
        tabela["num_nos"],
        tabela["tempo_medio"],
        yerr=tabela["tempo_ic95"],
        marker="o",
        capsize=4,
        linestyle="-",
    )
    ax.set_xlabel("Número de Nós")
    ax.set_ylabel("Tempo de convergência (passos lógicos)")
    ax.set_title("Convergência da Eleição em Anel × número de Nós")
    ax.grid(True, linestyle=":", alpha=0.6)
    graficos.salvar(fig, destino)
