"""Validação de config: domínios aceitos passam, fora de domínio recusam cedo."""

from __future__ import annotations

import pytest

from src.comum.config import ConfigInvalidaError, ConfigNo


def config_valida(**sobrescritas) -> ConfigNo:
    """Uma config válida de base, com campos sobrescritos pelos kwargs."""
    base = dict(
        id=5,
        algoritmo="anel",
        porta=8005,
        semente=42,
        deadline=1.0,
        retries=2,
        sucessores=(1,),
    )
    base.update(sobrescritas)
    return ConfigNo(**base)


def test_config_valida_nao_levanta() -> None:
    config_valida().validar()


def test_anel_e_2pc_sao_algoritmos_aceitos() -> None:
    config_valida(algoritmo="anel").validar()
    config_valida(algoritmo="2pc").validar()


def test_sucessores_vazios_aceitos() -> None:
    config_valida(sucessores=()).validar()


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("id", 0),
        ("id", -1),
        ("id", True),  # bool é recusado mesmo sendo subclasse de int
        ("algoritmo", "bully"),
        ("algoritmo", ""),
        ("algoritmo", 1),
        ("porta", 0),
        ("porta", 70_000),
        ("semente", -1),
        ("semente", 2_147_483_648),
        ("deadline", 0.0),
        ("deadline", -0.5),
        ("deadline", 301.0),
        ("retries", -1),
        ("retries", 11),
    ],
)
def test_parametro_fora_de_dominio_recusa(campo: str, valor: object) -> None:
    with pytest.raises(ConfigInvalidaError) as info:
        config_valida(**{campo: valor}).validar()
    erro = info.value
    # A exceção identifica o parâmetro, o valor recebido e o domínio aceito.
    assert erro.parametro.startswith(campo) or campo in erro.parametro
    assert erro.valor == valor
    assert erro.dominio


def test_semente_negativa_identifica_parametro_e_dominio() -> None:
    with pytest.raises(ConfigInvalidaError) as info:
        config_valida(semente=-5).validar()
    assert info.value.parametro == "semente"
    assert "0" in info.value.dominio


@pytest.mark.parametrize(
    "sucessores",
    [
        (5,),            # igual ao próprio id
        (1, 1),          # repetido
        (0,),            # não positivo
        (-3,),           # negativo
        ("1",),          # não inteiro
        [1],             # não é tupla
    ],
)
def test_sucessores_invalidos_recusam(sucessores: object) -> None:
    with pytest.raises(ConfigInvalidaError):
        config_valida(sucessores=sucessores).validar()
