"""Falhas de aplicação no Anel sobre a rede real (Requisito 8.6), sem Docker.

Sobe os 5 Nós do Anel como apps FastAPI em uvicorn locais, cada um com um
controlador de falhas plugado e um Log_Estruturado em `tmp_path`. Cobre:

- crash do Líder (5), reeleição do maior vivo (4), e o 5 de volta na eleição
  seguinte;
- pause no 5 durante a eleição: o 4 o salta, e a eleição atrasada que o 5
  repassa ao acordar não fecha a rodada de novo com outro Líder.

Os invariantes de `scripts/verificar.py` rodam sobre os logs que os Nós deixaram.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import ExitStack, closing
from pathlib import Path

import httpx

from scripts import verificar
from src.anel.maquina import EstadoAnel
from src.anel.no import NoAnel, criar_app
from src.comum.falhas import ClienteComFalhas, ControladorFalhas
from src.comum.log import abrir_log
from src.comum.rede import ClienteRede
from tests.espera import esperar_ate

ANEL5 = (1, 2, 3, 4, 5)


def _porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServidorLocal:
    def __init__(self, app, porta: int) -> None:
        import uvicorn

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


class _Anel:
    """Os cinco Nós com controlador de falhas e log em `tmp_path`, servidos localmente."""

    def __init__(self, tmp_path: Path) -> None:
        portas = {i: _porta_livre() for i in ANEL5}
        self.urls = {i: f"http://127.0.0.1:{portas[i]}" for i in ANEL5}
        self._clientes = {i: ClienteRede(retries=1) for i in ANEL5}
        self.controladores = {i: ControladorFalhas(1000 + i, dormir=time.sleep) for i in ANEL5}
        self.nos = {
            i: NoAnel(
                EstadoAnel(no_id=i, anel=ANEL5),
                urls=self.urls,
                cliente=ClienteComFalhas(self._clientes[i], self.controladores[i]),
                deadline=1.0,
                log=abrir_log(tmp_path / f"anel-no-{i}.jsonl", i),
            )
            for i in ANEL5
        }
        self._pilha = ExitStack()
        self._servidores = [
            _ServidorLocal(criar_app(self.nos[i], controlador=self.controladores[i]), portas[i])
            for i in ANEL5
        ]

    def __enter__(self) -> "_Anel":
        for s in self._servidores:
            self._pilha.enter_context(s)
        return self

    def __exit__(self, *exc: object) -> None:
        # Os workers de despacho param antes de os clientes de rede fecharem.
        for n in self.nos.values():
            n.parar()
        for c in self._clientes.values():
            c.fechar()
        self._pilha.close()

    def eleger(self, vivos: tuple[int, ...], esperado: int) -> None:
        """Dispara uma eleição no Nó 1 e espera os `vivos` reportarem o `esperado`."""
        httpx.post(f"{self.urls[1]}/iniciar-eleicao", timeout=5.0)
        assert esperar_ate(
            lambda: all(self.nos[i].estado.lider == esperado for i in vivos), prazo=15.0
        ), {i: self.nos[i].estado.lider for i in vivos}


def test_crash_do_lider_reelege_o_maior_vivo_e_o_no_volta(tmp_path: Path) -> None:
    with _Anel(tmp_path) as anel:
        anel.eleger(ANEL5, 5)

        # Crash do Líder (5): passa a responder 503 via o controlador.
        r = httpx.post(f"{anel.urls[5]}/falha/crash", timeout=5.0)
        assert r.json()["crash"] is True
        anel.eleger((1, 2, 3, 4), 4)
        assert 5 in anel.nos[4].estado.mortos

        # O 5 volta. A rodada nova zera as suspeitas, e ele vence a eleição seguinte.
        httpx.post(f"{anel.urls[5]}/falha/limpar", timeout=5.0)
        anel.eleger(ANEL5, 5)

    assert verificar.verificar([tmp_path], imprimir=lambda _linha: None) == 0


def test_pause_no_5_nao_gera_dois_lideres(tmp_path: Path) -> None:
    with _Anel(tmp_path) as anel:
        # O 5 congela ao receber a eleição. O 4 esgota o Deadline e o salta.
        anel.controladores[5].injetar_pause(2.0)
        anel.eleger((1, 2, 3, 4), 4)

        # Ao acordar, o 5 repassa a eleição atrasada ao 1, que já fechou a rodada.
        log_1 = tmp_path / "anel-no-1.jsonl"
        assert esperar_ate(lambda: '"origem": 5' in log_1.read_text(encoding="utf-8"), prazo=10.0)
        assert {anel.nos[i].estado.lider for i in (1, 2, 3, 4)} == {4}

    saida: list[str] = []
    assert verificar.verificar([tmp_path], imprimir=saida.append) == 0, "\n".join(saida)
