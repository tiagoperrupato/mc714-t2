"""CLI de cenário: sobe UM cenário reproduzível de um algoritmo sobre o compose.

Enquanto a bateria (`scripts/bateria.py`) roda muitas Réplicas, este script sobe
um cenário real sobre o Docker Compose, conduz o algoritmo pelos
endpoints de controle, coleta as métricas observadas **e os Logs_Estruturados e
Logs_Duraveis que os Nós reais deixaram** para `results/`, e por fim roda os
invariantes de correção sobre esses logs (`scripts/verificar.py`). É o
complemento "ponta a ponta" da orquestração: a bateria mede
tendência com reprodutibilidade; o cenário demonstra um caso único sobre a rede
real e prova sua correção pelos invariantes.

Cada cenário registra a **Semente** com que foi conduzido:
ela aparece no registro JSON coletado e, por vir do ambiente dos contêineres,
também no `inicio_replica` de cada Log_Estruturado. Com a mesma Semente e a
mesma configuração do compose, o Cenário de falha é repetível.

O registro guarda o desfecho esperado ao lado do observado. O script sai 1
quando eles diferem, mesmo que os invariantes passem, porque um `2pc commit`
que termina em ABORT não viola nenhuma propriedade de safety.

Cenários do Anel:

    python scripts/cenario.py anel normal
        Sobe os 5 Nós, dispara uma eleição e coleta o Líder e o
        Tempo_de_Convergencia observado. A eleição converge para o maior id (5).

    python scripts/cenario.py anel falha
        Idem, e então derruba o contêiner do Líder (Nó 5) e dispara nova
        eleição: os Nós vivos reelegem o maior id vivo (4).

    python scripts/cenario.py anel reinicio
        Como `falha`, e então reinicia o contêiner do 5 com `docker compose start`.
        O 5 volta sem memória, sem Líder e sem rodadas, e uma terceira eleição o
        reintegra: todos reportam o Líder 5.

Cenários do 2PC:

    python scripts/cenario.py 2pc commit
        Coordenador + 3 Participantes, todos votando SIM → COMMIT em todos.

    python scripts/cenario.py 2pc abort
        Participante 3 vota NAO → ABORT atômico em todos.

    python scripts/cenario.py 2pc crash-coord
        Todos votam SIM, e o processo do Coordenador morre logo depois de
        gravar a Decisao, antes de difundi-la (flag `DOISPC_CAIR_APOS_DECIDIR`).
        O cenário mostra os Participantes em Estado_Em_Duvida e confere os logs
        com o Coordenador fora, o que dá o aviso de dúvida dos três. Depois
        reinicia o contêiner e mostra todos decidindo COMMIT depois que o
        Coordenador relê o Log_Duravel e difunde a Decisao gravada.

    python scripts/cenario.py 2pc crash-coord-part
        Como o crash-coord, e o contêiner do Participante 11 cai e volta
        duas vezes: em dúvida, antes da verificação com o Coordenador fora e do
        reinício dele, e depois que todos decidem COMMIT, quando recupera a
        Decisao do Log_Duravel.

    python scripts/cenario.py 2pc particao
        Todos votam SIM, com o Toxiproxy entre o Coordenador e os Participantes. O
        proxy do Participante 11 é desativado antes da transação, e o PREPARE não
        chega a ele. O Coordenador decide ABORT no prazo de coleta de Votos e o
        difunde. O 11 fica sem Decisao enquanto a partição dura, e depois da cura a
        retransmissão da DECISAO o alcança. Todos terminam em ABORT.

Cenários da integração, em que o Líder do Anel de três candidatos coordena o 2PC
(compose.integracao.yaml):

    python scripts/cenario.py integracao normal
        Elege o candidato 3. O candidato 1 recusa a transação com 409 e aponta o 3,
        que a comita com o id c3-tx-n em todos os Participantes.

    python scripts/cenario.py integracao failover
        O candidato 3 grava o COMMIT de c3-tx-a e morre antes de difundi-lo. O
        cliente dispara a eleição, que elege o 2, e o 2 comita c2-tx-b com c3-tx-a
        ainda em dúvida nos Participantes. O 3 volta, relê o Log_Duravel e difunde o
        COMMIT de c3-tx-a, e a eleição seguinte o reintegra como Líder.

Requer Docker com Compose v2. Sem Docker, o script falha com uma mensagem clara
e código de saída 2, em vez de travar.

O script valida a configuração do cenário ANTES de subir qualquer contêiner ou
tocar `results/`, e sempre derruba o compose ao final
(`down -v --remove-orphans`), para não deixar contêiner nem volume órfão.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
from functools import partial
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scripts import verificar  # noqa: E402
from src.comum.toxiproxy import ClienteToxiproxy, nome_proxy_participante, proxies_2pc  # noqa: E402
from src.experimentos.ambiente import coletar_ambiente  # noqa: E402
from src.experimentos.conteineres import (  # noqa: E402
    BASE_COORD,
    COMPOSE,
    COMPOSE_INTEGRACAO,
    COMPOSE_TOXIPROXY,
    PORTAS_ANEL,
    PORTAS_CANDIDATO,
    PORTAS_PART,
    PORTAS_PART_INTEGRACAO,
    SERVICOS_2PC,
    SERVICOS_ANEL,
    SERVICOS_INTEGRACAO,
    coletar_logs,
    compose,
    decisoes,
    derrubar_servico,
    docker_disponivel,
    eleger,
    esperar_conclusao,
    esperar_saudavel,
    esperar_saude,
    estados_participantes,
    http_json,
    override_2pc,
    override_integracao,
    reiniciar_coordenador,
    reiniciar_servico,
    url_estado,
)

RESULTS_PADRAO = _RAIZ / "results"

# Semente padrão dos cenários (casa com SEMENTE do compose). Registrada no
# resultado e sobreponível por --semente para reproduzir uma execução.
SEMENTE_PADRAO = 0

# Os modos de cada algoritmo e os invariantes que cada um demonstra. Um deles sem caso
# verificado nos logs do cenário o reprova, e por isso um cenário sem log não passa.
MODOS: dict[str, dict[str, tuple[int, ...]]] = {
    "anel": {"normal": (1, 2, 3), "falha": (1, 2, 3), "reinicio": (1, 2, 3)},
    "2pc": {
        "commit": (4, 6, 7),
        "abort": (4, 7),
        "crash-coord": (4, 5, 6, 7),
        "crash-coord-part": (4, 5, 6, 7),
        "particao": (4, 7),
    },
    "integracao": {"normal": (1, 2, 3, 4, 6, 7), "failover": (1, 2, 3, 4, 5, 6, 7)},
}


# --------------------------------------------------------------------------- #
# Override do Anel (semente)
# --------------------------------------------------------------------------- #


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


def _exigir_saude(servico: str, base: str, cenario: str, arquivos: list[Path]) -> None:
    """Espera o /saude de `servico` depois do start e, sem resposta, falha com a cauda do log."""
    if esperar_saude(base, 30.0):
        return
    # A cauda do log vai na mensagem, porque o down do fim do cenário apaga o contêiner.
    cauda = compose("logs", "--no-color", "--tail", "20", servico, arquivos=arquivos).stdout
    raise RuntimeError(
        f"{servico} não respondeu em {base}/saude 30 s depois do start. Rode de novo "
        f"python scripts/cenario.py {cenario}. Últimas linhas do {servico}:\n{cauda}"
    )


def _cenario_anel(modo: str, arquivos: list[Path]) -> dict[str, Any]:
    """Conduz um cenário do Anel sobre o compose e devolve as métricas observadas.

    - `normal`: dispara uma eleição com os cinco Nós vivos, que converge para o
      maior id (5).
    - `falha`: depois dessa eleição, derruba o contêiner do Líder com
      `docker compose kill` e dispara outra. Ela salta o 5 e
      converge para o maior id vivo (4).
    - `reinicio`: depois da reeleição, dá `start` no contêiner do 5, que volta sem
      memória, e dispara a terceira eleição, que o reintegra como Líder (5).

    Cada eleição é uma rodada com id próprio, e os invariantes conferem cada uma
    contra os Nós vivos nela.
    """
    bases = {i: f"http://127.0.0.1:{porta}" for i, porta in PORTAS_ANEL.items()}
    todos = list(PORTAS_ANEL)
    lideres, tempo = eleger(bases, todos, 5)
    registro: dict[str, Any] = {
        "cenario": f"anel_{modo}",
        "algoritmo": "anel",
        "modo": modo,
        "maior_id_topologia": 5,
        "lideres_iniciais": lideres,
        "tempo_convergencia_s": round(tempo, 4),
        "esperado": {"lideres_iniciais": dict.fromkeys(todos, 5)},
    }
    if modo in ("falha", "reinicio"):
        derrubar_servico("no-anel-5", arquivos)
        vivos = [i for i in todos if i != 5]
        lideres, tempo = eleger(bases, vivos, 4)
        registro.update(
            {
                "falha_injetada": "docker compose kill no-anel-5, depois da primeira eleição",
                "lideres_pos_falha": lideres,
                "tempo_reeleicao_s": round(tempo, 4),
            }
        )
        registro["esperado"]["lideres_pos_falha"] = dict.fromkeys(vivos, 4)
    if modo == "reinicio":
        reiniciar_servico("no-anel-5", arquivos)
        # Sem esperar o /saude, o 4 saltaria o 5 na terceira rodada, que elegeria o 4.
        _exigir_saude("no-anel-5", bases[5], "anel reinicio", arquivos)
        estado5 = http_json(f"{bases[5]}/estado")
        lideres, tempo = eleger(bases, todos, 5)
        registro["falha_injetada"] += ", e docker compose start no-anel-5 depois da segunda"
        registro.update(
            {
                "estado_5_apos_reinicio": {
                    "lider": estado5["lider"],
                    "rodadas_concluidas": estado5["rodadas_concluidas"],
                },
                "lideres_pos_reinicio": lideres,
                "tempo_reintegracao_s": round(tempo, 4),
            }
        )
        registro["esperado"]["estado_5_apos_reinicio"] = {"lider": None, "rodadas_concluidas": []}
        registro["esperado"]["lideres_pos_reinicio"] = dict.fromkeys(todos, 5)
    return registro


# --------------------------------------------------------------------------- #
# Cenário do 2PC
# --------------------------------------------------------------------------- #

# O Participante que o `crash-coord-part` derruba duas vezes.
SERVICO_PART_REINICIADO = "participante-1"
ID_PART_REINICIADO = 11


def _reiniciar_participante(arquivos: list[Path]) -> dict[str, Any]:
    """Derruba e sobe o Participante 11 e devolve o Voto e a Decisao que ele recuperou do log."""
    derrubar_servico(SERVICO_PART_REINICIADO, arquivos)
    reiniciar_servico(SERVICO_PART_REINICIADO, arquivos)
    base = f"http://127.0.0.1:{PORTAS_PART[ID_PART_REINICIADO]}"
    _exigir_saude(SERVICO_PART_REINICIADO, base, "2pc crash-coord-part", arquivos)
    estado = http_json(f"{base}/estado")
    return {chave: estado.get(chave) for chave in ("voto", "decisao", "em_duvida")}


def _cenario_2pc(modo: str, arquivos: list[Path], erro) -> dict[str, Any]:
    """Conduz uma transação 2PC sobre o compose e devolve as métricas observadas.

    O voto dos Participantes e o crash do Coordenador vêm do override montado por
    `override_2pc`. A DECISAO sai da fila do Coordenador depois da resposta do
    `/transacao`, então o condutor espera cada Participante decidir, com prazo.
    Em `crash-coord` o `/transacao` não responde, porque o processo morre depois
    de gravar a Decisao. O condutor registra os Participantes em dúvida, verifica
    os logs com o Coordenador fora, reinicia o contêiner e mede o tempo até todos
    decidirem. Em `crash-coord-part`, o Participante 11 cai e volta em dúvida antes
    dessa verificação, e cai e volta de novo depois que todos decidem.
    """
    if modo == "particao":
        return _cenario_2pc_particao(erro)
    esperada = "ABORT" if modo == "abort" else "COMMIT"
    esperado: dict[str, Any] = {
        "decisao": esperada,
        "decisoes_participantes": dict.fromkeys(PORTAS_PART, esperada),
    }
    esperar_saude(BASE_COORD, 30.0)
    inicio = time.monotonic()
    try:
        resposta = http_json(f"{BASE_COORD}/transacao", metodo="POST", timeout=30.0)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - rede do cenário
        resposta = {"erro": str(exc)}
    latencia = time.monotonic() - inicio

    if modo not in ("crash-coord", "crash-coord-part"):
        return {
            "cenario": f"2pc_{modo}",
            "algoritmo": "2pc",
            "modo": modo,
            "decisao": resposta.get("decisao"),
            "latencia_commit_s": round(latencia, 4),
            "participantes": resposta.get("participantes"),
            "decisoes_participantes": decisoes(estados_participantes(esperar_decisao=True)),
            "esperado": esperado,
        }

    em_duvida = _em_duvida(estados_participantes(esperar_decisao=False))
    print(f"[cenario] Coordenador caiu ({resposta.get('erro')}). Em dúvida: {em_duvida}", file=erro)
    combinado = modo == "crash-coord-part"
    if combinado:
        reinicio_em_duvida = _reiniciar_participante(arquivos)
        print(
            f"[cenario] Participante {ID_PART_REINICIADO} reiniciado com o Coordenador fora: "
            f"{reinicio_em_duvida}",
            file=erro,
        )
        # O start do Coordenador espera o healthcheck dos Participantes, fora do tempo medido.
        esperar_saudavel(SERVICO_PART_REINICIADO, arquivos)
    codigo_fora, relatorio_fora = _verificar_com_coordenador_fora(arquivos)
    print("[cenario] verificação com o Coordenador fora:", file=erro)
    print("\n".join(relatorio_fora), file=erro)

    recuperacao, finais = reiniciar_coordenador(arquivos)
    print(f"[cenario] Coordenador reiniciado. Em {recuperacao:.2f} s: {finais}", file=erro)
    # Os logs só são coletados depois de todos os ACKs, para incluírem a retransmissão.
    esperar_saude(BASE_COORD, 30.0)
    estado_coord = esperar_conclusao()
    registro = {
        "cenario": f"2pc_{modo}",
        "algoritmo": "2pc",
        "modo": modo,
        "falha_injetada": "processo do Coordenador morre depois de gravar a Decisao "
        "(DOISPC_CAIR_APOS_DECIDIR) e o contêiner é reiniciado",
        "participantes_em_duvida": em_duvida,
        "verificacao_com_coordenador_fora": {"codigo": codigo_fora, "relatorio": relatorio_fora},
        "avisos_com_coordenador_fora": sum("aviso:" in linha for linha in relatorio_fora),
        "decisao": estado_coord.get("decisao"),
        "concluido": estado_coord.get("concluido"),
        "tempo_recuperacao_s": round(recuperacao, 4),
        "decisoes_participantes": finais,
        "esperado": esperado
        | {
            "participantes_em_duvida": sorted(PORTAS_PART),
            "avisos_com_coordenador_fora": len(PORTAS_PART),
            "concluido": True,
        },
    }
    if not combinado:
        return registro
    reinicio_apos_decidir = _reiniciar_participante(arquivos)
    print(
        f"[cenario] Participante {ID_PART_REINICIADO} reiniciado depois da Decisao: "
        f"{reinicio_apos_decidir}",
        file=erro,
    )
    registro["falha_injetada"] += (
        f". O contêiner do Participante {ID_PART_REINICIADO} cai e volta em dúvida antes do "
        "reinício do Coordenador, e cai e volta com a Decisao depois que todos decidem"
    )
    registro |= {
        "reinicio_em_duvida": reinicio_em_duvida,
        "reinicio_apos_decidir": reinicio_apos_decidir,
    }
    registro["esperado"] |= {
        "reinicio_em_duvida": {"voto": "SIM", "decisao": None, "em_duvida": True},
        "reinicio_apos_decidir": {"voto": "SIM", "decisao": esperada, "em_duvida": False},
    }
    return registro


def _cenario_2pc_particao(erro) -> dict[str, Any]:
    """Particiona o Participante 11 durante uma transação e cura a partição depois do ABORT.

    Os três votariam SIM, então o ABORT só pode vir do prazo de coleta de Votos, e o log do
    Coordenador traz o `timeout_votos`.
    """
    proxy = nome_proxy_participante(1)  # o primeiro Participante, id 11
    outros = {pid: porta for pid, porta in PORTAS_PART.items() if pid != 11}
    with ClienteToxiproxy() as tox:
        tox.esperar_pronto()
        tox.semear(proxies_2pc(tuple(sorted(PORTAS_PART))))
        esperar_saude(BASE_COORD, 30.0)
        tox.definir_habilitado(proxy, False)
        inicio = time.monotonic()
        resposta = http_json(f"{BASE_COORD}/transacao", metodo="POST", timeout=30.0)
        latencia = time.monotonic() - inicio
        estados_participantes(esperar_decisao=True, portas=outros)
        durante = decisoes(estados_participantes(esperar_decisao=False))
        coord_durante = http_json(f"{BASE_COORD}/estado")
        print(
            f"[cenario] {resposta.get('decisao')} em {latencia:.2f} s com o 11 isolado: {durante}",
            file=erro,
        )
        tox.definir_habilitado(proxy, True)
    finais = decisoes(estados_participantes(esperar_decisao=True))
    estado_coord = esperar_conclusao()
    print(f"[cenario] partição curada: {finais}", file=erro)
    return {
        "cenario": "2pc_particao",
        "algoritmo": "2pc",
        "modo": "particao",
        "falha_injetada": f"proxy {proxy} desativado antes da transação e reativado depois do ABORT",
        "decisao": resposta.get("decisao"),
        "latencia_decisao_s": round(latencia, 4),
        "decisoes_durante_particao": durante,
        "concluido_durante_particao": coord_durante.get("concluido"),
        "decisoes_participantes": finais,
        "concluido": estado_coord.get("concluido"),
        "esperado": {
            "decisao": "ABORT",
            "decisoes_durante_particao": {11: None, 12: "ABORT", 13: "ABORT"},
            "concluido_durante_particao": False,
            "decisoes_participantes": dict.fromkeys(PORTAS_PART, "ABORT"),
            "concluido": True,
        },
    }


def _em_duvida(estados: dict[int, Any]) -> list[int]:
    """Os Participantes em Estado_Em_Duvida, em ordem."""
    return sorted(pid for pid, est in estados.items() if est.get("em_duvida"))


def _verificar_com_coordenador_fora(arquivos: list[Path]) -> tuple[int, list[str]]:
    """Coleta os logs do 2PC com o Coordenador fora e roda os invariantes neles.

    Os logs ficam fora de `results/`, para a verificação de lá não ler os mesmos eventos duas vezes.
    O subdiretório tem nome fixo, para o relatório gravado no JSON não levar um nome sorteado.
    """
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "coordenador_fora_logs"
        coletar_logs("2pc", SERVICOS_2PC, destino, arquivos)
        linhas: list[str] = []
        return verificar.verificar([destino], imprimir=linhas.append), linhas


# --------------------------------------------------------------------------- #
# Cenários da integração
# --------------------------------------------------------------------------- #

# Os ids levam o prefixo do candidato que coordena a transação.
TX_NORMAL = "c3-tx-n"
TX_A = "c3-tx-a"
TX_B = "c2-tx-b"


def _pedir_transacao(base: str, nome: str) -> tuple[int | None, dict[str, Any]]:
    """`POST /transacao` em `base` com o status e o corpo da resposta.

    Um status de erro, como o 409 de quem não é Líder, volta com o corpo dele. Sem resposta, porque
    o processo caiu, o status é None.
    """
    try:
        return 200, http_json(
            f"{base}/transacao", metodo="POST", corpo={"transacao": nome}, timeout=30.0
        )
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(texto)
        except json.JSONDecodeError:
            return exc.code, {"erro": texto}
    except (urllib.error.URLError, OSError) as exc:
        return None, {"erro": str(exc)}


def _cenario_integracao(modo: str, arquivos: list[Path], erro) -> dict[str, Any]:
    """Conduz a integração sobre o compose dela e devolve as métricas observadas.

    Quem detecta a queda do Líder é o cliente, pelo POST sem resposta, e é ele que dispara a
    eleição. Nenhum candidato assume a transação de outro, então c3-tx-a espera o 3 voltar.
    Cada passo imprime uma linha `[cenario]` em `erro`.
    """
    bases = {i: f"http://127.0.0.1:{porta}" for i, porta in PORTAS_CANDIDATO.items()}
    todos = list(PORTAS_CANDIDATO)
    participantes = partial(estados_participantes, portas=PORTAS_PART_INTEGRACAO)
    commit = dict.fromkeys(PORTAS_PART_INTEGRACAO, "COMMIT")

    lideres, tempo = eleger(bases, todos, 3)
    print(f"[cenario] eleição no candidato 1: Líderes {lideres} em {tempo:.2f} s", file=erro)
    registro: dict[str, Any] = {
        "cenario": f"integracao_{modo}",
        "algoritmo": "integracao",
        "modo": modo,
        "lideres_iniciais": lideres,
        "tempo_eleicao_s": round(tempo, 4),
        "esperado": {"lideres_iniciais": dict.fromkeys(todos, 3)},
    }

    if modo == "normal":
        status, recusa = _pedir_transacao(bases[1], "tx-n")
        print(f"[cenario] candidato 1 responde {status}: {recusa.get('erro')}", file=erro)
        inicio = time.monotonic()
        _, resposta = _pedir_transacao(bases[3], "tx-n")
        latencia = time.monotonic() - inicio
        print(
            f"[cenario] candidato 3: {resposta.get('transacao')} {resposta.get('decisao')} "
            f"em {latencia:.2f} s",
            file=erro,
        )
        finais = decisoes(participantes(esperar_decisao=True, transacao=TX_NORMAL))
        print(f"[cenario] Participantes em {TX_NORMAL}: {finais}", file=erro)
        conclusao = esperar_conclusao(TX_NORMAL, base=bases[3])
        print(f"[cenario] todos os ACK no candidato 3: {conclusao.get('concluido')}", file=erro)
        registro |= {
            "recusa_nao_lider": {"status": status, "lider": recusa.get("lider")},
            "transacao": resposta.get("transacao"),
            "decisao": resposta.get("decisao"),
            "latencia_commit_s": round(latencia, 4),
            "decisoes_participantes": finais,
            "concluido": conclusao.get("concluido"),
        }
        registro["esperado"] |= {
            "recusa_nao_lider": {"status": 409, "lider": 3},
            "transacao": TX_NORMAL,
            "decisao": "COMMIT",
            "decisoes_participantes": commit,
            "concluido": True,
        }
        return registro

    status, resposta_a = _pedir_transacao(bases[3], "tx-a")
    queda = time.monotonic()
    if status is not None:
        raise RuntimeError(
            f"o candidato 3 respondeu {status} ao POST de tx-a, mas devia cair logo depois de "
            "gravar a Decisao. Confira DOISPC_CAIR_APOS_DECIDIR do candidato-3 em "
            "override_integracao."
        )
    print(f"[cenario] candidato 3 caiu depois de decidir {TX_A}: {resposta_a['erro']}", file=erro)
    em_duvida = _em_duvida(participantes(esperar_decisao=False, transacao=TX_A))
    print(f"[cenario] Participantes em dúvida em {TX_A}: {em_duvida}", file=erro)

    vivos = [1, 2]
    lideres_pos, tempo_reeleicao = eleger(bases, vivos, 2)
    print(
        f"[cenario] o cliente dispara a eleição no candidato 1: Líderes {lideres_pos} "
        f"em {tempo_reeleicao:.2f} s",
        file=erro,
    )
    inicio = time.monotonic()
    _, resposta_b = _pedir_transacao(bases[2], "tx-b")
    fim = time.monotonic()
    print(
        f"[cenario] candidato 2: {resposta_b.get('transacao')} {resposta_b.get('decisao')} em "
        f"{fim - inicio:.2f} s, {fim - queda:.2f} s depois da queda do 3",
        file=erro,
    )
    finais_b = decisoes(participantes(esperar_decisao=True, transacao=TX_B))
    print(f"[cenario] Participantes em {TX_B}: {finais_b}", file=erro)
    duvida_durante = _em_duvida(participantes(esperar_decisao=False, transacao=TX_A))
    tx_a_no_2 = http_json(url_estado(bases[2], TX_A)).get("transacao")
    print(
        f"[cenario] {TX_A} segue em dúvida em {duvida_durante}, e o /estado dela no candidato 2 "
        f"traz transacao={tx_a_no_2}",
        file=erro,
    )

    recuperacao, finais_a = reiniciar_coordenador(
        arquivos, servico="candidato-3", portas=PORTAS_PART_INTEGRACAO, transacao=TX_A
    )
    print(
        f"[cenario] candidato 3 reiniciado. Em {recuperacao:.2f} s, {TX_A}: {finais_a}", file=erro
    )
    _exigir_saude("candidato-3", bases[3], "integracao failover", arquivos)
    lideres_finais, tempo_reintegracao = eleger(bases, todos, 3)
    print(
        f"[cenario] eleição com o 3 de volta: Líderes {lideres_finais} em "
        f"{tempo_reintegracao:.2f} s",
        file=erro,
    )
    conclusao_a = esperar_conclusao(TX_A, base=bases[3])
    conclusao_b = esperar_conclusao(TX_B, base=bases[2])
    print(
        f"[cenario] todos os ACK: {TX_A} no 3 {conclusao_a.get('concluido')}, {TX_B} no 2 "
        f"{conclusao_b.get('concluido')}",
        file=erro,
    )
    registro |= {
        "falha_injetada": f"processo do candidato 3 morre depois de gravar a Decisao de {TX_A} "
        f"(DOISPC_CAIR_APOS_DECIDIR) e o contêiner é reiniciado depois do COMMIT de {TX_B}",
        "em_duvida_tx_a": em_duvida,
        "lideres_pos_falha": lideres_pos,
        "tempo_reeleicao_s": round(tempo_reeleicao, 4),
        "transacao_tx_b": resposta_b.get("transacao"),
        "decisao_tx_b": resposta_b.get("decisao"),
        "latencia_tx_b_s": round(fim - inicio, 4),
        "tempo_failover_s": round(fim - queda, 4),
        "decisoes_participantes_tx_b": finais_b,
        "em_duvida_tx_a_durante_tx_b": duvida_durante,
        "tx_a_no_novo_lider": tx_a_no_2,
        "tempo_recuperacao_tx_a_s": round(recuperacao, 4),
        "decisoes_participantes_tx_a": finais_a,
        "lideres_finais": lideres_finais,
        "tempo_reintegracao_s": round(tempo_reintegracao, 4),
        "concluido_tx_a": conclusao_a.get("concluido"),
        "concluido_tx_b": conclusao_b.get("concluido"),
    }
    registro["esperado"] |= {
        "em_duvida_tx_a": sorted(PORTAS_PART_INTEGRACAO),
        "lideres_pos_falha": dict.fromkeys(vivos, 2),
        "transacao_tx_b": TX_B,
        "decisao_tx_b": "COMMIT",
        "decisoes_participantes_tx_b": commit,
        "em_duvida_tx_a_durante_tx_b": sorted(PORTAS_PART_INTEGRACAO),
        "tx_a_no_novo_lider": None,
        "decisoes_participantes_tx_a": commit,
        "lideres_finais": dict.fromkeys(todos, 3),
        "concluido_tx_a": True,
        "concluido_tx_b": True,
    }
    return registro


# --------------------------------------------------------------------------- #
# Coleta dos logs reais (JSONL estruturado + Log_Duravel) de dentro dos Nós
# --------------------------------------------------------------------------- #


def _coletar_stdout(servicos: list[str], destino: Path, arquivos: list[Path]) -> None:
    """Grava o stdout de contêiner de cada serviço (um .txt por Nó), para depuração."""
    destino.mkdir(parents=True, exist_ok=True)
    for servico in servicos:
        saida = compose("logs", "--no-color", servico, arquivos=arquivos, timeout=60)
        (destino / f"{servico}.txt").write_text(saida.stdout, encoding="utf-8")


def _divergencias(registro: dict[str, Any]) -> list[str]:
    """Os campos de `registro["esperado"]` que o desfecho observado não reproduz."""
    return [
        f"{campo}: esperado {valor}, obtido {registro.get(campo)}"
        for campo, valor in registro["esperado"].items()
        if registro.get(campo) != valor
    ]


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


def _validar_modo(algoritmo: str, modo: str) -> None:
    """Recusa um algoritmo ou modo fora de `MODOS`, antes de qualquer efeito colateral."""
    if algoritmo not in MODOS:
        raise ValueError(f"algoritmo inválido: {algoritmo!r} (use {'|'.join(MODOS)})")
    if modo not in MODOS[algoritmo]:
        raise ValueError(
            f"modo de {algoritmo} inválido: {modo!r} (use {'|'.join(MODOS[algoritmo])})"
        )


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

    Devolve 0 em sucesso (avisos de invariante não reprovam), 1 em falha de
    execução, desfecho diferente do esperado, reprovação de um invariante ou
    invariante de `MODOS` sem caso verificado, e 2 quando o Docker não está disponível. A configuração do cenário é
    validada antes de subir qualquer contêiner, e o compose é sempre derrubado ao
    final.
    """
    try:
        _validar_modo(algoritmo, modo)
    except ValueError as exc:
        print(f"[cenario] configuração inválida: {exc}", file=erro)
        return 1

    if not isinstance(semente, int) or isinstance(semente, bool) or semente < 0:
        print(f"[cenario] semente deve ser inteiro >= 0; recebido {semente!r}", file=erro)
        return 1

    particao = (algoritmo, modo) == ("2pc", "particao")
    if algoritmo == "anel":
        nos, compose_base = SERVICOS_ANEL, [COMPOSE]
        override_texto = _override_anel(semente)
        conduzir = lambda arquivos: _cenario_anel(modo, arquivos)  # noqa: E731
    elif algoritmo == "2pc":
        nos, compose_base = SERVICOS_2PC, [COMPOSE, COMPOSE_TOXIPROXY] if particao else [COMPOSE]
        override_texto = override_2pc(modo, semente)
        conduzir = lambda arquivos: _cenario_2pc(modo, arquivos, erro)  # noqa: E731
    else:
        nos, compose_base = SERVICOS_INTEGRACAO, [COMPOSE_INTEGRACAO]
        override_texto = override_integracao(modo, semente)
        conduzir = lambda arquivos: _cenario_integracao(modo, arquivos, erro)  # noqa: E731
    # Só a partição põe o Toxiproxy entre os Nós. Os logs são dos Nós, e o stdout de todos.
    servicos = [*nos, "toxiproxy"] if particao else nos

    if not docker_disponivel():
        print(
            "[cenario] Docker (daemon + compose v2) indisponível. Este script "
            "sobe contêineres: inicie o daemon e confira com docker info.",
            file=erro,
        )
        return 2

    base = Path(results)
    rotulo_arq = f"{algoritmo}_{modo}"

    with tempfile.TemporaryDirectory() as tmp:
        override = Path(tmp) / "cenario-override.yaml"
        override.write_text(override_texto, encoding="utf-8")
        arquivos = [*compose_base, override]

        compose("down", "-v", "--remove-orphans", arquivos=arquivos)
        up = compose("up", "-d", "--build", "--wait", *servicos, arquivos=arquivos, timeout=900)
        if up.returncode != 0:
            print(f"[cenario] falha ao subir o compose:\n{up.stdout}\n{up.stderr}", file=erro)
            compose("down", "-v", "--remove-orphans", arquivos=arquivos)
            return 1

        try:
            registro = conduzir(arquivos)
            registro["semente"] = semente
            divergencias = _divergencias(registro)
            registro["desfecho"] = {"ok": not divergencias, "divergencias": divergencias}

            logs_dir = base / f"cenario_{rotulo_arq}_logs"
            if logs_dir.exists():
                shutil.rmtree(logs_dir)
            coletar_logs(algoritmo, nos, logs_dir, arquivos)
            _coletar_stdout(servicos, base / f"cenario_{rotulo_arq}_stdout", arquivos)

            # Roda os invariantes sobre os logs coletados deste cenário.
            linhas_verif: list[str] = []
            codigo_verif = verificar.verificar(
                [logs_dir], imprimir=linhas_verif.append, exigidos=MODOS[algoritmo][modo]
            )
            registro["verificacao"] = {
                "codigo": codigo_verif,
                "ok": codigo_verif == 0,
                "relatorio": linhas_verif,
            }

            arquivo_registro = base / f"cenario_{rotulo_arq}.json"
            _gravar_registro(registro, arquivo_registro)
            print(
                f"[cenario] {algoritmo}/{modo} (semente={semente}): "
                f"{json.dumps({k: v for k, v in registro.items() if k != 'verificacao'}, ensure_ascii=False)}",
                file=saida,
            )
            print("\n".join(linhas_verif), file=saida)
            if divergencias:
                print(
                    f"[cenario] o desfecho de {algoritmo}/{modo} difere do esperado: "
                    f"{'; '.join(divergencias)}. Registro em {arquivo_registro}, "
                    f"logs dos Nós em {logs_dir}",
                    file=erro,
                )
            if codigo_verif != 0:
                print(
                    f"[cenario] VERIFICAÇÃO REPROVOU para {algoritmo}/{modo}",
                    file=erro,
                )
            return 1 if divergencias or codigo_verif != 0 else 0
        except Exception as exc:  # noqa: BLE001 - qualquer falha de condução
            print(f"[cenario] falha ao conduzir {algoritmo}/{modo}: {exc}", file=erro)
            return 1
        finally:
            compose("down", "-v", "--remove-orphans", arquivos=arquivos)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cenario",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("algoritmo", choices=list(MODOS), help="algoritmo alvo")
    parser.add_argument(
        "modo",
        help=" ".join(f"{alg}: {'|'.join(modos)}." for alg, modos in MODOS.items()),
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
