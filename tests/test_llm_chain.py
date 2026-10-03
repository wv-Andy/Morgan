"""
Pruebas de la cadena de proveedores LLM.

La cadena admite N eslabones (antes solo dos) y se construye en un único sitio,
compartido por la API y la CLI: antes estaba duplicada y añadir un proveedor
obligaba a tocar los dos, con el riesgo de que se desincronizaran.
"""

import pytest

from src.models.base import LLMProvider
from src.models.chain import DEFAULT_ORDER, build_provider_chain
from src.models.fallback import FallbackProvider
from src.models.mock import MockLLMProvider

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



class Caido(LLMProvider):
    """Proveedor que siempre falla, para forzar la conmutación."""

    def __init__(self, nombre="caido"):
        self._n = nombre
        self.llamadas = 0

    @property
    def model_name(self):
        return self._n

    def generate(self, messages, tools=None, system_prompt=None):
        self.llamadas += 1
        raise RuntimeError(f"{self._n} no disponible")


class TestCadenaDeProveedores:
    def test_tres_eslabones_y_responde_el_tercero(self):
        tercero = MockLLMProvider()
        tercero.queue_text("respondo yo")

        cadena = FallbackProvider(Caido("a"), Caido("b"), tercero)

        assert cadena.generate([]).content == "respondo yo"
        assert cadena.fallback_uses == 1
        assert cadena.primary_failures == 1

    def test_el_nombre_lista_todos_los_respaldos(self):
        cadena = FallbackProvider(Caido("a"), Caido("b"), Caido("c"))

        assert "a" in cadena.model_name
        assert "b" in cadena.model_name and "c" in cadena.model_name

    def test_si_caen_todos_se_propaga_el_ultimo_error(self):
        cadena = FallbackProvider(Caido("a"), Caido("b"), Caido("c"))

        with pytest.raises(RuntimeError) as exc:
            cadena.generate([])

        assert "c no disponible" in str(exc.value)

    def test_un_solo_proveedor_sigue_funcionando(self):
        """Con uno solo no se envuelve en nada: envolverlo no aportaría."""
        unico = MockLLMProvider()
        unico.queue_text("solo")

        assert FallbackProvider(unico).generate([]).content == "solo"

    def test_no_se_llama_a_los_siguientes_si_el_primero_responde(self):
        principal = MockLLMProvider()
        principal.queue_text("ok")
        segundo = Caido("b")

        FallbackProvider(principal, segundo).generate([])

        assert segundo.llamadas == 0


#: Se derivan de `PROVEEDORES_CONOCIDOS` en lugar de escribirse a mano, y hay
#: motivo: cuando esta lista era literal, añadir OpenAI dejó tres pruebas
#: rojas porque nadie se acordó de limpiar su clave, y la del `.env` de verdad
#: se colaba en las pruebas. Derivarla hace que el próximo proveedor no pueda
#: olvidarse.
def _claves_de_proveedor() -> tuple[str, ...]:
    from src.config import PROVEEDORES_CONOCIDOS

    return tuple(f"{p.upper()}_API_KEY" for p in PROVEEDORES_CONOCIDOS)


CLAVES_DE_PROVEEDOR = _claves_de_proveedor()


def _sin_ninguna_clave(monkeypatch):
    for var in CLAVES_DE_PROVEEDOR:
        monkeypatch.setenv(var, "")


class TestConstruccionDeLaCadena:
    def test_sin_claves_no_hay_proveedor(self, monkeypatch):
        from src.config import load_settings

        _sin_ninguna_clave(monkeypatch)

        modelo, activos = build_provider_chain(load_settings())

        assert modelo is None
        assert activos == []

    def test_el_orden_por_defecto_pone_groq_primero(self):
        """Groq es el más rápido según lo medido: 0,42 s de media."""
        assert DEFAULT_ORDER[0] == "groq"

    def test_el_orden_es_configurable(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("MORGAN_LLM_ORDER", "gemini,groq")

        assert load_settings().llm_order == ("gemini", "groq")

    def test_un_proveedor_desconocido_en_el_orden_se_ignora(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("MORGAN_LLM_ORDER", "groq,inventado")

        assert load_settings().llm_order == ("groq",)

    def test_un_orden_totalmente_invalido_cae_al_de_defecto(self, monkeypatch):
        from src.config import load_settings

        monkeypatch.setenv("MORGAN_LLM_ORDER", "nada,inventado")

        assert load_settings().llm_order == DEFAULT_ORDER

    def test_con_una_sola_clave_no_se_envuelve_en_cadena(self, monkeypatch):
        from src.config import load_settings

        _sin_ninguna_clave(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_FALSA)
        # Sin relevos: desde la 2.0.20 Groq trae uno de serie, que es otro
        # eslabon. Lo que se prueba aqui es que UN proveedor no se envuelve.
        monkeypatch.setenv("GROQ_MODELOS_RELEVO", "")

        modelo, activos = build_provider_chain(load_settings())

        assert activos == ["groq"]
        assert not isinstance(modelo, FallbackProvider)


class TestElOrdenSeDefineUnaSolaVez:
    """Estaba escrito dos veces: en `Settings.llm_order` y en
    `chain.DEFAULT_ORDER`. Al reordenar la cadena se cambió uno y los dos
    dejaron de coincidir, así que `load_settings()` devolvía un orden y el
    módulo de la cadena usaba otro como respaldo.

    Lo cazó una prueba que ya existía. De no haberla, habría quedado un
    desacuerdo silencioso sobre qué proveedor va primero.
    """

    def test_los_dos_sitios_dicen_lo_mismo(self):
        from src.config import Settings
        from src.models.chain import DEFAULT_ORDER

        assert DEFAULT_ORDER == Settings.llm_order, (
            "El orden por defecto está definido dos veces y ya no coinciden. "
            f"chain dice {DEFAULT_ORDER} y config dice {Settings.llm_order}"
        )

    def test_el_de_pago_va_ultimo(self):
        """La regla que sustituye a «NVIDIA va última», y es más general.

        **El que cobra va al final**, y no por ser peor: medido, OpenAI es el
        más fiable de los cuatro (1,0 s de texto, 1,7 s con herramientas, sin un
        solo fallo). Va último porque mientras quede cuota gratuita no hay razón
        para pagar, así que el dinero solo se gasta cuando Groq y Gemini se
        agotan — que es justo cuando Morgan dejaba de contestar.

        La prueba anterior fijaba que NVIDIA fuera la última, por su latencia
        impredecible. Sigue siendo verdad que lo era, y ya no está en la cadena:
        la auditoría de la V2.0.2 midió quince llamadas en producción con quince
        plazos agotados y ningún éxito.
        """
        from src.config import Settings

        assert Settings.llm_order[-1] == "openai", (
            f"El proveedor de pago ya no va último: {Settings.llm_order}. "
            "Delante de uno gratuito, se paga por lo que era gratis."
        )

    def test_nvidia_ya_no_esta_en_la_cadena_pero_sigue_existiendo(self):
        """Quitarlo del orden por defecto no es borrarlo. Su código está entero
        y con pruebas, y basta una variable de entorno para volver a activarlo.
        """
        from src.config import PROVEEDORES_CONOCIDOS, Settings

        assert "nvidia" not in Settings.llm_order
        assert "nvidia" in PROVEEDORES_CONOCIDOS, (
            "Si se borra de los conocidos, deja de poder activarse a mano"
        )

    def test_todo_lo_que_esta_en_el_orden_es_un_proveedor_conocido(self):
        """El invariante que ataba las dos listas, ahora explícito. Un nombre
        mal escrito en el orden por defecto dejaría a Morgan sin ese eslabón
        y sin decir nada.
        """
        from src.config import PROVEEDORES_CONOCIDOS, Settings

        desconocidos = set(Settings.llm_order) - set(PROVEEDORES_CONOCIDOS)
        assert not desconocidos, f"En el orden y sin existir: {desconocidos}"

    def test_y_groq_primero(self):
        from src.config import Settings

        assert Settings.llm_order[0] == "groq", "Groq es el más rápido medido"
