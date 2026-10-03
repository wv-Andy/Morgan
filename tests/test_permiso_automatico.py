"""
El permiso automático (4.6, pedido por mí el 2026-09-30).

*«Darme permiso automático de ejecución al hacer la mayoría de acciones en verde y
amarillo, pero en las rojas preguntar.»* Con él encendido, un plan verde o amarillo se
aprueba y se ejecuta al crearlo, en el mismo turno; uno con algo rojo (borrar) espera al
botón como siempre. Y lo que haría daño si fallase: que lo rojo se colara, que el permiso
se pudiera conceder desde la conversación (la memoria) o con un token de API.
"""

import pytest
from fastapi.testclient import TestClient

from src.agent.core import Agent
from src.identidad.permiso_automatico import entra
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tasks.planificador import Planificador
from src.tools.planificacion import plan_tools
from src.tools.registry import ToolRegistry
from tests.test_ejecutar_plan import RUTA, DelPc


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
    agente.permiso_automatico = lambda: True
    return agente, modelo, planificador


CREAR = {"descripcion": "crear las notas", "herramienta": "create_file",
         "argumentos": {"path": RUTA, "content": "pan"}}
BORRAR = {"descripcion": "borrar las viejas", "herramienta": "delete_file",
          "argumentos": {"path": "C:/Users/ana/viejas.txt"}}


def _propone(modelo, pasos):
    modelo.queue_tool_call("create_plan", {"objetivo": "ordenar mis notas", "pasos": pasos})


class TestLosColores:
    @pytest.mark.parametrize("nivel", ["safe", "low_risk", "moderate", "MODERATE"])
    def test_verde_y_amarillo_entran(self, nivel):
        assert entra(nivel)

    @pytest.mark.parametrize("nivel", ["high_risk", "sensitive", "critical", "", None, "raro"])
    def test_rojo_y_lo_desconocido_no(self, nivel):
        assert not entra(nivel)


class TestEnElTurno:
    def test_un_plan_amarillo_se_ejecuta_al_crearlo(self, montaje):
        agente, modelo, planificador = montaje
        _propone(modelo, [CREAR])
        respuesta = agente.chat("crea mis notas", session_id="s1")

        assert DelPc.hechas == [("create_file", {"path": RUTA, "content": "pan"})]
        assert respuesta == "Listo: crear las notas (comprobado)."
        assert modelo.call_count == 1, "el resumen lo escribe el núcleo"
        plan = planificador.listar(session_id="s1")[0]
        assert plan.estado == "completado"
        assert plan.decidido_por.endswith("(automático)")
        assert DelPc.origenes == [f"{plan.id}#1"], "el paso sale del plan, no de «auto»"

    def test_un_plan_con_algo_rojo_espera_al_boton(self, montaje):
        agente, modelo, planificador = montaje
        _propone(modelo, [CREAR, BORRAR])
        respuesta = agente.chat("ordena mis notas", session_id="s1")

        assert DelPc.hechas == [], "no se hace ni lo amarillo de un plan con algo rojo"
        assert respuesta.startswith("Te propongo este plan")
        assert planificador.listar(session_id="s1")[0].estado == "pendiente"

    def test_sin_el_permiso_todo_espera(self, montaje):
        agente, modelo, planificador = montaje
        agente.permiso_automatico = lambda: False
        _propone(modelo, [CREAR])
        assert agente.chat("crea mis notas", session_id="s1").startswith("Te propongo este plan")
        assert DelPc.hechas == []

    def test_si_un_paso_falla_lo_cuenta_el_modelo(self, montaje):
        agente, modelo, _ = montaje
        DelPc.fallan = {"create_file"}
        _propone(modelo, [CREAR])
        modelo.queue_text("No pude crear tus notas: el PC dijo que no.")
        respuesta = agente.chat("crea mis notas", session_id="s1")
        assert respuesta == "No pude crear tus notas: el PC dijo que no."
        assert "NO salió" in modelo._calls[-1][-1].content

    def test_una_llamada_amarilla_sin_plan_se_hace(self, montaje):
        agente, modelo, _ = montaje
        modelo.queue_tool_call("create_file", {"path": RUTA, "content": "pan"})
        modelo.queue_text("Hecho.")
        agente.chat("crea mis notas", session_id="s1")
        assert DelPc.hechas == [("create_file", {"path": RUTA, "content": "pan"})]
        assert DelPc.origenes[0].startswith("auto:"), "el PC distingue un turno de otro"

    def test_una_llamada_roja_sin_plan_no(self, montaje):
        agente, modelo, _ = montaje
        modelo.queue_tool_call("delete_file", {"path": RUTA})
        modelo.queue_text("Necesito tu aprobación.")
        agente.chat("borra mis notas", session_id="s1")
        assert DelPc.hechas == []

    def test_un_fallo_al_mirarlo_es_que_no(self, montaje):
        agente, modelo, _ = montaje

        def roto():
            raise RuntimeError("la base no contesta")

        agente.permiso_automatico = roto
        _propone(modelo, [CREAR])
        assert agente.chat("crea mis notas", session_id="s1").startswith("Te propongo este plan")


@pytest.fixture
def web(monkeypatch, modelo_simulado):
    from src.api import dependencies
    from src.api.app import create_app
    from src.api.sesion_web import CABECERA_CSRF
    from src.config import reset_settings

    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    reset_settings()
    dependencies.reset_container()
    app = create_app()
    cliente = TestClient(app)
    r = cliente.post("/auth/registro", json={"username": "ana", "email": "ana@ejemplo.co",
                                             "password": "contrasena-larga"})
    assert r.status_code == 200, r.text
    cliente.headers[CABECERA_CSRF] = r.json()["csrf"]
    yield cliente, r.json()["usuario"]["id"], dependencies.get_container()
    dependencies.reset_container()
    reset_settings()


class TestEnAjustes:
    def test_nace_apagado_y_se_enciende(self, web):
        cliente, user_id, container = web
        assert cliente.get("/auth/permiso-automatico").json()["encendido"] is False
        assert cliente.put("/auth/permiso-automatico", json={"encendido": True}).json()["encendido"] is True
        assert cliente.get("/auth/permiso-automatico").json()["encendido"] is True

        from src.identidad import como_usuario

        with como_usuario(user_id):
            assert container.agent.permiso_automatico() is True
        cliente.put("/auth/permiso-automatico", json={"encendido": False})
        with como_usuario(user_id):
            assert container.agent.permiso_automatico() is False

    def test_no_se_concede_desde_la_memoria(self, web):
        """Los ajustes de perfil son recuerdos, y el modelo escribe recuerdos."""
        cliente, user_id, container = web
        from src.identidad import como_usuario

        with como_usuario(user_id):
            container.memory_manager.remember(key="permiso_automatico", value="true", category="preferencias")
            assert container.agent.permiso_automatico() is False

    def test_con_un_token_de_api_no_vale(self, web, monkeypatch):
        cliente, user_id, container = web
        cliente.put("/auth/permiso-automatico", json={"encendido": True})
        from src.identidad import como_usuario
        from src.identidad import tokens

        monkeypatch.setattr(tokens, "token_actual", lambda: "tok-1")
        with como_usuario(user_id):
            assert container.agent.permiso_automatico() is False

    def test_un_token_no_puede_cambiarlo(self):
        from src.identidad.tokens import alcance_necesario

        assert alcance_necesario("PUT", "/auth/permiso-automatico") is None
        assert alcance_necesario("GET", "/auth/permiso-automatico") is None

    def test_queda_en_la_auditoria(self, web, monkeypatch):
        cliente, user_id, container = web
        eventos = []
        monkeypatch.setattr(container.audit_logger, "registrar_evento",
                            lambda accion, **k: eventos.append((accion, k.get("detalle"))))
        cliente.put("/auth/permiso-automatico", json={"encendido": True})
        assert eventos == [("permiso_automatico", "encendido")]


class TestLoVerdeNoEspera:
    """4.8, medido con el modelo real: para «abre VS Code» planeaba un paso verde, el plan
    quedaba «aprobado» (no necesita aprobación) sin que nadie lo ejecutara, y el modelo
    tenía que volver a llamar a la herramienta."""

    def _registro(self, tmp_path):
        from src.tools.base import Tool

        hechas = []

        class Verde(Tool):
            def __init__(self, nombre):
                self._n = nombre

            name = property(lambda self: self._n)
            description = "verde"
            category = "system"
            permission_level = "safe"
            parameters = {"type": "object", "properties": {"x": {"type": "string"}}}

            def execute(self, **kwargs):
                hechas.append(self._n)
                return {"success": True, "data": {"hecho": True}, "error": None}

        registro = ToolRegistry()
        registro.register(Verde("abrir_algo"))
        registro.register(Verde("system_info"))          # una consulta (CONSULTAS)
        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "v.db"))
        planificador = Planificador(fabrica.planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro,
                       permission_manager=PermissionManager(interactive=False), planes=planificador)
        return agente, modelo, hechas

    def test_un_plan_verde_de_accion_se_hace_sin_permiso_automatico(self, tmp_path):
        agente, modelo, hechas = self._registro(tmp_path)
        modelo.queue_tool_call("create_plan", {"objetivo": "abrir", "pasos": [
            {"descripcion": "abrir la app", "herramienta": "abrir_algo", "argumentos": {"x": "1"}}]})
        respuesta = agente.chat("abre la app", session_id="s")
        assert hechas == ["abrir_algo"] and respuesta.startswith("Listo") and modelo.call_count == 1

    def test_pero_uno_que_consulta_lo_cuenta_el_modelo(self, tmp_path):
        agente, modelo, hechas = self._registro(tmp_path)
        modelo.queue_tool_call("create_plan", {"objetivo": "mirar", "pasos": [
            {"descripcion": "ver el sistema", "herramienta": "system_info", "argumentos": {}}]})
        modelo.queue_text("Tienes 16 GB de RAM.")
        respuesta = agente.chat("¿cuánta RAM tengo?", session_id="s")
        assert respuesta == "Tienes 16 GB de RAM." and not respuesta.startswith("Listo")
