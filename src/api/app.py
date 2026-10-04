"""
Factoría de la aplicación FastAPI para Morgan API (V1.0).
Configura CORS, documentación OpenAPI, manejo global de errores y rutas.
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import logging
from contextlib import asynccontextmanager

from src import __version__
from src.config import Settings, get_settings
from src.api.routes.health import router as health_router
from src.api.routes.chat import router as chat_router
from src.api.routes.tools import router as tools_router
from src.api.routes.memory import router as memory_router
from src.api.routes.audit import router as audit_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.settings import router as settings_router
from src.api.routes.tasks import router as tasks_router
from src.api.routes.uploads import router as uploads_router
from src.api.routes.cuentas import router as cuentas_router
from src.api.routes.integraciones import router as integraciones_router
from src.api.routes.admin import router as admin_router
from src.api.routes.planes import router as planes_router
from src.api.routes.automatizaciones import router as automatizaciones_router
from src.api.routes.diagnostico import router as diagnostico_router
from src.api.routes.espacios import router as espacios_router
from src.api.routes.agentes import router_agente, router_web as agentes_web_router
from src.api.auth import install_token_auth
from src.api.observabilidad_middleware import CABECERA, install_observabilidad
from src.api.identidad_middleware import install_identidad
from src.api.static import mount_api_root, mount_web_ui


logger = logging.getLogger(__name__)


def _es_despliegue_de_prueba() -> bool:
    """Si este proceso es un despliegue de prueba (src/prueba_de_carga.py).

    Si el modo está pedido pero no puede encenderse, el arranque ya aborta en
    `server.py`; aquí basta con no montar nada.
    """
    from src import prueba_de_carga

    try:
        return prueba_de_carga.comprobar(get_settings())
    except prueba_de_carga.PruebaDeCargaInsegura:
        return False


@asynccontextmanager
async def _ciclo_de_vida(app: FastAPI):
    """Arranque y apagado de la app.

    Al arrancar, el aviso de reinicio a los agentes locales (3.0-D): el proxy de Render
    no les reenvía el cierre 1012 y tardaban 13 s en notar un despliegue. Ver
    `src/canal/reinicio.py`.
    """
    from src.canal import reinicio

    try:
        reinicio.instalar()
    except Exception:
        logger.warning("No se pudo instalar el aviso de reinicio a los agentes", exc_info=True)

    # El reloj interno de las automatizaciones (4.14). En otro hilo: montar el contenedor
    # tarda, y el servidor tiene que contestar ya (Render mide el arranque).
    if get_settings().reloj_interno:
        import threading

        def arrancar_el_reloj() -> None:
            try:
                from src.api.dependencies import get_container

                get_container().reloj.arrancar()
            except Exception:
                logger.warning("No se pudo arrancar el reloj de las automatizaciones", exc_info=True)

        threading.Thread(target=arrancar_el_reloj, name="morgan-reloj-arranque", daemon=True).start()
    # Los errores, a la bandeja del propietario (4.20).
    if get_settings().avisar_errores:
        from src import alertas

        alertas.instalar()
    yield


def create_app() -> FastAPI:
    """Crea y configura la instancia de FastAPI para Morgan."""

    app = FastAPI(
        title="Morgan API",
        version=__version__,
        description=(
            "Servicio Backend y API REST para Morgan (Personal AI Agent para Windows).\n\n"
            "Permite la interacción con el Core de Morgan, ejecución de herramientas, "
            "consulta de memoria persistente SQLite, gestión de permisos y auditoría de seguridad."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_ciclo_de_vida,
    )

    # --- Configuración CORS ---
    settings = get_settings()

    # Un origen no autorizado no da un error legible: el navegador bloquea la
    # llamada y la interfaz solo puede decir "API desconectada". Por eso el
    # arranque deja constancia de que orígenes acepta.
    if settings.is_cloud and settings.cors_origins == Settings.cors_origins and not settings.cors_origin_regex:
        logger.warning(
            "MORGAN_CORS_ORIGINS no está configurada en un despliegue cloud: solo "
            "se aceptan %s. Cualquier interfaz servida desde otro dominio verá sus "
            "peticiones bloqueadas por el navegador.",
            ", ".join(settings.cors_origins),
        )
    else:
        logger.info(
            "CORS: orígenes permitidos %s%s",
            ", ".join(settings.cors_origins),
            f" (+ patrón {settings.cors_origin_regex})" if settings.cors_origin_regex else "",
        )

    cors_kwargs = {
        "allow_origins": list(settings.cors_origins),
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        # Sin esto el navegador recibe la cabecera y NO deja leerla: `*` en
        # `allow_headers` es para las que MANDA el cliente, no para las que
        # puede leer de la respuesta. Hay que nombrarla.
        "expose_headers": [CABECERA],
    }
    if settings.cors_origin_regex:
        cors_kwargs["allow_origin_regex"] = settings.cors_origin_regex

    # El middleware se instala mas abajo, DESPUES del de autenticacion.
    # Starlette aplica el ultimo registrado por fuera, y CORS tiene que quedar
    # por fuera de todo para poder etiquetar tambien las respuestas de error.

    # --- Manejadores globales de errores uniformes ---

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", "HTTP_ERROR")
            message = exc.detail.get("message", str(exc.detail))
            details = exc.detail.get("details", None)
        else:
            code = f"HTTP_{exc.status_code}"
            message = str(exc.detail)
            details = None

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Datos de petición no válidos según el esquema.",
                    "details": [
                        {"loc": list(err.get("loc", [])), "msg": err.get("msg"), "type": err.get("type")}
                        for err in exc.errors()
                    ],
                },
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        # La traza se registra en el log del servidor pero NO se devuelve al cliente:
        # str(exc) filtraba rutas absolutas del disco y detalles internos.
        logger.exception("Error no controlado en %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Ocurrió un error inesperado en el servidor. "
                               "Consulta logs/morgan.log para el detalle.",
                    "details": None,
                },
            },
        )

    # --- Registrar Routers ---
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(tools_router)
    app.include_router(memory_router)
    app.include_router(audit_router)
    app.include_router(sessions_router)
    app.include_router(settings_router)
    app.include_router(uploads_router)
    app.include_router(tasks_router)
    app.include_router(cuentas_router)
    app.include_router(integraciones_router)
    app.include_router(admin_router)
    app.include_router(planes_router)
    app.include_router(automatizaciones_router)
    app.include_router(espacios_router)
    # Agentes locales (3.0-C): la gestión con sesión y lo que llama el propio agente.
    app.include_router(agentes_web_router)
    app.include_router(router_agente)
    # Instrumentos, no funcionalidad: miden Morgan en lugar de servir para
    # usarlo. Exigen permiso de sistema, y por eso van con los demas.
    app.include_router(diagnostico_router)
    # La sonda del canal de la 3.0-B: SOLO en el despliegue de prueba (modo de
    # prueba de carga, que se niega a encenderse con claves reales o con el
    # Supabase de producción). En producción esta ruta no existe.
    if _es_despliegue_de_prueba():
        from src.api.routes.sonda_canal import router as sonda_canal_router

        app.include_router(sonda_canal_router)

    # --- Identidad: quien hace cada peticion (identidad, V2.0 adelantada) ---
    # Se registra ANTES que el token compartido, luego queda por dentro: el
    # token protege el despliegue entero y las cuentas separan a las personas
    # dentro de el. Al reves, una peticion sin token llegaria a tocar la base
    # de cuentas antes de ser rechazada.
    install_identidad(app)

    # --- Autenticación por token (opcional) ---
    install_token_auth(app)

    # --- En que se va el tiempo (V2.0) --------------------------------------
    # El ultimo de los propios, o sea el mas externo: asi mide tambien lo que
    # cuestan la identidad, el CSRF y el token, que son parte de la peticion.
    install_observabilidad(app)

    # --- CORS, el ultimo en registrarse a proposito -------------------------
    # Starlette aplica el ultimo middleware registrado por FUERA de los demas.
    # Registrado antes que la autenticacion, un 401 salia sin cabeceras CORS: el
    # navegador bloqueaba la respuesta y el codigo del cliente nunca llegaba a
    # ver el 401, asi que en vez de pedir el token mostraba "API desconectada".
    # Poniendolo por fuera, hasta los errores viajan etiquetados.
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # --- Interfaz web (opcional) ---
    # Se monta al final, para que el comodín de la SPA no ensombrezca las rutas
    # de la API registradas arriba.
    montada = mount_web_ui(app) if settings.serve_web else False
    if not montada:
        # Sin interfaz que servir, la raiz explica que es esto en lugar de
        # devolver un 404 que parece un despliegue roto.
        mount_api_root(app)

    return app


app = create_app()
