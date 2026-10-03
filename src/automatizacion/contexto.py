"""
Lo que una petición o un turno saben de las automatizaciones (4.14).

- **La zona horaria de quien pide**: la manda la web en cada petición (`X-Morgan-Zona`, la
  de su navegador) y la fija el middleware de identidad. «A las 9» se guarda como las 9 de
  esa zona.
- **Si este turno es de una automatización**: nadie está delante, así que solo se usan
  las herramientas de `PERMITIDAS` (decisión mía, 2026-09-30: «solo consultas»; del
  PC, solo leer). Lo comprueba el núcleo dos veces: al ofrecer el catálogo y al ejecutar.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from src.automatizacion.horario import zona_valida

#: Lo que puede usar una automatización en la 4.14. Lista blanca, no negra: una herramienta
#: nueva no entra sola. Fuera, a propósito: lo que pide «Permitir» en el PC (portapapeles,
#: capturas: nadie lo pulsaría), lo que actúa (abrir aplicaciones, ventanas, avisos,
#: escribir, planes, tareas, recordar) y crear otras automatizaciones.
PERMITIDAS = frozenset({
    # Internet, el calendario y GitHub, solo leer.
    "search_web", "read_webpage", "calendario_ver_eventos",
    "github_leer_archivo", "github_listar_issues", "github_listar_prs", "github_listar_repos",
    # Lo que la persona le ha enseñado y lo que recuerda.
    "search_knowledge", "list_knowledge_sources", "recall_memory", "list_uploads", "read_upload",
    # El PC, solo leer (y solo si está conectado y encendido allí).
    "list_files", "read_file", "search_files", "file_info", "system_info", "pc_context",
    "pc_diagnostics", "get_processes", "run_command",
})

_zona: ContextVar[str | None] = ContextVar("morgan_zona", default=None)
_automatizacion: ContextVar[dict | None] = ContextVar("morgan_automatizacion", default=None)


def fijar_zona(nombre: str | None) -> object:
    """Fija la zona de esta petición si es válida (si no, ninguna). Devuelve el testigo."""
    return _zona.set(zona_valida(nombre))


def zona_actual() -> str | None:
    return _zona.get()


@contextmanager
def en_automatizacion(automatizacion: dict):
    """Marca el bloque como el turno de una automatización."""
    testigo = _automatizacion.set(automatizacion)
    try:
        yield
    finally:
        _automatizacion.reset(testigo)


def automatizacion_actual() -> dict | None:
    return _automatizacion.get()


def permitida(herramienta: str, argumentos: dict | None = None) -> bool:
    """Si esta herramienta se puede usar ahora: siempre, salvo en una automatización.

    En una de **pasos fijos** (4.15, `_pasos`: los de esta ejecución, ya con su fecha), solo
    una llamada **idéntica** a uno de ellos, que es lo que hace el ejecutor de planes. Ni
    siquiera las de consulta: el modelo, si habla, solo redacta el aviso. Sin argumentos
    (al armar el catálogo del turno), ninguna."""
    auto = _automatizacion.get()
    if auto is None:
        return True
    pasos = auto.get("_pasos")
    if pasos is not None:
        return argumentos is not None and any(
            p["herramienta"] == herramienta and p["argumentos"] == argumentos for p in pasos)
    return herramienta in PERMITIDAS


NO_PERMITIDA = ("Una automatización solo consulta (4.14): esta herramienta no se puede usar sin "
                "nadie delante. Cuenta en el informe qué habría hecho falta.")
