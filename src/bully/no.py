"""Nó do Bully sobre a rede: adaptador FastAPI da Máquina_de_Estados pura.

Cada destino tem a sua fila de saída, para que as entregas a um Nó morto, que esgotam o Deadline,
não atrasem as Mensagens aos vivos. Os prazos que a máquina pede viram temporizadores que voltam a
ela como eventos.
"""

from __future__ import annotations

import threading
from functools import partial
from typing import Any, Callable, Mapping

from src.bully.maquina import Acao, EstadoBully, Prazo, iniciar_eleicao, prazo_expirou, receber
from src.comum.despacho import Despachante
from src.comum.falhas import ControladorFalhas, recebedor_http, registrar_endpoints_falha
from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem
from src.comum.rede import ClienteRede, criar_app_recebedor, entregar

GATILHO_INICIO = "inicio"
GATILHO_PEDIDO = "pedido"

Agendar = Callable[[float, Callable[[], None]], None]


def _agendar_com_timer(segundos: float, acao: Callable[[], None]) -> None:
    """Roda `acao` depois de `segundos`, numa thread daemon."""
    timer = threading.Timer(segundos, acao)
    timer.daemon = True
    timer.start()


class NoBully:
    """Guarda o `EstadoBully`, entrega cada evento à máquina e executa as ações que ela devolve.

    `prazos` dá os segundos de cada tipo de `Prazo`. `bully_iniciou` e `bully_lider` são gravados
    sob o lock que troca o estado, então a ordem no arquivo é a ordem das trocas.
    """

    def __init__(
        self,
        estado: EstadoBully,
        *,
        urls: Mapping[int, str],
        cliente: ClienteRede,
        deadline: float,
        prazos: Mapping[str, float],
        log: LogEstruturado | None = None,
        agendar: Agendar = _agendar_com_timer,
    ) -> None:
        self._estado = estado
        self._cliente = cliente
        self._deadline = deadline
        self._prazos = dict(prazos)
        self._log = log
        self._agendar = agendar
        self._lock = threading.Lock()
        self._parado = False
        self._saidas = {
            destino: Despachante(partial(self._despachar_uma, urls[destino]))
            for destino in estado.ids
            if destino != estado.no_id
        }

    def parar(self) -> None:
        """Passa a ignorar os prazos e encerra as filas depois de esvaziá-las."""
        with self._lock:
            self._parado = True
        for saida in self._saidas.values():
            saida.parar()

    def drenar(self) -> None:
        """Bloqueia até todas as filas de saída esvaziarem."""
        for saida in self._saidas.values():
            saida.drenar()

    @property
    def estado(self) -> EstadoBully:
        with self._lock:
            return self._estado

    def estado_atual(self) -> dict[str, Any]:
        """Representação JSON do estado, para `GET /estado`."""
        est = self.estado
        return {
            "no_id": est.no_id,
            "ids": list(est.ids),
            "lider": est.lider,
            "fase": est.fase,
            "epoca": est.epoca,
        }

    def processar_mensagem(self, mensagem: Mensagem) -> dict[str, Any]:
        """Entrega a Mensagem à máquina e responde antes de qualquer envio."""
        with self._lock:
            self._registrar(
                "bully_recebeu",
                msg_tipo=mensagem.tipo,
                origem=mensagem.origem,
                corpo=dict(mensagem.corpo),
            )
            acoes = self._trocar(receber(self._estado, mensagem), mensagem.tipo)
        self._executar(acoes)
        return {"ack": True, "no_id": self.estado.no_id}

    def disparar_eleicao(self, gatilho: str) -> dict[str, Any]:
        """Convoca uma eleição, o que nada faz se o Nó já está numa, e devolve o estado."""
        with self._lock:
            acoes = self._trocar(iniciar_eleicao(self._estado), gatilho)
        self._executar(acoes)
        return self.estado_atual()

    def _ao_expirar(self, prazo: Prazo) -> None:
        with self._lock:
            if self._parado:
                return
            acoes = self._trocar(prazo_expirou(self._estado, prazo), f"prazo_{prazo.tipo}")
        self._executar(acoes)

    def _trocar(self, resultado: tuple[EstadoBully, list[Acao]], gatilho: str) -> list[Acao]:
        """Troca o estado e registra a convocação e a troca de Líder. Só roda sob o lock."""
        anterior = self._estado
        novo, acoes = resultado
        self._estado = novo
        if novo.epoca != anterior.epoca:
            self._registrar("bully_iniciou", epoca=novo.epoca, gatilho=gatilho)
        if novo.lider != anterior.lider:
            self._registrar(
                "bully_lider",
                lider=novo.lider,
                anterior=anterior.lider,
                epoca=novo.epoca,
                gatilho=gatilho,
            )
        return acoes

    def _executar(self, acoes: list[Acao]) -> None:
        for acao in acoes:
            if isinstance(acao, Prazo):
                self._agendar(self._prazos[acao.tipo], partial(self._ao_expirar, acao))
            else:
                self._saidas[acao.destino].enfileirar([acao])

    def _despachar_uma(self, url: str, mensagem: Mensagem) -> None:
        """Entrega com reenvio dentro do Deadline.

        A falha só vai ao log, porque o prazo que a máquina pediu já cobre o silêncio do destino.
        """
        motivo = entregar(self._cliente, url, mensagem, self._deadline)
        if motivo is not None:
            self._registrar(
                "bully_envio_falhou",
                msg_tipo=mensagem.tipo,
                destino=mensagem.destino,
                motivo=motivo,
            )

    def _registrar(self, tipo: str, **campos: Any) -> None:
        if self._log is not None:
            self._log.evento(tipo, **campos)


def criar_app(no: NoBully, *, controlador: ControladorFalhas | None = None):
    """App FastAPI do Nó, com os endpoints `/falha/*` quando há `controlador`."""
    app = criar_app_recebedor(recebedor_http(controlador, no.processar_mensagem))
    if controlador is not None:
        registrar_endpoints_falha(app, controlador)

    @app.get("/estado")
    async def estado() -> dict[str, Any]:  # pragma: no cover - exercido via servidor
        return no.estado_atual()

    @app.post("/iniciar-eleicao")
    async def iniciar() -> dict[str, Any]:  # pragma: no cover - exercido via servidor
        return no.disparar_eleicao(GATILHO_PEDIDO)

    @app.get("/saude")
    async def saude() -> dict[str, Any]:  # pragma: no cover - healthcheck do compose
        return {"ok": True, "no_id": no.estado.no_id}

    return app
