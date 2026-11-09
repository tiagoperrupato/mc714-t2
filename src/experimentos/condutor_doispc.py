"""Bateria do 2PC nos nós reais, no compose com o Toxiproxy entre coordenador e participantes.

Cada réplica sobe a pilha do zero e aplica a latência do nível nos dois sentidos de cada proxy, então
um envio custa dois atrasos de ida. A semente entra nos ids de transação e no log de cada nó, mas o
tempo de parede não depende dela.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import urllib.error
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.comum.toxiproxy import ClienteToxiproxy, proxies_2pc
from src.experimentos import conteineres as ct
from src.experimentos.bateria import ItemReplica

PARTICIPANTES = tuple(sorted(ct.PORTAS_PART))
SERVICOS = [*ct.SERVICOS_2PC, "toxiproxy"]

# Sem falha, o último ACK chega poucos RTT depois da Decisao.
PRAZO_CONCLUSAO_S = 10.0
# Acima do Deadline de Voto do compose (3 s), para que um ABORT por timeout ainda responda.
PRAZO_TRANSACAO_S = 30.0


def preparar() -> dict[str, str]:
    """Constrói a imagem e devolve as versões do Docker como colunas de ambiente."""
    if not ct.docker_disponivel():
        raise RuntimeError(
            "Docker indisponível. A bateria do 2PC sobe o compose: inicie o daemon e "
            "confira com docker info."
        )
    construcao = ct.compose("build", timeout=900)
    if construcao.returncode != 0:
        raise RuntimeError(f"docker compose build falhou:\n{construcao.stderr}")
    return ct.versoes_docker()


@contextmanager
def _pilha(modo: str, semente: int, latencia_ms: int) -> Iterator[list[Path]]:
    """Sobe o 2PC com o Toxiproxy no caminho, aplica a latência e derruba a pilha ao sair."""
    with tempfile.TemporaryDirectory() as tmp:
        override = Path(tmp) / "override.yaml"
        override.write_text(ct.override_2pc(modo, semente), encoding="utf-8")
        arquivos = [ct.COMPOSE, ct.COMPOSE_TOXIPROXY, override]
        ct.compose("down", "-v", "--remove-orphans", arquivos=arquivos)
        try:
            subida = ct.compose("up", "-d", "--wait", *SERVICOS, arquivos=arquivos, timeout=600)
            if subida.returncode != 0:
                raise RuntimeError(f"docker compose up falhou:\n{subida.stderr}")
            with ClienteToxiproxy() as tox:
                tox.esperar_pronto()
                tox.semear(proxies_2pc(PARTICIPANTES))
                if latencia_ms > 0:
                    for proxy in proxies_2pc(PARTICIPANTES):
                        for direcao in ("upstream", "downstream"):
                            tox.latencia(proxy.nome, latencia_ms, direcao=direcao)
            if not ct.esperar_saude(ct.BASE_COORD, 30.0):
                raise RuntimeError(f"o coordenador não respondeu em {ct.BASE_COORD}/saude em 30 s")
            yield arquivos
        finally:
            ct.compose("down", "-v", "--remove-orphans", arquivos=arquivos)


def _coletar(destino: Path, arquivos: list[Path]) -> None:
    """Copia os logs dos nós para `destino`, que é recriado."""
    if destino.exists():
        shutil.rmtree(destino)
    ct.coletar_logs("2pc", ct.SERVICOS_2PC, destino, arquivos)


def contar_mensagens(diretorio: Path) -> Counter[str]:
    """Mensagens enviadas por transação, contadas nos eventos `envia` dos logs estruturados.

    Cada evento é uma mensagem do protocolo, retransmissões incluídas. O retry do transporte fica
    dentro de um mesmo evento.
    """
    contagem: Counter[str] = Counter()
    for arquivo in sorted(diretorio.rglob("*.jsonl")):
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            try:
                evento = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if evento.get("tipo") == "envia":
                contagem[evento.get("transacao")] += 1
    return contagem


def executar_replica_latencia(
    item: ItemReplica, *, transacoes: int, aquecimento: int, dir_logs: Path
) -> list[dict[str, Any]]:
    """Executa `aquecimento + transacoes` commits em sequência e devolve uma linha por transação.

    A latência é a do `POST /transacao`, que responde quando a Decisao está no Log_Duravel do
    coordenador. A transação seguinte só começa depois do último ACK, para não herdar a fila de
    saída da anterior.
    """
    latencia_ms = int(item.parametros["latencia_rede"])
    linhas: list[dict[str, Any]] = []
    with _pilha("commit", item.semente, latencia_ms) as arquivos:
        inicio = time.monotonic()
        for ordem in range(aquecimento + transacoes):
            transacao = f"lat-{item.semente}-{ordem:03d}"
            t0 = time.monotonic()
            resposta = ct.http_json(
                f"{ct.BASE_COORD}/transacao",
                metodo="POST",
                corpo={"transacao": transacao},
                timeout=PRAZO_TRANSACAO_S,
            )
            latencia = time.monotonic() - t0
            estado = ct.esperar_conclusao(transacao, PRAZO_CONCLUSAO_S)
            if not estado.get("concluido"):
                raise RuntimeError(
                    f"a transação {transacao} não recebeu todos os ACK em {PRAZO_CONCLUSAO_S} s. "
                    f"Último /estado do coordenador: {estado}"
                )
            linhas.append(
                {
                    "ordem": ordem,
                    "aquecimento": int(ordem < aquecimento),
                    "transacao": transacao,
                    "decisao": resposta.get("decisao"),
                    "latencia_commit_s": round(latencia, 6),
                }
            )
        duracao = time.monotonic() - inicio
        destino = dir_logs / f"lat-{item.semente}"
        _coletar(destino, arquivos)
    mensagens = contar_mensagens(destino)
    for linha in linhas:
        linha["mensagens"] = mensagens[linha["transacao"]]
        linha["duracao_replica_s"] = round(duracao, 3)
    return linhas


def executar_replica_recuperacao(item: ItemReplica, *, dir_logs: Path) -> dict[str, Any]:
    """Derruba o coordenador depois de gravar a Decisao, reinicia e mede até todos decidirem.

    O tempo vai do `docker compose start` até o último participante sair da dúvida, e inclui a
    partida do processo, a releitura do Log_Duravel e a DECISAO retransmitida pela rede.
    """
    latencia_ms = int(item.parametros["latencia_rede"])
    transacao = f"rec-{item.semente}"
    with _pilha("crash-coord", item.semente, latencia_ms) as arquivos:
        inicio = time.monotonic()
        try:
            ct.http_json(
                f"{ct.BASE_COORD}/transacao",
                metodo="POST",
                corpo={"transacao": transacao},
                timeout=PRAZO_TRANSACAO_S,
            )
        except (urllib.error.URLError, OSError):
            pass  # o processo morre depois do fsync da Decisao, sem responder
        else:
            raise RuntimeError(
                "o coordenador respondeu ao /transacao, mas devia cair depois de gravar a Decisao. "
                "Confira DOISPC_CAIR_APOS_DECIDIR no override do modo crash-coord."
            )
        em_duvida = sum(
            1
            for est in ct.estados_participantes(esperar_decisao=False).values()
            if est.get("em_duvida")
        )
        tempo, finais = ct.reiniciar_coordenador(arquivos)
        if not all(finais.values()):
            raise RuntimeError(f"depois do reinício do coordenador, nem todo participante decidiu: {finais}")
        ct.esperar_saude(ct.BASE_COORD, 30.0)
        estado = ct.esperar_conclusao(transacao, PRAZO_CONCLUSAO_S)
        duracao = time.monotonic() - inicio
        destino = dir_logs / f"rec-{item.semente}"
        _coletar(destino, arquivos)
    return {
        "transacao": transacao,
        "decisao": estado.get("decisao"),
        "em_duvida": em_duvida,
        "tempo_recuperacao_s": round(tempo, 6),
        "mensagens": contar_mensagens(destino)[transacao],
        "duracao_replica_s": round(duracao, 3),
    }
