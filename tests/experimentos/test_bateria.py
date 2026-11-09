"""Testes da bateria de experimentos (tarefa 9, Requisito 10).

Nenhum exige Docker: a execução por Réplica é plugável, e aqui injetamos tanto um
runner fake (para checar o esquema do CSV, a reprodução por Semente e a política
de falha-e-prossegue de forma isolada) quanto os runners de produção em processo
(para provar que a bateria real é determinística por Semente).

As propriedades verificadas, ligadas aos requisitos:

- a bateria com Semente fixa reproduz exatamente o mesmo conjunto de Réplicas
  (Requisito 10.4);
- o CSV tem as colunas esperadas e uma linha por Réplica (Requisitos 10.1, 10.4);
- cada linha carrega a Semente e o ambiente (Requisitos 10.6, 13.6);
- a configuração é validada antes de produzir resultado (Requisito 10.5);
- uma Réplica que falha é registrada e a bateria prossegue (Requisito 10.7).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Mapping

import pytest

from src.experimentos.bateria import (
    ItemReplica,
    executar_bateria,
    planejar_bateria,
)
from src.experimentos.runner_anel import executar_replica_anel


# ---------------------------------------------------------------------------
# Planejamento
# ---------------------------------------------------------------------------


def test_planejar_exige_pelo_menos_tres_niveis() -> None:
    with pytest.raises(ValueError):
        planejar_bateria(
            parametro="num_nos", niveis=(3, 5), replicas=2, semente_base=42
        )


def test_planejar_sementes_independentes_e_deterministicas() -> None:
    itens = planejar_bateria(
        parametro="num_nos", niveis=(3, 5, 7), replicas=3, semente_base=100
    )
    # Uma Semente por Réplica, derivada da posição global; todas distintas.
    sementes = [it.semente for it in itens]
    assert sementes == list(range(100, 100 + 9))
    assert len(set(sementes)) == len(sementes)
    # Replanejar com a mesma Semente base reproduz o MESMO conjunto (Req 10.4).
    de_novo = planejar_bateria(
        parametro="num_nos", niveis=(3, 5, 7), replicas=3, semente_base=100
    )
    assert [(it.nivel, it.k, it.semente) for it in itens] == [
        (it.nivel, it.k, it.semente) for it in de_novo
    ]


def test_planejar_uma_linha_por_replica() -> None:
    itens = planejar_bateria(
        parametro="latencia_rede", niveis=(1.0, 5.0, 10.0), replicas=4, semente_base=0
    )
    assert len(itens) == 3 * 4


# ---------------------------------------------------------------------------
# Runner fake: esquema, ambiente, falha-e-prossegue
# ---------------------------------------------------------------------------


def _runner_fake(item: ItemReplica) -> Mapping[str, Any]:
    """Runner determinístico que só ecoa a Semente numa métrica previsível."""
    return {"metrica": item.semente * 2}


def _ler_csv(caminho: Path) -> list[dict[str, str]]:
    with caminho.open(encoding="utf-8", newline="") as fluxo:
        return list(csv.DictReader(fluxo))


def test_csv_tem_colunas_esperadas_e_uma_linha_por_replica(tmp_path: Path) -> None:
    itens = planejar_bateria(
        parametro="num_nos", niveis=(3, 5, 7), replicas=2, semente_base=42
    )
    destino = tmp_path / "bateria.csv"
    resultado = executar_bateria(
        itens,
        _runner_fake,
        destino,
        meta={"algoritmo": "fake", "parametro_variado": "num_nos"},
        saida=io.StringIO(),
        erro=io.StringIO(),
    )
    assert resultado.codigo_saida == 0
    linhas = _ler_csv(destino)
    # Uma linha por Réplica (Req 10.4).
    assert len(linhas) == len(itens) == 6
    colunas = set(linhas[0].keys())
    # Colunas de identificação, parâmetro variado, métrica e ambiente/Semente.
    assert {"algoritmo", "parametro_variado", "indice", "nivel", "k", "semente"} <= colunas
    assert "num_nos" in colunas
    assert "metrica" in colunas
    assert {"amb_so", "amb_python_versao", "amb_pacotes"} <= colunas


def test_cada_linha_registra_semente_e_ambiente(tmp_path: Path) -> None:
    itens = planejar_bateria(
        parametro="num_nos", niveis=(3, 5, 7), replicas=1, semente_base=7
    )
    destino = tmp_path / "bateria.csv"
    executar_bateria(itens, _runner_fake, destino, saida=io.StringIO(), erro=io.StringIO())
    linhas = _ler_csv(destino)
    for linha in linhas:
        assert linha["semente"] != ""
        assert linha["amb_so"] != ""  # ambiente presente em TODA linha (Req 13.6)
        assert linha["amb_python_versao"] != ""


def test_replica_que_falha_e_registrada_e_bateria_prossegue(tmp_path: Path) -> None:
    itens = planejar_bateria(
        parametro="num_nos", niveis=(3, 5, 7), replicas=1, semente_base=0
    )

    def runner_que_falha_no_meio(item: ItemReplica) -> Mapping[str, Any]:
        if item.indice == 1:  # a segunda Réplica falha
            raise RuntimeError("falha proposital")
        return {"metrica": item.semente}

    destino = tmp_path / "bateria.csv"
    resultado = executar_bateria(
        itens, runner_que_falha_no_meio, destino, saida=io.StringIO(), erro=io.StringIO()
    )
    # Prosseguiu: 3 planejadas, 1 falha registrada, 2 linhas gravadas (Req 10.7).
    assert len(resultado.falhas) == 1
    assert resultado.falhas[0][0].indice == 1
    linhas = _ler_csv(destino)
    assert len(linhas) == 2
    # Código de saída sinaliza a falha, sem abortar a bateria.
    assert resultado.codigo_saida == 1


def test_runner_com_varias_linhas_grava_uma_por_elemento(tmp_path: Path) -> None:
    # O condutor do 2PC devolve uma linha por transação, todas com a identificação da Réplica.
    itens = planejar_bateria(
        parametro="latencia_rede", niveis=(0, 10, 25), replicas=2, semente_base=5
    )
    destino = tmp_path / "bateria.csv"
    executar_bateria(
        itens,
        lambda item: [{"ordem": o, "metrica": item.semente + o} for o in range(3)],
        destino,
        ambiente_extra={"amb_docker_versao": "0.0"},
        saida=io.StringIO(),
        erro=io.StringIO(),
    )
    linhas = _ler_csv(destino)
    assert len(linhas) == len(itens) * 3
    assert [l["ordem"] for l in linhas[:3]] == ["0", "1", "2"]
    assert {l["semente"] for l in linhas[:3]} == {"5"}
    assert all(l["amb_docker_versao"] == "0.0" and l["amb_so"] for l in linhas)


def test_bateria_vazia_e_recusada(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        executar_bateria([], _runner_fake, tmp_path / "x.csv", saida=io.StringIO(), erro=io.StringIO())


# ---------------------------------------------------------------------------
# Runners de produção em processo: determinismo por Semente
# ---------------------------------------------------------------------------


def _runner_anel(item: ItemReplica) -> Mapping[str, Any]:
    r = executar_replica_anel(
        num_nos=int(item.parametros["num_nos"]),
        taxa_falhas=float(item.parametros["taxa_falhas"]),
        semente=item.semente,
    )
    return {"tempo_convergencia": r.tempo_convergencia, "mensagens": r.mensagens}


def test_bateria_anel_reproduz_o_mesmo_conjunto_com_semente_fixa(tmp_path: Path) -> None:
    def rodar(destino: Path) -> list[dict[str, str]]:
        itens = planejar_bateria(
            parametro="num_nos",
            niveis=(3, 5, 7),
            replicas=3,
            semente_base=2024,
            fixos={"taxa_falhas": 0.1},
        )
        executar_bateria(itens, _runner_anel, destino, saida=io.StringIO(), erro=io.StringIO())
        return _ler_csv(destino)

    a = rodar(tmp_path / "a.csv")
    b = rodar(tmp_path / "b.csv")
    # Reprodução dígito a dígito das métricas por Réplica (Req 10.4).
    chaves = ("semente", "nivel", "k", "tempo_convergencia", "mensagens")
    assert [tuple(l[c] for c in chaves) for l in a] == [
        tuple(l[c] for c in chaves) for l in b
    ]
