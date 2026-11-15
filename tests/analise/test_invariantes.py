"""Testes dos invariantes de correção do T2.

Cada teste constrói, num diretório temporário, pequenos Logs_Estruturados (JSONL)
e Logs_Duraveis sintéticos e checa que:

- um log propositalmente violado REPROVA o invariante correspondente;
- logs corretos PASSAM;
- o Estado_Em_Duvida (crash do Coordenador) vira AVISO, não reprovação, e
  `scripts/verificar.py` ainda sai com código zero.

Nenhum teste requer Docker: os fixtures imitam exatamente as linhas que
`src/comum/log.py` e `src/doispc/log_duravel.py` produzem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analise import invariantes as inv
from tests.apoio import escrever_jsonl


# --------------------------------------------------------------------------- #
# Helpers para montar logs sintéticos no formato real dos produtores.
# --------------------------------------------------------------------------- #


def _ev(no: int, tipo: str, ts: float = 0.0, **campos) -> dict:
    """Uma linha de Log_Estruturado como `src/comum/log.py` grava."""
    return {"ts": ts, "no": no, "tipo": tipo, **campos}


def _recebeu_lider(no: int, rodada: str, lider: int) -> dict:
    """Evento `recebeu` de uma Mensagem_Lider, como `src/anel/no.py` grava."""
    return _ev(
        no,
        "recebeu",
        msg_tipo="lider",
        origem=0,
        corpo={"rodada": rodada, "lider": lider},
    )


def _entrada_duravel(transacao: str, fase: str, valor: str, no: int) -> dict:
    """Uma entrada de Log_Duravel como `EntradaLog.para_json` grava."""
    return {"transacao": transacao, "fase": fase, "valor": valor, "no": no}


def _dados(eventos: list[dict], duraveis: list[dict] | None = None) -> inv.Dados:
    evs = tuple(inv.EventoLog.de_json(e) for e in eventos)
    durs = tuple(inv.EntradaDuravel.de_json(e) for e in (duraveis or []))
    return inv.Dados(eventos=evs, duraveis=durs)


def _por_numero(vereditos, numero: int) -> inv.Veredito:
    return next(v for v in vereditos if v.numero == numero)


# --------------------------------------------------------------------------- #
# Anel — invariante 1: unicidade do Líder por rodada.
# --------------------------------------------------------------------------- #


def test_anel_lider_unico_passa_com_rodada_correta() -> None:
    # Rodada r1: todos os vivos anunciam o Líder 5.
    eventos = [_recebeu_lider(no, rodada="r1", lider=5) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_01_lider_unico(_dados(eventos))
    assert v.ok
    assert v.verificados == 1


def test_anel_lider_unico_reprova_com_dois_lideres() -> None:
    # Violação: a mesma rodada anuncia dois Líderes distintos.
    eventos = [
        _recebeu_lider(1, rodada="r1", lider=5),
        _recebeu_lider(2, rodada="r1", lider=4),  # defeito
    ]
    v = inv.invariante_01_lider_unico(_dados(eventos))
    assert not v.ok
    assert any("Líderes distintos" in f for f in v.falhas)


# --------------------------------------------------------------------------- #
# Anel — invariante 2: convergência dos Nós vivos.
# --------------------------------------------------------------------------- #


def test_anel_convergencia_passa_quando_todos_vivos_conhecem() -> None:
    eventos = [_recebeu_lider(no, rodada="r3", lider=5) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_02_convergencia(_dados(eventos))
    assert v.ok


def test_anel_convergencia_reprova_quando_um_vivo_nao_conhece() -> None:
    # O Nó 4 está vivo (emitiu evento do anel) mas nunca observou o anúncio.
    eventos = [_recebeu_lider(no, rodada="r1", lider=5) for no in (1, 2, 3, 5)]
    eventos.append(_ev(4, "iniciou_eleicao", no_id=4))  # 4 vivo, sem anúncio
    v = inv.invariante_02_convergencia(_dados(eventos))
    assert not v.ok
    assert any("não" in f and "4" in f for f in v.falhas)


def test_anel_convergencia_reprova_quando_observadores_divergem() -> None:
    eventos = [
        _recebeu_lider(1, rodada="r1", lider=5),
        _recebeu_lider(2, rodada="r1", lider=3),
    ]
    v = inv.invariante_02_convergencia(_dados(eventos))
    assert not v.ok


# --------------------------------------------------------------------------- #
# Anel — invariante 3: o Líder é o maior id vivo.
# --------------------------------------------------------------------------- #


def test_anel_maior_id_vivo_passa() -> None:
    eventos = [_recebeu_lider(no, rodada="r1", lider=5) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_03_maior_id_vivo(_dados(eventos))
    assert v.ok


def test_anel_maior_id_vivo_reprova_quando_elege_menor() -> None:
    # Líder anunciado é 3, mas o maior id vivo é 5 -> violação de correção.
    eventos = [_recebeu_lider(no, rodada="r1", lider=3) for no in (1, 2, 3, 4, 5)]
    v = inv.invariante_03_maior_id_vivo(_dados(eventos))
    assert not v.ok
    assert any("maior id vivo" in f for f in v.falhas)


def test_anel_maior_id_vivo_respeita_mortos_por_salto() -> None:
    # O 5 morreu (contornado por salto): o maior id vivo passa a ser 4, e eleger
    # 4 é CORRETO.
    eventos = [_recebeu_lider(no, rodada="r1", lider=4) for no in (1, 2, 3, 4)]
    eventos.append(
        _ev(4, "sucessor_falho", rodada="r1", sucessor=5, motivo="EnvioTimeout", reemitidas=1)
    )
    v = inv.invariante_03_maior_id_vivo(_dados(eventos))
    assert v.ok


def test_anel_reeleicao_pelo_mesmo_no_e_outra_rodada() -> None:
    # O 1 inicia as duas rodadas. Na r1 todos vivem e o 5 vence. Na r2 o 5 é
    # saltado e o 4 vence. O salto da r2 não faz do 5 um morto na r1.
    eventos = [_recebeu_lider(no, rodada="r1", lider=5) for no in (1, 2, 3, 4, 5)]
    eventos += [_recebeu_lider(no, rodada="r2", lider=4) for no in (1, 2, 3, 4)]
    eventos.append(
        _ev(4, "sucessor_falho", rodada="r2", sucessor=5, motivo="EnvioTimeout", reemitidas=1)
    )
    d = _dados(eventos)
    for invariante in (
        inv.invariante_01_lider_unico,
        inv.invariante_02_convergencia,
        inv.invariante_03_maior_id_vivo,
    ):
        v = invariante(d)
        assert v.ok, (v.titulo, v.falhas)
        assert v.verificados == 2


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


def test_2pc_estado_em_duvida_avisa_e_sem_reinicio_fica_em_lacuna() -> None:
    # Participante votou SIM e NÃO tem Decisao durável: bloqueio esperado do 2PC.
    # Sem `recuperacao` no log, não há reinício a conferir.
    duraveis = [
        _entrada_duravel("tx1", "voto", "SIM", 11),
        _entrada_duravel("tx1", "voto", "SIM", 12),
    ]
    v = inv.invariante_05_durabilidade(_dados([], duraveis))
    assert v.verificados == 0
    assert not v.falhas
    assert len(v.avisos) == 2
    assert all("Estado_Em_Duvida" in a for a in v.avisos)


def test_2pc_durabilidade_voto_sim_com_decisao_nao_avisa() -> None:
    duraveis = [
        _entrada_duravel("tx1", "voto", "SIM", 11),
        _entrada_duravel("tx1", "decisao", "COMMIT", 11),
    ]
    v = inv.invariante_05_durabilidade(_dados([], duraveis))
    assert not v.avisos


# --------------------------------------------------------------------------- #
# 2PC — invariante 6: validade.
# --------------------------------------------------------------------------- #


def _inicia_transacao(transacao: str, participantes: list[int]) -> dict:
    """Evento `inicia_transacao` do Coordenador, como `src/doispc/coordenador.py` grava."""
    return _ev(1, "inicia_transacao", transacao=transacao, participantes=participantes)


def test_2pc_validade_passa_commit_com_todos_sim() -> None:
    duraveis = [_entrada_duravel("tx1", "voto", "SIM", p) for p in (11, 12, 13)]
    duraveis += [_entrada_duravel("tx1", "decisao", "COMMIT", no) for no in (1, 11, 12, 13)]
    v = inv.invariante_06_validade(_dados([_inicia_transacao("tx1", [11, 12, 13])], duraveis))
    assert v.ok, v.falhas
    assert v.verificados == 1


@pytest.mark.parametrize(
    ("votos", "faltam"),
    [
        ({11: "SIM", 12: "SIM", 13: "NAO"}, [13]),  # COMMIT com um NAO
        ({11: "SIM", 12: "SIM"}, [13]),  # COMMIT sem ouvir o 13
    ],
)
def test_2pc_validade_reprova_commit_sem_todos_sim(votos: dict[int, str], faltam: list[int]) -> None:
    duraveis = [_entrada_duravel("tx1", "voto", valor, p) for p, valor in votos.items()]
    duraveis += [_entrada_duravel("tx1", "decisao", "COMMIT", no) for no in (1, 11, 12, 13)]
    v = inv.invariante_06_validade(_dados([_inicia_transacao("tx1", [11, 12, 13])], duraveis))
    assert not v.ok
    assert f"sem o Voto SIM de {faltam}" in v.falhas[0]


def test_2pc_validade_reprova_commit_sem_participante_conhecido() -> None:
    duraveis = [_entrada_duravel("tx1", "decisao", "COMMIT", 1)]
    v = inv.invariante_06_validade(_dados([], duraveis))
    assert not v.ok
    assert "sem Participante conhecido" in v.falhas[0]


def test_2pc_validade_nao_confere_abort() -> None:
    # ABORT é válido com qualquer combinação de Votos.
    duraveis = [
        _entrada_duravel("tx1", "voto", "SIM", 11),
        _entrada_duravel("tx1", "voto", "NAO", 12),
        _entrada_duravel("tx1", "decisao", "ABORT", 1),
    ]
    v = inv.invariante_06_validade(_dados([_inicia_transacao("tx1", [11, 12])], duraveis))
    assert v.verificados == 0
    assert not v.falhas


# --------------------------------------------------------------------------- #
# Relatório e código de saída.
# --------------------------------------------------------------------------- #


def test_relatar_devolve_false_quando_ha_reprovacao() -> None:
    eventos = [
        _recebeu_lider(1, rodada="r1", lider=5),
        _recebeu_lider(2, rodada="r1", lider=4),  # viola unicidade
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
    escrever_jsonl(
        tmp_path / "no-anel-1.log",
        [_recebeu_lider(1, rodada="r1", lider=5), _ev(1, "iniciou_eleicao", no_id=1)],
    )
    escrever_jsonl(
        tmp_path / "participante-11.log",
        [_entrada_duravel("tx1", "voto", "SIM", 11),
         _entrada_duravel("tx1", "decisao", "COMMIT", 11)],
    )
    eventos = inv.carregar_eventos(tmp_path)
    duraveis = inv.carregar_duraveis(tmp_path)
    assert len(eventos) == 2
    assert len(duraveis) == 2


def test_carregar_duraveis_ignora_o_evento_grava_log(tmp_path: Path) -> None:
    # O evento repete transacao, fase, valor e no, mas é do Log_Estruturado.
    escrever_jsonl(
        tmp_path / "doispc-participante-11.jsonl",
        [_ev(11, "grava_log", transacao="tx1", fase="voto", valor="SIM")],
    )
    assert inv.carregar_duraveis(tmp_path) == ()
    assert len(inv.carregar_eventos(tmp_path)) == 1


def test_carregar_ignora_linhas_corrompidas(tmp_path: Path) -> None:
    arquivo = tmp_path / "ruidoso.log"
    arquivo.write_text(
        json.dumps(_recebeu_lider(1, rodada="r1", lider=5)) + "\n"
        "{linha truncada sem fechar\n"
        "\n"
        + json.dumps(_ev(2, "recebeu", msg_tipo="lider", corpo={"rodada": "r1", "lider": 5}))
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

    escrever_jsonl(
        tmp_path / "anel.log",
        [_recebeu_lider(no, rodada="r1", lider=5) for no in (1, 2, 3, 4, 5)],
    )
    escrever_jsonl(tmp_path / "2pc.jsonl", [_inicia_transacao("tx1", [11, 12, 13])])
    escrever_jsonl(
        tmp_path / "2pc.log",
        [_entrada_duravel("tx1", "voto", "SIM", p) for p in (11, 12, 13)]
        + [_entrada_duravel("tx1", "decisao", "COMMIT", no) for no in (1, 11, 12, 13)],
    )
    saida: list[str] = []
    codigo = verificar.verificar([tmp_path], imprimir=saida.append)
    assert codigo == 0, "\n".join(saida)


def test_verificar_sai_um_em_violacao_de_safety(tmp_path: Path) -> None:
    from scripts import verificar

    escrever_jsonl(
        tmp_path / "2pc.log",
        [
            _entrada_duravel("tx1", "decisao", "COMMIT", 11),
            _entrada_duravel("tx1", "decisao", "ABORT", 12),  # viola atomicidade
        ],
    )
    saida: list[str] = []
    codigo = verificar.verificar([tmp_path], imprimir=saida.append)
    assert codigo == 1


def test_verificar_sai_um_em_commit_com_voto_nao(tmp_path: Path) -> None:
    from scripts import verificar

    # Todos decidem COMMIT, então a atomicidade passa. Só a validade reprova.
    escrever_jsonl(
        tmp_path / "coordenador.log", [_entrada_duravel("tx1", "decisao", "COMMIT", 1)]
    )
    for pid, voto in ((11, "SIM"), (12, "SIM"), (13, "NAO")):
        escrever_jsonl(
            tmp_path / f"participante-{pid}.log",
            [
                _entrada_duravel("tx1", "voto", voto, pid),
                _entrada_duravel("tx1", "decisao", "COMMIT", pid),
            ],
        )
    saida: list[str] = []
    codigo = verificar.verificar([tmp_path], imprimir=saida.append)
    assert codigo == 1
    assert any("FALHA   6." in linha for linha in saida), "\n".join(saida)
    assert any("OK      4." in linha for linha in saida), "\n".join(saida)


def test_verificar_sai_zero_com_apenas_aviso_de_duvida(tmp_path: Path) -> None:
    from scripts import verificar

    # Crash do Coordenador: Participantes votaram SIM e ficaram sem Decisao.
    escrever_jsonl(
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


def test_verificar_main_sem_caminho_usa_results_padrao(tmp_path: Path, monkeypatch) -> None:
    from scripts import verificar

    escrever_jsonl(
        tmp_path / "cenario_anel_normal_logs" / "anel.jsonl",
        [_recebeu_lider(no, rodada="r1", lider=5) for no in (1, 2, 3, 4, 5)],
    )
    monkeypatch.setattr(verificar, "RESULTS_PADRAO", tmp_path)
    assert verificar.main([]) == 0
