"""
La anotación automática de pasos y las tareas huérfanas (V1.5).

Ambas cosas salieron del Stable Gate de la V1.5, ejecutando un encargo real:

1. Hacer que el modelo llamara a `advance_task` tras cada herramienta duplicaba
   los viajes de ida y vuelta al proveedor. El agente ya sabe qué ejecutó y cómo
   fue: preguntárselo al modelo era pagar por un dato que ya estaba en la mano.
2. Una tarea quedaba en `running` **para siempre** si el turno que la ejecutaba
   moría por el límite de tiempo. La interfaz la mostraría girando sin fin.
"""

import time

import pytest

from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.tasks import TaskManager, TaskState


@pytest.fixture
def gestor(tmp_path):
    return TaskManager(SQLiteRepositoryFactory(Database(tmp_path / "tareas.db")).tasks)


@pytest.fixture
def agente(gestor):
    from src.agent.core import Agent
    from src.agent.sessions import SessionStore
    from src.models.mock import MockLLMProvider
    from src.security.permissions import PermissionManager
    from src.tools.registry import ToolRegistry
    from src.tools.tareas import task_tools

    registro = ToolRegistry()
    for herramienta in task_tools(gestor):
        registro.register(herramienta)

    return Agent(
        model=MockLLMProvider(),
        tool_registry=registro,
        permission_manager=PermissionManager(interactive=False),
        sessions=SessionStore(),
        tasks=gestor,
    )


def _crear_atada(agente, gestor, sesion, pasos=None):
    creada = agente.tools.get("create_task").execute(objetivo="Investigar", pasos=pasos or [])
    agente._anotar_paso(sesion, "create_task", {}, creada)
    return gestor.obtener(creada["data"]["id"])


class TestElAgenteAnotaLosPasosSolo:
    def test_la_tarea_queda_atada_a_la_conversacion(self, agente, gestor):
        """Sin ese vínculo, los pasos no encuentran a qué tarea pertenecen y la
        tarea se queda con sus pasos en blanco. Pasó en el gate."""
        sesion = agente.sessions.get("s1")

        tarea = _crear_atada(agente, gestor, sesion)

        assert tarea.session_id == "s1"

    def test_cada_herramienta_ejecutada_queda_anotada(self, agente, gestor):
        sesion = agente.sessions.get("s1")
        tarea = _crear_atada(agente, gestor, sesion)

        agente._anotar_paso(sesion, "search_web", {"query": "x"}, {"success": True, "data": "ok"})

        actualizada = gestor.obtener(tarea.id)
        assert len(actualizada.pasos) == 1
        assert actualizada.pasos[0].herramienta == "search_web"
        assert actualizada.pasos[0].estado == TaskState.COMPLETED.value

    def test_un_fallo_de_herramienta_se_anota_como_tal(self, agente, gestor):
        sesion = agente.sessions.get("s1")
        tarea = _crear_atada(agente, gestor, sesion)

        agente._anotar_paso(sesion, "read_webpage", {"url": "x"}, {"success": False, "error": "404"})

        paso = gestor.obtener(tarea.id).pasos[0]
        assert paso.estado == TaskState.FAILED.value
        assert paso.error == "404"

    def test_se_conserva_la_descripcion_del_plan(self, agente, gestor):
        """El plan es lo legible; la herramienta va en su propio campo y la
        interfaz la muestra aparte."""
        sesion = agente.sessions.get("s1")
        tarea = _crear_atada(agente, gestor, sesion, pasos=["Buscar en la web", "Resumir"])

        agente._anotar_paso(sesion, "search_web", {"query": "x"}, {"success": True, "data": "ok"})

        paso = gestor.obtener(tarea.id).pasos[0]
        assert paso.descripcion == "Buscar en la web"
        assert paso.herramienta == "search_web"

    def test_sin_plan_el_paso_se_describe_con_la_llamada(self, agente, gestor):
        sesion = agente.sessions.get("s1")
        tarea = _crear_atada(agente, gestor, sesion)

        agente._anotar_paso(sesion, "search_web", {"query": "supabase"}, {"success": True})

        assert "search_web" in gestor.obtener(tarea.id).pasos[0].descripcion

    def test_las_herramientas_de_tareas_no_se_anotan_a_si_mismas(self, agente, gestor):
        """Llenarían la tarea de ruido sobre su propia gestión."""
        sesion = agente.sessions.get("s1")
        tarea = _crear_atada(agente, gestor, sesion)

        agente._anotar_paso(sesion, "list_tasks", {}, {"success": True, "data": {}})

        assert gestor.obtener(tarea.id).pasos == []

    def test_sin_tarea_activa_no_pasa_nada(self, agente, gestor):
        agente._anotar_paso(agente.sessions.get("sin-tarea"), "search_web", {}, {"success": True})

        assert gestor.listar() == []

    def test_solo_se_anota_en_la_tarea_de_esa_conversacion(self, agente, gestor):
        """Dos conversaciones a la vez no deben mezclar sus pasos."""
        primera = _crear_atada(agente, gestor, agente.sessions.get("s1"))
        segunda = _crear_atada(agente, gestor, agente.sessions.get("s2"))

        agente._anotar_paso(agente.sessions.get("s1"), "search_web", {}, {"success": True})

        assert len(gestor.obtener(primera.id).pasos) == 1
        assert gestor.obtener(segunda.id).pasos == []

    def test_anotar_nunca_tumba_el_turno(self, agente, gestor, monkeypatch):
        """Llevar la cuenta es accesorio: no puede cargarse el trabajo de verdad."""
        sesion = agente.sessions.get("s1")
        _crear_atada(agente, gestor, sesion)

        def romper(*a, **k):
            raise RuntimeError("la base no responde")

        monkeypatch.setattr(gestor, "avanzar", romper)

        agente._anotar_paso(sesion, "search_web", {}, {"success": True})  # no debe lanzar

    def test_sin_gestor_el_agente_funciona_igual(self, gestor):
        """El sistema de tareas es opcional: sin él, Morgan no lleva cuenta pero
        sigue trabajando."""
        from src.agent.core import Agent
        from src.agent.sessions import SessionStore
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        agente = Agent(
            model=MockLLMProvider(),
            tool_registry=ToolRegistry(),
            permission_manager=PermissionManager(interactive=False),
            sessions=SessionStore(),
        )

        agente._anotar_paso(agente.sessions.get("s1"), "search_web", {}, {"success": True})


class TestLasTareasHuerfanas:
    def test_una_tarea_sin_avanzar_se_cierra_sola(self, gestor):
        tarea = gestor.crear("Se quedó a medias")
        gestor.iniciar(tarea.id)

        vieja = gestor.obtener(tarea.id)
        vieja.actualizado_en = time.time() - 3600
        gestor.repositorio.update(vieja)

        gestor.listar()

        cerrada = gestor.obtener(tarea.id)
        assert cerrada.estado == TaskState.FAILED.value
        assert cerrada.error and "minutos sin" in cerrada.error

    def test_una_tarea_reciente_no_se_toca(self, gestor):
        tarea = gestor.crear("Recién empezada")
        gestor.iniciar(tarea.id)

        gestor.listar()

        assert gestor.obtener(tarea.id).estado == TaskState.RUNNING.value

    def test_una_terminada_no_se_reabre_ni_se_reescribe(self, gestor):
        tarea = gestor.crear("Terminada hace mucho")
        gestor.completar(tarea.id, "salió bien")

        vieja = gestor.obtener(tarea.id)
        vieja.actualizado_en = time.time() - 3600
        gestor.repositorio.update(vieja)

        gestor.listar()

        final = gestor.obtener(tarea.id)
        assert final.estado == TaskState.COMPLETED.value
        assert final.resultado == "salió bien"
