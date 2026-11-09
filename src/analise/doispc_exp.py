"""Análise da bateria do 2PC nos nós reais: latência de commit e tempo de recuperação.

A latência entra por transação, sem as de aquecimento. Média e IC 95% saem das médias por Réplica,
que são as amostras independentes, e os percentis saem de todas as transações do nível. A recuperação
tem uma amostra por Réplica, então a tabela mostra mínimo e máximo, e não percentis.
"""

from __future__ import annotations

import pandas as pd

from src.analise import formato, graficos
from src.analise.estatistica import percentis, resumir


def _medidas(df: pd.DataFrame) -> pd.DataFrame:
    """Transações fora do aquecimento que decidiram COMMIT."""
    return df[(df["aquecimento"].astype(int) == 0) & (df["decisao"] == "COMMIT")]


def consolidar_latencia(df: pd.DataFrame) -> pd.DataFrame:
    """Latência de commit em ms por nível de latência de rede, com o n efetivo de cada linha."""
    linhas = []
    for latencia_rede, grupo in _medidas(df).groupby("latencia_rede", sort=True):
        por_replica = grupo.groupby("semente")["latencia_commit_s"].mean().to_numpy(dtype=float)
        resumo = resumir(por_replica * 1000.0)
        p = percentis(grupo["latencia_commit_s"].to_numpy(dtype=float) * 1000.0)
        linhas.append(
            {
                "latencia_rede": int(latencia_rede),
                "n_replicas": resumo.n,
                "n_transacoes": p.n,
                "media_ms": resumo.media,
                "ic95_ms": resumo.ic95_meio,
                "p50_ms": p.p50,
                "p95_ms": p.p95,
                "p99_ms": p.p99,
                "mensagens": float(grupo["mensagens"].mean()),
            }
        )
    return pd.DataFrame(linhas)


def consolidar_recuperacao(df: pd.DataFrame) -> pd.DataFrame:
    """Tempo de recuperação em s por nível de latência de rede, das Réplicas que terminaram em COMMIT."""
    linhas = []
    for latencia_rede, grupo in df[df["decisao"] == "COMMIT"].groupby("latencia_rede", sort=True):
        tempos = grupo["tempo_recuperacao_s"].to_numpy(dtype=float)
        resumo = resumir(tempos)
        linhas.append(
            {
                "latencia_rede": int(latencia_rede),
                "n_replicas": resumo.n,
                "media_s": resumo.media,
                "ic95_s": resumo.ic95_meio,
                "min_s": float(tempos.min()),
                "max_s": float(tempos.max()),
                "mensagens": float(grupo["mensagens"].mean()),
            }
        )
    return pd.DataFrame(linhas)


def latencia_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown da latência de commit × latência de rede."""
    linhas = [
        {
            "Latência de rede (ms)": formato.inteiro(r["latencia_rede"]),
            "Réplicas": formato.inteiro(r["n_replicas"]),
            "Transações": formato.inteiro(r["n_transacoes"]),
            "Média (ms)": formato.numero(r["media_ms"], 1),
            "IC 95% (±)": formato.numero(r["ic95_ms"], 1),
            "p50 (ms)": formato.numero(r["p50_ms"], 1),
            "p95 (ms)": formato.numero(r["p95_ms"], 1),
            "p99 (ms)": formato.numero(r["p99_ms"], 1),
            "Mensagens": formato.numero(r["mensagens"], 2),
        }
        for _, r in tabela.iterrows()
    ]
    colunas = list(linhas[0]) if linhas else []
    return (
        formato.tabela_markdown(linhas, colunas, colunas_texto=["Latência de rede (ms)"])
        + "\n\n"
        + formato.rodape_reproducao(comando, fontes)
    )


def recuperacao_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown do tempo de recuperação × latência de rede."""
    linhas = [
        {
            "Latência de rede (ms)": formato.inteiro(r["latencia_rede"]),
            "Réplicas": formato.inteiro(r["n_replicas"]),
            "Média (s)": formato.numero(r["media_s"], 3),
            "IC 95% (±)": formato.numero(r["ic95_s"], 3),
            "Mínimo (s)": formato.numero(r["min_s"], 3),
            "Máximo (s)": formato.numero(r["max_s"], 3),
            "Mensagens": formato.numero(r["mensagens"], 2),
        }
        for _, r in tabela.iterrows()
    ]
    colunas = list(linhas[0]) if linhas else []
    return (
        formato.tabela_markdown(linhas, colunas, colunas_texto=["Latência de rede (ms)"])
        + "\n\n"
        + formato.rodape_reproducao(comando, fontes)
    )


def gerar_grafico_latencia(tabela: pd.DataFrame, destino) -> None:
    """Figura da latência de commit × latência de rede: média (IC 95%), p95 e p99."""
    fig, ax = graficos.nova_figura()
    ax.errorbar(
        tabela["latencia_rede"],
        tabela["media_ms"],
        yerr=tabela["ic95_ms"],
        marker="o",
        capsize=4,
        linestyle="-",
        label="média (IC 95%)",
    )
    ax.plot(tabela["latencia_rede"], tabela["p95_ms"], marker="s", linestyle="--", label="p95")
    ax.plot(tabela["latencia_rede"], tabela["p99_ms"], marker="^", linestyle=":", label="p99")
    ax.set_xlabel("Latência de rede, atraso de ida em cada sentido (ms)")
    ax.set_ylabel("Latência de commit vista pelo cliente (ms)")
    ax.set_title("Latência de commit do 2PC × latência de rede")
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend()
    graficos.salvar(fig, destino)


def gerar_grafico_recuperacao(tabela: pd.DataFrame, destino) -> None:
    """Figura do tempo de recuperação × latência de rede, com barras de IC 95%."""
    fig, ax = graficos.nova_figura()
    ax.errorbar(
        tabela["latencia_rede"],
        tabela["media_s"],
        yerr=tabela["ic95_s"],
        marker="o",
        capsize=4,
        linestyle="-",
    )
    ax.set_xlabel("Latência de rede, atraso de ida em cada sentido (ms)")
    ax.set_ylabel("Do reinício do Coordenador até todos decidirem (s)")
    ax.set_title("Tempo de recuperação do 2PC × latência de rede")
    ax.grid(True, linestyle=":", alpha=0.6)
    graficos.salvar(fig, destino)
