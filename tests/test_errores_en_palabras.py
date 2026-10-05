"""
Los errores de la API, en palabras que entiende cualquiera (4.23).

Antes, alguien nuevo podía leer «Not Found», «Method Not Allowed», «Datos de petición no válidos
según el esquema» o «Consulta logs/morgan.log para el detalle» (un fichero que en la nube nadie
tiene). Aquí se pide a la API cada clase de error y se mira el `message`, que es lo que la web
enseña: sin jerga, y diciendo qué hacer. El `code` sigue igual para los programas.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.errores_en_palabras import en_palabras

#: Lo que delata un mensaje técnico.
JERGA = ("log", "esquema", "schema", "not found", "not allowed", "internal server", "exception",
         "traceback", "api", "servidor en ejecución", "http")


def _limpio(mensaje: str) -> bool:
    bajo = mensaje.lower()
    return mensaje and not any(j in bajo for j in JERGA) and mensaje[0].isupper()


@pytest.fixture
def cliente():
    return TestClient(app, raise_server_exceptions=False)


def _mensaje(r) -> str:
    cuerpo = r.json()
    return (cuerpo.get("error") or {}).get("message") or ""


def test_el_404_de_siempre():
    """Las rutas que no existen tienen su propio mensaje (para quien programa contra la API); el
    404 por defecto de lo demás («Not Found») ya no sale tal cual."""
    assert _limpio(en_palabras(404, "Not Found")) and "borrado" in en_palabras(404, "Not Found")


def test_un_metodo_que_no_vale(cliente):
    r = cliente.delete("/health")
    assert r.status_code == 405
    assert _limpio(_mensaje(r))


def test_datos_que_no_valen(cliente):
    r = cliente.post("/memory", json={"key": "solo la clave"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _limpio(_mensaje(r)) and "Revísalo" in _mensaje(r)
    assert r.json()["error"]["details"], "el detalle sigue ahí para quien programa"


def test_un_fallo_inesperado_no_manda_a_un_fichero(cliente, monkeypatch):
    from src.api import dependencies

    def revienta():
        raise RuntimeError("C:/ruta/secreta del disco")

    app.dependency_overrides[dependencies.get_container] = revienta
    try:
        r = cliente.get("/memory")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert _limpio(_mensaje(r)) and "ruta" not in _mensaje(r)


def test_un_mensaje_de_morgan_se_respeta():
    assert en_palabras(409, "Ya tienes 50 espacios de trabajo, el máximo.") == "Ya tienes 50 espacios de trabajo, el máximo."
    assert en_palabras(404, "Not Found") != "Not Found"
    assert en_palabras(418, "I'm a Teapot") and _limpio(en_palabras(418, "I'm a Teapot"))
    assert _limpio(en_palabras(599, ""))


def test_el_fallo_de_un_turno_tampoco():
    from src.api.errores_en_palabras import ERROR_EN_EL_TURNO

    assert _limpio(ERROR_EN_EL_TURNO)
    texto = open("src/api/routes/chat.py", encoding="utf-8").read()
    assert "logs/morgan.log" not in texto and texto.count("ERROR_EN_EL_TURNO") >= 3
