"""App FastAPI do Participante do 2PC — adaptador fino sobre a Máquina_de_Estados.

Este módulo **não reimplementa 2PC**. Ele é o adaptador de rede
que liga a Máquina_de_Estados pura de `src.doispc.maquina` ao Transporte com
Deadline de `src.comum.rede` e ao `Log_Duravel` de `src.doispc.log_duravel`.
O fluxo é sempre o mesmo:

1. recebe uma Mensagem (PREPARE ou DECISAO) por `POST /mensagem`;
2. entrega a Mensagem a `participante_recebe`, que devolve o novo estado e uma
   lista ordenada de `Acao`;
3. executa as `Acao` **na ordem em que vêm**, e nessa ordem está codificada a
   regra write-ahead: uma `GravarLog` sempre precede o `Aplicar`/`Enviar` que
   dependem dela. O adaptador não precisa lembrar a regra;
   basta obedecer à sequência.

O Participante pode ser configurado para votar NAO: o
`voto_desejado` é fixado na construção a partir de uma flag (env `DOISPC_VOTO`),
e repassado à Máquina_de_Estados a cada PREPARE. Todo o resto — atomicidade,
não-reversão, recuperação — já está na lógica pura e é apenas exercido aqui.

A recuperação após reinício relê o Log_Duravel na partida e
reconstrói o estado com `recuperar_participante`, de modo que um Participante
reiniciado reporte a mesma Decisao de antes.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede
from src.doispc.executor import ExecutorAcoes
from src.doispc.log_duravel import VOTO_SIM, LogDuravel
from src.doispc.maquina import (
    EstadoParticipante,
    participante_recebe,
    recuperar_participante,
)


class ServicoParticipante:
    """Núcleo do Participante, independente de FastAPI, para ser testável sem servidor.

    Guarda o estado de cada transação num mapa pelo id, o `voto_desejado`
    configurado e as dependências de efeito colateral (Log_Duravel e
    Transporte), delegando toda a decisão à Máquina_de_Estados, que trata uma
    transação por estado.
    """

    def __init__(
        self,
        participante: No,
        coordenador: No,
        *,
        log_duravel: LogDuravel,
        cliente: ClienteRede,
        deadline: float,
        voto_desejado: str = VOTO_SIM,
        log: Optional[LogEstruturado] = None,
    ) -> None:
        self._eu = participante
        self._coordenador = coordenador
        self._voto_desejado = voto_desejado
        self._log_duravel = log_duravel
        self._log = log
        self._estados: dict[str, EstadoParticipante] = {}
        self._ultima: Optional[str] = None
        # O Participante só fala com o Coordenador; o executor resolve o id do
        # destino para a URL do Coordenador.
        self._executor = ExecutorAcoes(
            no_id=participante.id,
            log_duravel=log_duravel,
            cliente=cliente,
            deadline=deadline,
            enderecos={coordenador.id: coordenador.base_url},
            log=log,
        )

    @property
    def estado(self) -> Optional[EstadoParticipante]:
        """Estado da transação vista por último (ou `None` antes da primeira)."""
        return self._estados.get(self._ultima) if self._ultima is not None else None

    def _guardar(self, transacao: str, estado: EstadoParticipante) -> None:
        """Guarda o estado da transação. A última é a mais recente a aparecer pela primeira vez."""
        if transacao not in self._estados:
            self._ultima = transacao
        self._estados[transacao] = estado

    def recuperar(self, transacao: str) -> Optional[EstadoParticipante]:
        """Reconstrói o estado a partir do Log_Duravel após reinício."""
        persistido = self._log_duravel.recuperar(transacao)
        if persistido.voto is None and persistido.decisao is None:
            return None
        estado = recuperar_participante(persistido)
        self._guardar(transacao, estado)
        if self._log is not None:
            self._log.evento(
                "recuperacao",
                transacao=transacao,
                voto=estado.voto,
                decisao=estado.decisao,
                em_duvida=estado.em_duvida,
            )
        return estado

    def receber(self, mensagem: Mensagem) -> Mapping[str, Any]:
        """Entrega a Mensagem à Máquina_de_Estados e executa as `Acao` devolvidas.

        A resposta HTTP é um ACK imediato de recebimento. As
        Mensagens de saída (o VOTO, o ACK da DECISAO) vão para a fila do executor
        e saem depois da resposta. O estado novo só é publicado depois de gravado.
        """
        if self._log is not None:
            self._log.evento(
                "recebe",
                msg_tipo=mensagem.tipo,
                origem=mensagem.origem,
                transacao=mensagem.corpo.get("transacao"),
            )
        transacao = str(mensagem.corpo.get("transacao", ""))
        novo, acoes = participante_recebe(
            self._estados.get(transacao),
            self._eu.id,
            self._coordenador.id,
            mensagem,
            voto_desejado=self._voto_desejado,
        )
        self._executor.executar(acoes)
        self._guardar(transacao, novo)
        return {"ack": True}

    def instantaneo(self) -> dict[str, Any]:
        """Resumo serializável da transação vista por último, para o endpoint `/estado`."""
        estado = self.estado
        return {
            "no": self._eu.id,
            "voto_desejado": self._voto_desejado,
            "transacao": estado.transacao if estado else None,
            "voto": estado.voto if estado else None,
            "decisao": estado.decisao if estado else None,
            "aplicada": estado.aplicada if estado else False,
            "em_duvida": estado.em_duvida if estado else False,
        }


def criar_app(servico: ServicoParticipante, *, controlador=None):
    """Cria o app FastAPI do Participante em volta de um `ServicoParticipante`.

    Importa FastAPI localmente, mantendo a fronteira: importar este módulo não
    exige FastAPI para quem só quer o `ServicoParticipante` (por exemplo, testes
    de integração que falam direto com o serviço).

    Com um `controlador` de falhas, o recebimento de PREPARE/DECISAO passa pelo
    controlador antes da Máquina (drop/delay/pause/crash), os endpoints
    `/falha/*` são expostos e um crash do Participante devolve
    503. Um crash do Coordenador no meio da coleta deixa este Participante em
    Estado_Em_Duvida, que já é comportamento da Máquina pura.
    """
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    from src.comum.rede import CAMINHO_MENSAGEM

    app = FastAPI()

    receber_callback = servico.receber
    if controlador is not None:
        from src.comum.falhas import envolver_recebimento

        receber_callback = envolver_recebimento(controlador, servico.receber)

    @app.post(CAMINHO_MENSAGEM)
    async def receber(corpo: dict = Body(...)) -> JSONResponse:  # pragma: no cover - via servidor
        from src.comum.falhas import NoEmCrashError

        mensagem = Mensagem.de_json(corpo)
        try:
            return JSONResponse(content=dict(receber_callback(mensagem)))
        except NoEmCrashError as erro:
            raise HTTPException(status_code=503, detail=str(erro)) from erro

    @app.get("/estado")
    async def estado() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content=servico.instantaneo())

    @app.get("/saude")
    async def saude() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content={"ok": True, "papel": "participante", "no": servico._eu.id})

    if controlador is not None:
        from src.comum.falhas import registrar_endpoints_falha

        registrar_endpoints_falha(app, controlador)

    return app
