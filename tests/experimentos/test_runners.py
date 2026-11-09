"""Testes do runner em processo do Anel.

Provam que o runner dirige a Máquina_de_Estados pura corretamente e produz
métricas determinísticas por Semente, sem rede nem Docker: a eleição converge
para o maior id vivo e conta Mensagens, a mesma Semente reproduz as mesmas
métricas, e uma taxa de falhas maior exige mais Mensagens e passos para
convergir, sem quebrar a convergência.
"""

from __future__ import annotations

import pytest

from src.experimentos.runner_anel import executar_replica_anel


# ---------------------------------------------------------------------------
# Anel
# ---------------------------------------------------------------------------


def test_anel_sem_falhas_converge_para_maior_id() -> None:
    r = executar_replica_anel(num_nos=5, taxa_falhas=0.0, semente=1)
    assert r.convergiu
    assert r.lider == 5
    assert r.mensagens > 0


def test_anel_mesma_semente_reproduz_metricas() -> None:
    a = executar_replica_anel(num_nos=6, taxa_falhas=0.2, semente=99)
    b = executar_replica_anel(num_nos=6, taxa_falhas=0.2, semente=99)
    assert (a.convergiu, a.lider, a.tempo_convergencia, a.mensagens) == (
        b.convergiu,
        b.lider,
        b.tempo_convergencia,
        b.mensagens,
    )


def test_anel_sementes_diferentes_podem_divergir_nas_metricas() -> None:
    # Não é garantido, mas com falhas e sementes distintas espera-se variação em
    # ao menos uma das Réplicas: checamos que a função é sensível à Semente.
    metricas = {
        executar_replica_anel(num_nos=6, taxa_falhas=0.3, semente=s).tempo_convergencia
        for s in range(10)
    }
    assert len(metricas) > 1


@pytest.mark.parametrize("num_nos", [3, 5, 7])
def test_anel_com_perda_converge_sempre_e_conta_a_perda(num_nos: int) -> None:
    # Uma Mensagem perdida é reenviada, e não faz o remetente saltar um Nó vivo.
    replicas = [
        executar_replica_anel(num_nos=num_nos, taxa_falhas=0.05, semente=s) for s in range(50)
    ]
    assert all(r.convergiu and r.lider == num_nos for r in replicas)
    # Sem perda são 2n - 1 Mensagens até a convergência. Cada perda soma uma.
    assert all(r.mensagens >= 2 * num_nos - 1 for r in replicas)
    assert any(r.mensagens > 2 * num_nos - 1 for r in replicas)


def test_anel_converge_com_no_morto_pela_regra_de_salto() -> None:
    r = executar_replica_anel(
        num_nos=5, taxa_falhas=0.0, semente=3, nos_mortos=frozenset({5})
    )
    assert r.convergiu
    # Com o 5 morto, o maior id vivo é 4.
    assert r.lider == 4
    assert r.nos_vivos == 4


def test_anel_valida_parametros_antes_de_agir() -> None:
    with pytest.raises(ValueError):
        executar_replica_anel(num_nos=0, taxa_falhas=0.0, semente=1)
    with pytest.raises(ValueError):
        executar_replica_anel(num_nos=3, taxa_falhas=1.0, semente=1)
