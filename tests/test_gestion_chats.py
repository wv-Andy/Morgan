"""
Gestión del historial de conversaciones (Etapa A de la especificación web).

Renombrar, archivar, fijar, agrupar y buscar. Crear, listar y eliminar ya existían
desde la V1.2 y se comprueban en `test_api_sessions.py`; aquí solo lo nuevo.

Las pruebas se escriben contra la **interfaz de repositorio**, no contra SQLite,
para que valgan igual el día que el almacén sea Supabase.
"""

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory


@pytest.fixture
def repos(tmp_path):
    return SQLiteRepositoryFactory(Database(tmp_path / "chats.db"))


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_API_TOKEN", "")
    config.reset_settings()
    from src.api.app import create_app

    yield TestClient(create_app())
    config.reset_settings()


# --- Renombrar ---------------------------------------------------------------


class TestRenombrar:
    def test_cambia_el_titulo(self, repos):
        repos.sessions.create("s1", title="Sin nombre")

        actualizada = repos.sessions.update("s1", title="Notas de Supabase")

        assert actualizada.title == "Notas de Supabase"
        assert repos.sessions.get("s1").title == "Notas de Supabase"

    def test_renombrar_pisa_el_titulo_existente(self, repos):
        """`touch` solo pone título si falta; renombrar es explícito y manda."""
        repos.sessions.create("s1", title="Primero")

        repos.sessions.update("s1", title="Segundo")

        assert repos.sessions.get("s1").title == "Segundo"

    def test_un_titulo_en_blanco_lo_deja_sin_titulo(self, repos):
        repos.sessions.create("s1", title="Algo")

        assert repos.sessions.update("s1", title="   ").title is None

    def test_una_sesion_inexistente_devuelve_none(self, repos):
        assert repos.sessions.update("no-existe", title="x") is None

    def test_renombrar_no_altera_el_orden_del_historial(self, repos):
        """Renombrar no es usar la conversación: no debe saltar al principio."""
        repos.sessions.create("vieja")
        repos.sessions.create("nueva")
        antes = repos.sessions.get("vieja").updated_at

        repos.sessions.update("vieja", title="Renombrada")

        assert repos.sessions.get("vieja").updated_at == antes
        assert [s.id for s in repos.sessions.list()] == ["nueva", "vieja"]


# --- Archivar ----------------------------------------------------------------


class TestArchivar:
    def test_archivar_la_saca_del_historial_principal(self, repos):
        repos.sessions.create("s1")
        repos.sessions.create("s2")

        repos.sessions.update("s1", archived=True)

        assert [s.id for s in repos.sessions.list()] == ["s2"]

    def test_se_pueden_listar_las_archivadas(self, repos):
        repos.sessions.create("s1")
        repos.sessions.update("s1", archived=True)

        assert [s.id for s in repos.sessions.list(archived=True)] == ["s1"]

    def test_archivar_no_es_eliminar(self, repos):
        """La diferencia entera entre las dos operaciones."""
        repos.sessions.create("s1", title="Importante")
        repos.messages  # el repositorio de mensajes existe y no se toca

        repos.sessions.update("s1", archived=True)

        conservada = repos.sessions.get("s1")
        assert conservada is not None
        assert conservada.title == "Importante"

    def test_desarchivar_la_devuelve_al_historial(self, repos):
        repos.sessions.create("s1")
        repos.sessions.update("s1", archived=True)

        repos.sessions.update("s1", archived=False)

        assert [s.id for s in repos.sessions.list()] == ["s1"]

    def test_con_archived_none_salen_todas(self, repos):
        repos.sessions.create("activa")
        repos.sessions.create("guardada")
        repos.sessions.update("guardada", archived=True)

        assert len(repos.sessions.list(archived=None)) == 2


# --- Fijar -------------------------------------------------------------------


class TestFijar:
    def test_las_fijadas_van_primero(self, repos):
        repos.sessions.create("antigua")
        repos.sessions.create("reciente")

        repos.sessions.update("antigua", pinned=True)

        assert [s.id for s in repos.sessions.list()] == ["antigua", "reciente"]

    def test_dejar_de_fijar_restaura_el_orden_normal(self, repos):
        repos.sessions.create("antigua")
        repos.sessions.create("reciente")
        repos.sessions.update("antigua", pinned=True)

        repos.sessions.update("antigua", pinned=False)

        assert [s.id for s in repos.sessions.list()] == ["reciente", "antigua"]


# --- Agrupar -----------------------------------------------------------------


class TestAgrupar:
    def test_filtra_por_grupo(self, repos):
        repos.sessions.create("uni")
        repos.sessions.create("curro")
        repos.sessions.update("uni", group_name="Universidad")
        repos.sessions.update("curro", group_name="Trabajo")

        assert [s.id for s in repos.sessions.list(group_name="Universidad")] == ["uni"]

    def test_una_cadena_vacia_lo_saca_del_grupo(self, repos):
        """Distinto de None, que significa «no lo toques»."""
        repos.sessions.create("s1")
        repos.sessions.update("s1", group_name="Proyectos")

        repos.sessions.update("s1", group_name="")

        assert repos.sessions.get("s1").group_name is None

    def test_none_no_toca_el_grupo(self, repos):
        repos.sessions.create("s1")
        repos.sessions.update("s1", group_name="Proyectos")

        repos.sessions.update("s1", title="Otro nombre")

        assert repos.sessions.get("s1").group_name == "Proyectos"


# --- Buscar ------------------------------------------------------------------


class TestBuscar:
    def test_encuentra_por_fragmento_del_titulo(self, repos):
        repos.sessions.create("s1", title="Configurar Supabase y RLS")
        repos.sessions.create("s2", title="Recetas de cocina")

        assert [s.id for s in repos.sessions.list(query="supabase")] == ["s1"]

    def test_no_distingue_mayusculas(self, repos):
        repos.sessions.create("s1", title="SUPABASE")

        assert len(repos.sessions.list(query="supabase")) == 1

    def test_sin_coincidencias_devuelve_vacio(self, repos):
        repos.sessions.create("s1", title="Algo")

        assert repos.sessions.list(query="nada de nada") == []

    @pytest.mark.parametrize("comodin", ["%", "_", "%%%"])
    def test_los_comodines_se_tratan_como_texto(self, repos, comodin):
        """Sin escapar, un '%' escrito por el usuario lo casaría todo."""
        repos.sessions.create("s1", title="Conversacion normal")
        repos.sessions.create("s2", title=f"Tiene un {comodin} dentro")

        resultados = [s.id for s in repos.sessions.list(query=comodin)]

        assert resultados == ["s2"], "el comodín debe buscarse literalmente"

    def test_la_busqueda_respeta_el_filtro_de_archivadas(self, repos):
        repos.sessions.create("s1", title="Supabase")
        repos.sessions.update("s1", archived=True)

        assert repos.sessions.list(query="supabase") == []
        assert len(repos.sessions.list(query="supabase", archived=True)) == 1


# --- A través de la API ------------------------------------------------------


class TestDesdeLaAPI:
    def _crear(self, api, titulo):
        return api.post("/sessions", json={"title": titulo}).json()["id"]

    def test_patch_aplica_solo_lo_enviado(self, api):
        sid = self._crear(api, "Original")

        api.patch(f"/sessions/{sid}", json={"pinned": True})
        cuerpo = api.get(f"/sessions/{sid}").json()

        assert cuerpo["pinned"] is True
        assert cuerpo["title"] == "Original", "no se envió título: no debe cambiar"

    def test_patch_de_una_inexistente_da_404(self, api):
        respuesta = api.patch("/sessions/no-existe", json={"title": "x"})

        assert respuesta.status_code == 404
        assert respuesta.json()["error"]["code"] == "SESSION_NOT_FOUND"

    def test_un_titulo_desmesurado_se_rechaza(self, api):
        sid = self._crear(api, "Original")

        respuesta = api.patch(f"/sessions/{sid}", json={"title": "x" * 500})

        assert respuesta.status_code == 422

    def test_el_listado_filtra_y_busca(self, api):
        sid = self._crear(api, "Notas de Supabase")
        self._crear(api, "Otra cosa")

        api.patch(f"/sessions/{sid}", json={"archived": True})

        assert api.get("/sessions").json()["count"] == 1
        assert api.get("/sessions?archived=true").json()["count"] == 1
        assert api.get("/sessions?archived=true&q=supabase").json()["count"] == 1
        assert api.get("/sessions?archived=true&q=zzz").json()["count"] == 0

    def test_el_listado_devuelve_los_campos_nuevos(self, api):
        self._crear(api, "Una")

        sesion = api.get("/sessions").json()["sessions"][0]

        assert set(sesion) >= {"archived", "pinned", "group_name"}


# --- Chat temporal -----------------------------------------------------------


class TestChatTemporal:
    """Una conversación temporal vive solo en memoria.

    La promesa es concreta: no escribe en la base de datos **ni** en la memoria
    permanente. Lo segundo no sale gratis de lo primero: sin más, el modelo podría
    llamar a `remember_fact` y dejar rastro igualmente.
    """

    @staticmethod
    def _agente(repositorios):
        from src.agent.core import Agent
        from src.agent.history import ConversationHistory
        from src.agent.sessions import SessionStore
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry
        from src.tools.memory import RememberFactTool
        from src.tools.web import SearchWebTool
        from src.memory.manager import MemoryManager

        registro = ToolRegistry()
        registro.register(RememberFactTool(memory_manager=MemoryManager()))
        registro.register(SearchWebTool())

        return Agent(
            model=MockLLMProvider(),
            tool_registry=registro,
            permission_manager=PermissionManager(interactive=False),
            conversation_history=ConversationHistory(repositorios),
            sessions=SessionStore(),
        )

    def test_no_se_guarda_en_la_base_de_datos(self, repos):
        agente = self._agente(repos)

        agente.process("normal", session_id="normal")
        agente.process("temporal", session_id="efimera", temporary=True)

        guardadas = [s.id for s in repos.sessions.list(archived=None)]
        assert "normal" in guardadas
        assert "efimera" not in guardadas

    def test_no_contamina_el_historial_normal(self, repos):
        """Lo que pide explícitamente la especificación."""
        agente = self._agente(repos)

        agente.process("uno", session_id="efimera", temporary=True)
        agente.process("dos", session_id="efimera", temporary=True)

        assert repos.messages.list_for_session("efimera") == []

    def test_la_conversacion_si_continua_en_memoria(self, repos):
        """No persistir no significa perder el hilo mientras dura."""
        agente = self._agente(repos)

        agente.process("primero", session_id="efimera", temporary=True)
        agente.process("segundo", session_id="efimera", temporary=True)

        viva = agente.sessions.get("efimera")
        contenidos = [m.content for m in viva.messages if m.role == "user"]
        assert contenidos == ["primero", "segundo"]

    def test_no_se_ofrecen_las_herramientas_que_escriben_memoria(self, repos):
        agente = self._agente(repos)
        sesion = agente.sessions.get("efimera")
        sesion.temporary = True

        nombres = {e["name"] for e in agente._schemas_for(sesion)}

        assert "remember_fact" not in nombres
        assert "search_web" in nombres, "las demás deben seguir disponibles"

    def test_en_una_sesion_normal_si_se_ofrecen(self, repos):
        agente = self._agente(repos)

        nombres = {e["name"] for e in agente._schemas_for(agente.sessions.get("normal"))}

        assert "remember_fact" in nombres

    def test_una_vez_temporal_siempre_temporal(self, repos):
        """Basta pedirlo en el primer turno; los siguientes no pueden revertirlo."""
        agente = self._agente(repos)

        agente.process("uno", session_id="efimera", temporary=True)
        agente.process("dos", session_id="efimera", temporary=False)

        assert agente.sessions.get("efimera").temporary is True
        assert [s.id for s in repos.sessions.list(archived=None)] == []

    def test_desde_la_api(self, api):
        respuesta = api.post("/chat", json={
            "message": "hola",
            "session_id": "desde-la-api",
            "temporary": True,
        })

        # Sin claves de LLM configuradas el agente puede estar degradado; lo que
        # importa aqui es que el campo se acepta y que no crea la sesion.
        assert respuesta.status_code in (200, 503)
        ids = [s["id"] for s in api.get("/sessions?archived=all").json()["sessions"]]
        assert "desde-la-api" not in ids
