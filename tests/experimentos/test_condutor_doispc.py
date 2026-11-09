"""Testes do condutor da bateria do 2PC: a contagem de mensagens e uma réplica de cada tipo no compose."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import verificar
from src.experimentos import condutor_doispc, conteineres
from src.experimentos.bateria import ItemReplica


def _jsonl(caminho: Path, eventos: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("".join(json.dumps(e) + "\n" for e in eventos) + "{corrompida\n", encoding="utf-8")


def test_contar_mensagens_soma_os_envios_de_cada_transacao_em_todos_os_nos(tmp_path: Path) -> None:
    _jsonl(
        tmp_path / "coordenador" / "logs" / "doispc-coordenador-1.jsonl",
        [
            {"tipo": "envia", "transacao": "t1", "msg_tipo": "prepare"},
            {"tipo": "envia", "transacao": "t2", "msg_tipo": "prepare"},
            {"tipo": "recebe", "transacao": "t1", "msg_tipo": "voto"},
        ],
    )
    _jsonl(
        tmp_path / "participante-1" / "logs" / "doispc-participante-11.jsonl",
        [{"tipo": "envia", "transacao": "t1", "msg_tipo": "voto"}],
    )
    assert condutor_doispc.contar_mensagens(tmp_path) == {"t1": 2, "t2": 1}


def _item(semente: int, latencia_ms: int) -> ItemReplica:
    return ItemReplica(
        indice=0,
        nivel=latencia_ms,
        k=0,
        semente=semente,
        parametros={"latencia_rede": latencia_ms, "num_participantes": 3},
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not conteineres.docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível, bateria do 2PC pulada",
)
def test_replicas_de_latencia_e_de_recuperacao_nos_nos_reais(tmp_path: Path) -> None:
    condutor_doispc.preparar()
    latencia_ms = 10
    linhas = condutor_doispc.executar_replica_latencia(
        _item(9000, latencia_ms), transacoes=3, aquecimento=1, dir_logs=tmp_path
    )
    assert [l["aquecimento"] for l in linhas] == [1, 0, 0, 0]
    assert {l["decisao"] for l in linhas} == {"COMMIT"}
    # PREPARE, VOTO, DECISAO e ACK para cada um dos três participantes.
    assert {l["mensagens"] for l in linhas} == {12}
    # Os PREPARE saem em sequência, então o último VOTO chega depois de seis atrasos de ida.
    assert min(l["latencia_commit_s"] for l in linhas) >= 6 * latencia_ms / 1000

    recuperacao = condutor_doispc.executar_replica_recuperacao(
        _item(9001, latencia_ms), dir_logs=tmp_path
    )
    assert recuperacao["em_duvida"] == 3
    assert recuperacao["decisao"] == "COMMIT"
    assert recuperacao["tempo_recuperacao_s"] > 0
    assert recuperacao["mensagens"] >= 12

    assert verificar.verificar([tmp_path], imprimir=lambda _linha: None) == 0
