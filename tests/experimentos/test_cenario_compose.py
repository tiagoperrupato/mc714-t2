"""Integração do `scripts/cenario.py` sobre o Docker Compose (tarefa 9).

Segue o padrão de `tests/anel/test_compose.py`: a orquestração por contêiner é
PULADA de forma limpa quando o Docker não está disponível, em vez de falhar — a
demonstração por compose não é um requisito de todo ambiente de CI. A
orquestração de experimentos em si (planejamento, reprodução por Semente, esquema
do CSV) é coberta sem Docker em `test_bateria.py` e `test_cli_bateria.py`.

Quando o Docker existe, o teste sobe o cenário normal do Anel, conduz a eleição e
confere que o registro coletado em `results/` reporta a convergência para o maior
id, derrubando o compose ao final.
"""

from __future__ import annotations

import importlib.util
import json
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


def _carregar_cli():
    caminho = _RAIZ / "scripts" / "cenario.py"
    spec = importlib.util.spec_from_file_location("cenario_cli", caminho)
    modulo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modulo)
    return modulo


def test_cenario_sem_docker_retorna_codigo_2(tmp_path: Path, monkeypatch) -> None:
    """Sem Docker, o cenário falha de forma limpa (código 2), não trava.

    Forçamos a indisponibilidade de Docker via monkeypatch, para que este teste
    rode em qualquer ambiente e cubra o caminho de degradação explicitamente.
    """
    cli = _carregar_cli()
    monkeypatch.setattr(cli, "_docker_disponivel", lambda: False)
    import io

    codigo = cli.executar_cenario(
        "anel", "normal", tmp_path, saida=io.StringIO(), erro=io.StringIO()
    )
    assert codigo == 2


@pytest.mark.slow
@pytest.mark.skipif(
    not _docker_disponivel(),
    reason="Docker (daemon + compose v2) indisponível; cenário por contêiner pulado",
)
def test_cenario_anel_normal_sobre_compose(tmp_path: Path) -> None:
    cli = _carregar_cli()
    codigo = cli.executar_cenario("anel", "normal", tmp_path)
    assert codigo == 0
    registro = json.loads(
        (tmp_path / "cenario_anel_normal.json").read_text(encoding="utf-8")
    )
    assert registro["desfecho"]["ok"] is True
    assert registro["lideres_iniciais"] == {str(i): 5 for i in range(1, 6)}
    assert "ambiente" in registro
