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

O Voto e o ACK vão ao Coordenador que mandou a Mensagem, desde que ele esteja no mapa de
Coordenadores conhecidos. Mensagem de origem desconhecida é recusada antes da Máquina.

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
        coordenadores: Mapping[int, No],
        *,
        log_duravel: LogDuravel,
        cliente: ClienteRede,
        deadline: float,
        voto_desejado: str = VOTO_SIM,
        log: Optional[LogEstruturado] = None,
    ) -> None:
        if not coordenadores:
            raise ValueError(
                f"o Participante {participante.id} precisa de ao menos um Coordenador. "
                "Defina DOISPC_COORDENADORES ou COORD_ID."
            )
        self._eu = participante
        self._coordenadores = dict(coordenadores)
        self._voto_desejado = voto_desejado
        self._log_duravel = log_duravel
        self._log = log
        self._estados: dict[str, EstadoParticipante] = {}
        self._ultima: Optional[str] = None
        # O Participante só fala com Coordenadores, e o executor resolve o id da origem para a URL.
        self._executor = ExecutorAcoes(
            no_id=participante.id,
            log_duravel=log_duravel,
            cliente=cliente,
            deadline=deadline,
            enderecos={cid: no.base_url for cid, no in self._coordenadores.items()},
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
        A origem desconhecida grava `origem_desconhecida`, e não `recebe`, para ficar fora do
        invariante 07.
        """
        if mensagem.origem not in self._coordenadores:
            conhecidos = sorted(self._coordenadores)
            if self._log is not None:
                self._log.evento(
                    "origem_desconhecida",
                    msg_tipo=mensagem.tipo,
                    origem=mensagem.origem,
                    transacao=mensagem.corpo.get("transacao"),
                    conhecidos=conhecidos,
                )
            return {
                "ack": False,
                "erro": f"origem {mensagem.origem} não é Coordenador conhecido {conhecidos}. "
                "Confira DOISPC_COORDENADORES ou COORD_ID.",
            }
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
            mensagem.origem,
            mensagem,
            voto_desejado=self._voto_desejado,
        )
        self._executor.executar(acoes)
        self._guardar(transacao, novo)
        return {"ack": True}

    def instantaneo(self, transacao: Optional[str] = None) -> dict[str, Any]:
        """Resumo de uma transação para o `/estado`. Sem `transacao`, a vista por último."""
        estado = self.estado if transacao is None else self._estados.get(transacao)
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
    from fastapi.responses import JSONResponse

    from src.comum.falhas import recebedor_http
    from src.comum.rede import criar_app_recebedor

    app = criar_app_recebedor(recebedor_http(controlador, servico.receber))

    @app.get("/estado")
    async def estado(  # pragma: no cover - via servidor
        transacao: Optional[str] = None,
    ) -> JSONResponse:
        return JSONResponse(content=servico.instantaneo(transacao))

    @app.get("/saude")
    async def saude() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content={"ok": True, "papel": "participante", "no": servico._eu.id})

    if controlador is not None:
        from src.comum.falhas import registrar_endpoints_falha

        registrar_endpoints_falha(app, controlador)

    return app
