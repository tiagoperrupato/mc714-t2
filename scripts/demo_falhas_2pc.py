"""Demo de falha no 2PC: crash do Coordenador e Participantes bloqueados.

Sobe o Coordenador e três Participantes como apps FastAPI locais (sem Docker).
A demonstração reproduz o bloqueio característico do 2PC (Requisito 8.5): o
Coordenador coleta os Votos (todos SIM), decide COMMIT e grava a Decisao no
Log_Duravel, mas cai antes de difundir a Decisao. Os Participantes que votaram
SIM ficam em Estado_Em_Duvida, aguardando uma Decisao que nunca chega.

O crash do Coordenador entre decidir e difundir é modelado suprimindo o despacho
da Mensagem de DECISAO: os PREPARE saem, os Votos voltam, mas a DECISAO não
alcança nenhum Participante — exatamente como se o processo tivesse morrido nesse
instante.

Em prosa, sobre o compose, equivale a injetar `POST /falha/crash` no Coordenador
logo após a coleta dos Votos e observar `GET /estado` de cada Participante
reportando `em_duvida = true`.

Uso:
    python -m scripts.demo_falhas_2pc
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

import uvicorn

from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede, EnvioOk, ResultadoEnvio
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coord
from src.doispc.log_duravel import VOTO_SIM, LogDuravelArquivo
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
        config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="error")
        self._servidor = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._servidor.run, daemon=True)

    def __enter__(self) -> "_ServidorLocal":
        self._thread.start()
        limite = time.monotonic() + 10.0
        while time.monotonic() < limite and not self._servidor.started:
            time.sleep(0.02)
        if not self._servidor.started:
            raise RuntimeError("servidor local não ficou pronto")
        return self

    def __exit__(self, *_exc: object) -> None:
        self._servidor.should_exit = True
        self._thread.join(timeout=10.0)


class _ClienteSuprimeDecisao:
    """Entrega tudo menos a DECISAO: modela o crash do Coordenador antes de difundir."""

    def __init__(self, cliente: ClienteRede) -> None:
        self._cliente = cliente
        self.suprimidas = 0

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        if mensagem.tipo == MSG_DECISAO:
            self.suprimidas += 1
            return EnvioOk(status=200, corpo={"ack": True, "descartada": True})
        return self._cliente.enviar(destino_url, mensagem, deadline)

    def fechar(self) -> None:
        self._cliente.fechar()


def _demo() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="demo-2pc-"))
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
                log_duravel=LogDuravelArquivo(tmp / f"part-{pid}.log"),
                cliente=ClienteRede(),
                deadline=2.0,
                voto_desejado=VOTO_SIM,
            )
            servicos_part[pid] = servico
            s = _ServidorLocal(criar_app_part(servico), portas[pid])
            s.__enter__()
            servidores.append(s)

        cliente_crash = _ClienteSuprimeDecisao(ClienteRede())
        servico_coord = ServicoCoordenador(
            coord_no,
            part_nos,
            log_duravel=LogDuravelArquivo(tmp / "coord.log"),
            cliente=cliente_crash,
            deadline_voto=2.0,
            deadline_decisao=1.0,
        )
        s = _ServidorLocal(criar_app_coord(servico_coord), portas[COORD_ID])
        s.__enter__()
        servidores.append(s)

        print("== Transação com crash do Coordenador antes de difundir a Decisao ==")
        resultado = servico_coord.iniciar("tx-demo")
        print(f"  Coordenador decidiu: {resultado['decisao']} (gravado no Log_Duravel)")
        print(f"  DECISAO suprimida para {cliente_crash.suprimidas} Participantes (crash).\n")

        print("== Estado de cada Participante ==")
        todos_em_duvida = True
        for pid in PART_IDS:
            est = servicos_part[pid].estado
            em_duvida = est.em_duvida if est else False
            todos_em_duvida = todos_em_duvida and em_duvida
            print(
                f"  participante-{pid}: voto = {est.voto if est else None}, "
                f"decisao = {est.decisao if est else None}, em_duvida = {em_duvida}"
            )

        if todos_em_duvida:
            print("\nBloqueio do 2PC OK: todos os Participantes ficaram em Estado_Em_Duvida.")
            return 0
        print("\nFalhou: algum Participante não ficou em Estado_Em_Duvida.")
        return 1
    finally:
        for s in reversed(servidores):
            s.__exit__(None, None, None)
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    raise SystemExit(_demo())


if __name__ == "__main__":
    main()
