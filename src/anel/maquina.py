"""Máquina de estados pura da Eleição de Líder em Anel.

Lógica de decisão isolada da rede e do disco (Requisito 4.1): funções puras
sobre estado imutável que, dado o estado corrente e uma Mensagem recebida,
devolvem o novo estado e a lista de Mensagens a enviar. Nada aqui importa
FastAPI nem httpx, e nenhuma função executa entrada ou saída. O app FastAPI da
tarefa 4 é um adaptador fino que entrega Mensagens a estas funções e despacha
pela rede as Mensagens devolvidas.

Modelo do algoritmo (estilo Chang-Roberts sobre anel unidirecional):

- Um iniciador dispara emitindo uma `Mensagem_Eleicao` que carrega o conjunto
  acumulado de ids visitados, começando pelo seu próprio (Requisito 4.2).
- Ao receber uma `Mensagem_Eleicao`, um Nó acrescenta seu id e repassa ao
  Sucessor; se a Mensagem já contém seu próprio id, a volta ao Anel se completou:
  o Nó determina o Líder como o maior id do conjunto e passa a circular uma
  `Mensagem_Lider` anunciando o eleito (Requisito 4.3).
- Ao receber uma `Mensagem_Lider` com id diferente do seu, o Nó registra o Líder
  anunciado e repassa ao Sucessor (Requisito 4.4). Quando a `Mensagem_Lider`
  retorna a quem a originou, a rodada encerra sem reenvio (Requisito 4.5).
- Se o repasse ao Sucessor estoura o Deadline, o Nó trata o Sucessor como falho
  e repassa ao próximo Nó vivo na ordem do Anel (Requisito 4.6). Isso é modelado
  por `com_sucessor_falho`, que a camada de rede chama ao receber o erro tipado
  de timeout.
- Rodadas concorrentes convergem para o mesmo Líder, o maior id vivo, porque a
  eleição é determinada pelo conteúdo do conjunto acumulado, não por quem
  iniciou (Requisito 4.7).

A identidade de uma rodada é o id do seu iniciador. Isso permite que duas
rodadas concorrentes coexistam sem se confundir e deixa o log reconstruir a
ordem causal por rodada (Requisito 2.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from src.comum.modelos import Mensagem

# Tipos de Mensagem que circulam no Anel. Mantidos como constantes para que o
# app FastAPI e os testes não dependam de strings soltas.
TIPO_ELEICAO = "eleicao"
TIPO_LIDER = "lider"


@dataclass(frozen=True)
class EstadoAnel:
    """Estado imutável de um Nó do Anel.

    `no_id` é a identidade deste Nó. `anel` é a ordem cíclica dos ids de toda a
    topologia (o Sucessor de `anel[i]` é `anel[i+1]`, e o de `anel[-1]` é
    `anel[0]`). `mortos` são os ids que este Nó já descobriu falhos por timeout;
    eles são pulados na hora de escolher o Sucessor, mas continuam no `anel` para
    preservar a ordem cíclica. `lider` é o último Líder conhecido, ou `None` antes
    de qualquer eleição concluída. `rodadas_concluidas` guarda os ids de iniciador
    cujas `Mensagem_Lider` já deram a volta completa, para encerrar sem reenvio
    (Requisito 4.5) e para reconhecer uma rodada já finalizada.
    """

    no_id: int
    anel: tuple[int, ...]
    mortos: frozenset[int] = field(default_factory=frozenset)
    lider: int | None = None
    rodadas_concluidas: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.no_id not in self.anel:
            raise ValueError(
                f"no_id {self.no_id} não pertence ao anel {tuple(self.anel)}"
            )
        if len(set(self.anel)) != len(self.anel):
            raise ValueError(f"anel com ids repetidos: {tuple(self.anel)}")
        # Normaliza as coleções para tipos imutáveis, mantendo a dataclass frozen.
        object.__setattr__(self, "anel", tuple(self.anel))
        object.__setattr__(self, "mortos", frozenset(self.mortos))
        object.__setattr__(self, "rodadas_concluidas", frozenset(self.rodadas_concluidas))

    def sucessor_de(self, no_id: int, *, ignorando: frozenset[int] = frozenset()) -> int | None:
        """Próximo Nó vivo após `no_id` na ordem do Anel, pulando mortos.

        `ignorando` é um conjunto adicional de ids a pular nesta consulta (por
        exemplo, o Sucessor que acabou de estourar o Deadline, antes mesmo de ser
        gravado em `mortos`). Devolve `None` se não há nenhum outro Nó vivo além
        de `no_id`, caso em que a circulação não tem para onde seguir.
        """
        n = len(self.anel)
        inicio = self.anel.index(no_id)
        pular = self.mortos | ignorando
        for passo in range(1, n):
            candidato = self.anel[(inicio + passo) % n]
            if candidato == no_id:
                continue
            if candidato not in pular:
                return candidato
        return None

    @property
    def meu_sucessor(self) -> int | None:
        """Próximo Nó vivo após este, ou `None` se este é o único vivo."""
        return self.sucessor_de(self.no_id)


def iniciar_eleicao(estado: EstadoAnel) -> tuple[EstadoAnel, list[Mensagem]]:
    """Dispara uma Rodada_de_Eleicao a partir deste Nó (Requisito 4.2).

    Emite uma `Mensagem_Eleicao` com o conjunto acumulado contendo apenas o
    próprio id e endereçada ao Sucessor. Se este Nó é o único vivo, ele é o
    Líder trivial e nenhuma Mensagem é emitida.
    """
    sucessor = estado.meu_sucessor
    if sucessor is None:
        # Único Nó vivo: Líder trivial, sem circulação.
        return replace(estado, lider=estado.no_id), []
    msg = Mensagem(
        tipo=TIPO_ELEICAO,
        origem=estado.no_id,
        destino=sucessor,
        corpo={"iniciador": estado.no_id, "ids": [estado.no_id]},
    )
    return estado, [msg]


def receber(estado: EstadoAnel, msg: Mensagem) -> tuple[EstadoAnel, list[Mensagem]]:
    """Decide a próxima ação a partir de uma Mensagem recebida (Requisito 4.1).

    Despacha pelo `tipo` da Mensagem. Devolve o novo estado e as Mensagens de
    saída, sem efeito colateral. Mensagens de tipo desconhecido são ignoradas
    sem erro, para que o Nó seja robusto a ruído.
    """
    if msg.tipo == TIPO_ELEICAO:
        return _receber_eleicao(estado, msg)
    if msg.tipo == TIPO_LIDER:
        return _receber_lider(estado, msg)
    return estado, []


def _receber_eleicao(estado: EstadoAnel, msg: Mensagem) -> tuple[EstadoAnel, list[Mensagem]]:
    """Acumula ids e repassa, ou fecha a volta e anuncia o Líder (Requisito 4.3)."""
    iniciador = int(msg.corpo["iniciador"])
    ids_vistos = [int(x) for x in msg.corpo["ids"]]

    if estado.no_id in ids_vistos:
        # A Mensagem_Eleicao já passou por este Nó: a volta ao Anel se completou.
        # O Líder é o maior id acumulado; começa a circular a Mensagem_Lider.
        lider = max(ids_vistos)
        return _emitir_lider(estado, iniciador, lider)

    acumulado = ids_vistos + [estado.no_id]
    sucessor = estado.meu_sucessor
    if sucessor is None:
        # Nenhum outro Nó vivo para repassar: este Nó fecha sozinho a rodada.
        lider = max(acumulado)
        return replace(estado, lider=lider, rodadas_concluidas=estado.rodadas_concluidas | {iniciador}), []

    adiante = Mensagem(
        tipo=TIPO_ELEICAO,
        origem=estado.no_id,
        destino=sucessor,
        corpo={"iniciador": iniciador, "ids": acumulado},
    )
    return estado, [adiante]


def _emitir_lider(
    estado: EstadoAnel, iniciador: int, lider: int
) -> tuple[EstadoAnel, list[Mensagem]]:
    """Registra o Líder e inicia a circulação da Mensagem_Lider (Requisito 4.3)."""
    sucessor = estado.meu_sucessor
    novo = replace(estado, lider=lider)
    if sucessor is None:
        # Único vivo: já conhece o Líder; a rodada encerra aqui mesmo.
        return replace(novo, rodadas_concluidas=novo.rodadas_concluidas | {iniciador}), []
    anuncio = Mensagem(
        tipo=TIPO_LIDER,
        origem=estado.no_id,
        destino=sucessor,
        corpo={"iniciador": iniciador, "lider": lider},
    )
    return novo, [anuncio]


def _receber_lider(estado: EstadoAnel, msg: Mensagem) -> tuple[EstadoAnel, list[Mensagem]]:
    """Registra e repassa o anúncio, ou encerra a rodada ao voltar à origem."""
    iniciador = int(msg.corpo["iniciador"])
    lider = int(msg.corpo["lider"])

    if estado.no_id == iniciador:
        # A Mensagem_Lider voltou a quem a originou: encerra sem reenviar
        # (Requisito 4.5). Todos os Nós vivos já conhecem o mesmo Líder.
        return (
            replace(
                estado,
                lider=lider,
                rodadas_concluidas=estado.rodadas_concluidas | {iniciador},
            ),
            [],
        )

    # Nó intermediário: registra o Líder anunciado e repassa adiante (Requisito 4.4).
    novo = replace(estado, lider=lider)
    sucessor = novo.meu_sucessor
    if sucessor is None:
        return novo, []
    adiante = Mensagem(
        tipo=TIPO_LIDER,
        origem=estado.no_id,
        destino=sucessor,
        corpo={"iniciador": iniciador, "lider": lider},
    )
    return novo, [adiante]


def com_sucessor_falho(
    estado: EstadoAnel, msg: Mensagem, sucessor_falho: int
) -> tuple[EstadoAnel, list[Mensagem]]:
    """Trata um Sucessor falho por timeout e salta para o próximo Nó vivo (Req. 4.6).

    A camada de rede chama esta função quando o repasse de `msg` ao
    `sucessor_falho` estoura o Deadline. O Nó marca o Sucessor como morto e
    reemite a MESMA Mensagem ao próximo Nó vivo na ordem do Anel, preservando a
    circulação. Se não há mais nenhum Nó vivo para onde seguir, a Mensagem morre
    aqui (o Anel degenerou a um único Nó vivo), e o estado apenas registra o
    morto.
    """
    novo = replace(estado, mortos=estado.mortos | {sucessor_falho})
    proximo = novo.sucessor_de(estado.no_id)
    if proximo is None:
        return novo, []
    reemitida = replace(msg, destino=proximo, origem=estado.no_id)
    return novo, [reemitida]
