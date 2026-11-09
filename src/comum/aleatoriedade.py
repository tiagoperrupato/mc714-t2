"""Streams de aleatoriedade independentes por responsabilidade, derivados da Semente.

Cada responsabilidade tem seu próprio gerador. Consumir valores de
um stream não desloca os demais, então, para a mesma Semente, a sequência de
jitter, de drops e de delays é idêntica entre execuções, qualquer que seja a
ordem em que o Nó os consome. É o que torna um Cenário de
falha repetível dígito a dígito.

O Mersenne Twister de `random` é estável entre versões e plataformas, o que
sustenta a reprodutibilidade sem depender de NumPy no núcleo.
"""

from __future__ import annotations

import random

# As sementes derivadas saem de [0, 2**63), o que torna a colisão desprezível.
_LIMITE_SEMENTE = 2**63


class Streams:
    """Os geradores independentes de um Nó: `jitter`, `drop` e `delay`.

    - `jitter`: pequenas variações temporais que não representam falha.
    - `drop`: decide se uma Mensagem é descartada (falha de perda).
    - `delay`: decide o atraso aplicado a uma resposta (falha de atraso).
    """

    jitter: random.Random
    drop: random.Random
    delay: random.Random

    def __init__(self, semente: int) -> None:
        """Deriva as três sementes, na ordem fixa, de um gerador semeado com `semente`.

        A ordem de derivação é parte do contrato de reprodutibilidade: não a
        reordene sem revisar os Cenários gravados.
        """
        spawner = random.Random(semente)
        self.jitter = random.Random(spawner.randrange(_LIMITE_SEMENTE))
        self.drop = random.Random(spawner.randrange(_LIMITE_SEMENTE))
        self.delay = random.Random(spawner.randrange(_LIMITE_SEMENTE))
