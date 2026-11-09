"""Demo de falha no Anel: crash do Líder via `/falha/crash` e a reeleição.

Sobe os 5 Nós do Anel como apps FastAPI locais (em portas efêmeras, sem Docker),
cada um com um controlador de falhas plugado. A demonstração:

1. dispara uma Rodada_de_Eleicao e mostra todos convergindo para o maior id (5);
2. injeta crash no Líder (5) via `POST /falha/crash` — o Nó passa a responder
   503, "deixando de responder";
3. dispara uma nova eleição e mostra os Nós vivos reelegendo o maior id vivo (4),
   com o 5 marcado como morto pela regra de salto do Anel.

Equivale, em prosa, à sequência de `curl` da demonstração sobre o compose:

    docker compose up -d no-anel-1 ... no-anel-5
    curl -X POST http://localhost:8001/iniciar-eleicao
    curl -X POST http://localhost:8005/falha/crash
    curl -X POST http://localhost:8001/iniciar-eleicao
    for p in 8001 8002 8003 8004; do curl -s http://localhost:$p/estado; done

Uso:
    python -m scripts.demo_falhas_anel
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import ExitStack, closing

import httpx
import uvicorn

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


def _lideres(urls: dict[int, str], ids) -> dict[int, int | None]:
    return {i: httpx.get(f"{urls[i]}/estado", timeout=5.0).json()["lider"] for i in ids}


def _esperar(urls, ids, alvo, timeout: float) -> dict[int, int | None]:
    limite = time.monotonic() + timeout
    lideres: dict[int, int | None] = {}
    while time.monotonic() < limite:
        lideres = _lideres(urls, ids)
        if all(v == alvo for v in lideres.values()):
            break
        time.sleep(0.1)
    return lideres


def _demo() -> int:
    portas = {i: _porta_livre() for i in ANEL5}
    urls = {i: f"http://127.0.0.1:{portas[i]}" for i in ANEL5}
    clientes = {i: ClienteRede(retries=1) for i in ANEL5}
    controladores = {i: ControladorFalhas(1000 + i) for i in ANEL5}
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
            print("== 1ª eleição (sem falha) ==")
            httpx.post(f"{urls[1]}/iniciar-eleicao", timeout=5.0)
            lideres = _esperar(urls, ANEL5, 5, 10.0)
            for i in ANEL5:
                print(f"  no-anel-{i}: lider = {lideres.get(i)}")
            if set(lideres.values()) != {5}:
                print("Falhou: não convergiu para 5.")
                return 1
            print("  -> Líder eleito: 5 (maior id).\n")

            print("== Crash do Líder (5) via /falha/crash ==")
            r = httpx.post(f"{urls[5]}/falha/crash", timeout=5.0)
            print(f"  estado da falha no Nó 5: {r.json()}")
            print("  o Nó 5 agora responde 503 — 'deixou de responder'.\n")

            print("== Nova eleição (Líder morto) ==")
            httpx.post(f"{urls[1]}/iniciar-eleicao", timeout=5.0)
            vivos = [1, 2, 3, 4]
            lideres = _esperar(urls, vivos, 4, 15.0)
            for i in vivos:
                estado = httpx.get(f"{urls[i]}/estado", timeout=5.0).json()
                print(f"  no-anel-{i}: lider = {estado['lider']}, mortos = {estado['mortos']}")
            if set(lideres.values()) == {4}:
                print("\nReeleição OK: os Nós vivos elegeram o maior id vivo (4).")
                return 0
            print("\nFalhou: os Nós vivos não convergiram para 4.")
            return 1
        finally:
            for n in nos.values():
                n.parar()
            for c in clientes.values():
                c.fechar()


def main() -> None:
    raise SystemExit(_demo())


if __name__ == "__main__":
    main()
