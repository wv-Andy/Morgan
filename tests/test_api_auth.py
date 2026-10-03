"""
Pruebas de la autenticación por token de Morgan API.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import extract_token, generate_token, install_token_auth

TOKEN = "token-de-prueba-muy-secreto"


def _build_app(token: str | None):
    app = FastAPI()

    @app.get("/health")
    def _health():
        return {"status": "ok"}

    @app.get("/status")
    def _status():
        return {"status": "ready"}

    @app.post("/chat")
    def _chat():
        return {"response": "hola"}

    @app.get("/tools")
    def _tools():
        return {"total": 25}

    @app.get("/")
    def _index():
        return {"ui": True}

    install_token_auth(app, token)
    return TestClient(app)


class TestSinTokenConfigurado:
    """Sin MORGAN_API_TOKEN el comportamiento no cambia: uso local."""

    def test_no_se_exige_nada(self):
        client = _build_app(None)

        assert client.get("/status").status_code == 200
        assert client.post("/chat").status_code == 200

    def test_install_devuelve_false(self):
        assert install_token_auth(FastAPI(), None) is False


class TestConTokenConfigurado:
    @pytest.fixture
    def client(self):
        return _build_app(TOKEN)

    def test_sin_cabecera_devuelve_401(self, client):
        r = client.get("/status")

        assert r.status_code == 401
        cuerpo = r.json()
        assert cuerpo["success"] is False
        assert cuerpo["error"]["code"] == "UNAUTHORIZED"
        assert r.headers.get("www-authenticate") == "Bearer"

    def test_token_incorrecto_devuelve_401(self, client):
        r = client.get("/status", headers={"Authorization": "Bearer token-equivocado"})

        assert r.status_code == 401

    def test_token_correcto_por_bearer(self, client):
        r = client.get("/status", headers={"Authorization": f"Bearer {TOKEN}"})

        assert r.status_code == 200

    def test_token_correcto_por_cabecera_propia(self, client):
        r = client.get("/status", headers={"X-Morgan-Token": TOKEN})

        assert r.status_code == 200

    def test_bearer_es_insensible_a_mayusculas(self, client):
        r = client.get("/status", headers={"Authorization": f"bearer {TOKEN}"})

        assert r.status_code == 200

    @pytest.mark.parametrize("ruta,metodo", [("/status", "get"), ("/chat", "post"), ("/tools", "get")])
    def test_todas_las_rutas_de_api_quedan_protegidas(self, client, ruta, metodo):
        assert getattr(client, metodo)(ruta).status_code == 401

    def test_health_sigue_abierta(self, client):
        """Permite comprobar disponibilidad sin repartir el token."""
        assert client.get("/health").status_code == 200

    def test_la_interfaz_web_sigue_accesible(self, client):
        """Los archivos de la interfaz por si solos no permiten hacer nada."""
        assert client.get("/").status_code == 200

    def test_un_prefijo_parecido_no_burla_la_proteccion(self, client):
        """'/statuses' no debe colarse por empezar igual que '/status'."""
        r = client.get("/statuses")

        # No existe la ruta, pero lo importante es que no devuelva datos.
        assert r.status_code in (401, 404)


class TestExtraccionDelToken:
    def test_desde_bearer(self):
        req = type("R", (), {"headers": {"authorization": f"Bearer {TOKEN}"}})()
        assert extract_token(req) == TOKEN

    def test_desde_cabecera_propia(self):
        req = type("R", (), {"headers": {"x-morgan-token": TOKEN}})()
        assert extract_token(req) == TOKEN

    def test_sin_cabeceras(self):
        req = type("R", (), {"headers": {}})()
        assert extract_token(req) is None

    def test_bearer_vacio(self):
        req = type("R", (), {"headers": {"authorization": "Bearer "}})()
        assert extract_token(req) is None


class TestGeneradorDeToken:
    def test_genera_tokens_distintos_y_largos(self):
        a, b = generate_token(), generate_token()

        assert a != b
        assert len(a) >= 32
