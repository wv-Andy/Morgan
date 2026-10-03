"""
Rutas de salud y diagnóstico del sistema para Morgan API (V1.0).
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from src.api.schemas import (
    HealthResponse,
    StatusResponse,
    ComponentStatus,
    ServiceStatusItem,
    CapabilityItem,
    SyncStatus,
)
from src.config import get_settings
from src.api.dependencies import CoreContainer, get_container

from src import __version__

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Status"])


@router.get("/health", response_model=HealthResponse, summary="Health Check básico")
async def health_check() -> HealthResponse:
    """Verifica que el servicio HTTP de Morgan esté activo y respondiendo.

    `async` a propósito (V2.0.27): así no espera turno en la reserva de hilos de
    las rutas síncronas. Es la que mira Render para reiniciar el servicio, y no
    puede depender de lo ocupado que esté el resto. No hace nada que bloquee.
    """
    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _nombre_del_almacen(container) -> str:
    """Cómo se llama el almacén que Morgan está usando de verdad.

    Se deduce de la fábrica de repositorios en uso, no de la configuración: en
    la nube el contenedor cambia `repositories` a Supabase al arrancar, y es ese
    cambio —no la variable de entorno— el que decide dónde acaban los datos.
    """
    fabrica = type(container.repositories).__name__
    if "Supabase" in fabrica:
        return "Supabase"
    if "SQLite" in fabrica:
        return "SQLite"
    return fabrica



def _cuantas_claves(modelo) -> str:
    """«(2 claves)» si algun proveedor lleva llavero. Vacio si ninguno.

    **Por que esto esta en `/status`.** Cuando se recreo el servicio de
    produccion, dos claves de modelo salieron mal escritas y el sintoma fue que
    **todo parecia funcionar**: los turnos se contestaban y `/status` decia que
    el modelo estaba disponible listando la cadena entera. Lo unico distinto era
    que el proveedor de pago contestaba todos los turnos.

    Con varias claves por proveedor ese fallo tiene una version nueva: si
    `GROQ_API_KEY_2` esta mal copiada, Morgan se cae a una sola clave, la cuota
    vuelve a la mitad **y desde fuera no se nota**. El aviso existe, pero solo
    en el registro, y el registro hay que ir a buscarlo.

    Asi que el numero se dice donde ya se mira. Se dice **cuantas**, nunca
    cuales: es la misma regla que en `Settings.redacted()`.
    """
    from src.models.fallback import FallbackProvider

    proveedores = (
        list(modelo.providers) if isinstance(modelo, FallbackProvider) else [modelo]
    )
    partes = [
        f"{p.claves_disponibles} claves"
        for p in proveedores
        if getattr(p, "claves_disponibles", 1) > 1
    ]
    return f" ({', '.join(partes)})" if partes else ""


@router.get("/status", response_model=StatusResponse, summary="Diagnóstico de Resiliencia del Sistema")
def system_status(container: CoreContainer = Depends(get_container)) -> StatusResponse:
    """
    Ejecuta el 'Morgan Startup / Resilience Check':
    Inspecciona cada subsistema (Core, Base de datos SQLite, Herramientas, Permisos, LLM).
    Reporta si opera en modo 'normal' o en modo 'degraded'.
    """
    components = {}

    # 1. Core
    components["core"] = ComponentStatus(status="ok", details="Morgan Core activo")

    # 2. Base de datos
    #
    # Dice QUE almacen es y si responde. Antes decia «SQLite conectado» siempre,
    # escrito a mano: en la nube el almacen es Supabase, asi que el panel de
    # estado informaba de algo que no era cierto.
    #
    # Y ya no cuenta recuerdos. Contaba los de QUIEN PREGUNTA, y esta ruta es
    # publica: sin sesion el contexto es el usuario implicito, de modo que
    # marcaba «0 recuerdos» a todo el mundo, incluida gente que tenia. Un numero
    # por usuario tampoco pinta nada en un estado de sistema; donde importa es
    # en la vista de memoria, que ya lo enseña.
    db_ok, db_error = container.memory_manager.health()
    if db_ok:
        components["database"] = ComponentStatus(
            status="ok", details=f"{_nombre_del_almacen(container)} conectado",
        )
    else:
        components["database"] = ComponentStatus(status="error", details=db_error)

    # 3. Herramientas
    tools_count = len(container.tool_registry)
    domains_count = len(container.tool_registry.get_categories())
    components["tools"] = ComponentStatus(
        status="ok",
        details=f"{tools_count} herramientas en {domains_count} dominios",
    )

    # 4. Permisos & Auditoría
    components["permissions"] = ComponentStatus(
        status="ok",
        details="5 niveles de riesgo y auditoría activa",
    )

    # 5. LLM
    if container.agent is not None:
        components["llm"] = ComponentStatus(
            status="ok",
            details=f"Modelo: {container.agent.model.model_name}"
                    f"{_cuantas_claves(container.agent.model)}",
        )
    else:
        components["llm"] = ComponentStatus(
            status=container.llm_status,
            details=container.llm_error or "Sin proveedor LLM configurado. Modo degradado activo.",
        )

    overall_status = "ready" if container.mode == "normal" else "degraded"

    # --- Estado por servicio (V1.3 §8) ---
    # check_all respeta el TTL: no sale a la red si el valor sigue fresco, de
    # modo que consultar /status a menudo no penaliza.
    servicios = container.health.check_all()
    services = [ServiceStatusItem(**s.to_dict()) for s in servicios.values()]

    settings = get_settings()

    return StatusResponse(
        status=overall_status,
        mode=container.mode,
        tools_count=tools_count,
        domains_count=domains_count,
        components=components,
        environment=settings.environment,
        services=services,
        capabilities=_build_capabilities(container, servicios, settings),
        sync=_build_sync_status(container, servicios),
    )


def _build_sync_status(container, servicios) -> SyncStatus:
    """Cuántas operaciones esperan a subir a la nube."""
    cola = getattr(container, "sync_queue", None)
    if cola is None:
        return SyncStatus(enabled=False)

    remoto = servicios.get("database.remote")
    try:
        return SyncStatus(
            enabled=True,
            pending=cola.count(),
            failed=len(cola.failed()),
            remote_available=remoto.is_usable if remoto else None,
        )
    except Exception:
        # La cola es informativa: un fallo al consultarla no debe romper /status.
        logger.warning("No se pudo consultar la cola de sincronización", exc_info=True)
        return SyncStatus(enabled=True)


def _archivos(registry) -> CapabilityItem:
    """Los archivos del equipo: en local, siempre; en la nube, con el agente conectado.

    En la nube, `read_file` es el representante del agente local (3.0-E): está
    registrado aunque nadie tenga su PC conectado. Mirar solo si existe decía
    «disponible» sin ningún PC; lo que cuenta es si está disponible **para quien
    pregunta**.
    """
    lectura = registry.get("read_file")
    disponible = lectura is not None and lectura.disponible()
    if disponible:
        motivo = None
    elif lectura is not None and getattr(lectura, "dinamica", False):
        motivo = "Con el agente local conectado en tu PC (Ajustes → Tu equipo)"
    else:
        motivo = "No disponible en el entorno cloud"
    return CapabilityItem(name="Archivos del equipo", available=disponible, reason=motivo)


def _build_capabilities(container, servicios, settings) -> list[CapabilityItem]:
    """Qué puede hacer Morgan ahora mismo, en lenguaje del usuario (V1.3 §14)."""
    internet = servicios.get("internet")
    base_datos = servicios.get("database.local")
    llm = servicios.get("llm")

    hay_internet = internet.is_usable if internet else False
    hay_bd = base_datos.is_usable if base_datos else False
    hay_llm = llm.is_usable if llm else False

    registry = container.tool_registry
    tiene = registry.__contains__

    return [
        CapabilityItem(
            name="Conversación (LLM)",
            available=hay_llm,
            reason=None if hay_llm else (llm.detail if llm else "Sin proveedor configurado"),
        ),
        CapabilityItem(
            name="Memoria y historial",
            available=hay_bd,
            reason=None if hay_bd else (base_datos.detail if base_datos else None),
        ),
        CapabilityItem(
            name="Búsqueda web",
            available=hay_internet and tiene("search_web"),
            reason=None if hay_internet else "Sin conexión a Internet",
        ),
        _archivos(registry),
        CapabilityItem(
            name="Terminal y procesos",
            available=tiene("execute_command"),
            reason=None if tiene("execute_command") else "No disponible en el entorno cloud",
        ),
    ]
