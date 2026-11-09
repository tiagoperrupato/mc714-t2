"""Testes do Log_Duravel em arquivo: write-ahead e recuperação após reinício.

Exercitam a implementação em disco (`LogDuravelArquivo`): uma entrada gravada
sobrevive ao descarte do objeto e é relida por um novo `LogDuravelArquivo`
apontando para o mesmo arquivo, modelando o reinício do Nó. A
não-reversão é verificada relendo a primeira Decisao registrada
mesmo quando outra é anexada depois.
"""

from __future__ import annotations

from pathlib import Path

from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    FASE_DECISAO,
    FASE_VOTO,
    VOTO_SIM,
    EntradaLog,
    LogDuravel,
    LogDuravelArquivo,
)

TX = "tx-disco"


def test_arquivo_satisfaz_a_interface(tmp_path: Path) -> None:
    log = LogDuravelArquivo(tmp_path / "p1.log")
    assert isinstance(log, LogDuravel)


def test_grava_e_recupera_voto_e_decisao(tmp_path: Path) -> None:
    caminho = tmp_path / "p1.log"
    log = LogDuravelArquivo(caminho)
    log.registrar(EntradaLog(transacao=TX, fase=FASE_VOTO, valor=VOTO_SIM, no=1))
    log.registrar(EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_COMMIT, no=1))

    estado = log.recuperar(TX)
    assert estado.voto == VOTO_SIM
    assert estado.decisao == DECISAO_COMMIT


def test_recuperacao_apos_reinicio_rele_do_arquivo(tmp_path: Path) -> None:
    caminho = tmp_path / "p1.log"
    # Primeira "vida" do Nó: grava Voto e Decisao, depois é descartado.
    primeira_vida = LogDuravelArquivo(caminho)
    primeira_vida.registrar(EntradaLog(transacao=TX, fase=FASE_VOTO, valor=VOTO_SIM, no=1))
    primeira_vida.registrar(
        EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_COMMIT, no=1)
    )
    del primeira_vida

    # "Reinício": um novo objeto sobre o MESMO arquivo relê a Decisao de antes.
    apos_reinicio = LogDuravelArquivo(caminho)
    estado = apos_reinicio.recuperar(TX)
    assert estado.decisao == DECISAO_COMMIT
    assert estado.voto == VOTO_SIM


def test_arquivo_persiste_linhas_json_legiveis(tmp_path: Path) -> None:
    caminho = tmp_path / "p1.log"
    log = LogDuravelArquivo(caminho)
    log.registrar(EntradaLog(transacao=TX, fase=FASE_VOTO, valor=VOTO_SIM, no=1))
    conteudo = caminho.read_text(encoding="utf-8").strip().splitlines()
    assert len(conteudo) == 1
    assert '"fase": "voto"' in conteudo[0]


def test_nao_reverte_primeira_decisao(tmp_path: Path) -> None:
    caminho = tmp_path / "p1.log"
    log = LogDuravelArquivo(caminho)
    log.registrar(EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_COMMIT, no=1))
    log.registrar(EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_ABORT, no=1))
    assert log.recuperar(TX).decisao == DECISAO_COMMIT


def test_linha_corrompida_e_ignorada(tmp_path: Path) -> None:
    caminho = tmp_path / "p1.log"
    log = LogDuravelArquivo(caminho)
    log.registrar(EntradaLog(transacao=TX, fase=FASE_VOTO, valor=VOTO_SIM, no=1))
    # Simula uma escrita parcial interrompida por crash no meio do append.
    with caminho.open("a", encoding="utf-8") as fluxo:
        fluxo.write('{"transacao": "tx-disco", "fase": "deci')  # linha truncada
    log.registrar(EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_COMMIT, no=1))
    estado = log.recuperar(TX)
    assert estado.voto == VOTO_SIM
    assert estado.decisao == DECISAO_COMMIT


def test_log_vazio_recupera_estado_sem_voto_nem_decisao(tmp_path: Path) -> None:
    log = LogDuravelArquivo(tmp_path / "vazio.log")
    estado = log.recuperar(TX)
    assert estado.voto is None
    assert estado.decisao is None


def test_multiplas_transacoes_no_mesmo_log(tmp_path: Path) -> None:
    caminho = tmp_path / "coord.log"
    log = LogDuravelArquivo(caminho)
    log.registrar(EntradaLog(transacao="a", fase=FASE_DECISAO, valor=DECISAO_COMMIT, no=10))
    log.registrar(EntradaLog(transacao="b", fase=FASE_DECISAO, valor=DECISAO_ABORT, no=10))
    assert log.recuperar("a").decisao == DECISAO_COMMIT
    assert log.recuperar("b").decisao == DECISAO_ABORT
    assert log.transacoes() == ("a", "b")
