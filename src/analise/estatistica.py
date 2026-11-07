"""Tendência central, variabilidade, percentis e IC 95% das métricas das Réplicas.

A média vem acompanhada do `n` e do IC 95% pela
t de Student (média ± t(gl; 0,975) · s/√n), porque o t depende dos graus de
liberdade e nunca é constante. O T2 acrescenta os percentis p50/p95/p99 exigidos
para as latências (Requisito 11.2), calculados pelo método linear de
interpolação do numpy, estável e reproduzível.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Quantis de 0,975 da t de Student por graus de liberdade, tabelados para não
# depender de scipy.
_T_CRITICO_95: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
    40: 2.021, 60: 2.000, 120: 1.980,
}

_T_NORMAL_95: float = 1.960


def t_critico_95(gl: int) -> float:
    """Quantil de 0,975 da t com `gl` graus de liberdade.

    Exato até 30. Acima disso usa a maior entrada tabelada que não exceda `gl`, o
    que alarga o intervalo em vez de estreitá-lo. Acima de 120 devolve 1,960.
    """
    if gl < 1:
        raise ValueError(
            f"graus de liberdade devem ser ao menos 1 (n >= 2 réplicas); recebido: {gl}"
        )
    if gl in _T_CRITICO_95:
        return _T_CRITICO_95[gl]
    candidatos = [g for g in _T_CRITICO_95 if g <= gl]
    if not candidatos:
        return _T_NORMAL_95
    return _T_CRITICO_95[max(candidatos)] if gl <= 120 else _T_NORMAL_95


@dataclass(frozen=True)
class Resumo:
    """n, média, desvio amostral (ddof=1) e meia-largura do IC 95% de uma amostra."""

    n: int
    media: float
    desvio: float
    ic95_meio: float

    @property
    def ic95_inf(self) -> float:
        return self.media - self.ic95_meio

    @property
    def ic95_sup(self) -> float:
        return self.media + self.ic95_meio


def resumir(valores) -> Resumo:
    """Resume uma amostra, descartando NaN antes.

    Com uma única réplica o desvio sai 0,0 e o intervalo sai NaN, em vez de
    fingir um intervalo de largura zero.
    """
    amostra = np.asarray(valores, dtype=float)
    amostra = amostra[~np.isnan(amostra)]
    n = int(amostra.size)
    if n == 0:
        raise ValueError("amostra vazia: nenhum valor válido para resumir")

    media = float(np.mean(amostra))
    if n == 1:
        return Resumo(n=1, media=media, desvio=0.0, ic95_meio=float("nan"))

    desvio = float(np.std(amostra, ddof=1))
    erro_padrao = desvio / math.sqrt(n)
    ic95_meio = t_critico_95(n - 1) * erro_padrao
    return Resumo(n=n, media=media, desvio=desvio, ic95_meio=ic95_meio)


@dataclass(frozen=True)
class Percentis:
    """n e percentis p50, p95 e p99 de uma amostra de latências (Requisito 11.2)."""

    n: int
    p50: float
    p95: float
    p99: float


# Os percentis de latência exigidos pelo enunciado (Requisito 11.2).
PERCENTIS_LATENCIA: tuple[int, ...] = (50, 95, 99)


def percentis(valores, quantis: tuple[int, ...] = PERCENTIS_LATENCIA) -> Percentis:
    """p50/p95/p99 de uma amostra, descartando NaN.

    Usa interpolação linear entre as posições (o padrão do numpy), que é a
    convenção mais comum para percentis amostrais de latência e coincide com a
    mediana usual em p50. Levanta `ValueError` se a amostra ficar vazia.
    """
    if quantis != PERCENTIS_LATENCIA:
        raise ValueError(
            f"os percentis de latência do enunciado são {PERCENTIS_LATENCIA}; "
            f"recebido {quantis}"
        )
    amostra = np.asarray(valores, dtype=float)
    amostra = amostra[~np.isnan(amostra)]
    n = int(amostra.size)
    if n == 0:
        raise ValueError("amostra vazia: nenhum valor válido para os percentis")
    p50, p95, p99 = (float(np.percentile(amostra, q)) for q in quantis)
    return Percentis(n=n, p50=p50, p95=p95, p99=p99)


def agregar(
    df: pd.DataFrame,
    chaves: list[str],
    metricas: list[str],
    minimo_replicas: int = 1,
) -> pd.DataFrame:
    """Resume `metricas` por grupo de `chaves`, em formato longo: uma linha por métrica.

    Grupos com menos de `minimo_replicas` valores válidos são descartados. Cada
    linha traz n, média, desvio e o IC 95% do grupo (Requisito 11.2).
    """
    linhas = []
    for valores_chave, grupo in df.groupby(chaves, sort=True):
        if not isinstance(valores_chave, tuple):
            valores_chave = (valores_chave,)
        for metrica in metricas:
            serie = grupo[metrica].to_numpy(dtype=float)
            serie = serie[~np.isnan(serie)]
            if serie.size < minimo_replicas:
                continue
            resumo = resumir(serie)
            linhas.append(
                dict(zip(chaves, valores_chave))
                | {
                    "metrica": metrica,
                    "n_replicas": resumo.n,
                    "media": resumo.media,
                    "desvio_padrao": resumo.desvio,
                    "ic95_meio": resumo.ic95_meio,
                    "ic95_inf": resumo.ic95_inf,
                    "ic95_sup": resumo.ic95_sup,
                }
            )
    return pd.DataFrame(linhas)


def agregar_percentis(
    df: pd.DataFrame,
    chaves: list[str],
    metrica: str,
    minimo_replicas: int = 1,
) -> pd.DataFrame:
    """p50/p95/p99 de uma métrica de latência por grupo de `chaves` (Requisito 11.2).

    Uma linha por grupo, com n e os três percentis, descartando grupos com menos
    de `minimo_replicas` valores válidos.
    """
    linhas = []
    for valores_chave, grupo in df.groupby(chaves, sort=True):
        if not isinstance(valores_chave, tuple):
            valores_chave = (valores_chave,)
        serie = grupo[metrica].to_numpy(dtype=float)
        serie = serie[~np.isnan(serie)]
        if serie.size < minimo_replicas:
            continue
        p = percentis(serie)
        linhas.append(
            dict(zip(chaves, valores_chave))
            | {
                "metrica": metrica,
                "n_replicas": p.n,
                "p50": p.p50,
                "p95": p.p95,
                "p99": p.p99,
            }
        )
    return pd.DataFrame(linhas)
