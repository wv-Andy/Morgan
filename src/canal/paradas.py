"""
Parar un turno desde la web, y con él lo que está haciendo en el PC (3.4).

Decisión mía (2026-09-24): **«parar» en la web cancela también lo del PC**, en su
siguiente punto seguro. Pero **solo el botón «Detener»**, no una conexión que se cae:
en el móvil, cambiar de aplicación corta el stream, y el turno sigue en su hilo y se
guarda (decisión B del streaming, 2026-09-16). Si cortar la conexión cancelara, una
copia se pararía por mirar otra aplicación.

Así que el turno en streaming se da de alta aquí con un identificador (`abrir`), que
viaja en el evento `inicio`. «Detener» llama a `POST /chat/parar` con él, y eso levanta
la señal que ve el despacho (`PARADA`, en el contexto del hilo del turno):

- una orden al PC **en marcha** se cancela (`cancelar`);
- una orden **nueva** de ese turno ya no sale (`TurnoParado`).

Solo lo puede parar **su dueño**: el identificador es aleatorio y, además, se comprueba
la persona. Vive en la memoria de esta instancia de la nube; durante un despliegue
conviven dos unos segundos y «Detener» puede caer en la otra (aceptado: ver mediciones).
"""

import secrets
import threading
import time
from contextvars import ContextVar

#: La señal de parar del turno en curso. `None` fuera de un turno en streaming.
PARADA: ContextVar[threading.Event | None] = ContextVar("parada", default=None)

#: Un turno dura como mucho `turn_timeout_stream` (170 s); lo que pase de esto es basura.
DURA = 600

_cerrojo = threading.Lock()
_turnos: dict[str, tuple[str, threading.Event, float]] = {}


def abrir(user_id: str) -> tuple[str, threading.Event]:
    turno = secrets.token_urlsafe(12)
    parada = threading.Event()
    with _cerrojo:
        limite = time.time() - DURA
        for viejo in [t for t, (_, _, creado) in _turnos.items() if creado < limite]:
            del _turnos[viejo]
        _turnos[turno] = (user_id, parada, time.time())
    return turno, parada


def cerrar(turno: str) -> None:
    with _cerrojo:
        _turnos.pop(turno, None)


def parar(turno: str, user_id: str) -> bool:
    """Levanta la señal. False si el turno no existe (ya acabó) o no es de esa persona."""
    with _cerrojo:
        dato = _turnos.get(turno)
    if dato is None or dato[0] != user_id:
        return False
    dato[1].set()
    return True


def parado() -> bool:
    parada = PARADA.get()
    return parada is not None and parada.is_set()
