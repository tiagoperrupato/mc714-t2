"""Testes do adaptador de rede do Bully (`src.bully.no`) e da leitura do ambiente, sem Docker.

Os unitários usam um cliente falso e um `agendar` falso, que guarda os prazos para o teste
dispará-los. A integração sobe os cinco Nós em uvicorn locais, com rede real e um controlador de
falhas cada.
"""

from __future__ import annotations

import io
import json
import threading
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest

from src.bully.__main__ import construir_no
from src.bully.maquina import (
    FASE_AGUARDANDO_OK,
    FASE_OCIOSA,
    PRAZO_COORDENADOR,
    PRAZO_OK,
    TIPO_COORDENADOR,
    TIPO_ELEICAO,
    EstadoBully,
)
from src.bully.no import GATILHO_INICIO, GATILHO_PEDIDO, NoBully, criar_app
from src.comum.config import ConfigInvalidaError
from src.comum.falhas import ClienteComFalhas, ControladorFalhas
from src.comum.log import LogEstruturado, abrir_log
from src.comum.modelos import Mensagem
from src.comum.rede import ClienteRede, EnvioOk, EnvioTimeout
from tests.apoio import ServidorLocal, esperar_ate, porta_livre

IDS = (1, 2, 3, 4, 5)
URLS = {i: f"http://no-{i}" for i in IDS}
PRAZOS = {PRAZO_OK: 2.5, PRAZO_COORDENADOR: 5.0}


class _ClienteFake:
    """Aceita toda Mensagem, menos as aos `mortos`. Um envio a `travado` espera `liberar` abrir."""

    def __init__(self, *, mortos=frozenset(), travado: int | None = None) -> None:
        self._por_url = {url: i for i, url in URLS.items()}
        self.mortos = set(mortos)
        self.travado = travado
        self.liberar = threading.Event()
        self.enviadas: list[tuple[int, Mensagem]] = []

    def enviar(self, destino_url, mensagem, deadline):
        destino = self._por_url[destino_url]
        if destino == self.travado:
            self.liberar.wait(5.0)
        self.enviadas.append((destino, mensagem))
        if destino in self.mortos:
            return EnvioTimeout(deadline=deadline, tentativas=1)
        return EnvioOk(status=200, corpo={"ack": True, "no_id": destino})

    def destinos(self, tipo: str) -> list[int]:
        return sorted(d for d, m in list(self.enviadas) if m.tipo == tipo)


class _Agenda:
    """`agendar` falso: guarda cada pedido de prazo, e o teste o dispara quando quer."""

    def __init__(self) -> None:
        self.pedidos: list[tuple[float, object]] = []

    def __call__(self, segundos, acao) -> None:
        self.pedidos.append((segundos, acao))

    def disparar(self, indice: int) -> None:
        self.pedidos[indice][1]()


def _no(no_id: int, cliente: _ClienteFake, agenda: _Agenda, *, deadline=1.0, log=None) -> NoBully:
    return NoBully(
        EstadoBully(no_id=no_id, ids=IDS),
        urls=URLS,
        cliente=cliente,
        deadline=deadline,
        prazos=PRAZOS,
        log=log,
        agendar=agenda,
    )


def _eventos(fluxo: io.StringIO, *tipos: str) -> list[dict]:
    linhas = [json.loads(linha) for linha in fluxo.getvalue().splitlines()]
    return [linha for linha in linhas if linha["tipo"] in tipos]


def test_convocar_enfileira_eleicao_por_destino_e_pede_o_prazo_de_ok() -> None:
    cliente, agenda = _ClienteFake(), _Agenda()
    no = _no(3, cliente, agenda)
    try:
        estado = no.disparar_eleicao(GATILHO_PEDIDO)
        no.drenar()
        assert (estado["fase"], estado["epoca"]) == (FASE_AGUARDANDO_OK, 1)
        assert cliente.destinos(TIPO_ELEICAO) == [4, 5]
        assert [segundos for segundos, _ in agenda.pedidos] == [PRAZOS[PRAZO_OK]]
    finally:
        no.parar()


def test_prazo_de_ok_vencido_leva_ao_coordenador() -> None:
    cliente, agenda = _ClienteFake(), _Agenda()
    no = _no(3, cliente, agenda)
    try:
        no.disparar_eleicao(GATILHO_PEDIDO)
        agenda.disparar(0)
        no.drenar()
        assert no.estado.lider == 3
        assert cliente.destinos(TIPO_COORDENADOR) == [1, 2, 4, 5]
    finally:
        no.parar()


def test_destino_travado_nao_atrasa_os_outros() -> None:
    cliente, agenda = _ClienteFake(travado=4), _Agenda()
    no = _no(3, cliente, agenda)
    try:
        no.disparar_eleicao(GATILHO_PEDIDO)
        assert esperar_ate(lambda: cliente.destinos(TIPO_ELEICAO) == [5], prazo=2.0)
        cliente.liberar.set()
        no.drenar()
        assert cliente.destinos(TIPO_ELEICAO) == [4, 5]
    finally:
        cliente.liberar.set()
        no.parar()


def test_iniciou_e_lider_saem_na_ordem_das_trocas() -> None:
    fluxo = io.StringIO()
    cliente, agenda = _ClienteFake(), _Agenda()
    no = _no(3, cliente, agenda, log=LogEstruturado(3, fluxo))
    try:
        no.disparar_eleicao(GATILHO_PEDIDO)
        agenda.disparar(0)
        no.processar_mensagem(Mensagem(TIPO_COORDENADOR, 5, 3, {"lider": 5}))
        no.processar_mensagem(Mensagem(TIPO_COORDENADOR, 2, 3, {"lider": 2}))
        no.drenar()
    finally:
        no.parar()

    resumo = [
        (e["tipo"], e.get("lider", e["epoca"]), e["gatilho"])
        for e in _eventos(fluxo, "bully_iniciou", "bully_lider")
    ]
    assert resumo == [
        ("bully_iniciou", 1, GATILHO_PEDIDO),
        ("bully_lider", 3, "prazo_ok"),
        ("bully_lider", 5, TIPO_COORDENADOR),
        ("bully_iniciou", 2, TIPO_COORDENADOR),
    ]
    recebidas = _eventos(fluxo, "bully_recebeu")
    assert [(e["origem"], e["corpo"]) for e in recebidas] == [(5, {"lider": 5}), (2, {"lider": 2})]


def test_envio_sem_aceite_vai_ao_log() -> None:
    fluxo = io.StringIO()
    cliente, agenda = _ClienteFake(mortos={5}), _Agenda()
    no = _no(4, cliente, agenda, deadline=0.03, log=LogEstruturado(4, fluxo))
    try:
        no.disparar_eleicao(GATILHO_PEDIDO)
        no.drenar()
    finally:
        no.parar()
    [falha] = _eventos(fluxo, "bully_envio_falhou")
    assert (falha["msg_tipo"], falha["destino"], falha["motivo"]) == (
        TIPO_ELEICAO,
        5,
        "EnvioTimeout",
    )


def test_prazo_que_dispara_depois_de_parar_e_ignorado() -> None:
    cliente, agenda = _ClienteFake(), _Agenda()
    no = _no(3, cliente, agenda)
    no.disparar_eleicao(GATILHO_PEDIDO)
    no.parar()
    agenda.disparar(0)
    assert no.estado.fase == FASE_AGUARDANDO_OK
    assert cliente.destinos(TIPO_COORDENADOR) == []


# ---------------------------------------------------------------------------
# Integração local: cinco Nós em uvicorn, rede real e controlador de falhas
# ---------------------------------------------------------------------------


class _Bully:
    """Os cinco Nós com Deadline de 0,2 s, prazos de 0,5 s e 1,0 s e log em `tmp_path`."""

    def __init__(self, tmp_path: Path) -> None:
        portas = {i: porta_livre() for i in IDS}
        self.urls = {i: f"http://127.0.0.1:{portas[i]}" for i in IDS}
        self._clientes = {i: ClienteRede(retries=1) for i in IDS}
        self.controladores = {i: ControladorFalhas(1000 + i) for i in IDS}
        self.logs = {i: tmp_path / f"bully-no-{i}.jsonl" for i in IDS}
        self.nos = {
            i: NoBully(
                EstadoBully(no_id=i, ids=IDS),
                urls=self.urls,
                cliente=ClienteComFalhas(self._clientes[i], self.controladores[i]),
                deadline=0.2,
                prazos={PRAZO_OK: 0.5, PRAZO_COORDENADOR: 1.0},
                log=abrir_log(self.logs[i], i),
            )
            for i in IDS
        }
        self._pilha = ExitStack()
        self._servidores = [
            ServidorLocal(criar_app(self.nos[i], controlador=self.controladores[i]), portas[i])
            for i in IDS
        ]

    def __enter__(self) -> "_Bully":
        for servidor in self._servidores:
            self._pilha.enter_context(servidor)
        return self

    def __exit__(self, *exc: object) -> None:
        for no in self.nos.values():
            no.parar()
        for cliente in self._clientes.values():
            cliente.fechar()
        self._pilha.close()

    def esperar(self, vivos: tuple[int, ...], lider: int) -> None:
        """Espera os `vivos` ociosos com `lider`, pelo `GET /estado` de cada um."""

        def estados() -> dict[int, dict]:
            return {i: httpx.get(f"{self.urls[i]}/estado", timeout=2.0).json() for i in vivos}

        assert esperar_ate(
            lambda: all(
                (e["lider"], e["fase"]) == (lider, FASE_OCIOSA) for e in estados().values()
            ),
            prazo=10.0,
        ), estados()


def test_cinco_nos_elegem_o_5_reelegem_o_4_e_voltam_ao_5(tmp_path: Path) -> None:
    with _Bully(tmp_path) as bully:
        for no in bully.nos.values():
            no.disparar_eleicao(GATILHO_INICIO)
        bully.esperar(IDS, 5)

        bully.controladores[5].injetar_crash()
        r = httpx.post(f"{bully.urls[1]}/iniciar-eleicao", timeout=2.0)
        assert r.status_code == 200
        bully.esperar((1, 2, 3, 4), 4)

        bully.controladores[5].limpar()
        bully.nos[5].disparar_eleicao(GATILHO_INICIO)
        bully.esperar(IDS, 5)

    aceites = [
        evento
        for caminho in bully.logs.values()
        for evento in map(json.loads, caminho.read_text(encoding="utf-8").splitlines())
        if evento["tipo"] == "bully_lider"
    ]
    assert aceites and all(e["lider"] >= e["no"] for e in aceites)


# ---------------------------------------------------------------------------
# construir_no: o ambiente do contêiner
# ---------------------------------------------------------------------------


def _ambiente(monkeypatch, tmp_path: Path, **sobrescritas: str | None) -> None:
    base = {
        "NO_ID": "2",
        "IDS": "1,2,3",
        "URLS": "1=http://a,2=http://b,3=http://c",
        "DEADLINE": "1.0",
        "PRAZO_OK": "2.5",
        "PRAZO_COORDENADOR": "5.0",
        "SEMENTE": "7",
        "LOG_DIR": str(tmp_path),
    }
    base.update(sobrescritas)
    for variavel, valor in base.items():
        if valor is None:
            monkeypatch.delenv(variavel, raising=False)
        else:
            monkeypatch.setenv(variavel, valor)


def test_construir_no_le_o_ambiente_e_grava_o_inicio_da_replica(monkeypatch, tmp_path) -> None:
    _ambiente(monkeypatch, tmp_path)
    no, config, controlador = construir_no()
    try:
        assert (config.id, config.algoritmo, config.porta) == (2, "bully", 8000)
        assert controlador.semente == 7
        assert no.estado == EstadoBully(no_id=2, ids=(1, 2, 3))
        linha = json.loads((tmp_path / "bully-no-2.jsonl").read_text(encoding="utf-8"))
        assert linha["tipo"] == "inicio_replica"
        assert linha["cenario"] == {
            "algoritmo": "bully",
            "ids": [1, 2, 3],
            "deadline": 1.0,
            "prazo_ok": 2.5,
            "prazo_coordenador": 5.0,
        }
    finally:
        no.parar()


@pytest.mark.parametrize(
    ("sobrescritas", "parametro"),
    [
        ({"PRAZO_OK": "2.0"}, "PRAZO_OK"),
        ({"PRAZO_COORDENADOR": "4.5"}, "PRAZO_COORDENADOR"),
        ({"IDS": "1,3"}, "NO_ID"),
        ({"PRAZO_OK": None}, "PRAZO_OK"),
        ({"PRAZO_COORDENADOR": None}, "PRAZO_COORDENADOR"),
    ],
)
def test_construir_no_recusa_antes_de_abrir_o_log(
    monkeypatch, tmp_path, sobrescritas, parametro
) -> None:
    _ambiente(monkeypatch, tmp_path, **sobrescritas)
    with pytest.raises(ConfigInvalidaError) as info:
        construir_no()
    assert info.value.parametro == parametro
    assert list(tmp_path.iterdir()) == []
