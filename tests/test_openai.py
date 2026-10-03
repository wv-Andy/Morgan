"""
El proveedor de OpenAI (V2.0.3), que es el primero que cuesta dinero.

**Ninguna prueba de aquí llama al servicio real.** No solo por reproducibilidad
—que es la razón de siempre— sino porque cada llamada se factura, y una suite de
1.800 pruebas que gastara un céntimo por ejecución sería un grifo abierto.

Lo que se comprueba, por orden de lo que costaría equivocarse:

1. **Los tres parámetros que este modelo rechaza.** Copiar `nvidia.py` daba un
   400 en cada llamada, y esas tres diferencias salieron de preguntarle al
   servicio, no de leer documentación.
2. **Que un plazo agotado no se reintenta.** En un proveedor gratuito eso cuesta
   tiempo; aquí puede costar dos respuestas facturadas por una que no llegó.
3. **Que el freno del presupuesto frena.** Un tope que no se comprueba es un
   comentario.
4. **Que va al final de la cadena**, porque es el único que cobra.
"""

from unittest.mock import patch

import httpx
import pytest

from src.models.base import ChatMessage
from src.models.capabilities import SOLO_TEXTO, Capability
from src.models.openai_chat import (
    CONTADOR,
    OpenAIProvider,
    PresupuestoAgotado,
)

CLAVE = "sk-proj-de-mentira"


def respuesta(cuerpo=None, status=200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=cuerpo if cuerpo is not None else {
            "choices": [{"message": {"role": "assistant", "content": "hola"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        },
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


@pytest.fixture(autouse=True)
def contador_limpio():
    """El contador es global a propósito: lo que importa es lo que gasta el
    proceso entero. Eso obliga a limpiarlo entre pruebas.
    """
    CONTADOR.entrada = CONTADOR.salida = 0
    CONTADOR.llamadas = CONTADOR.rechazadas = 0
    yield
    CONTADOR.entrada = CONTADOR.salida = 0
    CONTADOR.llamadas = CONTADOR.rechazadas = 0


@pytest.fixture
def proveedor():
    return OpenAIProvider(api_key=CLAVE, model_name="gpt-5.6-luna")


def capturar(monkeypatch, cuerpo=None, status=200):
    """Sustituye la llamada y devuelve lo que se le mandó al servicio."""
    enviado = {}

    def falso(url, **kwargs):
        enviado.update(kwargs.get("json") or {})
        enviado["_url"] = url
        enviado["_headers"] = kwargs.get("headers") or {}
        return respuesta(cuerpo, status)

    monkeypatch.setattr(httpx, "post", falso)
    return enviado


class TestLosTresParametrosQueEsteModeloRechaza:
    """Los tres salieron de preguntarle al servicio, y los tres dan 400.

    Un 400 no consume tokens, así que descubrirlos costó cero. Pero si alguien
    "arregla" esto para parecerse a los otros proveedores, Morgan se queda sin
    modelo de pago y el síntoma es un 400 en cada turno.
    """

    def test_manda_max_completion_tokens_y_no_max_tokens(self, proveedor, monkeypatch):
        """`max_tokens` is not supported with this model.
        Use `max_completion_tokens` instead.
        """
        enviado = capturar(monkeypatch)

        proveedor.generate([ChatMessage(role="user", content="hola")])

        assert "max_completion_tokens" in enviado
        assert "max_tokens" not in enviado, (
            "Este modelo rechaza max_tokens con un 400"
        )

    def test_no_manda_temperature(self, proveedor, monkeypatch):
        """«Unsupported value: 'temperature' does not support 0.2 with this
        model. Only the default (1) value is supported.»

        Los otros proveedores la mandan. Este no puede.
        """
        enviado = capturar(monkeypatch)

        proveedor.generate([ChatMessage(role="user", content="hola")])

        assert "temperature" not in enviado

    def test_manda_reasoning_effort_en_none(self, proveedor, monkeypatch):
        """«Function tools with reasoning_effort are not supported … set
        reasoning_effort to 'none'.»

        Es el importante de los tres: **sin esto las herramientas no
        funcionan**, y Morgan sin herramientas es un chat. Se manda siempre y no
        solo cuando hay herramientas, porque el razonamiento además se factura
        como salida.
        """
        enviado = capturar(monkeypatch)

        proveedor.generate([ChatMessage(role="user", content="hola")])

        assert enviado.get("reasoning_effort") == "none"

    def test_tambien_con_herramientas(self, proveedor, monkeypatch):
        enviado = capturar(monkeypatch)

        proveedor.generate(
            [ChatMessage(role="user", content="que hora es")],
            tools=[{"name": "dime_la_hora", "description": "", "parameters": {}}],
        )

        assert enviado.get("reasoning_effort") == "none"
        assert enviado["tools"][0]["function"]["name"] == "dime_la_hora"
        assert enviado.get("tool_choice") == "auto"


class TestElPlazoAgotadoNoSeReintenta:
    """La diferencia importa más aquí que en ningún otro proveedor.

    Un `ReadTimeout` significa que el servicio no contestó en el plazo. Y la
    respuesta **puede haberse generado y facturado igualmente** aunque no
    llegara a tiempo. Reintentar paga dos veces por una respuesta.

    Es el defecto que la auditoría encontró en `nvidia.py`, donde capturaba
    `httpx.RequestError` —que incluye los plazos— y los reintentaba. Allí
    costaba 60 segundos en lugar de 30; aquí costaría dinero.
    """

    def test_un_plazo_agotado_se_intenta_una_sola_vez(self, proveedor):
        proveedor._max_retries = 3  # aunque se permitan reintentos

        with patch("httpx.post", side_effect=httpx.ReadTimeout("tarde")) as llamada:
            with pytest.raises(RuntimeError, match="no respondió en"):
                proveedor.generate([])

        assert llamada.call_count == 1, (
            "Se ha reintentado un plazo agotado, y eso puede pagarse dos veces"
        )

    def test_un_fallo_de_conexion_si_se_reintenta(self, proveedor):
        """Aquí no se ha generado nada, así que no hay nada que pagar dos veces.
        """
        proveedor._max_retries = 2

        with patch("httpx.post", side_effect=httpx.ConnectError("sin red")) as llamada:
            with pytest.raises(RuntimeError, match="No se pudo conectar"):
                proveedor.generate([])

        assert llamada.call_count == 3

    def test_un_400_no_se_reintenta(self, proveedor):
        """Repetir una petición mal formada no la arregla, y su motivo es lo que
        dice qué parámetro no acepta el modelo.
        """
        proveedor._max_retries = 2

        with patch("httpx.post", return_value=respuesta({"error": "x"}, 400)) as ll:
            with pytest.raises(RuntimeError, match="400"):
                proveedor.generate([])

        assert ll.call_count == 1

    def test_un_429_si_se_reintenta(self, proveedor):
        proveedor._max_retries = 1

        with patch("httpx.post", return_value=respuesta({}, 429)) as llamada:
            with pytest.raises(RuntimeError):
                proveedor.generate([])

        assert llamada.call_count == 2


class TestElFrenoDelPresupuesto:
    """Un tope que no se comprueba es un comentario."""

    def test_por_debajo_del_tope_se_llama(self, proveedor, monkeypatch):
        proveedor._tope_tokens = 1000
        capturar(monkeypatch)

        r = proveedor.generate([ChatMessage(role="user", content="hola")])

        assert r.type == "text"

    def test_al_alcanzarlo_se_niega_ANTES_de_gastar(self, proveedor, monkeypatch):
        """Se comprueba antes de construir la petición, no después: el objetivo
        es no hacer la llamada, no enterarse de que se hizo.
        """
        proveedor._tope_tokens = 10
        CONTADOR.entrada = 8
        CONTADOR.salida = 5  # 13 > 10

        def no_deberia(*a, **k):
            raise AssertionError("se ha llamado al servicio con el tope agotado")

        monkeypatch.setattr(httpx, "post", no_deberia)

        with pytest.raises(PresupuestoAgotado, match="tope"):
            proveedor.generate([ChatMessage(role="user", content="hola")])

    def test_el_gasto_se_acumula_llamada_a_llamada(self, proveedor, monkeypatch):
        capturar(monkeypatch)

        for _ in range(3):
            proveedor.generate([ChatMessage(role="user", content="hola")])

        resumen = CONTADOR.resumen()
        assert resumen["llamadas"] == 3
        assert resumen["tokens_entrada"] == 27  # 9 por llamada
        assert resumen["tokens_salida"] == 12   # 4 por llamada

    def test_un_tope_en_cero_significa_sin_limite(self, proveedor, monkeypatch):
        """Para poder desactivarlo sin borrar la comprobación."""
        proveedor._tope_tokens = 0
        CONTADOR.entrada = 999_999
        capturar(monkeypatch)

        assert proveedor.generate([]).type == "text"

    def test_la_ventana_reinicia_la_cuenta(self, proveedor, monkeypatch):
        """Sin el reinicio, el freno se quedaría echado para siempre tras el
        primer día de uso intenso.
        """
        import time

        proveedor._tope_tokens = 10
        proveedor._ventana = 60
        CONTADOR.entrada = 50
        CONTADOR.desde = time.time() - 120  # la ventana ya pasó
        capturar(monkeypatch)

        assert proveedor.generate([]).type == "text"

    def test_el_presupuesto_agotado_no_es_un_fallo_del_proveedor(self):
        """La distinción importa para la cadena: no hay nada que reintentar ni
        de qué recuperarse. Es una decisión de Morgan, no una avería.
        """
        assert issubclass(PresupuestoAgotado, RuntimeError)


class TestLoQueDevuelve:
    def test_texto(self, proveedor, monkeypatch):
        capturar(monkeypatch)

        r = proveedor.generate([ChatMessage(role="user", content="hola")])

        assert r.type == "text"
        assert r.content == "hola"

    def test_llamadas_a_herramienta(self, proveedor, monkeypatch):
        """Medido contra el servicio real: devuelve `tool_calls` con el nombre,
        los argumentos en JSON válido y su identificador.
        """
        capturar(monkeypatch, {
            "choices": [{"message": {"tool_calls": [{
                "id": "call_kr49", "type": "function",
                "function": {"name": "dime_la_hora", "arguments": "{}"},
            }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 132, "completion_tokens": 16},
        })

        r = proveedor.generate([ChatMessage(role="user", content="hora")],
                               tools=[{"name": "dime_la_hora", "description": "",
                                       "parameters": {}}])

        assert r.type == "tool_call"
        assert r.tool_calls[0].name == "dime_la_hora"
        assert r.tool_calls[0].id == "call_kr49"

    def test_una_respuesta_sin_opciones_no_revienta(self, proveedor, monkeypatch):
        """Un filtro de contenido puede devolver esto."""
        capturar(monkeypatch, {"choices": [], "usage": {}})

        r = proveedor.generate([])

        assert r.type == "text"
        assert r.content

    RESPUESTA_CON_RAZONAMIENTO = {
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 100,
                  "completion_tokens_details": {"reasoning_tokens": 90}},
    }

    def test_los_tokens_de_razonamiento_se_avisan_en_el_registro(
        self, proveedor, monkeypatch, caplog
    ):
        """No deberían existir con `reasoning_effort: none`. Si aparecen, es que
        alguien cambió algo o el servicio cambió, y se factura como salida sin
        que se vea en la respuesta.
        """
        capturar(monkeypatch, self.RESPUESTA_CON_RAZONAMIENTO)

        with caplog.at_level("WARNING"):
            proveedor.generate([])

        assert "razonamiento" in caplog.text

    def test_y_tambien_en_la_medicion_de_la_peticion(self, proveedor, monkeypatch):
        """Son **dos** sitios, y probar solo uno no vale: se descubrió
        rompiéndolo. Quitando el `anotar` y dejando el `logger.warning`, la
        prueba de arriba seguía pasando.

        El registro sirve para enterarse después; la medición, para verlo en la
        cabecera de la petición que lo provocó. Sin el segundo hay que cruzar
        registros a mano para saber qué turno costó de más.
        """
        from src.observabilidad import midiendo

        capturar(monkeypatch, self.RESPUESTA_CON_RAZONAMIENTO)

        with midiendo() as medicion:
            proveedor.generate([])
            datos = dict(medicion.datos)

        assert datos.get("openai_tokens_razonamiento") == 90, (
            "El razonamiento facturado no llega a la medición de la petición"
        )

    def test_los_tokens_normales_siempre_van_a_la_medicion(
        self, proveedor, monkeypatch
    ):
        """Sin razonamiento de por medio: lo que se gasta en un turno tiene que
        poder leerse en la cabecera de ese turno, no solo en un contador global.
        """
        from src.observabilidad import midiendo

        capturar(monkeypatch)

        with midiendo() as medicion:
            proveedor.generate([])
            datos = dict(medicion.datos)

        assert datos.get("openai_tokens_entrada") == 9
        assert datos.get("openai_tokens_salida") == 4


class TestSuSitioEnLaCadena:
    def test_va_el_ULTIMO_por_ser_el_unico_que_cobra(self):
        """No por ser peor: medido, es el más fiable de los cuatro —1,0 s de
        texto, 1,7 s con herramientas, sin un solo fallo—. Va al final para que
        el dinero solo se gaste cuando los gratuitos se agotan, que es
        exactamente cuando Morgan dejaba de funcionar.
        """
        from src.models.chain import DEFAULT_ORDER

        assert DEFAULT_ORDER[-1] == "openai"
        assert DEFAULT_ORDER == ("groq", "gemini", "openai")

    def test_nvidia_ya_no_se_construye_por_defecto(self):
        """La auditoría lo midió en producción: quince llamadas, quince plazos
        agotados a los 60 s, ningún éxito. Un eslabón con el 100% de fallos no
        es un respaldo, es un peaje de un minuto antes de rendirse.

        El código sigue ahí y funciona; lo que cambió es que no se pone solo.
        """
        from src.models.chain import DEFAULT_ORDER

        assert "nvidia" not in DEFAULT_ORDER

    def test_pero_se_puede_volver_a_poner_a_mano(self, monkeypatch):
        """Quitarlo del orden por defecto no es borrarlo: si algún día contesta,
        basta una variable de entorno.
        """
        from src.config import load_settings, reset_settings
        from src.models.chain import build_provider_chain

        monkeypatch.setenv("MORGAN_LLM_ORDER", "nvidia")
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-de-mentira")
        monkeypatch.setenv("GROQ_API_KEY", "")
        monkeypatch.setenv("GEMINI_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        reset_settings()
        try:
            _, activos = build_provider_chain(load_settings())
        finally:
            reset_settings()

        assert activos == ["nvidia"]

    def test_la_cadena_lo_construye_si_hay_clave(self, monkeypatch):
        from src.config import load_settings, reset_settings
        from src.models.chain import build_provider_chain

        monkeypatch.setenv("OPENAI_API_KEY", CLAVE)
        monkeypatch.setenv("GROQ_API_KEY", "")
        monkeypatch.setenv("GEMINI_API_KEY", "")
        reset_settings()
        try:
            modelo, activos = build_provider_chain(load_settings())
        finally:
            reset_settings()

        assert activos == ["openai"]
        assert modelo is not None

    def test_sin_clave_no_se_construye(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        import src.config as config

        config.reset_settings()
        try:
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                OpenAIProvider()
        finally:
            config.reset_settings()

    def test_el_nombre_identifica_al_proveedor(self, proveedor):
        """Para que la respuesta pueda decir quién contestó, que es lo que
        importa saber cuando la factura sube.
        """
        assert proveedor.model_name == "OpenAI:gpt-5.6-luna"

    def test_declara_solo_texto(self, proveedor):
        """Del lado seguro mientras no se compruebe la visión: en un proveedor
        de pago, equivocarse aquí cuesta una llamada facturada que da un 400.
        """
        assert proveedor.capabilities == SOLO_TEXTO
        assert not proveedor.supports(Capability.VISION)


class TestElTopeDeSalidaEsUnLimiteDeFactura:
    def test_se_manda_el_configurado(self, proveedor, monkeypatch):
        proveedor._max_salida = 512
        enviado = capturar(monkeypatch)

        proveedor.generate([])

        assert enviado["max_completion_tokens"] == 512

    def test_el_valor_por_defecto_es_modesto(self):
        """Una respuesta cortada se ve y se puede subir. Una factura no se puede
        bajar.
        """
        from src.config import Settings

        assert Settings.openai_max_salida <= 4096

    def test_la_clave_viaja_en_la_cabecera_y_no_en_la_url(self, proveedor, monkeypatch):
        """En la URL acabaría en los registros de cualquier intermediario."""
        enviado = capturar(monkeypatch)

        proveedor.generate([])

        assert CLAVE not in enviado["_url"]
        assert enviado["_headers"]["Authorization"] == f"Bearer {CLAVE}"
