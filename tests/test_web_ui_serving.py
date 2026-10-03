"""
Pruebas de la publicación de la interfaz web desde la propia API.

Verifican que servir la SPA no ensombrece ninguna ruta de la API: una ruta
desconocida bajo un prefijo de API debe seguir devolviendo 404 JSON, no HTML.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.static import mount_web_ui


@pytest.fixture
def dist(tmp_path):
    """Un build de frontend mínimo pero realista."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Morgan</title>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log('morgan')", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return d


@pytest.fixture
def app_with_ui(dist):
    app = FastAPI()

    @app.get("/status")
    def _status():
        return {"status": "ready"}

    @app.get("/tools/{name}")
    def _tool(name: str):
        return {"tool": name}

    mount_web_ui(app, dist_dir=dist)
    return TestClient(app)


class TestMontaje:
    def test_sin_build_no_monta_nada(self, tmp_path):
        app = FastAPI()

        assert mount_web_ui(app, dist_dir=tmp_path / "no-existe") is False

    def test_con_build_monta(self, dist):
        assert mount_web_ui(FastAPI(), dist_dir=dist) is True


class TestServidoDeLaInterfaz:
    def test_la_raiz_devuelve_el_html(self, app_with_ui):
        r = app_with_ui.get("/")

        assert r.status_code == 200
        assert "Morgan" in r.text

    def test_sirve_los_assets(self, app_with_ui):
        r = app_with_ui.get("/assets/app.js")

        assert r.status_code == 200
        assert "morgan" in r.text

    def test_sirve_archivos_sueltos_del_build(self, app_with_ui):
        assert app_with_ui.get("/favicon.svg").status_code == 200

    def test_una_ruta_de_la_spa_devuelve_el_html(self, app_with_ui):
        r = app_with_ui.get("/cualquier/ruta/de/la/interfaz")

        assert r.status_code == 200
        assert "Morgan" in r.text


class TestNoEnsombreceLaApi:
    def test_las_rutas_de_la_api_siguen_respondiendo(self, app_with_ui):
        r = app_with_ui.get("/status")

        assert r.status_code == 200
        assert r.json() == {"status": "ready"}

    def test_ruta_con_parametro_sigue_funcionando(self, app_with_ui):
        assert app_with_ui.get("/tools/system_info").json() == {"tool": "system_info"}

    @pytest.mark.parametrize("ruta", ["/health/inventado", "/memory/x/y", "/audit/nope", "/chat/nope"])
    def test_ruta_desconocida_de_api_es_404_json(self, app_with_ui, ruta):
        """No debe devolverse el HTML de la SPA para un prefijo de API."""
        r = app_with_ui.get(ruta)

        assert r.status_code == 404
        cuerpo = r.json()
        assert cuerpo["success"] is False
        assert cuerpo["error"]["code"] == "NOT_FOUND"

    def test_no_permite_salir_del_directorio_del_build(self, app_with_ui):
        """Una ruta con .. no debe servir archivos de fuera de dist."""
        r = app_with_ui.get("/../../../src/config.py")

        # Se normaliza o cae en la SPA, pero nunca devuelve el fichero de Python.
        assert "get_settings" not in r.text


class TestLaRaizCuandoNoHayInterfaz:
    """Sintoma real: al abrir la URL del backend desplegado en Render, el
    navegador mostraba {"detail": "Not Found"}. El servicio estaba perfectamente,
    pero ese 404 escueto parece un despliegue roto."""

    @staticmethod
    def _cliente(monkeypatch, entorno="local"):
        import src.config as config

        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_ENVIRONMENT", entorno)
        if entorno == "cloud":
            monkeypatch.setenv("MORGAN_API_TOKEN", "")
        config.reset_settings()
        from src.api.app import create_app

        return TestClient(create_app())

    def test_la_raiz_explica_que_es_esto(self, monkeypatch):
        respuesta = self._cliente(monkeypatch).get("/")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["service"] == "Morgan API"
        assert "/health" in cuerpo["endpoints"].values()

    def test_en_cloud_dice_que_la_interfaz_esta_en_otro_sitio(self, monkeypatch):
        cuerpo = self._cliente(monkeypatch, entorno="cloud").get("/").json()

        assert cuerpo["environment"] == "cloud"
        assert "interfaz" in cuerpo["message"].lower()

    def test_no_expone_ningun_secreto(self, monkeypatch):
        """La raiz no pide token, asi que su contenido es publico."""
        texto = self._cliente(monkeypatch).get("/").text.lower()

        for prohibido in ("key", "token=", "secret", "password", "supabase"):
            assert prohibido not in texto

    def test_si_hay_build_la_raiz_sigue_sirviendo_la_pagina(self, dist):
        """El descriptor no debe robarle la raiz a la interfaz."""
        from src.api.static import mount_api_root

        app = FastAPI()
        montada = mount_web_ui(app, dist_dir=dist)
        if not montada:
            mount_api_root(app)

        assert montada
        respuesta = TestClient(app).get("/")
        assert "<!doctype html>" in respuesta.text.lower()
