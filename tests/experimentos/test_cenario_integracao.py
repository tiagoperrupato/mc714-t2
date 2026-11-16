"""Cenários da integração: modos, override, pilha própria no compose e condução ponta a ponta.

Sem Docker, os testes conferem os modos, o override de cada um, a escolha do compose da integração,
a conferência do desfecho e a sondagem por transação. A comparação dos dois arquivos de compose
precisa só do CLI. Os dois modos ponta a ponta sobem a pilha e ficam marcados `slow`.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import cenario
from src.analise import invariantes as inv
from src.experimentos import conteineres
from tests.apoio import ServidorLocal, compose_cli_disponivel, porta_livre, recursos_do_compose

# --------------------------------------------------------------------------- #
# Sem Docker: modos e override.
# --------------------------------------------------------------------------- #


def test_modos_da_integracao_e_invariantes_exigidos() -> None:
    for modo in ("normal", "failover", "failover-isolamento"):
        cenario._validar_modo("integracao", modo)
    # Só os failovers têm recuperação de Coordenador para o 05 conferir.
    assert cenario.MODOS["integracao"]["normal"] == (1, 2, 3, 4, 6, 7)
    assert cenario.MODOS["integracao"]["failover"] == (1, 2, 3, 4, 5, 6, 7)
    assert cenario.MODOS["integracao"]["failover-isolamento"] == (1, 2, 3, 4, 5, 6, 7)


@pytest.mark.parametrize(
    ("algoritmo", "modo"), [("integracao", "crash-coord"), ("inexistente", "normal")]
)
def test_algoritmo_ou_modo_fora_de_modos_e_recusado(algoritmo: str, modo: str) -> None:
    with pytest.raises(ValueError, match="use "):
        cenario._validar_modo(algoritmo, modo)


def _ambiente_por_servico(override: str) -> dict[str, str]:
    """O bloco `environment` de cada serviço do override, como texto."""
    return dict(re.findall(r"^  (\S+):\n((?:    .*\n)+)", override, re.MULTILINE))


def test_override_normal_fixa_a_semente_sem_queda() -> None:
    blocos = _ambiente_por_servico(conteineres.override_integracao("normal", 7))
    assert sorted(blocos) == sorted(conteineres.SERVICOS_INTEGRACAO)
    for texto in blocos.values():
        assert 'SEMENTE: "7"' in texto
        assert "DOISPC_CAIR_APOS_DECIDIR" not in texto


@pytest.mark.parametrize("modo", ["failover", "failover-isolamento"])
def test_override_failover_derruba_so_o_candidato_3(modo: str) -> None:
    blocos = _ambiente_por_servico(conteineres.override_integracao(modo, 0))
    com_queda = [s for s, texto in blocos.items() if 'DOISPC_CAIR_APOS_DECIDIR: "true"' in texto]
    assert com_queda == ["candidato-3"]
    assert all('SEMENTE: "0"' in texto for texto in blocos.values())


# --------------------------------------------------------------------------- #
# Sem Docker: execução do cenário com o compose e a condução substituídos.
# --------------------------------------------------------------------------- #


def test_integracao_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cenario, "docker_disponivel", lambda: False)
    codigo = cenario.executar_cenario(
        "integracao", "failover", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 2


@pytest.mark.parametrize(("transacao", "codigo"), [("c3-tx-n", 0), ("c1-tx-n", 1)])
def test_integracao_sai_1_quando_o_desfecho_difere(
    tmp_path: Path, monkeypatch, transacao: str, codigo: int
) -> None:
    arquivos_usados: list[list[Path]] = []
    coletados: list[tuple[str, list[str]]] = []

    def compose(*args, arquivos=None, **_kw):
        arquivos_usados.append(arquivos)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cenario, "docker_disponivel", lambda: True)
    monkeypatch.setattr(cenario, "compose", compose)
    monkeypatch.setattr(
        cenario, "coletar_logs", lambda alg, servicos, *_a: coletados.append((alg, servicos))
    )
    monkeypatch.setattr(cenario, "_coletar_stdout", lambda *a: None)
    # A verificação dos logs tem os próprios testes. Aqui só o desfecho decide o código.
    monkeypatch.setattr(cenario.verificar, "verificar", lambda *a, **k: 0)
    monkeypatch.setattr(
        cenario,
        "_cenario_integracao",
        lambda *a: {"transacao": transacao, "esperado": {"transacao": "c3-tx-n"}},
    )
    erro = io.StringIO()
    obtido = cenario.executar_cenario("integracao", "normal", tmp_path, saida=io.StringIO(), erro=erro)
    assert obtido == codigo
    assert {arquivos[0] for arquivos in arquivos_usados} == {conteineres.COMPOSE_INTEGRACAO}
    assert coletados == [("integracao", conteineres.SERVICOS_INTEGRACAO)]
    registro = json.loads((tmp_path / "cenario_integracao_normal.json").read_text(encoding="utf-8"))
    assert registro["desfecho"]["ok"] is (codigo == 0)
    assert ("difere do esperado" in erro.getvalue()) is (codigo == 1)


def test_failover_para_quando_o_candidato_3_responde(monkeypatch) -> None:
    # Sem a queda, os passos seguintes não teriam transação em dúvida para mostrar.
    monkeypatch.setattr(cenario, "eleger", lambda _b, ids, lider: (dict.fromkeys(ids, lider), 0.1))
    monkeypatch.setattr(cenario, "_pedir_transacao", lambda *_a: (200, {"decisao": "COMMIT"}))
    with pytest.raises(RuntimeError, match="DOISPC_CAIR_APOS_DECIDIR do candidato-3"):
        cenario._cenario_integracao("failover", [], io.StringIO())


def test_pedir_transacao_devolve_o_409_e_a_falta_de_resposta() -> None:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/transacao")
    def transacao() -> JSONResponse:
        return JSONResponse(status_code=409, content={"candidato": 1, "lider": 3})

    with ServidorLocal(app) as servidor:
        resposta = cenario._pedir_transacao(servidor.base_url, "tx")
    assert resposta == (409, {"candidato": 1, "lider": 3})
    status, corpo = cenario._pedir_transacao(f"http://127.0.0.1:{porta_livre()}", "tx")
    assert status is None and corpo["erro"]


def test_sondagens_pedem_o_estado_da_transacao(monkeypatch) -> None:
    urls: list[str] = []

    def http_json(url: str, **_kw) -> dict:
        urls.append(url)
        return {"decisao": "COMMIT", "concluido": True}

    monkeypatch.setattr(conteineres, "http_json", http_json)
    conteineres.esperar_conclusao("c3-tx-a", base="http://127.0.0.1:8023")
    conteineres.estados_participantes(esperar_decisao=True, portas={11: 8031}, transacao="c3-tx-a")
    assert urls == [
        "http://127.0.0.1:8023/estado?transacao=c3-tx-a",
        "http://127.0.0.1:8031/estado?transacao=c3-tx-a",
    ]


# --------------------------------------------------------------------------- #
# Com o CLI do Docker: a pilha da integração não colide com a principal.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not compose_cli_disponivel(), reason="Docker Compose CLI indisponível")
def test_pilha_da_integracao_nao_colide_com_a_principal() -> None:
    principal = recursos_do_compose(conteineres.COMPOSE)
    integracao = recursos_do_compose(conteineres.COMPOSE_INTEGRACAO)
    assert integracao["projeto"] == {"mc714-t2-integracao"}
    for recurso, nomes in integracao.items():
        assert nomes, f"a integração não declara {recurso}"
        assert not nomes & principal[recurso], f"{recurso} em comum: {nomes & principal[recurso]}"
    assert integracao["portas"] == {"8021", "8022", "8023", "8031", "8032", "8033"}


# --------------------------------------------------------------------------- #
# Com Docker (slow): os dois modos ponta a ponta.
# --------------------------------------------------------------------------- #

pularsem_docker = pytest.mark.skipif(
    not conteineres.docker_disponivel(),
    reason="Docker (daemon e Compose v2) indisponível, cenário por contêiner pulado",
)


def _registro(base: Path, modo: str) -> dict:
    return json.loads((base / f"cenario_integracao_{modo}.json").read_text(encoding="utf-8"))


@pytest.mark.slow
@pularsem_docker
def test_integracao_normal_e2e(tmp_path: Path) -> None:
    """O candidato 1 recusa com 409 e aponta o 3, que comita c3-tx-n em todos."""
    assert cenario.executar_cenario("integracao", "normal", tmp_path, semente=0) == 0
    reg = _registro(tmp_path, "normal")
    assert reg["recusa_nao_lider"] == {"status": 409, "lider": 3}
    assert reg["transacao"] == "c3-tx-n"
    assert reg["decisoes_participantes"] == dict.fromkeys(("11", "12", "13"), "COMMIT")
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True


@pytest.mark.slow
@pularsem_docker
def test_integracao_failover_e2e(tmp_path: Path) -> None:
    """O 2 comita c2-tx-b com c3-tx-a em dúvida, e o 3 volta, difunde o COMMIT e volta a liderar."""
    assert cenario.executar_cenario("integracao", "failover", tmp_path, semente=0) == 0
    reg = _registro(tmp_path, "failover")
    assert reg["lideres_pos_falha"] == {"1": 2, "2": 2}
    assert reg["transacao_tx_b"] == "c2-tx-b"
    assert reg["em_duvida_tx_a_durante_tx_b"] == [11, 12, 13]
    assert reg["tx_a_no_novo_lider"] is None
    assert reg["decisoes_participantes_tx_a"] == dict.fromkeys(("11", "12", "13"), "COMMIT")
    assert reg["lideres_finais"] == {"1": 3, "2": 3, "3": 3}
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    # O 05 confere a recuperação de c3-tx-a no candidato 3, que difunde a Decisao gravada.
    logs = tmp_path / "cenario_integracao_failover_logs" / "candidato-3"
    recuperacoes = [ev for ev in inv.carregar_eventos(logs) if ev.tipo == inv.EV_RECUPERACAO]
    assert [(ev.campos["transacao"], ev.campos["decisao"]) for ev in recuperacoes] == [
        ("c3-tx-a", "COMMIT")
    ]


@pytest.mark.slow
@pularsem_docker
def test_integracao_failover_isolamento_e2e(tmp_path: Path) -> None:
    """Com o 11 isolado, c2-tx-b aborta no prazo de Votos, e a retransmissão leva o ABORT a ele."""
    assert cenario.executar_cenario("integracao", "failover-isolamento", tmp_path, semente=0) == 0
    reg = _registro(tmp_path, "failover-isolamento")
    assert reg["decisao_tx_b"] == "ABORT"
    assert reg["decisoes_tx_b_durante_isolamento"] == {"11": None, "12": "ABORT", "13": "ABORT"}
    assert reg["concluido_tx_b_durante_isolamento"] is False
    assert reg["tx_b_no_isolado_apos_limpeza"] == {"voto": None, "decisao": "ABORT"}
    assert reg["decisoes_participantes_tx_a"] == dict.fromkeys(("11", "12", "13"), "COMMIT")
    assert reg["desfecho"]["ok"] is True
    assert reg["verificacao"]["ok"] is True
    # Os três votariam SIM, então o ABORT veio do prazo de coleta de Votos do candidato 2.
    log = tmp_path / "cenario_integracao_failover-isolamento_logs" / "candidato-2"
    assert any(ev.tipo == "timeout_votos" for ev in inv.carregar_eventos(log))
