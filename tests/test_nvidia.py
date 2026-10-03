"""
Proveedor de NVIDIA NIM (V1.4) y la conversión al formato de OpenAI.

Los fallos se simulan: nunca se llama al servicio real. Una prueba que dependa de
la red no es reproducible ni puede correr sin credenciales, y aquí lo que se
comprueba es la traducción y el manejo de errores, no que NVIDIA esté en pie.
"""

from unittest.mock import patch

import httpx
import pytest

from src.models.base import ChatMessage, ToolCallRequest
from src.models.capabilities import SOLO_TEXTO, Capability
from src.models.nvidia import NvidiaProvider
from src.models.openai_format import (
    from_openai_message,
    to_openai_messages,
    to_openai_tools,
)

CLAVE = "nvapi-clave-de-prueba"


def respuesta(status: int = 200, cuerpo: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=cuerpo if cuerpo is not None else {
            "choices": [{"message": {"role": "assistant", "content": "hola"}}]
        },
        request=httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions"),
    )


@pytest.fixture
def proveedor():
    return NvidiaProvider(api_key=CLAVE, model_name="modelo-de-prueba")


class TestLaConversionCompartida:
    """Groq y NVIDIA hablan el mismo dialecto: la conversión es una sola."""

    def test_el_system_prompt_va_primero(self):
        salida = to_openai_messages([ChatMessage(role="user", content="hola")], "eres Morgan")

        assert salida[0] == {"role": "system", "content": "eres Morgan"}

    def test_el_resultado_de_herramienta_conserva_su_identificador(self):
        """Sin él, el resultado no se empareja con su llamada y el modelo se pierde."""
        salida = to_openai_messages([
            ChatMessage(role="tool", tool_name="search_web", tool_call_id="call_42",
                        tool_result={"ok": True}),
        ])

        assert salida[0]["tool_call_id"] == "call_42"

    def test_sin_identificador_se_inventa_uno_estable(self):
        salida = to_openai_messages([
            ChatMessage(role="tool", tool_name="search_web", tool_result={}),
        ])

        assert salida[0]["tool_call_id"] == "call_search_web"

    def test_las_llamadas_se_serializan_como_json(self):
        salida = to_openai_messages([
            ChatMessage(role="model", tool_calls=[
                ToolCallRequest(id="c1", name="buscar", arguments={"q": "morgan"})
            ]),
        ])

        assert salida[0]["tool_calls"][0]["function"]["arguments"] == '{"q": "morgan"}'

    def test_sin_herramientas_devuelve_none_y_no_una_lista_vacia(self):
        """Los campos deben omitirse, no enviarse nulos: Groq rechaza eso con 400."""
        assert to_openai_tools(None) is None
        assert to_openai_tools([]) is None

    def test_unos_argumentos_mal_formados_no_revientan_el_turno(self):
        """Un modelo puede devolver JSON roto. Mejor una llamada sin argumentos,
        que el agente rechazará por validación, que perder el turno entero."""
        respuesta = from_openai_message({
            "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{roto"}}]
        })

        assert respuesta.type == "tool_call"
        assert respuesta.tool_calls[0].arguments == {}

    def test_funciona_igual_con_objetos_que_con_diccionarios(self):
        """Groq devuelve objetos del SDK; NVIDIA, JSON en crudo."""
        class Mensaje:
            tool_calls = None
            content = "texto"

        assert from_openai_message(Mensaje()).content == "texto"
        assert from_openai_message({"content": "texto"}).content == "texto"


class TestElProveedor:
    def test_sin_clave_no_se_construye(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "")
        import src.config as config

        config.reset_settings()
        try:
            with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
                NvidiaProvider()
        finally:
            config.reset_settings()

    def test_declara_solo_texto(self, proveedor):
        """No entiende imágenes: la cadena no debe mandarle ninguna."""
        assert proveedor.capabilities == SOLO_TEXTO
        assert not proveedor.supports(Capability.VISION)

    def test_el_nombre_identifica_al_proveedor(self, proveedor):
        assert proveedor.model_name.startswith("NVIDIA:")

    def test_devuelve_texto(self, proveedor):
        with patch("httpx.post", return_value=respuesta()):
            r = proveedor.generate([ChatMessage(role="user", content="hola")])

        assert r.type == "text"
        assert r.content == "hola"

    def test_devuelve_llamadas_a_herramienta(self, proveedor):
        cuerpo = {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "search_web", "arguments": '{"query": "x"}'}}
        ]}}]}

        with patch("httpx.post", return_value=respuesta(cuerpo=cuerpo)):
            r = proveedor.generate([ChatMessage(role="user", content="busca")], tools=[
                {"name": "search_web", "description": "", "parameters": {}}
            ])

        assert r.type == "tool_call"
        assert r.tool_calls[0].name == "search_web"
        assert r.tool_calls[0].arguments == {"query": "x"}

    def test_una_respuesta_sin_opciones_no_revienta(self, proveedor):
        """Un filtro de contenido puede devolver esto; antes era un IndexError."""
        with patch("httpx.post", return_value=respuesta(cuerpo={"choices": []})):
            r = proveedor.generate([])

        assert r.type == "text"
        assert r.content


class TestElManejoDeErrores:
    def test_un_400_no_se_reintenta(self, proveedor):
        """Repetir una petición mal formada no la arregla."""
        with patch("httpx.post", return_value=respuesta(400, {"error": "mal"})) as llamada:
            with pytest.raises(RuntimeError):
                proveedor.generate([])

        assert llamada.call_count == 1

    def test_un_503_si_se_reintenta(self, proveedor):
        proveedor._max_retries = 1

        with patch("httpx.post", return_value=respuesta(503, {})) as llamada:
            with pytest.raises(RuntimeError):
                proveedor.generate([])

        assert llamada.call_count == 2

    def test_un_fallo_de_red_da_un_mensaje_reconocible(self, proveedor):
        proveedor._max_retries = 0

        with patch("httpx.post", side_effect=httpx.ConnectError("sin red")):
            with pytest.raises(RuntimeError, match="No se pudo conectar"):
                proveedor.generate([])

    def test_el_error_llega_traducido_a_la_interfaz(self, proveedor):
        """El cuerpo crudo va al log; a la persona, una explicación."""
        from src.models.errors import describe_llm_error

        mensaje = describe_llm_error(RuntimeError("NVIDIA NIM devolvió 503: {...}"))

        assert "disponible" in mensaje
        assert "NVIDIA NIM devolvió" not in mensaje


class TestSuSitioEnLaCadena:
    def test_ya_no_va_en_la_cadena_por_defecto(self):
        """Estuvo segundo, luego último, y desde la V2.0.3 está fuera.

        **La razón final la dio la medición en producción**, no el
        razonamiento: la auditoría de la V2.0.2 encontró quince llamadas con
        quince plazos agotados a los 60 s y **ningún éxito**. Un eslabón que
        falla el 100% de las veces no es un respaldo, es un peaje de un minuto
        antes de rendirse.

        Y queda algo sin explicar, que se deja escrito como pregunta: desde un
        equipo de casa el mismo modelo contesta en 3 segundos. Desde Render,
        nunca. Se midió el efecto, no la causa.

        Lo que sigue era el razonamiento anterior, y sigue siendo cierto:

        Su latencia es **impredecible**, no lenta: la misma petición al mismo
        modelo dio 2,8 s, 4,8 s, 18 s, 23 s y 37 s la misma tarde, más varios
        agotes de más de 60. Con `llm_timeout` en 30 s, unas veces contesta y
        otras se come el plazo entero sin dar nada.

        Con la cuota de Groq agotada eso hacía que cada turno costara 64 s:
        Groq 0,4 s (429) + NVIDIA 30 s (plazo agotado) + Gemini ~34 s. Gemini
        contesta de forma estable, así que va antes.

        No es que NVIDIA sea peor: un eslabón con latencia impredecible solo
        puede ir al final.
        """
        from src.config import PROVEEDORES_CONOCIDOS
        from src.models.chain import DEFAULT_ORDER

        # Ya no esta en la cadena, y el motivo esta en el docstring de abajo.
        assert "nvidia" not in DEFAULT_ORDER
        # Pero sigue siendo activable, que es lo que distingue apagar de borrar.
        assert "nvidia" in PROVEEDORES_CONOCIDOS

    def test_la_cadena_lo_construye_si_hay_clave(self, monkeypatch):
        from src.config import load_settings, reset_settings
        from src.models.chain import build_provider_chain

        monkeypatch.setenv("NVIDIA_API_KEY", CLAVE)
        monkeypatch.setenv("GROQ_API_KEY", "")
        monkeypatch.setenv("GEMINI_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        # Hay que pedirlo a mano: ya no va en el orden por defecto.
        monkeypatch.setenv("MORGAN_LLM_ORDER", "nvidia")
        reset_settings()
        try:
            modelo, activos = build_provider_chain(load_settings())
        finally:
            reset_settings()

        assert activos == ["nvidia"]
        assert modelo is not None

    def test_admite_herramientas_y_por_eso_es_respaldo_de_verdad(self, proveedor):
        """Un proveedor solo de texto no podría sustituir a Groq en un turno
        completo: se quedaría sin poder llamar a ninguna herramienta."""
        enviado = {}

        def capturar(url, **kwargs):
            enviado.update(kwargs.get("json", {}))
            return respuesta()

        with patch("httpx.post", side_effect=capturar):
            proveedor.generate([], tools=[{"name": "x", "description": "", "parameters": {}}])

        assert enviado.get("tool_choice") == "auto"
        assert enviado["tools"][0]["function"]["name"] == "x"


class TestElTextoNoSePierdeSiLlegaComoRazonamiento:
    """Medido con `openai/gpt-oss-20b`: `content` de 0 caracteres y
    `reasoning_content` de 109. Morgan devolvía **una cadena vacía**, así que la
    persona veía un turno que tardaba diez segundos y no decía nada.
    """

    def test_solo_razonamiento_cuenta_como_fallo_y_lleva_el_texto(self):
        """Desde la 4.0.5 la cadena prueba antes otro proveedor (ver
        `TestElRazonamientoEsElUltimoRecurso`), pero el texto no se pierde."""
        from src.models.openai_format import SoloRazonamiento, from_openai_message

        with pytest.raises(SoloRazonamiento) as exc:
            from_openai_message({
                "content": "",
                "reasoning_content": "El usuario pide la hora, así que respondo eso.",
            })
        assert "pide la hora" in exc.value.razonamiento

    def test_tambien_si_el_campo_se_llama_reasoning(self):
        """Cada proveedor lo llama de una forma."""
        from src.models.openai_format import SoloRazonamiento, from_openai_message

        with pytest.raises(SoloRazonamiento) as exc:
            from_openai_message({"content": None, "reasoning": "Pues esto."})
        assert exc.value.razonamiento == "Pues esto."

    def test_el_contenido_manda_cuando_existe(self):
        """El razonamiento es el último recurso, no una alternativa: no está
        redactado para leerse.
        """
        from src.models.openai_format import from_openai_message

        respuesta = from_openai_message({
            "content": "La respuesta buena.",
            "reasoning_content": "Mis divagaciones.",
        })

        assert respuesta.content == "La respuesta buena."

    def test_y_las_herramientas_mandan_sobre_los_dos(self):
        """Un modelo que llama a una herramienta suele dejar `content` vacío, y
        eso es normal: lo que importa es la llamada.
        """
        from src.models.openai_format import from_openai_message

        respuesta = from_openai_message({
            "content": "",
            "reasoning_content": "Voy a usar la herramienta.",
            "tool_calls": [{"id": "c1", "function": {"name": "dime_la_hora", "arguments": "{}"}}],
        })

        assert respuesta.type == "tool_call"
        assert respuesta.tool_calls[0].name == "dime_la_hora"

    def test_sin_nada_devuelve_vacio_y_no_revienta(self):
        from src.models.openai_format import from_openai_message

        assert from_openai_message({"content": None}).content == ""


class TestElRazonamientoEsElUltimoRecurso:
    """Medido en la 4.0.5: `openai/gpt-oss-20b` contestó a la persona, en inglés, con su
    razonamiento («The user says plan approved, but…»), instrucciones internas incluidas.
    Ahora la cadena prueba antes el siguiente proveedor."""

    class _Razona:
        model_name = "razona"
        es_relevo_de_cuota = False
        capabilities = SOLO_TEXTO

        def supports(self, capacidad):
            return True

        def generate(self, messages, tools=None, system_prompt=None):
            from src.models.openai_format import from_openai_message
            return from_openai_message({"content": "", "reasoning": "The user says plan approved, but..."})

    def test_si_otro_contesta_se_usa_lo_suyo(self):
        from src.models.fallback import FallbackProvider
        from src.models.mock import MockLLMProvider

        otro = MockLLMProvider()
        otro.queue_text("No pude crear el archivo: esa carpeta no se permite.")
        respuesta = FallbackProvider(self._Razona(), otro).generate([ChatMessage(role="user", content="hola")])
        assert respuesta.content.startswith("No pude crear")

    def test_si_nadie_contesta_el_razonamiento_antes_que_nada(self):
        from src.models.fallback import FallbackProvider
        from src.models.mock import MockLLMProvider

        class Roto(MockLLMProvider):
            def generate(self, messages, tools=None, system_prompt=None):
                raise RuntimeError("caído")

        respuesta = FallbackProvider(self._Razona(), Roto()).generate([ChatMessage(role="user", content="hola")])
        assert "plan approved" in respuesta.content
