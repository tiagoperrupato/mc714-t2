"""Injeção de falhas no nível da aplicação, determinística por Semente.

Este módulo oferece um `ControladorFalhas` plugável em qualquer Nó — tanto no
adaptador do Anel (`src.anel.no`) quanto nos apps do 2PC
(`src.doispc.coordenador`/`participante`) — com os quatro modos exigidos pelo
Requisito 8.1:

- **crash**: o Nó deixa de responder. O controlador passa a recusar o
  processamento de Mensagens recebidas (levanta `NoEmCrashError`, que o
  adaptador traduz num HTTP 503) e a abortar o despacho de Mensagens de saída.
- **pause**: o Nó congela por N segundos e depois volta a operar. Implementado
  como um `sleep` no caminho de recebimento/despacho; ao fim do intervalo o Nó
  responde normalmente de novo.
- **delay**: atrasa cada resposta por um intervalo. O intervalo de cada decisão
  vem do stream `delay` da Semente (Requisito 8.2), então é idêntico entre
  execuções com a mesma Semente e o mesmo Cenário.
- **drop**: descarta uma Mensagem com probabilidade `p`. A decisão vem do stream
  `drop` da Semente (Requisito 8.2), também reproduzível dígito a dígito.

**Por que a camada comum/adaptador, e não a Máquina_de_Estados.** As
Máquinas_de_Estados do Anel (`src.anel.maquina`) e do 2PC (`src.doispc.maquina`)
permanecem puras: elas não sabem que falhas existem. O controlador intercepta
nas duas fronteiras onde a rede toca a lógica — o **recebimento** de uma
Mensagem (antes de entregá-la à Máquina) e o **despacho** de uma Mensagem de
saída (o `ClienteRede`). Essa é a mesma fronteira onde o jitter/timeout já
vivem, e é o único lugar onde faz sentido descartar ou atrasar uma Mensagem sem
contaminar a decisão do algoritmo.

**Determinismo (Requisito 8.2).** As decisões de drop e de delay consomem,
respectivamente, dos streams independentes `drop` e `delay` de `Streams`
(`src.comum.aleatoriedade`). Consumir um não desloca o outro, então, fixada a
Semente e o Cenário, a sequência de Mensagens descartadas e de atrasos aplicados
é idêntica entre execuções. crash e pause são determinísticos por construção:
não consomem aleatoriedade, são ligados/desligados por comando de controle.

**Controle em tempo de execução (Requisito 8.3).** `registrar_endpoints_falha`
pendura os endpoints `/falha/*` em qualquer app FastAPI, para injetar e limpar
falhas sem reiniciar o Nó. `limpar` zera todos os modos.

**Reprodutibilidade no log (Requisito 8.4).** `registrar_plano_no_log` grava a
Semente e o Plano_de_Falhas no Log_Estruturado, no início da Réplica, usando o
`inicio_replica` já existente.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from src.comum.aleatoriedade import Streams
from src.comum.log import LogEstruturado
from src.comum.modelos import Mensagem
from src.comum.rede import ResultadoEnvio


class NoEmCrashError(RuntimeError):
    """Sinaliza que o Nó está em crash e não deve responder.

    Levantada pelo controlador no caminho de recebimento quando o modo crash
    está ativo. O adaptador a captura e devolve um HTTP 503, reproduzindo "o Nó
    deixou de responder" sem derrubar o processo do servidor — o que permite
    curar o crash depois via `/falha/limpar` na demonstração.
    """


@dataclass
class _Modos:
    """Estado mutável dos modos de falha ativos, protegido por lock no controlador.

    `crash` desliga as respostas enquanto ligado. `pausa_s` congela por N
    segundos (aplicado uma vez por evento, não a cada Mensagem indefinidamente —
    ver `_consumir_pausa`). `atraso_s` é o teto do atraso sorteado por Mensagem
    quando `atraso_ativo`. `drop_p` é a probabilidade de descarte quando
    `drop_ativo`.
    """

    crash: bool = False
    pausa_s: float = 0.0
    atraso_ativo: bool = False
    atraso_s: float = 0.0
    drop_ativo: bool = False
    drop_p: float = 0.0


class ControladorFalhas:
    """Controlador de falhas plugável, dirigido pela Semente, com estado em runtime.

    Recebe a `Semente` (para derivar os streams `drop` e `delay` reproduzíveis) e,
    opcionalmente, um `LogEstruturado` para registrar as decisões. É thread-safe:
    os endpoints de controle, o worker de despacho do Anel e os handlers HTTP
    concorrentes tocam o mesmo controlador.

    A mesma instância serve aos dois lados: `antes_de_receber` é chamada no
    recebimento de uma Mensagem (antes de entregá-la à Máquina), e
    `antes_de_enviar` é consultada no despacho de uma Mensagem de saída. Ambos
    consultam os mesmos modos, de modo que um crash injetado silencia o Nó nas
    duas direções.
    """

    def __init__(
        self,
        semente: int,
        *,
        log: Optional[LogEstruturado] = None,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(semente, int) or isinstance(semente, bool) or semente < 0:
            raise ValueError(
                f"semente deve ser inteiro >= 0; recebido {semente!r}"
            )
        self._semente = semente
        self._streams = Streams(semente)
        self._log = log
        self._dormir = dormir
        self._modos = _Modos()
        self._lock = threading.Lock()

    # -- Injeção e limpeza (Requisito 8.3) ---------------------------------

    @property
    def semente(self) -> int:
        """A Semente que dirige os streams de drop e delay."""
        return self._semente

    def injetar_crash(self) -> None:
        """Liga o modo crash: o Nó deixa de responder até `limpar`/`curar_crash`."""
        with self._lock:
            self._modos.crash = True
        self._evento("falha_injetada", modo="crash")

    def curar_crash(self) -> None:
        """Desliga só o crash, preservando os demais modos (útil na demonstração)."""
        with self._lock:
            self._modos.crash = False
        self._evento("falha_curada", modo="crash")

    def injetar_pause(self, segundos: float) -> None:
        """Agenda um congelamento de `segundos` no próximo evento (Requisito 8.1)."""
        if segundos < 0:
            raise ValueError(f"pause >= 0; recebido {segundos!r}")
        with self._lock:
            self._modos.pausa_s = float(segundos)
        self._evento("falha_injetada", modo="pause", segundos=float(segundos))

    def injetar_delay(self, segundos: float) -> None:
        """Liga o atraso de respostas com teto `segundos` (Requisito 8.1)."""
        if segundos < 0:
            raise ValueError(f"delay >= 0; recebido {segundos!r}")
        with self._lock:
            self._modos.atraso_ativo = segundos > 0
            self._modos.atraso_s = float(segundos)
        self._evento("falha_injetada", modo="delay", segundos=float(segundos))

    def injetar_drop(self, probabilidade: float) -> None:
        """Liga o descarte de Mensagens com probabilidade `p` em [0, 1] (Requisito 8.1)."""
        if not 0.0 <= probabilidade <= 1.0:
            raise ValueError(
                f"drop p deve estar em [0, 1]; recebido {probabilidade!r}"
            )
        with self._lock:
            self._modos.drop_ativo = probabilidade > 0
            self._modos.drop_p = float(probabilidade)
        self._evento("falha_injetada", modo="drop", probabilidade=float(probabilidade))

    def limpar(self) -> None:
        """Zera todos os modos de falha, voltando o Nó à operação normal."""
        with self._lock:
            self._modos = _Modos()
        self._evento("falha_limpa")

    def estado(self) -> dict[str, Any]:
        """Snapshot serializável dos modos ativos, para `GET /falha/estado`."""
        with self._lock:
            m = self._modos
            return {
                "semente": self._semente,
                "crash": m.crash,
                "pausa_s": m.pausa_s,
                "delay_ativo": m.atraso_ativo,
                "delay_s": m.atraso_s,
                "drop_ativo": m.drop_ativo,
                "drop_p": m.drop_p,
            }

    def plano_de_falhas(self) -> dict[str, Any]:
        """Descrição do Plano_de_Falhas corrente, para registrar no log (Requisito 8.4)."""
        return self.estado()

    # -- Interceptação do recebimento --------------------------------------

    def antes_de_receber(self, mensagem: Mensagem) -> bool:
        """Aplica os modos de falha ANTES de entregar `mensagem` à Máquina.

        Devolve `True` se a Mensagem deve seguir para a Máquina_de_Estados, e
        `False` se deve ser descartada (drop). Levanta `NoEmCrashError` se o Nó
        está em crash — o adaptador traduz isso num 503. Aplica, nesta ordem:
        crash (recusa), pause (congela), drop (descarta pelo stream), delay
        (atrasa pelo stream).
        """
        return self._aplicar(mensagem, direcao="receber")

    def antes_de_enviar(self, mensagem: Mensagem) -> bool:
        """Aplica os modos de falha ANTES de despachar `mensagem` de saída.

        Mesma semântica de `antes_de_receber`, do lado do despacho: `False`
        significa descartar o envio (a Mensagem some na rede), e `NoEmCrashError`
        significa que o Nó em crash não despacha nada. crash e drop no envio
        reproduzem a perda de Mensagens de saída de um Nó falho.
        """
        return self._aplicar(mensagem, direcao="enviar")

    def _aplicar(self, mensagem: Mensagem, *, direcao: str) -> bool:
        with self._lock:
            crash = self._modos.crash
            pausa = self._consumir_pausa()
            dropar = self._decidir_drop()
            atraso = self._decidir_atraso()

        if crash:
            self._evento("drop_por_crash", direcao=direcao, msg_tipo=mensagem.tipo)
            raise NoEmCrashError(f"nó em crash: {direcao} recusado")

        if pausa > 0:
            self._evento("pause", direcao=direcao, segundos=pausa)
            self._dormir(pausa)

        if dropar:
            self._evento("drop", direcao=direcao, msg_tipo=mensagem.tipo)
            return False

        if atraso > 0:
            self._evento("delay", direcao=direcao, segundos=atraso, msg_tipo=mensagem.tipo)
            self._dormir(atraso)

        return True

    def _consumir_pausa(self) -> float:
        """Devolve o tempo de pausa agendado e o zera (pausa é evento único).

        Chamado sob o lock. Modelar pause como evento único — e não como um modo
        que congela toda Mensagem para sempre — é o que permite "congela N s e
        depois resume" do Requisito 8.1: injeta-se o pause, o próximo evento
        paga o congelamento, e o Nó volta a operar.
        """
        pausa = self._modos.pausa_s
        self._modos.pausa_s = 0.0
        return pausa

    def _decidir_drop(self) -> bool:
        """Decide descarte consumindo do stream `drop` (reproduzível). Sob o lock.

        Só consome do stream quando o drop está ativo, para que ligar/desligar o
        drop não desloque a sequência quando ele está desligado.
        """
        if not self._modos.drop_ativo:
            return False
        return self._streams.drop.random() < self._modos.drop_p

    def _decidir_atraso(self) -> float:
        """Sorteia o atraso consumindo do stream `delay` (reproduzível). Sob o lock.

        O atraso é uniforme em [0, teto], de modo que o teto limite o pior caso e
        a sequência de atrasos seja a mesma para a mesma Semente.
        """
        if not self._modos.atraso_ativo:
            return 0.0
        return self._streams.delay.uniform(0.0, self._modos.atraso_s)

    def _evento(self, tipo: str, **campos: Any) -> None:
        if self._log is not None:
            self._log.evento(tipo, **campos)


# ---------------------------------------------------------------------------
# Plugagem no recebimento: embrulha o callback de Mensagem do adaptador
# ---------------------------------------------------------------------------

CallbackMensagem = Callable[[Mensagem], Mapping[str, Any]]


def envolver_recebimento(
    controlador: ControladorFalhas, callback: CallbackMensagem
) -> CallbackMensagem:
    """Embrulha um callback de recebimento aplicando o controlador antes dele.

    O callback original (por exemplo, `NoAnel.processar_mensagem` ou
    `ServicoParticipante.receber`) só é chamado se o controlador deixa a
    Mensagem passar. Um drop devolve um ACK de "descartada" sem tocar a Máquina;
    um crash propaga `NoEmCrashError` para o adaptador converter em 503. Assim o
    controlador é plugável sem que o adaptador mude sua lógica de decisão.
    """

    def recebedor(mensagem: Mensagem) -> Mapping[str, Any]:
        if not controlador.antes_de_receber(mensagem):
            return {"ack": False, "descartada": True}
        return callback(mensagem)

    return recebedor


# ---------------------------------------------------------------------------
# Plugagem no despacho: embrulha o ClienteRede de saída
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvioDescartado:
    """Resultado de um envio suprimido pelo controlador (drop ou crash de saída).

    É um `ResultadoEnvio` adicional, distinto dos de transporte: a Mensagem não
    chegou à rede porque o próprio Nó a descartou. Para quem consome o resultado
    (o executor do 2PC, o worker do Anel), comporta-se como um envio que não
    obteve `EnvioOk`, disparando o mesmo tratamento de "não entregue".
    """

    motivo: str


class ClienteComFalhas:
    """Decorador de `ClienteRede` que aplica o controlador antes de cada envio.

    Expõe a mesma interface `enviar(url, mensagem, deadline) -> ResultadoEnvio` e
    `fechar()`, de modo que entra no lugar de um `ClienteRede` sem que o
    adaptador perceba. Antes de delegar ao cliente real, consulta o controlador:
    um crash ou um drop de saída devolve `EnvioDescartado` e a Mensagem nunca
    toca a rede; caso contrário (possivelmente após um delay/pause), o envio
    segue normalmente.
    """

    def __init__(self, cliente, controlador: ControladorFalhas) -> None:
        self._cliente = cliente
        self._controlador = controlador

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        try:
            passar = self._controlador.antes_de_enviar(mensagem)
        except NoEmCrashError:
            return EnvioDescartado(motivo="crash")
        if not passar:
            return EnvioDescartado(motivo="drop")
        return self._cliente.enviar(destino_url, mensagem, deadline)

    def fechar(self) -> None:
        fechar = getattr(self._cliente, "fechar", None)
        if callable(fechar):
            fechar()

    def __enter__(self) -> "ClienteComFalhas":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.fechar()


# ---------------------------------------------------------------------------
# Endpoints de controle /falha/* (Requisito 8.3)
# ---------------------------------------------------------------------------


def registrar_endpoints_falha(app, controlador: ControladorFalhas) -> None:
    """Pendura os endpoints `/falha/*` num app FastAPI já criado.

    Expõe, para injeção e limpeza em tempo de execução (Requisito 8.3):

    - `POST /falha/crash`  — liga o crash;
    - `POST /falha/pause`  — congela por `segundos` (corpo `{"segundos": N}`);
    - `POST /falha/delay`  — liga o atraso com teto `segundos`;
    - `POST /falha/drop`   — liga o descarte com probabilidade `p`;
    - `POST /falha/limpar` — zera todos os modos;
    - `GET  /falha/estado` — devolve os modos ativos.

    É importado FastAPI localmente, como no resto do projeto, para não exigir o
    framework de quem só usa o controlador em teste de unidade.
    """
    from fastapi import Body
    from fastapi.responses import JSONResponse

    @app.post("/falha/crash")
    async def _crash() -> JSONResponse:  # pragma: no cover - via servidor
        controlador.injetar_crash()
        return JSONResponse(content=controlador.estado())

    @app.post("/falha/pause")
    async def _pause(corpo: dict = Body(default={})) -> JSONResponse:  # pragma: no cover
        segundos = float((corpo or {}).get("segundos", 0.0))
        controlador.injetar_pause(segundos)
        return JSONResponse(content=controlador.estado())

    @app.post("/falha/delay")
    async def _delay(corpo: dict = Body(default={})) -> JSONResponse:  # pragma: no cover
        segundos = float((corpo or {}).get("segundos", 0.0))
        controlador.injetar_delay(segundos)
        return JSONResponse(content=controlador.estado())

    @app.post("/falha/drop")
    async def _drop(corpo: dict = Body(default={})) -> JSONResponse:  # pragma: no cover
        probabilidade = float((corpo or {}).get("p", (corpo or {}).get("probabilidade", 0.0)))
        controlador.injetar_drop(probabilidade)
        return JSONResponse(content=controlador.estado())

    @app.post("/falha/limpar")
    async def _limpar() -> JSONResponse:  # pragma: no cover - via servidor
        controlador.limpar()
        return JSONResponse(content=controlador.estado())

    @app.get("/falha/estado")
    async def _estado() -> JSONResponse:  # pragma: no cover - via servidor
        return JSONResponse(content=controlador.estado())


def registrar_plano_no_log(
    controlador: ControladorFalhas,
    log: LogEstruturado,
    *,
    cenario: Mapping[str, Any] | None = None,
) -> None:
    """Registra a Semente e o Plano_de_Falhas no início da Réplica (Requisito 8.4).

    Reaproveita o `inicio_replica` do Log_Estruturado, que já carimba a Semente,
    o Plano_de_Falhas e o Cenário numa linha JSON, tornando a Réplica
    reproduzível a partir do próprio log (Requisito 2.2, 8.4).
    """
    log.inicio_replica(
        semente=controlador.semente,
        plano_de_falhas=controlador.plano_de_falhas(),
        cenario=dict(cenario or {}),
    )
