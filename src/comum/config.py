"""Configuração de um Nó, limites de domínio e validação antes de efeitos colaterais.

Toda configuração é uma dataclass imutável que se
valida contra seu domínio antes de abrir socket, consumir aleatoriedade ou
escrever em disco. A primeira violação levanta `ConfigInvalidaError`, que
identifica o parâmetro, o valor recebido e o domínio aceito, para que um
parâmetro inválido encerre o Nó sem produzir efeito de rede ou de arquivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

