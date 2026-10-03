"""
Transcribir un audio para poder revisarlo antes de enviarlo.

**Por qué existe esta ruta, si Morgan ya sabía transcribir.** Sabía, pero como
herramienta: la ejecutaba *dentro* del turno. Lo primero que veías de tu propia
grabación era la respuesta a algo que no habías podido leer, y si Whisper
entendía mal una palabra —con nombres propios y términos técnicos pasa— Morgan
respondía a otra pregunta sin que hubiera forma de saber por qué.

Hay además un efecto de seguridad que conviene nombrar: lo que se dice en un
audio es contenido no confiable, igual que una página web. Por el camino
anterior entraba directo al contexto del modelo. Ahora lo lee una persona antes,
y al enviarlo pasa a ser *su* mensaje: un intento de inyección por audio deja de
ser invisible.
"""

import pytest
from fastapi.testclient import TestClient

from src import config

TOKEN = "token-de-prueba"
CABECERA = {"Authorization": f"Bearer {TOKEN}"}

# Un PNG mínimo válido, para el caso «esto no es un audio».
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
WAV = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 32


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_API_TOKEN", TOKEN)
    config.reset_settings()
    from src.api import dependencies
    from src.api.app import create_app

    dependencies.reset_container()
    yield TestClient(create_app())
    config.reset_settings()
    dependencies.reset_container()


def _subir(api, nombre: str, contenido: bytes) -> str:
    respuesta = api.post("/uploads", files={"file": (nombre, contenido)}, headers=CABECERA)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["id"]


class TestLaRutaEstaProtegida:
    """Un audio es contenido del usuario. Transcribirlo cuesta cupo y devuelve
    lo que esa persona dijo: no puede quedar abierto."""

    def test_sin_credencial_no_se_pasa(self, api):
        assert api.post("/uploads/lo-que-sea/transcripcion").status_code == 401

    def test_un_archivo_que_no_existe_da_404(self, api):
        respuesta = api.post("/uploads/no-existe/transcripcion", headers=CABECERA)

        assert respuesta.status_code == 404


class TestLoQueNoEsAudioSeRechaza:
    def test_una_imagen_no_se_transcribe(self, api):
        """Y el código lo dice: 409, no 400. La petición está bien formada; es
        el archivo el que no admite esta operación."""
        upload_id = _subir(api, "foto.png", PNG)

        respuesta = api.post(f"/uploads/{upload_id}/transcripcion", headers=CABECERA)

        assert respuesta.status_code == 409
        assert "audio" in respuesta.json()["error"]["message"].lower()


class TestElTextoLlegaLimpio:
    """El envoltorio `<untrusted_file_data>` existe para que el MODELO sepa que
    lo de dentro es dato y no instrucción. Aquí lo lee una persona y se le pega
    en el compositor, así que ahí solo sería ruido."""

    def test_se_quita_el_marcado(self):
        from src.api.routes.uploads import _sin_envoltorio

        crudo = (
            '<untrusted_file_data source="grabacion.webm">\n'
            "Hola, esto es una prueba.\n"
            "</untrusted_file_data>"
        )

        assert _sin_envoltorio(crudo) == "Hola, esto es una prueba."

    def test_un_texto_sin_marcado_pasa_igual(self):
        from src.api.routes.uploads import _sin_envoltorio

        assert _sin_envoltorio("  Hola  ") == "Hola"

    def test_no_se_come_el_texto_si_lleva_signos_de_mayor(self):
        """El marcado se corta por el primer `>`, que es el que cierra la
        etiqueta. Un `>` dentro del texto transcrito no debe perder nada."""
        from src.api.routes.uploads import _sin_envoltorio

        crudo = (
            '<untrusted_file_data source="a.webm">\n'
            "Si a > b entonces gana a.\n"
            "</untrusted_file_data>"
        )

        assert _sin_envoltorio(crudo) == "Si a > b entonces gana a."


class TestCuandoNoHayServicio:
    """En la nube el catálogo de herramientas es otro. Si `transcribe_audio` no
    está registrada, decirlo es más útil que un 500."""

    def test_sin_la_herramienta_se_responde_503(self, api, monkeypatch):
        from src.api.dependencies import get_container

        contenedor = get_container()
        upload_id = _subir(api, "voz.wav", WAV)

        monkeypatch.setattr(
            contenedor.tool_registry, "list_tools", lambda: [],
        )

        respuesta = api.post(f"/uploads/{upload_id}/transcripcion", headers=CABECERA)

        assert respuesta.status_code == 503
        assert respuesta.json()["error"]["code"] == "SIN_TRANSCRIPCION"


class TestElCupoSeConsumeIgual:
    """La transcripción cuesta lo mismo se pida desde donde se pida. Si esta
    ruta no gastara cupo, sería la forma de saltárselo."""

    def test_agotado_el_cupo_se_responde_429(self, api, monkeypatch):
        from src.api.dependencies import get_container
        from src.identidad.cuotas import CuotaAgotada

        contenedor = get_container()
        upload_id = _subir(api, "voz.wav", WAV)

        def sin_cupo(*_args, **_kwargs):
            raise CuotaAgotada("transcripciones", 20)

        monkeypatch.setattr(contenedor.uso, "apuntar", sin_cupo)

        respuesta = api.post(f"/uploads/{upload_id}/transcripcion", headers=CABECERA)

        assert respuesta.status_code == 429
