"""Nó do Anel sobre a rede: adaptador fino FastAPI sobre a Máquina_de_Estados pura.

Este módulo é a camada de rede do algoritmo de Eleição em Anel.
Ele NÃO reimplementa a lógica de eleição: toda decisão é delegada a
`src.anel.maquina` (funções puras sobre `EstadoAnel`), e o único papel deste
adaptador é traduzir Mensagens HTTP recebidas em chamadas à Máquina_de_Estados e
despachar as Mensagens de saída pelo `ClienteRede` do Transporte.

Responsabilidades, e só elas:

- Expor `POST /mensagem` (via `criar_app_recebedor`), que entrega cada Mensagem
  recebida a `receber` e enfileira o que a Máquina devolver para despacho.
- Expor os endpoints de controle `GET /estado` e `POST /iniciar-eleicao`.
- Ao repassar uma Mensagem ao Sucessor, reenviar dentro do Deadline até ele
  aceitá-la. Só quando o Deadline acaba sem aceite o adaptador chama
  `com_sucessor_falho`, a regra de salto do Anel. Uma
  Mensagem perdida não deve tirar do Anel um Nó vivo.

Despacho fora do caminho da resposta HTTP. Um ponto sutil, mas
essencial para a correção: o repasse ao Sucessor NÃO acontece dentro do handler
HTTP que recebeu a Mensagem. Se acontecesse, o handler de cada Nó ficaria
bloqueado esperando o repasse ao próximo, que por sua vez espera o seguinte, e
assim por diante — uma cadeia de chamadas HTTP aninhadas e síncronas. Numa
circulação de Anel, isso estoura o Deadline de cada hop e dispara a regra de
salto por engano, com Nós vivos sendo marcados como mortos. Para evitar isso, as
Mensagens de saída vão para a fila de `src.comum.despacho`, esvaziada por uma
thread; a resposta HTTP é um ACK imediato de recebimento, desacoplado do término
das rodadas subsequentes.

A Máquina continua pura: o lock protege apenas a troca atômica do `EstadoAnel`
corrente por um novo valor, e a fila/worker são mecanismo de transporte, não
lógica de eleição.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Mapping

from src.anel.maquina import (
    EstadoAnel,
    com_sucessor_falho,
    iniciar_eleicao,
    receber,
)
from src.comum.despacho import Despachante
from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem
from src.comum.rede import ClienteRede, EnvioOk

# Tentativas de entrega a um Sucessor antes do salto, uma a cada fração igual do Deadline.
TENTATIVAS_ANTES_DO_SALTO = 3


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
        self._saida: Despachante[Mensagem] = Despachante(
            self._despachar_uma, iniciar=iniciar_worker
        )

    # -- Ciclo de vida do worker de despacho -------------------------------

    def iniciar(self) -> None:
        """Inicia o worker de despacho em background, se ainda não está rodando."""
        self._saida.iniciar()

    def parar(self) -> None:
        """Sinaliza o worker a encerrar e aguarda, drenando a fila."""
        self._saida.parar()

    def drenar(self) -> None:
        """Bloqueia até a fila de saída esvaziar, útil em testes determinísticos."""
        self._saida.drenar()

    # -- Consulta de estado --------------------------------

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
        """Entrega uma Mensagem recebida à Máquina e enfileira a saída.

        A resposta HTTP é um ACK imediato de recebimento; ela não espera o
        término das rodadas que o despacho venha a provocar. O
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
        """Dispara uma Rodada_de_Eleicao neste Nó e enfileira a saída.

        O id da rodada leva o id do Nó e um sufixo aleatório, para não repetir o
        de uma rodada anterior, nem depois de um reinício.
        """
        rodada = f"{self._estado.no_id}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            novo, saida = iniciar_eleicao(self._estado, rodada)
            self._estado = novo
        if self._log is not None:
            self._log.evento("iniciou_eleicao", no_id=self.estado.no_id, rodada=rodada)
        self._enfileirar(saida)
        return {"iniciada": True, "no_id": self.estado.no_id, "lider": self.estado.lider}

    # -- Despacho em background com a regra de salto --

    def _enfileirar(self, saida: list[Mensagem]) -> None:
        self._saida.enfileirar(saida)

    def _despachar_uma(self, mensagem: Mensagem) -> None:
        """Entrega uma Mensagem ao destino, ou aplica o salto e reenfileira.

        Se o destino não aceita a Mensagem dentro do Deadline, chama
        `com_sucessor_falho` para que a Máquina marque o Sucessor como morto e
        reemita a MESMA Mensagem ao próximo Nó vivo. As Mensagens reemitidas
        voltam para a fila, sem recursão nem bloqueio do caminho HTTP.
        """
        destino_url = self._urls.get(mensagem.destino)
        if destino_url is None:
            self._enfileirar(self._tratar_falha(mensagem, mensagem.destino, motivo="sem_url"))
            return
        motivo = self._entregar(destino_url, mensagem)
        if motivo is None:
            if self._log is not None:
                self._log.evento("repassou", msg_tipo=mensagem.tipo, destino=mensagem.destino)
            return
        self._enfileirar(self._tratar_falha(mensagem, mensagem.destino, motivo=motivo))

    def _entregar(self, destino_url: str, mensagem: Mensagem) -> str | None:
        """Envia até o destino aceitar, ou até o Deadline acabar.

        As tentativas começam a cada fração do Deadline, e cada uma pode usar
        todo o prazo que resta. Assim uma resposta lenta ainda chega, e uma
        falha que volta logo (drop, 503, conexão recusada) tem nova tentativa.
        Só um `EnvioOk` com `ack` verdadeiro é aceite: um destino que descartou
        a Mensagem responde 200 com `ack` falso. Devolve `None` no aceite, ou o
        tipo do último resultado.
        """
        inicio = time.monotonic()
        intervalo = self._deadline / TENTATIVAS_ANTES_DO_SALTO
        for tentativa in range(TENTATIVAS_ANTES_DO_SALTO):
            time.sleep(max(0.0, inicio + tentativa * intervalo - time.monotonic()))
            restante = self._deadline - (time.monotonic() - inicio)
            if restante <= 0:
                break
            resultado = self._cliente.enviar(destino_url, mensagem, restante)
            if isinstance(resultado, EnvioOk) and resultado.corpo.get("ack") is True:
                return None
        return type(resultado).__name__

    def _tratar_falha(self, mensagem: Mensagem, sucessor_falho: int, *, motivo: str) -> list[Mensagem]:
        """Aplica `com_sucessor_falho` sob o lock e devolve a Mensagem reemitida."""
        with self._lock:
            novo, reemitidas = com_sucessor_falho(self._estado, mensagem, sucessor_falho)
            self._estado = novo
        if self._log is not None:
            self._log.evento(
                "sucessor_falho",
                rodada=mensagem.corpo.get("rodada"),
                sucessor=sucessor_falho,
                motivo=motivo,
                reemitidas=len(reemitidas),
            )
        return reemitidas


def criar_app(no: NoAnel, *, controlador=None):
    """Monta o app FastAPI de um Nó do Anel sobre um `NoAnel`.

    Registra `POST /mensagem` (via o helper do Transporte, que desacopla a
    resposta HTTP da lógica) e os endpoints de controle `GET /estado` e
    `POST /iniciar-eleicao`. A lógica de eleição vive em
    `src.anel.maquina`; este app apenas a expõe pela rede.

    Quando um `controlador` de falhas é fornecido, o recebimento de Mensagens
    passa por ele antes de chegar à Máquina (drop/delay/pause/crash), os
    endpoints `/falha/*` são expostos para controle em runtime,
    e um crash devolve HTTP 503 (o Nó "deixa de responder"). O despacho de saída
    é interceptado passando um `ClienteComFalhas` ao `NoAnel` na construção; a
    Máquina continua pura.
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
