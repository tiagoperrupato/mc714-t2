"""Participante com vários Coordenadores e `/estado` por transação, em servidores locais.

O Participante responde a quem mandou a Mensagem quando a origem está no mapa de Coordenadores, e
recusa a origem desconhecida antes da Máquina. Com um Coordenador só, o caminho do contêiner sem
`DOISPC_COORDENADORES` manda o Voto ao `COORD_ID`, como no 2PC puro.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.analise import invariantes as inv
from src.comum.config import ConfigInvalidaError
from src.comum.log import abrir_log
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede, criar_app_recebedor
from src.doispc.__main__ import _mapa_de_nos, construir_participante
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coord
from src.doispc.log_duravel import DECISAO_COMMIT, VOTO_SIM, LogDuravelArquivo, LogDuravelMemoria
from src.doispc.maquina import MSG_ACK, MSG_DECISAO, MSG_PREPARE, MSG_VOTO
from src.doispc.participante import ServicoParticipante, criar_app as criar_app_part
from src.experimentos.conteineres import http_json
from tests.apoio import ServidorLocal, esperar_ate, porta_livre

PART_ID = 11
PART_IDS = (11, 12, 13)


class _CoordenadoresFalsos:
    """Recebedores em servidores locais que só anotam (tipo, transação) de cada Mensagem."""

    def __init__(self, ids: tuple[int, ...]) -> None:
        self.recebidas: dict[int, list[tuple[str, str]]] = {cid: [] for cid in ids}
        self.servidores = {
            cid: ServidorLocal(criar_app_recebedor(self._anotador(cid))) for cid in ids
        }

    def _anotador(self, cid: int):
        def anotar(mensagem: Mensagem) -> dict:
            self.recebidas[cid].append((mensagem.tipo, mensagem.corpo.get("transacao")))
            return {"ack": True}

        return anotar

    def ambiente(self) -> str:
        return ",".join(f"{cid}@127.0.0.1:{s.porta}" for cid, s in self.servidores.items())

    def __enter__(self) -> "_CoordenadoresFalsos":
        for servidor in self.servidores.values():
            servidor.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        for servidor in self.servidores.values():
            servidor.__exit__(*exc)


def _participante_do_ambiente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ambiente: dict[str, str]
) -> ServidorLocal:
    """Participante 11 montado por `construir_participante`, o caminho do contêiner."""
    monkeypatch.delenv("DOISPC_COORDENADORES", raising=False)
    base = {
        "PART_ID": str(PART_ID),
        "PART_PORTA": str(porta_livre()),
        "DOISPC_LOG_DURAVEL": str(tmp_path / "part-11.log"),
        "LOG_DIR": str(tmp_path / "logs"),
    }
    for nome, valor in {**base, **ambiente}.items():
        monkeypatch.setenv(nome, valor)
    app, porta = construir_participante()
    return ServidorLocal(app, porta)


def _postar(base_url: str, tipo: str, origem: int, transacao: str, **corpo: str) -> dict:
    mensagem = Mensagem(tipo, origem, PART_ID, {"transacao": transacao, **corpo})
    return httpx.post(f"{base_url}/mensagem", json=mensagem.para_json(), timeout=5.0).json()


def _eventos_do_participante(tmp_path: Path) -> list[inv.EventoLog]:
    return list(inv.carregar_eventos(tmp_path / "logs" / "doispc-participante-11.jsonl"))


def _coordenadores_no_inicio(tmp_path: Path) -> list[int]:
    """Os Coordenadores que o Participante gravou no `inicio_replica`."""
    inicio = next(ev for ev in _eventos_do_participante(tmp_path) if ev.tipo == "inicio_replica")
    return inicio.campos["cenario"]["coordenadores"]


def test_um_coordenador_recebe_o_voto_no_coord_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _CoordenadoresFalsos((1,)) as coords:
        ambiente = {
            "COORD_ID": "1",
            "COORD_HOST": "127.0.0.1",
            "COORD_PORTA": str(coords.servidores[1].porta),
        }
        with _participante_do_ambiente(tmp_path, monkeypatch, ambiente) as part:
            assert _postar(part.base_url, MSG_PREPARE, 1, "tx-1") == {"ack": True}
            voto = [(MSG_VOTO, "tx-1")]
            assert esperar_ate(lambda: coords.recebidas[1] == voto), coords.recebidas

    assert _coordenadores_no_inicio(tmp_path) == [1]


def test_dois_coordenadores_recebem_voto_e_ack_na_origem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _CoordenadoresFalsos((2, 3)) as coords:
        ambiente = {"DOISPC_COORDENADORES": coords.ambiente()}
        with _participante_do_ambiente(tmp_path, monkeypatch, ambiente) as part:
            _postar(part.base_url, MSG_PREPARE, 2, "tx-a")
            _postar(part.base_url, MSG_PREPARE, 3, "tx-b")
            _postar(part.base_url, MSG_DECISAO, 3, "tx-b", decisao=DECISAO_COMMIT)
            _postar(part.base_url, MSG_DECISAO, 2, "tx-a", decisao=DECISAO_COMMIT)
            esperadas = {
                2: [(MSG_VOTO, "tx-a"), (MSG_ACK, "tx-a")],
                3: [(MSG_VOTO, "tx-b"), (MSG_ACK, "tx-b")],
            }
            assert esperar_ate(lambda: coords.recebidas == esperadas), coords.recebidas

    assert _coordenadores_no_inicio(tmp_path) == [2, 3]


def test_origem_desconhecida_nao_grava_nem_envia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _CoordenadoresFalsos((2, 3)) as coords:
        ambiente = {"DOISPC_COORDENADORES": coords.ambiente()}
        with _participante_do_ambiente(tmp_path, monkeypatch, ambiente) as part:
            resposta = _postar(part.base_url, MSG_PREPARE, 7, "tx-x")
            # A fila de saída é FIFO, então um envio de tx-x chegaria antes do Voto de tx-y.
            _postar(part.base_url, MSG_PREPARE, 2, "tx-y")
            voto = [(MSG_VOTO, "tx-y")]
            assert esperar_ate(lambda: coords.recebidas[2] == voto), coords.recebidas

    assert resposta == {
        "ack": False,
        "erro": "origem 7 não é Coordenador conhecido [2, 3]. "
        "Confira DOISPC_COORDENADORES ou COORD_ID.",
    }
    assert coords.recebidas[3] == []
    assert LogDuravelArquivo(tmp_path / "part-11.log").transacoes() == ("tx-y",)
    eventos = _eventos_do_participante(tmp_path)
    recusas = [ev.campos for ev in eventos if ev.tipo == "origem_desconhecida"]
    assert recusas == [
        {"msg_tipo": MSG_PREPARE, "origem": 7, "transacao": "tx-x", "conhecidos": [2, 3]}
    ]
    assert [ev.campos["transacao"] for ev in eventos if ev.tipo == inv.EV_RECEBE] == ["tx-y"]


@pytest.mark.parametrize("item", ["2@127.0.0.1", "x@h:8000", "2@:8000", "2-h:8000", "2@h:porta"])
def test_mapa_de_nos_malformado_cita_a_variavel(
    item: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOISPC_COORDENADORES", f"1@a:8000,{item}")
    with pytest.raises(ConfigInvalidaError) as erro:
        _mapa_de_nos("DOISPC_COORDENADORES")
    assert "DOISPC_COORDENADORES" in str(erro.value)
    assert "id@host:porta" in str(erro.value)
    assert repr(item) in str(erro.value)


def test_mapa_de_coordenadores_vazio_recusa() -> None:
    with pytest.raises(ValueError, match="DOISPC_COORDENADORES"):
        ServicoParticipante(
            No(PART_ID, "127.0.0.1", 1), {},
            log_duravel=LogDuravelMemoria(),
            cliente=ClienteRede(),
            deadline=1.0,
        )


class _DoisCoordenadores:
    """Coordenadores 2 e 3 e Participantes 11 a 13 reais em servidores locais, com logs em disco."""

    def __init__(self, tmp_path: Path) -> None:
        nos = {i: No(i, "127.0.0.1", porta_livre()) for i in (2, 3, *PART_IDS)}
        coordenadores = {cid: nos[cid] for cid in (2, 3)}
        participantes = {pid: nos[pid] for pid in PART_IDS}
        self.participantes = {
            pid: ServicoParticipante(
                nos[pid], coordenadores,
                log_duravel=LogDuravelArquivo(tmp_path / f"part-{pid}.log"),
                cliente=ClienteRede(), deadline=1.0, voto_desejado=VOTO_SIM,
                log=abrir_log(tmp_path / f"part-{pid}.jsonl", pid),
            )
            for pid in PART_IDS
        }
        self.coordenadores = {
            cid: ServicoCoordenador(
                nos[cid], participantes,
                log_duravel=LogDuravelArquivo(tmp_path / f"coord-{cid}.log"),
                cliente=ClienteRede(), deadline_voto=2.0, deadline_decisao=1.0,
                log=abrir_log(tmp_path / f"coord-{cid}.jsonl", cid),
            )
            for cid in (2, 3)
        }
        apps = {pid: criar_app_part(s) for pid, s in self.participantes.items()}
        apps.update({cid: criar_app_coord(s) for cid, s in self.coordenadores.items()})
        self.servidores = {i: ServidorLocal(apps[i], nos[i].porta) for i in nos}

    def __enter__(self) -> "_DoisCoordenadores":
        for servidor in self.servidores.values():
            servidor.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        for servidor in reversed(self.servidores.values()):
            servidor.__exit__(*exc)

    def comitar(self, cid: int, transacao: str) -> None:
        """Inicia a transação no Coordenador `cid` e espera o COMMIT e todos os ACKs."""
        assert self.coordenadores[cid].iniciar(transacao)["decisao"] == DECISAO_COMMIT
        assert esperar_ate(lambda: self.coordenadores[cid].instantaneo(transacao)["concluido"])

    def estado(self, no_id: int, transacao: str | None = None) -> dict:
        consulta = "" if transacao is None else f"?transacao={transacao}"
        return http_json(f"{self.servidores[no_id].base_url}/estado{consulta}")


def _dados(tmp_path: Path) -> inv.Dados:
    eventos, duraveis = inv.carregar_eventos(tmp_path), inv.carregar_duraveis(tmp_path)
    return inv.Dados(eventos=eventos, duraveis=duraveis)


def _reprovados(tmp_path: Path) -> dict[int, tuple[str, ...]]:
    vereditos = inv.verificar_todos(_dados(tmp_path))
    return {v.numero: v.falhas for v in vereditos if v.verificados and not v.ok}


def test_coordenadores_com_ids_distintos_concluem_e_o_07_passa(tmp_path: Path) -> None:
    with _DoisCoordenadores(tmp_path) as rede:
        rede.comitar(2, "c2-tx")
        rede.comitar(3, "c3-tx")

    v7 = inv.invariante_07_coordenador_unico(_dados(tmp_path))
    assert v7.ok and v7.verificados == 2
    assert _reprovados(tmp_path) == {}


def test_estado_por_transacao_nos_dois_servicos(tmp_path: Path) -> None:
    with _DoisCoordenadores(tmp_path) as rede:
        rede.comitar(2, "c2-tx")
        rede.comitar(3, "c3-tx")
        assert esperar_ate(lambda: rede.estado(PART_ID, "c3-tx")["decisao"] == DECISAO_COMMIT)

        coord_2 = rede.estado(2, "c2-tx")
        assert (coord_2["transacao"], coord_2["decisao"], coord_2["concluido"]) == (
            "c2-tx", DECISAO_COMMIT, True,
        )
        assert rede.estado(2, "c3-tx") == rede.estado(2, "desconhecida") == {
            "no": 2, "participantes": list(PART_IDS), "transacao": None,
            "votos": {}, "decisao": None, "concluido": False,
        }

        assert rede.estado(PART_ID)["transacao"] == "c3-tx"
        part_2 = rede.estado(PART_ID, "c2-tx")
        assert (part_2["transacao"], part_2["voto"], part_2["decisao"]) == (
            "c2-tx", VOTO_SIM, DECISAO_COMMIT,
        )
        assert rede.estado(PART_ID, "desconhecida") == {
            "no": PART_ID, "voto_desejado": VOTO_SIM, "transacao": None, "voto": None,
            "decisao": None, "aplicada": False, "em_duvida": False,
        }


def test_mesmo_id_em_dois_coordenadores_so_o_07_reprova(tmp_path: Path) -> None:
    with _DoisCoordenadores(tmp_path) as rede:
        rede.comitar(2, "tx-x")
        rede.comitar(3, "tx-x")

    assert _reprovados(tmp_path) == {7: ("transação tx-x: Coordenadores [2, 3]",)}
