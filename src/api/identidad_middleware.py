"""
El middleware que decide quién hace cada petición (identidad, V2.0 adelantada).

Va antes que cualquier ruta y hace tres cosas, en este orden:

1. **Fija el usuario** de la petición a partir de la cookie de sesión (o de un
   token personal de API, ver más abajo). Todo lo
   que ocurra después —conversaciones, memoria, archivos, tareas— queda filtrado
   por él sin que ninguna ruta tenga que acordarse de hacerlo.
2. **Comprueba el CSRF** en los métodos que cambian algo.
3. **Corta el paso** si el despliegue exige cuenta y no hay sesión.

El orden importa. Que el filtrado por usuario esté aquí y no en cada consulta es
lo que hace que una ruta nueva nazca aislada por defecto: olvidarse del filtro
deja de ser posible, porque no hay filtro que escribir. La alternativa —confiar
en que cada consulta se acuerde— falla la primera vez que alguien añade una ruta
con prisa, y el fallo es que un usuario ve los datos de otro.

**La otra puerta: un token personal de API** (`Authorization: Bearer mgn_...`,
plan de la API, fase 1). Es para clientes que no son el navegador. Entra solo si la
petición **no trae cookie de sesión**: con cookie, se sigue el camino de siempre,
con su CSRF, traiga la cabecera que traiga. Así eximir del CSRF a los tokens no abre
nada a la web. Un token se resuelve igual que una sesión —fija usuario y rol— y
además se comprueba que la ruta esté a su alcance (src/identidad/tokens.py).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.automatizacion.contexto import fijar_zona
from src.espacios.contexto import fijar_espacio
from src.identidad import USUARIO_LOCAL, fijar_usuario
from src.identidad.cuentas import ServicioDeCuentas
from src.identidad.tokens import (
    ServicioDeTokens,
    alcance_necesario,
    fijar_token,
    token_de_la_cabecera,
)
from src.api.sesion_web import (
    COOKIE_SESION,
    csrf_valido,
    quitar_cookies,
    resolver_usuario,
)

logger = logging.getLogger(__name__)

# Rutas que funcionan sin sesión, porque son las que sirven para conseguirla.
# `/auth/yo` entra aquí a propósito: responde «no autenticado» en lugar de 401,
# que es lo que la interfaz necesita al cargar por primera vez.
PUBLICAS = (
    "/health",
    # `/status` es lo que la interfaz consulta para saber si el backend vive, y
    # tambien lo que despierta a Render cuando lleva un rato dormido. Protegerlo
    # hace que la PROPIA PANTALLA DE ACCESO diga "API desconectada": no puedes
    # entrar porque no has entrado. Ya paso una vez, por otro motivo, y costo
    # una tarde entera.
    #
    # No publica datos de nadie: estado de los componentes, modo y version. Y si
    # hay MORGAN_API_TOKEN configurado, ese sigue cubriendolo igual.
    "/status",
    "/auth/login",
    "/auth/registro",
    "/auth/recuperar",
    "/auth/restablecer",
    "/auth/yo",
    "/auth/logout",
    # Confirmar el correo llega desde un enlace: puede abrirse en otro navegador,
    # o sin sesion. Exigirla convertiria un clic en un correo en «primero inicia
    # sesion», que es la friccion que esta funcion existe para evitar.
    #
    # Reenviar SI exige sesion, y por eso esta en otra ruta: una ruta abierta que
    # dispara correos es una herramienta para molestar a terceros.
    "/auth/verificar",
    # La sonda del origen (auditoría 2.3): mide lo que llega SIN sesión, que es el
    # caso del registro y del inicio de sesión. No escribe nada y solo devuelve lo
    # que trae la propia petición.
    "/diagnostico/origen",
    # El agente local (3.0-C) aún no tiene credencial cuando empareja: la consigue
    # ahí, con el código de un solo uso. Y desemparejar se autentica con la
    # credencial del agente (`mga_…`), que esta capa no conoce a propósito: la
    # comprueba la propia ruta. Ver src/api/routes/agentes.py.
    "/agente/emparejar/consultar",
    "/agente/emparejar/confirmar",
    "/agente/desemparejar",
    "/agente/historial",      # 3.7: para `cruzar`, con la credencial del agente
    "/agente/actualizacion",  # 3.8: con la credencial del agente o un código vivo
    "/agente/paquete",
    "/agente/rotar",          # 3.8: con la credencial del agente
    "/agente/instalar.ps1",   # 3.8: el instalador; sin secretos, y no hace nada sin un código
    # El reloj de Supabase (4.14): sin sesión, con su propio secreto, que comprueba la ruta.
    "/automatizaciones/reloj",
)

# El retorno de OAuth. Llega como una navegacion del navegador desde GitHub, no
# como una peticion de la web, asi que puede no traer cookie de sesion — y no
# hace falta: de quien es la autorizacion lo dice el 'state', que se emitio con
# una sesion valida y se guardo con su user_id.
#
# Exigir sesion aqui rompia el flujo justo al volver, despues de autorizar, que
# es el peor momento posible para fallar.
PUBLICAS_POR_PREFIJO = ("/integraciones/",)
PUBLICAS_POR_SUFIJO = ("/callback",)

# Prefijos que no son API: la interfaz web y la documentación. Sin esto, exigir
# cuenta en la nube devolvería 401 al propio HTML del formulario de acceso, y no
# habría forma de entrar.
PREFIJOS_ABIERTOS = ("/assets", "/docs", "/redoc", "/openapi.json", "/favicon")

#: Con qué espacio de trabajo trabaja la web (V2.2). Ver `src/espacios/contexto.py`.
CABECERA_ESPACIO = "X-Morgan-Espacio"
#: La zona horaria del navegador (4.14). Ver `src/automatizacion/contexto.py`.
CABECERA_ZONA = "X-Morgan-Zona"


def _es_publica(path: str) -> bool:
    if path in PUBLICAS or path.startswith(PREFIJOS_ABIERTOS) or path == "/":
        return True

    # Solo el retorno de OAuth, y solo bajo su prefijo. Se comprueban las dos
    # cosas a la vez a proposito: con el sufijo suelto, cualquier ruta futura
    # que acabara en /callback quedaria abierta sin que nadie lo decidiera.
    return path.startswith(PUBLICAS_POR_PREFIJO) and path.endswith(PUBLICAS_POR_SUFIJO)


def _error(codigo: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=codigo,
        content={"success": False, "error": {"code": code, "message": message, "details": None}},
    )


def _fijar_espacio_pedido(request: Request) -> JSONResponse | None:
    """Fija el espacio que pide la web, **solo si es de quien pide**.

    Se llama después de fijar el usuario, porque comprobarlo es buscar el
    espacio con el filtro por usuario puesto: un identificador de otra persona no
    aparece. Devuelve la respuesta de error si hay que cortar, o `None` si se
    puede seguir.

    Un espacio que no existe responde **404** en lugar de caer a «General» sin
    decir nada. Caer en silencio haría que la web, creyéndose en un proyecto,
    subiera archivos a otro sitio.
    """
    pedido = (request.headers.get(CABECERA_ESPACIO) or "").strip()
    if not pedido:
        return None

    try:
        from src.api.dependencies import get_container
        from src.espacios.repositorio import repositorio_de_espacios

        repositorio = repositorio_de_espacios(get_container().repositories)
        espacio = repositorio.obtener(pedido) if repositorio is not None else None
    except Exception:
        logger.warning("No se pudo comprobar el espacio de trabajo pedido", exc_info=True)
        return _error(
            503, "ESPACIOS_NO_DISPONIBLES",
            "No se pudo comprobar el espacio de trabajo. Inténtalo más tarde.",
        )

    if espacio is None:
        return _error(
            # Código propio: al recibirlo, la web olvida el espacio que tenía
            # seleccionado. Con uno compartido, cualquier 404 de otro espacio
            # le haría perder el suyo.
            404, "ESPACIO_ACTUAL_NO_ENCONTRADO",
            "Ese espacio de trabajo no existe o no es tuyo.",
        )

    fijar_espacio(espacio.id)
    return None


def install_identidad(app: FastAPI) -> None:
    """Instala la resolución de identidad en la aplicación."""

    @app.middleware("http")
    async def _identidad(request: Request, call_next):
        # Cada peticion empieza como el usuario implicito. Sin este reinicio, un
        # hilo del pool reutilizado conservaria al usuario de la peticion
        # anterior: dos personas a la vez y una veria los datos de la otra.
        fijar_usuario(USUARIO_LOCAL)
        # Y en «General», por el mismo motivo: un hilo reutilizado no puede
        # arrastrar el espacio de la petición anterior.
        fijar_espacio(None)
        # Y sin token: la auditoría anota el de la petición, no el de la anterior.
        fijar_token(None)
        # La zona horaria de quien pide (4.14): la manda la web, la de su navegador. Las
        # automatizaciones guardan «a las 9» como las 9 de esa zona. Si no vale, ninguna.
        fijar_zona(request.headers.get(CABECERA_ZONA))

        if request.method == "OPTIONS":
            return await call_next(request)

        servicio = _servicio()

        # Un token personal y ninguna cookie de sesión: la puerta de los clientes
        # que no son el navegador. Va ANTES de mirar si hay capa de cuentas porque
        # un token que no se puede comprobar se rechaza siempre; sin esto, en un
        # Morgan sin cuentas exigidas pasaría como el usuario implícito.
        valor = token_de_la_cabecera(request.headers.get("authorization"))
        if valor is not None and not request.cookies.get(COOKIE_SESION):
            return await _por_token(request, call_next, servicio, valor)

        if servicio is None:
            # Que la capa de cuentas no arranque NO puede dejar pasar a nadie
            # cuando el despliegue exige cuenta: un fallo que abre la puerta es
            # mucho peor que uno que la cierra. Sin cuentas exigidas —el Morgan
            # de tu equipo— se sigue como el usuario implicito de siempre.
            if get_settings().require_auth:
                logger.error(
                    "No hay capa de cuentas y este despliegue exige sesión: se "
                    "rechazan las peticiones en lugar de atenderlas sin autenticar."
                )
                return _error(
                    503, "SIN_CUENTAS",
                    "El servicio de cuentas no está disponible. Inténtalo más tarde.",
                )
            error = _fijar_espacio_pedido(request)
            return error if error is not None else await call_next(request)

        # Las rutas de entrada quedan fuera del CSRF. No actuan sobre la sesion
        # que puedas tener: entrar, salir o pedir un enlace de recuperacion
        # valen igual con cookie vieja que sin ninguna. Exigirselo dejaba
        # atrapado a quien volvia con una sesion caducada —tenia cookie, pero no
        # el token que la acompanaba— y no podia ni entrar ni salir.
        #
        # El precio conocido es el "login CSRF": una web ajena puede forzar que
        # entres en LA CUENTA DEL ATACANTE sin que te des cuenta. Se acepta a
        # sabiendas: evitarlo exige emitir un token antes de tener sesion, y el
        # dano —trabajar sin querer en una cuenta ajena— es visible en cuanto se
        # mira el nombre en pantalla, a diferencia de un robo de datos.
        if not _es_publica(request.url.path) and not csrf_valido(request):
            logger.warning("Petición rechazada por CSRF: %s %s", request.method, request.url.path)
            return _error(
                403, "CSRF",
                "La petición no incluye la comprobación de seguridad. "
                "Recarga la página e inténtalo de nuevo.",
            )

        resuelto = resolver_usuario(request, servicio)

        if resuelto is None:
            if _es_publica(request.url.path):
                return await call_next(request)

            # Una cookie que ya no vale se borra al vuelo: si no, el navegador
            # la reenvia en cada peticion y la interfaz se queda dando vueltas
            # entre "tengo sesion" y "el servidor dice que no".
            respuesta = _error(
                401, "SIN_SESION", "Necesitas iniciar sesión para usar Morgan."
            )
            if request.cookies.get(COOKIE_SESION):
                # Con `quitar_cookies`, no con un `delete_cookie` a mano.
                #
                # El de antes no llevaba `secure` ni `samesite`, asi que emitia
                # un borrado con SameSite=Lax para una cookie puesta con
                # SameSite=None. En una peticion entre dominios —Vercel contra
                # Render, o sea el despliegue real— el navegador RECHAZA esa
                # escritura, de modo que la cookie muerta seguia ahi y esto no
                # hacia absolutamente nada. Justo lo que pretendia evitar.
                quitar_cookies(respuesta)
            return respuesta

        user_id, rol = resuelto
        fijar_usuario(user_id, rol)
        error = _fijar_espacio_pedido(request)
        return error if error is not None else await call_next(request)


_DESAFIO = {"WWW-Authenticate": "Bearer"}


async def _por_token(request: Request, call_next, servicio, valor: str):
    """Atiende una petición hecha con un token personal de API.

    Cada rechazo tiene su código, porque quien integra necesita saber si el token
    no vale (401: crear otro) o si vale pero no para esto (403: darle el alcance
    o no intentarlo).
    """
    if not get_settings().tokens_api:
        respuesta = _error(
            401, "TOKENS_DESACTIVADOS", "Este Morgan no tiene activados los tokens de API."
        )
        respuesta.headers.update(_DESAFIO)
        return respuesta

    if servicio is None:
        return _error(
            503, "SIN_CUENTAS",
            "El servicio de cuentas no está disponible. Inténtalo más tarde.",
        )

    try:
        resuelto = ServicioDeTokens(servicio.repo).resolver(valor)
    except Exception:
        logger.warning("No se pudo comprobar un token de API", exc_info=True)
        return _error(
            503, "SIN_CUENTAS",
            "No se pudo comprobar el token. Inténtalo más tarde.",
        )

    if resuelto is None:
        respuesta = _error(
            401, "TOKEN_INVALIDO",
            "Ese token no vale: no existe, ha caducado o se ha revocado.",
        )
        respuesta.headers.update(_DESAFIO)
        return respuesta

    necesario = alcance_necesario(request.method, request.url.path)
    if necesario is None:
        return _error(
            403, "TOKEN_NO_PERMITIDO",
            "Esto no se puede hacer con un token de API, solo desde la web con tu "
            "sesión: tokens, contraseña, cuenta, administración y conexiones.",
        )

    token = resuelto["token"]
    if necesario not in token["alcances"]:
        return _error(
            403, "ALCANCE_INSUFICIENTE",
            f"Este token no tiene el alcance «{necesario}», que es el que hace falta aquí.",
        )

    usuario = ServicioDeCuentas._a_usuario(resuelto["usuario"])
    fijar_usuario(usuario.id, usuario.rol_efectivo)
    fijar_token(token["id"])
    error = _fijar_espacio_pedido(request)
    return error if error is not None else await call_next(request)


def _servicio() -> ServicioDeCuentas | None:
    """El servicio de cuentas, sin arrastrar el contenedor a este módulo.

    Se importa aquí dentro porque `dependencies` construye medio Morgan al
    importarse —modelos, herramientas, repositorios— y un import arriba haría que
    este módulo no se pudiera cargar sin todo eso en pie.
    """
    try:
        from src.api.dependencies import get_container
        from src.identidad.repositorio import repositorio_de_cuentas

        return ServicioDeCuentas(repositorio_de_cuentas(get_container().repositories))
    except Exception:
        logger.warning("No se pudo construir el servicio de cuentas", exc_info=True)
        return None
