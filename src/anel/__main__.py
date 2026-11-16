"""Ponto de entrada do Nó do Anel (`python -m src.anel`), o que o contêiner executa.

Lê o Nó do ambiente com `ler_no_do_ambiente`, que documenta as variáveis. Valida tudo antes de
abrir socket ou tocar o disco e serve o app FastAPI com uvicorn. O log vai para
`{LOG_DIR}/anel-no-{NO_ID}.jsonl`.
"""

from __future__ import annotations

import os

from src.anel.maquina import EstadoAnel
from src.anel.no import NoAnel, criar_app
from src.comum.config import ConfigInvalidaError, ConfigNo, ler_no_do_ambiente
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import abrir_log
from src.comum.rede import ClienteRede


def construir_no() -> tuple[NoAnel, ConfigNo, int, ControladorFalhas]:
    """Monta o `NoAnel` a partir do ambiente, validando antes de qualquer efeito.

    Devolve o `NoAnel`, a `ConfigNo` validada, a porta de escuta e o
    `ControladorFalhas` deste Nó. O controlador é derivado da `SEMENTE` (seus
    streams `drop`/`delay` são reproduzíveis), intercepta o
    recebimento de Mensagens e, via `ClienteComFalhas`, o despacho de saída, e
    expõe os endpoints `/falha/*`. A Máquina de eleição continua
    pura. Levanta `ConfigInvalidaError` na primeira violação de domínio, sem
    abrir socket.
    """
    ambiente = ler_no_do_ambiente(os.environ, algoritmo="anel", variavel_ids="ANEL")
    config = ambiente.config
    log = abrir_log(ambiente.log_dir / f"anel-no-{config.id}.jsonl", config.id)

    # Controlador de falhas dirigido pela Semente, plugado no recebimento e no
    # despacho do Nó. `registrar_plano_no_log` grava o `inicio_replica` com a
    # Semente e o Plano_de_Falhas.
    controlador = ControladorFalhas(config.semente, log=log)
    registrar_plano_no_log(
        controlador,
        log,
        cenario={"algoritmo": "anel", "anel": list(ambiente.ids), "deadline": config.deadline},
    )

    estado = EstadoAnel(no_id=config.id, anel=ambiente.ids)
    cliente = ClienteComFalhas(ClienteRede(retries=config.retries), controlador)
    no = NoAnel(estado, urls=ambiente.urls, cliente=cliente, deadline=config.deadline, log=log)
    return no, config, config.porta, controlador


def main() -> None:
    """Valida a configuração do ambiente e serve o Nó com uvicorn."""
    import uvicorn

    try:
        no, config, porta, controlador = construir_no()
    except ConfigInvalidaError as erro:
        # Encerra antes de iniciar o Transporte, sem efeito de rede.
        raise SystemExit(f"configuração inválida do Nó do Anel: {erro}") from erro

    app = criar_app(no, controlador=controlador)
    print(f"[anel] Nó {config.id} escutando em 0.0.0.0:{porta} (anel={config.sucessores})")
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
