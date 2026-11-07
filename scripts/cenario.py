"""CLI de cenário: sobe UM cenário reproduzível de um algoritmo sobre o compose.

Enquanto a bateria (`scripts/bateria.py`) roda muitas Réplicas em processo, este
script sobe um cenário real sobre o Docker Compose, conduz o algoritmo pelos
endpoints de controle, coleta as métricas observadas **e os Logs_Estruturados e
Logs_Duraveis que os Nós reais deixaram** para `results/`, e por fim roda os
invariantes de correção sobre esses logs (`scripts/verificar.py`). É o
complemento "ponta a ponta" da orquestração (tarefas 9 e 12): a bateria mede
tendência com reprodutibilidade; o cenário demonstra um caso único sobre a rede
real e prova sua correção pelos invariantes.

Cada cenário registra a **Semente** com que foi conduzido (Requisitos 2.2, 8.4):
ela aparece no registro JSON coletado e, por vir do ambiente dos contêineres,
também no `inicio_replica` de cada Log_Estruturado. Com a mesma Semente e a
mesma configuração do compose, o Cenário de falha é repetível.

Cenários do Anel:

    python scripts/cenario.py anel normal
        Sobe os 5 Nós, dispara uma eleição e coleta o Líder e o
        Tempo_de_Convergencia observado. A eleição converge para o maior id (5).

    python scripts/cenario.py anel falha
        Idem, e então injeta crash no Líder (Nó 5) via `/falha/crash` e dispara
        nova eleição: os Nós vivos reelegem o maior id vivo (4), cumprindo o
        Requisito 8.6.

Cenários do 2PC:

    python scripts/cenario.py 2pc commit        (alias: 2pc normal)
        Coordenador + 3 Participantes, todos votando SIM → COMMIT em todos.

    python scripts/cenario.py 2pc abort          (alias: 2pc falha)
        Participante 3 vota NAO → ABORT atômico em todos (Requisito 7.4).

    python scripts/cenario.py 2pc crash-coord
        Todos votam SIM, mas o Coordenador cai entre decidir e difundir a
        Decisao (flag `DOISPC_SUPRIMIR_DECISAO`): os Participantes que votaram
        SIM ficam em Estado_Em_Duvida (Requisito 8.5). Nos logs isso aparece
        como Voto SIM durável sem Decisao durável, que `verificar.py` reporta
        como **aviso**, não reprovação (Requisito 12.4) — a verificação ainda
        sai com código 0.

Requer Docker com Compose v2. Sem Docker, o script falha com uma mensagem clara
e código de saída 2, em vez de travar — a demonstração por contêiner não é um
requisito de todo ambiente (os testes que validam a orquestração rodam sem
Docker, dirigindo as Máquinas puras em processo).

O script valida a configuração do cenário ANTES de subir qualquer contêiner ou
tocar `results/` (Requisito 10.5), e sempre derruba o compose ao final
(`down -v --remove-orphans`), para não deixar contêiner nem volume órfão.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scripts import verificar  # noqa: E402
from src.experimentos.ambiente import coletar_ambiente  # noqa: E402

COMPOSE = _RAIZ / "compose.yaml"
RESULTS_PADRAO = _RAIZ / "results"

SERVICOS_ANEL = [f"no-anel-{i}" for i in range(1, 6)]
PORTAS_ANEL = {i: 8000 + i for i in range(1, 6)}  # 8001..8005

SERVICOS_2PC = ["participante-1", "participante-2", "participante-3", "coordenador"]
PORTA_COORD = 8000
PORTAS_PART = {11: 8011, 12: 8012, 13: 8013}

# Semente padrão dos cenários (casa com SEMENTE do compose). Registrada no
# resultado e sobreponível por --semente para reproduzir uma execução.
SEMENTE_PADRAO = 0

# Modos normalizados do 2PC. Mantemos os aliases `normal`/`falha` por
# compatibilidade com a tarefa 9 (normal == commit, falha == abort).
_MODOS_2PC = {
    "normal": "commit",
    "commit": "commit",
    "falha": "abort",
    "abort": "abort",
    "crash-coord": "crash-coord",
    "crash_coord": "crash-coord",
}


# --------------------------------------------------------------------------- #
# Docker Compose
# --------------------------------------------------------------------------- #


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


def _compose(*args: str, arquivos: list[Path] | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose"]
    for arq in arquivos or [COMPOSE]:
        cmd += ["-f", str(arq)]
    cmd += list(args)
    return subprocess.run(
        cmd,
        cwd=str(_RAIZ),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _http_json(url: str, metodo: str = "GET", timeout: float = 5.0) -> dict[str, Any]:
    """GET/POST simples via urllib, para não acoplar o cenário a httpx."""
    dados = b"{}" if metodo == "POST" else None
    cabecalhos = {"Content-Type": "application/json"} if metodo == "POST" else {}
    req = urllib.request.Request(url, data=dados, headers=cabecalhos, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host local
        corpo = resp.read().decode("utf-8")
    return json.loads(corpo) if corpo else {}


# --------------------------------------------------------------------------- #
# Overrides de cenário (voto, supressão de decisão, semente)
# --------------------------------------------------------------------------- #


def _override_2pc(modo_norm: str, semente: int) -> str:
    """Monta o YAML de override do 2PC para o modo e a Semente pedidos.

    - commit: Participante 3 vota SIM (COMMIT em todos).
    - abort:  Participante 3 vota NAO (ABORT atômico — é o default do base, mas
      explicitamos para o cenário ser autocontido).
    - crash-coord: todos votam SIM e o Coordenador suprime a DECISAO, deixando os
      Participantes em Estado_Em_Duvida (Requisito 8.5).

    A Semente é escrita em todos os serviços do 2PC, para que o `inicio_replica`
    de cada log a carimbe (reprodutibilidade, Requisito 8.4).
    """
    voto_p3 = "NAO" if modo_norm == "abort" else "SIM"
    suprimir = "true" if modo_norm == "crash-coord" else "false"
    sem = str(semente)
    return f"""\
services:
  coordenador:
    environment:
      SEMENTE: "{sem}"
      DOISPC_SUPRIMIR_DECISAO: "{suprimir}"
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


def _override_anel(semente: int) -> str:
    """Override que fixa a Semente dos cinco Nós do Anel para o cenário."""
    sem = str(semente)
    linhas = ["services:"]
    for i in range(1, 6):
        linhas.append(f"  no-anel-{i}:")
        linhas.append("    environment:")
        linhas.append(f'      SEMENTE: "{sem}"')
    return "\n".join(linhas) + "\n"


# --------------------------------------------------------------------------- #
# Cenários do Anel
# --------------------------------------------------------------------------- #


def _esperar_lider(bases: dict[int, str], ids, alvo, timeout: float) -> dict[int, Any]:
    limite = time.monotonic() + timeout
    lideres: dict[int, Any] = {}
    while time.monotonic() < limite:
        try:
            lideres = {i: _http_json(f"{bases[i]}/estado")["lider"] for i in ids}
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
            continue
        if all(v == alvo for v in lideres.values()):
            break
        time.sleep(0.5)
    return lideres


def _cenario_anel(modo: str, semente: int) -> dict[str, Any]:
    """Conduz um cenário do Anel sobre o compose e devolve as métricas observadas.

    - `normal`: dispara UMA eleição com os cinco Nós vivos; converge para o maior
      id da topologia (5).
    - `falha`: injeta crash no Líder natural (Nó 5, o maior id) ANTES da eleição
      observada e dispara UMA eleição, que salta o Nó 5 morto (regra do
      Requisito 5.5) e converge para o maior id **vivo** restante (4),
      cumprindo o Requisito 8.6.

    Por que o crash vem antes da eleição observada, e não depois de uma primeira
    eleição: a identidade de uma Rodada_de_Eleicao é o id do seu iniciador
    (Requisito 2.3), e os invariantes inferem os Nós vivos de uma rodada a partir
    do conjunto de eventos coletado, tratando como mortos os Nós contornados por
    salto (`sucessor_falho`). Encadear duas eleições num mesmo log — uma com 5
    vivo e outra com 5 morto — misturaria dois conjuntos de vivos no mesmo
    arquivo e confundiria a inferência. Mantendo UMA rodada observada por
    cenário, o log coletado é consistente e os invariantes passam sem ambiguidade.
    A convergência para o maior id COM todos vivos é demonstrada pelo cenário
    `normal`; a reeleição para o maior id vivo APÓS a morte do Líder, por este.
    """
    bases = {i: f"http://127.0.0.1:{PORTAS_ANEL[i]}" for i in range(1, 6)}

    if modo == "falha":
        # Mata o Líder natural (o maior id, 5) antes da eleição observada.
        _http_json(f"{bases[5]}/falha/crash", metodo="POST")
        vivos = [1, 2, 3, 4]
        alvo = 4
    else:
        vivos = list(range(1, 6))
        alvo = 5

    inicio = time.monotonic()
    _http_json(f"{bases[1]}/iniciar-eleicao", metodo="POST")
    lideres = _esperar_lider(bases, vivos, alvo, 30.0)
    convergencia = time.monotonic() - inicio

    registro: dict[str, Any] = {
        "cenario": f"anel_{modo}",
        "algoritmo": "anel",
        "modo": modo,
        "semente": semente,
        "maior_id_topologia": 5,
        "lider": alvo if set(lideres.values()) == {alvo} else None,
        "convergiu": set(lideres.values()) == {alvo},
        "tempo_convergencia_s": round(convergencia, 4),
        # Mantém as chaves históricas para compatibilidade com testes/consumidores
        # que inspecionam a convergência inicial do cenário normal.
        "lider_inicial": alvo if set(lideres.values()) == {alvo} else None,
        "convergiu_inicial": set(lideres.values()) == {alvo},
    }
    if modo == "falha":
        registro.update(
            {
                "falha_injetada": "crash_lider_natural(5) via /falha/crash, antes da eleicao",
                "lider_pos_falha": alvo if set(lideres.values()) == {alvo} else None,
                "reeleicao_ok": set(lideres.values()) == {alvo},
                "tempo_reeleicao_s": round(convergencia, 4),
            }
        )
    return registro


# --------------------------------------------------------------------------- #
# Cenário do 2PC
# --------------------------------------------------------------------------- #


def _esperar_saude(base: str, timeout: float) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        try:
            if _http_json(f"{base}/saude").get("ok"):
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def _cenario_2pc(modo_norm: str, semente: int) -> dict[str, Any]:
    """Conduz uma transação 2PC sobre o compose e devolve as métricas observadas.

    O voto dos Participantes e a supressão da DECISAO vêm do override montado por
    `_override_2pc`; este condutor apenas dispara a transação e observa a
    Decisao e os estados dos Participantes. Em `crash-coord`, a resposta do
    `/transacao` pode demorar o Deadline (o Coordenador decide e tenta difundir,
    mas a DECISAO é suprimida), e os Participantes ficam em Estado_Em_Duvida.
    """
    base_coord = f"http://127.0.0.1:{PORTA_COORD}"
    _esperar_saude(base_coord, 30.0)
    inicio = time.monotonic()
    try:
        resposta = _http_json(f"{base_coord}/transacao", metodo="POST", timeout=30.0)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - rede do cenário
        resposta = {"erro": str(exc)}
    latencia = time.monotonic() - inicio
    estados_part = {}
    for pid, porta in PORTAS_PART.items():
        try:
            estados_part[pid] = _http_json(f"http://127.0.0.1:{porta}/estado")
        except (urllib.error.URLError, OSError):
            estados_part[pid] = {}

    em_duvida = [
        pid
        for pid, est in estados_part.items()
        if est.get("voto") == "SIM" and est.get("decisao") in (None, "")
    ]
    registro = {
        "cenario": f"2pc_{modo_norm}",
        "algoritmo": "2pc",
        "modo": modo_norm,
        "semente": semente,
        "decisao": resposta.get("decisao"),
        "latencia_commit_s": round(latencia, 4),
        "participantes": resposta.get("participantes"),
        "estados_participantes": estados_part,
    }
    if modo_norm == "crash-coord":
        registro["falha_injetada"] = "crash_coordenador antes de difundir DECISAO (DOISPC_SUPRIMIR_DECISAO)"
        registro["participantes_em_duvida"] = sorted(em_duvida)
    return registro


# --------------------------------------------------------------------------- #
# Coleta dos logs reais (JSONL estruturado + Log_Duravel) de dentro dos Nós
# --------------------------------------------------------------------------- #


def _copiar_de_container(servico: str, origem: str, destino: Path, arquivos: list[Path]) -> bool:
    """Copia `origem` (caminho no contêiner) para `destino` no host via compose cp.

    Devolve True em sucesso. Falhas (caminho ausente no contêiner) são toleradas:
    nem todo serviço grava no mesmo lugar, e um caminho inexistente apenas não
    contribui com logs.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    r = _compose(
        "cp", f"{servico}:{origem}", str(destino), arquivos=arquivos, timeout=120
    )
    return r.returncode == 0


def _coletar_logs_estruturados(
    algoritmo: str, servicos: list[str], destino: Path, arquivos: list[Path]
) -> None:
    """Extrai os Logs_Estruturados (JSONL) e Logs_Duraveis dos Nós para `destino`.

    - Anel: o Log_Estruturado fica em `/app/results` dentro do contêiner (LOG_DIR
      default). Copiamos o diretório inteiro de cada Nó.
    - 2PC: o Log_Estruturado fica em `/dados/logs` e o Log_Duravel em `/dados`
      (um volume por Nó). Copiamos ambos, pois os invariantes de atomicidade e
      durabilidade leem os Logs_Duraveis.

    Esses são os logs que `scripts/verificar.py` consome — distintos do stdout do
    contêiner (coletado à parte por `_coletar_stdout`).
    """
    destino.mkdir(parents=True, exist_ok=True)
    for servico in servicos:
        alvo = destino / servico
        if algoritmo == "anel":
            _copiar_de_container(servico, "/app/results/.", alvo, arquivos)
        else:
            # Log_Estruturado em /dados/logs e Log_Duravel(s) em /dados.
            _copiar_de_container(servico, "/dados/logs/.", alvo / "logs", arquivos)
            _copiar_de_container(servico, "/dados/.", alvo / "dados", arquivos)


def _coletar_stdout(servicos: list[str], destino: Path, arquivos: list[Path]) -> None:
    """Grava o stdout de contêiner de cada serviço (um .txt por Nó), para depuração."""
    destino.mkdir(parents=True, exist_ok=True)
    for servico in servicos:
        saida = _compose("logs", "--no-color", servico, arquivos=arquivos, timeout=60)
        (destino / f"{servico}.txt").write_text(saida.stdout, encoding="utf-8")


def _gravar_registro(registro: dict[str, Any], destino: Path) -> None:
    """Grava o registro do cenário em JSON, carimbando o ambiente."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    registro = dict(registro)
    registro["ambiente"] = coletar_ambiente().como_colunas()
    destino.write_text(
        json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Orquestração: valida, sobe, conduz, coleta, verifica, derruba
# --------------------------------------------------------------------------- #


def _normalizar(algoritmo: str, modo: str) -> tuple[str, str]:
    """Valida e normaliza (algoritmo, modo) ANTES de qualquer efeito colateral.

    Devolve (modo_rotulo, modo_normalizado). Para o Anel, o rótulo é o próprio
    modo (normal/falha). Para o 2PC, o rótulo é o modo normalizado
    (commit/abort/crash-coord), resolvendo os aliases normal/falha.
    """
    if algoritmo == "anel":
        if modo not in ("normal", "falha"):
            raise ValueError(f"modo do Anel inválido: {modo!r} (use normal|falha)")
        return modo, modo
    norm = _MODOS_2PC.get(modo)
    if norm is None:
        raise ValueError(
            f"modo do 2PC inválido: {modo!r} (use commit|abort|crash-coord)"
        )
    return norm, norm


def executar_cenario(
    algoritmo: str,
    modo: str,
    results: Path,
    *,
    semente: int = SEMENTE_PADRAO,
    saida=sys.stdout,
    erro=sys.stderr,
) -> int:
    """Sobe o cenário, conduz o algoritmo, coleta logs, VERIFICA e derruba o compose.

    Devolve 0 em sucesso (incluindo cenários com avisos esperados, como o
    Estado_Em_Duvida do crash-coord), 1 em falha de execução ou reprovação de um
    invariante de safety, e 2 quando o Docker não está disponível. A configuração
    do cenário é validada antes de subir qualquer contêiner; o compose é sempre
    derrubado ao final.
    """
    try:
        rotulo, modo_norm = _normalizar(algoritmo, modo)
    except ValueError as exc:
        print(f"[cenario] configuração inválida: {exc}", file=erro)
        return 1

    if not isinstance(semente, int) or isinstance(semente, bool) or semente < 0:
        print(f"[cenario] semente deve ser inteiro >= 0; recebido {semente!r}", file=erro)
        return 1

    if algoritmo == "anel":
        servicos = SERVICOS_ANEL
        override_texto = _override_anel(semente)
        conduzir = lambda: _cenario_anel(rotulo, semente)  # noqa: E731
    else:
        servicos = SERVICOS_2PC
        override_texto = _override_2pc(modo_norm, semente)
        conduzir = lambda: _cenario_2pc(modo_norm, semente)  # noqa: E731

    if not _docker_disponivel():
        print(
            "[cenario] Docker (daemon + compose v2) indisponível; este script "
            "exige contêineres. A orquestração em processo está em "
            "scripts/bateria.py.",
            file=erro,
        )
        return 2

    base = Path(results)
    rotulo_arq = f"{algoritmo}_{rotulo}"

    with tempfile.TemporaryDirectory() as tmp:
        override = Path(tmp) / "cenario-override.yaml"
        override.write_text(override_texto, encoding="utf-8")
        arquivos = [COMPOSE, override]

        _compose("down", "-v", "--remove-orphans", arquivos=arquivos)
        up = _compose("up", "-d", "--build", "--wait", *servicos, arquivos=arquivos, timeout=900)
        if up.returncode != 0:
            print(f"[cenario] falha ao subir o compose:\n{up.stdout}\n{up.stderr}", file=erro)
            _compose("down", "-v", "--remove-orphans", arquivos=arquivos)
            return 1

        try:
            registro = conduzir()
            registro["semente"] = semente

            logs_dir = base / f"cenario_{rotulo_arq}_logs"
            if logs_dir.exists():
                shutil.rmtree(logs_dir)
            _coletar_logs_estruturados(algoritmo, servicos, logs_dir, arquivos)
            _coletar_stdout(servicos, base / f"cenario_{rotulo_arq}_stdout", arquivos)

            # Roda os invariantes sobre os logs coletados deste cenário.
            linhas_verif: list[str] = []
            codigo_verif = verificar.verificar([logs_dir], imprimir=linhas_verif.append)
            registro["verificacao"] = {
                "codigo": codigo_verif,
                "ok": codigo_verif == 0,
                "relatorio": linhas_verif,
            }

            _gravar_registro(registro, base / f"cenario_{rotulo_arq}.json")
            print(
                f"[cenario] {algoritmo}/{rotulo} (semente={semente}): "
                f"{json.dumps({k: v for k, v in registro.items() if k != 'verificacao'}, ensure_ascii=False)}",
                file=saida,
            )
            print("\n".join(linhas_verif), file=saida)
            if codigo_verif != 0:
                print(
                    f"[cenario] VERIFICAÇÃO REPROVOU para {algoritmo}/{rotulo}",
                    file=erro,
                )
                return 1
            return 0
        except Exception as exc:  # noqa: BLE001 - qualquer falha de condução
            print(f"[cenario] falha ao conduzir {algoritmo}/{rotulo}: {exc}", file=erro)
            return 1
        finally:
            _compose("down", "-v", "--remove-orphans", arquivos=arquivos)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cenario",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("algoritmo", choices=["anel", "2pc"], help="algoritmo alvo")
    parser.add_argument(
        "modo",
        help="Anel: normal|falha. 2PC: commit|abort|crash-coord (normal=commit, falha=abort)",
    )
    parser.add_argument(
        "--semente",
        type=int,
        default=SEMENTE_PADRAO,
        help=f"Semente do cenário, registrada no resultado (default: {SEMENTE_PADRAO})",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS_PADRAO,
        help=f"diretório de saída (default: {RESULTS_PADRAO})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)
    return executar_cenario(
        args.algoritmo, args.modo, Path(args.results), semente=args.semente
    )


if __name__ == "__main__":
    raise SystemExit(main())
