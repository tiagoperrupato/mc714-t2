"""Log estruturado: uma linha JSON por evento, com ts, id do Nó e tipo."""

from __future__ import annotations

import io
import json

from src.comum.log import LogEstruturado


def _linhas(buffer: io.StringIO) -> list[dict]:
    return [json.loads(l) for l in buffer.getvalue().splitlines() if l.strip()]


def test_evento_grava_linha_json_com_campos_reservados() -> None:
    buffer = io.StringIO()
    log = LogEstruturado(no_id=7, destino=buffer, relogio=lambda: 123.5)
    log.evento("envio", destino=2, rodada=1)

    (registro,) = _linhas(buffer)
    assert registro["ts"] == 123.5
    assert registro["no"] == 7
    assert registro["tipo"] == "envio"
    assert registro["destino"] == 2
    assert registro["rodada"] == 1


def test_uma_linha_por_evento() -> None:
    buffer = io.StringIO()
    log = LogEstruturado(no_id=1, destino=buffer)
    log.evento("a")
    log.evento("b")
    log.evento("c")
    assert len(_linhas(buffer)) == 3


def test_campos_do_evento_nao_sobrescrevem_reservados() -> None:
    buffer = io.StringIO()
    log = LogEstruturado(no_id=1, destino=buffer, relogio=lambda: 1.0)
    # Campos que colidam com as chaves reservadas ts/no são ignorados, para que
    # a análise sempre encontre o timestamp e o id do Nó verdadeiros.
    log.evento("x", ts=999, no=999, extra="ok")

    (registro,) = _linhas(buffer)
    assert registro["ts"] == 1.0
    assert registro["no"] == 1
    assert registro["tipo"] == "x"
    assert registro["extra"] == "ok"


def test_inicio_replica_registra_semente_plano_e_cenario() -> None:
    buffer = io.StringIO()
    log = LogEstruturado(no_id=1, destino=buffer)
    log.inicio_replica(
        semente=42,
        plano_de_falhas={"drop_p": 0.1},
        cenario={"algoritmo": "anel", "nos": 5},
    )

    (registro,) = _linhas(buffer)
    assert registro["tipo"] == "inicio_replica"
    assert registro["semente"] == 42
    assert registro["plano_de_falhas"] == {"drop_p": 0.1}
    assert registro["cenario"] == {"algoritmo": "anel", "nos": 5}


class _FluxoQuebrado:
    """Fluxo que falha ao escrever, para exercitar a degradação do log."""

    def write(self, _texto: str) -> int:
        raise OSError("disco cheio")

    def flush(self) -> None:  # pragma: no cover
        raise OSError("disco cheio")


def test_falha_de_escrita_nao_propaga() -> None:
    # Uma falha de log não pode alterar as decisões do algoritmo.
    log = LogEstruturado(no_id=1, destino=_FluxoQuebrado())
    log.evento("envio")  # não deve levantar
