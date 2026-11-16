"""Testes do runner em processo do Bully: custo exato sem perda, determinismo e perda reenviada."""

from __future__ import annotations

import pytest

from src.experimentos.runner_bully import executar_replica_bully


@pytest.mark.parametrize("num_nos", [3, 5, 7])
def test_no_1_iniciador_e_o_pior_caso(num_nos: int) -> None:
    r = executar_replica_bully(num_nos=num_nos, taxa_falhas=0.0, semente=1)
    assert (r.convergiu, r.lider) == (True, num_nos)
    assert r.mensagens_total == num_nos * num_nos - 1
    assert r.tempo_convergencia == 2
    assert r.mensagens < r.mensagens_total


@pytest.mark.parametrize("num_nos", [3, 5, 7])
def test_maior_id_iniciador_e_o_melhor_caso(num_nos: int) -> None:
    r = executar_replica_bully(num_nos=num_nos, taxa_falhas=0.0, semente=1, iniciador=num_nos)
    assert (r.convergiu, r.lider) == (True, num_nos)
    assert r.mensagens_total == num_nos - 1
    assert r.tempo_convergencia == 1


@pytest.mark.parametrize("num_nos", [3, 5, 7])
def test_maior_morto_elege_o_seguinte_sem_contar_mensagem_a_ele(num_nos: int) -> None:
    r = executar_replica_bully(
        num_nos=num_nos, taxa_falhas=0.0, semente=1, nos_mortos=frozenset({num_nos})
    )
    assert (r.convergiu, r.lider) == (True, num_nos - 1)
    assert r.mensagens_total == (num_nos - 1) ** 2 - 1


def test_mesma_semente_reproduz_metricas() -> None:
    a = executar_replica_bully(num_nos=6, taxa_falhas=0.2, semente=99)
    b = executar_replica_bully(num_nos=6, taxa_falhas=0.2, semente=99)
    assert a == b


@pytest.mark.parametrize("num_nos", [3, 5, 7])
def test_com_perda_converge_sempre_e_conta_a_perda(num_nos: int) -> None:
    replicas = [
        executar_replica_bully(num_nos=num_nos, taxa_falhas=0.05, semente=s) for s in range(50)
    ]
    assert all(r.convergiu and r.lider == num_nos for r in replicas)
    assert all(r.mensagens_total >= num_nos * num_nos - 1 for r in replicas)
    assert any(r.mensagens_total > num_nos * num_nos - 1 for r in replicas)


@pytest.mark.parametrize("num_nos", [3, 5, 7])
def test_com_perda_cada_tentativa_perdida_no_caminho_soma_um_salto(num_nos: int) -> None:
    """Sem perda são 2 saltos. Cada perda soma no máximo um, e alguma cai no caminho."""
    replicas = [
        executar_replica_bully(num_nos=num_nos, taxa_falhas=0.05, semente=s) for s in range(50)
    ]
    perdas = [r.mensagens_total - (num_nos * num_nos - 1) for r in replicas]
    assert all(2 <= r.tempo_convergencia <= 2 + p for r, p in zip(replicas, perdas))
    assert any(r.tempo_convergencia > 2 for r in replicas)


@pytest.mark.parametrize(
    "parametros",
    [
        {"num_nos": 0, "taxa_falhas": 0.0},
        {"num_nos": 3, "taxa_falhas": 1.0},
        {"num_nos": 3, "taxa_falhas": 0.0, "nos_mortos": frozenset({4})},
        {"num_nos": 3, "taxa_falhas": 0.0, "iniciador": 4},
        {"num_nos": 3, "taxa_falhas": 0.0, "iniciador": 1, "nos_mortos": frozenset({1})},
    ],
)
def test_parametros_invalidos_sao_recusados(parametros: dict) -> None:
    with pytest.raises(ValueError):
        executar_replica_bully(semente=1, **parametros)
