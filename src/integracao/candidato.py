"""App do candidato: um Nó do Anel e um Coordenador do 2PC atrás do mesmo `/mensagem`.

Só o Líder aceita transação, e o id dela leva o prefixo do candidato. A safety não depende de haver
um Líder só, porque dois candidatos que se julgam Líderes nunca criam o mesmo id.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from src.anel.maquina import TIPO_ELEICAO, TIPO_LIDER
from src.anel.no import NoAnel
from src.comum.falhas import ControladorFalhas, recebedor_http, registrar_endpoints_falha
from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem
from src.comum.rede import criar_app_recebedor
from src.doispc.coordenador import ServicoCoordenador
from src.doispc.maquina import MSG_ACK, MSG_VOTO

# O candidato recebe do Anel a eleição e o anúncio, e dos Participantes o Voto e o ACK.
TIPOS_ANEL = frozenset({TIPO_ELEICAO, TIPO_LIDER})
TIPOS_COORDENADOR = frozenset({MSG_VOTO, MSG_ACK})


def id_da_transacao(candidato: int, nome: str | None) -> str:
    """`c<candidato>-<nome>`, com 8 hex aleatórios sem nome.

    O nome que já leva o prefixo fica igual, para o cliente poder repetir o id devolvido.
    """
    prefixo = f"c{candidato}-"
    if not nome:
        return f"{prefixo}{uuid.uuid4().hex[:8]}"
    return nome if nome.startswith(prefixo) else prefixo + nome


def criar_app(
    no: NoAnel,
    servico: ServicoCoordenador,
    *,
    log: LogEstruturado,
    controlador: ControladorFalhas | None = None,
):
    """App do candidato: `/mensagem` despacha pelo tipo e `/transacao` só no Líder.

    Um controlador só para o processo faz o crash calar o Nó e o Coordenador juntos.
    """
    import anyio
    from fastapi import Body, HTTPException
    from fastapi.responses import JSONResponse

    candidato = no.estado.no_id

    def despachar(mensagem: Mensagem) -> Mapping[str, Any]:
        if mensagem.tipo in TIPOS_ANEL:
            return no.processar_mensagem(mensagem)
        if mensagem.tipo in TIPOS_COORDENADOR:
            return servico.receber(mensagem)
        raise HTTPException(
            status_code=400,
            detail=f"tipo {mensagem.tipo!r} não é do anel ({', '.join(sorted(TIPOS_ANEL))}) "
            f"nem do Coordenador ({', '.join(sorted(TIPOS_COORDENADOR))})",
        )

    app = criar_app_recebedor(recebedor_http(controlador, despachar))
    if controlador is not None:
        registrar_endpoints_falha(app, controlador)

    @app.post("/transacao")
    async def transacao(corpo: dict = Body(default={})) -> JSONResponse:
        nome = (corpo or {}).get("transacao")
        lider = no.estado.lider
        if lider != candidato:
            log.evento("transacao_recusada", nome=nome, lider=lider)
            return JSONResponse(status_code=409, content=_recusa(candidato, lider))
        tx = id_da_transacao(candidato, str(nome) if nome else None)
        # `iniciar` bloqueia até a Decisao, então roda em thread para não travar o loop.
        resultado = await anyio.to_thread.run_sync(servico.iniciar, tx)
        return JSONResponse(content=resultado)

    @app.post("/iniciar-eleicao")
    async def iniciar_eleicao() -> dict[str, Any]:
        return no.disparar_eleicao()

    @app.get("/estado")
    async def estado(transacao: str | None = None) -> dict[str, Any]:
        return {**no.estado_atual(), **servico.instantaneo(transacao)}

    @app.get("/saude")
    async def saude() -> dict[str, Any]:
        return {"ok": True, "papel": "candidato", "no": candidato}

    return app


def _recusa(candidato: int, lider: int | None) -> dict[str, Any]:
    """Corpo do 409: a quem mandar a transação ou, sem Líder conhecido, como eleger um."""
    if lider is None:
        erro = (
            f"o candidato {candidato} não conhece o Líder. "
            "Dispare POST /iniciar-eleicao em qualquer candidato e repita."
        )
    else:
        erro = f"o candidato {candidato} não é o Líder. Envie a transação ao candidato {lider}."
    return {"candidato": candidato, "lider": lider, "erro": erro}
