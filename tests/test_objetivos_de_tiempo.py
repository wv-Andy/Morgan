"""
Los objetivos de tiempo por tipo de turno (2.3-D).

Salen de medir 15 turnos reales con el modelo real (docs/mediciones.md,
apartado 5) y viven en `src/observabilidad.py`. Lo que se fija aquí:

- **El tipo es lo que pasó**, no lo que se pidió: un turno que crea un plan es de
  plan aunque también haya buscado algo.
- **La alarma salta solo al pasarse**, y lleva el reparto: un aviso sin reparto
  obliga a reproducir el turno para saber algo.
- **La alarma está conectada de verdad**: la dispara `/chat/stream`, que es lo
  que usa la web. Si alguien la quita de la ruta, la función seguiría probada y
  producción callada.
- **La parte de Morgan cabe con holgura**: con un modelo instantáneo, un turno
  simple tiene que quedarse muy por debajo de su objetivo. Si no, el objetivo ya
  no depende del modelo y hay que mirarlo antes de culpar a Groq.
"""

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from src.models.base import LLMResponse, ToolCallRequest
from src.observabilidad import OBJETIVOS_TURNO, Medicion, tipo_de_turno, vigilar_turno


def _medicion(*herramientas: str) -> Medicion:
    medicion = Medicion()
    for nombre in herramientas:
        medicion.sumar(f"herramienta.{nombre}", 0.1)
    medicion.sumar("modelo", 0.5)
    return medicion


class TestElTipoEsLoQuePaso:
    def test_sin_herramientas_es_simple(self):
        assert tipo_de_turno(_medicion()) == "simple"

    def test_con_una_herramienta_es_de_herramienta(self):
        assert tipo_de_turno(_medicion("search_web")) == "herramienta"

    def test_si_crea_un_plan_es_de_plan_aunque_busque_antes(self):
        assert tipo_de_turno(_medicion("search_web", "create_plan")) == "plan"


class TestLaAlarma:
    def test_dentro_del_objetivo_no_dice_nada(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.observabilidad"):
            assert vigilar_turno(_medicion(), OBJETIVOS_TURNO["simple"] - 0.1) is None
        assert not caplog.records

    def test_fuera_avisa_con_el_tipo_el_objetivo_y_el_reparto(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.observabilidad"):
            assert vigilar_turno(_medicion("search_web"), 9.0) == "herramienta"

        mensaje = caplog.records[-1].getMessage()
        assert "fuera de objetivo" in mensaje
        assert "herramienta, 9.0 s (objetivo 6 s)" in mensaje
        assert "modelo=" in mensaje, "sin reparto no se sabe por qué"

    def test_sin_medicion_no_revienta(self):
        assert vigilar_turno(None, 999) is None


@pytest.fixture
def web(monkeypatch, modelo_simulado):
    from src.api import dependencies
    from src.api.app import create_app
    from src.config import reset_settings

    monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    reset_settings()
    dependencies.reset_container()
    cliente = TestClient(create_app(), base_url="https://testserver")
    r = cliente.post("/auth/registro", json={
        "username": "ana", "email": "ana@ejemplo.co", "password": "contrasena-larga",
    })
    assert r.status_code == 200, r.text
    cliente.headers["X-Morgan-CSRF"] = r.json()["csrf"]
    yield cliente
    reset_settings()
    dependencies.reset_container()


class TestConectadaALaRuta:
    def test_un_turno_de_plan_lento_deja_el_aviso(self, web, modelo_simulado, monkeypatch, caplog):
        import src.observabilidad as observabilidad

        # Objetivo a cero: cualquier turno se pasa. Lo que se prueba es que la
        # ruta llama a la alarma y que clasifica el turno como de plan.
        monkeypatch.setitem(observabilidad.OBJETIVOS_TURNO, "plan", 0.0)
        modelo_simulado.queue_response(LLMResponse(type="tool_call", tool_calls=[ToolCallRequest(
            name="create_plan", id="p1",
            arguments={"objetivo": "ordenar", "pasos": [{"descripcion": "preguntar", "herramienta": None}]},
        )]))
        modelo_simulado.queue_text("Te propongo un plan.")

        with caplog.at_level(logging.WARNING, logger="src.observabilidad"):
            r = web.post("/chat/stream", json={"message": "ordena mis apuntes", "session_id": "s1"})
            eventos = [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]
            assert eventos[-1]["tipo"] == "fin"
            # El aviso sale en el hilo del turno, justo antes de la marca de fin.
            for _ in range(50):
                if any("fuera de objetivo" in x.getMessage() for x in caplog.records):
                    break
                time.sleep(0.02)

        avisos = [x.getMessage() for x in caplog.records if "fuera de objetivo" in x.getMessage()]
        assert avisos and avisos[0].startswith("Turno fuera de objetivo: plan,")

    def test_la_parte_de_morgan_en_un_turno_simple_cabe_con_holgura(self, web, modelo_simulado):
        """Con un modelo instantáneo, lo que queda es Morgan: base, sesiones,
        prompt y streaming. Tiene que dejar casi todo el objetivo al modelo."""
        tiempos = []
        for n in range(5):
            modelo_simulado.queue_text("Hola.")
            inicio = time.perf_counter()
            r = web.post("/chat/stream", json={"message": "hola", "session_id": f"h{n}"})
            tiempos.append(time.perf_counter() - inicio)
            assert r.status_code == 200

        assert max(tiempos) < OBJETIVOS_TURNO["simple"] / 3, (
            f"Morgan solo ya tarda {max(tiempos):.2f} s en un turno simple"
        )
