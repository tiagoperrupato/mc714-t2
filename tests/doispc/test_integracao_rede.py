"""Integração do 2PC sobre a rede real: Coordenador e Participantes em uvicorn.

Estes testes sobem o Coordenador e três Participantes como apps FastAPI reais,
cada um num servidor uvicorn local em porta efêmera, e exercitam o fluxo
completo pela rede (httpx ↔ FastAPI), com Log_Duravel em arquivo por
Participante. Não dependem de Docker: provam o mesmo comportamento que o compose
demonstra, mas rodam em qualquer ambiente com pytest (os testes de compose, que
exigem Docker, ficam em `test_integracao_compose.py` e pulam quando o Docker não
está disponível).

Cobrem:
- transação em que todos votam SIM → COMMIT no Coordenador e em todos;
- transação em que um Participante vota NAO (via flag) → ABORT em todos;
- ABORT por timeout quando um Participante está fora e não vota a tempo;
- várias transações, seguidas e concorrentes, nos mesmos Nós;
- id de transação repetido, que devolve a Decisao gravada sem abrir rodada nova;
- DECISAO perdida com o Coordenador vivo, entregue pela retransmissão;
- Participante que vota depois do ABORT por timeout e sai da dúvida pela
  retransmissão.

A ordem write-ahead é verificada indiretamente: a Decisao
recuperada de cada Log_Duravel em arquivo coincide com a reportada, o que só
vale se a gravação ocorreu antes da aplicação/resposta.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.comum.falhas import ControladorFalhas
from src.comum.modelos import Mensagem, No
from src.comum.rede import ClienteRede, EnvioTimeout, ResultadoEnvio
from src.doispc.coordenador import ServicoCoordenador, criar_app as criar_app_coord
from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    ESTADO_WAIT,
    FASE_DECISAO,
    FASE_INICIO,
    VOTO_NAO,
    VOTO_SIM,
    LogDuravelArquivo,
)
from src.doispc.maquina import MSG_DECISAO, MSG_PREPARE
from src.doispc.participante import (
    ServicoParticipante,
    criar_app as criar_app_part,
)
from tests.apoio import ServidorLocal, esperar_ate, porta_livre

COORD_ID = 1
PART_IDS = (11, 12, 13)


class _ClienteComRoteiro:
    """Cliente do Coordenador que perde ou atrasa as Mensagens indicadas por (tipo, destino).

    `perder` diz quantas Mensagens de cada chave somem antes de a rede vê-las. `atrasar` segura
    cada Mensagem da chave antes de enviá-la, na thread que envia.
    """

    def __init__(
        self,
        *,
        perder: dict[tuple[str, int], int] | None = None,
        atrasar: dict[tuple[str, int], float] | None = None,
    ) -> None:
        self._cliente = ClienteRede()
        self._perder = dict(perder or {})
        self._atrasar = dict(atrasar or {})
        self._trava = threading.Lock()

    def enviar(self, destino_url: str, mensagem: Mensagem, deadline: float) -> ResultadoEnvio:
        chave = (mensagem.tipo, mensagem.destino)
        with self._trava:
            perder = self._perder.get(chave, 0) > 0
            if perder:
                self._perder[chave] -= 1
        if perder:
            return EnvioTimeout(deadline=deadline, tentativas=1)
        time.sleep(self._atrasar.get(chave, 0.0))
        return self._cliente.enviar(destino_url, mensagem, deadline)

    def fechar(self) -> None:
        self._cliente.fechar()


class _Cenario2PC:
    """Monta Coordenador + 3 Participantes em servidores locais, com logs em disco.

    `votos` mapeia id de Participante ao Voto desejado (SIM por padrão); é a flag
    que faz um Participante votar NAO. `participantes_ativos` permite deixar um
    Participante fora do ar, para exercitar o ABORT por timeout de coleta.
    `controladores` liga a injeção de falhas ao recebimento de um Participante, e
    `cliente_coord` troca o cliente de rede do Coordenador.
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
        cliente_coord=None,
    ) -> None:
        self._tmp = tmp_path
        self._cliente_coord = cliente_coord or ClienteRede()
        self._votos = votos or {}
        self._controladores = controladores or {}
        self._ativos = participantes_ativos
        self._deadline_voto = deadline_voto
        self._deadline_decisao = deadline_decisao

        self._portas = {no_id: porta_livre() for no_id in (COORD_ID, *PART_IDS)}
        self.coord_no = No(COORD_ID, "127.0.0.1", self._portas[COORD_ID])
        self.part_nos = {
            pid: No(pid, "127.0.0.1", self._portas[pid]) for pid in PART_IDS
        }
        self.logs: dict[int, LogDuravelArquivo] = {}
        self._servidores: list[ServidorLocal] = []

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
            cliente=self._cliente_coord,
            deadline_voto=self._deadline_voto,
            deadline_decisao=self._deadline_decisao,
        )
        self._abrir(criar_app_coord(self.servico_coord), self._portas[COORD_ID])
        return self

    def _abrir(self, app, porta: int) -> None:
        servidor = ServidorLocal(app, porta)
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
    # Todos votam SIM: COMMIT no Coordenador e em todos os Participantes.
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
    # Participante 13 vota NAO via flag: ABORT em todos.
    with _Cenario2PC(tmp_path, votos={13: VOTO_NAO}) as cen:
        resultado = cen.servico_coord.iniciar("tx-abort")
        cen.esperar_decisoes("tx-abort")

    assert resultado["decisao"] == DECISAO_ABORT
    assert cen.decisao_recuperada(COORD_ID, "tx-abort") == DECISAO_ABORT
    for pid in PART_IDS:
        assert cen.decisao_recuperada(pid, "tx-abort") == DECISAO_ABORT


def test_timeout_de_votos_leva_a_abort(tmp_path: Path) -> None:
    # Participante 13 está fora do ar: seu Voto nunca chega. Ao estourar o
    # Deadline de coleta, o Coordenador decide ABORT e difunde.
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
        # P13, que estava fora, recebe o ABORT pela retransmissão. Ele nunca votou,
        # então nenhum PREPARE de rodada nova chegou a ele.
        cen.esperar_decisoes("x", participantes=(13,))
        assert cen.logs[13].recuperar("x").voto is None

    linhas = (tmp_path / "coord.log").read_text(encoding="utf-8").splitlines()
    assert [(json.loads(l)["fase"], json.loads(l)["valor"]) for l in linhas] == [
        (FASE_INICIO, ESTADO_WAIT),
        (FASE_DECISAO, DECISAO_ABORT),
    ]
    for pid in (11, 12):
        assert cen.decisao_recuperada(pid, "x") == DECISAO_ABORT

    # Um Coordenador reiniciado sobre o mesmo log recupera "x" e também não abre rodada nova.
    reiniciado = ServicoCoordenador(
        cen.coord_no,
        cen.part_nos,
        log_duravel=LogDuravelArquivo(tmp_path / "coord.log"),
        cliente=ClienteRede(),
        deadline_voto=0.6,
        deadline_decisao=0.3,
    )
    reiniciado.recuperar()
    assert reiniciado.iniciar("x")["decisao"] == DECISAO_ABORT
    assert (tmp_path / "coord.log").read_text(encoding="utf-8").splitlines() == linhas


def test_decisao_perdida_chega_pela_retransmissao(tmp_path: Path) -> None:
    # A primeira DECISAO a cada Participante some, com o Coordenador vivo.
    cliente = _ClienteComRoteiro(perder={(MSG_DECISAO, pid): 1 for pid in PART_IDS})
    with _Cenario2PC(tmp_path, cliente_coord=cliente) as cen:
        assert cen.servico_coord.iniciar("tx-perdida")["decisao"] == DECISAO_COMMIT
        cen.esperar_decisoes("tx-perdida")
        assert esperar_ate(lambda: cen.servico_coord.estado.concluido)

    for pid in PART_IDS:
        assert cen.decisao_recuperada(pid, "tx-perdida") == DECISAO_COMMIT


def test_voto_depois_do_abort_sai_da_duvida_pela_retransmissao(tmp_path: Path) -> None:
    # O PREPARE a P13 sai depois do prazo de coleta, então P13 vota SIM depois do
    # ABORT por timeout. A primeira DECISAO a ele some, e só a retransmissão o
    # tira da dúvida.
    cliente = _ClienteComRoteiro(
        atrasar={(MSG_PREPARE, 13): 0.8}, perder={(MSG_DECISAO, 13): 1}
    )
    with _Cenario2PC(
        tmp_path, cliente_coord=cliente, deadline_voto=0.6, deadline_decisao=0.3
    ) as cen:
        assert cen.servico_coord.iniciar("tx-tardio")["decisao"] == DECISAO_ABORT
        cen.esperar_decisoes("tx-tardio")
        assert esperar_ate(lambda: cen.servico_coord.estado.concluido)

    assert cen.logs[13].recuperar("tx-tardio").voto == VOTO_SIM
    for pid in PART_IDS:
        assert cen.decisao_recuperada(pid, "tx-tardio") == DECISAO_ABORT
