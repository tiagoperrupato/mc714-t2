"""Runner em processo de uma Réplica de Eleição em Anel, determinístico por Semente.

Dirige a Máquina_de_Estados pura do Anel (`src.anel.maquina`) com um barramento
de Mensagens em memória, sem rede nem contêiner. O barramento perde Mensagens com
uma taxa de falhas derivada da Semente (stream `drop` de `Streams`), medindo:

- **Tempo_de_Convergencia**: aqui, um tempo lógico em "passos" de entrega de
  Mensagem até todos os Nós conhecerem o mesmo Líder. Medir em passos
  lógicos (e não em segundos de relógio) é o que torna a Réplica reproduzível
  dígito a dígito entre máquinas. Relógios físicos não são a base da correção
  nem da comparação.
- **Mensagens**: o total de Mensagens enviadas até a convergência, contando as
  perdidas.

Modelo de falhas. `taxa_falhas` em [0, 1) é a probabilidade de uma Mensagem se
perder, sorteada do stream `drop` semeado. O remetente a reenvia até ela chegar,
como o Nó faz dentro do Deadline. Todos os Nós estão vivos. O salto por Nó morto
está nos testes da Máquina e da rede. A bateria conta passos, e não tempo, então
não modela o Deadline que acaba antes de um reenvio dar certo, a suspeita
errada que os testes com rede cobrem.

A pureza das Máquinas é preservada: o runner só orquestra `iniciar_eleicao` e
`receber`, sem reimplementar eleição.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from src.anel.maquina import EstadoAnel, iniciar_eleicao, receber
from src.comum.aleatoriedade import Streams
from src.comum.modelos import Mensagem

# Teto de passos de entrega antes de declarar não-convergência numa Réplica. É
# folgado para qualquer anel pequeno sob falhas moderadas. Estourá-lo indica uma
# Réplica patológica (por exemplo, taxa de falhas alta demais) e é reportado como
# falha de réplica pela bateria, que prossegue.
MAX_PASSOS = 100_000


@dataclass(frozen=True)
class ResultadoAnel:
    """Métricas de uma Réplica do Anel.

    `convergiu` indica se todos os Nós chegaram ao mesmo Líder. `lider` é o
    Líder eleito (o maior id, quando convergiu). `tempo_convergencia` é o
    número de passos lógicos de entrega até a convergência, e `mensagens` o total
    de tentativas de entrega, perdidas incluídas.
    """

    convergiu: bool
    lider: Optional[int]
    tempo_convergencia: int
    mensagens: int


def executar_replica_anel(*, num_nos: int, taxa_falhas: float, semente: int) -> ResultadoAnel:
    """Executa uma Réplica de eleição no Anel 1 -> 2 -> ... -> num_nos -> 1, iniciada pelo Nó 1.

    Levanta `ValueError` para parâmetros fora de domínio, antes de qualquer efeito.
    """
    if not isinstance(num_nos, int) or num_nos < 1:
        raise ValueError(f"num_nos deve ser inteiro >= 1; recebido {num_nos!r}")
    if not 0.0 <= taxa_falhas < 1.0:
        raise ValueError(f"taxa_falhas deve estar em [0, 1); recebido {taxa_falhas!r}")
    anel = tuple(range(1, num_nos + 1))

    streams = Streams(semente)
    estados: dict[int, EstadoAnel] = {i: EstadoAnel(no_id=i, anel=anel) for i in anel}

    # Fila de entregas pendentes. A ordem FIFO dá um escalonamento determinístico.
    fila: deque[Mensagem] = deque()
    estados[1], saida = iniciar_eleicao(estados[1], rodada="1-1")
    fila.extend(saida)

    passos = 0
    mensagens = 0
    while fila and passos < MAX_PASSOS:
        passos += 1
        msg = fila.popleft()
        mensagens += 1
        if descartar(streams, taxa_falhas):
            fila.appendleft(msg)  # perdida: o remetente reenvia
            continue
        estados[msg.destino], saida = receber(estados[msg.destino], msg)
        fila.extend(saida)

        if todos_convergiram(estados, num_nos):
            break

    convergiu = todos_convergiram(estados, num_nos)
    return ResultadoAnel(
        convergiu=convergiu,
        lider=num_nos if convergiu else None,
        tempo_convergencia=passos,
        mensagens=mensagens,
    )


def descartar(streams: Streams, taxa: float) -> bool:
    """Decide o descarte consumindo do stream `drop` (reproduzível por Semente).

    Só consome do stream quando há taxa > 0, para que uma taxa nula não desloque
    a sequência de uma Réplica sem falhas.
    """
    if taxa <= 0.0:
        return False
    return streams.drop.random() < taxa


def todos_convergiram(estados: dict[int, EstadoAnel], lider: int) -> bool:
    """Verdadeiro quando todos os Nós conhecem `lider` como Líder."""
    return all(estado.lider == lider for estado in estados.values())
