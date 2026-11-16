"""Análise da bateria do Bully: Mensagens contra o esperado e comparação com o Anel.

Com o Nó 1 iniciador, todo Nó convoca uma vez, cada ELEICAO recebe um OK e o maior manda
n − 1 COORDENADOR, o pior caso de n² − 1 Mensagens até não restar nenhuma em trânsito. O
melhor caso, o maior que convoca, custa n − 1. A comparação com o Anel usa as Mensagens e os
saltos até a convergência, a mesma parada nas duas baterias, e exige os mesmos níveis, perda e
Sementes.
"""

from __future__ import annotations

import math
from typing import Iterator

import pandas as pd

from src.analise import formato, graficos
from src.analise.dados import DadosError
from src.analise.estatistica import resumir


def _niveis(df: pd.DataFrame) -> Iterator[tuple[int, pd.DataFrame, pd.DataFrame, float]]:
    """Por número de Nós: as Réplicas, as que convergiram e a perda, NaN se o nível mistura."""
    for num_nos, grupo in df.groupby("num_nos", sort=True):
        convergidas = grupo[grupo["convergiu"].astype(float) > 0.5]
        taxas = grupo["taxa_falhas"].astype(float).unique()
        perda = float(taxas[0]) if len(taxas) == 1 else math.nan
        yield int(num_nos), grupo, convergidas, perda


def consolidar(df: pd.DataFrame) -> pd.DataFrame:
    """Mensagens até esvaziar a fila, por número de Nós, e os custos esperados."""
    linhas = []
    for n, grupo, convergidas, perda in _niveis(df):
        if convergidas.empty:
            continue
        resumo = resumir(convergidas["mensagens_total"].to_numpy(dtype=float))
        linhas.append(
            {
                "num_nos": n,
                "n_replicas": int(len(grupo)),
                "n_convergiram": resumo.n,
                "taxa_falhas": perda,
                "msgs_total_medio": resumo.media,
                "msgs_total_ic95": resumo.ic95_meio,
                "msgs_melhor_caso": n - 1,
                "msgs_pior_caso": n * n - 1,
                "msgs_pior_caso_com_perda": (n * n - 1) / (1.0 - perda),
            }
        )
    return pd.DataFrame(linhas)


def complexidade_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown das Mensagens do Bully contra o melhor e o pior caso."""
    linhas = [
        {
            "Nós (n)": formato.inteiro(r["num_nos"]),
            "Perda (p)": formato.numero(r["taxa_falhas"], 2),
            "Convergiram": formato.inteiro(r["n_convergiram"]),
            "Mensagens médias": formato.numero(r["msgs_total_medio"], 2),
            "IC 95% (±)": formato.numero(r["msgs_total_ic95"], 2),
            "n − 1": formato.inteiro(r["msgs_melhor_caso"]),
            "n² − 1": formato.inteiro(r["msgs_pior_caso"]),
            "(n² − 1)/(1 − p)": formato.numero(r["msgs_pior_caso_com_perda"], 2),
        }
        for _, r in tabela.iterrows()
    ]
    return formato.tabela_markdown(linhas, ["Nós (n)"], comando, fontes)


def _exigir_mesmas_condicoes(df_anel: pd.DataFrame, df_bully: pd.DataFrame) -> None:
    """Levanta `DadosError` se as baterias diferem em nível, Sementes ou perda."""
    regenerar = (
        "Regenere as duas com os mesmos parâmetros: python scripts/bateria.py --algoritmo anel "
        "e python scripts/bateria.py --algoritmo bully"
    )
    niveis_anel = sorted(df_anel["num_nos"].unique().tolist())
    niveis_bully = sorted(df_bully["num_nos"].unique().tolist())
    if niveis_anel != niveis_bully:
        raise DadosError(
            f"níveis de num_nos diferentes: Anel {niveis_anel}, Bully {niveis_bully}. {regenerar}"
        )
    for n in niveis_anel:
        for nome, coluna in (("Sementes", "semente"), ("perda", "taxa_falhas")):
            anel = sorted(df_anel.loc[df_anel["num_nos"] == n, coluna].unique().tolist())
            bully = sorted(df_bully.loc[df_bully["num_nos"] == n, coluna].unique().tolist())
            if anel != bully:
                raise DadosError(
                    f"{nome} diferentes em num_nos = {n}: Anel {anel}, Bully {bully}. {regenerar}"
                )


def comparar_com_anel(df_anel: pd.DataFrame, df_bully: pd.DataFrame) -> pd.DataFrame:
    """Média e IC 95% das Mensagens e dos saltos até a convergência, por algoritmo e nível."""
    _exigir_mesmas_condicoes(df_anel, df_bully)
    convergidas_bully = {n: convergidas for n, _, convergidas, _ in _niveis(df_bully)}
    linhas = []
    for n, _, convergidas_anel, perda in _niveis(df_anel):
        por_algoritmo = {"anel": convergidas_anel, "bully": convergidas_bully[n]}
        if any(c.empty for c in por_algoritmo.values()):
            continue
        linha: dict[str, float] = {"num_nos": n, "taxa_falhas": perda}
        for algoritmo, convergidas in por_algoritmo.items():
            msgs = resumir(convergidas["mensagens"].to_numpy(dtype=float))
            tempo = resumir(convergidas["tempo_convergencia"].to_numpy(dtype=float))
            linha |= {
                f"{algoritmo}_n_convergiram": msgs.n,
                f"{algoritmo}_msgs_medio": msgs.media,
                f"{algoritmo}_msgs_ic95": msgs.ic95_meio,
                f"{algoritmo}_tempo_medio": tempo.media,
                f"{algoritmo}_tempo_ic95": tempo.ic95_meio,
            }
        linhas.append(linha)
    return pd.DataFrame(linhas)


def _media_ic(media: float, ic95: float) -> str:
    return f"{formato.numero(media, 2)} ± {formato.numero(ic95, 2)}"


def comparacao_como_markdown(tabela: pd.DataFrame, comando: str, fontes: list[str]) -> str:
    """Tabela markdown do Anel contra o Bully, com média ± IC 95% em cada célula."""
    linhas = [
        {
            "Nós (n)": formato.inteiro(r["num_nos"]),
            "Perda (p)": formato.numero(r["taxa_falhas"], 2),
            "Convergiram no Anel": formato.inteiro(r["anel_n_convergiram"]),
            "Convergiram no Bully": formato.inteiro(r["bully_n_convergiram"]),
            "Mensagens no Anel": _media_ic(r["anel_msgs_medio"], r["anel_msgs_ic95"]),
            "Mensagens no Bully": _media_ic(r["bully_msgs_medio"], r["bully_msgs_ic95"]),
            "Saltos no Anel": _media_ic(r["anel_tempo_medio"], r["anel_tempo_ic95"]),
            "Saltos no Bully": _media_ic(r["bully_tempo_medio"], r["bully_tempo_ic95"]),
        }
        for _, r in tabela.iterrows()
    ]
    return formato.tabela_markdown(linhas, ["Nós (n)"], comando, fontes)


def gerar_grafico(tabela: pd.DataFrame, destino) -> None:
    """Mensagens até a convergência × número de Nós, nos dois algoritmos, com IC 95%."""
    fig, ax = graficos.nova_figura()
    for algoritmo, rotulo, marcador in (("anel", "Anel", "o"), ("bully", "Bully", "s")):
        ax.errorbar(
            tabela["num_nos"],
            tabela[f"{algoritmo}_msgs_medio"],
            yerr=tabela[f"{algoritmo}_msgs_ic95"],
            marker=marcador,
            capsize=4,
            linestyle="-",
            label=rotulo,
        )
    ax.set_xlabel("Número de Nós")
    ax.set_ylabel("Mensagens até a convergência")
    ax.set_title("Mensagens até a convergência: Anel × Bully")
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend()
    graficos.salvar(fig, destino)
