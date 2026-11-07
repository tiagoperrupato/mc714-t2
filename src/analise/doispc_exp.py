"""Análise dos experimentos do Two-Phase Commit (Requisito 11.3).

Cobre as duas visões exigidas pelo enunciado a partir do CSV da bateria do 2PC:

- **Latencia_de_Commit × latência de rede**: por nível de `latencia_rede`, a
  média e o IC 95% da Latencia_de_Commit e os percentis p50/p95/p99 dessa
  latência (Requisito 11.2). Latência é a métrica que mais pede percentis: a
  cauda (p95/p99) revela o pior caso que a média esconde.
- **Tempo_de_Recuperacao sob falha**: a média, o IC 95% e os percentis do
  Tempo_de_Recuperacao, o custo lógico de um Participante reler seu Log_Duravel
  após reinício (Requisito 6.6), medido na bateria quando a recuperação é
  simulada.

Como o Tempo_de_Convergencia do Anel, a Latencia_de_Commit e o
Tempo_de_Recuperacao são tempos lógicos reproduzíveis por Semente, não segundos
de relógio (limitação de ambiente do Requisito 13.7).
"""

from __future__ import annotations

import pandas as pd

from src.analise import formato, graficos
from src.analise.estatistica import percentis, resumir

MINIMO_REPLICAS: int = 2


def consolidar_latencia(df: pd.DataFrame) -> pd.DataFrame:
    """Latencia_de_Commit por nível de latência de rede: média, IC 95% e percentis.

    Uma linha por nível de `latencia_rede`, ordenada pela latência de rede.
    """
    linhas = []
    for latencia_rede, grupo in df.groupby("latencia_rede", sort=True):
        valores = grupo["latencia_commit"].to_numpy(dtype=float)
        resumo = resumir(valores)
        p = percentis(valores)
        linhas.append(
            {
                "latencia_rede": float(latencia_rede),
                "n_replicas": int(len(grupo)),
                "media": resumo.media,
                "ic95_meio": resumo.ic95_meio,
                "p50": p.p50,
                "p95": p.p95,
                "p99": p.p99,
            }
        )
    tabela = pd.DataFrame(linhas)
    if tabela.empty:
        return tabela
    return tabela.sort_values("latencia_rede").reset_index(drop=True)


def consolidar_recuperacao(df: pd.DataFrame) -> pd.DataFrame:
    """Tempo_de_Recuperacao por nível de latência de rede: média, IC 95% e percentis.

    Agrupado pelo mesmo nível de `latencia_rede` da bateria, para que a tabela de
    recuperação e a de latência compartilhem o eixo e sejam lidas lado a lado.
    """
    linhas = []
    for latencia_rede, grupo in df.groupby("latencia_rede", sort=True):
        valores = grupo["tempo_recuperacao"].to_numpy(dtype=float)
        resumo = resumir(valores)
        p = percentis(valores)
        linhas.append(
            {
                "latencia_rede": float(latencia_rede),
                "n_replicas": int(len(grupo)),
                "media": resumo.media,
                "ic95_meio": resumo.ic95_meio,
                "p50": p.p50,
                "p95": p.p95,
                "p99": p.p99,
            }
        )
    tabela = pd.DataFrame(linhas)
    if tabela.empty:
        return tabela
    return tabela.sort_values("latencia_rede").reset_index(drop=True)


def _markdown_percentis(
    tabela: pd.DataFrame, titulo_metrica: str, comando: str, fontes: list[str]
) -> str:
    """Tabela markdown genérica de uma métrica de latência por latência de rede."""
    linhas = [
        {
            "Latência de rede": formato.numero(r["latencia_rede"], 1),
            "n": formato.inteiro(r["n_replicas"]),
            f"{titulo_metrica} média": formato.numero(r["media"], 4),
            "IC 95% (±)": formato.numero(r["ic95_meio"], 4),
            "p50": formato.numero(r["p50"], 4),
            "p95": formato.numero(r["p95"], 4),
            "p99": formato.numero(r["p99"], 4),
        }
        for _, r in tabela.iterrows()
    ]
    colunas = [
        "Latência de rede",
        "n",
        f"{titulo_metrica} média",
        "IC 95% (±)",
        "p50",
        "p95",
        "p99",
    ]
    return (
        formato.tabela_markdown(linhas, colunas, colunas_texto=["Latência de rede"])
        + "\n\n"
        + formato.rodape_reproducao(comando, fontes)
    )


def latencia_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown da Latencia_de_Commit × latência de rede."""
    return _markdown_percentis(tabela, "Latência de commit", comando, fontes)


def recuperacao_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown do Tempo_de_Recuperacao × latência de rede."""
    return _markdown_percentis(tabela, "Recuperação", comando, fontes)


def gerar_grafico_latencia(tabela: pd.DataFrame, destino) -> None:
    """Figura da Latencia_de_Commit × latência de rede: média (IC 95%) e p95/p99."""
    fig, ax = graficos.nova_figura()
    ax.errorbar(
        tabela["latencia_rede"],
        tabela["media"],
        yerr=tabela["ic95_meio"],
        marker="o",
        capsize=4,
        linestyle="-",
        label="média (IC 95%)",
    )
    ax.plot(tabela["latencia_rede"], tabela["p95"], marker="s", linestyle="--", label="p95")
    ax.plot(tabela["latencia_rede"], tabela["p99"], marker="^", linestyle=":", label="p99")
    ax.set_xlabel("Latência de rede (unidades lógicas)")
    ax.set_ylabel("Latência de commit (unidades lógicas)")
    ax.set_title("Latência de commit do 2PC × latência de rede")
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend()
    graficos.salvar(fig, destino)


def gerar_grafico_recuperacao(tabela: pd.DataFrame, destino) -> None:
    """Figura do Tempo_de_Recuperacao × latência de rede, com barras de IC 95%."""
    fig, ax = graficos.nova_figura()
    ax.errorbar(
        tabela["latencia_rede"],
        tabela["media"],
        yerr=tabela["ic95_meio"],
        marker="o",
        capsize=4,
        linestyle="-",
    )
    ax.set_xlabel("Latência de rede (unidades lógicas)")
    ax.set_ylabel("Tempo de recuperação (unidades lógicas)")
    ax.set_title("Tempo de recuperação do 2PC sob falha × latência de rede")
    ax.grid(True, linestyle=":", alpha=0.6)
    graficos.salvar(fig, destino)
