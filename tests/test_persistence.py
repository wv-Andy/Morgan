"""
Pruebas de la capa de persistencia de la V1.2: sesiones, mensajes y memoria.

Cubren el §16 de la especificación: base de datos, sesiones, mensajes, memoria e
integración Agent → Memoria → Base de datos.
"""

import threading

import pytest

from src.agent.core import Agent
from src.agent.history import ConversationHistory, build_title
from src.agent.sessions import SessionStore
from src.memory.db import Database, MemoryStorageError, SCHEMA_VERSION
from src.memory.models import Message, Session
from src.memory.sqlite_repositories import SQLiteRepositoryFactory, normalize_role
from src.models.mock import MockLLMProvider
from src.tools.registry import ToolRegistry


@pytest.fixture
def repos(tmp_path):
    return SQLiteRepositoryFactory(Database(tmp_path / "persistencia.db"))


# --- Migraciones y esquema -----------------------------------------------------


class TestEsquema:
    def test_version_al_dia(self, repos):
        assert repos.db.version() == SCHEMA_VERSION

    def test_existen_las_tablas_nuevas(self, repos):
        with repos.db.connect() as conn:
            tablas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        assert {"sessions", "messages", "memories", "schema_version"} <= tablas

    def test_la_tabla_conversations_se_retiro(self, repos):
        """La tabla de la V0.5 nunca tuvo consumidores; la sustituye 'messages'."""
        with repos.db.connect() as conn:
            tablas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        assert "conversations" not in tablas

    def test_migrar_es_idempotente(self, repos):
        assert repos.db.migrate() == repos.db.migrate() == SCHEMA_VERSION

    def test_base_limpia_desde_cero(self, tmp_path):
        """§8: debe poder crearse una base limpia y aplicar migraciones."""
        nueva = SQLiteRepositoryFactory(Database(tmp_path / "limpia.db"))

        assert nueva.db.version() == SCHEMA_VERSION
        assert nueva.sessions.list() == []


# --- Sesiones ------------------------------------------------------------------


class TestSesiones:
    def test_crear_y_recuperar(self, repos):
        creada = repos.sessions.create("s1", title="Prueba")

        assert isinstance(creada, Session)
        recuperada = repos.sessions.get("s1")
        assert recuperada is not None
        assert recuperada.id == "s1"
        assert recuperada.title == "Prueba"

    def test_crear_es_idempotente(self, repos):
        repos.sessions.create("s1", title="Original")
        repos.sessions.create("s1", title="Otro")

        assert repos.sessions.get("s1").title == "Original"

    def test_get_de_sesion_inexistente(self, repos):
        assert repos.sessions.get("no-existe") is None

    def test_id_vacio_se_rechaza(self, repos):
        with pytest.raises(MemoryStorageError):
            repos.sessions.create("   ")

    def test_listado_ordenado_por_recencia(self, repos):
        for sid in ("a", "b", "c"):
            repos.sessions.create(sid)
        repos.sessions.touch("a")

        ids = [s.id for s in repos.sessions.list()]
        assert ids[0] == "a"

    def test_listado_pagina(self, repos):
        for i in range(5):
            repos.sessions.create(f"s{i}")

        assert len(repos.sessions.list(limit=2)) == 2
        assert len(repos.sessions.list(limit=2, offset=4)) == 1

    def test_touch_pone_titulo_solo_si_falta(self, repos):
        repos.sessions.create("s1")
        repos.sessions.touch("s1", title="Primero")
        repos.sessions.touch("s1", title="Segundo")

        assert repos.sessions.get("s1").title == "Primero"

    def test_cuenta_los_mensajes(self, repos):
        repos.sessions.create("s1")
        repos.messages.add(Message(session_id="s1", role="user", content="hola"))

        assert repos.sessions.get("s1").message_count == 1

    def test_borrar_arrastra_los_mensajes(self, repos):
        repos.sessions.create("s1")
        repos.messages.add(Message(session_id="s1", role="user", content="hola"))

        assert repos.sessions.delete("s1") is True
        assert repos.messages.count("s1") == 0
        assert repos.sessions.get("s1") is None

    def test_borrar_inexistente(self, repos):
        assert repos.sessions.delete("no-existe") is False

    def test_las_marcas_de_tiempo_son_coherentes(self, repos):
        """El DEFAULT de SQLite es UTC; mezclarlo con hora local rompe el orden."""
        repos.sessions.create("explicita")
        # Esta se crea por el camino de respaldo, al llegar un mensaje primero.
        repos.messages.add(Message(session_id="implicita", role="user", content="hola"))

        explicita = repos.sessions.get("explicita").created_at
        implicita = repos.sessions.get("implicita").created_at

        # Ambas deben llevar milisegundos y estar en la misma escala de tiempo.
        assert "." in explicita and "." in implicita
        assert abs(len(explicita) - len(implicita)) == 0


# --- Mensajes ------------------------------------------------------------------


class TestMensajes:
    def test_persistir_y_recuperar(self, repos):
        guardado = repos.messages.add(Message(session_id="s1", role="user", content="hola"))

        assert guardado.id is not None
        recuperados = repos.messages.list_for_session("s1")
        assert [m.content for m in recuperados] == ["hola"]

    def test_se_crea_la_sesion_si_no_existia(self, repos):
        repos.messages.add(Message(session_id="huerfana", role="user", content="hola"))

        assert repos.sessions.get("huerfana") is not None

    def test_orden_cronologico(self, repos):
        for i in range(5):
            repos.messages.add(Message(session_id="s1", role="user", content=f"m{i}"))

        assert [m.content for m in repos.messages.list_for_session("s1")] == ["m0", "m1", "m2", "m3", "m4"]

    def test_el_limite_devuelve_los_mas_recientes(self, repos):
        """Cargar la cola, no la cabeza: interesa lo último dicho."""
        for i in range(10):
            repos.messages.add(Message(session_id="s1", role="user", content=f"m{i}"))

        recientes = repos.messages.list_for_session("s1", limit=3)
        assert [m.content for m in recientes] == ["m7", "m8", "m9"]

    def test_filtrado_por_rol(self, repos):
        repos.messages.add(Message(session_id="s1", role="user", content="pregunta"))
        repos.messages.add(Message(session_id="s1", role="model", content="respuesta"))
        repos.messages.add(Message(session_id="s1", role="tool", content="{}", tool_name="list_files"))

        solo_texto = repos.messages.list_for_session("s1", roles=("user", "assistant"))
        assert [m.role for m in solo_texto] == ["user", "assistant"]

    def test_el_rol_model_se_normaliza(self, repos):
        repos.messages.add(Message(session_id="s1", role="model", content="hola"))

        assert repos.messages.list_for_session("s1")[0].role == "assistant"
        assert normalize_role("model") == "assistant"

    def test_guarda_los_datos_de_la_herramienta(self, repos):
        repos.messages.add(Message(
            session_id="s1", role="tool", content="{}",
            tool_name="list_files", tool_call_id="call_1",
        ))

        guardado = repos.messages.list_for_session("s1")[0]
        assert guardado.tool_name == "list_files"
        assert guardado.tool_call_id == "call_1"

    def test_add_many(self, repos):
        n = repos.messages.add_many([
            Message(session_id="s1", role="user", content="a"),
            Message(session_id="s1", role="model", content="b"),
        ])

        assert n == 2
        assert repos.messages.count("s1") == 2

    def test_add_many_vacio(self, repos):
        assert repos.messages.add_many([]) == 0

    def test_los_mensajes_de_distintas_sesiones_no_se_mezclan(self, repos):
        repos.messages.add(Message(session_id="a", role="user", content="de a"))
        repos.messages.add(Message(session_id="b", role="user", content="de b"))

        assert [m.content for m in repos.messages.list_for_session("a")] == ["de a"]

    def test_borrar_los_mensajes_de_una_sesion(self, repos):
        repos.messages.add(Message(session_id="s1", role="user", content="hola"))

        assert repos.messages.delete_for_session("s1") == 1
        assert repos.messages.count("s1") == 0

    def test_metadatos_van_y_vuelven(self, repos):
        repos.messages.add(Message(session_id="s1", role="user", content="x", metadata={"origen": "web"}))

        assert repos.messages.list_for_session("s1")[0].metadata == {"origen": "web"}

    def test_metadatos_corruptos_no_rompen_la_lectura(self, repos):
        repos.messages.add(Message(session_id="s1", role="user", content="x"))
        with repos.db.connect() as conn:
            conn.execute("UPDATE messages SET metadata = 'esto no es json'")

        assert repos.messages.list_for_session("s1")[0].metadata == {}


# --- Memoria persistente -------------------------------------------------------


class TestMemoriaPersistente:
    def test_crear_y_leer(self, repos):
        repos.memories.upsert("nombre", "Andy", "user")

        registro = repos.memories.get("nombre")
        assert registro is not None
        assert registro.value == "Andy"
        assert registro.category == "user"

    def test_actualizar_por_clave(self, repos):
        repos.memories.upsert("nombre", "Andy")
        repos.memories.upsert("nombre", "Andrés")

        assert repos.memories.get("nombre").value == "Andrés"
        assert len(repos.memories.search()) == 1

    def test_buscar_por_texto(self, repos):
        repos.memories.upsert("lenguaje", "Python")
        repos.memories.upsert("editor", "VS Code")

        assert [r.key for r in repos.memories.search(query="Python")] == ["lenguaje"]

    def test_buscar_por_categoria(self, repos):
        repos.memories.upsert("a", "1", "uno")
        repos.memories.upsert("b", "2", "dos")

        assert [r.key for r in repos.memories.search(category="dos")] == ["b"]

    def test_eliminar(self, repos):
        repos.memories.upsert("temporal", "x")

        assert repos.memories.delete("temporal") is True
        assert repos.memories.get("temporal") is None

    def test_eliminar_inexistente(self, repos):
        assert repos.memories.delete("fantasma") is False

    def test_clave_vacia_se_rechaza(self, repos):
        with pytest.raises(MemoryStorageError):
            repos.memories.upsert("  ", "valor")

    def test_limpiar_por_categoria(self, repos):
        repos.memories.upsert("a", "1", "uno")
        repos.memories.upsert("b", "2", "dos")

        assert repos.memories.clear(category="uno") == 1
        assert len(repos.memories.search()) == 1


# --- Errores de la base --------------------------------------------------------


class TestErroresDeBaseDeDatos:
    def test_base_corrupta(self, tmp_path):
        corrupta = tmp_path / "rota.db"
        corrupta.write_bytes(b"no soy una base de datos" * 40)

        with pytest.raises(MemoryStorageError):
            SQLiteRepositoryFactory(Database(corrupta))

    def test_health_reporta_correctamente(self, repos):
        assert repos.health() == (True, None)

    def test_escrituras_concurrentes(self, repos):
        errores: list[Exception] = []

        def escribir(i: int):
            try:
                for j in range(15):
                    repos.messages.add(Message(session_id=f"s{i}", role="user", content=f"{i}-{j}"))
            except Exception as exc:  # pragma: no cover
                errores.append(exc)

        hilos = [threading.Thread(target=escribir, args=(i,)) for i in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        assert errores == []
        assert sum(repos.messages.count(f"s{i}") for i in range(4)) == 60


# --- Integración Agent -> Memoria -> Base --------------------------------------


class _RepositoriosRotos:
    """Simula una base caída para comprobar la degradación."""

    class _Roto:
        def __getattr__(self, _name):
            def _fallar(*args, **kwargs):
                raise MemoryStorageError("base de datos no disponible")
            return _fallar

    sessions = _Roto()
    messages = _Roto()
    memories = _Roto()

    def health(self):
        return False, "base de datos no disponible"


def _agente(repos, permisos, respuestas, window=20):
    modelo = MockLLMProvider()
    for r in respuestas:
        modelo.queue_text(r)
    return Agent(
        model=modelo,
        tool_registry=ToolRegistry(),
        permission_manager=permisos,
        sessions=SessionStore(),
        conversation_history=ConversationHistory(repos, window=window),
    )


class TestIntegracion:
    def test_la_conversacion_se_persiste(self, repos, non_interactive_permissions):
        agente = _agente(repos, non_interactive_permissions, ["Hola"])
        agente.process("Buenas", session_id="s1")

        guardados = repos.messages.list_for_session("s1")
        assert [(m.role, m.content) for m in guardados] == [("user", "Buenas"), ("assistant", "Hola")]

    def test_la_conversacion_se_recupera_tras_reiniciar(self, repos, non_interactive_permissions):
        primero = _agente(repos, non_interactive_permissions, ["Encantado"])
        primero.process("Me llamo Andy", session_id="s1")

        # Un agente nuevo equivale a haber reiniciado el servidor.
        segundo = _agente(repos, non_interactive_permissions, ["Te llamas Andy"])
        assert len(segundo.sessions.get("s1").messages) == 0

        segundo.process("¿Cómo me llamo?", session_id="s1")
        contexto = [m.content for m in segundo.sessions.get("s1").messages]
        assert "Me llamo Andy" in contexto

    def test_solo_se_recupera_la_ventana_reciente(self, repos, non_interactive_permissions):
        """§12: no cargar historial innecesario en cada petición."""
        for i in range(30):
            repos.messages.add(Message(session_id="s1", role="user", content=f"m{i}"))

        agente = _agente(repos, non_interactive_permissions, ["ok"], window=5)
        agente.process("nuevo", session_id="s1")

        recuperados = [m.content for m in agente.sessions.get("s1").messages if m.content.startswith("m")]
        assert len(recuperados) == 5
        assert recuperados == ["m25", "m26", "m27", "m28", "m29"]

    def test_no_se_reinyectan_mensajes_de_herramienta(self, repos, non_interactive_permissions):
        """Un resultado de herramienta sin su llamada rompe el protocolo del proveedor."""
        repos.messages.add(Message(session_id="s1", role="tool", content="{}", tool_name="list_files"))
        repos.messages.add(Message(session_id="s1", role="user", content="hola"))

        agente = _agente(repos, non_interactive_permissions, ["ok"])
        agente.process("otra", session_id="s1")

        assert all(m.role != "tool" for m in agente.sessions.get("s1").messages)

    def test_la_sesion_recibe_titulo_del_primer_mensaje(self, repos, non_interactive_permissions):
        agente = _agente(repos, non_interactive_permissions, ["ok"])
        agente.process("Ayúdame con el despliegue", session_id="s1")

        assert repos.sessions.get("s1").title == "Ayúdame con el despliegue"

    def test_hidrata_una_sola_vez(self, repos, non_interactive_permissions):
        agente = _agente(repos, non_interactive_permissions, ["a", "b"])
        agente.process("uno", session_id="s1")
        agente.process("dos", session_id="s1")

        # Cuatro mensajes del turno, sin duplicados por rehidratación.
        assert len([m for m in agente.sessions.get("s1").messages if m.content == "uno"]) == 1

    def test_una_base_caida_no_impide_conversar(self, non_interactive_permissions):
        """§15: un fallo de base no debe tumbar Morgan."""
        agente = _agente(_RepositoriosRotos(), non_interactive_permissions, ["Sigo respondiendo"])

        respuesta = agente.process("hola", session_id="s1")

        assert respuesta == "Sigo respondiendo"

    def test_sin_historial_configurado_funciona_igual(self, non_interactive_permissions):
        modelo = MockLLMProvider()
        modelo.queue_text("ok")
        agente = Agent(
            model=modelo,
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
        )

        assert agente.process("hola", session_id="s1") == "ok"


class TestTitulos:
    def test_titulo_corto_se_conserva(self):
        assert build_title("Hola mundo") == "Hola mundo"

    def test_titulo_largo_se_recorta(self):
        titulo = build_title("palabra " * 40)

        assert len(titulo) <= 60
        assert titulo.endswith("…")

    def test_normaliza_espacios(self):
        assert build_title("  hola\n   mundo  ") == "hola mundo"
