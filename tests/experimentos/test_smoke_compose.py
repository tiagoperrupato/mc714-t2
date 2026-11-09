"""Smoke test da sequência de compose documentada no README.

Valida, de ponta a ponta sobre o Docker Compose, que a sequência de
build + up --wait + um cenário mínimo + down -v --remove-orphans roda limpa a
partir de um estado zerado e NÃO deixa contêiner nem volume órfão. É a contraparte "com Docker" de `test_reprodutibilidade.py`, que
cobre a mesma sequência em processo.

O teste é marcado `slow` e PULA de forma limpa quando o Docker (daemon + compose
v2) não está disponível — a demonstração por contêiner não é requisito de todo
ambiente. Rode-o isolado:

    python -m pytest -m slow tests/experimentos/test_smoke_compose.py

Stacks de compose em paralelo podem conflitar no `--wait` e nas portas
publicadas; por isso este smoke roda UM cenário pequeno por vez.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]


def _docker_disponivel() -> bool:
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


def _carregar_cenario():
    caminho = _RAIZ / "scripts" / "cenario.py"
    spec = importlib.util.spec_from_file_location("cenario_cli_smoke", caminho)
    modulo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modulo)
    return modulo


def _containers_do_projeto() -> list[str]:
    """Nomes dos contêineres (vivos ou parados) dos serviços do compose do T2."""
    nomes = ["no-anel-1", "no-anel-2", "no-anel-3", "no-anel-4", "no-anel-5",
             "coordenador", "participante-1", "participante-2", "participante-3", "toxiproxy"]
    r = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30,
    )
    presentes = set(r.stdout.split())
    return [n for n in nomes if n in presentes]


def _volumes_do_projeto() -> list[str]:
    """Volumes nomeados do T2 ainda presentes (devem sumir com down -v)."""
    r = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True, text=True, timeout=30,
    )
    presentes = set(r.stdout.split())
    esperados = {
        "mc714-t2-log-coordenador",
        "mc714-t2-log-participante-1",
        "mc714-t2-log-participante-2",
        "mc714-t2-log-participante-3",
    }
    return sorted(esperados & presentes)


@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível; smoke de compose pulado",
)
def test_build_up_cenario_down_sem_orfaos(tmp_path: Path) -> None:
    """Build + up --wait + cenário + down -v --remove-orphans roda limpo e sem órfãos.

    `cenario.executar_cenario` já encadeia down inicial, up --build --wait,
    condução, coleta, verificação e down -v --remove-orphans final — a exata
    sequência que o README documenta. Depois dele, nenhum contêiner nem volume do
    projeto deve sobrar.
    """
    cli = _carregar_cenario()
    codigo = cli.executar_cenario("anel", "normal", tmp_path)
    assert codigo == 0, "o cenário mínimo do Anel sobre o compose deveria passar"

    # Teardown limpo: nenhum contêiner nem volume nomeado do T2 remanescente.
    assert _containers_do_projeto() == [], "contêineres órfãos após down -v --remove-orphans"
    assert _volumes_do_projeto() == [], "volumes órfãos após down -v --remove-orphans"
