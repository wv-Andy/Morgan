"""
Escenarios completos de la V1.3 (§17): online, offline, degradado y recuperación.

Estas pruebas no comprueban una función aislada, sino que **Morgan en conjunto**
se comporta como debe en cada situación. Todos los fallos se simulan, así que son
reproducibles y no dependen de que haya red.

El criterio que verifican es el §30: *continuidad de funcionamiento*. La caída de
un servicio externo no puede destruir lo que no dependía de él.
"""

import pytest
from fastapi.testclient import TestClient

from src.agent.core import Agent
from src.agent.sessions import SessionStore
from src.health import ServiceHealth, ServiceState
from src.memory.db import SCHEMA_VERSION, Database, MemoryStorageError
from src.memory.models import Message
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.memory.sync import SyncOperation, SyncQueue, SyncWorker
from src.models.base import LLMProvider, LLMResponse
from src.models.mock import MockLLMProvider
from src.tools.registry import ToolRegistry


class SinRed(LLMProvider):
    """Proveedor inalcanzable."""

    @property
    def model_name(self) -> str:
        return "sin-red"

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        raise ConnectionError("getaddrinfo failed")


@pytest.fixture
def repos(tmp_path):
    return SQLiteRepositoryFactory(Database(tmp_path / "escenarios.db"))


def _agente(modelo, permisos, historial=None):
    return Agent(
        model=modelo,
        tool_registry=ToolRegistry(),
        permission_manager=permisos,
        sessions=SessionStore(),
        conversation_history=historial,
    )


# --- ONLINE --------------------------------------------------------------------


class TestEscenarioOnline:
    def test_todo_disponible(self, repos):
        health = ServiceHealth()
        health.register("internet", lambda: (ServiceState.AVAILABLE, None))
        health.register("database.local", lambda: (ServiceState.AVAILABLE, None))
        health.register("llm", lambda: (ServiceState.AVAILABLE, None))
        health.check_all()

        assert health.overall() == ServiceState.AVAILABLE

    def test_la_conversacion_se_guarda_y_se_encola(self, repos, non_interactive_permissions):
        from src.agent.history import ConversationHistory

        cola = SyncQueue(repos.db)
        modelo = MockLLMProvider()
        modelo.queue_text("hola")
        agente = _agente(modelo, non_interactive_permissions,
                         ConversationHistory(repos, sync_queue=cola))

        agente.process("buenas", session_id="s1")

        assert repos.messages.count("s1") == 2
        assert cola.count() == 3


# --- OFFLINE -------------------------------------------------------------------


class TestEscenarioOffline:
    def test_sin_internet_la_base_local_sigue_funcionando(self, repos):
        """§5: Internet OFF → Morgan Local → SQLite → herramientas locales."""
        health = ServiceHealth()
        health.register("internet", lambda: (ServiceState.UNAVAILABLE, "sin red"))
        health.register("database.local", lambda: (ServiceState.AVAILABLE, None))
        health.check_all()

        assert health.get("database.local").is_usable is True
        assert health.overall() == ServiceState.DEGRADED

        repos.memories.upsert("nota", "sigo guardando sin conexión")
        assert repos.memories.get("nota").value == "sigo guardando sin conexión"

    def test_las_herramientas_locales_no_tocan_la_red(self):
        """§17: filesystem, terminal y procesos no deben depender de conectividad."""
        from src.tools.filesystem import ListFilesTool, ReadFileTool
        from src.tools.system import SystemInfoTool
        from src.tools.terminal import GetProcessesTool

        for herramienta in (ListFilesTool(), ReadFileTool(), SystemInfoTool(), GetProcessesTool()):
            modulo = type(herramienta).__module__
            fuente = __import__(modulo, fromlist=["x"]).__dict__
            # Ninguno de esos módulos importa un cliente HTTP.
            assert "httpx" not in fuente
            assert "urlopen" not in fuente

    def test_las_herramientas_de_red_dan_error_controlado(self):
        """§18: informar, no bloquear ni reventar."""
        from unittest.mock import patch

        from src.tools.web import SearchWebTool

        with patch("src.tools.web.urllib.request.urlopen", side_effect=OSError("sin red")):
            resultado = SearchWebTool().execute(query="lo que sea")

        assert resultado["success"] is False
        assert resultado["error"]
        assert set(resultado.keys()) == {"success", "data", "error"}

    def test_sin_llm_morgan_sigue_vivo(self, non_interactive_permissions):
        agente = _agente(SinRed(), non_interactive_permissions)

        respuesta = agente.process("hola")

        assert isinstance(respuesta, str)
        assert respuesta.strip()
        assert "modelo" in respuesta.lower()

    def test_la_nube_caida_no_pierde_datos(self, repos):
        """El dato local ya está guardado; la operación espera su turno."""
        cola = SyncQueue(repos.db)
        cola.enqueue(SyncOperation.UPSERT_MEMORY, {"key": "k", "value": "v"})

        class NubeCaida:
            def health(self):
                return False, "sin conexión"

        resumen = SyncWorker(cola, NubeCaida()).run_once()

        assert resumen["completadas"] == 0
        assert cola.count() == 1


# --- DEGRADADO -----------------------------------------------------------------


class TestEscenarioDegradado:
    def test_internet_disponible_pero_llm_caido(self, repos):
        """§6: online no significa que todo funcione."""
        health = ServiceHealth()
        health.register("internet", lambda: (ServiceState.AVAILABLE, None))
        health.register("database.local", lambda: (ServiceState.AVAILABLE, None))
        health.register("llm", lambda: (ServiceState.UNAVAILABLE, "proveedor caído"))
        health.check_all()

        assert health.overall() == ServiceState.DEGRADED
        assert health.is_usable("database.local") is True
        assert health.is_usable("llm") is False

    def test_el_principal_cae_y_responde_el_respaldo(self):
        from src.models.fallback import FallbackProvider

        respaldo = MockLLMProvider()
        respaldo.queue_text("respondo yo")
        proveedor = FallbackProvider(primary=SinRed(), fallback=respaldo)

        assert proveedor.generate([]).content == "respondo yo"
        assert proveedor.fallback_uses == 1

    def test_una_base_lenta_se_marca_degradada_no_caida(self):
        from src.api import health_checks

        class ReposLentos:
            def health(self):
                import time
                time.sleep(health_checks.SLOW_THRESHOLD_SECONDS + 0.05)
                return True, None

        estado, detalle = health_checks.make_local_database_check(ReposLentos())()

        assert estado == ServiceState.DEGRADED
        assert "lenta" in detalle


# --- RECUPERACIÓN --------------------------------------------------------------


class TestEscenarioRecuperacion:
    def test_el_servicio_vuelve_y_morgan_recupera_capacidad(self):
        estados = [
            (ServiceState.UNAVAILABLE, "caído"),
            (ServiceState.UNAVAILABLE, "caído"),
            (ServiceState.AVAILABLE, "recuperado"),
        ]
        health = ServiceHealth(ttl=0)
        health.register("x", lambda: estados.pop(0))

        health.check("x", force=True)
        health.check("x", force=True)
        assert health.is_usable("x") is False

        health.check("x", force=True)

        assert health.is_usable("x") is True
        assert health.get("x").consecutive_failures == 0

    def test_lo_pendiente_se_sube_al_volver_la_nube(self, repos):
        cola = SyncQueue(repos.db)
        cola.enqueue(SyncOperation.UPSERT_MEMORY, {"key": "k", "value": "v"})

        class Nube:
            def __init__(self):
                self.viva = False
                self.guardados = {}
                nube = self

                class _M:
                    def upsert(self, key, value, category="general"):
                        nube.guardados[key] = value

                self.memories = _M()

            def health(self):
                return (True, None) if self.viva else (False, "caída")

        nube = Nube()
        trabajador = SyncWorker(cola, nube)

        trabajador.run_once()
        assert cola.count() == 1

        nube.viva = True
        resumen = trabajador.run_once()

        assert resumen["completadas"] == 1
        assert cola.count() == 0
        assert nube.guardados["k"] == "v"


# --- TIMEOUT -------------------------------------------------------------------


class TestEscenarioTimeout:
    def test_ninguna_dependencia_puede_bloquear_indefinidamente(self, non_interactive_permissions):
        """§17: verificar que existe un tope real de tiempo."""
        import time

        from src.models.base import ToolCallRequest

        class Lento(LLMProvider):
            @property
            def model_name(self):
                return "lento"

            def generate(self, messages, tools=None, system_prompt=None):
                time.sleep(0.25)
                return LLMResponse(
                    type="tool_call",
                    tool_calls=[ToolCallRequest(id="c", name="inexistente", arguments={})],
                )

        agente = _agente(Lento(), non_interactive_permissions)
        agente.turn_timeout = 1

        inicio = time.monotonic()
        respuesta = agente.process("algo largo")

        assert time.monotonic() - inicio < 3
        assert "tiempo máximo" in respuesta

    def test_los_proveedores_llevan_timeout_configurado(self):
        from src.config import get_settings

        s = get_settings()

        assert 5 <= s.llm_timeout <= 300
        assert s.turn_timeout >= 10
        assert s.llm_max_retries <= 5


# --- SIN REGRESIONES -----------------------------------------------------------


class TestSinRegresionesDeV12:
    def test_status_sigue_respondiendo(self):
        from src.api.app import app

        respuesta = TestClient(app).get("/status")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        # Campos de la V1.2 que deben seguir presentes.
        assert cuerpo["tools_count"] >= 5
        assert "components" in cuerpo
        # Campos nuevos de la V1.3.
        assert "services" in cuerpo
        assert "sync" in cuerpo

    def test_las_sesiones_de_v12_siguen_funcionando(self, repos):
        repos.sessions.create("s1", title="V1.2")
        repos.messages.add(Message(session_id="s1", role="user", content="hola"))

        assert repos.sessions.get("s1").message_count == 1

    def test_la_memoria_de_v12_sigue_funcionando(self, repos):
        repos.memories.upsert("clave", "valor", "categoria")

        assert repos.memories.get("clave").value == "valor"

    def test_una_base_sin_la_tabla_de_cola_se_migra_sola(self, tmp_path):
        """Una instalación de V1.2 debe poder actualizarse sin intervención."""
        import sqlite3

        ruta = tmp_path / "vieja.db"
        # Base al estilo V1.2: sin sync_queue.
        with sqlite3.connect(ruta) as conn:
            conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)")
            conn.execute("INSERT INTO schema_version (version) VALUES (3)")
            conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, category TEXT, key TEXT UNIQUE, value TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)")
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, metadata TEXT DEFAULT '{}')")
            conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_name TEXT, tool_call_id TEXT, created_at TIMESTAMP, metadata TEXT DEFAULT '{}')")

        base = Database(ruta)

        # Atada a SCHEMA_VERSION en vez de a un numero: la intencion es "se pone al
        # dia sola", no "llega justo a la version 4". Con un literal, cada migracion
        # futura rompia esta prueba sin que nada estuviera mal.
        assert base.version() == SCHEMA_VERSION
        with base.connect() as conn:
            tablas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columnas = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}

        assert "sync_queue" in tablas
        # Y las columnas de organizacion del historial, anadidas despues.
        assert {"archived", "pinned", "group_name"} <= columnas
