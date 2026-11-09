"""Análise da bateria da Eleição em Anel: convergência e mensagens contra o esperado, por número de Nós.

O tempo é medido em passos lógicos de entrega (ver `runner_anel`), reproduzíveis dígito a dígito entre
máquinas. Média e IC saem só das Réplicas que convergiram, e as tabelas mostram quantas foram.

Uma rodada completa com um iniciador custa 2n mensagens: n de eleição até a lista voltar ao iniciador
e n de anúncio do Líder até o anúncio voltar a ele. A bateria para quando o último Nó conhece o Líder,
um salto antes, e por isso espera 2n − 1. Com perda p e reenvio até a entrega, cada mensagem custa em
média 1/(1 − p) tentativas.
"""

from __future__ import annotations

import math

import pandas as pd

from src.analise import formato, graficos
from src.analise.estatistica import resumir


def consolidar(df: pd.DataFrame) -> pd.DataFrame:
    """Tempo de convergência e mensagens por número de Nós, com o esperado das mensagens.

    O esperado com perda fica ausente quando o nível mistura taxas de perda diferentes.
    """
    linhas = []
    for num_nos, grupo in df.groupby("num_nos", sort=True):
        convergidas = grupo[grupo["convergiu"].astype(float) > 0.5]
        if convergidas.empty:
            continue
        resumo_tempo = resumir(convergidas["tempo_convergencia"].to_numpy(dtype=float))
        resumo_msgs = resumir(convergidas["mensagens"].to_numpy(dtype=float))
        taxas = grupo["taxa_falhas"].astype(float).unique()
        perda = float(taxas[0]) if len(taxas) == 1 else math.nan
        sem_perda = 2 * int(num_nos) - 1
        linhas.append(
            {
                "num_nos": int(num_nos),
                "n_replicas": int(len(grupo)),
                "n_convergiram": resumo_tempo.n,
                "tempo_medio": resumo_tempo.media,
                "tempo_ic95": resumo_tempo.ic95_meio,
                "taxa_falhas": perda,
                "msgs_medio": resumo_msgs.media,
                "msgs_ic95": resumo_msgs.ic95_meio,
                "msgs_esperado_sem_perda": sem_perda,
                "msgs_esperado_com_perda": sem_perda / (1.0 - perda),
            }
        )
    return pd.DataFrame(linhas)


def como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown da convergência do Anel por número de Nós."""
    linhas = [
        {
            "Nós": formato.inteiro(r["num_nos"]),
            "Réplicas": formato.inteiro(r["n_replicas"]),
            "Convergiram": formato.inteiro(r["n_convergiram"]),
            "Tempo médio (passos)": formato.numero(r["tempo_medio"], 2),
            "IC 95% (±)": formato.numero(r["tempo_ic95"], 2),
        }
        for _, r in tabela.iterrows()
    ]
    colunas = list(linhas[0]) if linhas else []
    return (
        formato.tabela_markdown(linhas, colunas, colunas_texto=["Nós"])
        + "\n\n"
        + formato.rodape_reproducao(comando, fontes)
    )


def complexidade_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown das mensagens medidas contra o esperado, por número de Nós."""
    linhas = [
        {
            "Nós (n)": formato.inteiro(r["num_nos"]),
            "Perda (p)": formato.numero(r["taxa_falhas"], 2),
            "Convergiram": formato.inteiro(r["n_convergiram"]),
            "Mensagens médias": formato.numero(r["msgs_medio"], 2),
            "IC 95% (±)": formato.numero(r["msgs_ic95"], 2),
            "2n − 1": formato.inteiro(r["msgs_esperado_sem_perda"]),
            "(2n − 1)/(1 − p)": formato.numero(r["msgs_esperado_com_perda"], 2),
        }
        for _, r in tabela.iterrows()
    ]
    colunas = list(linhas[0]) if linhas else []
    return (
        formato.tabela_markdown(linhas, colunas, colunas_texto=["Nós (n)"])
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
