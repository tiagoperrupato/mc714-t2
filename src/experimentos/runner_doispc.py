"""Runner em processo de uma Réplica de 2PC, determinístico por Semente.

Dirige a Máquina_de_Estados pura do 2PC (`src.doispc.maquina`) com um barramento
de Mensagens em memória e um Log_Duravel por Nó, sem rede nem contêiner. Mede as
métricas do Requisito 10.3:

- **Latencia_de_Commit**: tempo lógico do início da transação (difusão dos
  PREPARE) até a Decisao estar gravada no Coordenador. O tempo é a soma das
  latências de rede sorteadas por entrega, acumuladas no caminho crítico, mais
  um custo por hop. Medir em tempo lógico derivado da Semente, e não em segundos
  de relógio, mantém a Réplica reproduzível dígito a dígito (Requisito 13.7).
- **Vazão**: transações commitadas por unidade de tempo lógico — aqui,
  `1 / Latencia_de_Commit` para a transação única da Réplica, a medida natural de
  vazão quando há uma transação por Réplica.
- **Tempo_de_Recuperacao**: quando `simular_recuperacao` é pedido, o custo lógico
  de um Participante reler seu Log_Duravel e reconstruir a Decisao após um
  reinício (Requisito 6.6). É zero quando a recuperação não é simulada.

Modelo de rede. `latencia_base` é a latência média (em unidades lógicas) de um
hop; cada entrega sorteia um valor em [0, 2*latencia_base] do stream `delay`
semeado, então a Latencia_de_Commit é reproduzível para a mesma Semente. O custo
total soma as latências do caminho PREPARE -> VOTO -> DECISAO.

A pureza é preservada: o runner chama `iniciar_transacao`, `coordenador_recebe` e
`participante_recebe`, executando as `Acao` (incluindo a `GravarLog` write-ahead)
na ordem em que a Máquina as devolve, exatamente como o executor de produção faz.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from src.comum.aleatoriedade import Streams
from src.comum.modelos import Mensagem
from src.doispc.log_duravel import (
    VOTO_NAO,
    VOTO_SIM,
    EntradaLog,
    LogDuravel,
    LogDuravelMemoria,
)
from src.doispc.maquina import (
    Aplicar,
    EstadoCoordenador,
    EstadoParticipante,
    GravarLog,
    coordenador_recebe,
    iniciar_transacao,
    participante_recebe,
    recuperar_participante,
)

# Id lógico do Coordenador e base dos ids de Participante no runner. Mantidos
# fixos porque o runner constrói a topologia a partir do número de Participantes.
COORD_ID = 1
PART_BASE = 11

# Teto de passos de entrega por transação, análogo ao do Anel.
MAX_PASSOS_PADRAO = 100_000


@dataclass(frozen=True)
class ResultadoDoisPC:
    """Métricas de uma Réplica do 2PC (Requisito 10.3).

    `decisao` é COMMIT ou ABORT. `latencia_commit` é o tempo lógico até a Decisao
    estar durável no Coordenador; `vazao` é transações por unidade de tempo
    lógico; `tempo_recuperacao` é o custo de reler o Log_Duravel após reinício (0
    se não simulado). `num_participantes` e `latencia_rede` reproduzem os
    parâmetros variados da Réplica.
    """

    decisao: str
    latencia_commit: float
    vazao: float
    tempo_recuperacao: float
    num_participantes: int
    latencia_rede: float
    atomico: bool


@dataclass
class _NoEntrega:
    """Item de entrega: a Mensagem, o instante lógico em que foi enviada."""

    mensagem: Mensagem
    enviada_em: float


def executar_replica_doispc(
    *,
    num_participantes: int,
    latencia_rede: float,
    semente: int,
    votos_nao: frozenset[int] = frozenset(),
    simular_recuperacao: bool = False,
    custo_recuperacao_por_entrada: float = 1.0,
    max_passos: int = MAX_PASSOS_PADRAO,
) -> ResultadoDoisPC:
    """Executa uma Réplica de uma transação 2PC e devolve suas métricas.

    `num_participantes` é quantos Participantes a transação consulta;
    `latencia_rede` é a latência base de um hop (unidades lógicas); `semente`
    dirige o stream de latência (reproduzível); `votos_nao` são ordens de
    Participante (1-based) que votam NAO, forçando o ABORT atômico;
    `simular_recuperacao` liga a medição do Tempo_de_Recuperacao. Valida os
    parâmetros antes de qualquer efeito.
    """
    if not isinstance(num_participantes, int) or num_participantes < 1:
        raise ValueError(
            f"num_participantes deve ser inteiro >= 1; recebido {num_participantes!r}"
        )
    if latencia_rede < 0:
        raise ValueError(f"latencia_rede deve ser >= 0; recebido {latencia_rede!r}")

    streams = Streams(semente)
    participantes = tuple(PART_BASE + k for k in range(num_participantes))
    # Mapeia a ordem 1-based (usada em votos_nao) para o id do Participante.
    ids_nao = frozenset(PART_BASE + (ordem - 1) for ordem in votos_nao)

    logs: dict[int, LogDuravel] = {
        COORD_ID: LogDuravelMemoria(),
        **{p: LogDuravelMemoria() for p in participantes},
    }
    est_part: dict[int, Optional[EstadoParticipante]] = {p: None for p in participantes}

    transacao = f"tx-{semente}"
    est_coord, acoes = iniciar_transacao(COORD_ID, transacao, participantes)

    # Barramento de entregas com instante lógico; o relógio lógico é o máximo dos
    # instantes de chegada no caminho crítico (a Decisao durável no Coordenador).
    fila: deque[_NoEntrega] = deque()
    relogio_decisao = 0.0

    def _despachar(acoes_lst, origem_tempo: float) -> None:
        for acao in acoes_lst:
            if isinstance(acao, GravarLog):
                logs[acao.entrada.no].registrar(acao.entrada)
            elif isinstance(acao, Aplicar):
                pass  # efeito local; não altera o tempo do caminho de rede
            else:  # Enviar
                atraso = _latencia(streams, latencia_rede)
                fila.append(
                    _NoEntrega(mensagem=acao.mensagem, enviada_em=origem_tempo + atraso)
                )

    _despachar(acoes, 0.0)

    passos = 0
    while fila and passos < max_passos:
        passos += 1
        item = fila.popleft()
        msg = item.mensagem
        chegada = item.enviada_em
        destino = msg.destino

        if destino == COORD_ID:
            est_coord, novas = coordenador_recebe(est_coord, COORD_ID, msg)
            # Se esta entrega completou a Decisao, marca o instante da Decisao
            # durável (a GravarLog da Decisao está em `novas`, antes das Enviar).
            if est_coord.decidido and relogio_decisao == 0.0:
                relogio_decisao = chegada
            _despachar(novas, chegada)
        else:
            voto = VOTO_NAO if destino in ids_nao else VOTO_SIM
            est_part[destino], novas = participante_recebe(
                est_part[destino], destino, COORD_ID, msg, voto_desejado=voto
            )
            _despachar(novas, chegada)

    if not est_coord.decidido:
        raise RuntimeError(
            f"transação {transacao} não decidiu em {max_passos} passos"
        )

    decisao = est_coord.decisao or ""
    latencia_commit = max(relogio_decisao, _epsilon(latencia_rede))
    vazao = 1.0 / latencia_commit if latencia_commit > 0 else 0.0

    tempo_recuperacao = 0.0
    if simular_recuperacao:
        tempo_recuperacao = _medir_recuperacao(
            logs, participantes, transacao, custo_recuperacao_por_entrada
        )

    atomico = _verificar_atomicidade(logs, participantes, transacao, decisao)

    return ResultadoDoisPC(
        decisao=decisao,
        latencia_commit=latencia_commit,
        vazao=vazao,
        tempo_recuperacao=tempo_recuperacao,
        num_participantes=num_participantes,
        latencia_rede=latencia_rede,
        atomico=atomico,
    )


def _latencia(streams: Streams, base: float) -> float:
    """Sorteia a latência de um hop em [0, 2*base] do stream `delay` (reproduzível)."""
    if base <= 0.0:
        return 0.0
    return streams.delay.uniform(0.0, 2.0 * base)


def _epsilon(base: float) -> float:
    """Piso de latência para evitar divisão por zero na vazão quando base=0."""
    return base if base > 0 else 1.0


def _medir_recuperacao(
    logs: dict[int, LogDuravel],
    participantes: tuple[int, ...],
    transacao: str,
    custo_por_entrada: float,
) -> float:
    """Custo lógico de recuperar o estado relendo o Log_Duravel de cada Participante.

    Exercita o caminho real de recuperação (`recuperar_participante` sobre o
    `recuperar` do Log_Duravel), de modo que a durabilidade write-ahead seja de
    fato percorrida, e devolve o custo como `custo_por_entrada` por Participante
    recuperado — uma medida lógica estável e reproduzível do Tempo_de_Recuperacao.
    """
    total = 0.0
    for p in participantes:
        persistido = logs[p].recuperar(transacao)
        if persistido.voto is None and persistido.decisao is None:
            continue
        recuperar_participante(persistido)  # reconstrói o estado pós-reinício
        total += custo_por_entrada
    return total


def _verificar_atomicidade(
    logs: dict[int, LogDuravel],
    participantes: tuple[int, ...],
    transacao: str,
    decisao_coord: str,
) -> bool:
    """Verdadeiro se todo Participante que gravou uma Decisao concorda com o Coordenador.

    Atomicidade: nenhum Participante em COMMIT enquanto outro em ABORT para a
    mesma transação. É uma checagem leve sobre os Logs_Duraveis produzidos, útil
    para a bateria sinalizar uma Réplica anômala (e para os invariantes da tarefa
    11 cruzarem com o CSV).
    """
    for p in participantes:
        persistido = logs[p].recuperar(transacao)
        if persistido.decisao is not None and persistido.decisao != decisao_coord:
            return False
    return True
