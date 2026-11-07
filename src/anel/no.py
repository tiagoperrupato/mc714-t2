"""Nó do Anel sobre a rede: adaptador fino FastAPI sobre a Máquina_de_Estados pura.

Este módulo é a camada de rede do algoritmo de Eleição em Anel (Requisito 5.1).
Ele NÃO reimplementa a lógica de eleição: toda decisão é delegada a
`src.anel.maquina` (funções puras sobre `EstadoAnel`), e o único papel deste
adaptador é traduzir Mensagens HTTP recebidas em chamadas à Máquina_de_Estados e
despachar as Mensagens de saída pelo `ClienteRede` do Transporte (Requisito 3.6).

Responsabilidades, e só elas:

- Expor `POST /mensagem` (via `criar_app_recebedor`), que entrega cada Mensagem
  recebida a `receber` e enfileira o que a Máquina devolver para despacho.
- Expor os endpoints de controle `GET /estado` e `POST /iniciar-eleicao`
  (Requisito 5.1).
- Ao repassar uma Mensagem ao Sucessor, se o envio retorna `EnvioTimeout` (ou
  conexão recusada / indisponível, que também sinalizam Sucessor inalcançável),
  chamar `com_sucessor_falho` para implementar a regra de salto do Anel
  (Requisitos 4.6, 5.5). A decisão de saltar é da Máquina; o adaptador apenas
  reconhece o resultado tipado de transporte.

Despacho fora do caminho da resposta HTTP (Requisito 3.5). Um ponto sutil, mas
essencial para a correção: o repasse ao Sucessor NÃO acontece dentro do handler
HTTP que recebeu a Mensagem. Se acontecesse, o handler de cada Nó ficaria
bloqueado esperando o repasse ao próximo, que por sua vez espera o seguinte, e
assim por diante — uma cadeia de chamadas HTTP aninhadas e síncronas. Numa
circulação de Anel, isso estoura o Deadline de cada hop e dispara a regra de
salto por engano, com Nós vivos sendo marcados como mortos. Para evitar isso, as
Mensagens de saída vão para uma fila processada por um worker em background; a
resposta HTTP é um ACK imediato de recebimento, desacoplado do término das
rodadas subsequentes.

A Máquina continua pura: o lock protege apenas a troca atômica do `EstadoAnel`
corrente por um novo valor, e a fila/worker são mecanismo de transporte, não
lógica de eleição.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Mapping

from src.anel.maquina import (
    EstadoAnel,
    com_sucessor_falho,
    iniciar_eleicao,
    receber,
)
from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem
from src.comum.rede import ClienteRede, EnvioOk


class NoAnel:
    """Adaptador de rede de um Nó do Anel.

    Guarda o `EstadoAnel` corrente, um `ClienteRede` para despachar Mensagens, o
    mapa de ids para URLs base dos outros Nós e o Deadline a aplicar em cada
    repasse. Um worker em background consome uma fila de Mensagens de saída e as
    envia, aplicando a regra de salto no timeout. Os métodos `processar_mensagem`,
    `disparar_eleicao` e `estado_atual` são o que os endpoints HTTP chamam; eles
    delegam à Máquina_de_Estados e só enfileiram a saída, nunca reimplementando a
    eleição nem bloqueando na rede.
    """

    def __init__(
        self,
        estado: EstadoAnel,
        *,
        urls: Mapping[int, str],
        cliente: ClienteRede,
        deadline: float,
        log: LogEstruturado | None = None,
        iniciar_worker: bool = True,
    ) -> None:
        if deadline <= 0:
            raise ValueError(f"deadline deve ser > 0; recebido {deadline!r}")
        self._estado = estado
        self._urls = dict(urls)
        self._cliente = cliente
        self._deadline = deadline
        self._log = log
        self._lock = threading.Lock()
        self._saida: "queue.Queue[Mensagem | None]" = queue.Queue()
        self._worker: threading.Thread | None = None
        if iniciar_worker:
            self.iniciar()

    # -- Ciclo de vida do worker de despacho -------------------------------

    def iniciar(self) -> None:
        """Inicia o worker de despacho em background, se ainda não está rodando."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._laco_despacho, daemon=True)
        self._worker.start()

    def parar(self) -> None:
        """Sinaliza o worker a encerrar e aguarda, drenando a fila."""
        self._saida.put(None)
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def drenar(self, timeout: float = 10.0) -> None:
        """Bloqueia até a fila de saída esvaziar — útil em testes determinísticos."""
        self._saida.join()

    # -- Consulta de estado (Requisito 5.1) --------------------------------

    @property
    def estado(self) -> EstadoAnel:
        """Snapshot imutável do estado corrente (seguro para leitura concorrente)."""
        with self._lock:
            return self._estado

    def estado_atual(self) -> dict[str, Any]:
        """Representação JSON do estado para o endpoint `GET /estado`."""
        est = self.estado
        return {
            "no_id": est.no_id,
            "anel": list(est.anel),
            "lider": est.lider,
            "mortos": sorted(est.mortos),
            "sucessor": est.meu_sucessor,
            "rodadas_concluidas": sorted(est.rodadas_concluidas),
        }

    # -- Entradas que mudam o estado ---------------------------------------

    def processar_mensagem(self, mensagem: Mensagem) -> dict[str, Any]:
        """Entrega uma Mensagem recebida à Máquina e enfileira a saída (Requisito 5.1).

        A resposta HTTP é um ACK imediato de recebimento; ela não espera o
        término das rodadas que o despacho venha a provocar (Requisito 3.5). O
        envio em si fica a cargo do worker de background.
        """
        with self._lock:
            novo, saida = receber(self._estado, mensagem)
            self._estado = novo
        if self._log is not None:
            self._log.evento(
                "recebeu",
                msg_tipo=mensagem.tipo,
                origem=mensagem.origem,
                corpo=dict(mensagem.corpo),
            )
        self._enfileirar(saida)
        return {"ack": True, "no_id": self.estado.no_id}

    def disparar_eleicao(self) -> dict[str, Any]:
        """Dispara uma Rodada_de_Eleicao neste Nó e enfileira a saída (Requisito 5.1).

        Delega a `iniciar_eleicao`; se este é o único Nó vivo, a Máquina já o
        define Líder trivial e não há nada a despachar.
        """
        with self._lock:
            novo, saida = iniciar_eleicao(self._estado)
            self._estado = novo
        if self._log is not None:
            self._log.evento("iniciou_eleicao", no_id=self.estado.no_id)
        self._enfileirar(saida)
        return {"iniciada": True, "no_id": self.estado.no_id, "lider": self.estado.lider}

    # -- Despacho em background com a regra de salto (Requisitos 4.6, 5.5) --

    def _enfileirar(self, saida: list[Mensagem]) -> None:
        for mensagem in saida:
            self._saida.put(mensagem)

    def _laco_despacho(self) -> None:
        """Loop do worker: consome Mensagens da fila e as envia uma a uma."""
        while True:
            mensagem = self._saida.get()
            try:
                if mensagem is None:
                    return
                self._despachar_uma(mensagem)
            finally:
                self._saida.task_done()

    def _despachar_uma(self, mensagem: Mensagem) -> None:
        """Envia uma Mensagem ao destino; no fracasso, aplica o salto e reenfileira.

        Se o destino está inalcançável (timeout, conexão recusada ou
        indisponível), chama `com_sucessor_falho` para que a Máquina marque o
        Sucessor como morto e reemita a MESMA Mensagem ao próximo Nó vivo. As
        Mensagens reemitidas voltam para a fila, preservando a circulação sem
        recursão nem bloqueio do caminho HTTP.
        """
        destino_url = self._urls.get(mensagem.destino)
        if destino_url is None:
            self._enfileirar(self._tratar_falha(mensagem, mensagem.destino, motivo="sem_url"))
            return
        resultado = self._cliente.enviar(destino_url, mensagem, self._deadline)
        if isinstance(resultado, EnvioOk):
            if self._log is not None:
                self._log.evento("repassou", msg_tipo=mensagem.tipo, destino=mensagem.destino)
            return
        # Qualquer resultado não-OK sinaliza Sucessor inalcançável: salta.
        self._enfileirar(
            self._tratar_falha(mensagem, mensagem.destino, motivo=type(resultado).__name__)
        )

    def _tratar_falha(self, mensagem: Mensagem, sucessor_falho: int, *, motivo: str) -> list[Mensagem]:
        """Aplica `com_sucessor_falho` sob o lock e devolve a Mensagem reemitida."""
        with self._lock:
            novo, reemitidas = com_sucessor_falho(self._estado, mensagem, sucessor_falho)
            self._estado = novo
        if self._log is not None:
            self._log.evento(
                "sucessor_falho",
                sucessor=sucessor_falho,
                motivo=motivo,
                reemitidas=len(reemitidas),
            )
        return reemitidas


def criar_app(no: NoAnel, *, controlador=None):
    """Monta o app FastAPI de um Nó do Anel sobre um `NoAnel`.

    Registra `POST /mensagem` (via o helper do Transporte, que desacopla a
    resposta HTTP da lógica) e os endpoints de controle `GET /estado` e
    `POST /iniciar-eleicao` (Requisito 5.1). A lógica de eleição vive em
    `src.anel.maquina`; este app apenas a expõe pela rede.

    Quando um `controlador` de falhas é fornecido, o recebimento de Mensagens
    passa por ele antes de chegar à Máquina (drop/delay/pause/crash), os
    endpoints `/falha/*` são expostos para controle em runtime (Requisito 8.3),
    e um crash devolve HTTP 503 (o Nó "deixa de responder"). O despacho de saída
    é interceptado passando um `ClienteComFalhas` ao `NoAnel` na construção; a
    Máquina continua pura (Requisito 8.1).
    """
    from src.comum.rede import criar_app_recebedor

    callback = no.processar_mensagem
    if controlador is not None:
        from src.comum.falhas import NoEmCrashError, envolver_recebimento

        com_falhas = envolver_recebimento(controlador, no.processar_mensagem)

        def callback(mensagem):  # noqa: F811 - substitui o callback por um que converte crash em 503
            try:
                return com_falhas(mensagem)
            except NoEmCrashError as erro:
                from fastapi import HTTPException

                raise HTTPException(status_code=503, detail=str(erro)) from erro

    app = criar_app_recebedor(callback)
    if controlador is not None:
        from src.comum.falhas import registrar_endpoints_falha

        registrar_endpoints_falha(app, controlador)

    @app.get("/estado")
    async def estado() -> dict[str, Any]:  # pragma: no cover - exercido via servidor
        return no.estado_atual()

    @app.post("/iniciar-eleicao")
    async def iniciar() -> dict[str, Any]:  # pragma: no cover - exercido via servidor
        return no.disparar_eleicao()

    @app.get("/saude")
    async def saude() -> dict[str, Any]:  # pragma: no cover - healthcheck do compose
        return {"ok": True, "no_id": no.estado.no_id}

    return app
