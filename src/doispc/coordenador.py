"""App FastAPI do Coordenador do 2PC — adaptador fino sobre a Máquina_de_Estados.

Como o Participante, este módulo **não reimplementa 2PC**. Ele é o adaptador de
rede (Requisito 7.1) que liga `src.doispc.maquina` ao Transporte com Deadline e
ao Log_Duravel. O Coordenador:

- em `POST /transacao`, chama `iniciar_transacao`, que devolve os PREPARE a
  difundir, e os despacha (Requisito 6.2);
- recebe os VOTO e os ACK em `POST /mensagem`, entregando-os a
  `coordenador_recebe`; ao chegar o último Voto, a Máquina_de_Estados decide
  COMMIT ou ABORT e devolve, **na ordem write-ahead**, a `GravarLog` da Decisao
  antes das `Enviar` de DECISAO (Requisito 6.3, 6.4, 6.7);
- se o Deadline de coleta de Votos estoura antes de todos votarem, chama
  `coordenador_decide_por_timeout`, que decide ABORT e difunde a Decisao
  (Requisito 7.5);
- retransmite a DECISAO a quem ainda não mandou o ACK, a cada
  `intervalo_reenvio`, numa thread própria;
- na partida, `recuperar` relê o Log_Duravel, aborta o que estava em WAIT e
  difunde de novo o que estava decidido.

Os Deadlines de Voto e de Decisao são configuráveis (Requisito 7.6): o de Voto
limita a espera pela coleta em `/transacao`; o de Decisao é o prazo de cada
envio de rede (PREPARE, DECISAO) feito pelo Transporte.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Mapping, Optional, Sequence

from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede
from src.doispc.executor import ExecutorAcoes
from src.doispc.log_duravel import LogDuravel, LogDuravelArquivo
from src.doispc.maquina import (
    Acao,
    Enviar,
    EstadoCoordenador,
    coordenador_decide_por_timeout,
    coordenador_recebe,
    difundir_decisao,
    iniciar_transacao,
    recuperar_coordenador,
)

# Espera entre duas retransmissões da DECISAO para quem não mandou o ACK, em s.
INTERVALO_REENVIO_PADRAO = 1.0


class ServicoCoordenador:
    """Núcleo do Coordenador, independente de FastAPI, para ser testável sem servidor.

    O estado de cada transação fica num mapa pelo id, e várias transações podem
    correr ao mesmo tempo. `participantes` mapeia id → `No` de cada
    Participante, usado tanto para difundir PREPARE quanto para resolver
    endereços no executor. `deadline_voto` limita a coleta de Votos
    (Requisito 7.5, 7.6); `deadline_decisao` é o prazo de cada envio de rede
    (Requisito 7.6). Um `Lock` serializa as transições, pois Votos e ACKs
    chegam em requisições concorrentes.

    A retransmissão envia direto da própria thread, e não pela fila de saída.
    Assim uma rodada só começa depois da anterior, e um Participante que não
    responde não acumula DECISAO na fila nem atrasa os PREPARE.
    """

    def __init__(
        self,
        coordenador: No,
        participantes: Mapping[int, No],
        *,
        log_duravel: LogDuravel,
        cliente: ClienteRede,
        deadline_voto: float,
        deadline_decisao: float,
        intervalo_reenvio: float = INTERVALO_REENVIO_PADRAO,
        relogio=time.monotonic,
        dormir=time.sleep,
        log: Optional[LogEstruturado] = None,
    ) -> None:
        if intervalo_reenvio <= 0:
            raise ValueError(
                f"intervalo_reenvio deve ser > 0 s; recebido {intervalo_reenvio!r}"
            )
        self._eu = coordenador
        self._participantes = dict(participantes)
        self._log_duravel = log_duravel
        self._cliente = cliente
        self._deadline_voto = deadline_voto
        self._deadline_decisao = deadline_decisao
        self._intervalo_reenvio = intervalo_reenvio
        self._relogio = relogio
        self._dormir = dormir
        self._log = log
        self._estados: dict[str, EstadoCoordenador] = {}
        # Instante da próxima retransmissão de cada transação decidida sem todos os ACKs.
        self._reenvios: dict[str, float] = {}
        self._ultima: Optional[str] = None
        self._trava = threading.Lock()
        enderecos = {p.id: p.base_url for p in self._participantes.values()}
        self._executor = ExecutorAcoes(
            no_id=coordenador.id,
            log_duravel=log_duravel,
            cliente=cliente,
            deadline=deadline_decisao,
            enderecos=enderecos,
            log=log,
        )
        threading.Thread(target=self._laco_reenvio, daemon=True).start()

    @property
    def estado(self) -> Optional[EstadoCoordenador]:
        """Estado da transação iniciada por último (ou `None` antes da primeira)."""
        return self._estados.get(self._ultima) if self._ultima is not None else None

    def iniciar(self, transacao: str) -> dict[str, Any]:
        """Inicia uma transação: grava a entrada em WAIT, difunde PREPARE e espera a Decisao.

        Retorna a Decisao final (COMMIT/ABORT). Se o Deadline de coleta de Votos
        estoura antes de todos votarem, decide ABORT (Requisito 7.5). A espera é
        por polling do estado, que é atualizado pelos VOTO que chegam em
        `/mensagem`; o `Lock` evita corrida entre a decisão por Voto e a decisão
        por timeout.

        Um id já usado não abre rodada nova, que poderia decidir diferente da
        primeira. Ele devolve a Decisao, ou espera a rodada em curso. Depois de
        um reinício, `recuperar` já trouxe do Log_Duravel todas as transações.
        """
        participantes_ids = tuple(sorted(self._participantes))
        with self._trava:
            if transacao not in self._estados:
                estado, acoes = iniciar_transacao(self._eu.id, transacao, participantes_ids)
                if self._log is not None:
                    self._log.evento(
                        "inicia_transacao",
                        transacao=transacao,
                        participantes=list(participantes_ids),
                    )
                self._ultima = transacao
                self._transitar(transacao, estado, acoes)
            elif self._log is not None:
                self._log.evento("transacao_repetida", transacao=transacao)

        return {
            "transacao": transacao,
            "decisao": self._esperar_decisao(transacao),
            "participantes": list(participantes_ids),
        }

    def _esperar_decisao(self, transacao: str) -> Optional[str]:
        """Aguarda a Decisao até o Deadline de Voto; ao estourar, decide ABORT.

        Os VOTO chegam de forma assíncrona em `/mensagem` e podem já ter levado
        a Máquina_de_Estados a decidir. Aqui apenas observamos: se a Decisao
        aparece dentro do prazo, retornamos; senão, forçamos o ABORT por timeout
        (Requisito 7.5). A granularidade do polling é pequena o bastante para
        não atrasar perceptivelmente a resposta.
        """
        limite = self._relogio() + self._deadline_voto
        while self._relogio() < limite:
            estado = self._estados.get(transacao)
            if estado is not None and estado.decidido:
                return estado.decisao
            self._dormir(0.01)

        # Deadline de coleta de Votos excedido: ABORT por timeout (Requisito 7.5).
        with self._trava:
            estado = self._estados.get(transacao)
            if estado is None:
                return None
            if estado.decidido:
                return estado.decisao
            if self._log is not None:
                self._log.evento("timeout_votos", transacao=transacao)
            novo, acoes = coordenador_decide_por_timeout(estado, self._eu.id)
            self._transitar(transacao, novo, acoes)
        return novo.decisao

    def receber(self, mensagem: Mensagem) -> Mapping[str, Any]:
        """Entrega um VOTO ou um ACK à Máquina_de_Estados e executa as `Acao`.

        A resposta é um ACK imediato de recebimento (Requisito 3.5). Ao completar
        a coleta de Votos, `coordenador_recebe` já devolve a `GravarLog` da
        Decisao antes das `Enviar`, mantendo o write-ahead (Requisito 6.4).
        """
        if self._log is not None:
            self._log.evento(
                "recebe",
                msg_tipo=mensagem.tipo,
                origem=mensagem.origem,
                transacao=mensagem.corpo.get("transacao"),
            )
        transacao = mensagem.corpo.get("transacao")
        with self._trava:
            estado = self._estados.get(transacao)
            if estado is None:
                return {"ack": False}
            novo, acoes = coordenador_recebe(estado, self._eu.id, mensagem)
            self._transitar(transacao, novo, acoes)
        return {"ack": True}

    def _transitar(
        self, transacao: str, novo: EstadoCoordenador, acoes: Sequence[Acao]
    ) -> None:
        """Grava e enfileira as `Acao` e só então publica o estado. Chamar com a trava.

        `_esperar_decisao` lê o estado sem a trava, então o cliente só vê uma
        Decisao que já está no Log_Duravel. Uma transação decidida entra na
        retransmissão e sai dela quando todos mandam o ACK.
        """
        self._executor.executar(acoes)
        self._estados[transacao] = novo
        if novo.concluido:
            self._reenvios.pop(transacao, None)
        elif novo.decidido:
            self._reenvios.setdefault(transacao, self._relogio() + self._intervalo_reenvio)

    def recuperar(self) -> None:
        """Relê o Log_Duravel na partida, antes de servir (Requisito 6.6).

        Cada transação do log passa por `recuperar_coordenador`: a decidida é
        difundida de novo, e a que estava em WAIT vira ABORT, gravado e difundido.
        """
        participantes_ids = tuple(sorted(self._participantes))
        with self._trava:
            for transacao in self._log_duravel.transacoes():
                persistido = self._log_duravel.recuperar(transacao)
                estado, acoes = recuperar_coordenador(persistido, self._eu.id, participantes_ids)
                if self._log is not None:
                    self._log.evento(
                        "recuperacao", transacao=transacao, decisao=persistido.decisao
                    )
                self._ultima = transacao
                self._transitar(transacao, estado, acoes)

    def _laco_reenvio(self) -> None:
        """Roda `_reenviar_vencidas` para sempre, na thread de retransmissão."""
        espera = self._intervalo_reenvio
        while True:
            self._dormir(espera)
            espera = self._reenviar_vencidas()

    def _reenviar_vencidas(self) -> float:
        """Reenvia a DECISAO das transações vencidas e devolve a espera até a próxima.

        Cada envio tem o prazo do Transporte. O ACK que tira o Participante da
        lista é a Mensagem do protocolo, que chega por `receber`, e não o 2xx.
        """
        agora = self._relogio()
        acoes: list[Enviar] = []
        with self._trava:
            for transacao, vence in self._reenvios.items():
                if vence > agora:
                    continue
                pendentes = difundir_decisao(self._estados[transacao], self._eu.id)
                if self._log is not None:
                    self._log.evento(
                        "reenvia_decisao",
                        transacao=transacao,
                        destinos=[a.mensagem.destino for a in pendentes],
                    )
                acoes.extend(pendentes)
                self._reenvios[transacao] = agora + self._intervalo_reenvio
            proxima = min(self._reenvios.values(), default=agora + self._intervalo_reenvio)
        for acao in acoes:
            self._executor.enviar(acao)
        return max(proxima - self._relogio(), 0.0)

    def instantaneo(self) -> dict[str, Any]:
        """Resumo serializável da transação iniciada por último, para o endpoint `/estado`."""
        estado = self.estado
        return {
            "no": self._eu.id,
            "participantes": sorted(self._participantes),
            "transacao": estado.transacao if estado else None,
            "votos": dict(estado.votos) if estado else {},
            "decisao": estado.decisao if estado else None,
            "concluido": estado.concluido if estado else False,
        }


def _no_de_env(prefixo: str, padrao_porta: int) -> No:
    """Lê a identidade de um Nó (id/host/porta) das variáveis de ambiente."""
    return No(
        id=int(os.environ[f"{prefixo}_ID"]),
        host=os.environ.get(f"{prefixo}_HOST", "0.0.0.0"),
        porta=int(os.environ.get(f"{prefixo}_PORTA", str(padrao_porta))),
    )


def _participantes_de_env() -> dict[int, No]:
    """Lê os Participantes de `DOISPC_PARTICIPANTES` = "id@host:porta,id@host:porta"."""
    bruto = os.environ.get("DOISPC_PARTICIPANTES", "")
    participantes: dict[int, No] = {}
    for item in bruto.split(","):
        item = item.strip()
        if not item:
            continue
        ident, endereco = item.split("@", 1)
        host, porta = endereco.split(":", 1)
        no = No(id=int(ident), host=host, porta=int(porta))
        participantes[no.id] = no
    if not participantes:
        raise ValueError(
            "DOISPC_PARTICIPANTES vazio; esperado 'id@host:porta,...'"
        )
    return participantes


def criar_servico_de_env() -> ServicoCoordenador:
    """Monta um `ServicoCoordenador` a partir do ambiente (usado pelo contêiner).

    Variáveis esperadas: `COORD_ID`, `COORD_PORTA`, `DOISPC_PARTICIPANTES`,
    `DOISPC_DEADLINE_VOTO`, `DOISPC_DEADLINE_DECISAO`, `DOISPC_LOG_DURAVEL`.
    """
    eu = _no_de_env("COORD", 8000)
    participantes = _participantes_de_env()
    deadline_voto = float(os.environ.get("DOISPC_DEADLINE_VOTO", "3.0"))
    deadline_decisao = float(os.environ.get("DOISPC_DEADLINE_DECISAO", "2.0"))
    caminho_log = os.environ.get("DOISPC_LOG_DURAVEL", "/dados/coordenador.log")
    log_duravel = LogDuravelArquivo(caminho_log)
    cliente = ClienteRede()
    return ServicoCoordenador(
        eu,
        participantes,
        log_duravel=log_duravel,
        cliente=cliente,
        deadline_voto=deadline_voto,
        deadline_decisao=deadline_decisao,
    )


def criar_app(servico: Optional[ServicoCoordenador] = None, *, controlador=None):
    """Cria o app FastAPI do Coordenador em volta de um `ServicoCoordenador`.

    Expõe `POST /mensagem` (VOTO/ACK), `POST /transacao` (inicia um commit,
    Requisito 7.1) e `GET /estado`. Importa FastAPI localmente para preservar a
    fronteira de dependência.

    Com um `controlador` de falhas, o recebimento de VOTO/ACK passa pelo
    controlador antes da Máquina e os endpoints `/falha/*` ficam expostos
    (Requisito 8.3). Com o crash do controlador ligado, o despacho da DECISAO é
    suprimido (quando o `ClienteComFalhas` embrulha o cliente) e o `/mensagem`
    devolve 503. Os Participantes que votaram SIM ficam em Estado_Em_Duvida
    (Requisito 8.5) até o crash ser limpo, e então a retransmissão os alcança.
    """
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    from src.comum.rede import CAMINHO_MENSAGEM

    if servico is None:
        servico = criar_servico_de_env()

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

    @app.post("/transacao")
    async def transacao(corpo: dict = Body(default={})) -> JSONResponse:  # pragma: no cover - via servidor
        import uuid

        tx = str((corpo or {}).get("transacao") or f"tx-{uuid.uuid4().hex[:8]}")
        # `iniciar` bloqueia até a Decisao; roda em thread para não travar o loop.
        import anyio

        resultado = await anyio.to_thread.run_sync(servico.iniciar, tx)
        return JSONResponse(content=resultado)

    @app.get("/estado")
    async def estado() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content=servico.instantaneo())

    @app.get("/saude")
    async def saude() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content={"ok": True, "papel": "coordenador", "no": servico._eu.id})

    if controlador is not None:
        from src.comum.falhas import registrar_endpoints_falha

        registrar_endpoints_falha(app, controlador)

    return app


def _app_de_env():  # pragma: no cover - caminho de contêiner
    return criar_app()
