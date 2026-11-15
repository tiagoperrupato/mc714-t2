"""Imutabilidade e serialização de No e Mensagem."""

from __future__ import annotations

import dataclasses

import pytest

from src.comum.modelos import Mensagem, No


def test_no_base_url() -> None:
    no = No(id=3, host="no-anel-3", porta=8003)
    assert no.base_url == "http://no-anel-3:8003"


def test_no_e_frozen() -> None:
    no = No(id=1, host="h", porta=8001)
    with pytest.raises(dataclasses.FrozenInstanceError):
        no.id = 2  # type: ignore[misc]


def test_mensagem_corpo_e_somente_leitura() -> None:
    msg = Mensagem(tipo="eleicao", origem=1, destino=2, corpo={"ids": [1]})
    with pytest.raises((TypeError, Exception)):
        msg.corpo["ids"] = [1, 2]  # type: ignore[index]


def test_mensagem_e_frozen() -> None:
    msg = Mensagem(tipo="eleicao", origem=1, destino=2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.tipo = "lider"  # type: ignore[misc]


def test_roundtrip_json() -> None:
    msg = Mensagem(tipo="prepare", origem=10, destino=20, corpo={"tx": "abc"})
    reconstruida = Mensagem.de_json(msg.para_json())
    assert reconstruida == msg


def test_de_json_sem_corpo() -> None:
    msg = Mensagem.de_json({"tipo": "lider", "origem": 5, "destino": 1})
    assert msg.corpo == {}
