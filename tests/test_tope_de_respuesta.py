"""
Dejar de esperar un turno largo, sin tirar el trabajo.

**Por qué existe.** En la nube la web llega a la API por el proxy del borde de
Vercel —hace falta para que la cookie de sesión no sea de terceros, ver
`docs/web.md` apartado 9— y ese proxy corta a los 120 s con una
página de error suya que no dice de qué va.

El plazo del agente no bastaba, y hay medición que lo demuestra: se miraba
**entre pasos**, así que no acotaba lo que tarda un paso que ya arrancó. Con el
turno en 85 s, tres de cinco peticiones seguidas murieron igual a los 120,1 s.
Una búsqueda lenta, o una llamada al modelo con reintento y conmutación de
proveedor, se comen el margen sin preguntar.

Esto es de otra naturaleza: no le pide al agente que se dé prisa, **deja de
esperarle**. El turno sigue vivo, termina, y su respuesta se guarda como
siempre.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.agent.core import Agent
from src.agent.sessions import SessionStore
from src.api.app import app
from src.api.dependencies import get_container
from src.api.routes.chat import _sin_esperar_de_mas
from src.models.base import LLMProvider, LLMResponse


class ProveedorTardon(LLMProvider):
    """Un turno que se pasa del tope pase lo que pase."""

    def __init__(self, retardo: float):
        self.retardo = retardo
        self.termino = threading.Event()

    @property
    def model_name(self) -> str:
        return "tardon"

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        time.sleep(self.retardo)
        self.termino.set()
        return LLMResponse(type="text", content="tarde, pero aquí está")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def agente_tardon(monkeypatch):
    """Sustituye el agente por uno lento y lo devuelve todo a su sitio al salir."""

    def montar(retardo: float, tope: int):
        container = get_container()
        original = container.agent
        modelo = ProveedorTardon(retardo)
        container.agent = Agent(
            model=modelo,
            tool_registry=container.tool_registry,
            permission_manager=container.permission_manager,
            sessions=SessionStore(),
            # Sin esto el agente no guarda nada, y la prueba de que el turno
            # abandonado deja su respuesta pasaría por otra razón: porque no hay
            # nada que guardar.
            conversation_history=container.conversation_history,
        )

        # `Settings` es inmutable a propósito, así que se hace una copia con el
        # tope puesto en vez de tocar la de verdad.
        from dataclasses import replace

        from src.api.routes import chat as ruta_chat

        ajustes = replace(ruta_chat.get_settings(), http_deadline=tope)
        monkeypatch.setattr(ruta_chat, "get_settings", lambda: ajustes)

        def devolver():
            container.agent = original

        return modelo, devolver

    return montar


class TestSeDejaDeEsperarPeroNoSeTira:
    def test_contesta_dentro_del_tope_en_vez_de_colgarse(self, client, agente_tardon):
        modelo, devolver = agente_tardon(retardo=2.0, tope=1)
        try:
            inicio = time.monotonic()
            r = client.post("/chat", json={"message": "algo largo", "session_id": "tope-1"})
            transcurrido = time.monotonic() - inicio

            assert r.status_code == 200
            assert transcurrido < 1.8, (
                f"Tardó {transcurrido:.1f} s con un tope de 1 s: no se dejó de esperar"
            )
            assert "Sigo trabajando" in r.json()["response"]
        finally:
            modelo.termino.wait(5)
            devolver()

    def test_el_turno_no_se_mata_al_dejar_de_esperarlo(self, client, agente_tardon):
        """El turno ya está pagado al proveedor: tirarlo sería tirar dinero."""
        modelo, devolver = agente_tardon(retardo=1.5, tope=1)
        try:
            client.post("/chat", json={"message": "algo largo", "session_id": "tope-2"})
            assert modelo.termino.wait(5), "El turno se murió al dejar de esperarlo"
        finally:
            devolver()

    def test_no_dice_que_ha_fallado_porque_no_ha_fallado(self, client, agente_tardon):
        """El aviso decide si la persona lo repite y paga el modelo dos veces."""
        modelo, devolver = agente_tardon(retardo=1.5, tope=1)
        try:
            cuerpo = client.post(
                "/chat", json={"message": "algo largo", "session_id": "tope-3"}
            ).json()

            assert cuerpo["success"] is True
            texto = cuerpo["response"].lower()
            assert "error" not in texto
            assert "no hace falta que me lo repitas" in texto
        finally:
            modelo.termino.wait(5)
            devolver()

    def test_sin_tope_se_espera_lo_que_haga_falta(self, client, agente_tardon):
        """En local no hay proxy, y cortar sería empeorar por nada."""
        modelo, devolver = agente_tardon(retardo=0.5, tope=0)
        try:
            r = client.post("/chat", json={"message": "algo", "session_id": "tope-4"})
            assert r.json()["response"] == "tarde, pero aquí está"
        finally:
            devolver()


    def test_la_respuesta_del_turno_abandonado_acaba_en_la_conversacion(
        self, client, agente_tardon
    ):
        """Es la mitad de la promesa que se le hace a quien lee el aviso.

        El aviso dice «vuelve a abrir esta conversación en un momento y verás la
        respuesta completa». Si eso no pasara, sería peor que un error: la
        persona esperaría algo que no va a llegar.

        Se comprueba por la misma ruta que usa la web al recargar, no mirando el
        almacén por dentro: lo que importa es lo que ve quien vuelve.
        """
        modelo, devolver = agente_tardon(retardo=1.5, tope=1)
        sesion = "tope-persiste"
        try:
            primera = client.post(
                "/chat", json={"message": "algo largo", "session_id": sesion}
            ).json()["response"]
            assert "Sigo trabajando" in primera

            assert modelo.termino.wait(5), "el turno no llegó a terminar"
            # `_persist` corre en el `finally` de `process`, justo después de que
            # el modelo responda: se le da un momento para cerrar.
            for _ in range(50):
                cuerpo = client.get(f"/sessions/{sesion}/messages").json()
                respuestas = [
                    m["content"] for m in cuerpo["messages"] if m["role"] == "assistant"
                ]
                if respuestas:
                    break
                time.sleep(0.1)

            assert respuestas, "El turno abandonado no dejó su respuesta"
            assert "tarde, pero aquí está" in respuestas[-1], (
                f"Se guardó otra cosa: {respuestas[-1][:80]!r}"
            )
        finally:
            devolver()


class TestElHiloHeredaQuienEsElUsuario:
    def test_el_contexto_de_la_peticion_cruza_al_hilo(self):
        """`contextvars` no cruza a un hilo nuevo por su cuenta.

        Ahí viven el usuario y el rol de la petición. Sin copiarlos, el turno
        abandonado escribiría sin dueño, que es exactamente el fallo que costó
        el aislamiento entre cuentas en Supabase.
        """
        from src.identidad import fijar_usuario, usuario_actual

        fijar_usuario("usr-de-la-peticion")
        visto, _ = _sin_esperar_de_mas(lambda: usuario_actual(), segundos=5)

        assert visto == "usr-de-la-peticion"

    def test_una_excepcion_del_hilo_llega_a_quien_espera(self):
        """Si no, un fallo del turno se convertiría en un `IndexError` aquí."""

        def revienta():
            raise ValueError("me rompí")

        with pytest.raises(ValueError, match="me rompí"):
            _sin_esperar_de_mas(revienta, segundos=5)


class TestSeSabeQuePasoSePaso:
    """El tercer punto de la 2.1: «con observabilidad se podrá ver qué paso
    concreto se pasa».

    Había dos avisos y ninguno lo decía. El del agente apuntaba el tope y la
    conversación. El de la ruta, que la espera había vencido. Y el registro de
    la petición se escribía al devolver «sigo trabajando», **antes** del paso
    culpable, que seguía corriendo en su hilo.
    """

    def test_el_turno_abandonado_registra_su_reparto_al_terminar(self, caplog):
        import logging
        import time

        from src.observabilidad import medicion_actual, midiendo

        def turno_lento():
            medicion_actual().sumar("modelo", 0.2)
            time.sleep(0.3)
            medicion_actual().sumar("herramienta.search_web", 0.3)
            return "hecho"

        with caplog.at_level(logging.WARNING, logger="src.api.routes.chat"):
            with midiendo():
                resultado, se_paso = _sin_esperar_de_mas(turno_lento, segundos=0.05)
            assert (resultado, se_paso) == (None, True)

            # El aviso llega cuando el hilo termina, no al abandonarlo.
            limite = time.monotonic() + 3
            while "Reparto completo" not in caplog.text and time.monotonic() < limite:
                time.sleep(0.05)

        assert "Reparto completo" in caplog.text, (
            "el turno abandonado terminó y nadie apuntó en qué se le fue el tiempo"
        )
        assert "herramienta.search_web=300" in caplog.text, (
            "el reparto no incluye el paso que ocurrió DESPUÉS de abandonarlo, "
            "que es justo el que hacía falta ver"
        )

    def test_un_turno_que_llega_a_tiempo_no_deja_ese_aviso(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="src.api.routes.chat"):
            resultado, se_paso = _sin_esperar_de_mas(lambda: "rápido", segundos=5)

        assert (resultado, se_paso) == ("rápido", False)
        assert "Reparto completo" not in caplog.text

    def test_el_agente_detenido_por_tiempo_dice_el_reparto(self, caplog):
        import logging

        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.observabilidad import midiendo
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        agente = Agent(
            model=MockLLMProvider(),
            tool_registry=ToolRegistry(),
            permission_manager=PermissionManager(interactive=False),
        )
        agente.turn_timeout = 0

        with caplog.at_level(logging.WARNING, logger="src.agent.core"):
            with midiendo() as m:
                m.sumar("modelo", 0.48)
                m.sumar("modelo", 0.48)
                agente.chat("hola", session_id="ses-tope")

        assert "Reparto hasta aquí" in caplog.text
        assert "modelo=960x2" in caplog.text, (
            "el aviso de turno detenido no dice en qué se fue el tiempo"
        )
