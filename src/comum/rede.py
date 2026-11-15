"""Transporte HTTP/JSON com Deadline obrigatório e erros tipados.

Esta camada é a única que conhece FastAPI e httpx. As Máquinas_de_Estados dos
algoritmos nunca importam este módulo nem seus clientes de rede: o adaptador
(app FastAPI de cada Nó) é quem traduz uma Mensagem recebida numa chamada à
lógica pura e despacha as Mensagens de saída por este `ClienteRede`.

Duas regras de projeto estão codificadas aqui, não apenas documentadas:

- **Deadline obrigatório**. O `ClienteRede` não oferece nenhum
  caminho de código que envie uma Mensagem sem timeout. O Deadline é um
  parâmetro posicional de `enviar`; não há sobrecarga, default `None` nem flag
  que o desligue. Sem isso, uma partição de rede travaria um Nó indefinidamente
  e o salto do Anel ou o ABORT por timeout do 2PC deixariam de acontecer.

- **Erros tipados, nunca exceções silenciosas**. Toda falha de
  transporte vira um `ResultadoEnvio` tipado — `EnvioOk`, `EnvioTimeout`,
  `EnvioConexaoRecusada` ou `EnvioIndisponivel` — para que a Máquina_de_Estados
  decida o salto ou o ABORT de forma explícita, a partir do tipo do resultado,
  e não a partir de uma exceção capturada ad hoc.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Union

import httpx

from .modelos import Mensagem

# Caminho HTTP onde o servidor de um Nó recebe Mensagens. Fixo por convenção
# para que cliente e servidor combinem sem configuração adicional.
CAMINHO_MENSAGEM = "/mensagem"


class DeadlineInvalidoError(ValueError):
    """Deadline ausente ou fora de domínio numa chamada de envio.

    Levantada pelo próprio `ClienteRede.enviar` quando o Deadline não é um
    número estritamente positivo. É um erro de programação (uso indevido da
    API), distinto das falhas de transporte, que viram `ResultadoEnvio`. Existe
    justamente para que não haja caminho de envio sem um prazo válido.
    """


@dataclass(frozen=True)
class EnvioOk:
    """Entrega bem-sucedida: resposta HTTP 2xx e corpo JSON desserializado."""

    status: int
    corpo: Mapping[str, Any]


@dataclass(frozen=True)
class EnvioTimeout:
    """A chamada excedeu seu Deadline total, somadas as tentativas.

    `tentativas` é quantos envios foram feitos antes de desistir. Distinto de
    conexão recusada: aqui o destino pode estar vivo, apenas lento demais, e o
    Anel deve tratar o Sucessor como falho, o 2PC deve decidir ABORT.
    """

    deadline: float
    tentativas: int


@dataclass(frozen=True)
class EnvioConexaoRecusada:
    """O destino recusou a conexão (porta fechada / processo ausente).

    Sinal forte de que o Nó não está escutando, diferente de um timeout, em que
    o destino pode estar apenas lento.
    """

    tentativas: int
    detalhe: str


@dataclass(frozen=True)
class EnvioIndisponivel:
    """Resposta fora de 2xx ou erro de transporte.

    Agrupa as respostas HTTP fora de 2xx e as falhas de transporte do httpx que não
    são nem timeout nem conexão recusada (por exemplo, erro de resolução de nome). O
    `status` é o código HTTP quando houve resposta, senão `None`.
    """

    tentativas: int
    detalhe: str
    status: int | None = None


# Resultado tipado de um envio. A Máquina_de_Estados decide a partir do tipo,
# sem jamais ver uma exceção de transporte.
ResultadoEnvio = Union[EnvioOk, EnvioTimeout, EnvioConexaoRecusada, EnvioIndisponivel]


class _TransporteHTTP(Protocol):
    """O mínimo do httpx que o cliente usa, para permitir um fake nos testes."""

    def post(self, url: str, *, json: Any, timeout: float) -> httpx.Response: ...


class ClienteRede:
    """Cliente httpx que envia Mensagens JSON a outro Nó com Deadline obrigatório.

    `retries` é o número de tentativas extras além da primeira (0 = uma única
    tentativa). Nenhuma tentativa começa depois do Deadline total, e cada uma
    recebe como timeout o tempo que resta dele. O httpx aplica esse timeout a
    cada fase da tentativa (conexão, escrita, cada leitura e espera por conexão
    livre), e não à soma delas, então no pior caso uma tentativa passa do tempo
    que restava. O Deadline limita os retries, mas não é um prazo absoluto.

    O cliente é reutilizável entre chamadas; `fechar` libera a conexão
    subjacente. Aceita um `transporte` alternativo (um fake) para teste sem
    rede, mas em produção usa um `httpx.Client` real.
    """

    def __init__(
        self,
        *,
        retries: int = 2,
        transporte: _TransporteHTTP | None = None,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
            raise ValueError(f"retries deve ser inteiro >= 0; recebido {retries!r}")
        self._retries = retries
        self._relogio = relogio
        self._proprio_transporte = transporte is None
        self._transporte: _TransporteHTTP = transporte or httpx.Client()

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        """Envia `mensagem` como JSON para `destino_url`, respeitando o Deadline total.

        `deadline` é o orçamento, em segundos, da chamada inteira com os retries,
        nos limites descritos em `ClienteRede`. É obrigatório e deve ser
        estritamente positivo, porque não existe caminho de envio sem prazo. O
        retorno é sempre um `ResultadoEnvio` tipado; falhas de transporte nunca
        escapam como exceção.
        """
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or deadline <= 0:
            raise DeadlineInvalidoError(
                f"deadline obrigatório e > 0 em toda chamada; recebido {deadline!r}"
            )

        url = destino_url.rstrip("/") + CAMINHO_MENSAGEM
        corpo = mensagem.para_json()
        inicio = self._relogio()
        tentativas = 0
        ultimo_indisponivel: EnvioIndisponivel | None = None
        ultimo_recusado: EnvioConexaoRecusada | None = None

        # Uma tentativa inicial mais `retries` extras, sempre limitadas pelo
        # tempo restante do Deadline total.
        for n in range(self._retries + 1):
            restante = deadline - (self._relogio() - inicio)
            if restante <= 0:
                return self._timeout(deadline, tentativas)

            tentativas += 1
            resultado = self._uma_tentativa(url, corpo, restante)

            if isinstance(resultado, EnvioOk):
                return resultado
            if isinstance(resultado, EnvioTimeout):
                # Timeout da tentativa: o Deadline total mandou; não reenvia.
                return self._timeout(deadline, tentativas)
            if isinstance(resultado, EnvioConexaoRecusada):
                ultimo_recusado = resultado
            else:  # EnvioIndisponivel
                ultimo_indisponivel = resultado

            # Sem prazo para outra tentativa, sai o último erro de transporte.
            if n < self._retries and deadline - (self._relogio() - inicio) <= 0:
                break

        if ultimo_recusado is not None and ultimo_indisponivel is None:
            return EnvioConexaoRecusada(tentativas=tentativas, detalhe=ultimo_recusado.detalhe)
        if ultimo_indisponivel is not None:
            return EnvioIndisponivel(
                tentativas=tentativas,
                detalhe=ultimo_indisponivel.detalhe,
                status=ultimo_indisponivel.status,
            )
        # Só chega aqui se o prazo acabou antes de qualquer tentativa concluir.
        return self._timeout(deadline, tentativas)

    def _uma_tentativa(
        self, url: str, corpo: Mapping[str, Any], timeout: float
    ) -> ResultadoEnvio:
        """Faz um único POST e traduz o desfecho num `ResultadoEnvio` tipado."""
        try:
            resposta = self._transporte.post(url, json=corpo, timeout=timeout)
        except httpx.TimeoutException:
            return EnvioTimeout(deadline=timeout, tentativas=1)
        except httpx.ConnectError as erro:
            # ConnectError cobre recusa de conexão e falha de resolução/rota. A
            # recusa explícita ("refused") é o sinal de Nó fora; o resto é
            # indisponibilidade de transporte.
            texto = str(erro).lower()
            if "refused" in texto:
                return EnvioConexaoRecusada(tentativas=1, detalhe=str(erro))
            return EnvioIndisponivel(tentativas=1, detalhe=str(erro))
        except httpx.HTTPError as erro:
            return EnvioIndisponivel(tentativas=1, detalhe=str(erro))

        if 200 <= resposta.status_code < 300:
            return EnvioOk(status=resposta.status_code, corpo=_json_seguro(resposta))
        # Fora de 2xx o destino respondeu, mas não aceitou a Mensagem.
        return EnvioIndisponivel(
            tentativas=1,
            detalhe=f"HTTP {resposta.status_code}",
            status=resposta.status_code,
        )

    @staticmethod
    def _timeout(deadline: float, tentativas: int) -> EnvioTimeout:
        return EnvioTimeout(deadline=deadline, tentativas=tentativas)

    def fechar(self) -> None:
        """Libera a conexão subjacente, se o cliente é dono dela."""
        if self._proprio_transporte:
            fechar = getattr(self._transporte, "close", None)
            if callable(fechar):
                fechar()

    def __enter__(self) -> "ClienteRede":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.fechar()


def _json_seguro(resposta: httpx.Response) -> Mapping[str, Any]:
    """Desserializa o corpo da resposta como JSON, tolerando corpo vazio/não-JSON."""
    try:
        dados = resposta.json()
    except ValueError:
        return {}
    return dados if isinstance(dados, Mapping) else {"valor": dados}


# ---------------------------------------------------------------------------
# Helper de servidor FastAPI
# ---------------------------------------------------------------------------

# Um callback de Máquina_de_Estados: recebe a Mensagem desserializada e devolve
# um dicionário JSON de resposta imediata (um ACK, um estado, etc.). O callback
# NÃO deve esperar rodadas subsequentes do algoritmo: a resposta HTTP confirma
# apenas o recebimento/entrega à lógica, não o término de rondas futuras.
# O despacho de Mensagens de saída é responsabilidade do
# adaptador, por fora, usando o `ClienteRede`.
CallbackMensagem = Callable[[Mensagem], Mapping[str, Any]]


def criar_app_recebedor(ao_receber: CallbackMensagem):
    """Cria um app FastAPI mínimo que entrega Mensagens recebidas a `ao_receber`.

    O endpoint `POST /mensagem` desserializa o corpo JSON para uma `Mensagem`,
    chama `ao_receber` e devolve o que o callback retornar (um ACK imediato).
    Mantém a lógica do algoritmo desacoplada do framework: `ao_receber` é uma
    função pura/adaptador, sem conhecer FastAPI, e a resposta
    HTTP não fica presa a rodadas futuras.

    Importa FastAPI localmente para que o simples `import src.comum.rede` não
    exija FastAPI em contextos que só usam o cliente (por exemplo, testes de
    Máquina_de_Estados), reforçando a fronteira de dependência.
    """
    from fastapi import Body, FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    # `Body(...)` carrega o FieldInfo no default, o que dispensa a resolução da
    # anotação de tipo pela introspecção do FastAPI. Isso importa porque este
    # handler é um closure com import local de FastAPI: a anotação não está no
    # namespace de módulo que o FastAPI inspeciona.
    @app.post(CAMINHO_MENSAGEM)
    async def receber(corpo: dict = Body(...)) -> JSONResponse:  # pragma: no cover - exercido via servidor
        mensagem = Mensagem.de_json(corpo)
        resposta = ao_receber(mensagem)
        return JSONResponse(content=dict(resposta or {}))

    return app
