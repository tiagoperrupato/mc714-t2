"""Integração do 2PC sobre o Docker Compose.

Estes testes exercitam a seção 2PC do `compose.yaml`. Como o ambiente de
execução pode não ter Docker (ou pode não ter o daemon rodando, ou não ter
permissão), cada teste PULA de forma graciosa quando o Docker não está
disponível, em vez de falhar. Assim a suíte roda em qualquer máquina com pytest;
num ambiente com Docker, os testes sobem de fato o compose e validam os
cenários de ponta a ponta.

Dois níveis:

- `test_compose_config_valido`: sempre que o binário `docker compose` existir,
  valida que o `compose.yaml` é bem formado e que os serviços do 2PC
  (`coordenador`, `participante-1..3`), seus volumes por Participante e a rede
  dedicada estão presentes. Não sobe contêiner, então é
  barato e seguro.

- `test_compose_commit_e_abort_ponta_a_ponta`: quando o daemon do Docker está
  acessível, sobe o 2PC, dispara uma transação com todos votando SIM (COMMIT em
  todos) e outra forçando um NAO (ABORT em todos),
  consultando `/estado` de cada Participante. É marcado como lento e derruba o
  compose ao final.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.espera import esperar_ate

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "compose.yaml"

SERVICOS_2PC = ("coordenador", "participante-1", "participante-2", "participante-3")
VOLUMES_2PC = (
    "log-coordenador",
    "log-participante-1",
    "log-participante-2",
    "log-participante-3",
)


def _tem_compose_cli() -> bool:
    """Verdadeiro se o binário `docker` com subcomando `compose` existe."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=15,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _daemon_acessivel() -> bool:
    """Verdadeiro se o daemon do Docker responde a `docker info`."""
    if not _tem_compose_cli():
        return False
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pularsem_cli = pytest.mark.skipif(
    not _tem_compose_cli(),
    reason="Docker Compose CLI indisponível; teste de compose pulado graciosamente",
)
pularsem_daemon = pytest.mark.skipif(
    not _daemon_acessivel(),
    reason="daemon do Docker inacessível; teste de compose ponta a ponta pulado",
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


# Override de verificação: sobe os serviços do 2PC sem publicar portas no host,
# de modo que o teste não dependa de portas livres no host (que podem estar
# ocupadas por proxies órfãos de execuções anteriores). A verificação é feita
# por dentro da rede do compose, com `exec` no Coordenador. O override é escrito
# num arquivo temporário por teste e removido ao final.
_OVERRIDE_SEM_PORTAS = """\
services:
  coordenador:
    ports: !reset []
  participante-1:
    ports: !reset []
  participante-2:
    ports: !reset []
  participante-3:
    ports: !reset []
"""

# Override que faz o Participante 3 votar SIM, para o cenário de COMMIT em todos.
_OVERRIDE_COMMIT = """\
services:
  participante-3:
    environment:
      DOISPC_VOTO: "SIM"
"""


def _compose(arquivos: list[Path], *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose"]
    for arq in arquivos:
        cmd += ["-f", str(arq)]
    cmd += list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(RAIZ)
    )


def _exec_json(arquivos: list[Path], servico: str, script: str) -> dict:
    """Roda um snippet Python dentro de `servico` e parseia a última linha como JSON."""
    r = _compose(
        arquivos, "exec", "-T", servico, "python", "-c", script, timeout=30
    )
    assert r.returncode == 0, f"exec em {servico} falhou: {r.stderr}"
    linha = [l for l in r.stdout.splitlines() if l.strip()][-1]
    return json.loads(linha)


@pularsem_daemon
@pytest.mark.slow
def test_compose_commit_e_abort_ponta_a_ponta(tmp_path: Path) -> None:
    """Sobe o 2PC e valida ABORT (um NAO) e COMMIT (todos SIM).

    A validação é feita por dentro da rede do compose (um `exec` no Coordenador
    dispara a transação e consulta o `/estado` de cada Participante), para não
    depender de portas publicadas no host.
    """
    sem_portas = tmp_path / "sem-portas.yaml"
    sem_portas.write_text(_OVERRIDE_SEM_PORTAS, encoding="utf-8")
    commit = tmp_path / "commit.yaml"
    commit.write_text(_OVERRIDE_COMMIT, encoding="utf-8")

    servicos = [
        "coordenador", "participante-1", "participante-2", "participante-3"
    ]
    tx_script = (
        "import urllib.request;"
        "r=urllib.request.Request('http://localhost:8000/transacao',"
        "data=b'{}',headers={'Content-Type':'application/json'},method='POST');"
        "print(urllib.request.urlopen(r,timeout=15).read().decode())"
    )

    def _estado(arquivos: list[Path], host: str) -> dict:
        return _exec_json(
            arquivos,
            "coordenador",
            "import urllib.request;"
            f"print(urllib.request.urlopen('http://{host}:8000/estado',timeout=5).read().decode())",
        )

    def _esperar_decisao(arquivos: list[Path], host: str, esperada: str) -> None:
        # A DECISAO sai da fila do Coordenador depois da resposta do /transacao.
        ultimo: dict = {}

        def decidiu() -> bool:
            ultimo.update(_estado(arquivos, host))
            return ultimo.get("decisao") == esperada

        assert esperar_ate(decidiu, prazo=10.0, intervalo=0.2), f"{host}: {ultimo}"

    # ---- Cenário ABORT: Participante 3 vota NAO (padrão do compose) ----
    base = [COMPOSE, sem_portas]
    _compose(base, "down", "-v", "--remove-orphans", timeout=120)
    try:
        up = _compose(base, "up", "-d", "--wait", *servicos, timeout=600)
        assert up.returncode == 0, f"up (abort) falhou: {up.stderr}"
        resultado = _exec_json(base, "coordenador", tx_script)
        assert resultado["decisao"] == "ABORT", resultado
        for host in ("participante-1", "participante-2", "participante-3"):
            _esperar_decisao(base, host, "ABORT")
    finally:
        _compose(base, "down", "-v", "--remove-orphans", timeout=120)

    # ---- Cenário COMMIT: Participante 3 forçado a votar SIM ----
    commit_base = [COMPOSE, sem_portas, commit]
    _compose(commit_base, "down", "-v", "--remove-orphans", timeout=120)
    try:
        up = _compose(commit_base, "up", "-d", "--wait", *servicos, timeout=600)
        assert up.returncode == 0, f"up (commit) falhou: {up.stderr}"
        resultado = _exec_json(commit_base, "coordenador", tx_script)
        assert resultado["decisao"] == "COMMIT", resultado
        for host in ("participante-1", "participante-2", "participante-3"):
            _esperar_decisao(commit_base, host, "COMMIT")
    finally:
        _compose(commit_base, "down", "-v", "--remove-orphans", timeout=120)
