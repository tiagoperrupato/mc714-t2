"""Apoio comum aos testes: espera com prazo, servidor uvicorn local, Docker Compose e arquivos.

Docker, compose e HTTP dos contêineres vêm de `src/experimentos/conteineres.py`, que os cenários
também usam.
"""

from __future__ import annotations

import csv
import json
import shutil
import socket
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Callable, Iterable, Mapping


def esperar_ate(condicao: Callable[[], bool], prazo: float = 5.0, intervalo: float = 0.02) -> bool:
    """Devolve True assim que `condicao()` vale, ou False quando o prazo estoura."""
    limite = time.monotonic() + prazo
    while not condicao():
        if time.monotonic() >= limite:
            return False
        time.sleep(intervalo)
    return True


def porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServidorLocal:
    """Sobe um app FastAPI num uvicorn em thread e espera ficar pronto. Sem porta, usa uma livre."""

    def __init__(self, app, porta: int | None = None) -> None:
        import uvicorn

        self.porta = porta if porta is not None else porta_livre()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.porta, log_level="warning")
        self._servidor = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._servidor.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def __enter__(self) -> "ServidorLocal":
        self._thread.start()
        if not esperar_ate(lambda: self._servidor.started, prazo=10.0):
            raise RuntimeError(f"o servidor local na porta {self.porta} não ficou pronto em 10 s")
        return self

    def __exit__(self, *_exc: object) -> None:
        self._servidor.should_exit = True
        self._thread.join(timeout=10.0)


def compose_cli_disponivel() -> bool:
    """True se `docker compose version` responde, mesmo sem o daemon no ar."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def recursos_do_compose(arquivo: Path) -> dict[str, set[str]]:
    """Projeto, portas publicadas, contêineres, volumes e redes que `arquivo` declara."""
    saida = subprocess.run(
        ["docker", "compose", "-f", str(arquivo), "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(arquivo.parent),
    )
    assert saida.returncode == 0, f"docker compose config de {arquivo.name} falhou: {saida.stderr}"
    cfg = json.loads(saida.stdout)
    servicos = cfg["services"].values()
    return {
        "projeto": {cfg["name"]},
        "portas": {str(p["published"]) for s in servicos for p in s.get("ports", [])},
        "contêineres": {s["container_name"] for s in servicos},
        "volumes": {v["name"] for v in cfg.get("volumes", {}).values()},
        "redes": {rede["name"] for rede in cfg.get("networks", {}).values()},
    }


def escrever_jsonl(caminho: Path, registros: Iterable[Mapping]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as fluxo:
        for registro in registros:
            fluxo.write(json.dumps(registro, ensure_ascii=False) + "\n")


def ler_csv(caminho: Path) -> list[dict[str, str]]:
    with caminho.open(encoding="utf-8", newline="") as fluxo:
        return list(csv.DictReader(fluxo))
