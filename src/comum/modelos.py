"""Dados imutáveis compartilhados: identidade de Nó e Mensagens.

Tudo aqui é `frozen`, sem estado mutável compartilhado entre componentes.
As Máquinas_de_Estados dos algoritmos recebem e devolvem estes
objetos por valor; a camada de rede os serializa para JSON e de volta.

Este módulo não conhece os tipos de evento específicos de cada algoritmo: a
`Mensagem` carrega um `tipo` livre e um `corpo` de campos arbitrários, para que
o núcleo comum não importe a lógica do Anel nem do 2PC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class No:
    """Identidade de um Nó: id positivo e endereço de rede do seu servidor.

    O `id` é a identidade lógica usada pelos algoritmos (maior id vence a
    eleição, por exemplo). `host` e `porta` localizam o servidor HTTP do Nó.
    """

    id: int
    host: str
    porta: int

    @property
    def base_url(self) -> str:
        """URL base do servidor HTTP deste Nó, sem barra final."""
        return f"http://{self.host}:{self.porta}"


def _congelar_corpo(corpo: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Devolve uma view somente leitura de `corpo`, para manter a Mensagem imutável."""
    if corpo is None:
        return MappingProxyType({})
    return MappingProxyType(dict(corpo))


@dataclass(frozen=True)
class Mensagem:
    """Unidade de comunicação entre Nós, serializável para JSON.

    `tipo` nomeia o evento (por exemplo "eleicao", "lider", "prepare", "voto",
    "decisao") sem que o núcleo precise conhecer a semântica. `origem` e
    `destino` são ids de Nó. `corpo` carrega os campos específicos do evento
    (conjunto de ids acumulados, id de transação, voto, etc.) como um mapa
    somente leitura, preservando a imutabilidade.
    """

    tipo: str
    origem: int
    destino: int
    corpo: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # `frozen=True` impede atribuição comum; usamos `object.__setattr__`
        # apenas para normalizar o corpo numa view imutável na construção.
        object.__setattr__(self, "corpo", _congelar_corpo(self.corpo))

    def para_json(self) -> dict[str, Any]:
        """Representação serializável em dicionário simples (corpo copiado)."""
        return {
            "tipo": self.tipo,
            "origem": self.origem,
            "destino": self.destino,
            "corpo": dict(self.corpo),
        }

    @classmethod
    def de_json(cls, dados: Mapping[str, Any]) -> "Mensagem":
        """Reconstrói uma Mensagem a partir de um dicionário desserializado de JSON."""
        return cls(
            tipo=dados["tipo"],
            origem=dados["origem"],
            destino=dados["destino"],
            corpo=dados.get("corpo") or {},
        )
