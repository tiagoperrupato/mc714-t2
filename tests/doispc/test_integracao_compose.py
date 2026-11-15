"""Seção 2PC do `compose.yaml`, validada sem subir contêiner.

`test_compose_config_valido` roda sempre que o binário `docker compose` existir e
pula sem ele. Confere que o `compose.yaml` é bem formado e que os serviços do 2PC
(`coordenador`, `participante-1..3`), seus volumes por Participante e a rede
dedicada estão presentes. O COMMIT e o ABORT de ponta a ponta sobre o compose
são os cenários `2pc commit` e `2pc abort`, testados em
`tests/experimentos/test_cenario_falha_e2e.py`.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from src.experimentos.conteineres import COMPOSE, RAIZ
from tests.apoio import compose_cli_disponivel

SERVICOS_2PC = ("coordenador", "participante-1", "participante-2", "participante-3")
VOLUMES_2PC = (
    "log-coordenador",
    "log-participante-1",
    "log-participante-2",
    "log-participante-3",
)


pularsem_cli = pytest.mark.skipif(
    not compose_cli_disponivel(),
    reason="Docker Compose CLI indisponível; teste de compose pulado graciosamente",
)


@pularsem_cli
def test_compose_config_valido() -> None:
    """`docker compose config` resolve e a seção 2PC está completa."""
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(RAIZ),
    )
    assert r.returncode == 0, f"compose config falhou: {r.stderr}"
    cfg = json.loads(r.stdout)

    servicos = cfg.get("services", {})
    for nome in SERVICOS_2PC:
        assert nome in servicos, f"serviço {nome} ausente do compose"

    # Cada Participante tem o seu próprio volume nomeado para o Log_Duravel.
    volumes = cfg.get("volumes", {})
    for vol in VOLUMES_2PC:
        assert vol in volumes, f"volume {vol} ausente"

    # Os Participantes montam um volume distinto cada um.
    montados = []
    for nome in ("participante-1", "participante-2", "participante-3"):
        vols = servicos[nome].get("volumes", [])
        fontes = [v.get("source") for v in vols]
        assert any(f and f.startswith("log-participante-") for f in fontes), (
            f"{nome} não monta volume de Log_Duravel próprio"
        )
        montados.extend(f for f in fontes if f and f.startswith("log-participante-"))
    assert len(set(montados)) == 3, "Participantes devem ter volumes distintos"

    # A seção do Anel não foi removida: continua coexistindo (coordenação T4/T6).
    assert any(s.startswith("no-anel-") for s in servicos), "seção do Anel sumiu"

    # A rede dedicada do 2PC existe e não substitui a do Anel.
    redes = cfg.get("networks", {})
    assert "rede-2pc" in redes and "rede-anel" in redes
