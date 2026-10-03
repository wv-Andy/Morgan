"""
Pruebas de la cola de sincronización y del proveedor remoto (V1.3, fases 5 y 6).

**No tocan la red.** El proveedor remoto se simula, de modo que los escenarios
—nube caída, timeout, HTTP 500, recuperación— son reproducibles y no dependen de
que Supabase esté disponible ni consumen cuota.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.memory.db import Database, MemoryStorageError, SCHEMA_VERSION
from src.memory.models import MemoryRecord, Message, Session
from src.memory.sync import (
    MAX_ATTEMPTS,
    MAX_QUEUE_SIZE,
    SyncOperation,
    SyncQueue,
    SyncWorker,
)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "sync.db")


@pytest.fixture
def queue(db):
    return SyncQueue(db)


class RemotoFalso:
    """Proveedor remoto en memoria, con fallos programables."""

    def __init__(self, disponible: bool = True, error: Exception | None = None):
        self.disponible = disponible
        self.error = error
        self.sesiones: dict[str, Session] = {}
        self.mensajes: list[Message] = []
        self.recuerdos: dict[str, MemoryRecord] = {}
        self.llamadas = 0

        remoto = self

        class _Sesiones:
            def create(self, session_id, title=None, metadata=None):
                remoto._quizas_fallar()
                remoto.sesiones[session_id] = Session(id=session_id, title=title)
                return remoto.sesiones[session_id]

            def touch(self, session_id, title=None):
                remoto._quizas_fallar()
                if session_id in remoto.sesiones and title:
                    remoto.sesiones[session_id].title = title

            def delete(self, session_id):
                remoto._quizas_fallar()
                return remoto.sesiones.pop(session_id, None) is not None

        class _Mensajes:
            def add(self, message):
                remoto._quizas_fallar()
                remoto.mensajes.append(message)
                return message

        class _Recuerdos:
            def upsert(self, key, value, category="general"):
                remoto._quizas_fallar()
                remoto.recuerdos[key] = MemoryRecord(key=key, value=value, category=category)
                return remoto.recuerdos[key]

            def delete(self, key):
                remoto._quizas_fallar()
                return remoto.recuerdos.pop(key, None) is not None

        self.sessions = _Sesiones()
        self.messages = _Mensajes()
        self.memories = _Recuerdos()

    def _quizas_fallar(self):
        self.llamadas += 1
        if self.error:
            raise self.error

    def health(self):
        return (True, None) if self.disponible else (False, "nube no disponible")


# --- Esquema -------------------------------------------------------------------


class TestEsquemaDeLaCola:
    def test_la_migracion_crea_la_tabla(self, db):
        assert db.version() == SCHEMA_VERSION
        with db.connect() as conn:
            tablas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "sync_queue" in tablas


# --- Cola ----------------------------------------------------------------------


class TestColaDeSincronizacion:
    def test_encolar_y_contar(self, queue):
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        queue.enqueue(SyncOperation.INSERT_MESSAGE, {"session_id": "s1", "role": "user"})

        assert queue.count() == 2

    def test_se_conserva_el_orden_de_llegada(self, queue):
        for i in range(4):
            queue.enqueue(SyncOperation.INSERT_MESSAGE, {"n": i})

        assert [e.payload["n"] for e in queue.pending()] == [0, 1, 2, 3]

    def test_el_payload_va_y_vuelve(self, queue):
        datos = {"id": "s1", "title": "Título con acentos áéí", "metadata": {"a": 1}}
        queue.enqueue(SyncOperation.UPSERT_SESSION, datos)

        assert queue.pending()[0].payload == datos

    def test_marcar_completada_la_retira(self, queue):
        entrada_id = queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        queue.mark_done(entrada_id)

        assert queue.count() == 0

    def test_marcar_fallida_incrementa_los_intentos(self, queue):
        entrada_id = queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        queue.mark_failed(entrada_id, "error de red")

        entrada = queue.pending()[0]
        assert entrada.attempts == 1
        assert "error de red" in entrada.last_error

    def test_tras_agotar_intentos_deja_de_reintentarse(self, queue):
        """Si algo falla siempre, es un dato problemático, no un corte de red."""
        entrada_id = queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        for _ in range(MAX_ATTEMPTS):
            queue.mark_failed(entrada_id, "sigue fallando")

        assert queue.pending() == []
        # Pero no se borra: queda como evidencia.
        assert len(queue.failed()) == 1

    def test_la_cola_esta_acotada(self, queue, monkeypatch):
        """Sin límite, estar mucho tiempo sin conexión llenaría el disco."""
        monkeypatch.setattr("src.memory.sync.MAX_QUEUE_SIZE", 10)

        for i in range(15):
            queue.enqueue(SyncOperation.INSERT_MESSAGE, {"n": i})

        assert queue.count() <= 10
        # Se descartan las más antiguas, no las recientes.
        assert queue.pending()[-1].payload["n"] == 14

    def test_limpiar(self, queue):
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})

        assert queue.clear() == 1
        assert queue.count() == 0


# --- Trabajador ----------------------------------------------------------------


class TestTrabajadorDeSincronizacion:
    def test_sube_las_operaciones_pendientes(self, queue):
        remoto = RemotoFalso()
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1", "title": "Charla"})
        queue.enqueue(SyncOperation.INSERT_MESSAGE, {"session_id": "s1", "role": "user", "content": "hola"})
        queue.enqueue(SyncOperation.UPSERT_MEMORY, {"key": "nombre", "value": "Andy"})

        resumen = SyncWorker(queue, remoto).run_once()

        assert resumen["completadas"] == 3
        assert queue.count() == 0
        assert "s1" in remoto.sesiones
        assert len(remoto.mensajes) == 1
        assert remoto.recuerdos["nombre"].value == "Andy"

    def test_con_la_nube_caida_no_se_pierde_nada(self, queue):
        """§5: sin conexión, el dato local ya está guardado y espera."""
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        remoto = RemotoFalso(disponible=False)

        resumen = SyncWorker(queue, remoto).run_once()

        assert resumen["completadas"] == 0
        assert resumen["omitidas"] == 1
        assert queue.count() == 1
        # No se gasta un intento por cada entrada si el remoto entero no responde.
        assert queue.pending()[0].attempts == 0

    def test_sin_remoto_configurado_no_hace_nada(self, queue):
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})

        resumen = SyncWorker(queue, None).run_once()

        assert resumen["omitidas"] == 1
        assert queue.count() == 1

    @pytest.mark.parametrize("error", [
        MemoryStorageError("No se pudo contactar con Supabase: timeout"),
        MemoryStorageError("Supabase respondió 500: internal error"),
    ])
    def test_un_fallo_por_operacion_se_reintenta_despues(self, queue, error):
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        remoto = RemotoFalso(error=error)

        resumen = SyncWorker(queue, remoto).run_once()

        assert resumen["fallidas"] == 1
        assert queue.pending()[0].attempts == 1

    def test_recuperacion_tras_una_caida(self, queue):
        """§17 Recovery: vuelve el servicio y lo pendiente se sube."""
        queue.enqueue(SyncOperation.UPSERT_SESSION, {"id": "s1"})
        remoto = RemotoFalso(disponible=False)
        trabajador = SyncWorker(queue, remoto)

        trabajador.run_once()
        assert queue.count() == 1

        remoto.disponible = True
        resumen = trabajador.run_once()

        assert resumen["completadas"] == 1
        assert queue.count() == 0

    def test_una_operacion_desconocida_no_bloquea_la_cola(self, queue):
        with queue.db.connect() as conn:
            conn.execute(
                "INSERT INTO sync_queue (operation, payload, created_at) VALUES (?, ?, ?)",
                ("operacion_inventada", "{}", "2026-01-01 00:00:00"),
            )

        resumen = SyncWorker(queue, RemotoFalso()).run_once()

        assert resumen["fallidas"] == 1
        assert queue.pending()[0].attempts == 1


# --- Integración con el historial ---------------------------------------------


class TestHistorialEncolaParaLaNube:
    def test_un_turno_persistido_deja_operaciones_en_la_cola(self, db, non_interactive_permissions):
        from src.agent.core import Agent
        from src.agent.history import ConversationHistory
        from src.agent.sessions import SessionStore
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.models.mock import MockLLMProvider
        from src.tools.registry import ToolRegistry

        repos = SQLiteRepositoryFactory(db)
        cola = SyncQueue(db)
        modelo = MockLLMProvider()
        modelo.queue_text("respuesta")

        agente = Agent(
            model=modelo,
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
            conversation_history=ConversationHistory(repos, sync_queue=cola),
        )
        agente.process("hola", session_id="s1")

        # La sesión, más los dos mensajes del turno.
        assert cola.count() == 3
        assert repos.messages.count("s1") == 2

    def test_sin_cola_el_historial_funciona_igual(self, db, non_interactive_permissions):
        from src.agent.core import Agent
        from src.agent.history import ConversationHistory
        from src.agent.sessions import SessionStore
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.models.mock import MockLLMProvider
        from src.tools.registry import ToolRegistry

        repos = SQLiteRepositoryFactory(db)
        modelo = MockLLMProvider()
        modelo.queue_text("ok")

        agente = Agent(
            model=modelo,
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
            conversation_history=ConversationHistory(repos),
        )

        assert agente.process("hola", session_id="s1") == "ok"

    def test_un_fallo_al_encolar_no_afecta_a_la_conversacion(self, db, non_interactive_permissions):
        """El dato ya está en local, que es la fuente de verdad."""
        from src.agent.core import Agent
        from src.agent.history import ConversationHistory
        from src.agent.sessions import SessionStore
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.models.mock import MockLLMProvider
        from src.tools.registry import ToolRegistry

        class ColaRota:
            def enqueue(self, *a, **k):
                raise RuntimeError("cola inaccesible")

        repos = SQLiteRepositoryFactory(db)
        modelo = MockLLMProvider()
        modelo.queue_text("sigo respondiendo")

        agente = Agent(
            model=modelo,
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
            conversation_history=ConversationHistory(repos, sync_queue=ColaRota()),
        )

        assert agente.process("hola", session_id="s1") == "sigo respondiendo"
        assert repos.messages.count("s1") == 2


# --- Cliente de Supabase (sin red) --------------------------------------------


class TestClienteSupabase:
    def test_exige_url_y_clave(self):
        from src.memory.supabase_repositories import SupabaseClient

        with pytest.raises(MemoryStorageError):
            SupabaseClient("", "clave")

    # Se parchea `httpx.Client.request` y no `httpx.request`: el cliente de
    # Supabase mantiene UNA conexion y la reutiliza, porque con Render en
    # Frankfurt y Supabase en us-east-1 abrir una nueva por consulta pagaba dos
    # apretones de manos de mas —~250 ms cada vez—. Ver
    # `tests/test_supabase_conexion.py`. Lo que estas pruebas comprueban no ha
    # cambiado: un 500 y un fallo de red siguen traduciendose al error comun.
    def test_un_error_http_se_traduce_al_error_comun(self):
        from src.memory.supabase_repositories import SupabaseClient

        cliente = SupabaseClient("https://x.supabase.co", "clave")
        respuesta = MagicMock(status_code=500, text="internal error", content=b"x")

        with patch("httpx.Client.request", return_value=respuesta):
            with pytest.raises(MemoryStorageError) as exc:
                cliente.ping()

        assert "500" in str(exc.value)

    def test_un_fallo_de_red_se_traduce_al_error_comun(self):
        import httpx

        from src.memory.supabase_repositories import SupabaseClient

        cliente = SupabaseClient("https://x.supabase.co", "clave")

        with patch("httpx.Client.request",
                   side_effect=httpx.ConnectError("sin red")):
            with pytest.raises(MemoryStorageError) as exc:
                cliente.ping()

        assert "Supabase" in str(exc.value)

    def test_health_informa_sin_lanzar(self):
        import httpx

        from src.memory.supabase_repositories import SupabaseRepositoryFactory

        fabrica = SupabaseRepositoryFactory("https://x.supabase.co", "clave")

        with patch("httpx.Client.request",
                   side_effect=httpx.ConnectError("sin red")):
            ok, error = fabrica.health()

        assert ok is False
        assert error

    def test_la_clave_no_aparece_en_los_mensajes_de_error(self):
        """Un error nunca debe filtrar la credencial."""
        import httpx

        from src.memory.supabase_repositories import SupabaseClient

        secreto = "sb_secret_no_debe_aparecer"
        cliente = SupabaseClient("https://x.supabase.co", secreto)

        with patch("httpx.Client.request",
                   side_effect=httpx.ConnectError("fallo")):
            try:
                cliente.ping()
            except MemoryStorageError as exc:
                assert secreto not in str(exc)
