"""
Pruebas del traductor de fallos del proveedor LLM a mensajes para la persona.

Defecto que las origina: al pedirle a Morgan una búsqueda web, la ventana de chat
mostró tal cual el cuerpo de la respuesta de Google:

    Error al comunicar con el modelo LLM: 400 INVALID_ARGUMENT. {'error':
    {'code': 400, 'message': 'Function call is missing a thought_signature ...'}}

El plan de producción lo prohíbe expresamente: «Los errores técnicos internos no
deben mostrarse directamente al usuario».
"""

import logging

import pytest

from src.models.errors import describe_llm_error, log_llm_error


class ErrorConCodigo(Exception):
    """Imita a los SDK, que exponen el código en un atributo."""

    def __init__(self, mensaje: str, code: int | None = None):
        super().__init__(mensaje)
        self.code = code


# El texto real que vio el usuario, tal cual.
ERROR_REAL_GEMINI = (
    "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Function call is "
    "missing a thought_signature in functionCall parts. This is required for tools "
    "to work correctly, and missing thought_signature may lead to degraded model "
    "performance. Additional data, function call default_api:search_web , position "
    "10. Please refer to https://ai.google.dev/gemini-api/docs/thought-signatures "
    "for more details.', 'status': 'INVALID_ARGUMENT'}}"
)


class TestNoSeFiltraElDetalleTecnico:
    @pytest.mark.parametrize(
        "excepcion",
        [
            ErrorConCodigo(ERROR_REAL_GEMINI, 400),
            ErrorConCodigo("429 RESOURCE_EXHAUSTED. {'error': {'message': 'quota'}}", 429),
            ErrorConCodigo("401 UNAUTHENTICATED. API key not valid", 401),
            ConnectionError("getaddrinfo failed for generativelanguage.googleapis.com"),
            TimeoutError("The read operation timed out"),
            RuntimeError("C:\\Users\\alguien\\proyecto\\venv\\lib\\sdk.py line 42"),
        ],
    )
    def test_el_mensaje_no_contiene_el_texto_original(self, excepcion):
        mensaje = describe_llm_error(excepcion)

        assert str(excepcion) not in mensaje
        assert mensaje.strip()

    @pytest.mark.parametrize(
        "fragmento",
        [
            "thought_signature",       # jerga interna de la API
            "INVALID_ARGUMENT",        # codigo del proveedor
            "default_api",             # nombres internos
            "https://ai.google.dev",   # enlaces a documentacion ajena
            "{'error'",                # el cuerpo JSON crudo
        ],
    )
    def test_no_se_escapa_ningun_fragmento_tecnico(self, fragmento):
        assert fragmento not in describe_llm_error(ErrorConCodigo(ERROR_REAL_GEMINI, 400))

    def test_tampoco_se_filtran_rutas_del_disco(self):
        mensaje = describe_llm_error(RuntimeError("fallo en C:\\Users\\ana\\.env"))

        assert "C:\\" not in mensaje
        assert ".env" not in mensaje


class TestElMensajeEsUtil:
    @pytest.mark.parametrize(
        "excepcion, esperado",
        [
            (ErrorConCodigo("429 rate limit", 429), "límite"),
            (ErrorConCodigo("401 invalid api key", 401), "clave"),
            (ErrorConCodigo("402 Insufficient Balance", 402), "saldo"),
            (ErrorConCodigo("503 UNAVAILABLE high demand", 503), "disponible"),
            (TimeoutError("deadline exceeded"), "tardado"),
            (ConnectionError("dns failure"), "conectar"),
        ],
    )
    def test_distingue_la_causa(self, excepcion, esperado):
        """No basta con ocultar: hay que decir algo accionable."""
        assert esperado in describe_llm_error(excepcion).lower()

    def test_un_error_desconocido_no_deja_a_la_persona_sin_nada(self):
        mensaje = describe_llm_error(RuntimeError("algo que nadie previo"))

        assert "modelo" in mensaje.lower()
        assert "log" in mensaje.lower()

    def test_el_codigo_se_deduce_del_texto_si_no_hay_atributo(self):
        """Groq no siempre expone `code`; queda el texto de la excepción."""
        mensaje = describe_llm_error(RuntimeError("Error 503 del servidor"))

        assert "disponible" in mensaje.lower()


class TestElDetalleSiQuedaRegistrado:
    def test_log_llm_error_registra_todo_y_devuelve_lo_limpio(self, caplog):
        excepcion = ErrorConCodigo(ERROR_REAL_GEMINI, 400)

        with caplog.at_level(logging.ERROR, logger="src.models.errors"):
            mensaje = log_llm_error(excepcion, contexto="gemini-3.6-flash")

        registrado = caplog.text
        # El diagnostico no se pierde: esta entero en el log...
        assert "thought_signature" in registrado
        assert "gemini-3.6-flash" in registrado
        # ...y no en lo que ve la persona.
        assert "thought_signature" not in mensaje


class TestElAgenteUsaElTraductor:
    def test_un_fallo_del_proveedor_no_llega_crudo_al_chat(self, non_interactive_permissions):
        """Comprobación de extremo a extremo: el agente completo, no solo el helper."""
        from src.agent.core import Agent
        from src.agent.sessions import SessionStore
        from src.models.mock import MockLLMProvider
        from src.tools.registry import ToolRegistry

        class ProveedorQueFiltra(MockLLMProvider):
            def generate(self, messages, tools=None, system_prompt=None):
                raise ErrorConCodigo(ERROR_REAL_GEMINI, 400)

        agente = Agent(
            model=ProveedorQueFiltra(),
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
        )

        respuesta = agente.process("busca algo en internet")

        assert "thought_signature" not in respuesta
        assert "{'error'" not in respuesta
        assert respuesta.strip()
