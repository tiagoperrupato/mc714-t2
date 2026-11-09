"""Integração das falhas de rede com Toxiproxy sobre o compose.

Dois níveis, no mesmo espírito tolerante dos demais testes de compose do T2:

- `test_compose_config_com_toxiproxy_valido`: sempre que o binário
  `docker compose` existir, valida que `compose.yaml` + `compose.toxiproxy.yaml`
  resolvem, que o serviço `toxiproxy` está presente nas duas redes e com a
  imagem PINADA (sem `:latest`, para a reprodutibilidade), e que o
  override reescreve o endereçamento dos Nós para as portas de escuta do
  Toxiproxy (os Nós "passam a falar pelos proxies"). Não sobe contêiner.

- `test_particao_do_lider_dispara_reeleicao`: quando o daemon do Docker está
  acessível, sobe o Anel com roteamento pelo Toxiproxy, semeia os proxies,
  elege o Líder 5, DESABILITA o proxy do Nó 5 (partição) e dispara nova
  eleição, confirmando que os Nós vivos reelegem o maior id vivo, 4.
  Marcado como lento; derruba o compose ao final.

Quando o Docker não está disponível, os testes PULAM de forma limpa.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "compose.yaml"
OVERRIDE = RAIZ / "compose.toxiproxy.yaml"
SERVICOS_ANEL = [f"no-anel-{i}" for i in range(1, 6)]
PORTAS = {i: 8000 + i for i in range(1, 6)}


def _tem_compose_cli() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _daemon_acessivel() -> bool:
    if not _tem_compose_cli():
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pularsem_cli = pytest.mark.skipif(
    not _tem_compose_cli(),
    reason="Docker Compose CLI indisponível; teste de compose pulado graciosamente",
)
pularsem_daemon = pytest.mark.skipif(
    not _daemon_acessivel(),
    reason="daemon do Docker inacessível; integração Toxiproxy pulada",
)


def _compose(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(COMPOSE), "-f", str(OVERRIDE), *args]
    return subprocess.run(cmd, cwd=str(RAIZ), capture_output=True, text=True, timeout=timeout)


def _http(url: str, metodo: str = "GET", timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host local controlado
        corpo = resp.read().decode("utf-8")
    return json.loads(corpo) if corpo else {}


@pularsem_cli
def test_compose_config_com_toxiproxy_valido() -> None:
    """`docker compose config` resolve com o override e roteia pelos proxies."""
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "-f", str(OVERRIDE), "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(RAIZ),
    )
    assert r.returncode == 0, f"compose config falhou: {r.stderr}"
    cfg = json.loads(r.stdout)
    servicos = cfg["services"]

    # O serviço Toxiproxy existe, com imagem PINADA (sem :latest).
    assert "toxiproxy" in servicos, "serviço toxiproxy ausente do compose"
    imagem = servicos["toxiproxy"]["image"]
    assert ":" in imagem and not imagem.endswith(":latest"), f"imagem não pinada: {imagem}"

    # Ligado às DUAS redes, para rotear Anel e 2PC.
    redes_tp = set(servicos["toxiproxy"]["networks"].keys())
    assert {"rede-anel", "rede-2pc"} <= redes_tp, f"toxiproxy não liga as duas redes: {redes_tp}"

    # As seções do Anel e do 2PC continuam presentes (não removidas).
    assert any(s.startswith("no-anel-") for s in servicos)
    assert "coordenador" in servicos

    # O override fez os Nós do Anel falarem pelo Toxiproxy.
    env_anel1 = servicos["no-anel-1"]["environment"]
    assert "toxiproxy:16001" in env_anel1["URLS"], env_anel1["URLS"]

    # E o 2PC: Coordenador aponta aos proxies dos Participantes; estes ao 17000.
    env_coord = servicos["coordenador"]["environment"]
    assert "toxiproxy:17011" in env_coord["DOISPC_PARTICIPANTES"], env_coord["DOISPC_PARTICIPANTES"]
    env_p1 = servicos["participante-1"]["environment"]
    assert env_p1["COORD_HOST"] == "toxiproxy" and env_p1["COORD_PORTA"] == "17000"


def _esperar_lideres(alvo_ids, valor, timeout: float) -> dict:
    base = {i: f"http://127.0.0.1:{PORTAS[i]}" for i in range(1, 6)}
    limite = time.monotonic() + timeout
    lideres: dict = {}
    while time.monotonic() < limite:
        lideres = {i: _http(f"{base[i]}/estado")["lider"] for i in alvo_ids}
        if all(v == valor for v in lideres.values()):
            break
        time.sleep(0.5)
    return lideres


@pularsem_daemon
@pytest.mark.slow
def test_particao_do_lider_dispara_reeleicao() -> None:
    """Partição do Líder do Anel via Toxiproxy leva à reeleição."""
    from src.comum.toxiproxy import ClienteToxiproxy, proxies_anel

    base = {i: f"http://127.0.0.1:{PORTAS[i]}" for i in range(1, 6)}

    _compose("down", "-v", "--remove-orphans", timeout=180)
    up = _compose("up", "-d", "--build", "--wait", "toxiproxy", *SERVICOS_ANEL)
    if up.returncode != 0:
        _compose("down", "-v", "--remove-orphans", timeout=180)
        pytest.fail(f"falha ao subir o Anel+Toxiproxy:\n{up.stdout}\n{up.stderr}")

    try:
        tox = ClienteToxiproxy()
        tox.esperar_pronto(deadline=30.0)
        tox.semear(proxies_anel((1, 2, 3, 4, 5)))

        # 1ª eleição com rede saudável converge para 5.
        _http(f"{base[1]}/iniciar-eleicao", metodo="POST")
        lideres = _esperar_lideres(range(1, 6), 5, 30.0)
        assert set(lideres.values()) == {5}, f"não convergiu para 5: {lideres}"

        # Partição do Líder: desabilita o proxy do Nó 5.
        tox.definir_habilitado("anel-5", False)

        # Nova eleição: os Nós vivos (1..4) reelegem o maior id vivo, 4.
        _http(f"{base[1]}/iniciar-eleicao", metodo="POST")
        vivos = [1, 2, 3, 4]
        lideres = _esperar_lideres(vivos, 4, 40.0)
        assert set(lideres.values()) == {4}, f"reeleição não convergiu para 4: {lideres}"
    finally:
        _compose("down", "-v", "--remove-orphans", timeout=180)
