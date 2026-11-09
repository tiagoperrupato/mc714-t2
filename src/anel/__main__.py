"""Ponto de entrada do Nó do Anel: lê a topologia do ambiente e sobe o servidor.

Este módulo é o que o contêiner executa (`python -m src.anel`). Ele monta a
configuração a partir de variáveis de ambiente — a topologia do Anel NÃO está
embutida no código — valida tudo antes de abrir socket ou tocar
o disco, constrói o `NoAnel` e serve o app FastAPI com uvicorn.

Variáveis de ambiente:

- `NO_ID`            (obrigatória) id inteiro positivo deste Nó.
- `ANEL`             (obrigatória) ordem cíclica dos ids, separada por vírgula,
                     ex.: "1,2,3,4,5". O Sucessor é derivado dessa ordem.
- `URLS`             (obrigatória) mapa id=url_base por vírgula, ex.:
                     "1=http://no-anel-1:8000,2=http://no-anel-2:8000,...".
                     Deve cobrir todos os ids do Anel, inclusive o próprio.
- `PORTA`            porta HTTP onde este Nó escuta (default 8000).
- `SEMENTE`          semente de aleatoriedade (default 0).
- `DEADLINE`         Deadline por repasse, em segundos (default 1.0).
- `RETRIES`          tentativas extras por envio (default 2).
- `LOG_DIR`          diretório sob o qual gravar o Log_Estruturado (default
                     "results"); o arquivo é `{LOG_DIR}/anel-no-{NO_ID}.jsonl`.

A separação de responsabilidades do projeto é preservada: a lógica de eleição
fica em `src.anel.maquina`, a rede em `src.comum.rede`, e este módulo apenas as
costura a partir da configuração.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.anel.maquina import EstadoAnel
from src.anel.no import NoAnel, criar_app
from src.comum.config import ConfigInvalidaError, ConfigNo
from src.comum.falhas import ClienteComFalhas, ControladorFalhas, registrar_plano_no_log
from src.comum.log import abrir_log
from src.comum.rede import ClienteRede


def _ler_anel(bruto: str | None) -> tuple[int, ...]:
    """Parseia "1,2,3,4,5" numa tupla de ids, falhando cedo se malformado."""
    if not bruto:
        raise ConfigInvalidaError("ANEL", bruto, "lista de ids separada por vírgula")
    try:
        ids = tuple(int(parte.strip()) for parte in bruto.split(",") if parte.strip())
    except ValueError as erro:
        raise ConfigInvalidaError("ANEL", bruto, "inteiros separados por vírgula") from erro
    if len(ids) < 1:
        raise ConfigInvalidaError("ANEL", bruto, "ao menos um id")
    if len(set(ids)) != len(ids):
        raise ConfigInvalidaError("ANEL", bruto, "ids distintos")
    return ids


def _ler_urls(bruto: str | None) -> dict[int, str]:
    """Parseia "1=http://a:8000,2=http://b:8000" num mapa id -> url base."""
    if not bruto:
        raise ConfigInvalidaError("URLS", bruto, "mapa id=url separado por vírgula")
    mapa: dict[int, str] = {}
    for item in bruto.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ConfigInvalidaError("URLS", item, "cada item no formato id=url")
        chave, url = item.split("=", 1)
        try:
            no_id = int(chave.strip())
        except ValueError as erro:
            raise ConfigInvalidaError("URLS", chave, "id inteiro antes do '='") from erro
        url = url.strip()
        if not url:
            raise ConfigInvalidaError("URLS", item, "url não vazia após o '='")
        mapa[no_id] = url
    if not mapa:
        raise ConfigInvalidaError("URLS", bruto, "ao menos um mapeamento id=url")
    return mapa


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
    no_id = int(os.environ["NO_ID"])
    anel = _ler_anel(os.environ.get("ANEL"))
    urls = _ler_urls(os.environ.get("URLS"))
    porta = int(os.environ.get("PORTA", "8000"))
    semente = int(os.environ.get("SEMENTE", "0"))
    deadline = float(os.environ.get("DEADLINE", "1.0"))
    retries = int(os.environ.get("RETRIES", "2"))

    sucessores = tuple(i for i in anel if i != no_id)
    config = ConfigNo(
        id=no_id,
        algoritmo="anel",
        porta=porta,
        semente=semente,
        deadline=deadline,
        retries=retries,
        sucessores=sucessores,
    )
    config.validar()

    if no_id not in anel:
        raise ConfigInvalidaError("NO_ID", no_id, f"id presente em ANEL {anel}")
    faltando = [i for i in anel if i not in urls]
    if faltando:
        raise ConfigInvalidaError("URLS", urls, f"deve cobrir todos os ids do ANEL; faltam {faltando}")

    log_dir = Path(os.environ.get("LOG_DIR", "results"))
    log = abrir_log(log_dir / f"anel-no-{no_id}.jsonl", no_id)

    # Controlador de falhas dirigido pela Semente, plugado nas duas fronteiras
    # de rede do Nó. Registrado no log já com a Semente e o Plano_de_Falhas,
    # substituindo o `inicio_replica` sem Plano_de_Falhas.
    controlador = ControladorFalhas(semente, log=log)
    registrar_plano_no_log(
        controlador,
        log,
        cenario={"algoritmo": "anel", "anel": list(anel), "deadline": deadline},
    )

    estado = EstadoAnel(no_id=no_id, anel=anel)
    cliente = ClienteComFalhas(ClienteRede(retries=retries), controlador)
    no = NoAnel(estado, urls=urls, cliente=cliente, deadline=deadline, log=log)
    return no, config, porta, controlador


def main() -> None:
    """Valida a configuração do ambiente e serve o Nó com uvicorn."""
    import uvicorn

    try:
        no, config, porta, controlador = construir_no()
    except (KeyError, ValueError, ConfigInvalidaError) as erro:
        # Encerra antes de iniciar o Transporte, sem efeito de rede.
        raise SystemExit(f"configuração inválida do Nó do Anel: {erro}") from erro

    app = criar_app(no, controlador=controlador)
    print(f"[anel] Nó {config.id} escutando em 0.0.0.0:{porta} (anel={config.sucessores})")
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
