"""Fila de saída de um Nó, esvaziada por uma thread fora do caminho da resposta HTTP.

O handler que recebe uma Mensagem só enfileira o que precisa enviar e responde. Se enviasse de
dentro dele, dois Nós que chamam um ao outro esperariam um pelo outro até o prazo, e o remetente
veria timeout de uma Mensagem que chegou.
"""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Callable, Generic, Iterable, Optional, TypeVar

T = TypeVar("T")


class Despachante(Generic[T]):
    """Entrega cada item enfileirado a `enviar`, um por vez e em ordem, numa thread própria."""

    def __init__(self, enviar: Callable[[T], None], *, iniciar: bool = True) -> None:
        self._enviar = enviar
        self._fila: "queue.Queue[Optional[T]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        if iniciar:
            self.iniciar()

    def iniciar(self) -> None:
        """Inicia a thread, se ainda não está rodando."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()

    def parar(self) -> None:
        """Encerra a thread depois de esvaziar a fila."""
        self._fila.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def drenar(self) -> None:
        """Bloqueia até a fila esvaziar, inclusive o que for enfileirado durante a espera."""
        self._fila.join()

    def enfileirar(self, itens: Iterable[T]) -> None:
        for item in itens:
            self._fila.put(item)

    def _laco(self) -> None:
        while True:
            item = self._fila.get()
            try:
                if item is None:
                    return
                self._enviar(item)
            except Exception:
                # Um envio que falha não pode matar a thread, ou todos os seguintes se perdem.
                traceback.print_exc()
            finally:
                self._fila.task_done()
