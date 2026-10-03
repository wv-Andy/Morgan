"""
Pruebas de resiliencia de la API de Morgan.

Cubren los caminos que la suite original no ejercitaba: sesiones, permisos sin
consola, base de datos caída, respuestas de error y aislamiento del contenedor.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.dependencies import get_container
from src.agent.core import Agent
from src.agent.sessions import SessionStore
from src.memory.db import MemoryStorageError
from src.models.mock import MockLLMProvider
from src.tools.registry import ToolRegistry


@pytest.fixture
def client():
    return TestClient(app)


class TestSesionesEnLaApi:
    def test_las_conversaciones_de_distinta_sesion_no_se_mezclan(self, client):
        container = get_container()
        original = container.agent

        model = MockLLMProvider()
        for _ in range(4):
            model.queue_text("respuesta")
        container.agent = Agent(
            model=model,
            tool_registry=container.tool_registry,
            permission_manager=container.permission_manager,
            sessions=SessionStore(),
        )

        try:
            client.post("/chat", json={"message": "soy ana", "session_id": "ana"})
            client.post("/chat", json={"message": "soy bob", "session_id": "bob"})

            ana = [m.content for m in container.agent.sessions.get("ana").messages if m.role == "user"]
            bob = [m.content for m in container.agent.sessions.get("bob").messages if m.role == "user"]

            assert ana == ["soy ana"]
            assert bob == ["soy bob"]
        finally:
            container.agent = original


class TestPermisosViaHttp:
    def test_una_herramienta_moderada_se_deniega_y_no_cuelga(self, client):
        """Sin consola debe responder 403, no quedarse esperando confirmación."""
        respuesta = client.post(
            "/tools/create_file",
            json={"arguments": {"path": "salida_de_prueba.txt", "content": "hola"}},
        )

        assert respuesta.status_code == 403
        assert respuesta.json()["error"]["code"] == "PERMISSION_DENIED"

    def test_escritura_en_ruta_del_sistema_denegada(self, client):
        respuesta = client.post(
            "/tools/create_file",
            json={"arguments": {"path": "C:/Windows/System32/prueba.txt", "content": "x"}},
        )

        assert respuesta.status_code == 403

    def test_borrado_denegado_via_http(self, client):
        respuesta = client.post(
            "/tools/delete_file",
            json={"arguments": {"path": "cualquier_cosa.txt"}},
        )

        assert respuesta.status_code == 403

    def test_una_herramienta_segura_si_se_ejecuta(self, client):
        respuesta = client.post("/tools/system_info", json={"arguments": {}})

        assert respuesta.status_code == 200
        assert respuesta.json()["authorized"] is True


class TestArgumentosInvalidos:
    def test_argumento_desconocido_da_400(self, client):
        respuesta = client.post(
            "/tools/system_info",
            json={"arguments": {"parametro_inventado": 123}},
        )

        assert respuesta.status_code == 400
        assert respuesta.json()["error"]["code"] == "INVALID_ARGUMENTS"

    def test_limite_de_auditoria_fuera_de_rango(self, client):
        assert client.get("/audit?limit=0").status_code == 422
        assert client.get("/audit?limit=99999").status_code == 422

    def test_recuerdo_sin_valor_es_rechazado(self, client):
        respuesta = client.post("/memory", json={"category": "x", "key": "k", "value": ""})

        assert respuesta.status_code == 422
        assert respuesta.json()["error"]["code"] == "VALIDATION_ERROR"


class TestBaseDeDatosCaida:
    def test_status_reporta_la_base_caida_sin_romperse(self, client):
        container = get_container()
        original = container.memory_manager

        class MemoriaRota:
            def health(self):
                return False, "disco no disponible"

            def recall(self, *args, **kwargs):
                raise MemoryStorageError("disco no disponible")

            def get_context_summary(self):
                return ""

        container.memory_manager = MemoriaRota()
        try:
            respuesta = client.get("/status")

            assert respuesta.status_code == 200
            componentes = respuesta.json()["components"]
            assert componentes["database"]["status"] == "error"
            # El resto de subsistemas sigue informando con normalidad.
            assert componentes["tools"]["status"] == "ok"
        finally:
            container.memory_manager = original

    def test_listar_memoria_con_la_base_caida_no_devuelve_500_silencioso(self, client):
        container = get_container()
        original = container.memory_manager

        class MemoriaRota:
            def recall(self, *args, **kwargs):
                raise MemoryStorageError("base de datos bloqueada")

        container.memory_manager = MemoriaRota()
        try:
            respuesta = client.get("/memory")

            assert respuesta.status_code == 500
            cuerpo = respuesta.json()
            assert cuerpo["success"] is False
            # El detalle interno no debe viajar al cliente.
            assert cuerpo["error"]["details"] is None
        finally:
            container.memory_manager = original


class TestErroresNoFiltranDetalles:
    def test_el_error_interno_no_expone_la_traza(self, client):
        container = get_container()
        original = container.agent

        class AgenteRoto:
            model = type("M", (), {"model_name": "roto"})()

            def chat(self, *args, **kwargs):
                raise RuntimeError("C:/ruta/secreta/del/disco/interna.py explotó")

        container.agent = AgenteRoto()
        try:
            respuesta = client.post("/chat", json={"message": "hola"})

            assert respuesta.status_code == 500
            cuerpo = respuesta.json()
            assert "ruta/secreta" not in str(cuerpo)
        finally:
            container.agent = original


class TestModoDegradado:
    def test_el_resto_de_rutas_funciona_sin_llm(self, client):
        container = get_container()
        original = container.agent
        container.agent = None

        try:
            assert client.get("/health").status_code == 200
            assert client.get("/tools").status_code == 200
            assert client.get("/memory").status_code == 200
            assert client.get("/audit").status_code == 200
            assert client.post("/chat", json={"message": "hola"}).status_code == 503
        finally:
            container.agent = original
