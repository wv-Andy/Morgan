"""
Lo que actúa fuera de Morgan, solo con un plan aprobado (V2.0.16).

**Por qué existe.** decidí que Morgan pueda **escribir** en los servicios
conectados —empezando por Google Calendar—, pero siempre con un plan que la
persona aprueba antes. Al ir a construirlo apareció que aprobar un plan **no
desbloqueaba nada**:

- a quien no es propietario, la web le deniega todo lo confirmable —no hay
  consola—, con plan aprobado o sin él;
- al propietario no se le pregunta nunca.

Así que ni la aprobación servía para lo primero ni protegía de lo segundo.

`Tool.exige_plan` cierra las dos: la herramienta se ejecuta **si y solo si** hay
en esta conversación un paso aprobado con esta herramienta y **estos argumentos
exactos**, y **una sola vez**.
"""

import pytest

from src.agent.core import Agent
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tasks.planificador import Planificador
from src.tools.base import Tool
from src.tools.planificacion import plan_tools
from src.tools.registry import ToolRegistry

EVENTO = {"titulo": "Dentista", "inicio": "2026-09-20T10:00:00"}


class CrearEventoFalso(Tool):
    """Una herramienta que escribe fuera de Morgan, y apunta cada vez que lo hace."""

    def __init__(self, riesgo: str = "moderate", falla: bool = False):
        self._riesgo = riesgo
        self.falla = falla
        self.creados: list[dict] = []

    @property
    def name(self) -> str:
        return "crear_evento_falso"

    @property
    def description(self) -> str:
        return "Crea un evento en el calendario de la persona."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"titulo": {"type": "string"}, "inicio": {"type": "string"}},
            "required": ["titulo", "inicio"],
        }

    @property
    def permission_level(self) -> str:
        return self._riesgo

    @property
    def category(self) -> str:
        return "general"

    @property
    def requires_local(self) -> bool:
        return False

    @property
    def exige_plan(self) -> bool:
        return True

    def execute(self, **kwargs):
        if self.falla:
            return {"success": False, "data": None, "error": "el calendario no responde"}
        self.creados.append(kwargs)
        return {"success": True, "data": {"id": f"ev-{len(self.creados)}"}, "error": None}


@pytest.fixture
def montaje(tmp_path):
    def montar(herramienta: CrearEventoFalso | None = None, permisos: PermissionManager | None = None):
        herramienta = herramienta or CrearEventoFalso()
        registro = ToolRegistry()
        registro.register(herramienta)
        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "exige.db"))
        planificador = Planificador(fabrica.planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        modelo = MockLLMProvider()
        agente = Agent(
            model=modelo,
            tool_registry=registro,
            permission_manager=permisos or PermissionManager(interactive=False),
            planes=planificador,
        )
        return agente, modelo, planificador, herramienta

    return montar


def _llamar(agente, modelo, argumentos, session_id="ses-1") -> dict:
    """Un turno en el que el modelo pide la herramienta; devuelve lo que recibió."""
    modelo.queue_tool_call("crear_evento_falso", argumentos)
    modelo.queue_text("hecho")
    agente.chat("crea el evento", session_id=session_id)
    resultados = [
        m.tool_result for m in agente.sessions.get(session_id).messages
        if m.role == "tool" and m.tool_name == "crear_evento_falso"
    ]
    return resultados[-1]


def _plan(planificador, argumentos, session_id="ses-1", aprobar=True):
    plan = planificador.crear(
        "apuntar el dentista",
        [{"descripcion": "crear el evento", "herramienta": "crear_evento_falso", "argumentos": argumentos}],
        session_id=session_id,
    )
    if aprobar:
        planificador.aprobar(plan.id, quien="usr-que-aprueba")
    return plan


class TestSinPlanNoSeEjecuta:
    def test_sin_plan_dice_como_proponerlo(self, montaje):
        agente, modelo, _, herramienta = montaje()

        resultado = _llamar(agente, modelo, EVENTO)

        assert herramienta.creados == []
        assert resultado["success"] is False
        assert "create_plan" in resultado["error"]
        assert "Dentista" in resultado["error"], "tiene que decir con qué argumentos proponerlo"

    def test_aunque_los_permisos_lo_dejaran_pasar(self, montaje):
        """El propietario no tiene confirmaciones. Esto no puede depender de eso."""
        permisos = PermissionManager(interactive=False)
        permisos.allow_tool("crear_evento_falso")
        agente, modelo, _, herramienta = montaje(permisos=permisos)

        _llamar(agente, modelo, EVENTO)

        assert herramienta.creados == []

    def test_con_el_plan_pendiente_tampoco(self, montaje):
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, EVENTO, aprobar=False)

        resultado = _llamar(agente, modelo, EVENTO)

        assert herramienta.creados == []
        assert "espera aprobación" in resultado["error"]


class TestConElPlanAprobado:
    def test_se_ejecuta_lo_aprobado(self, montaje):
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, EVENTO)

        resultado = _llamar(agente, modelo, EVENTO)

        assert resultado["success"] is True
        assert herramienta.creados == [EVENTO]

    def test_una_sola_vez(self, montaje):
        """Aprobar «crear el evento de las 10» no autoriza a crearlo dos veces."""
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, EVENTO)

        _llamar(agente, modelo, EVENTO)
        segundo = _llamar(agente, modelo, EVENTO)

        assert len(herramienta.creados) == 1
        assert segundo["success"] is False

    def test_una_sola_vez_aunque_el_plan_siga_abierto(self, montaje):
        """El caso que la marca `ejecutado` protege de verdad.

        Con un solo paso, el plan se cierra al ejecutarlo y ya no se busca: esa
        barrera tapaba a esta, y una mutación que la quitaba sobrevivía. Con dos
        pasos, tras el primero el plan sigue abierto y solo la marca impide
        repetirlo.
        """
        agente, modelo, planificador, herramienta = montaje()
        otro = {"titulo": "Revisión", "inicio": "2026-09-27T10:00:00"}
        plan = planificador.crear(
            "apuntar dos citas",
            [
                {"descripcion": "primera", "herramienta": "crear_evento_falso", "argumentos": EVENTO},
                {"descripcion": "segunda", "herramienta": "crear_evento_falso", "argumentos": otro},
            ],
            session_id="ses-1",
        )
        planificador.aprobar(plan.id, quien="usr-que-aprueba")

        _llamar(agente, modelo, EVENTO)
        assert planificador.obtener(plan.id).estado == "ejecutando"
        repetido = _llamar(agente, modelo, EVENTO)

        assert herramienta.creados == [EVENTO]
        assert repetido["success"] is False

        _llamar(agente, modelo, otro)
        assert herramienta.creados == [EVENTO, otro]
        assert planificador.obtener(plan.id).estado == "completado"

    def test_otros_argumentos_no(self, montaje):
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, EVENTO)

        resultado = _llamar(agente, modelo, {**EVENTO, "inicio": "2026-09-20T11:00:00"})

        assert herramienta.creados == []
        assert "otros argumentos" in resultado["error"]
        assert "10:00" in resultado["error"], "tiene que decir qué se aprobó"

    def test_el_orden_de_las_claves_no_importa(self, montaje):
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, {"inicio": EVENTO["inicio"], "titulo": EVENTO["titulo"]})

        _llamar(agente, modelo, EVENTO)

        assert herramienta.creados == [EVENTO]

    def test_un_plan_de_otra_conversacion_no_autoriza_aqui(self, montaje):
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, EVENTO, session_id="otra-conversacion")

        _llamar(agente, modelo, EVENTO, session_id="ses-1")

        assert herramienta.creados == []

    def test_si_falla_la_aprobacion_sigue_valiendo(self, montaje):
        """La persona aprobó el efecto, y el efecto no ha ocurrido."""
        herramienta = CrearEventoFalso(falla=True)
        agente, modelo, planificador, _ = montaje(herramienta)
        plan = _plan(planificador, EVENTO)

        _llamar(agente, modelo, EVENTO)

        guardado = planificador.obtener(plan.id)
        assert guardado.pasos[0].ejecutado is False
        assert guardado.estado == "aprobado"

        herramienta.falla = False
        _llamar(agente, modelo, EVENTO)
        assert herramienta.creados == [EVENTO]

    def test_el_plan_queda_completado_y_el_paso_marcado(self, montaje):
        agente, modelo, planificador, _ = montaje()
        plan = _plan(planificador, EVENTO)

        _llamar(agente, modelo, EVENTO)

        guardado = planificador.obtener(plan.id)
        assert guardado.pasos[0].ejecutado is True
        assert guardado.estado == "completado"
        assert guardado.to_dict()["pasos"][0]["ejecutado"] is True

    def test_en_una_conversacion_temporal_no_hay_autorizacion(self, montaje):
        agente, modelo, planificador, herramienta = montaje()
        _plan(planificador, EVENTO, session_id="temporal")
        modelo.queue_tool_call("crear_evento_falso", EVENTO)
        modelo.queue_text("hecho")

        agente.chat("crea el evento", session_id="temporal", temporary=True)

        assert herramienta.creados == []


class TestElPlanNoSeApruebaSolo:
    def test_una_herramienta_que_exige_plan_nunca_cuenta_como_segura(self, montaje):
        """Un plan de pasos seguros nace aprobado: declararla `safe` lo aprobaría solo."""
        _, _, planificador, _ = montaje(CrearEventoFalso(riesgo="safe"))

        plan = _plan(planificador, EVENTO, aprobar=False)

        assert plan.estado == "pendiente"


class TestSeGuardaEnLosDosAlmacenes:
    def test_supabase_guarda_la_marca_de_ejecutado(self):
        from src.memory.supabase_repositories import SupabasePlanRepository
        from src.tasks.plan import PasoPlaneado, Plan

        plan = Plan(id="p1", objetivo="x", pasos=[
            PasoPlaneado(orden=1, descripcion="d", herramienta="h", argumentos={"a": "x" * 500}, ejecutado=True),
        ])

        fila = SupabasePlanRepository.__new__(SupabasePlanRepository)._fila(plan)

        assert fila["pasos"][0]["ejecutado"] is True
        assert fila["pasos"][0]["argumentos"]["a"] == "x" * 500, "los argumentos van enteros"
