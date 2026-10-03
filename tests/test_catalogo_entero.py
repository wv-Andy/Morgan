"""
Cada herramienta registrada, mirada entera (3.1).

**Por qué existe.** La copia del PC (`copy_file`) salió a producción con un import roto
dentro de `permission_level`: `RiskLevel` no está en `src.security.validator`. Sus 41
pruebas pasaban porque ninguna leía esa propiedad —las herramientas se prueban por su
`execute`—, y el fallo apareció en mi móvil, cuatro veces seguidas:

    Error interno: cannot import name 'RiskLevel' from 'src.security.validator'

Una propiedad de una herramienta se lee **cuando el catálogo va al modelo**, no al
ejecutarla, así que un error ahí no se ve hasta que alguien la usa de verdad. Esto lo
mira todo, de todas, incluidas las del PC, que solo existen con el equipo conectado.
"""

import json

import pytest

from src.security.permissions import PermissionManager
from src.tools.base import RiskLevel, Tool, ToolCategory

CATEGORIAS = {c.value for c in ToolCategory}
#: Los que el gestor de permisos entiende. Incluye «sensitive», un alias antiguo de
#: `critical` que sigue en uso (`delete_file`) y que el gestor traduce.
RIESGOS = set(PermissionManager.LEVELS) | {r.value for r in RiskLevel}


def _todas() -> list[Tool]:
    """Las del catálogo de la nube más las del PC, que no se registran sin agente."""
    from src.api import dependencies
    from src.canal.herramientas import herramientas_del_equipo

    dependencies.reset_container()
    contenedor = dependencies.get_container()
    del_equipo = herramientas_del_equipo(store=contenedor.uploads)
    registradas = contenedor.tool_registry.list_tools()
    nombres = {h.name for h in del_equipo}
    return [t for t in registradas if t.name not in nombres] + del_equipo


@pytest.fixture(scope="module")
def herramientas():
    from src.api import dependencies

    todas = _todas()
    yield todas
    dependencies.reset_container()


def test_hay_catalogo(herramientas):
    assert len(herramientas) > 20


def test_cada_una_se_puede_leer_entera(herramientas):
    """Nombre, descripción, parámetros, riesgo y categoría: lo que se lee al armar el
    catálogo del turno. Un import roto en cualquiera de ellas rompe el turno."""
    for h in herramientas:
        assert isinstance(h.name, str) and h.name, h
        assert isinstance(h.description, str) and len(h.description) > 10, h.name
        assert isinstance(h.parameters, dict) and h.parameters.get("type") == "object", h.name
        assert h.permission_level in RIESGOS, (h.name, h.permission_level)
        assert h.category in CATEGORIAS, (h.name, h.category)
        assert isinstance(h.requires_local, bool), h.name
        assert isinstance(h.disponible(), bool), h.name


def test_cada_esquema_viaja_como_json(herramientas):
    """Lo que de verdad se le manda al modelo."""
    for h in herramientas:
        esquema = json.loads(json.dumps(h.get_schema(), ensure_ascii=False))
        assert esquema["name"] == h.name
        assert esquema["parameters"]["type"] == "object"


def test_los_nombres_no_se_repiten(herramientas):
    nombres = [h.name for h in herramientas]
    assert len(nombres) == len(set(nombres)), "dos herramientas con el mismo nombre"
