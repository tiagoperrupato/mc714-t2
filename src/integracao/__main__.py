"""Ponto de entrada do candidato (`python -m src.integracao`).

O Anel vem de `ler_no_do_ambiente`, e o Coordenador das variáveis `DOISPC_*` descritas em
`src.doispc.__main__`. O id do Coordenador é o `NO_ID`, então `COORD_*` não é lida. O log dos dois
vai para `{LOG_DIR}/candidato-{NO_ID}.jsonl`.
"""

from __future__ import annotations

import os

from src.anel.maquina import EstadoAnel
from src.anel.no import NoAnel
from src.comum.config import ConfigInvalidaError, ler_no_do_ambiente
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import abrir_log
from src.comum.modelos import No
from src.comum.rede import ClienteRede
from src.doispc.__main__ import ler_ambiente_coordenador, montar_coordenador
from src.integracao.candidato import criar_app


def construir_candidato() -> tuple[object, int]:
    """Valida o Anel e o Coordenador e monta os dois sobre um log, um controlador e um cliente."""
    ambiente = ler_no_do_ambiente(os.environ)
    config = ambiente.config
    eu = No(config.id, "0.0.0.0", config.porta)
    amb = ler_ambiente_coordenador(eu)
    no_anel = sorted(set(amb.participantes) & set(ambiente.ids))
    if no_anel:
        raise ConfigInvalidaError(
            "DOISPC_PARTICIPANTES",
            os.environ["DOISPC_PARTICIPANTES"],
            f"ids fora do ANEL {list(ambiente.ids)}, sem {no_anel}",
        )

    log = abrir_log(ambiente.log_dir / f"candidato-{config.id}.jsonl", config.id)
    controlador = ControladorFalhas(config.semente, log=log)
    registrar_plano_no_log(
        controlador,
        log,
        cenario={
            "algoritmo": "integracao",
            "anel": list(ambiente.ids),
            "deadline": config.deadline,
            **amb.cenario(),
        },
    )
    cliente = ClienteComFalhas(ClienteRede(retries=config.retries), controlador)
    no = NoAnel(
        EstadoAnel(no_id=config.id, anel=ambiente.ids),
        urls=ambiente.urls,
        cliente=cliente,
        deadline=config.deadline,
        log=log,
    )
    servico = montar_coordenador(eu, amb, log=log, cliente=cliente)
    return criar_app(no, servico, log=log, controlador=controlador), config.porta


def main() -> None:
    """Sobe o candidato com uvicorn. Configuração inválida sai com SystemExit, sem efeito."""
    import uvicorn

    try:
        app, porta = construir_candidato()
    except (ValueError, ConfigInvalidaError) as erro:
        raise SystemExit(f"configuração inválida do candidato: {erro}") from erro

    print(f"[integracao] candidato escutando em 0.0.0.0:{porta}")
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
