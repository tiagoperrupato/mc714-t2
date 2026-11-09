"""Integração do 2PC sobre a rede real: Coordenador e Participantes em uvicorn.

Estes testes sobem o Coordenador e três Participantes como apps FastAPI reais,
cada um num servidor uvicorn local em porta efêmera, e exercitam o fluxo
completo pela rede (httpx ↔ FastAPI), com Log_Duravel em arquivo por
Participante. Não dependem de Docker: provam o mesmo comportamento que o compose
demonstra, mas rodam em qualquer ambiente com pytest (os testes de compose, que
exigem Docker, ficam em `test_integracao_compose.py` e pulam quando o Docker não
está disponível).

Cobrem:
- transação em que todos votam SIM → COMMIT no Coordenador e em todos (Req. 7.3);
- transação em que um Participante vota NAO (via flag) → ABORT em todos (Req. 7.4);
- ABORT por timeout quando um Participante está fora e não vota a tempo (Req. 7.5);
- várias transações, seguidas e concorrentes, nos mesmos Nós;
- id de transação repetido, que devolve a Decisao gravada sem abrir rodada nova.

A ordem write-ahead (Req. 6.4, 6.5) é verificada indiretamente: a Decisao
recuperada de cada Log_Duravel em arquivo coincide com a reportada, o que só
vale se a gravação ocorreu antes da aplicação/resposta.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
import uvicorn

from src.comum.falhas import ControladorFalhas
from src.comum.modelos import No
from src.comum.rede import ClienteRede
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coord
from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    VOTO_NAO,
    VOTO_SIM,
    LogDuravelArquivo,
)
from src.doispc.participante import (
    ServicoParticipante,
    criar_app as criar_app_part,
)
from tests.espera import esperar_ate

COORD_ID = 1
PART_IDS = (11, 12, 13)


def _porta_livre() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServidorLocal:
    """Sobe um app FastAPI num uvicorn em thread e espera ficar pronto."""

    def __init__(self, app, porta: int) -> None:
        self.porta = porta
        config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning")
        self._servidor = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._servidor.run, daemon=True)

    def __enter__(self) -> "_ServidorLocal":
        self._thread.start()
        limite = time.monotonic() + 10.0
        while time.monotonic() < limite:
            if self._servidor.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("servidor local não ficou pronto a tempo")

    def __exit__(self, *_exc: object) -> None:
        self._servidor.should_exit = True
        self._thread.join(timeout=10.0)


class _Cenario2PC:
    """Monta Coordenador + 3 Participantes em servidores locais, com logs em disco.

    `votos` mapeia id de Participante ao Voto desejado (SIM por padrão); é a flag
    que faz um Participante votar NAO. `participantes_ativos` permite deixar um
    Participante fora do ar, para exercitar o ABORT por timeout de coleta.
    `controladores` liga a injeção de falhas ao recebimento de um Participante.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        votos: dict[int, str] | None = None,
        participantes_ativos: tuple[int, ...] = PART_IDS,
        deadline_voto: float = 3.0,
        deadline_decisao: float = 2.0,
        controladores: dict[int, ControladorFalhas] | None = None,
    ) -> None:
        self._tmp = tmp_path
        self._votos = votos or {}
        self._controladores = controladores or {}
        self._ativos = participantes_ativos
        self._deadline_voto = deadline_voto
        self._deadline_decisao = deadline_decisao

        self._portas = {no_id: _porta_livre() for no_id in (COORD_ID, *PART_IDS)}
        self.coord_no = No(COORD_ID, "127.0.0.1", self._portas[COORD_ID])
        self.part_nos = {
            pid: No(pid, "127.0.0.1", self._portas[pid]) for pid in PART_IDS
        }
        self.logs: dict[int, LogDuravelArquivo] = {}
        self._servidores: list[_ServidorLocal] = []

    def __enter__(self) -> "_Cenario2PC":
        # Participantes primeiro, para estarem escutando quando o PREPARE chegar.
        self._servico_part: dict[int, ServicoParticipante] = {}
        for pid in self._ativos:
            log = LogDuravelArquivo(self._tmp / f"part-{pid}.log")
            self.logs[pid] = log
            servico = ServicoParticipante(
                self.part_nos[pid],
                self.coord_no,
                log_duravel=log,
                cliente=ClienteRede(),
                deadline=self._deadline_decisao,
                voto_desejado=self._votos.get(pid, VOTO_SIM),
            )
            self._servico_part[pid] = servico
            self._abrir(
                criar_app_part(servico, controlador=self._controladores.get(pid)),
                self._portas[pid],
            )

        log_coord = LogDuravelArquivo(self._tmp / "coord.log")
        self.logs[COORD_ID] = log_coord
        self.servico_coord = ServicoCoordenador(
            self.coord_no,
            self.part_nos,
            log_duravel=log_coord,
            cliente=ClienteRede(),
            deadline_voto=self._deadline_voto,
            deadline_decisao=self._deadline_decisao,
        )
        self._abrir(criar_app_coord(self.servico_coord), self._portas[COORD_ID])
        return self

    def _abrir(self, app, porta: int) -> None:
        servidor = _ServidorLocal(app, porta)
        servidor.__enter__()
        self._servidores.append(servidor)

    def __exit__(self, *exc: object) -> None:
        for servidor in reversed(self._servidores):
            servidor.__exit__(*exc)

    def decisao_recuperada(self, no_id: int, transacao: str) -> str | None:
        """Relê o Log_Duravel em disco do Nó e devolve a Decisao persistida."""
        return self.logs[no_id].recuperar(transacao).decisao

    def esperar_decisoes(self, transacao: str, participantes: tuple[int, ...] = PART_IDS) -> None:
        """Espera a DECISAO, que sai da fila do Coordenador depois da resposta, chegar ao disco."""
        assert esperar_ate(
            lambda: all(self.decisao_recuperada(p, transacao) for p in participantes)
        ), {p: self.decisao_recuperada(p, transacao) for p in participantes}


def test_transacao_commita_em_todos(tmp_path: Path) -> None:
    # Todos votam SIM: COMMIT no Coordenador e em todos os Participantes (Req. 7.3).
    with _Cenario2PC(tmp_path) as cen:
        inicio = time.monotonic()
        resultado = cen.servico_coord.iniciar("tx-commit")
        decorrido = time.monotonic() - inicio
        cen.esperar_decisoes("tx-commit")
        # Todos os ACKs voltam bem antes do prazo de envio (2 s). Com o envio feito
        # dentro do handler, cada DECISAO e cada ACK esperavam esse prazo inteiro.
        assert esperar_ate(lambda: cen.servico_coord.estado.concluido, prazo=1.0)

    assert decorrido < 1.0
    assert resultado["decisao"] == DECISAO_COMMIT
    # A Decisao foi gravada no Log_Duravel (em disco) de cada Nó — write-ahead.
    assert cen.decisao_recuperada(COORD_ID, "tx-commit") == DECISAO_COMMIT
    for pid in PART_IDS:
        assert cen.decisao_recuperada(pid, "tx-commit") == DECISAO_COMMIT


def test_transacao_aborta_quando_um_vota_nao(tmp_path: Path) -> None:
    # Participante 13 vota NAO via flag: ABORT em todos (Req. 7.4).
    with _Cenario2PC(tmp_path, votos={13: VOTO_NAO}) as cen:
        resultado = cen.servico_coord.iniciar("tx-abort")
        cen.esperar_decisoes("tx-abort")

    assert resultado["decisao"] == DECISAO_ABORT
    assert cen.decisao_recuperada(COORD_ID, "tx-abort") == DECISAO_ABORT
    for pid in PART_IDS:
        assert cen.decisao_recuperada(pid, "tx-abort") == DECISAO_ABORT


def test_timeout_de_votos_leva_a_abort(tmp_path: Path) -> None:
    # Participante 13 está fora do ar: seu Voto nunca chega. Ao estourar o
    # Deadline de coleta, o Coordenador decide ABORT e difunde (Req. 7.5).
    with _Cenario2PC(
        tmp_path,
        participantes_ativos=(11, 12),
        deadline_voto=0.6,
        deadline_decisao=0.3,
    ) as cen:
        inicio = time.monotonic()
        resultado = cen.servico_coord.iniciar("tx-timeout")
        decorrido = time.monotonic() - inicio
        cen.esperar_decisoes("tx-timeout", participantes=(11, 12))

    assert resultado["decisao"] == DECISAO_ABORT
    assert cen.decisao_recuperada(COORD_ID, "tx-timeout") == DECISAO_ABORT
    # Os Participantes vivos recebem a DECISAO de ABORT.
    assert cen.decisao_recuperada(11, "tx-timeout") == DECISAO_ABORT
    assert cen.decisao_recuperada(12, "tx-timeout") == DECISAO_ABORT
    # A decisão saiu perto do Deadline de Voto, não antes nem muito depois.
    assert decorrido < 2.0


def test_transacoes_seguidas_commitam(tmp_path: Path) -> None:
    # Cada Nó guarda o estado por transação: a segunda não é ignorada.
    with _Cenario2PC(tmp_path) as cen:
        for tx in ("tx-a", "tx-b", "tx-c"):
            assert cen.servico_coord.iniciar(tx)["decisao"] == DECISAO_COMMIT
            cen.esperar_decisoes(tx)

    for tx in ("tx-a", "tx-b", "tx-c"):
        for pid in PART_IDS:
            assert cen.decisao_recuperada(pid, tx) == DECISAO_COMMIT, (tx, pid)


def test_transacoes_concorrentes_commitam(tmp_path: Path) -> None:
    transacoes = [f"tx-{i}" for i in range(5)]
    with _Cenario2PC(tmp_path) as cen:
        with ThreadPoolExecutor(max_workers=len(transacoes)) as pool:
            decisoes = list(pool.map(lambda tx: cen.servico_coord.iniciar(tx)["decisao"], transacoes))
        for tx in transacoes:
            cen.esperar_decisoes(tx)

    assert decisoes == [DECISAO_COMMIT] * len(transacoes)


def test_id_repetido_devolve_a_decisao_gravada(tmp_path: Path) -> None:
    # Com P13 em crash, "x" aborta por timeout. Depois que P13 volta, repetir "x"
    # não abre rodada nova, que decidiria COMMIT e deixaria P11 e P12 com ABORT.
    p13 = ControladorFalhas(2026)
    with _Cenario2PC(
        tmp_path, controladores={13: p13}, deadline_voto=0.6, deadline_decisao=0.3
    ) as cen:
        p13.injetar_crash()
        assert cen.servico_coord.iniciar("x")["decisao"] == DECISAO_ABORT
        cen.esperar_decisoes("x", participantes=(11, 12))
        p13.limpar()

        assert cen.servico_coord.iniciar("x")["decisao"] == DECISAO_ABORT
        assert cen._servico_part[13].estado is None

    linhas = (tmp_path / "coord.log").read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["valor"] for l in linhas] == [DECISAO_ABORT]
    for pid in (11, 12):
        assert cen.decisao_recuperada(pid, "x") == DECISAO_ABORT

    # Um Coordenador novo sobre o mesmo log também devolve a Decisao gravada.
    reiniciado = ServicoCoordenador(
        cen.coord_no,
        cen.part_nos,
        log_duravel=LogDuravelArquivo(tmp_path / "coord.log"),
        cliente=ClienteRede(),
        deadline_voto=0.6,
        deadline_decisao=0.3,
    )
    assert reiniciado.iniciar("x")["decisao"] == DECISAO_ABORT
    assert reiniciado.estado is None
