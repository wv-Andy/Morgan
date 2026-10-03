"""
El plan aprobado se ejecuta solo (4.0-A).

Decisión mía (2026-09-26): **aprobar es la orden**. Antes, tras aprobar un plan había
que escribirle a Morgan «adelante» (visto en la prueba real de la 3.9). Ahora la web manda
el turno con `ejecutar_plan` y **el agente** ejecuta los pasos, no el modelo: en orden, con
los argumentos aprobados, por el mismo camino que cualquier llamada (autorización del plan,
verificación, anotación). Lo que haría daño si fallase, y se fija aquí: que se ejecute algo
distinto de lo aprobado, o dos veces, o un paso cuyo anterior falló, o un plan que no es de
esta conversación o que nadie aprobó.
"""

import pytest

from src.agent.core import Agent
from src.canal.herramientas import EscribirEnElEquipo
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tasks.planificador import Planificador
from src.tools.planificacion import plan_tools
from src.tools.registry import ToolRegistry

RUTA = "C:/Users/ana/notas.txt"


class DelPc(EscribirEnElEquipo):
    """Una de las del PC, que exige plan, sin agente: apunta lo que se le pide."""

    hechas: list = []
    origenes: list = []
    fallan: set = set()

    def disponible(self):
        return True

    def execute(self, equipo=None, **argumentos):
        DelPc.origenes.append(argumentos.pop("_origen", None))
        DelPc.hechas.append((self.name, argumentos))
        if self.name in DelPc.fallan:
            return {"success": False, "data": None, "error": "el PC dijo que no"}
        return {"success": True, "data": {"path": argumentos.get("path"), "sha256": "ab" * 32}, "error": None}


@pytest.fixture
def montaje(tmp_path):
    DelPc.hechas, DelPc.origenes, DelPc.fallan = [], [], set()
    registro = ToolRegistry()
    for nombre in ("create_file", "edit_file", "delete_file"):
        registro.register(DelPc(nombre))
    fabrica = SQLiteRepositoryFactory(Database(tmp_path / "planes.db"))
    planificador = Planificador(fabrica.planes, registro)
    for herramienta in plan_tools(planificador):
        registro.register(herramienta)
    modelo = MockLLMProvider()
    agente = Agent(model=modelo, tool_registry=registro,
                   permission_manager=PermissionManager(interactive=False), planes=planificador)
    return agente, modelo, planificador


def _plan(planificador, sesion, pasos, aprobar=True):
    plan = planificador.crear("cambiar mis notas", pasos, session_id=sesion)
    if aprobar:
        planificador.aprobar(plan.id)
    return plan


PASOS = [
    {"descripcion": "crear las notas", "herramienta": "create_file",
     "argumentos": {"path": RUTA, "content": "pan y leche"}},
    {"descripcion": "corregirlas", "herramienta": "edit_file",
     "argumentos": {"path": RUTA, "old_text": "pan", "new_text": "arroz"}, "depende_de": [1]},
]


def _ejecutar(agente, modelo, plan, sesion="s1", texto="Listo: creé y corregí tus notas."):
    modelo.queue_text(texto)
    return agente.chat("✅ Plan aprobado: cambiar mis notas", session_id=sesion, ejecutar_plan=plan.id)


class TestAprobarEsLaOrden:
    def test_los_pasos_se_hacen_en_orden_con_lo_aprobado(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", PASOS)
        respuesta = _ejecutar(agente, modelo, plan)
        assert DelPc.hechas == [("create_file", {"path": RUTA, "content": "pan y leche"}),
                                ("edit_file", {"path": RUTA, "old_text": "pan", "new_text": "arroz"})]
        # Todo salió: el resumen lo escribe el núcleo, sin llamar al modelo (4.1.5).
        assert respuesta == "Listo:\n- crear las notas (comprobado)\n- corregirlas (comprobado)"
        assert modelo.call_count == 0
        assert DelPc.origenes == [f"{plan.id}#1", f"{plan.id}#2"], "cada orden dice de qué paso sale (4.1)"
        assert planificador.obtener(plan.id).estado == "completado"

    def test_el_modelo_recibe_el_informe_y_no_ejecuta_nada(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", PASOS)
        _ejecutar(agente, modelo, plan)
        usuario = [m for m in agente.sessions.get("s1").messages if m.role == "user"][-1]
        assert "Morgan ya ejecutó sus pasos" in usuario.content
        assert "1. crear las notas (create_file): hecho, y comprobado." in usuario.content
        assert len(DelPc.hechas) == 2, "el modelo solo contesta: los pasos los ejecuta el agente"

    def test_si_un_paso_falla_el_que_depende_de_el_no_se_hace(self, montaje):
        agente, modelo, planificador = montaje
        DelPc.fallan = {"create_file"}
        plan = _plan(planificador, "s1", PASOS)
        _ejecutar(agente, modelo, plan, texto="No pude crear las notas.")
        assert [h[0] for h in DelPc.hechas] == ["create_file"]
        usuario = [m for m in agente.sessions.get("s1").messages if m.role == "user"][-1]
        assert "NO salió: el PC dijo que no" in usuario.content
        assert "NO se ejecutó: el paso 1 no salió" in usuario.content
        assert planificador.obtener(plan.id).estado == "fallido"

    def test_se_para_en_el_primer_fallo_aunque_no_se_declare_la_dependencia(self, montaje):
        """Medido con el modelo real (4.0.5): «cambia kiwi por mango y, solo si sale, crea
        hecho.txt» llegó sin `depende_de`, y hecho.txt se creó tras fallar el cambio."""
        agente, modelo, planificador = montaje
        DelPc.fallan = {"edit_file"}
        pasos = [{"descripcion": "cambiar", "herramienta": "edit_file",
                  "argumentos": {"path": RUTA, "old_text": "kiwi", "new_text": "mango"}},
                 {"descripcion": "apuntarlo", "herramienta": "create_file",
                  "argumentos": {"path": "C:/Users/ana/hecho.txt", "content": "cambiado"}}]
        plan = _plan(planificador, "s1", pasos)
        _ejecutar(agente, modelo, plan, texto="No encontré kiwi.")
        assert [h[0] for h in DelPc.hechas] == ["edit_file"]

    def test_lo_que_no_se_ejecuto_no_queda_autorizado(self, montaje):
        """El plan se cierra como fallido: el paso que se saltó ya no autoriza nada, ni
        aunque el modelo lo pida después con los mismos argumentos."""
        agente, modelo, planificador = montaje
        DelPc.fallan = {"create_file"}
        plan = _plan(planificador, "s1", PASOS)
        modelo.queue_tool_call("edit_file", {"path": RUTA, "old_text": "pan", "new_text": "arroz"})
        _ejecutar(agente, modelo, plan, texto="No salió.")
        assert [h[0] for h in DelPc.hechas] == ["create_file"]


class TestSoloLoQueToca:
    def test_un_plan_sin_aprobar_no_se_ejecuta(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", PASOS, aprobar=False)
        _ejecutar(agente, modelo, plan)
        assert DelPc.hechas == []
        assert planificador.obtener(plan.id).estado == "pendiente"

    def test_uno_de_otra_conversacion_no_existe_aqui(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "otra", PASOS)
        _ejecutar(agente, modelo, plan, sesion="s1")
        assert DelPc.hechas == []
        assert planificador.obtener(plan.id).estado == "aprobado"

    def test_uno_que_no_existe(self, montaje):
        agente, modelo, _ = montaje

        class Falso:
            id = "plan-inventado"
        _ejecutar(agente, modelo, Falso)
        assert DelPc.hechas == []

    def test_dos_veces_no_hace_nada_dos_veces(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", PASOS)
        _ejecutar(agente, modelo, plan)
        _ejecutar(agente, modelo, plan)
        assert len(DelPc.hechas) == 2

    def test_sin_ejecutar_plan_nada_cambia(self, montaje):
        """Un turno normal no ejecuta un plan aprobado por su cuenta."""
        agente, modelo, planificador = montaje
        _plan(planificador, "s1", PASOS)
        modelo.queue_text("hola")
        agente.chat("hola", session_id="s1")
        assert DelPc.hechas == []


class TestElOrigenNoLoPoneElModelo:
    def test_un_origen_escrito_en_el_plan_no_llega_a_ejecutarse(self, montaje):
        """Si el modelo mete `_origen` en los argumentos al proponer el plan, el paso no se
        ejecuta: el esquema de la herramienta no lo admite. Medido al escribir esta prueba;
        el `pop` del núcleo queda como defensa redundante (4.1)."""
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", [{"descripcion": "crear", "herramienta": "create_file",
                                           "argumentos": {"path": RUTA, "content": "x", "_origen": "falso#9"}}])
        _ejecutar(agente, modelo, plan)
        assert DelPc.hechas == [] and DelPc.origenes == []
        usuario = [m for m in agente.sessions.get("s1").messages if m.role == "user"][-1]
        assert "NO salió" in usuario.content and "_origen" in usuario.content


class TestElResumenCuandoTodoSale:
    """4.1.5, decisión mía: si todos los pasos salieron, el resumen lo escribe el núcleo
    y no se gasta una llamada al modelo (~4.500 tokens medidos) solo para decir «listo»."""

    def test_un_paso(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", PASOS[:1])
        assert _ejecutar(agente, modelo, plan) == "Listo: crear las notas (comprobado)."
        assert modelo.call_count == 0

    def test_si_algo_falla_lo_cuenta_el_modelo(self, montaje):
        agente, modelo, planificador = montaje
        DelPc.fallan = {"edit_file"}
        plan = _plan(planificador, "s1", PASOS)
        assert _ejecutar(agente, modelo, plan, texto="No pude corregirlas.") == "No pude corregirlas."
        assert modelo.call_count == 1

    def test_si_hay_un_paso_sin_herramienta_tambien(self, montaje):
        """Un paso sin herramienta es del modelo: explicar o preguntar."""
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", [PASOS[0], {"descripcion": "preguntarle el título", "herramienta": None}])
        assert _ejecutar(agente, modelo, plan, texto="¿Qué título le pongo?") == "¿Qué título le pongo?"
        assert modelo.call_count == 1

    def test_queda_en_la_conversacion(self, montaje):
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", PASOS[:1])
        _ejecutar(agente, modelo, plan)
        ultimo = agente.sessions.get("s1").messages[-1]
        assert ultimo.role == "model" and ultimo.content.startswith("Listo")


class TestUnPasoNoneEsSinHerramienta:
    def test_none_como_texto_no_para_el_plan(self, montaje):
        """Medido con el modelo real (4.1): un paso «none» delante de `append_file` contó
        como herramienta inexistente y el plan se paró antes de añadir la línea."""
        agente, modelo, planificador = montaje
        plan = _plan(planificador, "s1", [
            {"descripcion": "pensar la fecha", "herramienta": "none", "argumentos": {}},
            {"descripcion": "crear", "herramienta": "create_file", "argumentos": {"path": RUTA, "content": "x"}},
        ])
        assert planificador.obtener(plan.id).pasos[0].herramienta is None
        _ejecutar(agente, modelo, plan)
        assert [h[0] for h in DelPc.hechas] == ["create_file"]



class TestLoQueLaPersonaNoPermite:
    """4.4, medido con el modelo real: tras un borrado que nadie confirmó en el PC, la
    corrección de la 4.0-C lo volvía a proponer y quedaba otro plan pendiente. «No
    confirmado» es una decisión de la persona, no un fallo que corregir."""

    def test_ni_otro_camino_ni_otro_plan(self, montaje, monkeypatch):
        from src.agent.core import NO_CONFIRMADO

        agente, modelo, planificador = montaje

        def no_confirmado(self, equipo=None, **argumentos):
            DelPc.hechas.append((self.name, argumentos))
            return {"success": False, "data": None, "motivo": "no_confirmada",
                    "error": "La persona no lo permitió en su PC (o no contestó a tiempo)."}

        monkeypatch.setattr(DelPc, "execute", no_confirmado)
        plan = _plan(planificador, "s1", [{"descripcion": "borrar", "herramienta": "delete_file",
                                            "argumentos": {"path": RUTA}}])
        modelo.queue_tool_call("create_plan", {"objetivo": "borrar otra vez", "pasos": [
            {"descripcion": "borrar", "herramienta": "delete_file", "argumentos": {"path": RUTA}}]})
        modelo.queue_text("No lo borré: no lo permitiste en tu PC.")
        _ejecutar(agente, modelo, plan, texto="no debería llegar")
        usuario = [m for m in agente.sessions.get("s1").messages if m.role == "user"][-1]
        assert NO_CONFIRMADO in usuario.content and "UN intento" not in usuario.content
        rechazo = [m.tool_result for m in agente.sessions.get("s1").messages if m.role == "tool"][-1]
        assert rechazo["error"] == NO_CONFIRMADO
        assert [p for p in planificador.listar(session_id="s1") if p.estado == "pendiente"] == []


class TestElMotivoDelAgenteViaja:
    def test_la_nube_no_lo_descarta(self, monkeypatch):
        from src.canal import herramientas

        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: {
            "estado": "COMPLETED", "resultado": {"success": False, "error": "no", "motivo": "no_confirmada"}})
        r = herramientas.EscribirEnElEquipo("delete_file").execute(path="C:/x.txt")
        assert r["motivo"] == "no_confirmada"
