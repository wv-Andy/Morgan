"""
Pruebas de resiliencia frente a proveedores LLM lentos o caídos (V1.3, fases 1 y 8).

Simulan los fallos en lugar de depender de la red real, para que sean
reproducibles: ConnectionError, timeout, HTTP 500 y proveedor colgado.
"""

import time

import pytest

from src.agent.core import Agent
from src.agent.sessions import SessionStore
from src.models.base import ChatMessage, LLMProvider, LLMResponse
from src.models.fallback import FallbackProvider
from src.models.mock import MockLLMProvider
from src.tools.base import Tool
from src.tools.registry import ToolRegistry

#: Una clave falsa pero **con la forma de una de verdad**.
#:
#: Era "clave-de-prueba", de 15 caracteres, y dejo de valer cuando la cadena
#: empezo a comprobar las claves al arrancar: ninguna clave real baja de 39
#: caracteres, asi que el minimo de 16 la rechazaba.
#:
#: Se alargo la falsa en lugar de bajar el minimo. Una prueba que usa una clave
#: mas corta que cualquiera posible estaba probando con algo que el mundo real
#: no produce, y eso es lo que hay que arreglar.
CLAVE_FALSA = "gsk_clave-de-prueba-que-no-sirve-para-nada"



class ProveedorCaido(LLMProvider):
    """Simula un servicio inalcanzable."""

    def __init__(self, error: Exception | None = None):
        self.error = error or ConnectionError("getaddrinfo failed")
        self.llamadas = 0

    @property
    def model_name(self) -> str:
        return "caido"

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        self.llamadas += 1
        raise self.error


class ProveedorLento(LLMProvider):
    """Simula un servicio que responde, pero muy despacio."""

    def __init__(self, retardo: float = 0.3):
        self.retardo = retardo
        self.llamadas = 0

    @property
    def model_name(self) -> str:
        return "lento"

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        self.llamadas += 1
        time.sleep(self.retardo)
        # Siempre pide otra herramienta: fuerza a agotar el bucle del agente.
        from src.models.base import ToolCallRequest
        return LLMResponse(
            type="tool_call",
            tool_calls=[ToolCallRequest(id=f"c{self.llamadas}", name="inexistente", arguments={})],
        )


def _agente(modelo, permisos, **kwargs):
    return Agent(
        model=modelo,
        tool_registry=ToolRegistry(),
        permission_manager=permisos,
        sessions=SessionStore(),
        **kwargs,
    )


class TestTopeDeTiempoPorTurno:
    def test_un_proveedor_lento_no_bloquea_indefinidamente(self, non_interactive_permissions):
        """Sin tope, seis iteraciones con reintentos podían tardar minutos."""
        lento = ProveedorLento(retardo=0.3)
        agente = _agente(lento, non_interactive_permissions)
        agente.turn_timeout = 1  # segundo

        inicio = time.monotonic()
        respuesta = agente.process("haz algo largo")
        transcurrido = time.monotonic() - inicio

        assert "tiempo máximo" in respuesta
        # Debe cortar cerca del tope, no agotar las 6 iteraciones.
        assert transcurrido < 3

    def test_el_tope_no_estorba_a_un_turno_normal(self, non_interactive_permissions):
        modelo = MockLLMProvider()
        modelo.queue_text("respuesta rápida")
        agente = _agente(modelo, non_interactive_permissions)

        assert agente.process("hola") == "respuesta rápida"

    def test_el_plazo_se_mira_tambien_entre_herramientas(self, non_interactive_permissions):
        """Seis búsquedas en una sola respuesta caben enteras entre dos vueltas.

        El plazo se miraba solo al empezar cada iteración, así que una respuesta
        del modelo con muchas llamadas se ejecutaba entera pasara lo que pasara.
        En la nube eso no es un detalle: el proxy del borde corta a los 120 s con
        una página de error suya y el trabajo hecho se tira. Medido en
        producción: con el tope en 110 s, un turno murió en el proxy a los
        120,1 s — justo lo que ese número existía para evitar.
        """
        from src.models.base import ToolCallRequest

        class HerramientaLenta(Tool):
            ejecuciones = 0

            @property
            def name(self) -> str:
                return "tarda"

            @property
            def description(self) -> str:
                return "Tarda un poco a propósito."

            @property
            def parameters(self) -> dict:
                return {"type": "object", "properties": {"n": {"type": "integer"}}, "required": []}

            @property
            def requires_local(self) -> bool:
                return False

            def execute(self, **kwargs):
                HerramientaLenta.ejecuciones += 1
                time.sleep(0.4)
                return {"success": True, "data": "hecho", "error": None}

        class PideSeis(LLMProvider):
            @property
            def model_name(self) -> str:
                return "pide-seis"

            def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
                return LLMResponse(
                    type="tool_call",
                    # Argumentos distintos a propósito: con los seis iguales
                    # salta antes el detector de bucles y no se llega a probar
                    # lo que esta prueba mira.
                    tool_calls=[
                        ToolCallRequest(id=f"c{i}", name="tarda", arguments={"n": i})
                        for i in range(6)
                    ],
                )

        registro = ToolRegistry()
        registro.register(HerramientaLenta())
        agente = Agent(
            model=PideSeis(),
            tool_registry=registro,
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
        )
        agente.turn_timeout = 1

        inicio = time.monotonic()
        respuesta = agente.process("usa la herramienta seis veces")
        transcurrido = time.monotonic() - inicio

        assert "tiempo máximo" in respuesta
        # Seis a 0,4 s serían 2,4 s solo en la primera vuelta. Con el plazo
        # mirado entre herramientas se corta dentro de la primera.
        assert transcurrido < 2.0, (
            f"Tardó {transcurrido:.1f} s con un tope de 1 s: el plazo no se está "
            "mirando entre herramientas"
        )
        assert HerramientaLenta.ejecuciones < 6, (
            "Se ejecutaron las seis pese a haberse pasado el plazo"
        )

    def test_el_tope_es_configurable(self, non_interactive_permissions, monkeypatch):
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_TURN_TIMEOUT", "45")
        reset_settings()

        agente = _agente(MockLLMProvider(), non_interactive_permissions)

        assert agente.turn_timeout == 45


class TestConmutacionDeProveedor:
    def test_conmuta_al_respaldo_cuando_el_principal_cae(self):
        principal = ProveedorCaido()
        respaldo = MockLLMProvider()
        respaldo.queue_text("respondo yo")

        proveedor = FallbackProvider(primary=principal, fallback=respaldo)
        resultado = proveedor.generate([ChatMessage(role="user", content="hola")])

        assert resultado.content == "respondo yo"
        assert proveedor.primary_failures == 1
        assert proveedor.fallback_uses == 1

    def test_si_ambos_caen_se_propaga_un_error_claro(self):
        proveedor = FallbackProvider(
            primary=ProveedorCaido(),
            fallback=ProveedorCaido(TimeoutError("read timeout")),
        )

        with pytest.raises(TimeoutError):
            proveedor.generate([ChatMessage(role="user", content="hola")])

    @pytest.mark.parametrize("error", [
        ConnectionError("sin red"),
        TimeoutError("read timeout"),
        RuntimeError("HTTP 500 del proveedor"),
    ])
    def test_cualquier_fallo_del_principal_activa_el_respaldo(self, error):
        respaldo = MockLLMProvider()
        respaldo.queue_text("ok")

        proveedor = FallbackProvider(primary=ProveedorCaido(error), fallback=respaldo)

        assert proveedor.generate([]).content == "ok"

    def test_no_se_usa_el_respaldo_si_el_principal_responde(self):
        principal = MockLLMProvider()
        principal.queue_text("principal")
        respaldo = ProveedorCaido()

        proveedor = FallbackProvider(primary=principal, fallback=respaldo)

        assert proveedor.generate([]).content == "principal"
        assert respaldo.llamadas == 0


class TestElAgenteSobreviveAlLlm:
    @pytest.mark.parametrize("error", [
        ConnectionError("getaddrinfo failed"),
        TimeoutError("read timeout"),
        RuntimeError("500 Internal Server Error"),
    ])
    def test_morgan_sigue_vivo_sin_llm(self, non_interactive_permissions, error):
        """§18: un fallo externo no debe tumbar Morgan."""
        agente = _agente(ProveedorCaido(error), non_interactive_permissions)

        respuesta = agente.process("hola")

        assert isinstance(respuesta, str)
        assert respuesta.strip(), "Debe explicar algo, no devolver vacio"
        # El detalle tecnico va al log, no a la interfaz.
        assert str(error) not in respuesta

    def test_la_sesion_queda_utilizable_tras_el_fallo(self, non_interactive_permissions):
        agente = _agente(ProveedorCaido(), non_interactive_permissions)
        agente.process("hola")

        # El agente debe seguir aceptando mensajes después del error.
        assert isinstance(agente.process("¿sigues ahí?"), str)


class TestConfiguracionDeProveedores:
    def test_groq_recibe_timeout_y_reintentos_explicitos(self, monkeypatch):
        """Sin esto se heredan los del SDK: connect 5 s, read 60 s, 2 reintentos."""
        from unittest.mock import MagicMock, patch

        from src.config import reset_settings

        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        monkeypatch.setenv("MORGAN_LLM_TIMEOUT", "17")
        monkeypatch.setenv("MORGAN_LLM_MAX_RETRIES", "0")
        reset_settings()

        with patch("src.models.groq.Groq", MagicMock()) as fake:
            from src.models.groq import GroqProvider

            GroqProvider()

        kwargs = fake.call_args.kwargs
        assert kwargs["timeout"] == 17.0
        assert kwargs["max_retries"] == 0

    def test_groq_omite_tool_choice_si_no_hay_herramientas(self, monkeypatch):
        """Groq responde 400 si se le envía tool_choice=null."""
        from unittest.mock import MagicMock, patch

        from src.config import reset_settings

        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        reset_settings()

        with patch("src.models.groq.Groq", MagicMock()):
            from src.models.groq import GroqProvider

            proveedor = GroqProvider()
            proveedor.client = MagicMock()
            proveedor.client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(tool_calls=None, content="ok"))]
            )

            proveedor.generate([ChatMessage(role="user", content="hola")])

        enviado = proveedor.client.chat.completions.create.call_args.kwargs
        assert "tool_choice" not in enviado
        assert "tools" not in enviado

    def test_groq_envia_tool_choice_auto_cuando_hay_herramientas(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from src.config import reset_settings

        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        reset_settings()

        with patch("src.models.groq.Groq", MagicMock()):
            from src.models.groq import GroqProvider

            proveedor = GroqProvider()
            proveedor.client = MagicMock()
            proveedor.client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(tool_calls=None, content="ok"))]
            )

            proveedor.generate(
                [ChatMessage(role="user", content="hola")],
                tools=[{"name": "list_files", "description": "d", "parameters": {}}],
            )

        enviado = proveedor.client.chat.completions.create.call_args.kwargs
        assert enviado["tool_choice"] == "auto"
