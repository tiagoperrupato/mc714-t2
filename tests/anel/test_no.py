"""Testes do adaptador de rede do Nó do Anel (`src.anel.no`).

Dois níveis, nenhum exigindo Docker:

- **Unitário com cliente fake**: prova que `NoAnel` é um adaptador fino. Ele
  delega a decisão à Máquina_de_Estados pura e apenas despacha as Mensagens de
  saída. O repasse ao Sucessor tem novas tentativas dentro do Deadline. Só
  quando nenhuma é aceita o adaptador chama `com_sucessor_falho` e reemite ao
  próximo Nó vivo, sem reimplementar a eleição.

- **Integração local (sem contêiner)**: sobe os 5 Nós como apps FastAPI em
  uvicorn locais, em portas efêmeras, dispara uma eleição por HTTP e confere que
  todos convergem para o maior id ao consultar `GET /estado`. É a mesma prova do
  cenário `anel normal`, sem Docker.
"""

from __future__ import annotations

import time

import pytest

from src.anel.maquina import EstadoAnel, TIPO_ELEICAO
from src.anel.no import TENTATIVAS_ANTES_DO_SALTO, NoAnel, criar_app
from src.comum.modelos import Mensagem
from src.comum.rede import (
    EnvioConexaoRecusada,
    EnvioOk,
    EnvioTimeout,
)
from tests.apoio import ServidorLocal, porta_livre

ANEL5 = (1, 2, 3, 4, 5)


# ---------------------------------------------------------------------------
# Cliente de rede fake: roteia por destino, sem rede
# ---------------------------------------------------------------------------


class _ClienteFake:
    """`ClienteRede` falso: devolve um `ResultadoEnvio` por destino.

    `mortos` é o conjunto de ids inalcançáveis; um envio a eles devolve o
    resultado configurado em `falha` (timeout por padrão). `perdas` diz quantos
    envios a um id falham antes de ele aceitar. Os demais recebem um `EnvioOk`.
    Registra os envios feitos para inspeção.
    """

    def __init__(self, urls, *, mortos=frozenset(), falha=None, perdas=None) -> None:
        self._por_url = {url: no_id for no_id, url in urls.items()}
        self.mortos = set(mortos)
        self.falha = falha or EnvioTimeout(deadline=1.0, tentativas=1)
        self.perdas = dict(perdas or {})
        self.enviadas: list[tuple[int, Mensagem]] = []

    def enviar(self, destino_url, mensagem, deadline):
        no_id = self._por_url[destino_url]
        self.enviadas.append((no_id, mensagem))
        if self.perdas.get(no_id, 0) > 0:
            self.perdas[no_id] -= 1
            return self.falha
        if no_id in self.mortos:
            return self.falha
        return EnvioOk(status=200, corpo={"ack": True, "no_id": no_id})


def _urls(anel) -> dict[int, str]:
    return {i: f"http://no-{i}" for i in anel}


# ---------------------------------------------------------------------------
# Unitário: delegação e despacho
# ---------------------------------------------------------------------------


def test_disparar_eleicao_despacha_ao_sucessor() -> None:
    urls = _urls(ANEL5)
    cliente = _ClienteFake(urls)
    no = NoAnel(EstadoAnel(no_id=1, anel=ANEL5), urls=urls, cliente=cliente, deadline=1.0)
    try:
        resposta = no.disparar_eleicao()
        no.drenar()  # espera o worker de background terminar o despacho

        assert resposta["no_id"] == 1
        # Emitiu uma Mensagem_Eleicao ao Sucessor (2), com apenas o próprio id.
        assert len(cliente.enviadas) == 1
        destino, msg = cliente.enviadas[0]
        assert destino == 2
        assert msg.tipo == TIPO_ELEICAO
        assert list(msg.corpo["ids"]) == [1]
    finally:
        no.parar()


def test_processar_mensagem_delega_a_maquina_e_repassa() -> None:
    # Nó 2 recebe a Mensagem_Eleicao de 1 e deve repassar a 3 com ids [1, 2].
    urls = _urls(ANEL5)
    cliente = _ClienteFake(urls)
    no = NoAnel(EstadoAnel(no_id=2, anel=ANEL5), urls=urls, cliente=cliente, deadline=1.0)
    try:
        msg = Mensagem(tipo=TIPO_ELEICAO, origem=1, destino=2, corpo={"rodada": "1-1", "ids": [1]})
        resposta = no.processar_mensagem(msg)
        no.drenar()

        assert resposta == {"ack": True, "no_id": 2}
        assert len(cliente.enviadas) == 1
        destino, repassada = cliente.enviadas[0]
        assert destino == 3
        assert list(repassada.corpo["ids"]) == [1, 2]
    finally:
        no.parar()


def test_estado_atual_reporta_lider_apos_eleicao_trivial() -> None:
    # Anel de um Nó: iniciar_eleicao o torna Líder trivial, sem despacho.
    urls = {7: "http://no-7"}
    cliente = _ClienteFake(urls)
    no = NoAnel(EstadoAnel(no_id=7, anel=(7,)), urls=urls, cliente=cliente, deadline=1.0)
    try:
        no.disparar_eleicao()
        no.drenar()
        assert no.estado_atual()["lider"] == 7
        assert cliente.enviadas == []
    finally:
        no.parar()


# ---------------------------------------------------------------------------
# Unitário: regra de salto no timeout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "falha",
    [
        EnvioTimeout(deadline=1.0, tentativas=2),
        EnvioConexaoRecusada(tentativas=1, detalhe="refused"),
    ],
)
def test_salta_sucessor_inalcancavel_e_reemite_ao_proximo(falha) -> None:
    # Sucessor 2 inalcançável: depois das tentativas dentro do Deadline, o
    # adaptador marca 2 morto e reemite a MESMA Mensagem_Eleicao ao próximo vivo (3).
    urls = _urls(ANEL5)
    cliente = _ClienteFake(urls, mortos={2}, falha=falha)
    no = NoAnel(EstadoAnel(no_id=1, anel=ANEL5), urls=urls, cliente=cliente, deadline=0.03)
    try:
        no.disparar_eleicao()
        no.drenar()

        destinos = [d for d, _ in cliente.enviadas]
        assert destinos == [2] * TENTATIVAS_ANTES_DO_SALTO + [3]
        assert 2 in no.estado.mortos
        # A Mensagem reemitida preserva os ids acumulados da original.
        _, reemitida = cliente.enviadas[-1]
        assert list(reemitida.corpo["ids"]) == [1]
    finally:
        no.parar()


@pytest.mark.parametrize(
    "falha",
    [
        EnvioOk(status=200, corpo={"ack": False, "descartada": True}),
        EnvioTimeout(deadline=1.0, tentativas=1),
    ],
)
def test_reenvia_dentro_do_deadline_antes_de_saltar(falha) -> None:
    # A primeira entrega a 2 se perde. A nova tentativa é aceita, e 2 não é saltado.
    urls = _urls(ANEL5)
    cliente = _ClienteFake(urls, falha=falha, perdas={2: 1})
    no = NoAnel(EstadoAnel(no_id=1, anel=ANEL5), urls=urls, cliente=cliente, deadline=0.03)
    try:
        no.disparar_eleicao()
        no.drenar()

        assert [d for d, _ in cliente.enviadas] == [2, 2]
        assert no.estado.mortos == frozenset()
    finally:
        no.parar()


def test_salta_varios_mortos_consecutivos_ate_vivo() -> None:
    # 2 e 3 mortos: o salto encadeia 2 -> 3 -> 4, parando no primeiro vivo.
    urls = _urls(ANEL5)
    cliente = _ClienteFake(urls, mortos={2, 3})
    no = NoAnel(EstadoAnel(no_id=1, anel=ANEL5), urls=urls, cliente=cliente, deadline=0.03)
    try:
        no.disparar_eleicao()
        no.drenar()

        destinos = [d for d, _ in cliente.enviadas]
        n = TENTATIVAS_ANTES_DO_SALTO
        assert destinos == [2] * n + [3] * n + [4]
        assert {2, 3} <= no.estado.mortos
    finally:
        no.parar()


def test_deadline_invalido_recusado_na_construcao() -> None:
    urls = _urls(ANEL5)
    with pytest.raises(ValueError):
        NoAnel(EstadoAnel(no_id=1, anel=ANEL5), urls=urls, cliente=_ClienteFake(urls), deadline=0.0)


# ---------------------------------------------------------------------------
# Integração local (sem Docker): 5 Nós FastAPI, eleição por HTTP
# ---------------------------------------------------------------------------


def test_integracao_local_eleicao_converge_para_maior_id() -> None:
    import httpx

    from src.comum.rede import ClienteRede

    portas = {i: porta_livre() for i in ANEL5}
    urls = {i: f"http://127.0.0.1:{portas[i]}" for i in ANEL5}

    clientes = [ClienteRede(retries=1) for _ in ANEL5]
    nos = {
        i: NoAnel(EstadoAnel(no_id=i, anel=ANEL5), urls=urls, cliente=clientes[idx], deadline=2.0)
        for idx, i in enumerate(ANEL5)
    }
    servidores = [ServidorLocal(criar_app(nos[i]), portas[i]) for i in ANEL5]

    from contextlib import ExitStack

    with ExitStack() as pilha:
        for s in servidores:
            pilha.enter_context(s)
        try:
            # Dispara a eleição no Nó 1 via HTTP de controle.
            r = httpx.post(f"{urls[1]}/iniciar-eleicao", timeout=5.0)
            assert r.status_code == 200

            # Espera a convergência consultando GET /estado de todos os Nós.
            deadline = time.monotonic() + 10.0
            lideres: dict[int, int | None] = {}
            while time.monotonic() < deadline:
                lideres = {
                    i: httpx.get(f"{urls[i]}/estado", timeout=5.0).json()["lider"]
                    for i in ANEL5
                }
                if all(v == 5 for v in lideres.values()):
                    break
                time.sleep(0.05)

            assert set(lideres.values()) == {5}, lideres
            # Numa eleição sem falhas, nenhum Nó vivo deve ter marcado outro como
            # morto: a convergência não pode depender de falsos timeouts.
            for i in ANEL5:
                estado = httpx.get(f"{urls[i]}/estado", timeout=5.0).json()
                assert estado["mortos"] == [], (i, estado["mortos"])
        finally:
            # Para os workers de despacho antes de fechar os clientes de rede,
            # para que nenhum envio aconteça com o cliente já fechado.
            for n in nos.values():
                n.parar()
            for c in clientes:
                c.fechar()
