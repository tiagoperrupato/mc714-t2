"""Runner em processo de uma Réplica de Eleição em Anel, determinístico por Semente.

Dirige a Máquina_de_Estados pura do Anel (`src.anel.maquina`) com um barramento
de Mensagens em memória, sem rede nem contêiner. O barramento aplica a regra de
salto do Anel quando um Nó alvo está falho e descarta Mensagens com uma taxa de
falhas derivada da Semente (stream `drop` de `Streams`), medindo as métricas do
Requisito 10.2:

- **Tempo_de_Convergencia**: aqui, um tempo lógico em "passos" de entrega de
  Mensagem até todos os Nós vivos conhecerem o mesmo Líder. Medir em passos
  lógicos (e não em segundos de relógio) é o que torna a Réplica reproduzível
  dígito a dígito entre máquinas, respeitando a limitação de ambiente do
  Requisito 13.7 (relógios físicos não são a base da correção nem da comparação).
- **Mensagens**: o total de Mensagens entregues até a convergência.

Modelo de falhas. `taxa_falhas` em [0, 1) é a probabilidade de uma entrega ser
descartada, sorteada do stream `drop` semeado. Como o barramento reage ao
descarte aplicando a regra de salto (`com_sucessor_falho`), um descarte vira a
descoberta de um Sucessor temporariamente inalcançável — exatamente o efeito que
o Anel precisa contornar. `nos_mortos` marca Nós crash-stop, que nunca entregam.

A pureza das Máquinas é preservada: o runner só orquestra `iniciar_eleicao`,
`receber` e `com_sucessor_falho`, sem reimplementar eleição.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from src.anel.maquina import EstadoAnel, com_sucessor_falho, iniciar_eleicao, receber
from src.comum.aleatoriedade import Streams
from src.comum.modelos import Mensagem

# Teto de passos de entrega antes de declarar não-convergência numa Réplica. É
# folgado para qualquer anel pequeno sob falhas moderadas; estourá-lo indica uma
# Réplica patológica (por exemplo, taxa de falhas alta demais) e é reportado como
# falha de réplica pela bateria, que prossegue.
MAX_PASSOS_PADRAO = 100_000


@dataclass(frozen=True)
class ResultadoAnel:
    """Métricas de uma Réplica do Anel (Requisito 10.2).

    `convergiu` indica se todos os Nós vivos chegaram ao mesmo Líder. `lider` é o
    Líder eleito (o maior id vivo, quando convergiu). `tempo_convergencia` é o
    número de passos lógicos de entrega até a convergência; `mensagens` é o total
    de Mensagens entregues. `num_nos` e `taxa_falhas` reproduzem os parâmetros
    variados da Réplica, para a análise cruzar métrica e configuração.
    """

    convergiu: bool
    lider: Optional[int]
    tempo_convergencia: int
    mensagens: int
    num_nos: int
    taxa_falhas: float
    nos_vivos: int


def _anel_de(num_nos: int) -> tuple[int, ...]:
    """Topologia 1 -> 2 -> ... -> num_nos -> 1 (ids iguais à posição)."""
    return tuple(range(1, num_nos + 1))


def executar_replica_anel(
    *,
    num_nos: int,
    taxa_falhas: float,
    semente: int,
    nos_mortos: frozenset[int] = frozenset(),
    iniciador: int = 1,
    max_passos: int = MAX_PASSOS_PADRAO,
) -> ResultadoAnel:
    """Executa uma Réplica de eleição no Anel e devolve suas métricas.

    `num_nos` é o tamanho do Anel; `taxa_falhas` a probabilidade de descarte por
    entrega; `semente` dirige o stream de drop (reproduzível); `nos_mortos` são
    Nós crash-stop; `iniciador` é quem dispara a eleição. Levanta `ValueError`
    para parâmetros fora de domínio, antes de qualquer efeito.
    """
    if not isinstance(num_nos, int) or num_nos < 1:
        raise ValueError(f"num_nos deve ser inteiro >= 1; recebido {num_nos!r}")
    if not 0.0 <= taxa_falhas < 1.0:
        raise ValueError(f"taxa_falhas deve estar em [0, 1); recebido {taxa_falhas!r}")
    anel = _anel_de(num_nos)
    if iniciador not in anel:
        raise ValueError(f"iniciador {iniciador!r} fora do anel {anel}")
    for morto in nos_mortos:
        if morto not in anel:
            raise ValueError(f"nó morto {morto!r} fora do anel {anel}")

    streams = Streams(semente)
    estados: dict[int, EstadoAnel] = {
        i: EstadoAnel(no_id=i, anel=anel) for i in anel
    }
    vivos = frozenset(anel) - frozenset(nos_mortos)
    nos_vivos = len(vivos)

    # Fila de entregas pendentes: cada item é a Mensagem a ser entregue ao seu
    # `destino`. A ordem FIFO dá um escalonamento determinístico das entregas.
    fila: deque[Mensagem] = deque()

    # O iniciador dispara a eleição; se ele estiver morto, o primeiro vivo após
    # ele assume o papel de iniciador, pois um Nó morto não inicia nada.
    iniciador_efetivo = iniciador
    if iniciador not in vivos:
        iniciador_efetivo = _primeiro_vivo(anel, iniciador, vivos)
    if iniciador_efetivo is None:
        # Nenhum Nó vivo: não há eleição possível.
        return ResultadoAnel(
            convergiu=False,
            lider=None,
            tempo_convergencia=0,
            mensagens=0,
            num_nos=num_nos,
            taxa_falhas=taxa_falhas,
            nos_vivos=0,
        )

    estados[iniciador_efetivo], saida = iniciar_eleicao(estados[iniciador_efetivo])
    fila.extend(saida)

    passos = 0
    mensagens = 0
    while fila and passos < max_passos:
        passos += 1
        msg = fila.popleft()
        destino = msg.destino
        remetente = msg.origem

        entregue = destino in vivos and not _descartar(streams, taxa_falhas)
        if not entregue:
            # Entrega falhou: o remetente aplica a regra de salto para o próximo
            # vivo, reemitindo a MESMA Mensagem (Requisito 4.6). A decisão é da
            # Máquina pura; o runner apenas reage ao resultado de transporte.
            estados[remetente], reemitidas = com_sucessor_falho(
                estados[remetente], msg, destino
            )
            fila.extend(reemitidas)
            continue

        mensagens += 1
        estados[destino], saida = receber(estados[destino], msg)
        fila.extend(saida)

        if _todos_convergiram(estados, vivos):
            break

    convergiu = _todos_convergiram(estados, vivos)
    lider = estados[next(iter(vivos))].lider if convergiu else None
    return ResultadoAnel(
        convergiu=convergiu,
        lider=lider,
        tempo_convergencia=passos,
        mensagens=mensagens,
        num_nos=num_nos,
        taxa_falhas=taxa_falhas,
        nos_vivos=nos_vivos,
    )


def _descartar(streams: Streams, taxa: float) -> bool:
    """Decide o descarte consumindo do stream `drop` (reproduzível por Semente).

    Só consome do stream quando há taxa > 0, para que uma taxa nula não desloque
    a sequência de uma Réplica sem falhas.
    """
    if taxa <= 0.0:
        return False
    return streams.drop.random() < taxa


def _primeiro_vivo(anel: tuple[int, ...], a_partir_de: int, vivos: frozenset[int]) -> Optional[int]:
    """Primeiro Nó vivo na ordem do Anel a partir de `a_partir_de` (inclusive)."""
    n = len(anel)
    inicio = anel.index(a_partir_de)
    for passo in range(n):
        candidato = anel[(inicio + passo) % n]
        if candidato in vivos:
            return candidato
    return None


def _todos_convergiram(estados: dict[int, EstadoAnel], vivos: frozenset[int]) -> bool:
    """Verdadeiro quando todos os Nós vivos conhecem o mesmo Líder, o maior id vivo."""
    if not vivos:
        return False
    lideres = {estados[i].lider for i in vivos}
    if lideres != {max(vivos)}:
        return False
    return None not in lideres
