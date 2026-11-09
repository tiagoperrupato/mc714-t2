"""Demo de falha no 2PC: crash do Coordenador, bloqueio e recuperação.

Sobe o Coordenador e três Participantes como apps FastAPI locais (sem Docker).
O Coordenador coleta os Votos (todos SIM), decide COMMIT, grava a Decisao no
Log_Duravel e cai antes de difundi-la, modelado pela supressão de toda DECISAO.
Enquanto ele está fora, os Participantes ficam em Estado_Em_Duvida, o bloqueio
do 2PC. Um Coordenador novo sobe na mesma porta, relê o mesmo
Log_Duravel e difunde a Decisao gravada, e os três decidem COMMIT.

O cenário `python scripts/cenario.py 2pc crash-coord` mostra o mesmo no compose,
com o processo derrubado de verdade.

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

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        if mensagem.tipo == MSG_DECISAO:
            return EnvioOk(status=200, corpo={"ack": True, "descartada": True})
        return self._cliente.enviar(destino_url, mensagem, deadline)

    def fechar(self) -> None:
        self._cliente.fechar()


def _todos_decidiram(servicos_part: dict[int, ServicoParticipante]) -> bool:
    return all(
        servico.estado is not None and servico.estado.decisao == DECISAO_COMMIT
        for servico in servicos_part.values()
    )


def _imprimir_participantes(servicos_part: dict[int, ServicoParticipante]) -> None:
    for pid, servico in servicos_part.items():
        est = servico.estado
        print(
            f"  participante-{pid}: voto = {est.voto if est else None}, "
            f"decisao = {est.decisao if est else None}, "
            f"em_duvida = {est.em_duvida if est else False}"
        )


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
        primeira_vida = ServicoCoordenador(
            coord_no,
            part_nos,
            log_duravel=LogDuravelArquivo(tmp / "coord.log"),
            cliente=cliente_crash,
            deadline_voto=2.0,
            deadline_decisao=1.0,
        )
        print("== Coordenador decide e cai antes de difundir a Decisao ==")
        with _ServidorLocal(criar_app_coord(primeira_vida), portas[COORD_ID]):
            resultado = primeira_vida.iniciar("tx-demo")
        print(f"  Coordenador decidiu: {resultado['decisao']} (gravado no Log_Duravel)\n")
        _imprimir_participantes(servicos_part)

        print("\n== Coordenador reinicia sobre o mesmo Log_Duravel ==")
        segunda_vida = ServicoCoordenador(
            coord_no,
            part_nos,
            log_duravel=LogDuravelArquivo(tmp / "coord.log"),
            cliente=ClienteRede(),
            deadline_voto=2.0,
            deadline_decisao=1.0,
        )
        segunda_vida.recuperar()
        with _ServidorLocal(criar_app_coord(segunda_vida), portas[COORD_ID]):
            limite = time.monotonic() + 5.0
            while time.monotonic() < limite and not _todos_decidiram(servicos_part):
                time.sleep(0.05)
        _imprimir_participantes(servicos_part)

        if _todos_decidiram(servicos_part):
            print("\nRecuperação OK: os Participantes saíram da dúvida com a Decisao gravada.")
            return 0
        print("\nFalhou: algum Participante segue sem a Decisao depois do reinício.")
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
