"""Falhas de aplicação no Anel sobre a rede real (Requisito 8.6), sem Docker.

Sobe os 5 Nós do Anel como apps FastAPI em uvicorn locais, cada um com um
controlador de falhas plugado, e exercita a reeleição após o crash do Líder:
elege o Líder (o maior id, 5), injeta crash no Nó 5 via o controlador (ele passa
a responder 503, "deixa de responder"), e dispara uma nova Rodada_de_Eleicao.
A regra de salto do Anel trata o Nó 5 como falho e a eleição converge para o
maior id entre os Nós vivos restantes (4), cumprindo o Requisito 8.6.

Não depende de Docker: é a mesma prova do cenário de compose, mas roda em
qualquer ambiente com pytest.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import ExitStack, closing

import httpx
import pytest

from src.anel.maquina import EstadoAnel
from src.anel.no import NoAnel, criar_app
from src.comum.falhas import ClienteComFalhas, ControladorFalhas
from src.comum.rede import ClienteRede

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


def _lideres(urls: dict[int, str]) -> dict[int, int | None]:
    return {i: httpx.get(f"{urls[i]}/estado", timeout=5.0).json()["lider"] for i in urls}


def test_crash_do_lider_dispara_reeleicao_para_o_maior_vivo() -> None:
    portas = {i: _porta_livre() for i in ANEL5}
    urls = {i: f"http://127.0.0.1:{portas[i]}" for i in ANEL5}

    clientes = {i: ClienteRede(retries=1) for i in ANEL5}
    controladores = {i: ControladorFalhas(1000 + i, dormir=time.sleep) for i in ANEL5}
    nos = {
        i: NoAnel(
            EstadoAnel(no_id=i, anel=ANEL5),
            urls=urls,
            cliente=ClienteComFalhas(clientes[i], controladores[i]),
            deadline=1.5,
        )
        for i in ANEL5
    }
    servidores = [_ServidorLocal(criar_app(nos[i], controlador=controladores[i]), portas[i]) for i in ANEL5]

    with ExitStack() as pilha:
        for s in servidores:
            pilha.enter_context(s)
        try:
            # 1ª eleição: converge para o maior id (5).
            httpx.post(f"{urls[1]}/iniciar-eleicao", timeout=5.0)
            limite = time.monotonic() + 10.0
            while time.monotonic() < limite:
                if all(v == 5 for v in _lideres(urls).values()):
                    break
                time.sleep(0.05)
            assert set(_lideres(urls).values()) == {5}, _lideres(urls)

            # Crash do Líder (5): passa a responder 503 via o controlador.
            r = httpx.post(f"{urls[5]}/falha/crash", timeout=5.0)
            assert r.status_code == 200
            assert r.json()["crash"] is True

            # Nova eleição disparada por um Nó vivo (1): deve saltar o 5 morto e
            # convergir para o maior id vivo (4) entre {1,2,3,4}.
            httpx.post(f"{urls[1]}/iniciar-eleicao", timeout=5.0)
            vivos = [1, 2, 3, 4]
            limite = time.monotonic() + 15.0
            lideres_vivos: dict[int, int | None] = {}
            while time.monotonic() < limite:
                lideres_vivos = {
                    i: httpx.get(f"{urls[i]}/estado", timeout=5.0).json()["lider"]
                    for i in vivos
                }
                if all(v == 4 for v in lideres_vivos.values()):
                    break
                time.sleep(0.1)

            assert set(lideres_vivos.values()) == {4}, lideres_vivos
            # Algum Nó vivo marcou o 5 como morto ao tentar repassar a ele.
            mortos_por_no = {
                i: httpx.get(f"{urls[i]}/estado", timeout=5.0).json()["mortos"]
                for i in vivos
            }
            assert any(5 in m for m in mortos_por_no.values()), mortos_por_no
        finally:
            for n in nos.values():
                n.parar()
            for c in clientes.values():
                c.fechar()
