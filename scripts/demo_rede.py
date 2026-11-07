"""Demo do Transporte: dois apps triviais trocando Mensagens e um timeout no Deadline.

Sobe dois Nós FastAPI locais em portas efêmeras e usa o `ClienteRede` com
Deadline obrigatório para:

1. enviar uma Mensagem ao Nó rápido e receber um ACK (envio bem-sucedido);
2. enviar uma Mensagem ao Nó lento, que dorme além do Deadline, e observar o
   erro tipado de timeout disparando exatamente no Deadline configurado.

Não sobe contêiner nem depende do Docker: é a menor demonstração possível de
que o Deadline é honrado de ponta a ponta sobre HTTP real.

Uso:
    python -m scripts.demo_rede
    python -m scripts.demo_rede --deadline 0.2 --atraso-lento 1.5
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from contextlib import closing

import uvicorn

from src.comum.modelos import Mensagem
from src.comum.rede import ClienteRede, EnvioOk, EnvioTimeout, criar_app_recebedor


def _porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _NoLocal:
    """Um Nó trivial: app FastAPI num uvicorn em thread, com atraso opcional."""

    def __init__(self, nome: str, no_id: int, *, atraso: float = 0.0) -> None:
        self.nome = nome
        self.no_id = no_id
        self.porta = _porta_livre()

        def ao_receber(mensagem: Mensagem):
            if atraso > 0:
                time.sleep(atraso)
            print(f"  [{nome}] recebeu {mensagem.tipo} de {mensagem.origem}")
            return {"ack": True, "por": no_id, "tipo": mensagem.tipo}

        config = uvicorn.Config(
            criar_app_recebedor(ao_receber),
            host="127.0.0.1",
            port=self.porta,
            log_level="error",
        )
        self._servidor = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._servidor.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def __enter__(self) -> "_NoLocal":
        self._thread.start()
        limite = time.monotonic() + 10.0
        while time.monotonic() < limite and not self._servidor.started:
            time.sleep(0.02)
        if not self._servidor.started:
            raise RuntimeError(f"Nó {self.nome} não ficou pronto")
        return self

    def __exit__(self, *_exc: object) -> None:
        self._servidor.should_exit = True
        self._thread.join(timeout=10.0)


def _demo(deadline: float, atraso_lento: float) -> None:
    print(f"Deadline configurado: {deadline:g}s\n")
    with _NoLocal("rapido", no_id=1) as rapido, _NoLocal(
        "lento", no_id=2, atraso=atraso_lento
    ) as lento, ClienteRede(retries=2) as cliente:
        print("== Envio bem-sucedido (Nó rápido) ==")
        inicio = time.monotonic()
        r1 = cliente.enviar(rapido.base_url, Mensagem("ping", 9, 1), deadline)
        dt1 = time.monotonic() - inicio
        if isinstance(r1, EnvioOk):
            print(f"  OK em {dt1*1000:.0f} ms, resposta={dict(r1.corpo)}\n")
        else:
            print(f"  inesperado: {r1!r}\n")

        print(f"== Timeout no Deadline (Nó lento dorme {atraso_lento:g}s) ==")
        inicio = time.monotonic()
        r2 = cliente.enviar(lento.base_url, Mensagem("ping", 9, 2), deadline)
        dt2 = time.monotonic() - inicio
        if isinstance(r2, EnvioTimeout):
            print(
                f"  timeout tipado em {dt2*1000:.0f} ms "
                f"(deadline={r2.deadline:g}s, tentativas={r2.tentativas}); "
                f"o cliente não esperou os {atraso_lento:g}s do destino"
            )
        else:
            print(f"  inesperado: {r2!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=0.3, help="Deadline em segundos")
    parser.add_argument(
        "--atraso-lento",
        type=float,
        default=1.5,
        help="Quanto o Nó lento dorme antes de responder, em segundos",
    )
    args = parser.parse_args()
    _demo(args.deadline, args.atraso_lento)


if __name__ == "__main__":
    main()
