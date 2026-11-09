"""Espera com prazo por um efeito que chega depois da chamada que o causou."""

from __future__ import annotations

import time
from typing import Callable


def esperar_ate(condicao: Callable[[], bool], prazo: float = 5.0, intervalo: float = 0.02) -> bool:
    """Devolve True assim que `condicao()` vale, ou False quando o prazo estoura."""
    limite = time.monotonic() + prazo
    while not condicao():
        if time.monotonic() >= limite:
            return False
        time.sleep(intervalo)
    return True
