"""Candidato da integração: despacho por tipo, 409 fora do Líder e dois Líderes sem violar o 2PC.

O teste dos dois Líderes sobe os candidatos por `construir_candidato`, o caminho do contêiner, e os
Participantes em servidores locais. A falsa suspeita do Anel faz o 2 e o 3 se julgarem Líderes ao
mesmo tempo, e o prefixo no id mantém um Coordenador por transação.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from src.analise import invariantes as inv
from src.anel.maquina import TIPO_ELEICAO, TIPO_LIDER, EstadoAnel
from src.anel.no import NoAnel
from src.comum.log import abrir_log
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede
from src.doispc.coordenador import ServicoCoordenador
from src.doispc.log_duravel import DECISAO_COMMIT, LogDuravelArquivo, LogDuravelMemoria
from src.doispc.maquina import MSG_ACK, MSG_PREPARE, MSG_VOTO
from src.doispc.participante import ServicoParticipante, criar_app as criar_app_part
from src.experimentos.conteineres import http_json
from src.integracao.__main__ import construir_candidato, main
from src.integracao.candidato import (
    TIPOS_ANEL,
    TIPOS_COORDENADOR,
    criar_app,
    id_da_transacao,
)
from tests.apoio import ServidorLocal, esperar_ate, porta_livre

CANDIDATOS = (1, 2, 3)
PART_IDS = (11, 12, 13)
NOMES = tuple(f"tx-{i}" for i in range(5))


def test_tipos_do_anel_e_do_coordenador_sao_disjuntos() -> None:
    assert TIPOS_ANEL == {TIPO_ELEICAO, TIPO_LIDER}
    assert TIPOS_COORDENADOR == {MSG_VOTO, MSG_ACK}
    assert not TIPOS_ANEL & TIPOS_COORDENADOR


@pytest.mark.parametrize(
    ("nome", "esperado"), [("tx-a", "c3-tx-a"), ("c3-tx-a", "c3-tx-a"), ("c2-x", "c3-c2-x")]
)
def test_id_da_transacao_leva_o_prefixo_do_candidato(nome: str, esperado: str) -> None:
    assert id_da_transacao(3, nome) == esperado


def test_id_da_transacao_sem_nome_sorteia_oito_hex() -> None:
    assert re.fullmatch(r"c3-[0-9a-f]{8}", id_da_transacao(3, None))


class _CandidatoSemRede:
    """Candidato 1 de um Anel 1, 2, 3 que conhece `lider`, num servidor local. Nada sai dele."""

    def __init__(self, tmp_path: Path, lider: int | None) -> None:
        log = abrir_log(tmp_path / "candidato-1.jsonl", 1)
        self.no = NoAnel(
            EstadoAnel(no_id=1, anel=(1, 2, 3), lider=lider),
            urls={}, cliente=ClienteRede(), deadline=0.5, log=log,
        )
        self.duravel = LogDuravelMemoria()
        self.servico = ServicoCoordenador(
            No(1, "127.0.0.1", 1), {pid: No(pid, "127.0.0.1", 1) for pid in PART_IDS},
            log_duravel=self.duravel, cliente=ClienteRede(),
            deadline_voto=0.5, deadline_decisao=0.5, log=log,
        )
        self._servidor = ServidorLocal(criar_app(self.no, self.servico, log=log))
        self._tmp_path = tmp_path

    def __enter__(self) -> "_CandidatoSemRede":
        self._servidor.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._servidor.__exit__(*exc)

    def postar(self, caminho: str, corpo: dict) -> httpx.Response:
        return httpx.post(f"{self._servidor.base_url}{caminho}", json=corpo, timeout=5.0)

    def estado(self) -> dict:
        return http_json(f"{self._servidor.base_url}/estado")

    def eventos(self, *tipos: str) -> list[dict]:
        eventos = inv.carregar_eventos(self._tmp_path / "candidato-1.jsonl")
        return [dict(ev.campos) for ev in eventos if ev.tipo in tipos]


@pytest.mark.parametrize(
    ("lider", "erro"),
    [
        (3, "o candidato 1 não é o Líder. Envie a transação ao candidato 3."),
        (
            None,
            "o candidato 1 não conhece o Líder. "
            "Dispare POST /iniciar-eleicao em qualquer candidato e repita.",
        ),
    ],
)
def test_fora_do_lider_responde_409_sem_gravar_inicio(
    tmp_path: Path, lider: int | None, erro: str
) -> None:
    with _CandidatoSemRede(tmp_path, lider) as candidato:
        resposta = candidato.postar("/transacao", {"transacao": "tx-a"})

    assert resposta.status_code == 409
    assert resposta.json() == {"candidato": 1, "lider": lider, "erro": erro}
    assert candidato.duravel.transacoes() == ()
    assert candidato.eventos("transacao_recusada") == [{"nome": "tx-a", "lider": lider}]
    assert candidato.eventos(inv.EV_INICIA_TRANSACAO) == []


def test_tipo_desconhecido_responde_400_sem_mudar_estado(tmp_path: Path) -> None:
    prepare = Mensagem(MSG_PREPARE, 3, 1, {"transacao": "c3-tx"})
    with _CandidatoSemRede(tmp_path, 3) as candidato:
        antes = candidato.estado()
        resposta = candidato.postar("/mensagem", prepare.para_json())
        depois = candidato.estado()

    assert resposta.status_code == 400
    assert resposta.json()["detail"] == (
        "tipo 'prepare' não é do anel (eleicao, lider) nem do Coordenador (ack, voto)"
    )
    assert depois == antes
    assert candidato.duravel.transacoes() == ()
    assert candidato.eventos(inv.EV_RECEBEU, inv.EV_RECEBE) == []


def test_estado_junta_anel_e_coordenador_sem_colisao(tmp_path: Path) -> None:
    with _CandidatoSemRede(tmp_path, 3) as candidato:
        anel, coordenador = candidato.no.estado_atual(), candidato.servico.instantaneo()

        assert not anel.keys() & coordenador.keys()
        assert candidato.estado() == {**anel, **coordenador}


def test_participante_no_anel_sai_antes_de_criar_o_log_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "logs"
    for nome, valor in {
        "NO_ID": "1",
        "ANEL": "1,2",
        "URLS": "1=http://x:1,2=http://y:1",
        "DOISPC_PARTICIPANTES": "2@p:1",
        "LOG_DIR": str(log_dir),
    }.items():
        monkeypatch.setenv(nome, valor)

    with pytest.raises(SystemExit, match="DOISPC_PARTICIPANTES") as saida:
        main()

    assert "fora do ANEL [1, 2], sem [2]" in str(saida.value)
    assert not log_dir.exists()


class _Integracao:
    """Candidatos 1 a 3 de `construir_candidato` e Participantes 11 a 13 que respondem aos três.

    Todos os logs, estruturados e duráveis, ficam em `tmp_path`.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        portas = {i: porta_livre() for i in (*CANDIDATOS, *PART_IDS)}
        candidatos = {cid: No(cid, "127.0.0.1", portas[cid]) for cid in CANDIDATOS}
        apps = {
            pid: criar_app_part(
                ServicoParticipante(
                    No(pid, "127.0.0.1", portas[pid]), candidatos,
                    log_duravel=LogDuravelArquivo(tmp_path / f"part-{pid}.log"),
                    cliente=ClienteRede(), deadline=1.0,
                    log=abrir_log(tmp_path / f"part-{pid}.jsonl", pid),
                )
            )
            for pid in PART_IDS
        }
        comum = {
            "ANEL": ",".join(map(str, CANDIDATOS)),
            "URLS": ",".join(f"{c.id}={c.base_url}" for c in candidatos.values()),
            "DEADLINE": "0.5",
            "LOG_DIR": str(tmp_path),
            "DOISPC_PARTICIPANTES": ",".join(f"{pid}@127.0.0.1:{portas[pid]}" for pid in PART_IDS),
        }
        monkeypatch.delenv("DOISPC_CAIR_APOS_DECIDIR", raising=False)
        for cid in CANDIDATOS:
            for nome, valor in {
                **comum,
                "NO_ID": str(cid),
                "PORTA": str(portas[cid]),
                "DOISPC_LOG_DURAVEL": str(tmp_path / f"candidato-{cid}.log"),
            }.items():
                monkeypatch.setenv(nome, valor)
            apps[cid], _porta = construir_candidato()
        self.servidores = {i: ServidorLocal(app, portas[i]) for i, app in apps.items()}
        self._tmp_path = tmp_path

    def __enter__(self) -> "_Integracao":
        for servidor in self.servidores.values():
            servidor.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        for servidor in reversed(self.servidores.values()):
            servidor.__exit__(*exc)

    def postar(self, cid: int, caminho: str, corpo: dict | None = None) -> httpx.Response:
        url = f"{self.servidores[cid].base_url}{caminho}"
        return httpx.post(url, json=corpo or {}, timeout=10.0)

    def estado(self, cid: int, transacao: str | None = None) -> dict:
        consulta = "" if transacao is None else f"?transacao={transacao}"
        return http_json(f"{self.servidores[cid].base_url}/estado{consulta}")

    def voltas_no_iniciador(self) -> int:
        """Anúncios de Líder que o candidato 1 recebeu. Cada um fecha a volta de uma rodada dele."""
        eventos = inv.carregar_eventos(self._tmp_path / "candidato-1.jsonl")
        return sum(
            ev.tipo == inv.EV_RECEBEU and ev.campos.get("msg_tipo") == TIPO_LIDER for ev in eventos
        )


def test_dois_lideres_comitam_com_prefixos_distintos_e_nenhum_invariante_reprova(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Integracao(tmp_path, monkeypatch) as rede:
        rede.postar(1, "/iniciar-eleicao")
        assert esperar_ate(lambda: rede.voltas_no_iniciador() == 1)
        assert [rede.estado(cid)["lider"] for cid in CANDIDATOS] == [3, 3, 3]

        # O 3 fica fora só durante a segunda rodada, e volta crendo que ainda é o Líder.
        rede.postar(3, "/falha/crash")
        rede.postar(1, "/iniciar-eleicao")
        assert esperar_ate(lambda: rede.voltas_no_iniciador() == 2)
        rede.postar(3, "/falha/limpar")
        assert [rede.estado(cid)["lider"] for cid in CANDIDATOS] == [2, 2, 3]

        # Os mesmos nomes nos dois Líderes, que só o prefixo separa.
        with ThreadPoolExecutor(max_workers=2 * len(NOMES)) as pool:
            futuros = {
                (cid, nome): pool.submit(rede.postar, cid, "/transacao", {"transacao": nome})
                for cid in (2, 3)
                for nome in NOMES
            }
        for (cid, nome), futuro in futuros.items():
            resposta = futuro.result()
            assert resposta.status_code == 200, resposta.text
            assert resposta.json()["transacao"] == f"c{cid}-{nome}"
            assert resposta.json()["decisao"] == DECISAO_COMMIT

        recusa = rede.postar(1, "/transacao", {"transacao": "tx-9"})
        assert (recusa.status_code, recusa.json()["lider"]) == (409, 2)

        assert esperar_ate(
            lambda: all(rede.estado(cid, f"c{cid}-{nome}")["concluido"] for cid, nome in futuros)
        )

    dados = inv.Dados(inv.carregar_eventos(tmp_path), inv.carregar_duraveis(tmp_path))
    vereditos = {v.numero: v for v in inv.verificar_todos(dados)}
    assert {n: v.falhas for n, v in vereditos.items() if v.verificados and not v.ok} == {}
    assert {n: vereditos[n].verificados for n in (1, 2, 3, 4, 6, 7)} == {
        1: 2, 2: 2, 3: 2, 4: 10, 6: 10, 7: 10,
    }
