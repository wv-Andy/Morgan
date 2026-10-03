"""
Saber cuánta cuota queda, y no pagar dos veces por enterarse de que no hay.

Cierra los dos últimos puntos de 2.0-F del roadmap:

> 3. **Contar los tokens gastados** para poder avisar antes de agotar la cuota,
>    no después. Groq devuelve el gasto en cada respuesta y hoy se descarta.
> 4. **Cambiar el orden de la cadena** cuando el principal está agotado, para no
>    pagar un 429 en cada llamada.

**Por qué importa ahora y no antes.** La medición de la 2.0.5 dejó claro que a
Morgan ya no le sobra latencia: un turno trivial son 0,66 s. Lo que sí tiene es
un techo de **capacidad**, unos 54 turnos al día, y llegaba sin avisar.

La clase más importante de este archivo es `TestSaltarEsUnaOptimizacionNoUnaRegla`.
Todo lo demás ahorra milisegundos; eso evita que Morgan deje de contestar.
"""

import time

import pytest

from src.models.base import LLMResponse
from src.models.capabilities import SOLO_TEXTO, Capability
from src.models.cuota import (
    CUOTAS,
    VENTANA_MAXIMA,
    es_rechazo_por_cuota,
    segundos_hasta_reintentar,
)
from src.models.fallback import FallbackProvider

#: Los mensajes de rechazo tal como llegan, copiados de los registros de
#: producción. Son la razón de que el análisis mire el texto: cada proveedor lo
#: dice a su manera y ninguno usa una cabecera estándar en el cuerpo.
GROQ_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_01m` service tier `on_demand` "
    "on tokens per day (TPD): Limit 200000, Used 198047, Requested 3465. "
    "Please try again in 10m53.184s.', 'code': 'rate_limit_exceeded'}}"
)
GEMINI_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
    "your current quota... Please retry in 25.342565212s.', "
    "'status': 'RESOURCE_EXHAUSTED'}}"
)


@pytest.fixture(autouse=True)
def registro_limpio():
    """Es estado del proceso, así que hay que limpiarlo entre pruebas.

    Es el precio de que funcione: los frenos de `ServicioDeCuentas` vivían en
    `self` y por eso no frenaban nunca.
    """
    CUOTAS.reiniciar()
    yield
    CUOTAS.reiniciar()


class Falla(FallbackProvider):
    """No se usa como proveedor: solo para no repetir la clase de abajo."""


class Proveedor:
    """Un proveedor de mentira que responde o falla a voluntad."""

    def __init__(self, nombre, error=None):
        self._n = nombre
        self.error = error
        self.llamadas = 0

    @property
    def model_name(self):
        return self._n

    @property
    def capabilities(self):
        return SOLO_TEXTO

    def supports(self, capacidad):
        return capacidad in SOLO_TEXTO

    def generate(self, messages=None, tools=None, system_prompt=None, **kw):
        self.llamadas += 1
        if self.error:
            raise RuntimeError(self.error)
        return LLMResponse(type="text", content=f"soy {self._n}")


class TestSaltarEsUnaOptimizacionNoUnaRegla:
    """**La propiedad que gobierna el módulo entero.**

    La ventana de agotamiento es una estimación: sale de lo que el proveedor
    dice en su rechazo, y puede equivocarse. Y los dos errores posibles no
    cuestan lo mismo:

    | Si la estimación falla | Consecuencia |
    |---|---|
    | Llamamos a uno agotado | Un 429. Cuesta 0,02 s |
    | **Saltamos a uno que funcionaba** | **Morgan no contesta** |

    Por eso se REORDENA y no se filtra.
    """

    def test_si_TODOS_parecen_agotados_se_llama_igual(self):
        """El caso que no puede fallar. Si la ventana se equivoca y Morgan
        decide que no hay nadie a quien llamar, deja de contestar.
        """
        a, b = Proveedor("a"), Proveedor("b")
        CUOTAS.marcar_agotado("a", 300)
        CUOTAS.marcar_agotado("b", 300)

        respuesta = FallbackProvider(a, b).generate([])

        assert respuesta.content == "soy a", (
            "Con todos agotados no se ha llamado a nadie: Morgan se queda mudo"
        )

    def test_reordenar_nunca_acorta_la_lista(self):
        a, b, c = Proveedor("a"), Proveedor("b"), Proveedor("c")
        CUOTAS.marcar_agotado("a", 300)
        CUOTAS.marcar_agotado("c", 300)

        ordenados = FallbackProvider._agotados_al_final([a, b, c])

        assert len(ordenados) == 3
        assert {p.model_name for p in ordenados} == {"a", "b", "c"}

    def test_el_agotado_va_al_final_pero_sigue_estando(self):
        a, b = Proveedor("a"), Proveedor("b")
        CUOTAS.marcar_agotado("a", 300)

        ordenados = FallbackProvider._agotados_al_final([a, b])

        assert [p.model_name for p in ordenados] == ["b", "a"]


class TestNoSePagaUnRechazoEnCadaLlamada:
    """El punto 4 del roadmap. Un turno hace entre una y cinco llamadas, y con
    el principal agotado eran hasta cinco viajes para que nos dijeran lo que ya
    sabíamos.
    """

    def test_tras_un_rechazo_se_pospone_en_la_siguiente_llamada(self):
        groq = Proveedor("groq", error=GROQ_429)
        respaldo = Proveedor("respaldo")
        cadena = FallbackProvider(groq, respaldo)

        cadena.generate([])           # paga el rechazo una vez
        assert groq.llamadas == 1

        cadena.generate([])           # y ahora ya no
        cadena.generate([])

        assert groq.llamadas == 1, (
            f"Se ha vuelto a llamar al agotado {groq.llamadas} veces"
        )
        assert respaldo.llamadas == 3

    def test_un_fallo_que_NO_es_de_cuota_no_pospone_nada(self):
        """Un corte de red o un 500 no significan «no me llames más»: pueden
        estar arreglados en la siguiente llamada.
        """
        caido = Proveedor("caido", error="Connection reset by peer")
        respaldo = Proveedor("respaldo")
        cadena = FallbackProvider(caido, respaldo)

        cadena.generate([])
        cadena.generate([])

        assert caido.llamadas == 2, (
            "Se ha dado por agotado un proveedor que solo tuvo un fallo de red"
        )

    def test_pasada_la_ventana_se_vuelve_a_intentar(self):
        groq = Proveedor("groq")
        CUOTAS.marcar_agotado("groq", 1)

        assert CUOTAS.agotado("groq") is True
        time.sleep(1.1)
        assert CUOTAS.agotado("groq") is False

        FallbackProvider(groq, Proveedor("otro")).generate([])
        assert groq.llamadas == 1


class TestLeerElVuelveEnDelRechazo:
    """Cada proveedor lo dice a su manera. Adivinar mal solo cuesta un 429, así
    que se leen los formatos vistos y se cae a la ventana por defecto.
    """

    def test_groq_con_minutos_y_segundos(self):
        """«Please try again in 10m53.184s»"""
        assert segundos_hasta_reintentar(GROQ_429) == pytest.approx(653.184)

    def test_gemini_con_segundos(self):
        """«Please retry in 25.342565212s»"""
        assert segundos_hasta_reintentar(GEMINI_429) == pytest.approx(25.34, abs=0.01)

    def test_el_formato_de_retryDelay(self):
        assert segundos_hasta_reintentar("{'retryDelay': '25s'}") == 25.0

    def test_gemini_sin_cupo_del_dia_no_se_reintenta_en_28_s(self):
        """4.1.5, medido: la capa gratuita de Gemini da 20 peticiones al día, y al
        agotarlas dice «retry in 28s». Fiarse era probarlo cada medio minuto todo el día."""
        diario = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current "
                  "quota ... limit: 20, model: gemini-3.6-flash\\nPlease retry in 28.281060935s.', "
                  "'details': [{'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', "
                  "'quotaValue': '20'}]}, {'retryDelay': '28s'}]}}")
        assert segundos_hasta_reintentar(diario) == VENTANA_MAXIMA

    def test_el_diario_de_groq_sigue_leyendo_su_espera(self):
        """Groq sí dice la espera de verdad del cupo por día («on tokens per day (TPD)»)."""
        assert segundos_hasta_reintentar(GROQ_429) == pytest.approx(653.184)

    def test_un_mensaje_que_no_lo_dice(self):
        assert segundos_hasta_reintentar("429 Too Many Requests") is None
        assert segundos_hasta_reintentar("") is None

    def test_una_ventana_absurda_se_acota(self):
        """Un mensaje mal interpretado no puede dejar a un proveedor fuera medio
        día.
        """
        espera = CUOTAS.marcar_agotado("x", 99_999)

        assert espera == VENTANA_MAXIMA

    def test_sin_pista_se_usa_la_ventana_por_defecto(self):
        espera = CUOTAS.marcar_agotado("x", None)

        assert 1 <= espera <= 300


class TestQueEsUnRechazoDeCuotaYQueNo:
    @pytest.mark.parametrize("mensaje", [
        GROQ_429, GEMINI_429,
        "Error code: 429",
        "rate_limit_exceeded",
        "429 RESOURCE_EXHAUSTED",
        "You exceeded your current quota",
        "Too Many Requests",
    ])
    def test_lo_es(self, mensaje):
        assert es_rechazo_por_cuota(RuntimeError(mensaje)) is True

    @pytest.mark.parametrize("mensaje", [
        "Connection reset by peer",
        "The read operation timed out",
        "401 Invalid API Key",
        "500 Internal Server Error",
        "No se pudo conectar con NVIDIA NIM",
    ])
    def test_no_lo_es(self, mensaje):
        """Confundir un 401 con un agotamiento sería grave: la clave inválida no
        se arregla esperando, y posponer el proveedor esconde el problema.
        """
        assert es_rechazo_por_cuota(RuntimeError(mensaje)) is False


class TestContarLoQueSeGasta:
    """El punto 3 del roadmap. Groq devolvía el gasto en cada respuesta y Morgan
    lo tiraba, así que la primera señal de haberse pasado era un 429 en mitad de
    un turno.
    """

    def test_se_acumula_llamada_a_llamada(self):
        CUOTAS.apuntar_uso("groq", 3000, 50)
        CUOTAS.apuntar_uso("groq", 2500, 40)

        r = CUOTAS.resumen()["groq"]
        assert r["tokens"] == 5590
        assert r["llamadas"] == 2

    def test_cada_proveedor_por_separado(self):
        CUOTAS.apuntar_uso("groq", 1000, 10)
        CUOTAS.apuntar_uso("OpenAI:gpt-5.6-luna", 2000, 20)

        r = CUOTAS.resumen()
        assert r["groq"]["tokens"] == 1010
        assert r["OpenAI:gpt-5.6-luna"]["tokens"] == 2020

    def test_se_avisa_al_acercarse_al_tope_conocido(self, caplog):
        """Con margen para hacer algo, que es el punto: antes el aviso era el
        propio 429.
        """
        with caplog.at_level("WARNING"):
            CUOTAS.apuntar_uso("groq", 170_000, 0)

        assert "170000" in caplog.text or "170.000" in caplog.text
        assert "200000" in caplog.text

    def test_no_se_avisa_por_debajo_del_umbral(self, caplog):
        with caplog.at_level("WARNING"):
            CUOTAS.apuntar_uso("groq", 1000, 10)

        assert "tokens diarios" not in caplog.text

    def test_de_un_proveedor_sin_tope_conocido_no_se_avisa(self, caplog):
        """No se inventa un límite que no se sabe. OpenAI es de pago y su tope
        lo pone su dueño en el panel, no Morgan.
        """
        with caplog.at_level("WARNING"):
            CUOTAS.apuntar_uso("OpenAI:gpt-5.6-luna", 5_000_000, 0)

        assert "tokens diarios" not in caplog.text

    def test_el_resumen_dice_si_esta_agotado_y_cuanto_falta(self):
        CUOTAS.marcar_agotado("groq", 120)

        r = CUOTAS.resumen()["groq"]
        assert r["agotado"] is True
        assert 100 <= r["vuelve_en"] <= 120


class TestElEstadoSobreviveALaInstancia:
    """Mismo error que ya se cometió dos veces en `ServicioDeCuentas`: un
    contador en `self` no frena nada cuando el objeto se construye a menudo.
    """

    def test_dos_cadenas_distintas_comparten_lo_que_saben(self):
        groq_1 = Proveedor("groq", error=GROQ_429)
        FallbackProvider(groq_1, Proveedor("r1")).generate([])

        groq_2 = Proveedor("groq")
        FallbackProvider(groq_2, Proveedor("r2")).generate([])

        assert groq_2.llamadas == 0, (
            "Una cadena nueva ha vuelto a llamar al agotado: el estado no se "
            "comparte y el ahorro no existe"
        )


class TestLaCapacidadDeVisionNoSeRompe:
    """Reordenar no puede colarse por delante del filtro de capacidades: mandar
    una imagen a un modelo sin visión no da una respuesta peor, da un error.
    """

    class ConVision(Proveedor):
        @property
        def capabilities(self):
            from src.models.capabilities import TEXTO_Y_VISION

            return TEXTO_Y_VISION

        def supports(self, capacidad):
            from src.models.capabilities import TEXTO_Y_VISION

            return capacidad in TEXTO_Y_VISION

    def test_un_agotado_con_vision_sigue_siendo_el_unico_candidato(self):
        solo_texto = Proveedor("solo-texto")
        con_vision = self.ConVision("con-vision")
        CUOTAS.marcar_agotado("con-vision", 300)

        respuesta = FallbackProvider(solo_texto, con_vision).generate(
            [], requires=Capability.VISION
        )

        assert respuesta.content == "soy con-vision", (
            "Se ha saltado al único proveedor con visión por estar agotado, y "
            "el de solo texto no puede atender la petición"
        )
