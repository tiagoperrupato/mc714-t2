"""Testes dos invariantes de correção do T2 (tarefa 11).

Cada teste constrói, num diretório temporário, pequenos Logs_Estruturados (JSONL)
e Logs_Duraveis sintéticos e checa que:

- um log propositalmente violado REPROVA o invariante correspondente;
- logs corretos PASSAM;
- o Estado_Em_Duvida (crash do Coordenador) vira AVISO, não reprovação, e
  `scripts/verificar.py` ainda sai com código zero (Requisito 12.4).

Nenhum teste requer Docker: os fixtures imitam exatamente as linhas que
`src/comum/log.py` e `src/doispc/log_duravel.py` produzem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analise import invariantes as inv


# --------------------------------------------------------------------------- #
# Helpers para montar logs sintéticos no formato real dos produtores.
# --------------------------------------------------------------------------- #


def _escrever_jsonl(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as fluxo:
        for r in registros:
            fluxo.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ev(no: int, tipo: str, ts: float = 0.0, **campos) -> dict:
    """Uma linha de Log_Estruturado como `src/comum/log.py` grava."""
    return {"ts": ts, "no": no, "tipo": tipo, **campos}


def _recebeu_lider(no: int, iniciador: int, lider: int) -> dict:
    """Evento `recebeu` de uma Mensagem_Lider, como `src/anel/no.py` grava."""
    return _ev(
        no,
        "recebeu",
        msg_tipo="lider",
        origem=0,
        corpo={"iniciador": iniciador, "lider": lider},
    )


def _entrada_duravel(transacao: str, fase: str, valor: str, no: int) -> dict:
    """Uma entrada de Log_Duravel como `EntradaLog.para_json` grava."""
    return {"transacao": transacao, "fase": fase, "valor": valor, "no": no}


def _dados(eventos: list[dict], duraveis: list[dict] | None = None, **kw) -> inv.Dados:
    evs = tuple(inv.EventoLog.de_json(e) for e in eventos)
    durs = tuple(inv.EntradaDuravel.de_json(e) for e in (duraveis or []))
    return inv.Dados(eventos=evs, duraveis=durs, **kw)


def _por_numero(vereditos, numero: int) -> inv.Veredito:
    return next(v for v in vereditos if v.numero == numero)


# --------------------------------------------------------------------------- #
# Anel — invariante 1: unicidade do Líder por rodada.
# --------------------------------------------------------------------------- #


def test_anel_lider_unico_passa_com_rodada_correta() -> None:
    # Rodada do iniciador 1: todos os vivos anunciam o Líder 5.
    eventos = [_recebeu_lider(no, iniciador=1, lider=5) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_01_lider_unico(_dados(eventos))
    assert v.ok
    assert v.verificados == 1


def test_anel_lider_unico_reprova_com_dois_lideres() -> None:
    # Violação: a mesma rodada anuncia dois Líderes distintos.
    eventos = [
        _recebeu_lider(1, iniciador=1, lider=5),
        _recebeu_lider(2, iniciador=1, lider=4),  # defeito
    ]
    v = inv.invariante_01_lider_unico(_dados(eventos))
    assert not v.ok
    assert any("Líderes distintos" in f for f in v.falhas)


# --------------------------------------------------------------------------- #
# Anel — invariante 2: convergência dos Nós vivos.
# --------------------------------------------------------------------------- #


def test_anel_convergencia_passa_quando_todos_vivos_conhecem() -> None:
    eventos = [_recebeu_lider(no, iniciador=3, lider=5) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_02_convergencia(_dados(eventos))
    assert v.ok


def test_anel_convergencia_reprova_quando_um_vivo_nao_conhece() -> None:
    # O Nó 4 está vivo (emitiu evento do anel) mas nunca observou o anúncio.
    eventos = [_recebeu_lider(no, iniciador=1, lider=5) for no in (1, 2, 3, 5)]
    eventos.append(_ev(4, "iniciou_eleicao", no_id=4))  # 4 vivo, sem anúncio
    v = inv.invariante_02_convergencia(_dados(eventos))
    assert not v.ok
    assert any("não" in f and "4" in f for f in v.falhas)


def test_anel_convergencia_reprova_quando_observadores_divergem() -> None:
    eventos = [
        _recebeu_lider(1, iniciador=1, lider=5),
        _recebeu_lider(2, iniciador=1, lider=3),
    ]
    v = inv.invariante_02_convergencia(_dados(eventos))
    assert not v.ok


# --------------------------------------------------------------------------- #
# Anel — invariante 3: o Líder é o maior id vivo.
# --------------------------------------------------------------------------- #


def test_anel_maior_id_vivo_passa() -> None:
    eventos = [_recebeu_lider(no, iniciador=1, lider=5) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_03_maior_id_vivo(_dados(eventos))
    assert v.ok


def test_anel_maior_id_vivo_reprova_quando_elege_menor() -> None:
    # Líder anunciado é 3, mas o maior id vivo é 5 -> violação de correção.
    eventos = [_recebeu_lider(no, iniciador=1, lider=3) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_03_maior_id_vivo(_dados(eventos))
    assert not v.ok
    assert any("maior id vivo" in f for f in v.falhas)


def test_anel_maior_id_vivo_respeita_mortos_por_salto() -> None:
    # O 5 morreu (contornado por salto): o maior id vivo passa a ser 4, e eleger
    # 4 é CORRETO.
    eventos = [_recebeu_lider(no, iniciador=1, lider=4) for no in (1, 2, 3, 4)]
    eventos.append(_ev(4, "sucessor_falho", sucessor=5, motivo="EnvioTimeout", reemitidas=1))
    v = inv.invariante_03_maior_id_vivo(_dados(eventos))
    assert v.ok


def test_anel_maior_id_vivo_usa_vivos_por_rodada_quando_dado() -> None:
    # O chamador informa que na rodada 1 os vivos são {1,2,3,4}; eleger 4 é certo.
    eventos = [_recebeu_lider(no, iniciador=1, lider=4) for no in (1, 2, 3, 4)]
    d = _dados(eventos, vivos_por_rodada={1: frozenset({1, 2, 3, 4})})
    v = inv.invariante_03_maior_id_vivo(d)
    assert v.ok


# --------------------------------------------------------------------------- #
# 2PC — invariante 4: atomicidade.
# --------------------------------------------------------------------------- #


def test_2pc_atomicidade_passa_commit_em_todos() -> None:
    duraveis = [
        _entrada_duravel("tx1", "decisao", "COMMIT", no)
        for no in (1, 11, 12, 13)
    ]
    v = inv.invariante_04_atomicidade(_dados([], duraveis))
    assert v.ok
    assert v.verificados == 1


def test_2pc_atomicidade_passa_abort_em_todos() -> None:
    duraveis = [
        _entrada_duravel("tx1", "decisao", "ABORT", no)
        for no in (1, 11, 12, 13)
    ]
    v = inv.invariante_04_atomicidade(_dados([], duraveis))
    assert v.ok


def test_2pc_atomicidade_reprova_commit_e_abort_misturados() -> None:
    duraveis = [
        _entrada_duravel("tx1", "decisao", "COMMIT", 1),
        _entrada_duravel("tx1", "decisao", "COMMIT", 11),
        _entrada_duravel("tx1", "decisao", "ABORT", 12),  # defeito
    ]
    v = inv.invariante_04_atomicidade(_dados([], duraveis))
    assert not v.ok
    assert any("COMMIT" in f and "ABORT" in f for f in v.falhas)


# --------------------------------------------------------------------------- #
# 2PC — invariante 5: durabilidade e Estado_Em_Duvida.
# --------------------------------------------------------------------------- #


def test_2pc_durabilidade_passa_quando_recuperacao_bate() -> None:
    duraveis = [_entrada_duravel("tx1", "decisao", "COMMIT", 11)]
    eventos = [_ev(11, "recuperacao", transacao="tx1", decisao="COMMIT", voto="SIM")]
    v = inv.invariante_05_durabilidade(_dados(eventos, duraveis))
    assert v.ok
    assert not v.falhas


def test_2pc_durabilidade_reprova_quando_decisao_reverte() -> None:
    # Gravou COMMIT, mas recuperou ABORT -> reversão após reinício, viola safety.
    duraveis = [_entrada_duravel("tx1", "decisao", "COMMIT", 11)]
    eventos = [_ev(11, "recuperacao", transacao="tx1", decisao="ABORT")]
    v = inv.invariante_05_durabilidade(_dados(eventos, duraveis))
    assert not v.ok
    assert any("recuperou" in f for f in v.falhas)


def test_2pc_estado_em_duvida_vira_aviso_nao_reprova() -> None:
    # Participante votou SIM e NÃO tem Decisao durável: bloqueio esperado do 2PC.
    duraveis = [
        _entrada_duravel("tx1", "voto", "SIM", 11),
        _entrada_duravel("tx1", "voto", "SIM", 12),
    ]
    v = inv.invariante_05_durabilidade(_dados([], duraveis))
    assert v.ok  # aviso não reprova
    assert v.avisos
    assert all("Estado_Em_Duvida" in a for a in v.avisos)


def test_2pc_durabilidade_voto_sim_com_decisao_nao_avisa() -> None:
    duraveis = [
        _entrada_duravel("tx1", "voto", "SIM", 11),
        _entrada_duravel("tx1", "decisao", "COMMIT", 11),
    ]
    v = inv.invariante_05_durabilidade(_dados([], duraveis))
    assert not v.avisos


# --------------------------------------------------------------------------- #
# Relatório e código de saída.
# --------------------------------------------------------------------------- #


def test_relatar_devolve_false_quando_ha_reprovacao() -> None:
    eventos = [
        _recebeu_lider(1, iniciador=1, lider=5),
        _recebeu_lider(2, iniciador=1, lider=4),  # viola unicidade
    ]
    vereditos = inv.verificar_todos(_dados(eventos))
    saida: list[str] = []
    ok = inv.relatar(vereditos, imprimir=saida.append)
    assert ok is False


def test_relatar_devolve_true_com_apenas_avisos() -> None:
    duraveis = [_entrada_duravel("tx1", "voto", "SIM", 11)]  # em dúvida -> aviso
    vereditos = inv.verificar_todos(_dados([], duraveis))
    saida: list[str] = []
    ok = inv.relatar(vereditos, imprimir=saida.append)
    assert ok is True
    assert any("aviso" in linha.lower() for linha in saida)


# --------------------------------------------------------------------------- #
# Leitura de logs do disco (autocontida, sem Docker).
# --------------------------------------------------------------------------- #


def test_carregar_distingue_eventos_de_duraveis(tmp_path: Path) -> None:
    _escrever_jsonl(
        tmp_path / "no-anel-1.log",
        [_recebeu_lider(1, iniciador=1, lider=5), _ev(1, "iniciou_eleicao", no_id=1)],
    )
    _escrever_jsonl(
        tmp_path / "participante-11.log",
        [_entrada_duravel("tx1", "voto", "SIM", 11),
         _entrada_duravel("tx1", "decisao", "COMMIT", 11)],
    )
    eventos = inv.carregar_eventos(tmp_path)
    duraveis = inv.carregar_duraveis(tmp_path)
    assert len(eventos) == 2
    assert len(duraveis) == 2


def test_carregar_ignora_linhas_corrompidas(tmp_path: Path) -> None:
    arquivo = tmp_path / "ruidoso.log"
    arquivo.write_text(
        json.dumps(_recebeu_lider(1, iniciador=1, lider=5)) + "\n"
        "{linha truncada sem fechar\n"
        "\n"
        + json.dumps(_ev(2, "recebeu", msg_tipo="lider", corpo={"iniciador": 1, "lider": 5}))
        + "\n",
        encoding="utf-8",
    )
    eventos = inv.carregar_eventos(arquivo)
    assert len(eventos) == 2


def test_carregar_caminho_inexistente_nao_quebra(tmp_path: Path) -> None:
    assert inv.carregar_eventos(tmp_path / "nao_existe") == ()
    assert inv.carregar_duraveis(tmp_path / "nao_existe") == ()


# --------------------------------------------------------------------------- #
# Integração ponta a ponta com scripts/verificar.py.
# --------------------------------------------------------------------------- #


def test_verificar_sai_zero_em_logs_corretos(tmp_path: Path) -> None:
    from scripts import verificar

    _escrever_jsonl(
        tmp_path / "anel.log",
        [_recebeu_lider(no, iniciador=1, lider=5) for no in (1, 2, 3, 4, 5)],
    )
    _escrever_jsonl(
        tmp_path / "2pc.log",
        [_entrada_duravel("tx1", "decisao", "COMMIT", no) for no in (1, 11, 12, 13)],
    )
    saida: list[str] = []
    codigo = verificar.verificar([tmp_path], imprimir=saida.append)
    assert codigo == 0


def test_verificar_sai_um_em_violacao_de_safety(tmp_path: Path) -> None:
    from scripts import verificar

    _escrever_jsonl(
        tmp_path / "2pc.log",
        [
            _entrada_duravel("tx1", "decisao", "COMMIT", 11),
            _entrada_duravel("tx1", "decisao", "ABORT", 12),  # viola atomicidade
        ],
    )
    saida: list[str] = []
    codigo = verificar.verificar([tmp_path], imprimir=saida.append)
    assert codigo == 1


def test_verificar_sai_zero_com_apenas_aviso_de_duvida(tmp_path: Path) -> None:
    from scripts import verificar

    # Crash do Coordenador: Participantes votaram SIM e ficaram sem Decisao.
    _escrever_jsonl(
        tmp_path / "2pc.log",
        [
            _entrada_duravel("tx1", "voto", "SIM", 11),
            _entrada_duravel("tx1", "voto", "SIM", 12),
        ],
    )
    saida: list[str] = []
    codigo = verificar.verificar([tmp_path], imprimir=saida.append)
    assert codigo == 0
    assert any("aviso" in linha.lower() for linha in saida)


def test_verificar_sai_zero_sem_dados(tmp_path: Path) -> None:
    from scripts import verificar

    codigo = verificar.verificar([tmp_path], imprimir=lambda *_: None)
    assert codigo == 0


def test_verificar_main_usa_results_padrao(tmp_path: Path) -> None:
    from scripts import verificar

    codigo = verificar.main(["--results", str(tmp_path)])
    assert codigo == 0
