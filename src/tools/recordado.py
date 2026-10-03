"""
Una pregunta barata de repetir, pero no gratis, recordada un rato por persona (4.2).

`Tool.disponible()` se pregunta en **cada llamada al modelo** y por cada herramienta. Para
las que solo tienen sentido si la persona tiene algo —documentos, archivos subidos—, la
respuesta sale de la base, y cuatro herramientas por llamada serían cuatro consultas. Se
recuerda unos segundos por persona (y espacio), y quien crea o borra ese algo lo olvida.
"""

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Lo bastante corto para que un cambio que no pasa por aquí (otro proceso) se note
#: enseguida; lo bastante largo para cubrir las llamadas de un turno.
RECUERDA = 30.0


class Recordado:
    def __init__(self, segundos: float = RECUERDA) -> None:
        self.segundos = segundos
        self._datos: dict = {}

    def dato(self, clave, calcular: Callable[[], Any], si_falla: Any = None) -> Any:
        """Lo que devuelve `calcular()` para `clave`, de memoria si es reciente. Si falla,
        `si_falla`, sin recordarlo: la próxima vez se vuelve a intentar."""
        ahora = time.monotonic()
        guardado = self._datos.get(clave)
        if guardado is not None and ahora - guardado[0] < self.segundos:
            return guardado[1]
        try:
            respuesta = calcular()
        except Exception:
            logger.debug("No se pudo calcular %r", clave, exc_info=True)
            return si_falla
        self._datos[clave] = (ahora, respuesta)
        return respuesta

    def valor(self, clave, calcular: Callable[[], bool]) -> bool:
        """Como `dato`, en sí o no. Si `calcular` falla, `True`: ofrecer una herramienta de
        más cuesta unos tokens; quitar una que hacía falta deja a la persona sin poder
        hacer algo."""
        return bool(self.dato(clave, lambda: bool(calcular()), si_falla=True))

    def olvidar(self) -> None:
        self._datos.clear()


def de_quien() -> tuple[str, str | None]:
    """La persona y el espacio de trabajo de esta petición."""
    from src.identidad import usuario_actual
    from src.espacios.contexto import espacio_actual

    return usuario_actual(), espacio_actual()
