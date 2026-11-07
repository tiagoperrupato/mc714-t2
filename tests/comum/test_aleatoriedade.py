"""Determinismo dos streams por semente e independência entre eles."""

from __future__ import annotations

from src.comum.aleatoriedade import Streams


def _amostra(gerador, n: int = 20) -> list[float]:
    return [gerador.random() for _ in range(n)]


def test_mesma_semente_reproduz_todos_os_streams() -> None:
    a = Streams(1234)
    b = Streams(1234)
    assert _amostra(a.jitter) == _amostra(b.jitter)
    assert _amostra(a.drop) == _amostra(b.drop)
    assert _amostra(a.delay) == _amostra(b.delay)


def test_sementes_diferentes_divergem() -> None:
    a = Streams(1)
    b = Streams(2)
    assert _amostra(a.drop) != _amostra(b.drop)


def test_streams_sao_independentes_entre_si() -> None:
    # Os três streams de uma mesma Semente não compartilham estado: são
    # sequências distintas derivadas de sementes distintas.
    s = Streams(99)
    assert _amostra(s.jitter) != _amostra(s.drop)
    assert _amostra(s.drop) != _amostra(s.delay)
    assert _amostra(s.jitter) != _amostra(s.delay)


def test_consumir_um_stream_nao_desloca_os_outros() -> None:
    # A sequência de `drop` deve ser a mesma, tenha ou não o `jitter` sido
    # consumido antes: é o que garante um Plano_de_Falhas repetível
    # independentemente da ordem de consumo (Requisito 1.5).
    referencia = Streams(7)
    esperado_drop = _amostra(referencia.drop)

    perturbado = Streams(7)
    _amostra(perturbado.jitter, 50)  # consome bastante do jitter
    _amostra(perturbado.delay, 50)   # e do delay
    assert _amostra(perturbado.drop) == esperado_drop


def test_determinismo_independe_da_ordem_de_criacao() -> None:
    # Derivar na ordem fixa jitter->drop->delay é parte do contrato.
    s = Streams(2024)
    j1 = _amostra(s.jitter, 5)
    d1 = _amostra(s.drop, 5)

    outro = Streams(2024)
    d2 = _amostra(outro.drop, 5)  # consome drop primeiro
    j2 = _amostra(outro.jitter, 5)
    assert j1 == j2
    assert d1 == d2
