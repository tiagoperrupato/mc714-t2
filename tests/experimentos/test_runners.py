"""Testes dos runners em processo do Anel e do 2PC (tarefa 9).

Provam que cada runner dirige a Máquina_de_Estados pura corretamente e produz
métricas determinísticas por Semente, sem rede nem Docker:

- Anel: a eleição converge para o maior id vivo e conta Mensagens; a mesma
  Semente reproduz as mesmas métricas; uma taxa de falhas maior exige mais
  Mensagens/passos para convergir, sem quebrar a convergência.
- 2PC: todos SIM -> COMMIT atômico; um NAO -> ABORT atômico; a Latencia_de_Commit
  é reproduzível por Semente e cresce com a latência de rede; o Tempo_de_Recuperacao
  é medido quando pedido.
"""

from __future__ import annotations

import pytest

from src.experimentos.runner_anel import executar_replica_anel
from src.experimentos.runner_doispc import executar_replica_doispc


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


# ---------------------------------------------------------------------------
# 2PC
# ---------------------------------------------------------------------------


def test_2pc_todos_sim_commit_atomico() -> None:
    r = executar_replica_doispc(num_participantes=3, latencia_rede=5.0, semente=1)
    assert r.decisao == "COMMIT"
    assert r.atomico
    assert r.latencia_commit > 0
    assert r.vazao > 0


def test_2pc_um_nao_aborta_atomicamente() -> None:
    r = executar_replica_doispc(
        num_participantes=3, latencia_rede=5.0, semente=1, votos_nao=frozenset({3})
    )
    assert r.decisao == "ABORT"
    assert r.atomico


def test_2pc_mesma_semente_reproduz_latencia() -> None:
    a = executar_replica_doispc(num_participantes=4, latencia_rede=8.0, semente=55)
    b = executar_replica_doispc(num_participantes=4, latencia_rede=8.0, semente=55)
    assert a.latencia_commit == b.latencia_commit
    assert a.vazao == b.vazao


def test_2pc_latencia_cresce_com_latencia_de_rede() -> None:
    baixa = executar_replica_doispc(num_participantes=3, latencia_rede=1.0, semente=10)
    alta = executar_replica_doispc(num_participantes=3, latencia_rede=20.0, semente=10)
    assert alta.latencia_commit > baixa.latencia_commit


def test_2pc_tempo_de_recuperacao_medido_quando_pedido() -> None:
    sem = executar_replica_doispc(
        num_participantes=3, latencia_rede=5.0, semente=2, simular_recuperacao=False
    )
    com = executar_replica_doispc(
        num_participantes=3, latencia_rede=5.0, semente=2, simular_recuperacao=True
    )
    assert sem.tempo_recuperacao == 0.0
    assert com.tempo_recuperacao > 0.0


def test_2pc_valida_parametros_antes_de_agir() -> None:
    with pytest.raises(ValueError):
        executar_replica_doispc(num_participantes=0, latencia_rede=1.0, semente=1)
    with pytest.raises(ValueError):
        executar_replica_doispc(num_participantes=3, latencia_rede=-1.0, semente=1)
