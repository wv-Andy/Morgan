"""
Cancelar una operación que ya está corriendo en el PC (3.4).

Una capacidad corre en su propio hilo, y a un hilo no se le puede parar desde fuera
sin dejar las cosas a medias. Así que **la capacidad se para sola**, en los sitios donde
parar es seguro, preguntando si alguien pidió cancelarla:

- `punto_seguro()`: «si me han cancelado, paro aquí» (lanza `Cancelada`). Una búsqueda
  lo mira cada tantos archivos; una copia, en cada trozo; la notificación, mientras
  espera.
- `sin_vuelta()`: «a partir de aquí ya no paro». Una escritura lo marca **justo antes**
  del cambio de verdad (el renombrado, el borrado). Si la cancelación llega antes, no se
  toca nada; si llega después, la escritura termina y se dice (decisión mía,
  2026-09-24: «se deja y se dice», sin deshacer).

Las dos cosas se deciden bajo el mismo cerrojo que `pedir()`: o la cancelación llega
antes del punto sin vuelta y se para, o llega después y se sabe que no ha parado. No
hay un tercer caso en el que se crea parada una operación que en realidad siguió.

Sin control (la CLI, una prueba que llama a la capacidad directamente) no pasa nada:
los puntos no hacen nada.
"""

import threading
from contextvars import ContextVar


class Cancelada(Exception):
    """La operación se paró en un punto seguro. `motivo`: quién la paró."""

    def __init__(self, motivo: str):
        self.motivo = motivo
        super().__init__(motivo)


class Control:
    def __init__(self) -> None:
        self._cerrojo = threading.Lock()
        self.pedida = threading.Event()
        self.motivo = ""
        self.pasado_sin_vuelta = False

    def pedir(self, motivo: str) -> bool:
        """Pide parar. Devuelve si **va a parar** (False: ya pasó el punto sin vuelta)."""
        with self._cerrojo:
            if not self.pedida.is_set():
                self.motivo = motivo
                self.pedida.set()
            return not self.pasado_sin_vuelta

    def comprobar(self) -> None:
        with self._cerrojo:
            if self.pedida.is_set() and not self.pasado_sin_vuelta:
                raise Cancelada(self.motivo)

    def marcar_sin_vuelta(self) -> None:
        with self._cerrojo:
            if self.pedida.is_set() and not self.pasado_sin_vuelta:
                raise Cancelada(self.motivo)
            self.pasado_sin_vuelta = True


#: El control de la operación en curso. Lo pone el ejecutor; `asyncio.to_thread` lo
#: lleva al hilo de la capacidad.
ACTUAL: ContextVar[Control | None] = ContextVar("control", default=None)


def punto_seguro() -> None:
    control = ACTUAL.get()
    if control is not None:
        control.comprobar()


def sin_vuelta() -> None:
    control = ACTUAL.get()
    if control is not None:
        control.marcar_sin_vuelta()


def pedida() -> bool:
    """Si alguien pidió parar (sin lanzar): para esperas que se cortan solas."""
    control = ACTUAL.get()
    return control is not None and control.pedida.is_set() and not control.pasado_sin_vuelta
