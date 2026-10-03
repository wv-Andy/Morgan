"""
La sonda del canal de la 3.0-B (`src/api/routes/sonda_canal.py`).

Es un instrumento, pero retiene conexiones abiertas: lo primero que se fija es que
**en producción no existe**. Después, que mide lo que dice medir con los cuatro
canales, que el socket **no deja entrar sin token** (los middlewares HTTP no llegan
a un WebSocket), que un cliente mudo se detecta y que dos personas no comparten
buzón.
"""

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.app import create_app
from src.config import reset_settings

CLAVE = "contrasena-larga"


@pytest.fixture
def prueba(monkeypatch):
    """Un despliegue de prueba: cuentas exigidas y el modo de prueba de carga."""
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_PRUEBA_DE_CARGA", "1")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    from src.api.routes import sonda_canal

    sonda_canal._buzones.clear()
    sonda_canal._finales.clear()
    yield create_app()
    reset_settings()


def _token(app, nombre: str, alcances=("lectura", "escritura")) -> str:
    web = TestClient(app)
    r = web.post("/auth/registro", json={
        "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
    }, headers={"X-Forwarded-For": f"10.1.{len(nombre)}.{sum(map(ord, nombre)) % 250}"})
    assert r.status_code == 200, r.text
    r = web.post(
        "/auth/tokens", json={"nombre": "sonda", "alcances": list(alcances)},
        headers={"x-morgan-csrf": r.json()["csrf"]},
    )
    assert r.status_code == 200, r.text
    return r.json()["valor"]


def _cliente(app, token: str) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def _rutas(app) -> set[str]:
    """Las rutas HTTP montadas. Por el esquema, no por `app.routes`: esta versión de
    FastAPI envuelve cada router y `app.routes` no las enseña. La primera versión de
    estas pruebas miraba ahí y la de producción pasaba sin comprobar nada."""
    return set(app.openapi()["paths"])


class TestSoloEnElDespliegueDePrueba:
    def test_en_produccion_no_existe(self, monkeypatch):
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        app = create_app()
        token = _token(app, "ana")

        assert "/auth/tokens" in _rutas(app)  # la comprobación ve rutas de verdad
        assert not any(r.startswith("/sonda-canal") for r in _rutas(app))
        # Y el socket, que no sale en el esquema: con un token bueno, no hay nada.
        with pytest.raises(WebSocketDisconnect):
            with _cliente(app, token).websocket_connect("/sonda-canal/s1/ws") as ws:
                ws.receive_json()

    def test_con_el_supabase_de_produccion_tampoco(self, monkeypatch):
        import hashlib

        from src import prueba_de_carga

        # Un proyecto inventado con su huella: en el código solo está la del de verdad.
        monkeypatch.setattr(prueba_de_carga, "HUELLA_DE_PRODUCCION", hashlib.sha256(b"proyectodeproduccion").hexdigest())
        monkeypatch.setenv("MORGAN_PRUEBA_DE_CARGA", "1")
        monkeypatch.setenv("SUPABASE_URL", "https://proyectodeproduccion.supabase.co")
        reset_settings()

        assert not any(r.startswith("/sonda-canal") for r in _rutas(create_app()))

    def test_en_el_de_prueba_si(self, prueba):
        assert "/sonda-canal/{sesion}/emitir" in _rutas(prueba)


class TestLosCanalesEntreganYMiden:
    def test_sondeo_largo(self, prueba):
        c = _cliente(prueba, _token(prueba, "ana"))

        ident = c.post("/sonda-canal/s1/emitir").json()["id"]
        mensaje = c.get("/sonda-canal/s1/esperar?max=5").json()["mensaje"]
        assert mensaje["id"] == ident

        ms = c.post("/sonda-canal/s1/respuesta", json={"id": ident, "canal": "sondeo"}).json()["ms"]
        assert ms is not None and ms >= 0
        assert c.get("/sonda-canal/s1/resultados").json()["idas_y_vueltas"][0]["id"] == ident

    def test_sondeo_corto_sin_mensajes_vuelve_al_momento(self, prueba):
        c = _cliente(prueba, _token(prueba, "ana"))

        inicio = time.monotonic()
        assert c.get("/sonda-canal/s1/esperar?max=0").json()["mensaje"] is None
        assert time.monotonic() - inicio < 2

    def test_streaming(self, prueba, monkeypatch):
        from src.api.routes import sonda_canal

        monkeypatch.setattr(sonda_canal, "DURACION_MAXIMA", 1.5)
        c = _cliente(prueba, _token(prueba, "ana"))
        ident = c.post("/sonda-canal/s1/emitir").json()["id"]

        texto = c.get("/sonda-canal/s1/flujo?latido=0.5").text

        assert '"tipo": "inicio"' in texto.splitlines()[0]
        assert ident in texto
        assert '"tipo": "latido"' in texto

    def test_websocket(self, prueba):
        token = _token(prueba, "ana")
        c = _cliente(prueba, token)

        with c.websocket_connect("/sonda-canal/s1/ws?latido=5") as ws:
            assert ws.receive_json()["tipo"] == "inicio"
            ident = c.post("/sonda-canal/s1/emitir").json()["id"]
            assert ws.receive_json()["id"] == ident
            ws.send_json({"tipo": "respuesta", "id": ident})
            anotada = ws.receive_json()

        assert anotada["tipo"] == "anotada" and anotada["ms"] is not None

    def test_el_buzon_lleno_rechaza(self, prueba):
        from src.api.routes.sonda_canal import MENSAJES_POR_BUZON

        c = _cliente(prueba, _token(prueba, "ana"))
        for _ in range(MENSAJES_POR_BUZON):
            assert c.post("/sonda-canal/s1/emitir").status_code == 200

        assert c.post("/sonda-canal/s1/emitir").status_code == 429


class TestElSocketNoDejaEntrarSinToken:
    """Los middlewares son solo HTTP: si el socket no lo comprueba, nadie lo hace."""

    def test_sin_token(self, prueba):
        with pytest.raises(WebSocketDisconnect) as fallo:
            with TestClient(prueba).websocket_connect("/sonda-canal/s1/ws") as ws:
                ws.receive_json()
        assert fallo.value.code == 4401

    def test_con_un_token_inventado(self, prueba):
        with pytest.raises(WebSocketDisconnect) as fallo:
            with _cliente(prueba, "mgn_inventado").websocket_connect("/sonda-canal/s1/ws") as ws:
                ws.receive_json()
        assert fallo.value.code == 4401

    def test_con_un_token_sin_alcance_de_lectura(self, prueba):
        token = _token(prueba, "ana", alcances=("chat",))
        with pytest.raises(WebSocketDisconnect) as fallo:
            with _cliente(prueba, token).websocket_connect("/sonda-canal/s1/ws") as ws:
                ws.receive_json()
        assert fallo.value.code == 4401

    def test_las_rutas_http_piden_identidad(self, prueba):
        assert TestClient(prueba).post("/sonda-canal/s1/emitir").status_code == 401


class TestUnClienteMudoSeDetecta:
    def test_se_cierra_tras_varios_latidos_sin_noticias(self, prueba):
        c = _cliente(prueba, _token(prueba, "ana"))

        inicio = time.monotonic()
        with pytest.raises(WebSocketDisconnect) as fallo:
            with c.websocket_connect("/sonda-canal/s1/ws?latido=0.2") as ws:
                while True:
                    ws.receive_json()  # escucha, pero no contesta nunca
        assert fallo.value.code == 4408
        assert time.monotonic() - inicio < 5
        finales = c.get("/sonda-canal/s1/resultados").json()["finales"]
        assert "mudo" in finales[-1]["motivo"]

    def test_el_que_contesta_sigue_vivo(self, prueba):
        c = _cliente(prueba, _token(prueba, "ana"))

        with c.websocket_connect("/sonda-canal/s1/ws?latido=0.2") as ws:
            ws.receive_json()
            for _ in range(8):  # ocho latidos: más del doble del umbral
                assert ws.receive_json()["tipo"] == "latido"
                ws.send_json({"tipo": "pong"})


class TestCadaUnoSuBuzon:
    def test_b_no_recibe_lo_emitido_para_a(self, prueba):
        a = _cliente(prueba, _token(prueba, "ana"))
        b = _cliente(prueba, _token(prueba, "bruno"))

        a.post("/sonda-canal/s1/emitir")

        assert b.get("/sonda-canal/s1/esperar?max=0").json()["mensaje"] is None
        assert a.get("/sonda-canal/s1/esperar?max=0").json()["mensaje"] is not None

    def test_la_sesion_se_valida(self, prueba):
        c = _cliente(prueba, _token(prueba, "ana"))

        assert c.post("/sonda-canal/" + "x" * 41 + "/emitir").status_code == 400


class TestLaOrdenAlAgente:
    def test_sin_agente_conectado_lo_dice(self, prueba, monkeypatch):
        from src.canal import despacho
        from src.canal.registro import REGISTRO

        REGISTRO.reiniciar()
        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 0.3)
        c = _cliente(prueba, _token(prueba, "ana"))

        r = c.post("/sonda-canal/orden-agente/estado")

        assert r.status_code == 409 and r.json()["error"]["code"] == "AgenteNoConectado"

    def test_solo_en_el_despliegue_de_prueba(self, prueba):
        assert "/sonda-canal/orden-agente/{capacidad}" in _rutas(prueba)

