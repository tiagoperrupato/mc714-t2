"""Determinismo dos streams por semente e independência entre eles."""

from __future__ import annotations

import random

from src.comum.aleatoriedade import Streams


def _amostra(gerador, n: int = 20) -> list[float]:
    return [gerador.random() for _ in range(n)]


def test_mesma_semente_reproduz_todos_os_streams() -> None:
    a = Streams(1234)
    b = Streams(1234)
    assert _amostra(a.drop) == _amostra(b.drop)
    assert _amostra(a.delay) == _amostra(b.delay)


def test_sementes_diferentes_divergem() -> None:
    a = Streams(1)
    b = Streams(2)
    assert _amostra(a.drop) != _amostra(b.drop)


def test_streams_sao_independentes_entre_si() -> None:
    # Os dois streams de uma mesma Semente não compartilham estado: são
    # sequências distintas derivadas de sementes distintas.
    s = Streams(99)
    assert _amostra(s.drop) != _amostra(s.delay)


def test_consumir_um_stream_nao_desloca_o_outro() -> None:
    # A sequência de `drop` deve ser a mesma, tenha ou não o `delay` sido
    # consumido antes: é o que garante um Plano_de_Falhas repetível
    # independentemente da ordem de consumo.
    referencia = Streams(7)
    esperado_drop = _amostra(referencia.drop)

    perturbado = Streams(7)
    _amostra(perturbado.delay, 50)
    assert _amostra(perturbado.drop) == esperado_drop


def test_determinismo_independe_da_ordem_de_consumo() -> None:
    s = Streams(2024)
    d1 = _amostra(s.delay, 5)
    p1 = _amostra(s.drop, 5)

    outro = Streams(2024)
    p2 = _amostra(outro.drop, 5)  # consome drop primeiro
    d2 = _amostra(outro.delay, 5)
    assert d1 == d2
    assert p1 == p2


def test_drop_e_delay_sao_a_segunda_e_a_terceira_sementes_derivadas() -> None:
    spawner = random.Random(7)
    sementes = [spawner.randrange(2**63) for _ in range(3)]
    s = Streams(7)
    assert _amostra(s.drop) == _amostra(random.Random(sementes[1]))
    assert _amostra(s.delay) == _amostra(random.Random(sementes[2]))
