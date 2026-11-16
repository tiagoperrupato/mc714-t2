"""Testes da análise do Bully: consolidação contra o esperado e comparação com o Anel."""

from __future__ import annotations

import pandas as pd
import pytest

from src.analise import bully_exp
from src.analise.dados import DadosError


def _replica(semente: int, num_nos: int, mensagens: int, total: int, **extra) -> dict:
    return {"semente": semente, "num_nos": num_nos, "taxa_falhas": 0.05,
            "tempo_convergencia": 2, "mensagens": mensagens, "mensagens_total": total,
            "convergiu": 1} | extra


def _anel(semente: int, num_nos: int, mensagens: int, **extra) -> dict:
    return {"semente": semente, "num_nos": num_nos, "taxa_falhas": 0.05,
            "tempo_convergencia": mensagens, "mensagens": mensagens, "convergiu": 1} | extra


def test_consolidar_conta_so_as_convergidas_e_poe_o_esperado() -> None:
    tabela = bully_exp.consolidar(
        pd.DataFrame([
            _replica(1, 5, 18, 24),
            _replica(2, 5, 20, 26),
            _replica(3, 5, 99, 99, convergiu=0),
        ])
    )
    linha = tabela.iloc[0]
    assert (linha["n_replicas"], linha["n_convergiram"]) == (3, 2)
    assert linha["msgs_total_medio"] == pytest.approx(25.0)
    assert (linha["msgs_melhor_caso"], linha["msgs_pior_caso"]) == (4, 24)
    assert linha["msgs_pior_caso_com_perda"] == pytest.approx(24 / 0.95)


def test_comparar_com_anel_resume_mensagens_e_saltos_dos_dois() -> None:
    anel = pd.DataFrame([_anel(1, 3, 5), _anel(2, 3, 7)])
    bully = pd.DataFrame([_replica(1, 3, 7, 8), _replica(2, 3, 9, 10)])
    linha = bully_exp.comparar_com_anel(anel, bully).iloc[0]
    assert linha["anel_msgs_medio"] == pytest.approx(6.0)
    assert linha["anel_tempo_medio"] == pytest.approx(6.0)
    assert linha["bully_msgs_medio"] == pytest.approx(8.0)
    assert linha["bully_tempo_medio"] == pytest.approx(2.0)
    assert (linha["anel_n_convergiram"], linha["bully_n_convergiram"]) == (2, 2)


@pytest.mark.parametrize(
    ("bully", "trecho"),
    [
        ([_replica(1, 3, 7, 8), _replica(2, 5, 18, 24)], "níveis"),
        ([_replica(1, 3, 7, 8), _replica(9, 3, 7, 8)], "Sementes diferentes em num_nos = 3"),
        ([_replica(1, 3, 7, 8, taxa_falhas=0.1), _replica(2, 3, 7, 8, taxa_falhas=0.1)], "perda"),
    ],
    ids=["niveis", "sementes", "perda"],
)
def test_comparar_recusa_baterias_em_condicoes_diferentes(bully: list, trecho: str) -> None:
    anel = pd.DataFrame([_anel(1, 3, 5), _anel(2, 3, 5)])
    with pytest.raises(DadosError, match=trecho) as erro:
        bully_exp.comparar_com_anel(anel, pd.DataFrame(bully))
    assert "--algoritmo anel" in str(erro.value)
    assert "--algoritmo bully" in str(erro.value)
