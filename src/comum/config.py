"""Configuração de um Nó, limites de domínio e validação antes de efeitos colaterais.

Toda configuração é uma dataclass imutável que se
valida contra seu domínio antes de abrir socket, consumir aleatoriedade ou
escrever em disco. A primeira violação levanta `ConfigInvalidaError`, que
identifica o parâmetro, o valor recebido e o domínio aceito, para que um
parâmetro inválido encerre o Nó sem produzir efeito de rede ou de arquivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

# A Semente é um inteiro não negativo com teto no maior inteiro de 32 bits com
# sinal, estável entre plataformas.
SEMENTE_MIN = 0
SEMENTE_MAX = 2_147_483_647

# Deadline em segundos: estritamente positivo e com um teto generoso, porque uma
# chamada de rede que espera minutos já deixou de ser uma chamada com prazo.
DEADLINE_MIN_EXCLUSIVO = 0.0
DEADLINE_MAX = 300.0

# Retries de uma chamada: zero (sem retry) até um teto pequeno, porque retry
# ilimitado viola o Deadline total.
RETRIES_MIN = 0
RETRIES_MAX = 10

# Porta TCP válida.
PORTA_MIN = 1
PORTA_MAX = 65_535

# Algoritmos que um Nó deste projeto pode executar.
ALGORITMOS_ACEITOS: tuple[str, ...] = ("anel", "2pc")


class ConfigInvalidaError(Exception):
    """Parâmetro fora de domínio, com o nome, o valor recebido e o domínio aceito."""

    def __init__(self, parametro: str, valor: object, dominio: str) -> None:
        self.parametro = parametro
        self.valor = valor
        self.dominio = dominio
        super().__init__(
            f"parâmetro inválido '{parametro}': valor recebido {valor!r}; "
            f"domínio aceito: {dominio}"
        )


@dataclass(frozen=True)
class ConfigNo:
    """Configuração completa de um Nó. Imutável, validada antes de qualquer efeito.

    `id` identifica o Nó (inteiro positivo). `algoritmo` diz qual lógica o Nó
    executa. `porta` é onde o servidor HTTP escuta. `semente` determina toda a
    aleatoriedade da execução. `deadline` é o prazo padrão de uma chamada de
    rede, e `retries` o número de tentativas extras respeitando o Deadline total.
    `sucessores` são ids de outros Nós relevantes à topologia (Sucessor no Anel,
    Participantes no 2PC), sempre distintos do próprio `id`.
    """

    id: int
    algoritmo: str
    porta: int
    semente: int
    deadline: float = 1.0
    retries: int = 2
    sucessores: tuple[int, ...] = field(default_factory=tuple)

    def validar(self) -> None:
        """Levanta `ConfigInvalidaError` na primeira violação de domínio.

        Chamada antes de construir RNG, abrir socket ou escrever log, para que
        uma configuração inválida não produza efeito colateral.
        """
        _exigir_int("id", self.id, lambda v: v > 0, "inteiro > 0")
        _exigir(
            "algoritmo",
            self.algoritmo,
            lambda v: isinstance(v, str) and v in ALGORITMOS_ACEITOS,
            f"um de {list(ALGORITMOS_ACEITOS)}",
            tipos=(str,),
        )
        _exigir_int(
            "porta",
            self.porta,
            lambda v: PORTA_MIN <= v <= PORTA_MAX,
            f"inteiro em [{PORTA_MIN}, {PORTA_MAX}]",
        )
        _exigir_int(
            "semente",
            self.semente,
            lambda v: SEMENTE_MIN <= v <= SEMENTE_MAX,
            f"inteiro em [{SEMENTE_MIN}, {SEMENTE_MAX}]",
        )
        _exigir(
            "deadline",
            self.deadline,
            lambda v: DEADLINE_MIN_EXCLUSIVO < v <= DEADLINE_MAX,
            f"número real em ({DEADLINE_MIN_EXCLUSIVO:g}, {DEADLINE_MAX:g}]",
        )
        _exigir_int(
            "retries",
            self.retries,
            lambda v: RETRIES_MIN <= v <= RETRIES_MAX,
            f"inteiro em [{RETRIES_MIN}, {RETRIES_MAX}]",
        )
        _validar_sucessores(self.id, self.sucessores)


def _validar_sucessores(proprio_id: int, sucessores: object) -> None:
    """Exige uma tupla de ids inteiros positivos, distintos e diferentes do próprio."""
    if not isinstance(sucessores, tuple):
        raise ConfigInvalidaError(
            "sucessores", sucessores, "tupla de inteiros > 0, distintos do próprio id"
        )
    vistos: set[int] = set()
    for i, valor in enumerate(sucessores):
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ConfigInvalidaError(
                f"sucessores[{i}]", valor, "inteiro > 0"
            )
        if valor == proprio_id:
            raise ConfigInvalidaError(
                f"sucessores[{i}]", valor, f"inteiro != id do próprio Nó ({proprio_id})"
            )
        if valor in vistos:
            raise ConfigInvalidaError(
                f"sucessores[{i}]", valor, "inteiro não repetido na topologia"
            )
        vistos.add(valor)


def _exigir(
    nome: str, valor, valido, dominio: str, tipos: tuple = (int, float)
) -> None:
    """Levanta `ConfigInvalidaError` se `valor` não for de `tipos` ou falhar em `valido`.

    `bool` é recusado à parte porque é subclasse de `int` em Python.
    """
    if isinstance(valor, bool) or not isinstance(valor, tipos) or not valido(valor):
        raise ConfigInvalidaError(nome, valor, dominio)


def _exigir_int(nome: str, valor, valido, dominio: str) -> None:
    """Variante de `_exigir` para parâmetros estritamente inteiros."""
    _exigir(nome, valor, valido, dominio, tipos=(int,))


@dataclass(frozen=True)
class NoDoAmbiente:
    """Nó do Anel lido do ambiente: config validada, ordem do Anel, URL de cada id e pasta do log."""

    config: ConfigNo
    ids: tuple[int, ...]
    urls: Mapping[int, str]
    log_dir: Path


def ler_ids(variavel: str, bruto: str | None) -> tuple[int, ...]:
    """Parseia "1,2,3,4,5" numa tupla de ids distintos."""
    if not bruto:
        raise ConfigInvalidaError(variavel, bruto, "lista de ids separada por vírgula")
    try:
        ids = tuple(int(parte.strip()) for parte in bruto.split(",") if parte.strip())
    except ValueError as erro:
        raise ConfigInvalidaError(variavel, bruto, "inteiros separados por vírgula") from erro
    if len(ids) < 1:
        raise ConfigInvalidaError(variavel, bruto, "ao menos um id")
    if len(set(ids)) != len(ids):
        raise ConfigInvalidaError(variavel, bruto, "ids distintos")
    return ids


def ler_urls(variavel: str, bruto: str | None) -> dict[int, str]:
    """Parseia "1=http://a:8000,2=http://b:8000" num mapa id -> url base."""
    if not bruto:
        raise ConfigInvalidaError(variavel, bruto, "mapa id=url separado por vírgula")
    mapa: dict[int, str] = {}
    for item in bruto.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ConfigInvalidaError(variavel, item, "cada item no formato id=url")
        chave, url = item.split("=", 1)
        try:
            no_id = int(chave.strip())
        except ValueError as erro:
            raise ConfigInvalidaError(variavel, chave, "id inteiro antes do '='") from erro
        url = url.strip()
        if not url:
            raise ConfigInvalidaError(variavel, item, "url não vazia após o '='")
        mapa[no_id] = url
    if not mapa:
        raise ConfigInvalidaError(variavel, bruto, "ao menos um mapeamento id=url")
    return mapa


def ler_no_do_ambiente(ambiente: Mapping[str, str]) -> NoDoAmbiente:
    """Lê e valida um Nó do Anel, sem efeito colateral.

    `NO_ID` é obrigatória, assim como `ANEL`, a ordem cíclica dos ids ("1,2,3"),
    e `URLS`, o mapa id=url base que cobre todos os ids do Anel, o próprio
    incluído. `PORTA` (8000), `SEMENTE` (0), `DEADLINE` (1.0), `RETRIES` (2) e
    `LOG_DIR` ("results") têm padrão.
    """
    no_id = _ler_numero(ambiente, "NO_ID", int, None)
    ids = ler_ids("ANEL", ambiente.get("ANEL"))
    urls = ler_urls("URLS", ambiente.get("URLS"))
    config = ConfigNo(
        id=no_id,
        algoritmo="anel",
        porta=_ler_numero(ambiente, "PORTA", int, "8000"),
        semente=_ler_numero(ambiente, "SEMENTE", int, "0"),
        deadline=_ler_numero(ambiente, "DEADLINE", float, "1.0"),
        retries=_ler_numero(ambiente, "RETRIES", int, "2"),
        sucessores=tuple(i for i in ids if i != no_id),
    )
    config.validar()

    if no_id not in ids:
        raise ConfigInvalidaError("NO_ID", no_id, f"id presente em ANEL {ids}")
    faltando = [i for i in ids if i not in urls]
    if faltando:
        raise ConfigInvalidaError(
            "URLS", urls, f"deve cobrir todos os ids do ANEL, faltam {faltando}"
        )
    return NoDoAmbiente(
        config=config, ids=ids, urls=urls, log_dir=Path(ambiente.get("LOG_DIR", "results"))
    )


def _ler_numero(ambiente: Mapping[str, str], variavel: str, tipo: type, padrao: str | None):
    """Converte `ambiente[variavel]` com `tipo`, e sem padrão a variável é obrigatória."""
    bruto = ambiente.get(variavel, padrao)
    try:
        return tipo(bruto)
    except (TypeError, ValueError) as erro:
        dominio = "inteiro" if tipo is int else "número real"
        raise ConfigInvalidaError(variavel, bruto, dominio) from erro
