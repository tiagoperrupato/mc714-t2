"""Máquina de estados pura da Eleição de Líder em Anel.

Lógica de decisão isolada da rede e do disco: funções puras
sobre estado imutável que, dado o estado corrente e uma Mensagem recebida,
devolvem o novo estado e a lista de Mensagens a enviar. Nada aqui importa
FastAPI nem httpx, e nenhuma função executa entrada ou saída. O app FastAPI
(`src/anel/no.py`) é um adaptador fino que entrega Mensagens a estas funções e despacha
pela rede as Mensagens devolvidas.

Algoritmo: eleição em anel unidirecional com lista acumulada de ids.

- O iniciador abre uma rodada, com um id único que ele mesmo gera, e emite uma
  `Mensagem_Eleicao` com a lista de ids visitados, começando pelo seu.
- Ao receber a `Mensagem_Eleicao`, um Nó acrescenta seu id e repassa ao
  Sucessor. Se a lista já contém o seu id, a volta se completou: ele elege o
  maior id da lista e circula uma `Mensagem_Lider`.
- Cada Nó registra o Líder anunciado e repassa a `Mensagem_Lider` uma vez. Ela
  para no primeiro Nó que já conhecia o resultado da rodada, normalmente quem a
  emitiu.
- Se o repasse ao Sucessor falha dentro do Deadline, o Nó o marca morto e
  repassa ao próximo vivo, por `com_sucessor_falho`.
- Rodadas concorrentes convergem para o maior id vivo, porque o Líder sai do
  conteúdo da lista, e não de quem iniciou.

Um Nó que já conhece o resultado de uma rodada ignora uma `Mensagem_Eleicao`
atrasada dela, que fecharia a mesma rodada de novo com outro Líder. `mortos` é
uma suspeita do detector por timeout, que pode errar. O Nó a zera ao entrar
numa rodada nova e tira dela quem lhe manda Mensagem, e assim um Nó que volta
participa da eleição seguinte.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    `anel[0]`). `mortos` são os ids que este Nó suspeita falhos na rodada
    corrente. Eles são pulados na hora de escolher o Sucessor, mas continuam no
    `anel` para preservar a ordem cíclica. `lider` é o último Líder conhecido,
    ou `None` antes de qualquer eleição concluída. `rodadas_concluidas` guarda
    os ids das rodadas cujo Líder este Nó já conhece.
    """

    no_id: int
    anel: tuple[int, ...]
    mortos: frozenset[int] = field(default_factory=frozenset)
    lider: int | None = None
    rodadas_concluidas: frozenset[str] = field(default_factory=frozenset)

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

    def sucessor_de(self, no_id: int) -> int | None:
        """Próximo Nó vivo após `no_id` na ordem do Anel, pulando mortos.

        Devolve `None` se não há nenhum outro Nó vivo além de `no_id`, caso em que
        a circulação não tem para onde seguir.
        """
        n = len(self.anel)
        inicio = self.anel.index(no_id)
        # `passo` vai de 1 a n - 1 e os ids são distintos, então o candidato nunca é `no_id`.
        for passo in range(1, n):
            candidato = self.anel[(inicio + passo) % n]
            if candidato not in self.mortos:
                return candidato
        return None

    @property
    def meu_sucessor(self) -> int | None:
        """Próximo Nó vivo após este, ou `None` se este é o único vivo."""
        return self.sucessor_de(self.no_id)


def iniciar_eleicao(estado: EstadoAnel, rodada: str) -> tuple[EstadoAnel, list[Mensagem]]:
    """Abre a rodada `rodada` a partir deste Nó.

    `rodada` é um id que nenhuma outra rodada usou. Emite uma `Mensagem_Eleicao`
    com a lista contendo só o próprio id. Num Anel de um Nó só, ele é o Líder
    trivial e nada é emitido.
    """
    estado = replace(estado, mortos=frozenset())
    sucessor = estado.meu_sucessor
    if sucessor is None:
        return _concluir(estado, rodada, estado.no_id), []
    msg = Mensagem(
        tipo=TIPO_ELEICAO,
        origem=estado.no_id,
        destino=sucessor,
        corpo={"rodada": rodada, "ids": [estado.no_id]},
    )
    return estado, [msg]


def receber(estado: EstadoAnel, msg: Mensagem) -> tuple[EstadoAnel, list[Mensagem]]:
    """Decide a próxima ação a partir de uma Mensagem recebida.

    Quem manda Mensagem está vivo, então sai de `mortos`. Despacha pelo `tipo`,
    e Mensagens de tipo desconhecido são ignoradas sem erro.
    """
    estado = replace(estado, mortos=estado.mortos - {msg.origem})
    if msg.tipo == TIPO_ELEICAO:
        return _receber_eleicao(estado, msg)
    if msg.tipo == TIPO_LIDER:
        return _receber_lider(estado, msg)
    return estado, []


def _receber_eleicao(estado: EstadoAnel, msg: Mensagem) -> tuple[EstadoAnel, list[Mensagem]]:
    """Acumula o id e repassa, ou fecha a volta e anuncia o Líder."""
    rodada = str(msg.corpo["rodada"])
    ids_vistos = [int(x) for x in msg.corpo["ids"]]

    if rodada in estado.rodadas_concluidas:
        # Eleição atrasada, por exemplo de um Nó que estava pausado quando foi
        # saltado. Fechar a rodada de novo anunciaria um segundo Líder.
        return estado, []
    if estado.no_id in ids_vistos:
        return _anunciar_lider(estado, rodada, max(ids_vistos))

    # Primeira passagem desta rodada por este Nó: as suspeitas da rodada
    # anterior não valem mais.
    estado = replace(estado, mortos=frozenset())
    adiante = Mensagem(
        tipo=TIPO_ELEICAO,
        origem=estado.no_id,
        destino=estado.meu_sucessor,
        corpo={"rodada": rodada, "ids": ids_vistos + [estado.no_id]},
    )
    return estado, [adiante]


def _receber_lider(estado: EstadoAnel, msg: Mensagem) -> tuple[EstadoAnel, list[Mensagem]]:
    """Registra e repassa o anúncio, ou o encerra se a rodada já era conhecida."""
    rodada = str(msg.corpo["rodada"])
    if rodada in estado.rodadas_concluidas:
        # O anúncio deu a volta: encerra sem reenviar.
        return estado, []
    return _anunciar_lider(estado, rodada, int(msg.corpo["lider"]))


def _anunciar_lider(
    estado: EstadoAnel, rodada: str, lider: int
) -> tuple[EstadoAnel, list[Mensagem]]:
    """Registra o Líder da rodada e repassa a Mensagem_Lider."""
    novo = _concluir(estado, rodada, lider)
    sucessor = novo.meu_sucessor
    if sucessor is None:
        return novo, []
    anuncio = Mensagem(
        tipo=TIPO_LIDER,
        origem=estado.no_id,
        destino=sucessor,
        corpo={"rodada": rodada, "lider": lider},
    )
    return novo, [anuncio]


def _concluir(estado: EstadoAnel, rodada: str, lider: int) -> EstadoAnel:
    """Registra `lider` como o Líder da `rodada`."""
    return replace(
        estado, lider=lider, rodadas_concluidas=estado.rodadas_concluidas | {rodada}
    )


def com_sucessor_falho(
    estado: EstadoAnel, msg: Mensagem, sucessor_falho: int
) -> tuple[EstadoAnel, list[Mensagem]]:
    """Trata um Sucessor falho e salta para o próximo Nó vivo.

    A camada de rede chama esta função quando o repasse de `msg` ao
    `sucessor_falho` não é aceito dentro do Deadline. O Nó marca o Sucessor como morto e
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
