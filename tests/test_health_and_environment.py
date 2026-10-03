"""
Pruebas del estado por servicio y de la separación de capacidades (V1.3, §8 y §17).

Los fallos se simulan, no se provocan desenchufando la red: así son reproducibles.
"""

import time
from unittest.mock import patch


from src.health import ServiceHealth, ServiceState, ServiceStatus


def disponible():
    return ServiceState.AVAILABLE, "todo bien"


def caido():
    return ServiceState.UNAVAILABLE, "sin conexión"


def revienta():
    raise ConnectionError("getaddrinfo failed")


# --- Registro de salud ---------------------------------------------------------


class TestServiceHealth:
    def test_un_servicio_sin_comprobar_es_desconocido(self):
        health = ServiceHealth()
        health.register("x", disponible)

        assert health.get("x").state == ServiceState.UNKNOWN

    def test_comprobar_actualiza_el_estado(self):
        health = ServiceHealth()
        health.register("x", disponible)

        assert health.check("x").state == ServiceState.AVAILABLE

    def test_un_servicio_no_registrado_no_revienta(self):
        assert ServiceHealth().get("fantasma").state == ServiceState.UNKNOWN

    def test_una_comprobacion_que_lanza_excepcion_se_traduce_a_caido(self):
        """El sondeo informa; una excepción suya no debe propagarse."""
        health = ServiceHealth()
        health.register("x", revienta)

        estado = health.check("x")

        assert estado.state == ServiceState.UNAVAILABLE
        assert "getaddrinfo" in estado.detail

    def test_el_ttl_evita_comprobar_de_mas(self):
        """Consultar el estado a menudo no debe salir a la red cada vez."""
        llamadas = {"n": 0}

        def contar():
            llamadas["n"] += 1
            return ServiceState.AVAILABLE, None

        health = ServiceHealth(ttl=60)
        health.register("x", contar)
        for _ in range(5):
            health.check("x")

        assert llamadas["n"] == 1

    def test_force_ignora_el_ttl(self):
        llamadas = {"n": 0}

        def contar():
            llamadas["n"] += 1
            return ServiceState.AVAILABLE, None

        health = ServiceHealth(ttl=60)
        health.register("x", contar)
        health.check("x")
        health.check("x", force=True)

        assert llamadas["n"] == 2

    def test_un_servicio_caido_se_comprueba_cada_vez_menos(self):
        """§20: evitar bucles agresivos de reconexión."""
        health = ServiceHealth(ttl=10)
        health.register("x", caido)

        health.check("x")
        primera = health._next_check_delay(health.get("x"))
        health.check("x", force=True)
        segunda = health._next_check_delay(health.get("x"))

        assert segunda > primera

    def test_el_retroceso_esta_acotado(self):
        health = ServiceHealth(ttl=10, max_backoff=60)
        estado = ServiceStatus(name="x", consecutive_failures=20)

        assert health._next_check_delay(estado) == 60

    def test_recuperarse_reinicia_el_contador(self):
        """§17 Recovery: el servicio vuelve y Morgan recupera capacidad."""
        respuestas = [caido(), caido(), disponible()]

        def secuencia():
            return respuestas.pop(0)

        health = ServiceHealth(ttl=0)
        health.register("x", secuencia)
        health.check("x", force=True)
        health.check("x", force=True)
        assert health.get("x").consecutive_failures == 2

        health.check("x", force=True)

        assert health.get("x").state == ServiceState.AVAILABLE
        assert health.get("x").consecutive_failures == 0

    def test_degradado_sigue_siendo_utilizable(self):
        health = ServiceHealth()
        health.register("x", lambda: (ServiceState.DEGRADED, "lento"))
        health.check("x")

        assert health.is_usable("x") is True

    def test_caido_no_es_utilizable(self):
        health = ServiceHealth()
        health.register("x", caido)
        health.check("x")

        assert health.is_usable("x") is False


class TestEstadoGlobal:
    def test_todo_disponible(self):
        health = ServiceHealth()
        health.register("a", disponible)
        health.register("b", disponible)
        health.check_all()

        assert health.overall() == ServiceState.AVAILABLE

    def test_uno_caido_deja_el_conjunto_degradado(self):
        """§6: online no significa que todo funcione."""
        health = ServiceHealth()
        health.register("a", disponible)
        health.register("b", caido)
        health.check_all()

        assert health.overall() == ServiceState.DEGRADED

    def test_todo_caido(self):
        health = ServiceHealth()
        health.register("a", caido)
        health.register("b", caido)
        health.check_all()

        assert health.overall() == ServiceState.UNAVAILABLE

    def test_sin_servicios_registrados(self):
        assert ServiceHealth().overall() == ServiceState.UNKNOWN


class TestComprobacionesConcretas:
    def test_internet_caido_se_detecta(self):
        from src.api.health_checks import check_internet

        with patch("src.api.health_checks.socket.create_connection", side_effect=OSError("sin red")):
            estado, detalle = check_internet()

        assert estado == ServiceState.UNAVAILABLE
        assert "Internet" in detalle

    def test_internet_lento_se_marca_degradado(self):
        from src.api import health_checks

        class ConexionLenta:
            def __enter__(self):
                time.sleep(health_checks.SLOW_THRESHOLD_SECONDS + 0.05)
                return self

            def __exit__(self, *a):
                return False

        with patch.object(health_checks.socket, "create_connection", return_value=ConexionLenta()):
            estado, detalle = health_checks.check_internet()

        assert estado == ServiceState.DEGRADED
        assert "lenta" in detalle

    def test_base_local_caida(self):
        from src.api.health_checks import make_local_database_check

        class ReposRotos:
            def health(self):
                return False, "fichero bloqueado"

        estado, detalle = make_local_database_check(ReposRotos())()

        assert estado == ServiceState.UNAVAILABLE
        assert detalle == "fichero bloqueado"

    def test_llm_sin_agente_es_no_disponible(self):
        from src.api.health_checks import make_llm_check

        class SinAgente:
            agent = None
            llm_error = "sin claves"

        estado, detalle = make_llm_check(SinAgente())()

        assert estado == ServiceState.UNAVAILABLE
        assert detalle == "sin claves"

    def test_llm_que_uso_el_respaldo_se_marca_degradado(self):
        from src.api.health_checks import make_llm_check

        class Modelo:
            model_name = "principal [respaldo]"
            primary_failures = 2
            fallback_uses = 2

        class ConAgente:
            agent = type("A", (), {"model": Modelo()})()
            llm_error = None

        estado, detalle = make_llm_check(ConAgente())()

        assert estado == ServiceState.DEGRADED
        assert "respaldo" in detalle


# --- Separación de capacidades local / cloud (§3, §13, §15) --------------------


class TestSeparacionDeCapacidades:
    def test_las_herramientas_locales_estan_marcadas(self):
        from src.tools.filesystem import DeleteFileTool, ReadFileTool
        from src.tools.terminal import ExecuteCommandTool

        assert ReadFileTool().requires_local is True
        assert DeleteFileTool().requires_local is True
        assert ExecuteCommandTool().requires_local is True

    def test_las_aptas_en_cloud_estan_marcadas(self):
        from src.tools.memory import RecallMemoryTool
        from src.tools.web import SearchWebTool

        assert SearchWebTool().requires_local is False
        assert RecallMemoryTool().requires_local is False

    def test_una_herramienta_nueva_es_local_por_defecto(self):
        """Fail-safe: olvidarse de declararlo la deja fuera del cloud, no dentro."""
        from src.tools.base import Tool

        class Inventada(Tool):
            @property
            def name(self): return "inventada"

            @property
            def description(self): return "d"

            def execute(self, **kwargs): return {"success": True, "data": None, "error": None}

        assert Inventada().requires_local is True

    def test_en_cloud_las_herramientas_locales_no_se_registran(self, monkeypatch):
        """§15: Cloud Morgan != acceso automático al ordenador."""
        from src.api import dependencies
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        reset_settings()
        dependencies.reset_container()

        registry = dependencies.get_container().tool_registry
        nombres = {t.name for t in registry.list_tools()}

        assert "execute_command" not in nombres
        # `read_file` (3.0-E) y `delete_file` (3.3), solo como representantes del agente
        # local, y no disponibles sin un PC conectado: la nube sigue sin tocar ningún
        # disco. Borrar, además, exige un plan aprobado.
        from src.canal.herramientas import EscribirEnElEquipo, HerramientaDelEquipo

        assert isinstance(registry.get("delete_file"), EscribirEnElEquipo)
        assert registry.get("delete_file").exige_plan
        assert registry.get("delete_file").disponible() is False

        assert isinstance(registry.get("read_file"), HerramientaDelEquipo)
        assert registry.get("read_file").disponible() is False
        # Las seguras siguen ahí.
        assert "search_web" in nombres
        assert "recall_memory" in nombres

    def test_en_local_estan_todas(self, monkeypatch):
        from src.api import dependencies
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        reset_settings()
        dependencies.reset_container()

        nombres = {t.name for t in dependencies.get_container().tool_registry.list_tools()}

        # Las mismas que la prueba de la nube exige que falten. Antes se comparaba
        # con el catálogo de la consola, que era una copia: la comparación solo
        # veía si la consola tenía alguna DE MÁS, y lo que pasaba era lo
        # contrario. La consola usa ahora este mismo catálogo (test_consola.py).
        assert "execute_command" in nombres
        assert "delete_file" in nombres
        assert "read_file" in nombres

    def test_un_entorno_invalido_cae_a_local(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "marte")

        assert load_settings().environment == "local"

    def test_unregister_del_registro(self):
        from src.tools.registry import ToolRegistry
        from src.tools.system import SystemInfoTool

        registry = ToolRegistry()
        registry.register(SystemInfoTool())

        assert registry.unregister("system_info") is True
        assert registry.unregister("system_info") is False
        assert len(registry) == 0


class TestElEstadoDiceQueAlmacenEsDeVerdad:
    """`/status` decía «SQLite conectado» **siempre**, escrito a mano.

    En la nube el almacén es Supabase, así que el panel de estado —el sitio al
    que se mira cuando algo va mal— informaba de algo que no era cierto. Y en un
    panel de diagnóstico, un dato falso es peor que un dato ausente: manda a
    buscar el problema al sitio equivocado.

    Contaba además los recuerdos de **quien pregunta**, y `/status` es una ruta
    pública: sin sesión el contexto es el usuario implícito, de modo que
    marcaba «0 recuerdos» a todo el mundo. Ese número ya no está; donde importa
    es en la vista de memoria, que sí lo enseña.
    """

    def test_en_local_dice_sqlite(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        from src import config

        config.reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        from src.api.app import create_app

        try:
            respuesta = TestClient(create_app()).get("/status")
            detalle = respuesta.json()["components"]["database"]["details"]

            assert "SQLite" in detalle
        finally:
            config.reset_settings()
            dependencies.reset_container()

    def test_el_nombre_sale_de_la_fabrica_en_uso(self):
        """No de la configuración: en la nube es el contenedor el que cambia
        `repositories` al arrancar, y ese cambio —no la variable de entorno— es
        el que decide dónde acaban los datos."""
        from src.api.routes.health import _nombre_del_almacen

        class FabricaSupabase:
            pass

        class ContenedorFalso:
            repositories = FabricaSupabase()

        FabricaSupabase.__name__ = "SupabaseRepositoryFactory"

        assert _nombre_del_almacen(ContenedorFalso()) == "Supabase"

    def test_ya_no_se_cuentan_recuerdos_de_nadie(self):
        """Un número por usuario no pinta nada en un estado de sistema, y en una
        ruta pública siempre valdría cero."""
        from fastapi.testclient import TestClient

        from src.api.app import app

        detalle = TestClient(app).get("/status").json()["components"]["database"]["details"]

        assert "recuerdos" not in detalle


class TestStatusEndpoint:
    def test_status_expone_servicios_y_capacidades(self):
        from fastapi.testclient import TestClient

        from src.api.app import app

        cuerpo = TestClient(app).get("/status").json()

        assert "services" in cuerpo and cuerpo["services"]
        assert "capabilities" in cuerpo and cuerpo["capabilities"]
        assert cuerpo["environment"] in ("local", "cloud")
        nombres = {s["name"] for s in cuerpo["services"]}
        assert {"internet", "database.local", "llm"} <= nombres

    def test_las_capacidades_reflejan_el_entorno_cloud(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src.api import dependencies
        from src.api.app import app
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        reset_settings()
        dependencies.reset_container()

        cuerpo = TestClient(app).get("/status").json()
        capacidades = {c["name"]: c for c in cuerpo["capabilities"]}

        assert capacidades["Terminal y procesos"]["available"] is False
        assert capacidades["Archivos del equipo"]["available"] is False
        assert "cloud" in capacidades["Terminal y procesos"]["reason"]
