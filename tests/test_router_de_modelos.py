"""
El router de modelos, primera parte: relevos por capacidad (V2.0.20).

El roadmap maestro pedía «elegir el modelo adecuado para cada tarea» y avisaba
de no adelantarlo hasta que hubiera modelos con perfiles de verdad distintos.
Se midió y los hay: **la cuota de Groq es por modelo**, así que
`openai/gpt-oss-20b` es otro cupo diario y otro cupo por minuto con las mismas
claves, gratis. Detalle y números en docs/modelos.md.

Lo que fijan estas pruebas:

1. **La configuración.** Un relevo de serie; `GROQ_MODELOS_RELEVO=` vacío lo quita.
2. **La cadena.** El relevo va justo detrás de Groq, antes de Gemini y del de pago.
3. **Las etiquetas del llavero llevan el modelo.** La clave 1 agotada para 120b
   no está agotada para 20b.
4. **Un relevo solo entra tras un rechazo por cuota.** Si Groq está caído, otro
   modelo de Groq fallaría igual y solo retrasaría llegar a Gemini.

Nunca se llama a Groq de verdad.
"""

import pytest

from src.config import load_settings, reset_settings
from src.models.base import LLMProvider, LLMResponse
from src.models.chain import build_provider_chain
from src.models.cuota import CUOTAS, CUOTA_DIARIA_CONOCIDA, familia
from src.models.fallback import FallbackProvider

CLAVE_FALSA = "gsk_clave-de-prueba-que-no-sirve-para-nada"
OTRA_CLAVE = "gsk_otra-clave-de-prueba-que-tampoco-sirve"
PRINCIPAL = "openai/gpt-oss-120b"
RELEVO = "openai/gpt-oss-20b"


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    from src.config import MAXIMO_CLAVES_POR_PROVEEDOR, PROVEEDORES_CONOCIDOS

    # Tambien las numeradas: el .env de quien ejecuta puede traer GROQ_API_KEY_2.
    for p in PROVEEDORES_CONOCIDOS:
        monkeypatch.setenv(f"{p.upper()}_API_KEY", "")
        for n in range(2, MAXIMO_CLAVES_POR_PROVEEDOR + 1):
            monkeypatch.setenv(f"{p.upper()}_API_KEY_{n}", "")
    monkeypatch.delenv("GROQ_MODELOS_RELEVO", raising=False)
    monkeypatch.setenv("GROQ_MODEL_NAME", PRINCIPAL)
    monkeypatch.delenv("MORGAN_LLM_ORDER", raising=False)
    CUOTAS.reiniciar()
    reset_settings()
    yield
    CUOTAS.reiniciar()
    reset_settings()


# ── 1. La configuración ────────────────────────────────────────────────────────


class TestLaConfiguracion:
    def test_de_serie_hay_un_relevo_y_es_20b(self):
        assert load_settings().groq_modelos_relevo == (RELEVO,)

    def test_vacia_los_quita(self, monkeypatch):
        monkeypatch.setenv("GROQ_MODELOS_RELEVO", "")
        assert load_settings().groq_modelos_relevo == ()

    def test_admite_varios_sin_repetir_ni_espacios(self, monkeypatch):
        monkeypatch.setenv("GROQ_MODELOS_RELEVO", f" {RELEVO} ,otro/modelo,{RELEVO},")
        assert load_settings().groq_modelos_relevo == (RELEVO, "otro/modelo")


# ── 2. La cadena ───────────────────────────────────────────────────────────────


class TestLaCadena:
    def test_el_relevo_va_justo_detras_de_groq_y_antes_que_los_demas(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-clave-falsa-de-gemini-con-longitud")
        monkeypatch.setenv("MORGAN_LLM_ORDER", "groq,gemini")

        modelo, activos = build_provider_chain(load_settings())

        assert activos == ["groq", f"groq@{RELEVO}", "gemini"]
        assert isinstance(modelo, FallbackProvider)
        assert modelo.providers[1].model_name == f"Groq:{RELEVO}"

    def test_el_relevo_usa_las_mismas_claves(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        monkeypatch.setenv("GROQ_API_KEY_2", OTRA_CLAVE)

        modelo, _ = build_provider_chain(load_settings())

        assert modelo.providers[1].claves_disponibles == 2

    def test_un_relevo_igual_al_principal_no_se_duplica(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        monkeypatch.setenv("GROQ_MODELOS_RELEVO", PRINCIPAL)
        monkeypatch.setenv("MORGAN_LLM_ORDER", "groq")

        _, activos = build_provider_chain(load_settings())

        assert activos == ["groq"]

    def test_sin_groq_no_hay_relevos(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-clave-falsa-de-gemini-con-longitud")

        _, activos = build_provider_chain(load_settings())

        assert not any(a.startswith("groq") for a in activos)


# ── 3. Las etiquetas del llavero ───────────────────────────────────────────────


class TestLasEtiquetas:
    def _proveedores(self, monkeypatch):
        from src.models.groq import GroqProvider

        reset_settings()
        claves = (CLAVE_FALSA, OTRA_CLAVE)
        return GroqProvider(api_key=claves), GroqProvider(api_key=claves, model_name=RELEVO)

    def test_el_principal_conserva_las_de_siempre(self, monkeypatch):
        principal, _ = self._proveedores(monkeypatch)
        assert principal._llavero.etiquetas == ("Groq#1", "Groq#2")
        assert principal.es_relevo_de_cuota is False

    def test_el_relevo_lleva_el_modelo(self, monkeypatch):
        _, relevo = self._proveedores(monkeypatch)
        assert relevo._llavero.etiquetas == (f"Groq@{RELEVO}#1", f"Groq@{RELEVO}#2")
        assert relevo.es_relevo_de_cuota is True

    def test_una_clave_agotada_para_120b_no_lo_esta_para_20b(self, monkeypatch):
        principal, relevo = self._proveedores(monkeypatch)
        CUOTAS.marcar_agotado("Groq#1", 600)

        assert [e for e, _ in principal._llavero.turnos()][0] == "Groq#2"
        assert [e for e, _ in relevo._llavero.turnos()][0] == f"Groq@{RELEVO}#1"

    def test_el_relevo_sigue_encontrando_el_tope_de_groq(self):
        assert familia(f"Groq@{RELEVO}#2") == "groq"
        assert familia(f"Groq@{RELEVO}") == "groq"
        CUOTAS.apuntar_uso(f"Groq@{RELEVO}#1", 10, 5)
        assert CUOTAS.resumen()[f"Groq@{RELEVO}#1"]["tope_conocido"] == CUOTA_DIARIA_CONOCIDA["groq"]


# ── 4. Cuándo entra el relevo ──────────────────────────────────────────────────


class Falso(LLMProvider):
    def __init__(self, nombre, fallo=None, relevo=False):
        self._n = nombre
        self._fallo = fallo
        self.es_relevo_de_cuota = relevo
        self.llamadas = 0

    @property
    def model_name(self):
        return self._n

    def generate(self, messages, tools=None, system_prompt=None, requires=None):
        self.llamadas += 1
        if self._fallo:
            raise self._fallo
        return LLMResponse(type="text", content=f"contesta {self._n}")


CUOTA = RuntimeError("Error code: 429 - Rate limit reached ... tokens per day (TPD)")
CAIDO = RuntimeError("Error code: 503 - Service Unavailable")


class TestCuandoEntraElRelevo:
    def test_tras_un_rechazo_por_cuota_contesta_el_relevo(self):
        principal = Falso("Groq:120b", fallo=CUOTA)
        relevo = Falso("Groq:20b", relevo=True)
        gemini = Falso("Gemini")

        r = FallbackProvider(principal, relevo, gemini).generate([])

        assert r.content == "contesta Groq:20b"
        assert gemini.llamadas == 0

    def test_si_groq_esta_caido_se_salta_el_relevo(self):
        """Otro modelo de Groq fallaría igual: solo retrasaría llegar a Gemini."""
        principal = Falso("Groq:120b", fallo=CAIDO)
        relevo = Falso("Groq:20b", relevo=True)
        gemini = Falso("Gemini")

        r = FallbackProvider(principal, relevo, gemini).generate([])

        assert r.content == "contesta Gemini"
        assert relevo.llamadas == 0

    def test_con_el_principal_ya_agotado_se_empieza_por_el_relevo(self):
        principal = Falso("Groq:120b", fallo=CUOTA)
        relevo = Falso("Groq:20b", relevo=True)
        gemini = Falso("Gemini")
        CUOTAS.marcar_agotado("Groq:120b", 600)

        r = FallbackProvider(principal, relevo, gemini).generate([])

        assert r.content == "contesta Groq:20b"
        assert principal.llamadas == 0

    def test_si_el_relevo_tambien_se_agota_sigue_la_cadena(self):
        principal = Falso("Groq:120b", fallo=CUOTA)
        relevo = Falso("Groq:20b", fallo=CUOTA, relevo=True)
        gemini = Falso("Gemini")

        r = FallbackProvider(principal, relevo, gemini).generate([])

        assert r.content == "contesta Gemini"
        assert relevo.llamadas == 1

    def test_un_eslabon_normal_no_se_salta_nunca(self):
        """La regla es solo para relevos: Gemini tras un Groq caído entra siempre."""
        principal = Falso("Groq:120b", fallo=CAIDO)
        gemini = Falso("Gemini")

        assert FallbackProvider(principal, gemini).generate([]).content == "contesta Gemini"

    def test_que_conteste_el_relevo_se_dice(self):
        """La web enseña «de respaldo»: es otro modelo, y decirlo no es ruido."""
        from src.observabilidad import midiendo

        principal = Falso("Groq:120b", fallo=CUOTA)
        relevo = Falso("Groq:20b", relevo=True)

        with midiendo() as medicion:
            FallbackProvider(principal, relevo).generate([])

        assert medicion.datos.get("respaldo") == "si"
        assert medicion.datos.get("proveedor") == "Groq:20b"


    def test_con_el_principal_ya_agotado_tambien_se_dice(self):
        """Quedar primero en la lista no lo convierte en el principal.

        Comparaba la posición: con 120b agotado, el relevo pasaba a la cabeza
        y contestaba sin que la web dijera «de respaldo». Medido de punta a
        punta contra Groq antes de arreglarlo.
        """
        from src.observabilidad import midiendo

        principal = Falso("Groq:120b", fallo=CUOTA)
        relevo = Falso("Groq:20b", relevo=True)
        CUOTAS.marcar_agotado("Groq:120b", 600)

        with midiendo() as medicion:
            FallbackProvider(principal, relevo).generate([])

        assert medicion.datos.get("respaldo") == "si"


class TestCuandoLaPeticionNoCabe:
    """Medido en producción el 2026-09-19, con mi PC conectado: una llamada de
    8.459 tokens (el tope de Groq son 8.000 por minuto) se probó en `gpt-oss-120b` y
    después en `gpt-oss-20b`, que comparte tope, antes de pasar a Gemini. Groq manda ese
    rechazo como `rate_limit_exceeded`, así que parecía falta de cuota."""

    DEMASIADO_GRANDE = ("Error code: 413 - {'error': {'message': 'Request too large for model "
                        "`openai/gpt-oss-20b` ... on tokens per minute (TPM): Limit 8000, "
                        "Requested 8459, please reduce your message size and try again', "
                        "'code': 'rate_limit_exceeded'}}")

    def test_se_distingue_de_la_falta_de_cuota(self):
        from src.models.cuota import es_rechazo_por_cuota, es_rechazo_por_tamano

        grande = RuntimeError(self.DEMASIADO_GRANDE)
        assert es_rechazo_por_tamano(grande)
        assert es_rechazo_por_cuota(grande)          # lo parece, y por eso hay que mirar

        sin_cuota = RuntimeError("Error code: 429 - Rate limit reached (TPD)")
        assert es_rechazo_por_cuota(sin_cuota) and not es_rechazo_por_tamano(sin_cuota)

    def test_no_se_prueba_otro_modelo_del_mismo_proveedor(self):
        from src.models.base import LLMResponse
        from src.models.fallback import FallbackProvider

        intentos = []

        class Eslabon:
            capabilities = frozenset()

            def supports(self, _capacidad) -> bool:
                return True

            def __init__(self, nombre, fallo=None, relevo=False):
                self.model_name = nombre
                self.fallo = fallo
                self.es_relevo_de_cuota = relevo

            def generate(self, messages, tools=None, system_prompt=None):
                intentos.append(self.model_name)
                if self.fallo:
                    raise self.fallo
                return LLMResponse(type="text", content=f"contesta {self.model_name}")

        cadena = FallbackProvider(
            Eslabon("Groq:120b", fallo=RuntimeError(self.DEMASIADO_GRANDE)),
            Eslabon("Groq:20b", relevo=True),
            Eslabon("gemini"),
        )
        respuesta = cadena.generate([{"role": "user", "content": "hola"}])

        assert respuesta.content == "contesta gemini"
        assert intentos == ["Groq:120b", "gemini"], "se probó un modelo con el mismo tope"
