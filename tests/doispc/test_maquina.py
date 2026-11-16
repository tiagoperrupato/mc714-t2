"""Testes da Máquina_de_Estados pura do 2PC e do Log_Duravel write-ahead.

Exercitam a lógica inteiramente em memória, sem rede nem contêiner: um driver
entrega as Mensagens de saída de um Nó ao `...recebe` do destino, executando as
`Acao` na ordem em que a Máquina_de_Estados as devolve. Como essa ordem **é** a
ordem write-ahead (grava antes de enviar/aplicar), o driver prova a durabilidade
sem precisar de disco.

Cobrem:
- todos votam SIM → COMMIT no Coordenador e em todos os Participantes.
- um voto NAO → ABORT em todos.
- Participante relê o Log_Duravel e recupera a mesma Decisao após reinício
  simulado.
- a ordem write-ahead: o Voto é gravado antes de respondido e a Decisao antes de
  aplicada.
- não-reversão de uma Decisao já registrada.
- ABORT por timeout de coleta de Votos.
- recuperação do Coordenador: a Decisao gravada é difundida de novo, e a
  transação em WAIT vira ABORT.
- difusão da DECISAO só a quem ainda não mandou o ACK.
"""

from __future__ import annotations

from typing import Optional

from src.comum.modelos import Mensagem
from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    ESTADO_WAIT,
    FASE_DECISAO,
    FASE_INICIO,
    FASE_VOTO,
    VOTO_NAO,
    VOTO_SIM,
    EntradaLog,
    LogDuravel,
    LogDuravelMemoria,
)
from src.doispc.maquina import (
    MSG_ACK,
    MSG_DECISAO,
    MSG_PREPARE,
    MSG_VOTO,
    Aplicar,
    EstadoParticipante,
    Enviar,
    GravarLog,
    coordenador_decide_por_timeout,
    coordenador_recebe,
    difundir_decisao,
    iniciar_transacao,
    participante_recebe,
    recuperar_coordenador,
    recuperar_participante,
)


# ---------------------------------------------------------------------------
# Driver de simulação em memória
# ---------------------------------------------------------------------------


class DoisPCSimulado:
    """Roda uma transação 2PC entre um Coordenador e N Participantes, em memória.

    Cada Nó tem seu próprio `LogDuravelMemoria`, espelhando o volume por
    Participante do compose. O driver executa as `Acao` devolvidas pela
    Máquina_de_Estados **na ordem** em que vêm: uma `GravarLog` grava no
    Log_Duravel do Nó corrente antes de qualquer `Enviar`/`Aplicar` seguinte,
    exatamente a ordem write-ahead. Mensagens em trânsito
    são entregues ao destino até a fila esvaziar.

    `votos_desejados` mapeia id de Participante ao Voto que ele emitirá (SIM por
    padrão), modelando o Participante configurado para votar NAO. `perder` é o
    conjunto de tipos de Mensagem descartados em trânsito. Perder a DECISAO
    modela o crash do Coordenador depois de decidir, e perder o VOTO, o crash
    antes de decidir.
    """

    def __init__(
        self,
        coordenador_id: int,
        participantes: tuple[int, ...],
        *,
        votos_desejados: Optional[dict[int, str]] = None,
    ) -> None:
        self.coordenador_id = coordenador_id
        self.participantes = participantes
        self.votos_desejados = votos_desejados or {}
        self.logs: dict[int, LogDuravelMemoria] = {
            no_id: LogDuravelMemoria()
            for no_id in (coordenador_id, *participantes)
        }
        self.estado_coordenador = None
        self.estado_participante: dict[int, Optional[EstadoParticipante]] = {
            p: None for p in participantes
        }
        self.historico: list[Mensagem] = []
        self.aplicacoes: list[tuple[int, str, str]] = []
        self.perder: set[str] = set()

    # -- execução de ações na ordem write-ahead ---------------------------- #

    def _executar(self, no_id: int, acoes) -> list[Mensagem]:
        """Executa as `Acao` do Nó em ordem e devolve as Mensagens a despachar."""
        saida: list[Mensagem] = []
        for acao in acoes:
            if isinstance(acao, GravarLog):
                # Write-ahead: durável antes de qualquer envio/aplicação seguinte.
                self.logs[no_id].registrar(acao.entrada)
            elif isinstance(acao, Aplicar):
                self.aplicacoes.append((no_id, acao.transacao, acao.decisao))
            elif isinstance(acao, Enviar):
                saida.append(acao.mensagem)
        return saida

    def _entregar(self, pendentes: list[Mensagem]) -> None:
        """Entrega Mensagens até a fila esvaziar, roteando por tipo e destino."""
        fila = list(pendentes)
        limite = 1000 * max(1, len(self.participantes))
        passos = 0
        while fila:
            passos += 1
            if passos > limite:
                raise AssertionError("transação não convergiu: possível laço")
            msg = fila.pop(0)

            if msg.tipo in self.perder:
                self.historico.append(msg)
                continue

            self.historico.append(msg)

            if msg.destino == self.coordenador_id:
                self.estado_coordenador, acoes = coordenador_recebe(
                    self.estado_coordenador, self.coordenador_id, msg
                )
                fila.extend(self._executar(self.coordenador_id, acoes))
            else:
                destino = msg.destino
                voto = self.votos_desejados.get(destino, VOTO_SIM)
                self.estado_participante[destino], acoes = participante_recebe(
                    self.estado_participante[destino],
                    destino,
                    self.coordenador_id,
                    msg,
                    voto_desejado=voto,
                )
                fila.extend(self._executar(destino, acoes))

    # -- API de alto nível ------------------------------------------------- #

    def executar_transacao(self, transacao: str) -> None:
        """Inicia a transação e processa toda a troca de Mensagens até estabilizar."""
        self.estado_coordenador, acoes = iniciar_transacao(
            self.coordenador_id, transacao, self.participantes
        )
        self._entregar(self._executar(self.coordenador_id, acoes))

    def reiniciar_coordenador(self, transacao: str) -> None:
        """Reconstrói o Coordenador só do Log_Duravel e entrega o que a recuperação difunde."""
        persistido = self.logs[self.coordenador_id].recuperar(transacao)
        self.estado_coordenador, acoes = recuperar_coordenador(
            persistido, self.coordenador_id, self.participantes
        )
        self._entregar(self._executar(self.coordenador_id, acoes))

    def decisoes_participantes(self) -> dict[int, Optional[str]]:
        """Decisao corrente de cada Participante (None se ainda em dúvida)."""
        return {
            p: (self.estado_participante[p].decisao if self.estado_participante[p] else None)
            for p in self.participantes
        }


COORD = 10
PARTS = (1, 2, 3)
TX = "tx-1"


# ---------------------------------------------------------------------------
# Atomicidade: todos SIM -> COMMIT em todos
# ---------------------------------------------------------------------------


def test_todos_sim_resulta_commit_em_todos() -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.executar_transacao(TX)

    assert sim.estado_coordenador.decisao == DECISAO_COMMIT
    assert sim.estado_coordenador.concluido
    for p in PARTS:
        assert sim.decisoes_participantes()[p] == DECISAO_COMMIT
        assert sim.logs[p].recuperar(TX).decisao == DECISAO_COMMIT
        assert sim.logs[p].recuperar(TX).voto == VOTO_SIM
    # O Coordenador também gravou a Decisao de forma durável.
    assert sim.logs[COORD].recuperar(TX).decisao == DECISAO_COMMIT


def test_commit_aplicado_em_cada_participante() -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.executar_transacao(TX)
    aplicados = {no for (no, _tx, dec) in sim.aplicacoes if dec == DECISAO_COMMIT}
    assert aplicados == set(PARTS)


# ---------------------------------------------------------------------------
# Um voto NAO -> ABORT em todos
# ---------------------------------------------------------------------------


def test_um_voto_nao_resulta_abort_em_todos() -> None:
    sim = DoisPCSimulado(COORD, PARTS, votos_desejados={2: VOTO_NAO})
    sim.executar_transacao(TX)

    assert sim.estado_coordenador.decisao == DECISAO_ABORT
    for p in PARTS:
        assert sim.decisoes_participantes()[p] == DECISAO_ABORT
        assert sim.logs[p].recuperar(TX).decisao == DECISAO_ABORT
    assert sim.logs[COORD].recuperar(TX).decisao == DECISAO_ABORT
    # O Participante 2 gravou um Voto NAO, e os demais, SIM.
    assert sim.logs[2].recuperar(TX).voto == VOTO_NAO
    assert sim.logs[1].recuperar(TX).voto == VOTO_SIM


def test_todos_nao_tambem_aborta() -> None:
    sim = DoisPCSimulado(COORD, PARTS, votos_desejados={1: VOTO_NAO, 2: VOTO_NAO, 3: VOTO_NAO})
    sim.executar_transacao(TX)
    assert sim.estado_coordenador.decisao == DECISAO_ABORT
    for p in PARTS:
        assert sim.decisoes_participantes()[p] == DECISAO_ABORT


# ---------------------------------------------------------------------------
# Ordem write-ahead: grava antes de responder/aplicar
# ---------------------------------------------------------------------------


def test_participante_grava_voto_antes_de_responder() -> None:
    # Ao receber PREPARE, a primeira Acao deve ser GravarLog do Voto, e só então
    # a Enviar do Voto. É a ordem write-ahead codificada na sequência de Acao.
    prepare = Mensagem(
        tipo=MSG_PREPARE, origem=COORD, destino=1, corpo={"transacao": TX}
    )
    _, acoes = participante_recebe(None, 1, COORD, prepare, voto_desejado=VOTO_SIM)
    assert isinstance(acoes[0], GravarLog)
    assert acoes[0].entrada.fase == FASE_VOTO
    assert acoes[0].entrada.valor == VOTO_SIM
    assert isinstance(acoes[1], Enviar)
    assert acoes[1].mensagem.tipo == MSG_VOTO


def test_participante_grava_decisao_antes_de_aplicar_e_confirmar() -> None:
    # Com o Voto já emitido, a DECISAO deve gravar antes de aplicar e de enviar o ACK.
    estado = EstadoParticipante(transacao=TX, voto=VOTO_SIM)
    decisao = Mensagem(
        tipo=MSG_DECISAO,
        origem=COORD,
        destino=1,
        corpo={"transacao": TX, "decisao": DECISAO_COMMIT},
    )
    _, acoes = participante_recebe(estado, 1, COORD, decisao)
    assert isinstance(acoes[0], GravarLog)
    assert acoes[0].entrada.fase == FASE_DECISAO
    assert isinstance(acoes[1], Aplicar)
    assert isinstance(acoes[2], Enviar)
    assert acoes[2].mensagem.tipo == MSG_ACK


def test_coordenador_grava_inicio_antes_do_prepare() -> None:
    # A entrada em WAIT vai ao Log_Duravel antes de qualquer PREPARE sair.
    _, acoes = iniciar_transacao(COORD, TX, PARTS)
    assert acoes[0] == GravarLog(EntradaLog(TX, FASE_INICIO, ESTADO_WAIT, COORD))
    assert [a.mensagem.tipo for a in acoes[1:]] == [MSG_PREPARE] * len(PARTS)


def test_coordenador_grava_decisao_antes_de_difundir() -> None:
    # Quando o último Voto chega, a primeira Acao do Coordenador é GravarLog da
    # Decisao, antes das Enviar de DECISAO aos Participantes.
    estado, _ = iniciar_transacao(COORD, TX, PARTS)
    # Entrega três Votos SIM, e o terceiro dispara a decisão.
    for p in PARTS:
        voto = Mensagem(
            tipo=MSG_VOTO, origem=p, destino=COORD, corpo={"transacao": TX, "voto": VOTO_SIM}
        )
        estado, acoes = coordenador_recebe(estado, COORD, voto)
    assert isinstance(acoes[0], GravarLog)
    assert acoes[0].entrada.fase == FASE_DECISAO
    assert acoes[0].entrada.valor == DECISAO_COMMIT
    assert all(isinstance(a, Enviar) for a in acoes[1:])
    assert {a.mensagem.destino for a in acoes[1:]} == set(PARTS)


# ---------------------------------------------------------------------------
# Recuperação após reinício
# ---------------------------------------------------------------------------


def test_participante_recupera_decisao_apos_reinicio() -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.executar_transacao(TX)

    # "Reinício": joga fora o estado em memória e reconstrói só do Log_Duravel.
    for p in PARTS:
        persistido = sim.logs[p].recuperar(TX)
        recuperado = recuperar_participante(persistido)
        assert recuperado.decisao == DECISAO_COMMIT
        assert recuperado.voto == VOTO_SIM
        assert recuperado.aplicada is True


def test_recuperacao_reporta_mesma_decisao_de_antes() -> None:
    # A Decisao antes e depois do reinício é idêntica.
    sim = DoisPCSimulado(COORD, PARTS, votos_desejados={3: VOTO_NAO})
    sim.executar_transacao(TX)
    antes = sim.decisoes_participantes()
    depois = {
        p: recuperar_participante(sim.logs[p].recuperar(TX)).decisao for p in PARTS
    }
    assert antes == depois
    assert set(depois.values()) == {DECISAO_ABORT}


def test_coordenador_recupera_decisao_do_log() -> None:
    # A Decisao gravada volta e é difundida de novo a todos, sem regravar.
    sim = DoisPCSimulado(COORD, PARTS)
    sim.executar_transacao(TX)
    persistido = sim.logs[COORD].recuperar(TX)
    recuperado, acoes = recuperar_coordenador(persistido, COORD, PARTS)
    assert recuperado.decisao == DECISAO_COMMIT
    assert all(isinstance(a, Enviar) for a in acoes)
    assert {a.mensagem.destino for a in acoes} == set(PARTS)
    assert {a.mensagem.corpo["decisao"] for a in acoes} == {DECISAO_COMMIT}


def test_coordenador_em_wait_recupera_com_abort() -> None:
    # Só a entrada em WAIT no log: a recuperação grava ABORT antes de difundi-lo.
    log = LogDuravelMemoria()
    log.registrar(EntradaLog(TX, FASE_INICIO, ESTADO_WAIT, COORD))
    recuperado, acoes = recuperar_coordenador(log.recuperar(TX), COORD, PARTS)
    assert recuperado.decisao == DECISAO_ABORT
    assert acoes[0] == GravarLog(EntradaLog(TX, FASE_DECISAO, DECISAO_ABORT, COORD))
    assert {a.mensagem.destino for a in acoes[1:]} == set(PARTS)


def test_participante_em_duvida_recupera_voto_sem_decisao() -> None:
    # Coordenador cai antes de difundir: o Participante votou SIM e não decidiu.
    # A recuperação restaura o Estado_Em_Duvida (fenômeno do 2PC).
    log = LogDuravelMemoria()
    log.registrar(EntradaLog(transacao=TX, fase=FASE_VOTO, valor=VOTO_SIM, no=1))
    recuperado = recuperar_participante(log.recuperar(TX))
    assert recuperado.voto == VOTO_SIM
    assert recuperado.decisao is None
    assert recuperado.em_duvida is True


# ---------------------------------------------------------------------------
# Não-reversão de uma Decisao registrada
# ---------------------------------------------------------------------------


def test_decisao_nao_reverte_com_mensagem_repetida() -> None:
    # Uma DECISAO repetida não regrava nem muda a Decisao. Só reenvia o ACK.
    estado = EstadoParticipante(transacao=TX, voto=VOTO_SIM, decisao=DECISAO_COMMIT, aplicada=True)
    outra = Mensagem(
        tipo=MSG_DECISAO,
        origem=COORD,
        destino=1,
        corpo={"transacao": TX, "decisao": DECISAO_ABORT},
    )
    novo, acoes = participante_recebe(estado, 1, COORD, outra)
    assert novo.decisao == DECISAO_COMMIT  # não reverteu para ABORT
    assert len(acoes) == 1
    assert isinstance(acoes[0], Enviar)
    assert acoes[0].mensagem.tipo == MSG_ACK


def test_log_recupera_primeira_decisao_mesmo_com_entrada_posterior() -> None:
    # Se duas Decisoes aparecem no log, a recuperação fica com a primeira.
    log = LogDuravelMemoria()
    log.registrar(EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_COMMIT, no=1))
    log.registrar(EntradaLog(transacao=TX, fase=FASE_DECISAO, valor=DECISAO_ABORT, no=1))
    assert log.recuperar(TX).decisao == DECISAO_COMMIT


def test_votos_tardios_apos_decisao_sao_ignorados() -> None:
    estado, _ = iniciar_transacao(COORD, TX, PARTS)
    for p in PARTS:
        voto = Mensagem(
            tipo=MSG_VOTO, origem=p, destino=COORD, corpo={"transacao": TX, "voto": VOTO_SIM}
        )
        estado, _ = coordenador_recebe(estado, COORD, voto)
    assert estado.decisao == DECISAO_COMMIT
    # Um Voto tardio (duplicado por retry) não muda a Decisao nem gera ações.
    tardio = Mensagem(
        tipo=MSG_VOTO, origem=1, destino=COORD, corpo={"transacao": TX, "voto": VOTO_NAO}
    )
    novo, acoes = coordenador_recebe(estado, COORD, tardio)
    assert novo.decisao == DECISAO_COMMIT
    assert acoes == ()


# ---------------------------------------------------------------------------
# ABORT por timeout de coleta de Votos
# ---------------------------------------------------------------------------


def test_timeout_de_votos_resulta_abort() -> None:
    estado, _ = iniciar_transacao(COORD, TX, PARTS)
    # Só um Participante respondeu, e o Deadline estoura antes dos demais.
    voto = Mensagem(
        tipo=MSG_VOTO, origem=1, destino=COORD, corpo={"transacao": TX, "voto": VOTO_SIM}
    )
    estado, _ = coordenador_recebe(estado, COORD, voto)
    assert estado.decisao is None  # ainda coletando

    estado, acoes = coordenador_decide_por_timeout(estado, COORD)
    assert estado.decisao == DECISAO_ABORT
    assert isinstance(acoes[0], GravarLog)
    assert acoes[0].entrada.valor == DECISAO_ABORT


def test_timeout_apos_decisao_nao_reverte() -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.executar_transacao(TX)
    assert sim.estado_coordenador.decisao == DECISAO_COMMIT
    # Um timeout espúrio depois da decisão não deve reverter o COMMIT.
    estado, acoes = coordenador_decide_por_timeout(sim.estado_coordenador, COORD)
    assert estado.decisao == DECISAO_COMMIT
    assert acoes == ()


# ---------------------------------------------------------------------------
# Coordenador cai -> Participantes ficam em Estado_Em_Duvida (fenômeno do 2PC)
# ---------------------------------------------------------------------------


def test_crash_do_coordenador_deixa_participantes_em_duvida() -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.perder = {MSG_DECISAO}  # Coordenador cai antes de difundir a Decisao.
    sim.executar_transacao(TX)

    # O Coordenador chegou a decidir e gravar. Os Participantes votaram SIM mas
    # não receberam a Decisao: ficam bloqueados, aguardando.
    assert sim.estado_coordenador.decisao == DECISAO_COMMIT
    for p in PARTS:
        estado = sim.estado_participante[p]
        assert estado is not None
        assert estado.em_duvida is True
        assert sim.logs[p].recuperar(TX).voto == VOTO_SIM
        assert sim.logs[p].recuperar(TX).decisao is None


def test_coordenador_reiniciado_depois_de_decidir_tira_todos_da_duvida() -> None:
    sim = DoisPCSimulado(COORD, PARTS)
    sim.perder = {MSG_DECISAO}
    sim.executar_transacao(TX)

    sim.perder = set()
    sim.reiniciar_coordenador(TX)
    assert set(sim.decisoes_participantes().values()) == {DECISAO_COMMIT}
    assert sim.estado_coordenador.concluido


def test_coordenador_reiniciado_antes_de_decidir_aborta() -> None:
    # Os Votos SIM nunca chegam: o Coordenador cai em WAIT, com os três em dúvida.
    sim = DoisPCSimulado(COORD, PARTS)
    sim.perder = {MSG_VOTO}
    sim.executar_transacao(TX)
    assert all(sim.estado_participante[p].em_duvida for p in PARTS)

    sim.perder = set()
    sim.reiniciar_coordenador(TX)
    assert set(sim.decisoes_participantes().values()) == {DECISAO_ABORT}
    assert sim.logs[COORD].recuperar(TX).decisao == DECISAO_ABORT
    assert sim.estado_coordenador.concluido


def test_difundir_decisao_so_para_quem_nao_mandou_o_ack() -> None:
    estado, _ = iniciar_transacao(COORD, TX, PARTS)
    assert difundir_decisao(estado, COORD) == ()  # nada a difundir antes de decidir
    for p in PARTS:
        voto = Mensagem(tipo=MSG_VOTO, origem=p, destino=COORD, corpo={"transacao": TX, "voto": VOTO_SIM})
        estado, _ = coordenador_recebe(estado, COORD, voto)
    ack = Mensagem(tipo=MSG_ACK, origem=2, destino=COORD, corpo={"transacao": TX})
    estado, _ = coordenador_recebe(estado, COORD, ack)
    assert [a.mensagem.destino for a in difundir_decisao(estado, COORD)] == [1, 3]


# ---------------------------------------------------------------------------
# Pureza
# ---------------------------------------------------------------------------


def test_participante_recebe_nao_muta_estado() -> None:
    estado = EstadoParticipante(transacao=TX, voto=VOTO_SIM)
    decisao = Mensagem(
        tipo=MSG_DECISAO, origem=COORD, destino=1, corpo={"transacao": TX, "decisao": DECISAO_COMMIT}
    )
    participante_recebe(estado, 1, COORD, decisao)
    assert estado.decisao is None  # o valor original não mudou
    assert estado.aplicada is False


def test_log_memoria_satisfaz_a_interface() -> None:
    assert isinstance(LogDuravelMemoria(), LogDuravel)
