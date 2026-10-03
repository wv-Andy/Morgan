"""
Comprobaciones concretas de cada dependencia de Morgan.

Separadas del registro (`src/health.py`) para que este no sepa nada de SQLite, de
sockets ni de proveedores LLM: el registro solo orquesta, aquí vive el detalle.

Ninguna comprobación es cara ni bloquea mucho tiempo: todas llevan un límite
corto y propio, porque su función es informar, no trabajar.
"""

import logging
import socket
import time

from src.health import ServiceState

logger = logging.getLogger(__name__)

# Comprobar Internet resolviendo un nombre es más honesto que hacer ping a una IP:
# una DNS rota deja a Morgan igual de incomunicado que un cable desenchufado.
INTERNET_PROBE_HOST = "one.one.one.one"
INTERNET_PROBE_PORT = 443
INTERNET_PROBE_TIMEOUT = 2.0

# Por encima de este tiempo, un servicio responde pero va mal.
SLOW_THRESHOLD_SECONDS = 3.0


def check_internet() -> tuple[ServiceState, str | None]:
    """Comprueba que hay salida a Internet, con un socket y un límite corto."""
    started = time.monotonic()
    try:
        with socket.create_connection(
            (INTERNET_PROBE_HOST, INTERNET_PROBE_PORT), timeout=INTERNET_PROBE_TIMEOUT
        ):
            pass
    except OSError as exc:
        return ServiceState.UNAVAILABLE, f"Sin salida a Internet: {exc}"

    elapsed = time.monotonic() - started
    if elapsed > SLOW_THRESHOLD_SECONDS:
        return ServiceState.DEGRADED, f"Conexión lenta ({elapsed:.1f} s)"
    return ServiceState.AVAILABLE, f"Conectado ({elapsed * 1000:.0f} ms)"


def make_local_database_check(repositories) -> callable:
    """Comprobación de la base local. No sale a la red, así que siempre es barata."""

    def _check() -> tuple[ServiceState, str | None]:
        started = time.monotonic()
        ok, error = repositories.health()
        if not ok:
            return ServiceState.UNAVAILABLE, error

        elapsed = time.monotonic() - started
        if elapsed > SLOW_THRESHOLD_SECONDS:
            return ServiceState.DEGRADED, f"Respuesta lenta ({elapsed:.1f} s)"
        return ServiceState.AVAILABLE, "SQLite operativo"

    return _check


def make_llm_check(container) -> callable:
    """Estado del razonamiento.

    No se gasta una llamada real al modelo en cada comprobación: costaría dinero y
    latencia. Se informa de la configuración y de lo que haya observado el
    proveedor de respaldo durante el uso normal.
    """

    def _check() -> tuple[ServiceState, str | None]:
        agent = getattr(container, "agent", None)
        if agent is None:
            return ServiceState.UNAVAILABLE, container.llm_error or "Sin proveedor LLM configurado"

        model = agent.model
        fallos = getattr(model, "primary_failures", 0)
        usos_respaldo = getattr(model, "fallback_uses", 0)

        if usos_respaldo:
            return (
                ServiceState.DEGRADED,
                f"{model.model_name} · el proveedor principal falló {fallos} vez/veces; "
                f"se usó el respaldo {usos_respaldo}",
            )

        return ServiceState.AVAILABLE, model.model_name

    return _check


def make_remote_database_check(remote) -> callable:
    """Comprobación de la nube. Sale a la red, así que el TTL la protege."""

    def _check() -> tuple[ServiceState, str | None]:
        started = time.monotonic()
        ok, error = remote.health()
        if not ok:
            return ServiceState.UNAVAILABLE, error

        elapsed = time.monotonic() - started
        if elapsed > SLOW_THRESHOLD_SECONDS:
            return ServiceState.DEGRADED, f"Supabase responde lento ({elapsed:.1f} s)"
        return ServiceState.AVAILABLE, f"Supabase operativo ({elapsed * 1000:.0f} ms)"

    return _check


def check_correo() -> tuple[ServiceState, str | None]:
    """Comprueba si la recuperación de contraseña puede funcionar.

    **Solo mira si hay configuración, no envía nada.** Una comprobación que
    intentara autenticarse contra el servidor SMTP en cada consulta de estado
    tardaría segundos y acabaría bloqueada por el proveedor por conectarse
    demasiado a menudo.

    Existe porque el fallo que cubre es silencioso: sin SMTP, pedir un enlace de
    recuperación responde exactamente igual que con él —esa respuesta es vaga a
    propósito, para no delatar si la cuenta existe— y el único sitio donde se ve
    que no salió nada es el log del servidor. Aquí se ve sin entrar en él.
    """
    from src.config import get_settings
    from src.identidad.correo import _base_web, transporte, ultimo_intento

    via = transporte()
    nube = get_settings().require_auth

    # El enlace se construye con MORGAN_WEB_URL, y su valor por defecto es el de
    # desarrollo. Sin definirla en un despliegue publico, el correo SALE
    # PERFECTAMENTE y lleva a "localhost", donde no hay nada escuchando: la
    # persona recibe un enlace que no abre en ningun navegador.
    #
    # Es el peor tipo de fallo de configuracion —todo parece correcto, incluido
    # el estado del correo— y por eso se comprueba aqui.
    base = _base_web()
    if nube and ("localhost" in base or "127.0.0.1" in base):
        return ServiceState.UNAVAILABLE, (
            f"MORGAN_WEB_URL apunta a {base}: los enlaces de recuperación se "
            "envían bien pero llevan al equipo de quien los recibe, no a la web. "
            "Defínela con la dirección pública."
        )

    if via is not None:
        # Si ya se intento enviar algo, lo que paso pesa mas que "esta
        # configurado": una configuracion completa pero con la contrasena
        # equivocada tiene el mismo aspecto que una correcta hasta que se usa.
        intento = ultimo_intento()

        if intento["ok"] is False:
            return ServiceState.UNAVAILABLE, f"El último envío falló: {intento['detalle']}"

        if intento["ok"] is True:
            return ServiceState.AVAILABLE, f"Enviando por {via} · último envío correcto"

        return ServiceState.AVAILABLE, f"Configurado ({via}), aún no se ha enviado nada"

    if get_settings().require_auth:
        # Con cuentas, no poder recuperar la contrasena deja a la gente fuera
        # para siempre. Eso no es "degradado", es una funcion que no existe.
        return ServiceState.UNAVAILABLE, (
            "Sin correo configurado: quien pierda su contraseña NO podrá "
            "recuperarla. Faltan MORGAN_EMAIL_API y MORGAN_EMAIL_API_KEY (o, "
            "fuera de la nube, MORGAN_SMTP_HOST/USER/PASSWORD)."
        )

    # Sin cuentas no hay contrasenas que recuperar: no falta nada.
    return ServiceState.AVAILABLE, "No hace falta: este Morgan no usa cuentas"


def register_default_checks(health, container) -> None:
    """Da de alta las dependencias que Morgan conoce hoy."""
    health.register(
        "internet",
        check_internet,
        required_for=("search_web", "read_webpage", "llm remoto"),
    )
    health.register(
        "database.local",
        make_local_database_check(container.repositories),
        required_for=("memoria", "historial", "sesiones"),
    )
    health.register(
        "llm",
        make_llm_check(container),
        required_for=("chat", "razonamiento"),
    )
    health.register(
        "correo",
        check_correo,
        required_for=("recuperar la contraseña",),
    )

    # La nube solo se registra si está configurada: un servicio que nadie usa no
    # debería aparecer como "caído" y ensuciar el estado global.
    remoto = getattr(container, "remote_repositories", None)
    if remoto is not None:
        health.register(
            "database.remote",
            make_remote_database_check(remoto),
            required_for=("sincronización",),
        )
