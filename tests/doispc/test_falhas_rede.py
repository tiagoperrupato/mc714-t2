"""Falhas de aplicação no 2PC sobre a rede real (Requisito 8.5), sem Docker.

Sobe o Coordenador e três Participantes como apps FastAPI em uvicorn locais e
exercita o bloqueio característico do 2PC: quando o Coordenador cai depois de
coletar os Votos e antes de difundir a Decisao, os Participantes que votaram SIM
permanecem em Estado_Em_Duvida (Requisito 8.5).

O crash do Coordenador é modelado pelo controlador de falhas plugado no despacho
de saída: a DECISAO que o Coordenador tenta difundir é suprimida antes de tocar
a rede, exatamente como se o processo tivesse morrido entre decidir e difundir.
Os PREPARE já saíram e os Votos já voltaram, então a Máquina do Coordenador
grava a Decisao no Log_Duravel (write-ahead), mas nenhum Participante a recebe.

Não depende de Docker: é a mesma prova que o cenário de compose demonstra, mas
roda em qualquer ambiente com pytest.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest
import uvicorn

from src.comum.falhas import ControladorFalhas
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede, EnvioOk, ResultadoEnvio
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coord
from src.doispc.log_duravel import DECISAO_COMMIT, VOTO_SIM, LogDuravelArquivo
from src.doispc.maquina import MSG_DECISAO
from src.doispc.participante import ServicoParticipante, criar_app as criar_app_part

COORD_ID = 1
PART_IDS = (11, 12, 13)


def _porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServidorLocal:
    def __init__(self, app, porta: int) -> None:
        self.porta = porta
        config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning")
        self._servidor = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._servidor.run, daemon=True)

    def __enter__(self) -> "_ServidorLocal":
        self._thread.start()
        limite = time.monotonic() + 10.0
        while time.monotonic() < limite:
            if self._servidor.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("servidor local não ficou pronto a tempo")

    def __exit__(self, *_exc: object) -> None:
        self._servidor.should_exit = True
        self._thread.join(timeout=10.0)


class _ClienteSuprimeDecisao:
    """Cliente que entrega tudo, menos a DECISAO: simula o crash antes de difundir.

    Modela o Coordenador que morre depois de coletar os Votos e gravar a Decisao,
    mas antes de a DECISAO alcançar qualquer Participante. Os PREPARE passam (os
    Votos voltam normalmente); as Mensagens de tipo `decisao` são descartadas.
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


def test_crash_do_coordenador_deixa_participantes_em_duvida(tmp_path: Path) -> None:
    portas = {no_id: _porta_livre() for no_id in (COORD_ID, *PART_IDS)}
    coord_no = No(COORD_ID, "127.0.0.1", portas[COORD_ID])
    part_nos = {pid: No(pid, "127.0.0.1", portas[pid]) for pid in PART_IDS}

    servidores: list[_ServidorLocal] = []
    servicos_part: dict[int, ServicoParticipante] = {}
    try:
        # Participantes sobem primeiro, todos votando SIM.
        for pid in PART_IDS:
            log = LogDuravelArquivo(tmp_path / f"part-{pid}.log")
            servico = ServicoParticipante(
                part_nos[pid],
                coord_no,
                log_duravel=log,
                cliente=ClienteRede(),
                deadline=2.0,
                voto_desejado=VOTO_SIM,
            )
            servicos_part[pid] = servico
            s = _ServidorLocal(criar_app_part(servico), portas[pid])
            s.__enter__()
            servidores.append(s)

        # Coordenador com o cliente que suprime a DECISAO: ele decide COMMIT e
        # grava no Log_Duravel, mas a DECISAO nunca chega aos Participantes.
        cliente_crash = _ClienteSuprimeDecisao(ClienteRede())
        servico_coord = ServicoCoordenador(
            coord_no,
            part_nos,
            log_duravel=LogDuravelArquivo(tmp_path / "coord.log"),
            cliente=cliente_crash,
            deadline_voto=2.0,
            deadline_decisao=1.0,
        )
        s = _ServidorLocal(criar_app_coord(servico_coord), portas[COORD_ID])
        s.__enter__()
        servidores.append(s)

        resultado = servico_coord.iniciar("tx-crash")

        # O Coordenador decidiu COMMIT (todos votaram SIM) e gravou a Decisao...
        assert resultado["decisao"] == DECISAO_COMMIT
        # ...mas a DECISAO foi suprimida: os Participantes ficaram sem conhecê-la.
        assert cliente_crash.decisoes_suprimidas == len(PART_IDS)

        # Cada Participante votou SIM e não recebeu a Decisao: Estado_Em_Duvida.
        for pid in PART_IDS:
            estado = servicos_part[pid].estado
            assert estado is not None, pid
            assert estado.voto == VOTO_SIM, (pid, estado.voto)
            assert estado.decisao is None, (pid, estado.decisao)
            assert estado.em_duvida is True, (pid, estado)
    finally:
        for s in reversed(servidores):
            s.__exit__(None, None, None)


def test_crash_total_do_coordenador_via_controlador_bloqueia_decisao(tmp_path: Path) -> None:
    # Variante que usa o ControladorFalhas de verdade no despacho do Coordenador:
    # ao injetar crash ANTES de iniciar a transação, nem os PREPARE saem; os
    # Participantes nunca votam e, do lado do Coordenador, a coleta estoura o
    # Deadline levando a ABORT por timeout — mas nenhum Participante conhece a
    # Decisao, permanecendo sem Voto nem Decisao. Aqui provamos que o controlador
    # realmente suprime o despacho de saída de um Nó em crash.
    from src.comum.falhas import ClienteComFalhas

    portas = {no_id: _porta_livre() for no_id in (COORD_ID, *PART_IDS)}
    coord_no = No(COORD_ID, "127.0.0.1", portas[COORD_ID])
    part_nos = {pid: No(pid, "127.0.0.1", portas[pid]) for pid in PART_IDS}

    servidores: list[_ServidorLocal] = []
    servicos_part: dict[int, ServicoParticipante] = {}
    try:
        for pid in PART_IDS:
            servico = ServicoParticipante(
                part_nos[pid],
                coord_no,
                log_duravel=LogDuravelArquivo(tmp_path / f"part-{pid}.log"),
                cliente=ClienteRede(),
                deadline=1.0,
                voto_desejado=VOTO_SIM,
            )
            servicos_part[pid] = servico
            s = _ServidorLocal(criar_app_part(servico), portas[pid])
            s.__enter__()
            servidores.append(s)

        controlador = ControladorFalhas(2026, dormir=time.sleep)
        cliente = ClienteComFalhas(ClienteRede(), controlador)
        servico_coord = ServicoCoordenador(
            coord_no,
            part_nos,
            log_duravel=LogDuravelArquivo(tmp_path / "coord.log"),
            cliente=cliente,
            deadline_voto=0.8,
            deadline_decisao=0.4,
        )
        s = _ServidorLocal(criar_app_coord(servico_coord, controlador=controlador), portas[COORD_ID])
        s.__enter__()
        servidores.append(s)

        # Coordenador em crash: nenhum PREPARE sai; a coleta estoura o Deadline.
        controlador.injetar_crash()
        servico_coord.iniciar("tx-crash-total")

        # Participantes nunca receberam PREPARE: sem estado de transação.
        for pid in PART_IDS:
            assert servicos_part[pid].estado is None, (pid, servicos_part[pid].estado)
    finally:
        for s in reversed(servidores):
            s.__exit__(None, None, None)
