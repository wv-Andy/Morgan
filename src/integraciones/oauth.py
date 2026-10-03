"""
Lo común a todos los servicios conectados por OAuth (V2.0.17).

Hasta la 2.0.16 solo había GitHub, y las rutas llamaban a su módulo por su
nombre. Con Google Calendar hacen falta dos cosas que GitHub no necesitaba:

- **Un cliente por servicio**, para que conectar, volver y desconectar sean el
  mismo código para todos (`cliente_de`).
- **Tokens que caducan.** Los de GitHub no caducan; los de Google, **a la hora**.
  Hace falta guardar el de renovación y usarlo antes de cada llamada
  (`token_vigente`).

## El modo de pruebas de Google

Lo decidí: la app de Google va en **modo de pruebas**, sin verificar. Eso
significa hasta 100 usuarios añadidos a mano y que **la autorización caduca a los
7 días**. Cuando pasa, renovar el token devuelve `invalid_grant`, y Morgan tiene
que decirlo tal cual —vuelve a conectar— en lugar de fallar con un error que no
explica nada. Es `AutorizacionCaducada`.
"""

import time
from dataclasses import dataclass
from types import ModuleType

#: Cuánto antes de caducar se renueva un token. Una llamada que empieza con 5 s
#: de vida puede llegar a Google con el token ya muerto.
MARGEN_DE_RENOVACION = 60.0


class ServicioNoConfigurado(RuntimeError):
    """Al servidor le faltan las credenciales de la aplicación OAuth de ese servicio."""


class ErrorDelServicio(RuntimeError):
    """El servicio rechazó la operación o no respondió."""


class AutorizacionCaducada(ErrorDelServicio):
    """La autorización ya no vale y hay que volver a conectar.

    No es un fallo pasajero: reintentar no sirve. En Google, en modo de pruebas,
    pasa a los 7 días de conectar.
    """


@dataclass(frozen=True)
class Credenciales:
    """Lo que devuelve canjear un código de autorización."""

    token: str
    scopes: tuple[str, ...] = ()
    #: El token de renovación. `None` en los servicios cuyos tokens no caducan.
    refresco: str | None = None
    #: Cuándo caduca `token`, en segundos desde la época. `None`: no caduca.
    expira_en: float | None = None


def cliente_de(servicio: str) -> ModuleType:
    """El módulo que habla con ese servicio.

    Cada uno expone lo mismo: `url_de_autorizacion`, `canjear`, `cuenta_de`,
    `revocar` y, si sus tokens caducan, `refrescar`. Se importa aquí dentro para
    que un servicio que falte en el entorno no rompa a los demás al arrancar.
    """
    from src.integraciones.modelos import ServicioExterno

    if servicio == ServicioExterno.GITHUB.value:
        from src.integraciones import github
        return github
    if servicio == ServicioExterno.GOOGLE_CALENDAR.value:
        from src.integraciones import google_calendar
        return google_calendar
    raise KeyError(servicio)


def token_vigente(repositorio, servicio: str) -> str | None:
    """El token de quien pregunta, renovado si le queda menos de un minuto.

    `None` si no ha conectado el servicio. Lanza `AutorizacionCaducada` —y lo deja
    anotado en la integración, para que la pantalla de Servicios lo enseñe— si ya
    no se puede renovar.

    La renovación se guarda **sin tocar la cuenta ni los permisos**: solo cambian
    el token y su caducidad.
    """
    credenciales = repositorio.credenciales_de(servicio)
    if credenciales is None:
        return None

    token, refresco, expira_en = credenciales
    if expira_en is None or expira_en - MARGEN_DE_RENOVACION > time.time():
        return token

    if not refresco:
        mensaje = "La autorización caducó y no hay forma de renovarla. Vuelve a conectar el servicio."
        repositorio.anotar_error(servicio, mensaje)
        raise AutorizacionCaducada(mensaje)

    cliente = cliente_de(servicio)
    try:
        nuevo, nueva_caducidad = cliente.refrescar(refresco)
    except AutorizacionCaducada as exc:
        repositorio.anotar_error(servicio, str(exc))
        raise

    repositorio.actualizar_token(servicio, nuevo, nueva_caducidad)
    return nuevo
