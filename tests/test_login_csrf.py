"""
El «login CSRF» (4.22): una web ajena ya no puede hacerte entrar en SU cuenta.

Las rutas de entrada (entrar, registrarse, recuperar, restablecer, salir) no pueden exigir el
doble envío del CSRF: sin sesión no hay token que repetir. Hasta la 4.22 eso se aceptaba a
sabiendas (docs/autenticacion.md): una página ajena podía mandar desde tu navegador un
formulario a `/auth/login` con las credenciales del atacante, y entrabas en su cuenta sin darte
cuenta. Ahora esas rutas miran `Origin`, que el navegador pone siempre y la página no puede
cambiar: si no es la web de Morgan, 403.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import reset_settings

WEB = "https://morgan.ejemplo.co"
CUENTA = {"username": "ana", "email": "ana@ejemplo.co", "password": "contrasena-larga"}


@pytest.fixture
def nube(monkeypatch):
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_WEB_URL", WEB)
    monkeypatch.setenv("MORGAN_CORS_ORIGINS", WEB)
    monkeypatch.setenv("MORGAN_CORS_ORIGIN_REGEX", r"https://morgan-[a-z0-9]+\.vercel\.app")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()


def _registro(cliente, origen=None):
    cabeceras = {"Origin": origen} if origen is not None else {}
    return cliente.post("/auth/registro", json=CUENTA, headers=cabeceras)


class TestDesdeOtraWeb:
    @pytest.mark.parametrize("origen", ["https://malvada.ejemplo", "null", "http://morgan.ejemplo.co",
                                        "https://morgan.ejemplo.co.malvada.ejemplo",
                                        "https://morgan-abc123.vercel.app.malvada.ejemplo"])
    def test_entrar_desde_otra_pagina_no(self, nube, origen):
        _registro(nube, WEB)
        nube.cookies.clear()
        r = nube.post("/auth/login", json={"identificador": "ana", "password": CUENTA["password"]},
                      headers={"Origin": origen})
        assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGEN_AJENO"
        assert "morgan_sesion" not in r.cookies

    @pytest.mark.parametrize("ruta", ["/auth/registro", "/auth/recuperar", "/auth/restablecer", "/auth/logout"])
    def test_ni_las_demas_rutas_de_entrada(self, nube, ruta):
        r = nube.post(ruta, json={}, headers={"Origin": "https://malvada.ejemplo"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGEN_AJENO"


class TestDesdeMorgan:
    def test_desde_la_web_de_morgan_si(self, nube):
        assert _registro(nube, WEB).status_code == 200

    def test_desde_una_vista_previa_de_vercel_si(self, nube):
        assert _registro(nube, "https://morgan-abc123.vercel.app").status_code == 200

    def test_desde_la_misma_direccion_que_la_api_si(self, nube):
        """La API sirviendo la web ella misma (Morgan en local, `/`): el origen es el suyo."""
        assert _registro(nube, "http://testserver").status_code == 200

    def test_sin_origin_tambien(self, nube):
        """Un programa o la línea de comandos: sin navegador, no hay a quién engañar."""
        assert _registro(nube).status_code == 200

    def test_leer_no_se_frena(self, nube):
        assert nube.get("/auth/yo", headers={"Origin": "https://malvada.ejemplo"}).status_code == 200
