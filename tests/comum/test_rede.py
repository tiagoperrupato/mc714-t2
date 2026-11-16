"""Transporte HTTP com Deadline obrigatório e erros tipados.

Dois níveis de teste:

- **Integração real**: sobe dois apps FastAPI em servidores uvicorn locais, em
  portas efêmeras, e exercita envio bem-sucedido, timeout quando o destino
  dorme além do Deadline e erro quando o destino está fora. É o teste que
  prova que o caminho real (httpx ↔ FastAPI) honra o Deadline.
- **Unitário com transporte fake**: fixa relógio e transporte para checar, de
  forma determinística, que o Deadline é obrigatório, que os retries são
  limitados e respeitam o Deadline total, e que cada falha de transporte vira o
  erro tipado correto, sem exceção escapando.
"""

from __future__ import annotations

import time

import httpx
import pytest

from src.comum.modelos import Mensagem
from src.comum.rede import (
    CAMINHO_MENSAGEM,
    TENTATIVAS_ANTES_DO_SALTO,
    ClienteRede,
    DeadlineInvalidoError,
    EnvioConexaoRecusada,
    EnvioIndisponivel,
    EnvioOk,
    EnvioTimeout,
    criar_app_recebedor,
    entregar,
)
from tests.apoio import ServidorLocal, porta_livre


# ---------------------------------------------------------------------------
# Infraestrutura de teste: servidor uvicorn local em thread, porta efêmera
# ---------------------------------------------------------------------------


def _app_eco(recebidas: list[Mensagem]):
    """App que registra a Mensagem recebida e responde um ACK imediato."""

    def ao_receber(mensagem: Mensagem):
        recebidas.append(mensagem)
        return {"ack": True, "tipo": mensagem.tipo, "origem": mensagem.origem}

    return criar_app_recebedor(ao_receber)


def _app_lento(atraso: float):
    """App que dorme `atraso` segundos antes de responder, para estourar Deadline."""

    def ao_receber(mensagem: Mensagem):
        time.sleep(atraso)
        return {"ack": True}

    return criar_app_recebedor(ao_receber)


# ---------------------------------------------------------------------------
# Integração: dois apps FastAPI locais trocando Mensagens
# ---------------------------------------------------------------------------


def test_integracao_envio_bem_sucedido_entre_dois_apps() -> None:
    recebidas_a: list[Mensagem] = []
    recebidas_b: list[Mensagem] = []
    with ServidorLocal(_app_eco(recebidas_a)) as app_a, ServidorLocal(
        _app_eco(recebidas_b)
    ) as app_b, ClienteRede(retries=2) as cliente:
        msg = Mensagem(tipo="eleicao", origem=1, destino=2, corpo={"ids": [1]})
        resultado = cliente.enviar(app_b.base_url, msg, deadline=2.0)

        assert isinstance(resultado, EnvioOk)
        assert resultado.status == 200
        assert resultado.corpo["ack"] is True
        assert resultado.corpo["tipo"] == "eleicao"
        # O app B recebeu a Mensagem; o app A existe para provar dois Nós
        # distintos trocando Mensagens no mesmo teste.
        assert len(recebidas_b) == 1 and recebidas_b[0].corpo["ids"] == [1]

        resposta_b = cliente.enviar(
            app_a.base_url,
            Mensagem(tipo="eleicao", origem=2, destino=1, corpo={"ids": [1, 2]}),
            deadline=2.0,
        )
        assert isinstance(resposta_b, EnvioOk)
        assert len(recebidas_a) == 1


def test_integracao_timeout_quando_destino_dorme_alem_do_deadline() -> None:
    # O destino dorme bem além do Deadline; o cliente deve estourar e retornar
    # EnvioTimeout tipado, nunca uma exceção.
    with ServidorLocal(_app_lento(atraso=2.0)) as app_lento, ClienteRede(retries=2) as cliente:
        inicio = time.monotonic()
        resultado = cliente.enviar(app_lento.base_url, Mensagem("prepare", 1, 2), deadline=0.3)
        decorrido = time.monotonic() - inicio

        assert isinstance(resultado, EnvioTimeout)
        assert resultado.deadline == 0.3
        # Respeita o Deadline total: não pode ter esperado perto dos 2 s do destino.
        assert decorrido < 1.5


def test_integracao_erro_quando_destino_esta_fora() -> None:
    # Porta livre onde ninguém escuta: conexão recusada, erro tipado.
    porta = porta_livre()
    with ClienteRede(retries=1) as cliente:
        resultado = cliente.enviar(
            f"http://127.0.0.1:{porta}", Mensagem("prepare", 1, 2), deadline=1.0
        )
    assert isinstance(resultado, (EnvioConexaoRecusada, EnvioIndisponivel))
    # Com um destino fora, o tipo deve permitir ao algoritmo decidir salto/ABORT.
    assert resultado.tentativas >= 1


def test_integracao_caminho_do_endpoint_e_o_combinado() -> None:
    recebidas: list[Mensagem] = []
    with ServidorLocal(_app_eco(recebidas)) as app, httpx.Client() as http:
        # Confirma que o helper expõe exatamente CAMINHO_MENSAGEM.
        r = http.post(
            app.base_url + CAMINHO_MENSAGEM,
            json=Mensagem("lider", 5, 1, {"lider": 5}).para_json(),
            timeout=2.0,
        )
        assert r.status_code == 200
        assert recebidas[0].corpo["lider"] == 5


# ---------------------------------------------------------------------------
# Unitário determinístico: Deadline obrigatório, retries limitados, erros tipados
# ---------------------------------------------------------------------------


class _RelogioFake:
    """Relógio controlado: cada leitura avança `passo` segundos."""

    def __init__(self, passo: float = 0.0) -> None:
        self.agora = 0.0
        self.passo = passo

    def __call__(self) -> float:
        valor = self.agora
        self.agora += self.passo
        return valor


class _TransporteFake:
    """Transporte que devolve respostas/erros roteirizados, contando chamadas."""

    def __init__(self, roteiro) -> None:
        self._roteiro = list(roteiro)
        self.chamadas: list[float] = []

    def post(self, url: str, *, json, timeout: float):
        self.chamadas.append(timeout)
        acao = self._roteiro.pop(0) if self._roteiro else self._roteiro_default
        if isinstance(acao, Exception):
            raise acao
        return acao


def _resposta(status: int, corpo=None) -> httpx.Response:
    return httpx.Response(status_code=status, json=corpo or {})


@pytest.mark.parametrize("deadline", [0, 0.0, -1.0, None, False, "1.0"])
def test_deadline_obrigatorio_e_positivo(deadline) -> None:
    # Não existe caminho de envio sem um Deadline válido.
    cliente = ClienteRede(retries=0, transporte=_TransporteFake([]))
    with pytest.raises(DeadlineInvalidoError):
        cliente.enviar("http://x", Mensagem("t", 1, 2), deadline)  # type: ignore[arg-type]


def test_envio_ok_desserializa_corpo() -> None:
    transporte = _TransporteFake([_resposta(200, {"ack": True, "n": 7})])
    cliente = ClienteRede(retries=2, transporte=transporte)
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=1.0)
    assert isinstance(resultado, EnvioOk)
    assert resultado.corpo == {"ack": True, "n": 7}
    assert len(transporte.chamadas) == 1  # sucesso não reenvia


def test_timeout_de_transporte_nao_reenvia_e_vira_tipado() -> None:
    # TimeoutException numa tentativa encerra: o Deadline total mandou.
    transporte = _TransporteFake([httpx.TimeoutException("lento")])
    cliente = ClienteRede(retries=3, transporte=transporte, relogio=_RelogioFake(0.0))
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=1.0)
    assert isinstance(resultado, EnvioTimeout)
    assert len(transporte.chamadas) == 1


def test_conexao_recusada_vira_tipado() -> None:
    erro = httpx.ConnectError("[Errno 111] Connection refused")
    transporte = _TransporteFake([erro])
    cliente = ClienteRede(retries=0, transporte=transporte)
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=1.0)
    assert isinstance(resultado, EnvioConexaoRecusada)
    assert "refused" in resultado.detalhe.lower()


def test_cinco_centos_vira_indisponivel() -> None:
    transporte = _TransporteFake([_resposta(503)])
    cliente = ClienteRede(retries=0, transporte=transporte)
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=1.0)
    assert isinstance(resultado, EnvioIndisponivel)
    assert resultado.status == 503


def test_retries_limitados_reenviam_ate_o_limite() -> None:
    # Três indisponibilidades seguidas; com retries=2 são 3 tentativas no total.
    transporte = _TransporteFake([_resposta(503), _resposta(503), _resposta(503)])
    cliente = ClienteRede(retries=2, transporte=transporte, relogio=_RelogioFake(0.0))
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=5.0)
    assert isinstance(resultado, EnvioIndisponivel)
    assert len(transporte.chamadas) == 3
    assert resultado.tentativas == 3


def test_retry_recupera_apos_falha_transiente() -> None:
    # Primeira tentativa 503, segunda 200: o retry recupera.
    transporte = _TransporteFake([_resposta(503), _resposta(200, {"ok": 1})])
    cliente = ClienteRede(retries=2, transporte=transporte, relogio=_RelogioFake(0.0))
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=5.0)
    assert isinstance(resultado, EnvioOk)
    assert len(transporte.chamadas) == 2


def test_retries_respeitam_deadline_total() -> None:
    # O relógio avança 0.4 s por leitura; o Deadline total é 1.0 s. As tentativas
    # devem parar quando o tempo restante acaba, sem usar todos os retries nem
    # estourar o Deadline total.
    transporte = _TransporteFake([_resposta(503)] * 10)
    cliente = ClienteRede(retries=9, transporte=transporte, relogio=_RelogioFake(0.4))
    resultado = cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=1.0)
    assert isinstance(resultado, (EnvioTimeout, EnvioIndisponivel))
    # Com passo 0.4 e Deadline 1.0, cabe um punhado de tentativas, não as dez.
    assert len(transporte.chamadas) < 10


def test_timeout_por_tempo_restante_decrescente() -> None:
    # Cada envio recebe como timeout o tempo restante do Deadline total, então o
    # timeout passado ao transporte decresce a cada tentativa.
    transporte = _TransporteFake([_resposta(503), _resposta(503), _resposta(200, {})])
    cliente = ClienteRede(retries=5, transporte=transporte, relogio=_RelogioFake(0.1))
    cliente.enviar("http://destino", Mensagem("t", 1, 2), deadline=2.0)
    assert transporte.chamadas == sorted(transporte.chamadas, reverse=True)


class _Tempo:
    """Substitui o módulo `time` de `src.comum.rede`: `sleep` só avança o relógio."""

    def __init__(self) -> None:
        self.agora = 0.0

    def monotonic(self) -> float:
        return self.agora

    def sleep(self, segundos: float) -> None:
        self.agora += segundos


class _ClienteRoteirizado:
    """Registra `(instante, deadline)` de cada envio e devolve o roteiro, repetindo o último."""

    def __init__(self, tempo: _Tempo, *resultados, custo: float = 0.0) -> None:
        self._tempo = tempo
        self._resultados = list(resultados)
        self._custo = custo
        self.envios: list[tuple[float, float]] = []

    def enviar(self, destino_url, mensagem, deadline):
        self.envios.append((self._tempo.agora, deadline))
        self._tempo.agora += self._custo
        return self._resultados.pop(0) if len(self._resultados) > 1 else self._resultados[0]


@pytest.fixture
def tempo(monkeypatch) -> _Tempo:
    relogio = _Tempo()
    monkeypatch.setattr("src.comum.rede.time", relogio)
    return relogio


def test_entregar_aceite_devolve_none(tempo) -> None:
    cliente = _ClienteRoteirizado(tempo, EnvioOk(status=200, corpo={"ack": True}))
    assert entregar(cliente, "http://destino", Mensagem("t", 1, 2), 3.0) is None
    assert cliente.envios == [(0.0, 3.0)]


def test_entregar_reenvia_quando_o_ack_e_falso(tempo) -> None:
    cliente = _ClienteRoteirizado(
        tempo,
        EnvioOk(status=200, corpo={"ack": False, "descartada": True}),
        EnvioOk(status=200, corpo={"ack": True}),
    )
    assert entregar(cliente, "http://destino", Mensagem("t", 1, 2), 3.0) is None
    assert cliente.envios == [(0.0, 3.0), (1.0, 2.0)]


def test_entregar_sem_aceite_espaca_as_tentativas_com_o_prazo_restante(tempo) -> None:
    cliente = _ClienteRoteirizado(
        tempo,
        EnvioOk(status=200, corpo={"ack": False}),
        EnvioTimeout(deadline=1.0, tentativas=1),
    )
    assert entregar(cliente, "http://destino", Mensagem("t", 1, 2), 3.0) == "EnvioTimeout"
    assert cliente.envios == [(0.0, 3.0), (1.0, 2.0), (2.0, 1.0)]
    assert len(cliente.envios) == TENTATIVAS_ANTES_DO_SALTO


def test_entregar_prazo_esgotado_devolve_o_ultimo_resultado(tempo) -> None:
    # O primeiro envio gasta o Deadline inteiro, então não sobra prazo para outro.
    cliente = _ClienteRoteirizado(tempo, EnvioTimeout(deadline=3.0, tentativas=1), custo=3.0)
    assert entregar(cliente, "http://destino", Mensagem("t", 1, 2), 3.0) == "EnvioTimeout"
    assert cliente.envios == [(0.0, 3.0)]


@pytest.mark.parametrize("deadline", [0.0, -1.0, None, True])
def test_entregar_recusa_deadline_invalido(tempo, deadline) -> None:
    cliente = _ClienteRoteirizado(tempo, EnvioOk(status=200, corpo={"ack": True}))
    with pytest.raises(DeadlineInvalidoError):
        entregar(cliente, "http://destino", Mensagem("t", 1, 2), deadline)
    assert cliente.envios == []
