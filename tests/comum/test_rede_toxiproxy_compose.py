"""Integração das falhas de rede com Toxiproxy sobre o compose.

Dois níveis, no mesmo espírito tolerante dos demais testes de compose do T2:

- `test_compose_config_com_toxiproxy_valido`: sempre que o binário
  `docker compose` existir, valida que `compose.yaml` + `compose.toxiproxy.yaml`
  resolvem, que o serviço `toxiproxy` está presente nas duas redes e com a
  imagem PINADA (sem `:latest`, para a reprodutibilidade), e que o
  override reescreve o endereçamento dos Nós para as portas de escuta do
  Toxiproxy (os Nós "passam a falar pelos proxies"). Não sobe contêiner.

- `test_particao_do_lider_reelege_o_maior_vivo`: quando o daemon do Docker está
  acessível, sobe o Anel com roteamento pelo Toxiproxy, semeia os proxies,
  elege o Líder 5, DESATIVA o proxy do Nó 5, que deixa de receber Mensagens, e
  dispara nova eleição. Confere que os vivos reelegem 4 e que os invariantes
  passam nos logs dos Nós. Marcado como lento, derruba o compose ao final.

Quando o Docker não está disponível, os testes PULAM de forma limpa.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import verificar
from src.comum.toxiproxy import ClienteToxiproxy, nome_proxy_anel, proxies_anel
from src.experimentos.conteineres import (
    COMPOSE,
    COMPOSE_TOXIPROXY,
    PORTAS_ANEL,
    RAIZ,
    SERVICOS_ANEL,
    coletar_logs,
    compose,
    docker_disponivel,
    eleger,
)
from tests.apoio import compose_cli_disponivel

ARQUIVOS = [COMPOSE, COMPOSE_TOXIPROXY]

pularsem_cli = pytest.mark.skipif(
    not compose_cli_disponivel(),
    reason="Docker Compose CLI indisponível; teste de compose pulado graciosamente",
)
pularsem_daemon = pytest.mark.skipif(
    not docker_disponivel(),
    reason="daemon do Docker inacessível; integração Toxiproxy pulada",
)


@pularsem_cli
def test_compose_config_com_toxiproxy_valido() -> None:
    """`docker compose config` resolve com o override e roteia pelos proxies."""
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "-f", str(COMPOSE_TOXIPROXY), "config", "--format", "json"],
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


@pularsem_daemon
@pytest.mark.slow
def test_particao_do_lider_reelege_o_maior_vivo(tmp_path: Path) -> None:
    """Partição do Líder do Anel via Toxiproxy leva à reeleição."""
    bases = {i: f"http://127.0.0.1:{porta}" for i, porta in PORTAS_ANEL.items()}

    compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
    up = compose("up", "-d", "--build", "--wait", "toxiproxy", *SERVICOS_ANEL, arquivos=ARQUIVOS)
    if up.returncode != 0:
        compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
        pytest.fail(f"falha ao subir o Anel+Toxiproxy:\n{up.stdout}\n{up.stderr}")

    try:
        tox = ClienteToxiproxy()
        tox.esperar_pronto(deadline=30.0)
        tox.semear(proxies_anel(tuple(PORTAS_ANEL)))

        # 1ª eleição com rede saudável converge para 5.
        lideres, _ = eleger(bases, list(PORTAS_ANEL), 5)
        assert set(lideres.values()) == {5}, f"não convergiu para 5: {lideres}"

        # Partição do Líder: desabilita o proxy do Nó 5.
        tox.definir_habilitado(nome_proxy_anel(5), False)

        # Nova eleição: os Nós vivos (1..4) reelegem o maior id vivo, 4.
        lideres, _ = eleger(bases, [1, 2, 3, 4], 4)
        assert set(lideres.values()) == {4}, f"reeleição não convergiu para 4: {lideres}"

        # Na segunda rodada, o sucessor_falho do 4 tira o 5 dos vivos.
        coletar_logs("anel", SERVICOS_ANEL, tmp_path, ARQUIVOS)
        saida: list[str] = []
        assert verificar.verificar([tmp_path], imprimir=saida.append, exigidos=(1, 2, 3)) == 0, (
            "\n".join(saida)
        )
    finally:
        compose("down", "-v", "--remove-orphans", arquivos=ARQUIVOS, timeout=180)
