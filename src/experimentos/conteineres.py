"""Docker Compose e sondagem HTTP dos nós em contêiner, para os cenários e a bateria do 2PC.

As chamadas HTTP usam urllib, para que nada aqui dependa do transporte dos nós. Os tempos medidos
por sondagem têm a resolução de `SONDAGEM_S`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "compose.yaml"
COMPOSE_TOXIPROXY = RAIZ / "compose.toxiproxy.yaml"
COMPOSE_INTEGRACAO = RAIZ / "compose.integracao.yaml"

SERVICOS_ANEL = [f"no-anel-{i}" for i in range(1, 6)]
PORTAS_ANEL = {i: 8000 + i for i in range(1, 6)}  # 8001..8005

SERVICOS_2PC = ["participante-1", "participante-2", "participante-3", "coordenador"]
PORTA_COORD = 8000
PORTAS_PART = {11: 8011, 12: 8012, 13: 8013}
BASE_COORD = f"http://127.0.0.1:{PORTA_COORD}"

SERVICOS_INTEGRACAO = [f"{papel}-{i}" for papel in ("participante", "candidato") for i in (1, 2, 3)]
PORTAS_CANDIDATO = {1: 8021, 2: 8022, 3: 8023}
PORTAS_PART_INTEGRACAO = {11: 8031, 12: 8032, 13: 8033}

# Imagem que o compose constrói, usada para ler o Python de dentro dela.
IMAGEM = "mc714-t2:latest"

SONDAGEM_S = 0.01


def docker_disponivel() -> bool:
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


def compose(*args: str, arquivos: list[Path] | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """Roda `docker compose -f ... <args>` a partir da raiz do T2, capturando a saída."""
    cmd = ["docker", "compose"]
    for arq in arquivos or [COMPOSE]:
        cmd += ["-f", str(arq)]
    cmd += list(args)
    return subprocess.run(cmd, cwd=str(RAIZ), capture_output=True, text=True, timeout=timeout)


def http_json(
    url: str, metodo: str = "GET", corpo: dict[str, Any] | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    """GET ou POST com corpo JSON, devolvendo a resposta desserializada."""
    dados = json.dumps(corpo or {}).encode("utf-8") if metodo == "POST" else None
    cabecalhos = {"Content-Type": "application/json"} if metodo == "POST" else {}
    req = urllib.request.Request(url, data=dados, headers=cabecalhos, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host local
        texto = resp.read().decode("utf-8")
    return json.loads(texto) if texto else {}


def override_2pc(modo: str, semente: int) -> str:
    """YAML de override do 2PC: votos, crash do coordenador e semente em todos os nós.

    Em `abort` o participante 3 vota NAO. Em `crash-coord` e `crash-coord-part` todos votam SIM e
    o processo do coordenador morre logo depois de gravar a primeira Decisao.
    """
    voto_p3 = "NAO" if modo == "abort" else "SIM"
    cair = "true" if modo in ("crash-coord", "crash-coord-part") else "false"
    sem = str(semente)
    return f"""\
services:
  coordenador:
    environment:
      SEMENTE: "{sem}"
      DOISPC_CAIR_APOS_DECIDIR: "{cair}"
  participante-1:
    environment:
      SEMENTE: "{sem}"
      DOISPC_VOTO: "SIM"
  participante-2:
    environment:
      SEMENTE: "{sem}"
      DOISPC_VOTO: "SIM"
  participante-3:
    environment:
      SEMENTE: "{sem}"
      DOISPC_VOTO: "{voto_p3}"
"""


def override_integracao(modo: str, semente: int) -> str:
    """YAML de override da integração: semente nos seis serviços e, no failover, a queda do 3.

    No `failover` o processo do candidato 3 morre logo depois de gravar a primeira Decisao.
    """
    linhas = ["services:"]
    for servico in SERVICOS_INTEGRACAO:
        linhas += [f"  {servico}:", "    environment:", f'      SEMENTE: "{semente}"']
        if modo == "failover" and servico == "candidato-3":
            linhas.append('      DOISPC_CAIR_APOS_DECIDIR: "true"')
    return "\n".join(linhas) + "\n"


def url_estado(base: str, transacao: str | None = None) -> str:
    """URL do `/estado` de `base`, com a visão de `transacao` quando dada."""
    if transacao is None:
        return f"{base}/estado"
    return f"{base}/estado?{urllib.parse.urlencode({'transacao': transacao})}"


def esperar_saude(base: str, timeout: float) -> bool:
    """Espera `/saude` responder ok, até `timeout` segundos."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        try:
            if http_json(f"{base}/saude").get("ok"):
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(SONDAGEM_S)
    return False


def esperar_lider(bases: dict[int, str], ids, esperado: int, timeout: float) -> dict[int, Any]:
    """Espera os Nós `ids` reportarem `esperado` como Líder e devolve o que cada um reporta."""
    limite = time.monotonic() + timeout
    lideres: dict[int, Any] = {}
    while time.monotonic() < limite:
        try:
            lideres = {i: http_json(f"{bases[i]}/estado")["lider"] for i in ids}
        except (urllib.error.URLError, OSError):
            time.sleep(SONDAGEM_S)
            continue
        if all(v == esperado for v in lideres.values()):
            break
        time.sleep(SONDAGEM_S)
    return lideres


def eleger(bases: dict[int, str], ids: list[int], esperado: int) -> tuple[dict[int, Any], float]:
    """Dispara uma eleição no Nó 1 e espera os `ids` reportarem `esperado`.

    Devolve o Líder que cada um reporta e o tempo até todos reportarem `esperado`,
    ou até o prazo de 30 s.
    """
    inicio = time.monotonic()
    http_json(f"{bases[1]}/iniciar-eleicao", metodo="POST")
    lideres = esperar_lider(bases, ids, esperado, 30.0)
    return lideres, time.monotonic() - inicio


def estados_participantes(
    *,
    esperar_decisao: bool,
    timeout: float = 10.0,
    portas: dict[int, int] = PORTAS_PART,
    transacao: str | None = None,
) -> dict[int, Any]:
    """O `/estado` dos participantes de `portas`, esperando até todos eles decidirem se pedido.

    Sem `transacao`, cada um mostra a última que viu.
    """
    limite = time.monotonic() + timeout
    while True:
        estados: dict[int, Any] = {}
        for pid, porta in portas.items():
            try:
                estados[pid] = http_json(url_estado(f"http://127.0.0.1:{porta}", transacao))
            except (urllib.error.URLError, OSError):
                estados[pid] = {}
        decidiram = all(est.get("decisao") for est in estados.values())
        if not esperar_decisao or decidiram or time.monotonic() >= limite:
            return estados
        time.sleep(SONDAGEM_S)


def decisoes(estados: dict[int, Any]) -> dict[int, Any]:
    """A Decisao de cada participante, ou None se ele não decidiu."""
    return {pid: est.get("decisao") for pid, est in estados.items()}


def esperar_conclusao(
    transacao: str | None = None, prazo: float = 10.0, *, base: str = BASE_COORD
) -> dict[str, Any]:
    """Espera o coordenador em `base` receber os ACK de `transacao` e devolve o `/estado` dela.

    Sem `transacao`, vale a última iniciada. Ao fim do prazo devolve o último estado lido, e quem
    chama confere `concluido`.
    """
    limite = time.monotonic() + prazo
    while True:
        try:
            estado = http_json(url_estado(base, transacao))
        except (urllib.error.URLError, OSError):
            estado = {}
        if estado.get("concluido") or time.monotonic() >= limite:
            return estado
        time.sleep(SONDAGEM_S)


def _exigir(resultado: subprocess.CompletedProcess, comando: str) -> None:
    """Levanta RuntimeError com a saída do Compose quando `comando` falhou."""
    if resultado.returncode != 0:
        raise RuntimeError(
            f"docker compose {comando} falhou: {resultado.stderr.strip()}. "
            "Derrube a pilha com docker compose down -v --remove-orphans e repita."
        )


def derrubar_servico(servico: str, arquivos: list[Path]) -> None:
    """Mata o processo do contêiner de `servico` com SIGKILL, como um crash."""
    _exigir(compose("kill", servico, arquivos=arquivos, timeout=60), f"kill {servico}")


def reiniciar_servico(servico: str, arquivos: list[Path]) -> float:
    """Dá `start` no contêiner de `servico` que caiu e devolve o instante do `start` em `time.monotonic`.

    Logo depois do crash o Docker ainda pode ver o contêiner rodando, e um `start` nessa janela não
    faz nada. Por isso o `wait` vem antes, e volta quando ele parou.
    """
    compose("wait", servico, arquivos=arquivos, timeout=60)
    inicio = time.monotonic()
    _exigir(compose("start", servico, arquivos=arquivos, timeout=120), f"start {servico}")
    return inicio


def esperar_saudavel(servico: str, arquivos: list[Path]) -> None:
    """Espera o healthcheck do Docker aprovar `servico`, que já está rodando.

    O `start` de um serviço confere o healthcheck de cada serviço do `depends_on` dele, e um contêiner
    recém-reiniciado fica em `starting` até a primeira sondagem. Com `--no-recreate`, o `up --wait`
    só espera, e o contêiner não perde o processo que o cenário reiniciou.
    """
    args = ("up", "-d", "--wait", "--no-deps", "--no-recreate", servico)
    _exigir(compose(*args, arquivos=arquivos, timeout=120), f"up --wait {servico}")


def reiniciar_coordenador(
    arquivos: list[Path],
    prazo: float = 30.0,
    *,
    servico: str = "coordenador",
    portas: dict[int, int] = PORTAS_PART,
    transacao: str | None = None,
) -> tuple[float, dict[int, Any]]:
    """Reinicia o coordenador que caiu e mede do `start` até os participantes decidirem.

    Sem `transacao`, vale a última que cada participante viu.
    """
    inicio = reiniciar_servico(servico, arquivos)
    estados = estados_participantes(
        esperar_decisao=True, timeout=prazo, portas=portas, transacao=transacao
    )
    return time.monotonic() - inicio, decisoes(estados)


def copiar_de_container(servico: str, origem: str, destino: Path, arquivos: list[Path]) -> None:
    """Copia `origem` do contêiner para `destino` no host, ou levanta `RuntimeError`."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    r = compose("cp", f"{servico}:{origem}", str(destino), arquivos=arquivos, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(
            f"docker compose cp {servico}:{origem} falhou: {r.stderr.strip()}. "
            "Derrube a pilha com docker compose down -v --remove-orphans e repita."
        )


def coletar_logs(algoritmo: str, servicos: list[str], destino: Path, arquivos: list[Path]) -> None:
    """Copia o log estruturado e o Log_Duravel de cada nó para `destino/<servico>`.

    No anel o log estruturado fica em `/app/results`. No 2PC e na integração o volume `/dados`
    guarda o Log_Duravel e, em `/dados/logs`, o log estruturado, e uma cópia de `/dados` traz cada
    arquivo uma vez.
    """
    destino.mkdir(parents=True, exist_ok=True)
    origem = "/app/results/." if algoritmo == "anel" else "/dados/."
    for servico in servicos:
        copiar_de_container(servico, origem, destino / servico, arquivos)


@lru_cache(maxsize=1)
def versoes_docker() -> dict[str, str]:
    """Versões do Docker, do Compose e do Python da imagem, como colunas `amb_*`.

    Chamar depois de construir a imagem. Uma leitura que falha vira `desconhecido`.
    """

    def ler(cmd: list[str]) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return "desconhecido"
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "desconhecido"

    return {
        "amb_docker_versao": ler(["docker", "version", "--format", "{{.Server.Version}}"]),
        "amb_compose_versao": ler(["docker", "compose", "version", "--short"]),
        "amb_imagem_python": ler(
            ["docker", "run", "--rm", IMAGEM, "python", "-c",
             "import platform; print(platform.python_version())"]
        ),
    }
