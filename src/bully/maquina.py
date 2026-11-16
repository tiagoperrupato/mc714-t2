"""Máquina_de_Estados pura do Bully, com época e prazos entregues como eventos.

Duas regras a separam da variante de Garcia-Molina: o OK leva o Líder em exercício, que não
reabre eleição, e um COORDENADOR de id menor é rejeitado e faz o Nó convocar. Um `Prazo` é uma
ação que o adaptador transforma em temporizador e devolve por `prazo_expirou`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.comum.modelos import Mensagem

TIPO_ELEICAO = "eleicao"
TIPO_OK = "ok"
TIPO_COORDENADOR = "coordenador"

PRAZO_OK = "ok"
PRAZO_COORDENADOR = "coordenador"

FASE_OCIOSA = "ociosa"
FASE_AGUARDANDO_OK = "aguardando_ok"
FASE_AGUARDANDO_COORDENADOR = "aguardando_coordenador"


@dataclass(frozen=True)
class Prazo:
    """Pedido de temporizador, que só vale enquanto a época do Nó for `epoca`."""

    tipo: str
    epoca: int


Acao = Mensagem | Prazo


@dataclass(frozen=True)
class EstadoBully:
    """Estado imutável de um Nó do Bully.

    `ids` é a topologia inteira, guardada em ordem crescente. `lider` é o último Líder aceito,
    ou `None` antes do primeiro. `epoca` cresce a cada convocação e marca os OK e os prazos dela.
    """

    no_id: int
    ids: tuple[int, ...]
    lider: int | None = None
    fase: str = FASE_OCIOSA
    epoca: int = 0

    def __post_init__(self) -> None:
        if self.no_id not in self.ids:
            raise ValueError(f"no_id {self.no_id} não pertence a ids {tuple(self.ids)}")
        if len(set(self.ids)) != len(self.ids):
            raise ValueError(f"ids repetidos: {tuple(self.ids)}")
        object.__setattr__(self, "ids", tuple(sorted(self.ids)))

    @property
    def maiores(self) -> tuple[int, ...]:
        """Ids acima deste Nó, em ordem crescente."""
        return tuple(i for i in self.ids if i > self.no_id)


def iniciar_eleicao(estado: EstadoBully) -> tuple[EstadoBully, list[Acao]]:
    """Convoca uma eleição, ou nada faz se o Nó já está numa."""
    if estado.fase != FASE_OCIOSA:
        return estado, []
    return _convocar(estado)


def receber(estado: EstadoBully, msg: Mensagem) -> tuple[EstadoBully, list[Acao]]:
    """Decide a partir de uma Mensagem recebida. Tipo desconhecido é ignorado."""
    if msg.tipo == TIPO_ELEICAO:
        return _receber_eleicao(estado, msg)
    if msg.tipo == TIPO_OK:
        return _receber_ok(estado, msg)
    if msg.tipo == TIPO_COORDENADOR:
        return _receber_coordenador(estado, msg)
    return estado, []


def prazo_expirou(estado: EstadoBully, prazo: Prazo) -> tuple[EstadoBully, list[Acao]]:
    """Trata um prazo vencido. O de outra época ou de outra fase não muda nada."""
    if prazo.epoca != estado.epoca:
        return estado, []
    if prazo.tipo == PRAZO_OK and estado.fase == FASE_AGUARDANDO_OK:
        return _assumir(estado)
    if prazo.tipo == PRAZO_COORDENADOR and estado.fase == FASE_AGUARDANDO_COORDENADOR:
        return _convocar(estado)
    return estado, []


def _convocar(estado: EstadoBully) -> tuple[EstadoBully, list[Acao]]:
    """Abre uma época nova e desafia os maiores, ou assume se não há nenhum."""
    estado = replace(estado, epoca=estado.epoca + 1)
    if not estado.maiores:
        return _assumir(estado)
    eleicoes: list[Acao] = [
        Mensagem(
            tipo=TIPO_ELEICAO,
            origem=estado.no_id,
            destino=destino,
            corpo={"epoca": estado.epoca},
        )
        for destino in estado.maiores
    ]
    return replace(estado, fase=FASE_AGUARDANDO_OK), [*eleicoes, Prazo(PRAZO_OK, estado.epoca)]


def _assumir(estado: EstadoBully) -> tuple[EstadoBully, list[Acao]]:
    """Torna-se Líder e anuncia a todos os outros."""
    anuncios: list[Acao] = [
        Mensagem(
            tipo=TIPO_COORDENADOR,
            origem=estado.no_id,
            destino=destino,
            corpo={"lider": estado.no_id},
        )
        for destino in estado.ids
        if destino != estado.no_id
    ]
    return replace(estado, lider=estado.no_id, fase=FASE_OCIOSA), anuncios


def _aceitar(estado: EstadoBully, lider: int) -> EstadoBully:
    return replace(estado, lider=lider, fase=FASE_OCIOSA)


def _receber_eleicao(estado: EstadoBully, msg: Mensagem) -> tuple[EstadoBully, list[Acao]]:
    """Responde OK a um menor. O Líder em exercício para aí, os demais convocam."""
    if msg.origem >= estado.no_id:
        return estado, []
    em_exercicio = estado.fase == FASE_OCIOSA and estado.lider == estado.no_id
    ok = Mensagem(
        tipo=TIPO_OK,
        origem=estado.no_id,
        destino=msg.origem,
        corpo={"epoca": msg.corpo["epoca"], "lider": estado.no_id if em_exercicio else None},
    )
    if em_exercicio:
        return estado, [ok]
    novo, acoes = iniciar_eleicao(estado)
    return novo, [ok, *acoes]


def _receber_ok(estado: EstadoBully, msg: Mensagem) -> tuple[EstadoBully, list[Acao]]:
    """Aceita o Líder que o OK traz, ou passa a esperar o COORDENADOR da época atual."""
    lider = msg.corpo["lider"]
    if lider is not None and lider > estado.no_id:
        return _aceitar(estado, lider), []
    if lider is None and estado.fase == FASE_AGUARDANDO_OK and msg.corpo["epoca"] == estado.epoca:
        novo = replace(estado, fase=FASE_AGUARDANDO_COORDENADOR)
        return novo, [Prazo(PRAZO_COORDENADOR, novo.epoca)]
    return estado, []


def _receber_coordenador(estado: EstadoBully, msg: Mensagem) -> tuple[EstadoBully, list[Acao]]:
    """Aceita um Líder maior em qualquer fase. Um menor é rejeitado e o Nó convoca."""
    lider = msg.corpo["lider"]
    if lider > estado.no_id:
        return _aceitar(estado, lider), []
    if lider < estado.no_id:
        return iniciar_eleicao(estado)
    return estado, []
