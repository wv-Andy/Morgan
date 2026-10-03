"""
Autenticación por token para Morgan API.

Morgan ejecuta acciones sobre la máquina donde corre. Sin autenticación, la API
solo puede escuchar de forma segura en `127.0.0.1`: cualquiera que la alcanzase
tendría `execute_command`, `delete_file` y `kill_process` sobre ese equipo.

Este módulo añade un token compartido **opcional**:

- Sin `MORGAN_API_TOKEN` definido, el comportamiento no cambia (uso local).
- Con el token definido, toda petición a la API debe presentarlo. Solo quedan
  fuera `/health` (para comprobaciones de disponibilidad) y los archivos de la
  interfaz web, que por sí solos no permiten hacer nada.

No sustituye a un sistema de usuarios: es un secreto compartido, suficiente para
proteger un despliegue personal detrás de HTTPS.
"""

import hmac
import logging
import secrets

from fastapi import FastAPI, Request

from src.config import get_settings
from src.identidad.tokens import token_de_la_cabecera
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Rutas que exigen token cuando hay uno configurado.
#
# **Toda la API**, no una selección. Hasta la V3.0.0-dev faltaban siete prefijos
# —cuentas, administración, espacios, planes, integraciones, diagnóstico y la sonda
# del canal—, así que con el token compartido puesto (el Morgan de tu equipo abierto
# con el túnel) 32 rutas respondían SIN token y como el propietario: medido, crear un
# espacio daba 201, listar planes y usuarios 200, y aprobar un plan —lo que autoriza
# a ejecutar herramientas peligrosas— también. La prueba que debía impedirlo recorría
# `app.routes`, que en esta versión de FastAPI ya no enseña las rutas de los routers:
# pasaba sin comprobar nada. Ahora compara con el esquema OpenAPI.
PROTECTED_PREFIXES = (
    "status", "chat", "tools", "memory", "audit", "sessions", "settings", "uploads", "tasks",
    "auth", "admin", "espacios", "planes", "integraciones", "diagnostico", "sonda-canal",
    "agente",
    "automatizaciones", "avisos",   # 4.14
    "docs", "redoc", "openapi.json",
)

# Abierta a propósito: permite comprobar que el servicio vive sin repartir el token.
PUBLIC_PATHS = ("/health",
                # El reloj de Supabase (4.14) trae su propio secreto, no este token.
                "/automatizaciones/reloj")

BEARER_PREFIX = "bearer "


def generate_token(length: int = 32) -> str:
    """Genera un token aleatorio apto para `MORGAN_API_TOKEN`."""
    return secrets.token_urlsafe(length)


def extract_token(request: Request) -> str | None:
    """Lee el token de la cabecera Authorization o de X-Morgan-Token."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX):].strip() or None

    return request.headers.get("x-morgan-token") or None


def _requires_token(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return False
    # La vuelta de OAuth llega como una navegación desde GitHub: el navegador no
    # añade la cabecera del token. De quién es la autorización lo dice el `state`,
    # emitido con una petición que sí lo llevaba (lo mismo que en la identidad).
    if path.startswith("/integraciones/") and path.endswith("/callback"):
        return False
    first_segment = path.lstrip("/").split("/", 1)[0]
    return first_segment in PROTECTED_PREFIXES


def install_token_auth(app: FastAPI, token: str | None = None) -> bool:
    """Instala la comprobación de token en la aplicación.

    El token se lee **en cada petición**, no al crear la aplicación. La app se
    construye una sola vez al importar el módulo, así que fijarlo aquí congelaba
    la configuración: cambiar el token exigía reiniciar el proceso, y en las
    pruebas el valor del `.env` del desarrollador se colaba en toda la suite.

    El parámetro `token` sigue aceptándose para poder fijarlo explícitamente en
    pruebas; si se omite, se toma de la configuración vigente.
    """
    token_fijo = token

    @app.middleware("http")
    async def _check_token(request: Request, call_next):
        token_actual = token_fijo if token_fijo is not None else get_settings().api_token

        # Sin token configurado no se exige nada: es el uso local de siempre.
        if not token_actual:
            return await call_next(request)

        # Las peticiones preflight de CORS no llevan cabeceras propias.
        if request.method == "OPTIONS" or not _requires_token(request.url.path):
            return await call_next(request)

        # Un token personal de API (`mgn_...`) no es este secreto: lo comprueba
        # la capa de identidad, que va por dentro y lo RECHAZA si no vale, con o
        # sin cuentas exigidas. Pararlo aquí haría imposible usar tokens
        # personales en un despliegue con token compartido.
        if token_de_la_cabecera(request.headers.get("authorization")) is not None:
            return await call_next(request)

        presented = extract_token(request)

        # compare_digest evita filtrar el token por diferencias de tiempo.
        if presented is None or not hmac.compare_digest(presented, token_actual):
            logger.warning(
                "Petición sin token válido a %s desde %s",
                request.url.path,
                request.client.host if request.client else "origen desconocido",
            )
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Se requiere un token válido para usar Morgan API.",
                        "details": None,
                    },
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

    ajustes = get_settings()

    if ajustes.api_token:
        logger.info("Autenticación por token activada en Morgan API")
        return True

    # Sin token pero con cuentas, la API SI exige autenticacion: la piden las
    # sesiones, no el secreto compartido. Avisar aqui de que "no exige
    # autenticacion" haria creer que el despliegue esta abierto cuando no lo
    # esta, y es el tipo de aviso que lleva a "arreglarlo" poniendo un token
    # compartido, que es justo lo que impide que entre nadie mas.
    if ajustes.require_auth:
        logger.info(
            "Sin MORGAN_API_TOKEN, pero MORGAN_REQUIRE_AUTH está activo: el "
            "acceso lo controlan las cuentas de usuario."
        )
        return False

    logger.warning(
        "Ni MORGAN_API_TOKEN ni MORGAN_REQUIRE_AUTH: la API no exige "
        "autenticación. Es aceptable escuchando solo en 127.0.0.1; no expongas "
        "el servicio en red sin definir una de las dos."
    )
    return False
