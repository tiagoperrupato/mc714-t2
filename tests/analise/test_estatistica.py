"""Testes da estatística da análise: percentis e IC 95% contra valores à mão.

Os conjuntos são pequenos e escolhidos para que o resultado seja verificável
sem rodar o próprio código — a disciplina de não testar a implementação contra
ela mesma.
"""

from __future__ import annotations

import math

import pytest

from src.analise.estatistica import (
    PERCENTIS_LATENCIA,
    percentis,
    resumir,
    t_critico_95,
)


def test_percentis_de_conjunto_conhecido() -> None:
    # 0..100 de 10 em 10: 11 valores. Com interpolação linear do numpy, os
    # percentis caem exatamente sobre o próprio valor (0.50*100, etc.).
    amostra = list(range(0, 101, 10))  # 0,10,...,100
    p = percentis(amostra)
    assert p.n == 11
    assert p.p50 == pytest.approx(50.0)
    assert p.p95 == pytest.approx(95.0)
    assert p.p99 == pytest.approx(99.0)


def test_percentis_p50_e_a_mediana() -> None:
    # Amostra par: a mediana é a média dos dois centrais.
    amostra = [1.0, 2.0, 3.0, 4.0]
    p = percentis(amostra)
    assert p.p50 == pytest.approx(2.5)
    # p95 e p99 interpolam perto do topo: 3.85 e 3.97 pelo método linear.
    assert p.p95 == pytest.approx(3.85)
    assert p.p99 == pytest.approx(3.97)


def test_percentis_descarta_nan() -> None:
    amostra = [10.0, float("nan"), 20.0, 30.0]
    p = percentis(amostra)
    assert p.n == 3
    assert p.p50 == pytest.approx(20.0)


def test_percentis_amostra_vazia_reprova() -> None:
    with pytest.raises(ValueError):
        percentis([float("nan"), float("nan")])


def test_quantis_do_enunciado() -> None:
    assert PERCENTIS_LATENCIA == (50, 95, 99)


def test_ic95_de_amostra_conhecida() -> None:
    # Amostra: [1, 3, 5, 7, 9]. n=5, média=5. Desvios -4,-2,0,2,4; soma dos
    # quadrados=40; desvio amostral (ddof=1) = sqrt(40/4) = sqrt(10). Erro
    # padrão = sqrt(10)/sqrt(5) = sqrt(2). t(4; 0,975)=2,776.
    amostra = [1.0, 3.0, 5.0, 7.0, 9.0]
    r = resumir(amostra)
    assert r.n == 5
    assert r.media == pytest.approx(5.0)
    assert r.desvio == pytest.approx(math.sqrt(10.0))
    esperado = 2.776 * math.sqrt(2.0)
    assert r.ic95_meio == pytest.approx(esperado, rel=1e-6)


def test_ic95_uma_replica_sem_intervalo() -> None:
    r = resumir([42.0])
    assert r.n == 1
    assert r.media == pytest.approx(42.0)
    assert r.desvio == 0.0
    assert math.isnan(r.ic95_meio)


def test_resumir_descarta_nan() -> None:
    r = resumir([1.0, float("nan"), 3.0])
    assert r.n == 2
    assert r.media == pytest.approx(2.0)


def test_resumir_amostra_vazia_reprova() -> None:
    with pytest.raises(ValueError):
        resumir([float("nan")])


def test_t_critico_valores_tabelados() -> None:
    assert t_critico_95(1) == pytest.approx(12.706)
    assert t_critico_95(7) == pytest.approx(2.365)
    assert t_critico_95(30) == pytest.approx(2.042)


def test_t_critico_interpola_para_baixo() -> None:
    # Entre 30 e 40, usa a maior entrada <= gl (30), alargando o intervalo.
    assert t_critico_95(35) == pytest.approx(2.042)
    # Acima de 120, cai para a normal.
    assert t_critico_95(200) == pytest.approx(1.960)


def test_t_critico_gl_invalido_reprova() -> None:
    with pytest.raises(ValueError):
        t_critico_95(0)
