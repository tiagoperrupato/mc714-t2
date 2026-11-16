"""Crash e reinício do Coordenador e de um Participante do 2PC sobre a rede real, sem Docker.

Sobe três Participantes como apps FastAPI em uvicorn locais. A primeira vida do
Coordenador, a que cai, é modelada em processo: ou ele grava a Decisao e nenhuma
DECISAO sai, ou só a entrada em WAIT chega ao log. A segunda vida sai de
`construir_coordenador`, o mesmo caminho do contêiner, sobre o mesmo log e a
mesma porta. Os Participantes ficam em dúvida enquanto o Coordenador está fora e
decidem depois que ele volta. Um Participante reiniciado em dúvida sai de
`construir_participante` e espera o Coordenador voltar. Os cenários
`2pc crash-coord` e `2pc crash-coord-part` mostram o mesmo com os processos
derrubados de verdade.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analise import invariantes as inv
from src.comum.falhas import ClienteComFalhas, ControladorFalhas
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede, EnvioOk, ResultadoEnvio
from src.doispc.__main__ import construir_coordenador, construir_participante
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coord
from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    VOTO_SIM,
    LogDuravelArquivo,
)
from src.doispc.maquina import MSG_DECISAO, GravarLog, iniciar_transacao
from src.doispc.participante import ServicoParticipante, criar_app as criar_app_part
from src.experimentos.conteineres import decisoes, estados_participantes, http_json
from tests.apoio import ServidorLocal, esperar_ate, porta_livre

COORD_ID = 1
PART_IDS = (11, 12, 13)


class _ClienteSuprimeDecisao:
    """Cliente que entrega tudo, menos a DECISAO: o Coordenador morre logo depois de decidir.

    Os PREPARE passam e os Votos voltam, então o Coordenador grava a Decisao, mas nenhum
    Participante a recebe.
    """

    def __init__(self, cliente: ClienteRede) -> None:
        self._cliente = cliente
        self.decisoes_suprimidas = 0

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        if mensagem.tipo == MSG_DECISAO:
            self.decisoes_suprimidas += 1
            return EnvioOk(status=200, corpo={"ack": True, "descartada": True})
        return self._cliente.enviar(destino_url, mensagem, deadline)

    def fechar(self) -> None:
        self._cliente.fechar()


class _Participantes:
    """Três Participantes que votam SIM, cada um com Log_Duravel em `tmp_path` e servidor local."""

    def __init__(self, tmp_path: Path) -> None:
        portas = {no_id: porta_livre() for no_id in (COORD_ID, *PART_IDS)}
        self.coord_no = No(COORD_ID, "127.0.0.1", portas[COORD_ID])
        self.part_nos = {pid: No(pid, "127.0.0.1", portas[pid]) for pid in PART_IDS}
        self.servicos = {
            pid: ServicoParticipante(
                self.part_nos[pid],
                self.coord_no,
                log_duravel=LogDuravelArquivo(tmp_path / f"part-{pid}.log"),
                cliente=ClienteRede(),
                deadline=1.0,
                voto_desejado=VOTO_SIM,
            )
            for pid in PART_IDS
        }
        self._servidores = {
            pid: ServidorLocal(criar_app_part(self.servicos[pid]), portas[pid]) for pid in PART_IDS
        }

    def __enter__(self) -> "_Participantes":
        for servidor in self._servidores.values():
            servidor.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        for servidor in reversed(self._servidores.values()):
            servidor.__exit__(*exc)

    def parar(self, pid: int) -> None:
        """Derruba o servidor do Participante `pid`, como um crash."""
        self._servidores[pid].__exit__(None, None, None)

    def em_duvida(self) -> bool:
        return all(self.servicos[p].estado.em_duvida for p in PART_IDS)

    def esperar_decisao(self, esperada: str) -> None:
        def decidiram() -> bool:
            return all(self.servicos[p].estado.decisao == esperada for p in PART_IDS)

        assert esperar_ate(decidiram), {p: self.servicos[p].estado for p in PART_IDS}


def _coordenador_do_ambiente(tmp_path: Path, parts: _Participantes, monkeypatch) -> object:
    """Monta o Coordenador pelo caminho do contêiner, sobre `tmp_path/coord.log`."""
    ambiente = {
        "COORD_ID": str(COORD_ID),
        "COORD_PORTA": str(parts.coord_no.porta),
        "DOISPC_PARTICIPANTES": ",".join(
            f"{pid}@127.0.0.1:{parts.part_nos[pid].porta}" for pid in PART_IDS
        ),
        "DOISPC_LOG_DURAVEL": str(tmp_path / "coord.log"),
        "DOISPC_DEADLINE_VOTO": "2.0",
        "DOISPC_DEADLINE_DECISAO": "1.0",
        "LOG_DIR": str(tmp_path / "logs"),
    }
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    monkeypatch.delenv("DOISPC_CAIR_APOS_DECIDIR", raising=False)
    app, _porta = construir_coordenador()
    return app


def _participante_do_ambiente(
    tmp_path: Path, parts: _Participantes, pid: int, monkeypatch
) -> object:
    """Monta o Participante `pid` pelo caminho do contêiner, sobre o Log_Duravel da primeira vida."""
    ambiente = {
        "PART_ID": str(pid),
        "PART_PORTA": str(parts.part_nos[pid].porta),
        "COORD_ID": str(COORD_ID),
        "COORD_HOST": "127.0.0.1",
        "COORD_PORTA": str(parts.coord_no.porta),
        "DOISPC_VOTO": VOTO_SIM,
        "DOISPC_LOG_DURAVEL": str(tmp_path / f"part-{pid}.log"),
        "LOG_DIR": str(tmp_path / f"logs-{pid}"),
    }
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    app, _porta = construir_participante()
    return app


def _primeira_vida_que_cai(tmp_path: Path, parts: _Participantes) -> None:
    """O Coordenador grava COMMIT e cai sem difundir a DECISAO, e os três ficam em dúvida."""
    cliente_crash = _ClienteSuprimeDecisao(ClienteRede())
    primeira_vida = ServicoCoordenador(
        parts.coord_no,
        parts.part_nos,
        log_duravel=LogDuravelArquivo(tmp_path / "coord.log"),
        cliente=cliente_crash,
        deadline_voto=2.0,
        deadline_decisao=1.0,
    )
    with ServidorLocal(criar_app_coord(primeira_vida), parts.coord_no.porta):
        assert primeira_vida.iniciar("tx-crash")["decisao"] == DECISAO_COMMIT
        assert esperar_ate(lambda: cliente_crash.decisoes_suprimidas >= len(PART_IDS))
    assert parts.em_duvida()


def test_coordenador_reiniciado_depois_de_decidir_entrega_a_decisao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Participantes(tmp_path) as parts:
        _primeira_vida_que_cai(tmp_path, parts)
        app = _coordenador_do_ambiente(tmp_path, parts, monkeypatch)
        with ServidorLocal(app, parts.coord_no.porta):
            parts.esperar_decisao(DECISAO_COMMIT)


def test_participante_reiniciado_em_duvida_decide_quando_o_coordenador_volta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Participantes(tmp_path) as parts:
        _primeira_vida_que_cai(tmp_path, parts)
        parts.parar(11)
        portas = {pid: no.porta for pid, no in parts.part_nos.items()}
        app_11 = _participante_do_ambiente(tmp_path, parts, 11, monkeypatch)
        with ServidorLocal(app_11, portas[11]) as servidor_11:
            # Sem Coordenador, o 11 volta do próprio log com Voto SIM e sem Decisao.
            estado = http_json(f"{servidor_11.base_url}/estado")
            assert (estado["voto"], estado["decisao"], estado["em_duvida"]) == ("SIM", None, True)
            app = _coordenador_do_ambiente(tmp_path, parts, monkeypatch)
            with ServidorLocal(app, parts.coord_no.porta):
                finais = decisoes(estados_participantes(esperar_decisao=True, portas=portas))
                assert finais == dict.fromkeys(PART_IDS, DECISAO_COMMIT)

    # Um segundo reinício do 11, já decidido, recupera o COMMIT do Log_Duravel.
    _participante_do_ambiente(tmp_path, parts, 11, monkeypatch)
    jsonl = tmp_path / "logs-11" / "doispc-participante-11.jsonl"
    recuperacoes = [ev for ev in inv.carregar_eventos(jsonl) if ev.tipo == inv.EV_RECUPERACAO]
    assert [ev.campos["decisao"] for ev in recuperacoes] == [None, DECISAO_COMMIT]
    # O 05 conta a recuperação do Coordenador e a segunda do 11, e ninguém termina em dúvida.
    v5 = inv.invariante_05_durabilidade(
        inv.Dados(eventos=inv.carregar_eventos(tmp_path), duraveis=inv.carregar_duraveis(tmp_path))
    )
    assert v5.ok and v5.verificados == 2
    assert not v5.avisos


def test_coordenador_reiniciado_em_wait_aborta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _Participantes(tmp_path) as parts:
        # Primeira vida: grava a entrada em WAIT, manda os PREPARE e cai antes de
        # receber os Votos. As Acao da Máquina são executadas à mão, sem servidor.
        log_coord = LogDuravelArquivo(tmp_path / "coord.log")
        _, acoes = iniciar_transacao(COORD_ID, "tx-wait", PART_IDS)
        for acao in acoes:
            if isinstance(acao, GravarLog):
                log_coord.registrar(acao.entrada)
            else:
                parts.servicos[acao.mensagem.destino].receber(acao.mensagem)
        assert parts.em_duvida()

        app = _coordenador_do_ambiente(tmp_path, parts, monkeypatch)
        with ServidorLocal(app, parts.coord_no.porta):
            parts.esperar_decisao(DECISAO_ABORT)

    assert log_coord.recuperar("tx-wait").decisao == DECISAO_ABORT


def test_crash_total_do_coordenador_via_controlador_bloqueia_decisao(tmp_path: Path) -> None:
    # Com o ControladorFalhas em crash no despacho do Coordenador, nem os PREPARE
    # saem. A coleta estoura o Deadline e decide ABORT, e nenhum Participante tem
    # estado. Prova que o controlador suprime o despacho de saída de um Nó em crash.
    with _Participantes(tmp_path) as parts:
        controlador = ControladorFalhas(2026)
        coordenador = ServicoCoordenador(
            parts.coord_no,
            parts.part_nos,
            log_duravel=LogDuravelArquivo(tmp_path / "coord.log"),
            cliente=ClienteComFalhas(ClienteRede(), controlador),
            deadline_voto=0.8,
            deadline_decisao=0.4,
        )
        app = criar_app_coord(coordenador, controlador=controlador)
        with ServidorLocal(app, parts.coord_no.porta):
            controlador.injetar_crash()
            coordenador.iniciar("tx-crash-total")

        for pid in PART_IDS:
            assert parts.servicos[pid].estado is None, (pid, parts.servicos[pid].estado)


@pytest.mark.parametrize("papel", ["coordenador", "participante"])
def test_mensagem_recebida_em_crash_devolve_503(tmp_path: Path, papel: str) -> None:
    """O `/mensagem` dos dois papéis passa pelo controlador, e um Nó em crash responde 503."""
    import httpx

    coord = No(COORD_ID, "127.0.0.1", porta_livre())
    part = No(PART_IDS[0], "127.0.0.1", porta_livre())
    log_duravel = LogDuravelArquivo(tmp_path / f"{papel}.log")
    controlador = ControladorFalhas(2026)
    if papel == "coordenador":
        eu, origem = coord, part
        servico = ServicoCoordenador(
            coord, {part.id: part}, log_duravel=log_duravel, cliente=ClienteRede(),
            deadline_voto=1.0, deadline_decisao=1.0,
        )
        app = criar_app_coord(servico, controlador=controlador)
    else:
        eu, origem = part, coord
        servico = ServicoParticipante(
            part, coord, log_duravel=log_duravel, cliente=ClienteRede(), deadline=1.0,
            voto_desejado=VOTO_SIM,
        )
        app = criar_app_part(servico, controlador=controlador)
    mensagem = Mensagem(tipo=MSG_DECISAO, origem=origem.id, destino=eu.id, corpo={"transacao": "tx-x"})

    controlador.injetar_crash()
    with ServidorLocal(app, eu.porta) as servidor:
        resposta = httpx.post(f"{servidor.base_url}/mensagem", json=mensagem.para_json(), timeout=5.0)

    assert resposta.status_code == 503
    assert log_duravel.transacoes() == ()
