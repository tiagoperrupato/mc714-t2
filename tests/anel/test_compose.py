"""Integração do Anel sobre o Docker Compose (Requisitos 5.3, 5.4, 13.1–13.3).

Este teste sobe os 5 Nós do Anel definidos no `compose.yaml`, dispara uma
eleição num Nó via `curl`/HTTP e consulta `GET /estado` de todos, confirmando a
convergência para o mesmo Líder (o maior id, 5).

Requer Docker com o plugin Compose v2. Quando o ambiente não tem Docker — ou o
daemon não responde —, o teste é PULADO de forma limpa, em vez de falhar: a
verificação por contêiner não pode ser um requisito rígido de todo ambiente de
CI. A convergência sobre a rede real também é coberta, sem Docker, pelo
`test_integracao_local_eleicao_converge_para_maior_id` em `test_no.py`.

O teste usa apenas os serviços do Anel (`--profile` não é necessário porque a
seção do 2PC, quando existir, sobe em serviços próprios; aqui subimos os cinco
Nós do Anel por nome explícito para não acoplar a esta ou àquela seção).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "compose.yaml"
SERVICOS_ANEL = [f"no-anel-{i}" for i in range(1, 6)]
PORTAS = {i: 8000 + i for i in range(1, 6)}  # 8001..8005 publicadas no host


def _docker_disponivel() -> bool:
    """True se o binário docker existe e o daemon responde a `docker info`."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _compose(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        cwd=str(RAIZ),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


pytestmark = pytest.mark.skipif(
    not _docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível; integração por contêiner pulada",
)


@pytest.fixture(scope="module")
def anel_no_ar():
    """Sobe os 5 Nós do Anel e os derruba ao final, limpando volumes órfãos."""
    # Garante estado limpo antes de começar.
    _compose("down", "-v", "--remove-orphans")
    up = _compose("up", "-d", "--build", "--wait", *SERVICOS_ANEL, timeout=600)
    if up.returncode != 0:
        _compose("down", "-v", "--remove-orphans")
        pytest.fail(f"falha ao subir o Anel no compose:\n{up.stdout}\n{up.stderr}")
    try:
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


def _http_json(url: str, metodo: str = "GET", timeout: float = 5.0) -> dict:
    """GET/POST simples via urllib, para não depender de httpx no teste de compose."""
    import urllib.request

    req = urllib.request.Request(url, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host local controlado
        corpo = resp.read().decode("utf-8")
    return json.loads(corpo) if corpo else {}


def test_eleicao_sobre_compose_converge_para_maior_id(anel_no_ar) -> None:
    base = {i: f"http://127.0.0.1:{PORTAS[i]}" for i in range(1, 6)}

    # Dispara a eleição no Nó 1 (equivalente ao `curl -X POST .../iniciar-eleicao`).
    _http_json(f"{base[1]}/iniciar-eleicao", metodo="POST")

    # Aguarda a convergência consultando /estado de todos os Nós.
    deadline = time.monotonic() + 30.0
    lideres: dict[int, int | None] = {}
    while time.monotonic() < deadline:
        lideres = {i: _http_json(f"{base[i]}/estado")["lider"] for i in range(1, 6)}
        if all(v == 5 for v in lideres.values()):
            break
        time.sleep(0.5)

    assert set(lideres.values()) == {5}, f"Nós não convergiram para o Líder 5: {lideres}"
