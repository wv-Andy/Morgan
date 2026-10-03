"""
Pruebas exhaustivas para Morgan API (V1.0).
Cubre: /health, /status, /tools, /memory, /audit, /chat, modo degradado y middlewares.
"""

import pytest
from fastapi.testclient import TestClient
from src.api.app import app
from src.api.dependencies import get_container, CoreContainer
from src.models.mock import MockLLMProvider
from src.agent.core import Agent


@pytest.fixture
def client():
    """Cliente de pruebas HTTP para FastAPI."""
    return TestClient(app)


class TestHealthAndStatus:
    def test_health_endpoint(self, client):
        """La versión se compara con la del código, no con un literal.

        Escrita a mano decía `"1.0.0"` — el mismo número que `/health` llevaba
        mal desde hacía ocho versiones. La prueba no cazaba el desfase: lo
        sostenía. Ahora sale de `src.__version__`, y de que ese número sea el
        correcto se encarga `test_version.py`, que lo ata al CHANGELOG.
        """
        from src import __version__

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == __version__
        assert "timestamp" in data

    def test_status_endpoint(self, client):
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ready", "degraded")
        assert data["tools_count"] >= 25
        assert data["domains_count"] >= 7
        assert "core" in data["components"]
        assert "database" in data["components"]
        assert "tools" in data["components"]
        assert "permissions" in data["components"]
        assert "llm" in data["components"]


class TestToolsApi:
    def test_list_tools(self, client):
        response = client.get("/tools")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total"] >= 25
        assert len(data["categories"]) >= 7
        assert "system" in data["categories"]
        assert "filesystem" in data["categories"]
        assert "coding" in data["categories"]
        assert "git" in data["categories"]

    def test_list_tools_filter_category(self, client):
        response = client.get("/tools?category=coding")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 4
        for tool in data["tools"]:
            assert tool["category"] == "coding"

    def test_get_tool_success(self, client):
        response = client.get("/tools/system_info")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "system_info"
        assert data["category"] == "system"
        assert data["risk_level"] == "safe"
        assert "description" in data
        assert "parameters" in data

    def test_get_tool_not_found(self, client):
        response = client.get("/tools/herramienta_fantasma")
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "TOOL_NOT_FOUND"

    def test_execute_tool_safe(self, client):
        response = client.post("/tools/system_info", json={"arguments": {}})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tool"] == "system_info"
        assert data["authorized"] is True
        assert "cpu" in data["data"]
        assert "memory" in data["data"]

    def test_execute_tool_not_found(self, client):
        response = client.post("/tools/herramienta_fantasma", json={"arguments": {}})
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "TOOL_NOT_FOUND"

    def test_execute_tool_permission_denied(self, client):
        # Intentar ejecutar comando peligroso bloqueado por CommandValidator
        response = client.post(
            "/tools/execute_command",
            json={"arguments": {"command": "Format-Volume -DriveLetter C"}},
        )
        assert response.status_code == 403
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "PERMISSION_DENIED"


class TestMemoryApi:
    def test_memory_crud_flow(self, client):
        key = "test_framework_preference"
        val = "FastAPI + React"

        # 1. Crear recuerdo
        create_resp = client.post("/memory", json={"category": "preferences", "key": key, "value": val})
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["key"] == key
        assert created["value"] == val

        # 2. Consultar recuerdo
        get_resp = client.get(f"/memory?query={key}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["count"] >= 1
        assert any(m["key"] == key for m in data["memories"])

        # 3. Eliminar recuerdo
        del_resp = client.delete(f"/memory/{key}")
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True

        # 4. Eliminar de nuevo -> 404
        del_resp2 = client.delete(f"/memory/{key}")
        assert del_resp2.status_code == 404
        assert del_resp2.json()["error"]["code"] == "MEMORY_KEY_NOT_FOUND"


class TestAuditApi:
    def test_get_audit_logs(self, client):
        response = client.get("/audit?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["records"], list)


class TestChatApi:
    def test_chat_endpoint_with_mock_agent(self, client):
        # Crear contenedor con agente mock para no consumir tokens
        mock_llm = MockLLMProvider()
        mock_llm.queue_text("Hola, soy Morgan funcionando a través de la API REST.")

        container = get_container()
        original_agent = container.agent
        container.agent = Agent(
            model=mock_llm,
            tool_registry=container.tool_registry,
            permission_manager=container.permission_manager,
        )

        try:
            response = client.post("/chat", json={"message": "¿Hola quién eres?"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "Morgan funcionando a través de la API REST" in data["response"]
            assert data["elapsed_seconds"] >= 0
        finally:
            container.agent = original_agent

    def test_chat_endpoint_degraded_mode(self, client):
        container = get_container()
        original_agent = container.agent
        container.agent = None

        try:
            response = client.post("/chat", json={"message": "Hola"})
            assert response.status_code == 503
            data = response.json()
            assert data["success"] is False
            assert data["error"]["code"] == "DEGRADED_MODE"
        finally:
            container.agent = original_agent


class TestValidationAndErrorHandling:
    def test_chat_empty_message_validation_error(self, client):
        response = client.post("/chat", json={"message": ""})
        assert response.status_code == 422
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "VALIDATION_ERROR"
