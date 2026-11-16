"""Ponto de entrada do Nó do Bully (`python -m src.bully`), o que o contêiner executa.

`ler_no_do_ambiente` lê e documenta `NO_ID`, `IDS`, `URLS`, `PORTA`, `SEMENTE`, `DEADLINE`,
`RETRIES` e `LOG_DIR`. `PRAZO_OK` e `PRAZO_COORDENADOR`, em segundos, são obrigatórias. O log vai
para `{LOG_DIR}/bully-no-{NO_ID}.jsonl`, e o Nó convoca uma eleição assim que aceita conexões.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Mapping

from src.bully.maquina import PRAZO_COORDENADOR, PRAZO_OK, EstadoBully
from src.bully.no import GATILHO_INICIO, NoBully, criar_app
from src.comum.config import ConfigInvalidaError, ConfigNo, ler_no_do_ambiente, ler_numero
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import abrir_log
from src.comum.rede import ClienteRede

# Quanto a eleição de início espera o servidor aceitar conexões.
ESPERA_SERVIDOR_S = 30.0


def _ler_prazos(ambiente: Mapping[str, str], deadline: float) -> dict[str, float]:
    """Exige prazos que cubram as entregas esperadas, de até um Deadline cada.

    O prazo de OK cobre a ELEICAO e o OK. O de COORDENADOR cobre a ELEICAO ao vencedor, o prazo de
    OK dele e o COORDENADOR.
    """
    prazo_ok = ler_numero(ambiente, "PRAZO_OK", float, None)
    if not prazo_ok > 2 * deadline:
        raise ConfigInvalidaError(
            "PRAZO_OK", prazo_ok, f"número real > 2 * DEADLINE = {2 * deadline:g}"
        )
    prazo_coordenador = ler_numero(ambiente, "PRAZO_COORDENADOR", float, None)
    minimo = prazo_ok + 2 * deadline
    if not prazo_coordenador > minimo:
        raise ConfigInvalidaError(
            "PRAZO_COORDENADOR",
            prazo_coordenador,
            f"número real > PRAZO_OK + 2 * DEADLINE = {minimo:g}",
        )
    return {PRAZO_OK: prazo_ok, PRAZO_COORDENADOR: prazo_coordenador}


def construir_no() -> tuple[NoBully, ConfigNo, ControladorFalhas]:
    """Monta o Nó do ambiente. Configuração inválida levanta `ConfigInvalidaError` antes do log."""
    ambiente = ler_no_do_ambiente(os.environ, algoritmo="bully", variavel_ids="IDS")
    config = ambiente.config
    prazos = _ler_prazos(os.environ, config.deadline)
    log = abrir_log(ambiente.log_dir / f"bully-no-{config.id}.jsonl", config.id)
    controlador = ControladorFalhas(config.semente, log=log)
    registrar_plano_no_log(
        controlador,
        log,
        cenario={
            "algoritmo": "bully",
            "ids": list(ambiente.ids),
            "deadline": config.deadline,
            "prazo_ok": prazos[PRAZO_OK],
            "prazo_coordenador": prazos[PRAZO_COORDENADOR],
        },
    )
    no = NoBully(
        EstadoBully(no_id=config.id, ids=ambiente.ids),
        urls=ambiente.urls,
        cliente=ClienteComFalhas(ClienteRede(retries=config.retries), controlador),
        deadline=config.deadline,
        prazos=prazos,
        log=log,
    )
    return no, config, controlador


def _convocar_ao_subir(no: NoBully, servidor) -> None:
    """Convoca depois de o servidor aceitar conexões, senão as respostas à convocação se perdem."""
    limite = time.monotonic() + ESPERA_SERVIDOR_S
    while not servidor.started:
        if time.monotonic() >= limite:
            no_id = no.estado.no_id
            print(
                f"[bully] o servidor do Nó {no_id} não aceitou conexões em "
                f"{ESPERA_SERVIDOR_S:g} s, e a eleição de início não saiu. Veja o motivo com "
                f"docker compose -f compose.bully.yaml logs no-bully-{no_id}",
                file=sys.stderr,
            )
            return
        time.sleep(0.05)
    no.disparar_eleicao(GATILHO_INICIO)


def main() -> None:
    """Valida o ambiente, serve o Nó com uvicorn e convoca a eleição de início."""
    import uvicorn

    try:
        no, config, controlador = construir_no()
    except ConfigInvalidaError as erro:
        raise SystemExit(f"configuração inválida do Nó do Bully: {erro}") from erro

    app = criar_app(no, controlador=controlador)
    servidor = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=config.porta, log_level="warning")
    )
    threading.Thread(target=_convocar_ao_subir, args=(no, servidor), daemon=True).start()
    print(f"[bully] Nó {config.id} escutando em 0.0.0.0:{config.porta} (ids={no.estado.ids})")
    servidor.run()


if __name__ == "__main__":
    main()
