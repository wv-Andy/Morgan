"""
La sesión web: cookies, CSRF y quién es el usuario de cada petición (identidad, V2.0 adelantada).

Este módulo es la costura entre HTTP y las cuentas. Traduce «llega una petición
con estas cookies» a «esta petición la hace Ana», y decide qué pasa cuando no.

Tres decisiones que conviene entender antes de tocar nada:

**La sesión va en una cookie `HttpOnly`, no en `localStorage`.** Guardarla donde
el JavaScript pueda leerla la pone al alcance de cualquier script inyectado, y en
una aplicación que renderiza texto de un modelo eso no es una hipótesis remota.

**`SameSite=None` en la nube, y por eso hace falta CSRF.** La cookie viaja
también en peticiones que provoque otra web, así que la defensa es el **doble
envío**: una segunda cookie, esta legible, cuyo valor el cliente repite en una
cabecera. Una web ajena puede provocar la petición, pero no puede leer la cookie
para rellenar la cabecera —se lo impide la política de mismo origen— ni ponerla
sin disparar un *preflight* que CORS rechaza.

**La cookie que emite este módulo solo sirve si el navegador ve la API en el
mismo origen que la página.** Eso lo garantiza el proxy de `vercel.json`, no
este código, y es una dependencia que conviene tener presente antes de tocar
nada aquí: mientras la web llamó directamente a Render, la cookie era de
terceros y Safari en iPhone la descartaba entera. Ninguna combinación de
atributos lo arregla desde el servidor. Está contado en
`docs/web.md`, defecto 9 de producción.

**En local no se exige nada.** Es tu equipo y tus claves; inventarte una
contraseña para hablar con tu propio ordenador no protege de nada, y el Morgan de
escritorio depende de que esto siga siendo así.
"""

import logging
import secrets

from fastapi import Request, Response

from src.config import get_settings
from src.identidad import USUARIO_LOCAL
from src.identidad.roles import Rol
from src.identidad.cuentas import DURACION_SESION_DIAS, ServicioDeCuentas

logger = logging.getLogger(__name__)

COOKIE_SESION = "morgan_sesion"
COOKIE_CSRF = "morgan_csrf"
CABECERA_CSRF = "x-morgan-csrf"

# Métodos que no cambian nada. Se dejan fuera de la comprobación CSRF porque el
# ataque consiste en provocar un efecto, y leer no lo tiene.
METODOS_SEGUROS = ("GET", "HEAD", "OPTIONS")


def _seguro() -> bool:
    """Si las cookies deben marcarse `Secure`.

    En la nube siempre: sin esto viajarían también por HTTP en claro. En local no,
    porque `http://localhost` no es HTTPS y el navegador descartaría la cookie —
    el efecto sería no poder iniciar sesión en desarrollo.
    """
    return get_settings().is_cloud


def renovar_csrf(response: Response) -> str:
    """Emite un token CSRF nuevo y lo deja en su cookie. Devuelve el valor.

    Se separo de `poner_cookies` porque hace falta tambien por su cuenta: una
    sesion puede seguir viva y haber perdido esta cookie —el navegador la
    descarto, o caduco antes—, y sin poder reponerla la sesion se queda en
    **solo lectura** hasta volver a iniciar sesion. No es un caso raro: pasa con
    cualquier navegador que limpie cookies de terceros.
    """
    csrf = secrets.token_urlsafe(24)
    seguro = _seguro()
    same_site = "none" if seguro else "lax"

    # Esta cookie SI es legible por el cliente cuando comparte dominio con el:
    # su valor tiene que poder copiarse a la cabecera. No es un secreto de
    # sesion, es una prueba de mismo origen.
    response.set_cookie(
        COOKIE_CSRF, csrf,
        max_age=DURACION_SESION_DIAS * 86400,
        httponly=False, secure=seguro, samesite=same_site, path="/",
    )
    return csrf


def poner_cookies(response: Response, token: str) -> str:
    """Deja la sesión abierta en el navegador. Devuelve el token CSRF."""
    seguro = _seguro()
    # Cross-site necesita None; en local Lax, que es mas estricto y basta.
    same_site = "none" if seguro else "lax"

    response.set_cookie(
        COOKIE_SESION, token,
        max_age=DURACION_SESION_DIAS * 86400,
        httponly=True, secure=seguro, samesite=same_site, path="/",
    )
    return renovar_csrf(response)


def quitar_cookies(response: Response) -> None:
    """Cierra la sesión en el navegador.

    Se borran con los mismos atributos con los que se pusieron: un `delete_cookie`
    con `path` o `samesite` distintos no borra nada, y la sesión parecería seguir
    abierta al recargar.
    """
    seguro = _seguro()
    same_site = "none" if seguro else "lax"
    for nombre in (COOKIE_SESION, COOKIE_CSRF):
        response.delete_cookie(
            nombre, path="/", httponly=nombre == COOKIE_SESION,
            secure=seguro, samesite=same_site,
        )


def csrf_valido(request: Request) -> bool:
    """Comprueba el doble envío: la cookie y la cabecera deben coincidir."""
    if request.method in METODOS_SEGUROS:
        return True

    cookie = request.cookies.get(COOKIE_CSRF)
    cabecera = request.headers.get(CABECERA_CSRF)

    # Sin cookie de sesion no hay nada que proteger: es una peticion anonima
    # —registrarse, iniciar sesion— y exigirle CSRF impediria entrar.
    if not request.cookies.get(COOKIE_SESION):
        return True

    return bool(cookie) and bool(cabecera) and secrets.compare_digest(cookie, cabecera)


#: Las rutas de entrada: quedan fuera del doble envío (sin sesión no hay token que repetir), y
#: por eso se protegen mirando de dónde viene la petición (`origen_propio`).
RUTAS_DE_ENTRADA = ("/auth/login", "/auth/registro", "/auth/recuperar", "/auth/restablecer", "/auth/logout")


def origen_propio(request: Request) -> bool:
    """El «login CSRF» (4.22): que una web ajena no pueda hacerte entrar en SU cuenta.

    Las rutas de entrada no pueden exigir el doble envío, y una web ajena podía mandar desde tu
    navegador un formulario a `/auth/login` con las credenciales del atacante: entrabas en su
    cuenta sin darte cuenta, y lo que escribieras quedaba allí. Un navegador **siempre** dice de
    qué página sale una petición así (`Origin`), y la web ajena no puede cambiarlo: si no es la de
    Morgan (la lista de CORS, o la misma dirección que la API), se rechaza.

    Sin `Origin` no hay navegador de por medio (un programa, la línea de comandos): no hay a quién
    engañar, y pasa. `Origin: null` sí se rechaza: es lo que manda un marco aislado, el truco para
    esconder la página de origen.
    """
    if request.method in METODOS_SEGUROS:
        return True
    origen = request.headers.get("origin")
    if origen is None:
        return True
    ajustes = get_settings()
    if origen in ajustes.cors_origins:
        return True
    if ajustes.cors_origin_regex:
        import re

        if re.fullmatch(ajustes.cors_origin_regex, origen):
            return True
    from urllib.parse import urlsplit

    anfitrion = urlsplit(origen).netloc
    return bool(anfitrion) and anfitrion == request.headers.get("host")


#: Las cabeceras de origen que pone un proxy **y que el cliente no puede
#: imponer**, en orden de preferencia. Medido en producción con la sonda
#: `/diagnostico/origen` (2.3, V2.0.33):
#:
#: - Por la web, Vercel pone `x-vercel-forwarded-for` con la IP real y **tira** lo
#:   que el cliente mande en `X-Forwarded-For`. Ahí `cf-connecting-ip` es la IP
#:   de salida de Vercel, la misma para mucha gente: por eso va detrás.
#: - Directo contra Render, delante hay Cloudflare, que pone `cf-connecting-ip`
#:   con la IP real y **sobrescribe** la que traiga el cliente. En cambio, un
#:   `X-Forwarded-For: 6.6.6.6` inventado llegaba el primero de la cadena y era
#:   lo que contaban los frenos.
ORIGEN_DE_CONFIANZA = ("x-vercel-forwarded-for", "cf-connecting-ip")


def origen_de(request: Request) -> str:
    """De dónde viene la petición, para contar intentos fallidos.

    Detrás de Render o Vercel, `request.client.host` es el proxy y sería el mismo
    para todo el mundo: contar por él bloquearía a todos los usuarios a la vez.

    Se prefieren las cabeceras que pone el proxy (`ORIGEN_DE_CONFIANZA`) a
    `X-Forwarded-For`, que el cliente puede escribir. Queda un hueco medido: quien
    ataca directo contra Render puede inventarse también `x-vercel-forwarded-for`.
    Lo que consigue es lo mismo que antes —**no acumular** intentos en el mismo
    cubo—, y para eso siguen el tope por cuenta y el global de altas
    (docs/auditorias.md).
    """
    for cabecera in ORIGEN_DE_CONFIANZA:
        valor = request.headers.get(cabecera, "").split(",")[0].strip()
        if valor:
            return valor[:100]

    reenviado = request.headers.get("x-forwarded-for", "")
    if reenviado:
        return reenviado.split(",")[0].strip()[:100]

    return request.client.host if request.client else "desconocido"


def resolver_usuario(request: Request, servicio: ServicioDeCuentas):
    """Quién hace esta petición, y con qué rol.

    Devuelve `(user_id, rol)`, o `None` si no hay sesión válida y el despliegue
    exige tenerla.

    El rol sale de aquí porque la sesión ya lo trae: consultarlo después, en cada
    comprobación de permisos, sería un viaje a la base por herramienta ejecutada.
    """
    token = request.cookies.get(COOKIE_SESION)
    usuario = servicio.usuario_de_sesion(token) if token else None

    if usuario is not None:
        return usuario.id, usuario.rol_efectivo

    if get_settings().require_auth:
        return None

    # Sin cuentas exigidas, todo pertenece al usuario implicito: es el Morgan de
    # siempre, el de tu equipo, donde mandas tu.
    return USUARIO_LOCAL, Rol.OWNER

