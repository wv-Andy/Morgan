"""
El permiso automático (4.6, pedido por mí el 2026-09-30).

*«Integra una parte en Ajustes para darme permiso automático de ejecución al hacer la
mayoría de acciones en verde y amarillo, pero en las rojas preguntar.»*

Con él encendido, lo 🟢 y lo 🟡 (`safe`, `low_risk`, `moderate`) que hoy exige un plan
aprobado se hace sin esperar al botón: un plan así se aprueba y se ejecuta al crearlo, en
el mismo turno. Lo 🔴 (`high_risk`, `sensitive`, `critical`: borrar, un comando que
cambia algo, terminar un proceso, escribir en GitHub) sigue esperando a la persona.

Tres cosas que no cambia, a propósito:

- **Lo que el PC confirma, lo sigue confirmando.** La política del PC es suya y la nube
  no puede tocarla (3.2): si ese PC pide «Permitir» para escribir, lo pide igual.
- **No vive en la memoria.** Los ajustes de perfil se guardan como recuerdos, y el modelo
  puede escribir recuerdos (`remember_fact`): una página con instrucciones escondidas
  podría concederse el permiso. Vive en `morgan_users.permiso_automatico`, que solo
  cambia esta ruta de Ajustes, con la sesión de la web.
- **No vale con un token de API.** Un token filtrado no hereda el permiso: sus turnos
  siguen pidiendo aprobación.
"""

import logging

from src.tasks.plan import GRAVEDAD, UMBRAL_CONFIRMACION
from src.tools.recordado import Recordado

logger = logging.getLogger(__name__)

COLUMNA = "permiso_automatico"

_RECORDADO = Recordado()


def entra(nivel: str | None) -> bool:
    """Si un nivel de riesgo es verde o amarillo. Uno desconocido, no: ante la duda, rojo."""
    return GRAVEDAD.get(str(nivel or "").lower(), UMBRAL_CONFIRMACION + 1) <= UMBRAL_CONFIRMACION


def lo_tiene(repo, user_id: str) -> bool:
    """Si la persona lo encendió. Recordado 30 s; si la base falla, no."""
    def leer() -> bool:
        fila = repo.obtener(user_id) or {}
        return bool(fila.get(COLUMNA))

    return bool(_RECORDADO.dato((id(repo), user_id), leer, si_falla=False))


def cambiar(repo, user_id: str, encendido: bool) -> bool:
    repo.actualizar(user_id, {COLUMNA: bool(encendido)})
    _RECORDADO.olvidar()
    return bool(encendido)


def para_este_turno(repo) -> bool:
    """Si el turno en curso lo tiene: la persona lo encendió y no llega con un token."""
    from src.identidad import usuario_actual
    from src.identidad.tokens import token_actual

    if token_actual() is not None:
        return False
    return lo_tiene(repo, usuario_actual())
