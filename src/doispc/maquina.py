"""Máquina_de_Estados pura do 2PC: Coordenador e Participante.

Toda a lógica aqui é pura (Requisito 6.1): funções sobre estado imutável que,
dada a situação corrente e uma Mensagem (ou um comando de controle), devolvem o
novo estado e uma lista ordenada de `Acao`. Nada neste módulo toca rede ou
disco — não há `import` de FastAPI, httpx ou do próprio `log_duravel` para fazer
IO. A fronteira é a lista de `Acao`: o adaptador de rede (tarefa 6) a executa na
ordem em que aparece.

A ordem das `Acao` **é** a ordem write-ahead exigida pelo enunciado
(Requisito 6.4, 6.5). Quando o Participante vota, a função devolve primeiro uma
`GravarLog` do Voto e só depois a `Enviar` da resposta; quando decide, devolve
primeiro a `GravarLog` da Decisao e só depois o `Aplicar` e a `Enviar` do ACK.
O adaptador apenas obedece à ordem, de modo que a durabilidade não dependa de
ele lembrar a regra: gravar antes de responder está codificado na sequência.

Separar "decidir a partir de uma Mensagem" de "enviar/gravar" é o que torna
atomicidade, abort e recuperação testáveis em memória, sem contêiner.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Optional

from src.comum.modelos import Mensagem
from src.doispc.log_duravel import (
    DECISAO_ABORT,
    DECISAO_COMMIT,
    FASE_DECISAO,
    FASE_VOTO,
    VOTO_NAO,
    VOTO_SIM,
    EntradaLog,
    EstadoPersistido,
)

# Tipos de Mensagem do 2PC que circulam pela rede. Mantidos como constantes para
# não espalhar strings mágicas entre a Máquina_de_Estados e o adaptador.
MSG_PREPARE = "prepare"
MSG_VOTO = "voto"
MSG_DECISAO = "decisao"
MSG_ACK = "ack"


# --------------------------------------------------------------------------- #
# Ações: a fronteira pura entre decidir e executar (enviar/gravar/aplicar).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Enviar:
    """Pedido de envio de uma Mensagem pela rede, executado pelo adaptador."""

    mensagem: Mensagem


@dataclass(frozen=True)
class GravarLog:
    """Pedido de gravação write-ahead de uma `EntradaLog` no Log_Duravel.

    Quando aparece antes de uma `Enviar` ou de um `Aplicar` na lista de `Acao`,
    impõe que a gravação durável ocorra antes da resposta de rede ou da
    aplicação local (Requisito 6.4, 6.5).
    """

    entrada: EntradaLog


@dataclass(frozen=True)
class Aplicar:
    """Pedido de aplicação local da Decisao (efeito do COMMIT/ABORT no Nó)."""

    transacao: str
    decisao: str


# Uma Acao é qualquer um dos pedidos acima; o adaptador os executa em ordem.
Acao = Enviar | GravarLog | Aplicar


# --------------------------------------------------------------------------- #
# Coordenador
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EstadoCoordenador:
    """Estado imutável do Coordenador durante uma transação.

    `transacao` identifica a transação. `participantes` são os ids dos
    Participantes consultados. `votos` mapeia id de Participante ao Voto já
    recebido. `decisao` é a Decisao, quando já tomada e gravada; `None` enquanto
    o Coordenador ainda coleta Votos. `acks` são os ids que já confirmaram a
    Decisao.
    """

    transacao: str
    participantes: tuple[int, ...]
    votos: Mapping[int, str] = field(default_factory=dict)
    decisao: Optional[str] = None
    acks: frozenset[int] = frozenset()

    @property
    def coletou_todos_os_votos(self) -> bool:
        """Verdadeiro quando há um Voto de cada Participante consultado."""
        return all(p in self.votos for p in self.participantes)

    @property
    def decidido(self) -> bool:
        """Verdadeiro quando a Decisao já foi tomada."""
        return self.decisao is not None

    @property
    def concluido(self) -> bool:
        """Verdadeiro quando a Decisao foi tomada e todos confirmaram."""
        return self.decidido and set(self.acks) >= set(self.participantes)


def iniciar_transacao(
    coordenador_id: int, transacao: str, participantes: tuple[int, ...]
) -> tuple[EstadoCoordenador, tuple[Acao, ...]]:
    """Inicia uma transação: cria o estado e emite PREPARE a todos (Requisito 6.2).

    Função pura: devolve o estado inicial do Coordenador e uma `Enviar` de
    PREPARE por Participante. A coleta de Votos acontece em
    `coordenador_recebe`.
    """
    estado = EstadoCoordenador(transacao=transacao, participantes=tuple(participantes))
    acoes = tuple(
        Enviar(
            Mensagem(
                tipo=MSG_PREPARE,
                origem=coordenador_id,
                destino=p,
                corpo={"transacao": transacao},
            )
        )
        for p in participantes
    )
    return estado, acoes


def coordenador_recebe(
    estado: EstadoCoordenador, coordenador_id: int, mensagem: Mensagem
) -> tuple[EstadoCoordenador, tuple[Acao, ...]]:
    """Processa um VOTO ou um ACK recebido pelo Coordenador.

    Ao receber o último Voto que faltava, decide COMMIT se todos votaram SIM,
    senão ABORT (Requisito 6.3, 6.7), e a ordem das `Acao` grava a Decisao no
    Log_Duravel **antes** de difundi-la (Requisito 6.4). ACKs apenas registram
    quem confirmou. Mensagens fora de contexto (outra transação, repetições após
    a decisão) são ignoradas idempotentemente, sem mudar o estado.
    """
    if mensagem.corpo.get("transacao") != estado.transacao:
        return estado, ()

    if mensagem.tipo == MSG_VOTO:
        return _coordenador_recebe_voto(estado, coordenador_id, mensagem)
    if mensagem.tipo == MSG_ACK:
        return _coordenador_recebe_ack(estado, mensagem)
    return estado, ()


def _coordenador_recebe_voto(
    estado: EstadoCoordenador, coordenador_id: int, mensagem: Mensagem
) -> tuple[EstadoCoordenador, tuple[Acao, ...]]:
    """Registra o Voto e, se completou a coleta, decide e difunde a Decisao."""
    votante = mensagem.origem
    if votante not in estado.participantes:
        return estado, ()
    # Uma vez decidido, Votos tardios não mudam nada (não-reversão, Requisito 6.8).
    if estado.decidido:
        return estado, ()
    # Idempotência: o mesmo Voto repetido por retry não altera o estado.
    if votante in estado.votos:
        return estado, ()

    votos = dict(estado.votos)
    votos[votante] = mensagem.corpo.get("voto", VOTO_NAO)
    estado = replace(estado, votos=votos)

    if not estado.coletou_todos_os_votos:
        return estado, ()

    return _coordenador_decide(estado, coordenador_id)


def coordenador_decide_por_timeout(
    estado: EstadoCoordenador, coordenador_id: int
) -> tuple[EstadoCoordenador, tuple[Acao, ...]]:
    """Decide ABORT quando o Deadline de coleta de Votos é excedido (Requisito 6.3).

    Chamado pelo adaptador ao estourar o Deadline antes de todos os Votos
    chegarem. Se a Decisao já foi tomada, não faz nada (não-reversão).
    """
    if estado.decidido:
        return estado, ()
    return _coordenador_decide(estado, coordenador_id, forcar=DECISAO_ABORT)


def _coordenador_decide(
    estado: EstadoCoordenador, coordenador_id: int, *, forcar: Optional[str] = None
) -> tuple[EstadoCoordenador, tuple[Acao, ...]]:
    """Toma a Decisao, grava-a (write-ahead) e só então difunde a todos.

    `forcar` fixa a Decisao (usado pelo timeout, que sempre aborta). Sem
    `forcar`, decide COMMIT apenas se todos os Votos coletados são SIM, senão
    ABORT (Requisito 6.3, 6.7). A `GravarLog` vem antes das `Enviar` de DECISAO,
    codificando a ordem write-ahead do Coordenador.
    """
    if forcar is not None:
        decisao = forcar
    elif all(v == VOTO_SIM for v in estado.votos.values()):
        decisao = DECISAO_COMMIT
    else:
        decisao = DECISAO_ABORT

    estado = replace(estado, decisao=decisao)

    entrada = EntradaLog(
        transacao=estado.transacao,
        fase=FASE_DECISAO,
        valor=decisao,
        no=coordenador_id,
    )
    acoes: list[Acao] = [GravarLog(entrada)]
    for p in estado.participantes:
        acoes.append(
            Enviar(
                Mensagem(
                    tipo=MSG_DECISAO,
                    origem=coordenador_id,
                    destino=p,
                    corpo={"transacao": estado.transacao, "decisao": decisao},
                )
            )
        )
    return estado, tuple(acoes)


def _coordenador_recebe_ack(
    estado: EstadoCoordenador, mensagem: Mensagem
) -> tuple[EstadoCoordenador, tuple[Acao, ...]]:
    """Marca o Participante que confirmou a Decisao, sem outras ações."""
    if not estado.decidido:
        return estado, ()
    estado = replace(estado, acks=estado.acks | {mensagem.origem})
    return estado, ()


def recuperar_coordenador(
    estado_persistido: EstadoPersistido, participantes: tuple[int, ...]
) -> EstadoCoordenador:
    """Reconstrói o Coordenador a partir do Log_Duravel após reinício (Requisito 6.6).

    A mesma Decisao de antes do reinício é restaurada; os Votos individuais não
    são reconstruídos (o Coordenador já havia decidido com base neles e a
    Decisao não reverte).
    """
    return EstadoCoordenador(
        transacao=estado_persistido.transacao,
        participantes=tuple(participantes),
        decisao=estado_persistido.decisao,
    )


# --------------------------------------------------------------------------- #
# Participante
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EstadoParticipante:
    """Estado imutável de um Participante numa transação.

    `transacao` identifica a transação. `voto` é o Voto emitido (ou `None` antes
    do PREPARE). `decisao` é a Decisao gravada e aplicada (ou `None` enquanto o
    Participante ainda aguarda). `aplicada` indica se o efeito local da Decisao
    já foi aplicado.
    """

    transacao: str
    voto: Optional[str] = None
    decisao: Optional[str] = None
    aplicada: bool = False

    @property
    def em_duvida(self) -> bool:
        """Estado_Em_Duvida: votou SIM mas ainda não conhece a Decisao.

        É o bloqueio característico do 2PC quando o Coordenador cai depois de
        coletar o Voto e antes de difundir a Decisao (Requisito 8.5). Não é um
        defeito: é comportamento correto do algoritmo.
        """
        return self.voto == VOTO_SIM and self.decisao is None


def participante_recebe(
    estado: Optional[EstadoParticipante],
    participante_id: int,
    coordenador_id: int,
    mensagem: Mensagem,
    *,
    voto_desejado: str = VOTO_SIM,
) -> tuple[EstadoParticipante, tuple[Acao, ...]]:
    """Processa um PREPARE ou uma DECISAO recebidos por um Participante.

    `voto_desejado` é a inclinação local do Participante (SIM por padrão; NAO
    quando configurado para abortar). Ao receber PREPARE, o Participante decide
    seu Voto, e a ordem das `Acao` **grava o Voto antes de respondê-lo**
    (Requisito 6.5). Ao receber a DECISAO, **grava a Decisao antes de aplicá-la
    e confirmá-la** (Requisito 6.5). A não-reversão é respeitada: uma Decisao já
    registrada não muda ao chegar uma DECISAO repetida (Requisito 6.8).
    """
    if estado is None:
        estado = EstadoParticipante(transacao=mensagem.corpo.get("transacao", ""))

    if mensagem.corpo.get("transacao") != estado.transacao:
        return estado, ()

    if mensagem.tipo == MSG_PREPARE:
        return _participante_recebe_prepare(
            estado, participante_id, coordenador_id, voto_desejado
        )
    if mensagem.tipo == MSG_DECISAO:
        return _participante_recebe_decisao(
            estado, participante_id, coordenador_id, mensagem
        )
    return estado, ()


def _participante_recebe_prepare(
    estado: EstadoParticipante,
    participante_id: int,
    coordenador_id: int,
    voto_desejado: str,
) -> tuple[EstadoParticipante, tuple[Acao, ...]]:
    """Decide o Voto, grava-o (write-ahead) e só então o responde ao Coordenador."""
    # PREPARE repetido depois de já ter votado: reenvia o mesmo Voto sem regravar,
    # mantendo a resposta idempotente (retry do Coordenador).
    if estado.voto is not None:
        return estado, (
            Enviar(
                Mensagem(
                    tipo=MSG_VOTO,
                    origem=participante_id,
                    destino=coordenador_id,
                    corpo={"transacao": estado.transacao, "voto": estado.voto},
                )
            ),
        )

    voto = VOTO_SIM if voto_desejado == VOTO_SIM else VOTO_NAO
    estado = replace(estado, voto=voto)

    entrada = EntradaLog(
        transacao=estado.transacao,
        fase=FASE_VOTO,
        valor=voto,
        no=participante_id,
    )
    acoes: tuple[Acao, ...] = (
        GravarLog(entrada),
        Enviar(
            Mensagem(
                tipo=MSG_VOTO,
                origem=participante_id,
                destino=coordenador_id,
                corpo={"transacao": estado.transacao, "voto": voto},
            )
        ),
    )
    return estado, acoes


def _participante_recebe_decisao(
    estado: EstadoParticipante,
    participante_id: int,
    coordenador_id: int,
    mensagem: Mensagem,
) -> tuple[EstadoParticipante, tuple[Acao, ...]]:
    """Grava a Decisao (write-ahead), aplica-a e confirma com um ACK.

    Se já havia uma Decisao gravada, apenas reenvia o ACK sem regravar nem
    reaplicar (não-reversão e idempotência, Requisito 6.8).
    """
    if estado.decisao is not None:
        return estado, (
            Enviar(
                Mensagem(
                    tipo=MSG_ACK,
                    origem=participante_id,
                    destino=coordenador_id,
                    corpo={"transacao": estado.transacao},
                )
            ),
        )

    decisao = mensagem.corpo.get("decisao", DECISAO_ABORT)
    estado = replace(estado, decisao=decisao, aplicada=True)

    entrada = EntradaLog(
        transacao=estado.transacao,
        fase=FASE_DECISAO,
        valor=decisao,
        no=participante_id,
    )
    acoes: tuple[Acao, ...] = (
        GravarLog(entrada),
        Aplicar(transacao=estado.transacao, decisao=decisao),
        Enviar(
            Mensagem(
                tipo=MSG_ACK,
                origem=participante_id,
                destino=coordenador_id,
                corpo={"transacao": estado.transacao},
            )
        ),
    )
    return estado, acoes


def recuperar_participante(estado_persistido: EstadoPersistido) -> EstadoParticipante:
    """Reconstrói um Participante a partir do Log_Duravel após reinício.

    Restaura o Voto e a Decisao exatamente como gravados antes do reinício
    (Requisito 6.6). Se havia uma Decisao, ela é considerada aplicada; se havia
    só o Voto SIM, o Participante volta em Estado_Em_Duvida, aguardando a
    Decisao do Coordenador.
    """
    return EstadoParticipante(
        transacao=estado_persistido.transacao,
        voto=estado_persistido.voto,
        decisao=estado_persistido.decisao,
        aplicada=estado_persistido.decisao is not None,
    )
