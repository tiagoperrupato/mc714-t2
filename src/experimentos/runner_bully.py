"""Runner em processo de uma Réplica do Bully, com a regra de contagem e de perda do Anel.

Toda tentativa de entrega a um Nó vivo conta, a perdida inclusive, e a Mensagem a um Nó morto não
conta. O tempo é a maior profundidade causal, em saltos de Mensagem, em que um vivo passou a
aceitar o maior id vivo, e cada tentativa perdida soma um salto. Prazo só dispara com a fila vazia,
o que modela o sistema síncrono.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from src.bully.maquina import Acao, EstadoBully, Prazo, iniciar_eleicao, prazo_expirou, receber
from src.comum.aleatoriedade import Streams
from src.comum.modelos import Mensagem
from src.experimentos.runner_anel import MAX_PASSOS, descartar, todos_convergiram


@dataclass(frozen=True)
class ResultadoBully:
    """Métricas de uma Réplica do Bully.

    `mensagens` e `tempo_convergencia` param quando o último vivo aceita o maior id vivo, como no
    Anel. `mensagens_total` segue até não restar Mensagem nem prazo, e é o que se compara com
    n² − 1.
    """

    convergiu: bool
    lider: Optional[int]
    tempo_convergencia: int
    mensagens: int
    mensagens_total: int


def executar_replica_bully(
    *,
    num_nos: int,
    taxa_falhas: float,
    semente: int,
    nos_mortos: frozenset[int] = frozenset(),
    iniciador: int = 1,
) -> ResultadoBully:
    """Executa uma Réplica com os Nós 1 a `num_nos`, menos `nos_mortos`, iniciada por `iniciador`.

    Levanta `ValueError` para parâmetros fora de domínio, antes de qualquer efeito.
    """
    if not isinstance(num_nos, int) or num_nos < 1:
        raise ValueError(f"num_nos deve ser inteiro >= 1; recebido {num_nos!r}")
    if not 0.0 <= taxa_falhas < 1.0:
        raise ValueError(f"taxa_falhas deve estar em [0, 1); recebido {taxa_falhas!r}")
    ids = tuple(range(1, num_nos + 1))
    if not nos_mortos <= set(ids):
        raise ValueError(
            f"nos_mortos deve estar contido em 1..{num_nos}; recebido {sorted(nos_mortos)}"
        )
    vivos = [i for i in ids if i not in nos_mortos]
    if iniciador not in vivos:
        raise ValueError(f"iniciador deve ser um Nó vivo, um de {vivos}; recebido {iniciador!r}")

    streams = Streams(semente)
    estados = {i: EstadoBully(no_id=i, ids=ids) for i in vivos}
    maior = max(vivos)
    fila: deque[tuple[Mensagem, int]] = deque()
    prazos: deque[tuple[int, Prazo]] = deque()

    aceites: dict[int, int] = {}  # profundidade do evento em que cada Nó passou ao maior
    alcance = 0  # maior profundidade entregue até aqui

    def aplicar(no_id: int, resultado: tuple[EstadoBully, list[Acao]], profundidade: int) -> None:
        """Troca o estado do Nó por um evento na `profundidade` e enfileira as ações dele."""
        anterior = estados[no_id].lider
        estados[no_id], acoes = resultado
        if estados[no_id].lider == maior and anterior != maior:
            aceites[no_id] = profundidade
        for acao in acoes:
            if isinstance(acao, Prazo):
                prazos.append((no_id, acao))
            else:
                fila.append((acao, profundidade + 1))

    aplicar(iniciador, iniciar_eleicao(estados[iniciador]), 0)

    mensagens = 0
    convergencia: Optional[tuple[int, int]] = None
    passos = 0
    while True:
        if convergencia is None and todos_convergiram(estados, maior):
            convergencia = (mensagens, max(aceites.values()))
        if not (fila or prazos) or passos >= MAX_PASSOS:
            break
        passos += 1
        if fila:
            msg, profundidade = fila.popleft()
            if msg.destino not in estados:
                continue
            mensagens += 1
            if descartar(streams, taxa_falhas):
                fila.appendleft((msg, profundidade + 1))  # perdida: o remetente reenvia
                continue
            alcance = max(alcance, profundidade)
            aplicar(msg.destino, receber(estados[msg.destino], msg), profundidade)
        else:
            no_id, prazo = prazos.popleft()
            aplicar(no_id, prazo_expirou(estados[no_id], prazo), alcance)

    mensagens_ate, tempo = convergencia or (mensagens, alcance)
    return ResultadoBully(
        convergiu=convergencia is not None,
        lider=maior if convergencia is not None else None,
        tempo_convergencia=tempo,
        mensagens=mensagens_ate,
        mensagens_total=mensagens,
    )
