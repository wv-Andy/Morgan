"""
Un plan pertenece a la conversación que lo creó, y eso lo decide el agente.

## El defecto, reproducido antes de arreglarlo

`create_plan` y `list_plans` pedían `session_id` al modelo como un parámetro
más, y el modelo **no lo conoce**: no aparece ni en el prompt ni en los
mensajes. Y el agente nunca recibía el planificador, así que el recorte del
catálogo no veía planes en absoluto:

    create_plan ok: True | estado: pendiente | session_id guardado: None
    despues de crear el plan -> get_plan ofrecido: False

El prompt le dice al modelo que, cuando la persona apruebe, `get_plan` se lo
dirá. **Y el turno siguiente ya no tenía `get_plan`.** El recorte, que nació en
la 2.0.3 para ahorrar cuota, lo quitaba por no haber «nada abierto».

## Lo que fija este archivo

1. Tras crear un plan **en un turno de verdad**, el siguiente ofrece `get_plan`.
2. El plan queda en **esta** conversación, aunque el modelo invente otra.
3. El modelo ya no ve `session_id` en los esquemas, ni puede colarlo.
4. Un Morgan sin gestor de tareas también ve sus planes.
"""

import pytest

from src.agent.core import Agent
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tasks.planificador import Planificador
from src.tools.filesystem import DeleteFileTool, ListFilesTool
from src.tools.planificacion import plan_tools
from src.tools.registry import ToolRegistry

#: Un paso con una herramienta delicada, para que el plan quede pendiente.
PASO_DELICADO = {
    "descripcion": "borrar el archivo",
    "herramienta": "delete_file",
    "argumentos": {"path": "x.txt"},
}


@pytest.fixture
def montaje(tmp_path):
    """Un agente de verdad con planificador, y un modelo falso que obedece."""
    registro = ToolRegistry()
    registro.register(ListFilesTool())
    registro.register(DeleteFileTool())
    fabrica = SQLiteRepositoryFactory(Database(tmp_path / "planes.db"))
    planificador = Planificador(fabrica.planes, registro)
    for herramienta in plan_tools(planificador):
        registro.register(herramienta)

    modelo = MockLLMProvider()
    agente = Agent(
        model=modelo,
        tool_registry=registro,
        permission_manager=PermissionManager(interactive=False),
        planes=planificador,
    )
    return agente, modelo, planificador


def _visibles(agente, session_id):
    return {e["name"] for e in agente._schemas_for(agente.sessions.get(session_id))}


def _turno_que_crea_un_plan(agente, modelo, session_id, argumentos=None):
    modelo.queue_tool_call("create_plan", argumentos or {
        "objetivo": "borrar un archivo",
        "pasos": [PASO_DELICADO],
    })
    # Sin texto del modelo detrás: un plan pendiente lo cierra el núcleo (4.1.5).
    agente.chat("borra x.txt", session_id=session_id)


class TestTrasCrearUnPlanSigueHabiendoGetPlan:
    def test_el_turno_siguiente_ofrece_get_plan(self, montaje):
        """El defecto entero, a través del bucle real del agente."""
        agente, modelo, _ = montaje
        assert "get_plan" not in _visibles(agente, "ses-1"), (
            "sin ningún plan, get_plan tiene que estar recortado"
        )

        _turno_que_crea_un_plan(agente, modelo, "ses-1")

        visibles = _visibles(agente, "ses-1")
        assert "get_plan" in visibles, (
            "tras crear un plan pendiente, el modelo no puede preguntar si se "
            "aprobó: el prompt le manda usar una herramienta que no tiene"
        )
        assert "list_plans" in visibles

    def test_el_plan_queda_en_esta_conversacion(self, montaje):
        agente, modelo, planificador = montaje

        _turno_que_crea_un_plan(agente, modelo, "ses-1")

        planes = planificador.listar(session_id="ses-1")
        assert len(planes) == 1, "el plan no está atado a la conversación"
        assert planes[0].estado == "pendiente"

    def test_el_plan_de_una_conversacion_no_abre_la_otra(self, montaje):
        """El recorte pregunta por ESTA conversación, igual que con las tareas."""
        agente, modelo, _ = montaje

        _turno_que_crea_un_plan(agente, modelo, "ses-1")

        assert "get_plan" not in _visibles(agente, "ses-2")


class TestLaConversacionLaPoneElAgente:
    def test_una_conversacion_inventada_por_el_modelo_se_ignora(self, montaje):
        """El caso peor del diseño anterior: el modelo no la sabe y se la
        inventa. El plan acababa en otra conversación del mismo usuario.

        Hoy el esquema ya no la anuncia, así que el validador la rechaza. La
        prueba fija además que, si llegara a la herramienta, **el dato del
        agente manda**.
        """
        agente, modelo, planificador = montaje
        herramienta = agente.tools.get("create_plan")

        # Se le pone de vuelta al esquema solo para esta prueba, para que el
        # validador la deje pasar y se vea quién gana.
        original = herramienta.parameters
        extendido = dict(original)
        extendido["properties"] = {
            **original["properties"],
            "session_id": {"type": "string"},
        }
        type(herramienta).parameters = property(lambda self: extendido)
        try:
            _turno_que_crea_un_plan(agente, modelo, "ses-buena", {
                "objetivo": "borrar",
                "pasos": [PASO_DELICADO],
                "session_id": "ses-inventada",
            })
        finally:
            type(herramienta).parameters = property(lambda self: original)

        assert planificador.listar(session_id="ses-inventada") == []
        assert len(planificador.listar(session_id="ses-buena")) == 1

    @pytest.mark.parametrize("nombre", ["create_plan", "list_plans"])
    def test_los_esquemas_ya_no_piden_la_conversacion(self, montaje, nombre):
        """El modelo no la conoce. Anunciarla es invitarle a inventarla, y
        cuesta los tokens de un parámetro que no sirve."""
        agente, _, _ = montaje
        esquema = agente.tools.get(nombre).get_schema()

        assert "session_id" not in esquema["parameters"].get("properties", {})

    def test_list_plans_devuelve_solo_los_de_esta_conversacion(self, montaje):
        """Su descripción dice «de esta conversación», y antes, sin
        `session_id`, listaba todos los del usuario."""
        agente, modelo, planificador = montaje
        planificador.crear("de otra", [PASO_DELICADO], session_id="ses-otra")
        _turno_que_crea_un_plan(agente, modelo, "ses-1")

        modelo.queue_tool_call("list_plans", {})
        modelo.queue_text("listo")
        agente.chat("¿qué planes hay?", session_id="ses-1")

        resultados = [
            m.tool_result for m in agente.sessions.get("ses-1").messages
            if m.role == "tool" and m.tool_name == "list_plans"
        ]
        assert resultados, "list_plans no llegó a ejecutarse"
        datos = resultados[-1]["data"]
        assert datos["total"] == 1, (
            f"list_plans ha devuelto {datos['total']} planes: se cuelan los de "
            "otra conversación"
        )


class TestSinGestorDeTareasTambienSeVenLosPlanes:
    def test_un_plan_abre_get_plan_aunque_no_haya_tareas(self, montaje):
        """`_hay_algo_abierto` salía con un `return False` si no había gestor
        de tareas, **antes** de mirar los planes."""
        agente, modelo, _ = montaje
        assert agente.tasks is None, "este montaje es justo el caso sin tareas"

        _turno_que_crea_un_plan(agente, modelo, "ses-1")

        assert "get_plan" in _visibles(agente, "ses-1")


class TestElContenedorLeDaElPlanificadorAlAgente:
    def test_el_agente_de_produccion_ve_los_planes(self, tmp_path, monkeypatch, modelo_simulado):
        """La mitad del defecto estaba aquí: el contenedor construía el agente
        sin el planificador, y ninguna prueba unitaria podía verlo porque todas
        montaban su propio agente."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path / "datos"))
        monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")

        from src.api.dependencies import CoreContainer
        from src.config import reset_settings

        reset_settings()
        contenedor = CoreContainer()
        assert contenedor.agent is not None, "con el modelo simulado siempre hay agente"

        assert contenedor.agent.planes is contenedor.planificador
