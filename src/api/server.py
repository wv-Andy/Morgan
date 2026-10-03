"""
Punto de entrada para iniciar el servidor HTTP de Morgan API.

Además de arrancar uvicorn, aquí vive la comprobación previa que impide publicar
Morgan sin autenticación. Es el equivalente en el backend a la que hace
`scripts/morgan-remoto.ps1` antes de abrir el túnel.
"""

import os
import sys

import uvicorn

from src.config import get_settings
from src.logging_config import setup_logging

#: El tope de un mensaje por WebSocket, el mismo que usa el agente (`max_size`).
WS_MAX_SIZE = 2 * 2**20


class ArranqueInseguro(RuntimeError):
    """Se intentó publicar Morgan sin las protecciones mínimas."""


def check_startup_safety(settings) -> None:
    """Aborta si la configuración expondría Morgan sin autenticación.

    Un backend en `cloud` escucha en una URL pública. Sin nada que lo proteja,
    cualquiera que dé con ella puede usar Morgan, consumir la cuota de los modelos
    y leer la memoria. En `local` no se exige, porque ahí la API solo escucha en
    127.0.0.1 y el túnel tiene su propia comprobación.

    **Hay dos formas válidas de protegerlo, y basta con una:**

    - `MORGAN_API_TOKEN`: un secreto compartido. Sirve para un Morgan privado, al
      que entras solo tú.
    - `MORGAN_REQUIRE_AUTH`: cuentas de usuario. Sirve para un Morgan abierto a
      más gente.

    Antes se exigía el token siempre, y eso hacía **imposible** el segundo caso:
    para invitar a alguien había que darle el token, con lo que esa persona
    obtenía acceso a la API entera al margen de su cuenta. Las cuentas no son una
    protección más floja que el token: son más fuerte, porque además separan los
    datos de cada uno.
    """
    # Antes que nada, y en cualquier entorno: el modo de prueba de carga no puede
    # encenderse con claves reales ni contra el Supabase de producción.
    from src.prueba_de_carga import PruebaDeCargaInsegura, comprobar

    try:
        comprobar(settings)
    except PruebaDeCargaInsegura as exc:
        raise ArranqueInseguro(str(exc)) from exc

    if settings.environment != "cloud":
        return

    if not settings.api_token and not settings.require_auth:
        raise ArranqueInseguro(
            "MORGAN_ENVIRONMENT=cloud necesita MORGAN_API_TOKEN o "
            "MORGAN_REQUIRE_AUTH=true.\n"
            "Un backend en la nube responde en una URL publica: sin ninguna de "
            "las dos cosas queda abierto a cualquiera que la encuentre.\n"
            "  - Para un Morgan privado, solo tuyo, genera un token con:\n"
            '    python -c "from src.api.auth import generate_token; print(generate_token())"\n'
            "  - Para un Morgan con cuentas, abierto a mas gente, define\n"
            "    MORGAN_REQUIRE_AUTH=true"
        )

    if not os.getenv("MALLOC_ARENA_MAX"):
        # No impide arrancar, pero se dice (V2.0.39). Medido en un despliegue
        # igual al de producción (2.3-C): sin esta variable, con 25 personas a la
        # vez la memoria llegó a 530 MB de 537 y Render mató el proceso. Con
        # varios hilos reservando a la vez, glibc crea una zona de memoria por
        # hilo que crece y no se devuelve. Con `MALLOC_ARENA_MAX=2`: 182 MB con 25
        # y 197 MB con 50, sin un error.
        print(
            "Aviso: falta MALLOC_ARENA_MAX=2 en el entorno. Sin ella, con muchas "
            "personas a la vez la memoria crece hasta que la plataforma mata el "
            "proceso (docs/mediciones.md, apartado 4).",
            file=sys.stderr,
        )

    if settings.api_host not in ("0.0.0.0", "::"):
        # No es un fallo de seguridad, pero en un contenedor escuchar solo en
        # 127.0.0.1 hace que la plataforma nunca alcance el servicio.
        print(
            f"Aviso: MORGAN_API_HOST={settings.api_host} en entorno cloud. "
            "La plataforma no podra alcanzar el servicio; suele hacer falta 0.0.0.0.",
            file=sys.stderr,
        )


def start():
    """Inicia el servidor uvicorn para Morgan API."""
    settings = get_settings()
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)

    try:
        check_startup_safety(settings)
    except ArranqueInseguro as exc:
        print(f"\nMorgan no puede arrancar:\n\n{exc}\n", file=sys.stderr)
        sys.exit(1)

    print(f"Iniciando Morgan API en http://{settings.api_host}:{settings.api_port}")
    print(f"Documentación OpenAPI disponible en http://{settings.api_host}:{settings.api_port}/docs")
    uvicorn.run(
        "src.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
        # La implementación de WebSocket que manda el cierre 1012 («reinicio») a los
        # agentes conectados cuando el servidor se apaga. La que da `auto` hoy (la
        # antigua, marcada como obsoleta) corta sin código, y la sonda de la 3.0-B
        # lo vio así: el agente se enteraba por el corte, no por un aviso.
        ws="websockets-sansio",
        # Tope de un mensaje por WebSocket (3.0.5). El de uvicorn es 16 MB; un agente
        # sano manda como mucho ~300 KB (256 KB de lectura más el sobre), y en la
        # instancia gratuita de 512 MB, un agente autenticado mandando mensajes de
        # 16 MB es presión de memoria sin motivo. Medido: con 20 MB se cortaba; con 3
        # MB se aceptaba. El agente usa el mismo tope para lo que recibe.
        ws_max_size=WS_MAX_SIZE,
    )


if __name__ == "__main__":
    start()
