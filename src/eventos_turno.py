"""
Lo que va pasando en un turno, para quien lo esté escuchando (V2.0.14).

Es la pieza del streaming de `/chat/stream`: el agente y la cadena de modelos
**emiten** eventos —«empiezo la vuelta 2», «uso `search_web`», «contestó un
respaldo»— y la ruta los convierte en líneas NDJSON mientras el turno sigue.
Diseño aprobado en `docs/agente.md`.

## Por qué una `ContextVar` y no el `event_handler` del agente

**Hay un solo agente para todas las personas.** La API lo construye una vez y
atiende a todo el mundo con él, así que `agent.event_handler` es un único objeto
compartido. Conectar el streaming ahí haría que los eventos del turno de Ana
salieran en la respuesta de Bea, y no como un caso raro: en cuanto dos personas
escribieran a la vez.

El canal vive en una `ContextVar`, igual que el usuario de la petición
(`src/identidad/contexto.py`) y la medición del tiempo (`src/observabilidad.py`).
La ruta lo abre, lanza el turno en un hilo con `copy_context()`, y como eso copia
la *variable* y no el objeto, el hilo escribe en la misma cola que la ruta lee.

## Sin canal no pasa nada

Emitir fuera de una petición de streaming —la CLI, `/chat`, una prueba— no
encuentra canal y no hace nada. Nadie tiene que acordarse de comprobarlo.

## Lo que NO viaja

**Los argumentos de las herramientas.** Uno puede llevar una ruta del equipo, el
contenido de un archivo o un secreto que el modelo haya copiado de algún sitio,
y el evento sale hacia el navegador. Se publica el nombre y el resultado, nada
más. `emitir` no filtra: lo que no se pasa no se publica, y las llamadas del
agente solo pasan el nombre.

## Quien escucha no ocupa un hilo mientras espera (V2.0.27)

La cola es de hilos (el turno corre en uno), pero quien la lee es la ruta, que
es asíncrona. Antes la leía bloqueándose en `cola.get()`, y Starlette ejecuta
eso en su reserva de **40 hilos compartida por todas las rutas síncronas**. La
prueba de carga de la 2.3-C lo midió: con 60 turnos largos abiertos, `/health`
tardaba 9,7 s y el `inicio` de un turno nuevo 11,5 s, esperando a que algún
stream soltara su hilo en el siguiente latido.

Ahora `poner` además **despierta** a quien escuche en el bucle de eventos
(`al_poner`), y la ruta espera sin hilo.
"""

import queue
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator


class CanalDelTurno:
    """Una cola de eventos con el reloj del turno."""

    def __init__(self) -> None:
        self.cola: queue.Queue[dict[str, Any]] = queue.Queue()
        self.inicio = time.monotonic()
        #: Lo que avisa a quien escucha de que hay algo en la cola. Lo pone la
        #: ruta, y se llama desde el hilo del turno.
        self.al_poner: Callable[[], None] | None = None

    def segundos(self) -> float:
        return round(time.monotonic() - self.inicio, 3)

    def poner(self, tipo: str, **datos: Any) -> None:
        self.meter({"tipo": tipo, **datos, "t": self.segundos()})

    def meter(self, elemento: Any) -> None:
        """Encola y avisa. Lo usa también la ruta para la marca de fin."""
        self.cola.put(elemento)
        avisar = self.al_poner
        if avisar is not None:
            try:
                avisar()
            except RuntimeError:
                # El bucle ya se cerró: nadie escucha. El turno sigue igual.
                pass


_canal: ContextVar[CanalDelTurno | None] = ContextVar("canal_del_turno", default=None)


def canal_actual() -> CanalDelTurno | None:
    return _canal.get()


@contextmanager
def escuchando(canal: CanalDelTurno) -> Iterator[CanalDelTurno]:
    """Fija el canal para todo lo que corra dentro, incluidos los hilos que copien el contexto."""
    testigo = _canal.set(canal)
    try:
        yield canal
    finally:
        _canal.reset(testigo)


def emitir(tipo: str, **datos: Any) -> None:
    """Publica un evento en el canal de este turno, si alguien escucha."""
    canal = _canal.get()
    if canal is not None:
        canal.poner(tipo, **datos)
