"""
De dónde sale el `user_id` de cada petición (identidad, V2.0 adelantada).

**Del contexto de la petición, nunca del cuerpo ni de la URL.** Es la regla del
§10 de la especificación, y es donde estaría el agujero si se hiciera al revés:
si el cliente pudiera decir de quién son los datos que pide, bastaría con cambiar
un número para leer los de otro.

El contexto vive en una variable de contexto (`contextvars`), no en una global.
La diferencia importa: FastAPI atiende varias peticiones a la vez en un
*threadpool*, y con una global las peticiones concurrentes se pisarían el usuario
entre sí — que es exactamente la fuga que esta capa existe para evitar.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from src.identidad.modelos import USUARIO_LOCAL
from src.identidad.roles import Rol

logger = logging.getLogger(__name__)

# Por defecto, el usuario local: así el Morgan de escritorio y la línea de
# comandos funcionan sin que nadie inicie sesión, y el código de datos no tiene
# que preguntarse si hay usuario o no.
_usuario_actual: ContextVar[str] = ContextVar("morgan_user_id", default=USUARIO_LOCAL)

# El rol viaja junto al usuario, y por el mismo motivo. Se guarda aquí en lugar de
# consultarlo a la base cada vez que hace falta: se necesita en sitios calientes
# —una vez por herramienta ejecutada, por ejemplo— y un viaje a la base por cada
# comprobación de permisos sería un coste absurdo.
#
# Por defecto OWNER porque el usuario por defecto es el local, y en tu propio
# equipo mandas tú. Quien fija el usuario fija también el rol; el middleware lo
# hace con lo que ya trae la sesión, sin consultas de más.
_rol_actual: ContextVar[Rol] = ContextVar("morgan_user_role", default=Rol.OWNER)


def usuario_actual() -> str:
    """El identificador del usuario de esta petición."""
    return _usuario_actual.get()


def rol_actual() -> Rol:
    """El rol de quien hace esta petición."""
    return _rol_actual.get()


def fijar_usuario(user_id: str, rol: Rol | None = None) -> object:
    """Fija el usuario de esta petición. Devuelve el testigo para restaurarlo.

    Sin rol explícito se asume `USER`, salvo para el usuario local. Es lo
    prudente: quien no dice qué rol tiene, no tiene ninguno especial.

    **El testigo lleva los dos**, usuario y rol, y eso no es un detalle: una
    primera versión devolvía solo el del usuario, así que al salir de un bloque
    el rol se quedaba puesto. Un trabajo que actuara como el propietario dejaba
    `OWNER` colgando para lo siguiente que corriera en ese contexto — una fuga de
    privilegio silenciosa y difícil de ver. Lo cazó una prueba.
    """
    if not user_id or not user_id.strip():
        raise ValueError("El identificador de usuario no puede estar vacío.")

    limpio = user_id.strip()
    if rol is None:
        rol = Rol.OWNER if limpio == USUARIO_LOCAL else Rol.USER

    return (_usuario_actual.set(limpio), _rol_actual.set(rol))


def restaurar_usuario(testigo: object) -> None:
    """Deshace un `fijar_usuario`, incluido el rol."""
    if isinstance(testigo, tuple):
        testigo_usuario, testigo_rol = testigo
        _usuario_actual.reset(testigo_usuario)
        _rol_actual.reset(testigo_rol)
        return

    # Un testigo suelto es de una llamada anterior a que el rol viajara con el
    # usuario. Se acepta para no romper nada que lo guardara.
    _usuario_actual.reset(testigo)  # type: ignore[arg-type]


@contextmanager
def como_usuario(user_id: str, rol: Rol | None = None):
    """Ejecuta un bloque como un usuario concreto.

    Se usa en las pruebas de aislamiento y en cualquier trabajo de fondo que
    actúe en nombre de alguien: sin esto, una tarea programada escribiría con el
    usuario local en lugar de con su dueño.
    """
    testigo = fijar_usuario(user_id, rol)
    try:
        yield user_id
    finally:
        restaurar_usuario(testigo)
