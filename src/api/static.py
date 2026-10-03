"""
Publicación de la interfaz web desde la propia API.

Antes hacían falta dos procesos y dos puertos para usar Morgan: `uvicorn` en el
8000 y el servidor de desarrollo de Vite en el 5173. Si existe un build del
frontend en `web/dist`, la API lo sirve directamente y basta con una sola URL.

Es opcional a propósito: si no hay build, la API sigue funcionando exactamente
igual y el flujo de desarrollo con `npm run dev` no cambia.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import get_settings

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WEB_DIST = BASE_DIR / "web" / "dist"

# Prefijos que pertenecen a la API. El comodín de la SPA no debe capturarlos:
# una ruta desconocida bajo estos prefijos tiene que seguir devolviendo 404 JSON,
# no la página HTML.
API_PREFIXES = (
    "health", "status", "chat", "tools", "memory", "audit", "sessions", "settings", "uploads", "tasks",
    "docs", "redoc", "openapi.json",
)


def mount_web_ui(app: FastAPI, dist_dir: Path | None = None) -> bool:
    """Publica el build del frontend en la raíz de la API.

    Devuelve True si se montó, False si no hay build disponible.
    Debe llamarse **después** de registrar los routers: el comodín de la SPA se
    resuelve en último lugar y no debe ensombrecer las rutas de la API.
    """
    dist = Path(dist_dir) if dist_dir is not None else WEB_DIST
    index_file = dist / "index.html"

    if not index_file.is_file():
        logger.info(
            "No hay build del frontend en %s; la API no servirá la interfaz web. "
            "Genéralo con 'npm --prefix web run build'.",
            dist,
        )
        return False

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def _serve_index():
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str):
        # Archivos reales del build (favicon.svg, icons.svg, ...).
        candidate = (dist / full_path).resolve()
        if dist.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)

        # Una ruta desconocida de la API debe seguir siendo un 404 JSON.
        first_segment = full_path.split("/", 1)[0]
        if first_segment in API_PREFIXES:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"La ruta '/{full_path}' no existe en Morgan API.",
                        "details": None,
                    },
                },
            )

        # Cualquier otra ruta la resuelve la aplicación de una sola página.
        return FileResponse(index_file)

    logger.info("Interfaz web publicada desde %s", dist)
    return True


def mount_api_root(app: FastAPI) -> None:
    """Publica una raiz informativa cuando la API no sirve la interfaz.

    Sin esto, abrir la URL del backend en el navegador devuelve el 404 escueto de
    FastAPI (`{"detail": "Not Found"}`), que parece un despliegue roto cuando en
    realidad todo funciona: es que la interfaz vive en otro sitio.

    Solo se registra si `mount_web_ui` no monto nada; en local, donde la API si
    sirve el build, la raiz sigue devolviendo la pagina.
    """

    @app.get("/", include_in_schema=False)
    async def _describe_api():
        settings = get_settings()
        return {
            "service": "Morgan API",
            "status": "ok",
            "environment": settings.environment,
            "message": (
                "Esto es el backend de Morgan, no la interfaz. La interfaz web se "
                "sirve por separado."
                if settings.is_cloud
                else "Esto es la API de Morgan. No hay build del frontend en web/dist; "
                     "generalo con 'npm --prefix web run build'."
            ),
            "endpoints": {
                "health": "/health",
                "docs": "/docs",
                "status": "/status (requiere token)",
            },
            # Un origen mal configurado no da error legible: el navegador bloquea
            # la llamada y la interfaz solo puede decir "API desconectada".
            # Publicarlos convierte ese sintoma mudo en algo que se mira y se ve.
            # No son un secreto: cualquiera puede descubrirlos probando origenes.
            "cors": {
                "allowed_origins": list(settings.cors_origins),
                "origin_regex": settings.cors_origin_regex,
            },
        }
