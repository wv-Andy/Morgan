"""
En qué espacio de trabajo ocurre esta petición o este turno.

## El mismo patrón que el usuario, y por el mismo motivo

El usuario de cada petición vive en una variable de contexto y **todos** los
repositorios filtran por él sin que nadie tenga que pasárselo. Olvidarlo no es
posible, porque no es un argumento que se pueda olvidar. El espacio se hace igual.

Si fuera un parámetro, cada herramienta, cada ruta y cada almacén tendría que
acordarse de propagarlo, y el primero que se olvidara mostraría los archivos de un
proyecto dentro de otro sin decir nada.

## Quién lo fija, y por qué nunca el navegador sin comprobarlo

- **En un turno, el agente**, a partir de la conversación guardada. La
  conversación es la autoridad: una conversación del espacio A se atiende siempre
  en el espacio A, pida lo que pida el cliente.
- **En una petición de la web, el middleware**, a partir de la cabecera
  `X-Morgan-Espacio`, y **solo después de comprobar que ese espacio es de quien
  pide**. Un identificador ajeno se trata como si no existiera.

## `None` es «General», no «todos»

Sin espacio, las consultas ven lo que **no** pertenece a ningún espacio. Es lo que
había antes de que existieran, así que nada de lo guardado cambia de sitio. Y
significa que un espacio nunca ve lo de otro, tampoco a través de «General».
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token

#: Sin valor, «General»: lo que no está en ningún espacio.
_espacio_actual: ContextVar[str | None] = ContextVar("morgan_espacio", default=None)


def espacio_actual() -> str | None:
    """El identificador del espacio actual, o `None` si es «General»."""
    return _espacio_actual.get()


def fijar_espacio(espacio_id: str | None) -> Token:
    """Fija el espacio. Devuelve el testigo para restaurarlo.

    Una cadena vacía o de espacios se trata como `None`: es lo que manda un
    formulario vacío, y convertirla en un espacio llamado «» sería inventarse uno.
    """
    limpio = (espacio_id or "").strip() or None
    return _espacio_actual.set(limpio)


def restaurar_espacio(testigo: Token) -> None:
    _espacio_actual.reset(testigo)


@contextmanager
def en_espacio(espacio_id: str | None):
    """Ejecuta un bloque dentro de un espacio y lo restaura al salir, falle o no."""
    testigo = fijar_espacio(espacio_id)
    try:
        yield espacio_actual()
    finally:
        restaurar_espacio(testigo)
