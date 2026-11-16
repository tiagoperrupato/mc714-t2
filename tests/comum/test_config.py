"""Validação de config: domínios aceitos passam, fora de domínio recusam cedo."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.comum.config import ConfigInvalidaError, ConfigNo, ler_ids, ler_no_do_ambiente, ler_urls


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


def test_anel_2pc_e_bully_sao_algoritmos_aceitos() -> None:
    config_valida(algoritmo="anel").validar()
    config_valida(algoritmo="2pc").validar()
    config_valida(algoritmo="bully").validar()


def test_sucessores_vazios_aceitos() -> None:
    config_valida(sucessores=()).validar()


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("id", 0),
        ("id", -1),
        ("id", True),  # bool é recusado mesmo sendo subclasse de int
        ("algoritmo", "raft"),
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


def test_ler_ids_parseia_lista() -> None:
    assert ler_ids("ANEL", " 1, 2,3 ") == (1, 2, 3)


@pytest.mark.parametrize("bruto", [None, "", ",", "1,x", "1,1"])
def test_ler_ids_invalido_cita_a_variavel(bruto) -> None:
    with pytest.raises(ConfigInvalidaError) as info:
        ler_ids("IDS", bruto)
    assert info.value.parametro == "IDS"
    assert "IDS" in str(info.value)


def test_ler_urls_parseia_mapa() -> None:
    assert ler_urls("URLS", "1=http://a:8000, 2=http://b:8000") == {
        1: "http://a:8000",
        2: "http://b:8000",
    }


@pytest.mark.parametrize("bruto", [None, "", ",", "1http://a", "x=http://a", "1="])
def test_ler_urls_invalido_cita_a_variavel(bruto) -> None:
    with pytest.raises(ConfigInvalidaError) as info:
        ler_urls("URLS_DO_ANEL", bruto)
    assert info.value.parametro == "URLS_DO_ANEL"


def _ambiente(**sobrescritas: str | None) -> dict[str, str]:
    base = {"NO_ID": "2", "ANEL": "1,2,3", "URLS": "1=http://a,2=http://b,3=http://c"}
    base.update(sobrescritas)
    return {chave: valor for chave, valor in base.items() if valor is not None}


def _ler_anel(ambiente: dict[str, str]):
    return ler_no_do_ambiente(ambiente, algoritmo="anel", variavel_ids="ANEL")


def test_ler_no_do_ambiente_aplica_padroes() -> None:
    no = _ler_anel(_ambiente())
    assert no.config == ConfigNo(
        id=2, algoritmo="anel", porta=8000, semente=0, deadline=1.0, retries=2, sucessores=(1, 3)
    )
    assert no.ids == (1, 2, 3)
    assert no.urls == {1: "http://a", 2: "http://b", 3: "http://c"}
    assert no.log_dir == Path("results")


def test_ler_no_do_ambiente_le_opcionais() -> None:
    no = _ler_anel(
        _ambiente(PORTA="8010", SEMENTE="7", DEADLINE="0.5", RETRIES="0", LOG_DIR="/tmp/logs")
    )
    assert (no.config.porta, no.config.semente, no.config.deadline, no.config.retries) == (
        8010,
        7,
        0.5,
        0,
    )
    assert no.log_dir == Path("/tmp/logs")


@pytest.mark.parametrize(
    ("sobrescritas", "parametro"),
    [
        ({"NO_ID": None}, "NO_ID"),
        ({"NO_ID": "dois"}, "NO_ID"),
        ({"NO_ID": "4"}, "NO_ID"),
        ({"ANEL": None}, "ANEL"),
        ({"URLS": "1=http://a,2=http://b"}, "URLS"),
        ({"PORTA": "oito mil"}, "PORTA"),
        ({"DEADLINE": "0"}, "deadline"),
    ],
)
def test_ler_no_do_ambiente_recusa_com_o_parametro(sobrescritas, parametro) -> None:
    with pytest.raises(ConfigInvalidaError) as info:
        _ler_anel(_ambiente(**sobrescritas))
    assert info.value.parametro == parametro


def test_ler_no_do_ambiente_le_os_ids_da_variavel_do_algoritmo() -> None:
    ambiente = _ambiente(ANEL=None, IDS="3,1,2")
    no = ler_no_do_ambiente(ambiente, algoritmo="bully", variavel_ids="IDS")
    assert no.config.algoritmo == "bully"
    assert no.ids == (3, 1, 2)
    with pytest.raises(ConfigInvalidaError) as info:
        ler_no_do_ambiente(_ambiente(ANEL=None, IDS="1,3"), algoritmo="bully", variavel_ids="IDS")
    assert info.value.parametro == "NO_ID"
    assert "IDS" in str(info.value)
