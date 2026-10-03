"""
Corregir, con tope (4.0-C).

Decisión mía (2026-09-26): si algo que cambia cosas no sale (la herramienta falla o la
verificación dice que el efecto no está), Morgan tiene **un** intento con otro camino, nunca
lo mismo; si tampoco sale, lo dice con lo que intentó. Sin tope, un modelo insistente gasta
cuota y tiempo probando cambios sin llegar. Lo mismo que falló ya se bloqueaba (3.1).
"""

import pytest

from src.agent.core import SIN_MAS_INTENTOS, UN_INTENTO, YA_PROBASTE, Agent
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tools.base import Tool
from src.tools.registry import ToolRegistry


class Falsa(Tool):
    """Una herramienta que hace lo que se le diga y apunta sus llamadas."""

    def __init__(self, nombre, riesgo="moderate", resultados=None):
        self._nombre, self._riesgo = nombre, riesgo
        self.resultados = list(resultados or [])
        self.llamadas = []

    @property
    def name(self):
        return self._nombre

    @property
    def description(self):
        return self._nombre

    @property
    def parameters(self):
        texto = {"type": "string"}
        return {"type": "object", "properties": {"x": texto, "path": texto, "content": texto}, "required": []}

    @property
    def permission_level(self):
        return self._riesgo

    @property
    def requires_local(self):
        return False

    def execute(self, **kw):
        self.llamadas.append(kw)
        ok = self.resultados.pop(0) if self.resultados else True
        return {"success": ok, "data": {}, "error": None if ok else "no salió"}


@pytest.fixture
def montaje(monkeypatch):
    def armar(*herramientas):
        registro = ToolRegistry()
        for h in herramientas:
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        return Agent(model=modelo, tool_registry=registro, permission_manager=permisos), modelo
    return armar


def _turno(agente, modelo, llamadas, sesion="s"):
    for nombre, args in llamadas:
        modelo.queue_tool_call(nombre, args)
    modelo.queue_text("fin")
    agente.chat("hazlo", session_id=sesion)
    return [m.tool_result for m in agente.sessions.get(sesion).messages if m.role == "tool"]


class TestUnIntento:
    def test_un_fallo_da_un_intento_con_otro_camino(self, montaje):
        a, b = Falsa("crear", resultados=[False]), Falsa("otra_forma")
        agente, modelo = montaje(a, b)
        r = _turno(agente, modelo, [("crear", {"x": "1"}), ("otra_forma", {"x": "1"})])
        assert UN_INTENTO in r[0]["error"]
        assert r[1]["success"] and len(b.llamadas) == 1

    def test_si_el_intento_tampoco_sale_no_hay_mas_cambios(self, montaje):
        a, b, c = Falsa("crear", resultados=[False]), Falsa("otra_forma", resultados=[False]), Falsa("tercera")
        agente, modelo = montaje(a, b, c)
        r = _turno(agente, modelo, [("crear", {}), ("otra_forma", {}), ("tercera", {})])
        assert YA_PROBASTE in r[1]["error"]
        assert r[2]["error"] == SIN_MAS_INTENTOS and c.llamadas == [], "el tercer cambio no se ejecuta"

    def test_consultar_sigue_pudiendose(self, montaje):
        a, b = Falsa("crear", resultados=[False]), Falsa("otra_forma", resultados=[False])
        mirar = Falsa("mirar", riesgo="safe")
        agente, modelo = montaje(a, b, mirar)
        r = _turno(agente, modelo, [("crear", {}), ("otra_forma", {}), ("mirar", {})])
        assert r[2]["success"] and len(mirar.llamadas) == 1

    def test_un_intento_que_sale_cierra_el_asunto(self, montaje):
        a = Falsa("crear", resultados=[False, True, False, True])
        agente, modelo = montaje(a)
        r = _turno(agente, modelo, [("crear", {"x": "1"}), ("crear", {"x": "2"}), ("crear", {"x": "3"}),
                                    ("crear", {"x": "4"})])
        assert UN_INTENTO in r[2]["error"], "un fallo nuevo, tras corregir bien, da su propio intento"
        assert r[3]["success"] and len(a.llamadas) == 4

    def test_cada_turno_empieza_de_cero(self, montaje):
        a, b = Falsa("crear", resultados=[False, False, True]), Falsa("otra")
        agente, modelo = montaje(a, b)
        _turno(agente, modelo, [("crear", {"x": "1"}), ("crear", {"x": "2"})])
        r = _turno(agente, modelo, [("crear", {"x": "3"})])
        assert r[-1]["success"]


class TestQueCuentaComoFallo:
    def test_la_verificacion_que_dice_que_no(self, montaje, tmp_path):
        """`create_file` dice que sí, pero el archivo no está: cuenta como no salido."""
        crear = Falsa("create_file")
        otra = Falsa("otra", resultados=[False])
        tercera = Falsa("tercera")
        agente, modelo = montaje(crear, otra, tercera)
        r = _turno(agente, modelo, [("create_file", {"path": str(tmp_path / "no.txt"), "content": "x"}),
                                    ("otra", {}), ("tercera", {})])
        assert r[0]["success"] is False and UN_INTENTO in r[0]["error"]
        assert r[2]["error"] == SIN_MAS_INTENTOS

    def test_una_consulta_que_falla_no_cuenta(self, montaje):
        mirar = Falsa("mirar", riesgo="safe", resultados=[False])
        a, b = Falsa("crear", resultados=[False]), Falsa("otra")
        agente, modelo = montaje(mirar, a, b)
        r = _turno(agente, modelo, [("mirar", {}), ("crear", {}), ("otra", {})])
        assert UN_INTENTO not in (r[0]["error"] or "")
        assert UN_INTENTO in r[1]["error"] and r[2]["success"], "el primer cambio aún tiene su intento"


class TestEnUnPlanAprobado:
    def test_un_paso_que_falla_da_su_intento(self, tmp_path, monkeypatch):
        from src.canal.herramientas import EscribirEnElEquipo
        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tasks.planificador import Planificador
        from src.tools.planificacion import plan_tools

        class DelPc(EscribirEnElEquipo):
            def disponible(self):
                return True

            def execute(self, equipo=None, **kw):
                return {"success": False, "data": None, "error": "el PC dijo que no"}

        registro = ToolRegistry()
        registro.register(DelPc("create_file"))
        otra = Falsa("otra_forma")
        registro.register(otra)
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=permisos, planes=planificador)
        plan = planificador.crear("notas", [{"descripcion": "crear", "herramienta": "create_file",
                                              "argumentos": {"path": "C:/n.txt", "content": "x"}}], session_id="s")
        planificador.aprobar(plan.id)
        modelo.queue_tool_call("otra_forma", {"x": "1"})
        modelo.queue_text("No pude crearlo; probé otra forma.")
        agente.chat("✅ Plan aprobado: notas", session_id="s", ejecutar_plan=plan.id)
        usuario = [m for m in agente.sessions.get("s").messages if m.role == "user"][-1]
        assert "tienes UN intento con otro camino" in usuario.content
        assert len(otra.llamadas) == 1
