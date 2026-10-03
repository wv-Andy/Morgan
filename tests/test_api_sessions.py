"""
Pruebas de los endpoints de sesiones e historial (V1.2).
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.dependencies import get_container
from src.memory.db import MemoryStorageError
from src.memory.models import Message


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def repos():
    return get_container().repositories


class TestListarSesiones:
    def test_lista_vacia(self, client):
        r = client.get("/sessions")

        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["success"] is True
        assert cuerpo["sessions"] == []

    def test_lista_con_contenido(self, client, repos):
        repos.sessions.create("ses-a", title="Primera")
        repos.messages.add(Message(session_id="ses-a", role="user", content="hola"))

        cuerpo = client.get("/sessions").json()

        assert cuerpo["count"] == 1
        assert cuerpo["sessions"][0]["id"] == "ses-a"
        assert cuerpo["sessions"][0]["title"] == "Primera"
        assert cuerpo["sessions"][0]["message_count"] == 1

    def test_pagina(self, client, repos):
        for i in range(4):
            repos.sessions.create(f"ses-{i}")

        assert client.get("/sessions?limit=2").json()["count"] == 2

    @pytest.mark.parametrize("query", ["limit=0", "limit=999", "offset=-1"])
    def test_parametros_invalidos(self, client, query):
        assert client.get(f"/sessions?{query}").status_code == 422


class TestCrearSesion:
    def test_crea_con_id_propio(self, client):
        r = client.post("/sessions", json={"session_id": "mia", "title": "Mi charla"})

        assert r.status_code == 200
        assert r.json()["id"] == "mia"
        assert r.json()["title"] == "Mi charla"

    def test_genera_id_si_no_se_indica(self, client):
        r = client.post("/sessions", json={})

        assert r.status_code == 200
        assert r.json()["id"].startswith("ses-")

    def test_crear_dos_veces_no_duplica(self, client):
        client.post("/sessions", json={"session_id": "unica", "title": "Original"})
        r = client.post("/sessions", json={"session_id": "unica", "title": "Otro"})

        assert r.json()["title"] == "Original"


class TestDetalleYMensajes:
    def test_detalle(self, client, repos):
        repos.sessions.create("ses-x", title="Detalle")

        r = client.get("/sessions/ses-x")

        assert r.status_code == 200
        assert r.json()["title"] == "Detalle"

    def test_detalle_inexistente(self, client):
        r = client.get("/sessions/fantasma")

        assert r.status_code == 404
        assert r.json()["error"]["code"] == "SESSION_NOT_FOUND"

    def test_mensajes_en_orden(self, client, repos):
        for i in range(3):
            repos.messages.add(Message(session_id="ses-m", role="user", content=f"m{i}"))

        cuerpo = client.get("/sessions/ses-m/messages").json()

        assert [m["content"] for m in cuerpo["messages"]] == ["m0", "m1", "m2"]
        assert cuerpo["total"] == 3

    def test_mensajes_limitados_a_los_recientes(self, client, repos):
        for i in range(10):
            repos.messages.add(Message(session_id="ses-m", role="user", content=f"m{i}"))

        cuerpo = client.get("/sessions/ses-m/messages?limit=2").json()

        assert [m["content"] for m in cuerpo["messages"]] == ["m8", "m9"]
        assert cuerpo["total"] == 10

    def test_mensajes_de_sesion_inexistente(self, client):
        cuerpo = client.get("/sessions/fantasma/messages").json()

        assert cuerpo["count"] == 0


class TestEliminarSesion:
    def test_elimina_sesion_y_mensajes(self, client, repos):
        repos.sessions.create("ses-borrar")
        repos.messages.add(Message(session_id="ses-borrar", role="user", content="hola"))

        r = client.delete("/sessions/ses-borrar")

        assert r.status_code == 200
        assert r.json()["success"] is True
        assert repos.messages.count("ses-borrar") == 0

    def test_eliminar_inexistente(self, client):
        r = client.delete("/sessions/fantasma")

        assert r.status_code == 404
        assert r.json()["error"]["code"] == "SESSION_NOT_FOUND"


class TestErroresDePersistencia:
    def test_una_base_caida_devuelve_500_controlado(self, client):
        container = get_container()
        originales = container.repositories

        class _Rotos:
            class _R:
                def __getattr__(self, _n):
                    def _fallar(*a, **k):
                        raise MemoryStorageError("base caída")
                    return _fallar
            sessions = _R()
            messages = _R()
            memories = _R()

        container.repositories = _Rotos()
        try:
            r = client.get("/sessions")

            assert r.status_code == 500
            cuerpo = r.json()
            assert cuerpo["error"]["code"] == "STORAGE_ERROR"
            # El detalle interno no viaja al cliente.
            assert "base caída" not in str(cuerpo.get("error", {}).get("details"))
        finally:
            container.repositories = originales


class TestIntegracionConElChat:
    def test_el_chat_crea_la_sesion_y_persiste(self, client):
        from src.agent.core import Agent
        from src.agent.sessions import SessionStore
        from src.models.mock import MockLLMProvider

        container = get_container()
        original = container.agent

        modelo = MockLLMProvider()
        modelo.queue_text("Hola, soy Morgan")
        container.agent = Agent(
            model=modelo,
            tool_registry=container.tool_registry,
            permission_manager=container.permission_manager,
            sessions=SessionStore(),
            conversation_history=container.conversation_history,
        )

        try:
            r = client.post("/chat", json={"message": "Buenas", "session_id": "ses-chat"})
            assert r.status_code == 200

            mensajes = client.get("/sessions/ses-chat/messages").json()["messages"]
            assert [(m["role"], m["content"]) for m in mensajes] == [
                ("user", "Buenas"),
                ("assistant", "Hola, soy Morgan"),
            ]
        finally:
            container.agent = original
