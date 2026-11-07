"""Demo do Anel sobre o Docker Compose: dispara a eleição e lê o Líder de cada Nó.

Supõe que os 5 Nós do Anel já estejam no ar (`docker compose up -d no-anel-1
no-anel-2 no-anel-3 no-anel-4 no-anel-5`). O script dispara uma Rodada_de_Eleicao
num Nó via HTTP de controle e consulta `GET /estado` de todos, imprimindo o
Líder que cada um reporta. Ao final, todos devem reportar o maior id (5).

Equivale, em prosa, à sequência de `curl` da demonstração:

    docker compose up -d no-anel-1 no-anel-2 no-anel-3 no-anel-4 no-anel-5
    curl -X POST http://localhost:8001/iniciar-eleicao
    for p in 8001 8002 8003 8004 8005; do curl -s http://localhost:$p/estado; done

Uso:
    python -m scripts.demo_anel
    python -m scripts.demo_anel --iniciador 3 --timeout 30
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

PORTAS = {i: 8000 + i for i in range(1, 6)}  # 8001..8005


def _http_json(url: str, metodo: str = "GET", timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host local
        corpo = resp.read().decode("utf-8")
    return json.loads(corpo) if corpo else {}


def _demo(iniciador: int, timeout: float) -> int:
    base = {i: f"http://localhost:{PORTAS[i]}" for i in PORTAS}
    print(f"Disparando eleição no Nó {iniciador} ...")
    _http_json(f"{base[iniciador]}/iniciar-eleicao", metodo="POST")

    limite = time.monotonic() + timeout
    lideres: dict[int, int | None] = {}
    while time.monotonic() < limite:
        lideres = {i: _http_json(f"{base[i]}/estado").get("lider") for i in PORTAS}
        if all(v == 5 for v in lideres.values()):
            break
        time.sleep(0.5)

    print("Líder reportado por cada Nó:")
    for i in sorted(PORTAS):
        print(f"  no-anel-{i}: lider = {lideres.get(i)}")

    if set(lideres.values()) == {5}:
        print("Convergência: todos os Nós reportam o maior id (5). OK.")
        return 0
    print("Nós ainda não convergiram para o Líder 5.")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iniciador", type=int, default=1, help="Nó que dispara a eleição (1..5)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Tempo máximo de espera, em s")
    args = parser.parse_args()
    raise SystemExit(_demo(args.iniciador, args.timeout))


if __name__ == "__main__":
    main()
