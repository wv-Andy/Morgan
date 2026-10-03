"""
Capacidades por proveedor (V1.4).

Hasta la V1.3 todos los proveedores hacían lo mismo y eran intercambiables. Con la
entrada multimodal dejan de serlo, y la cadena de respaldo ya no puede conmutar a
ciegas: mandar una imagen a un modelo sin visión no da una respuesta peor, da un
error, y además gasta una llamada y su tiempo de espera antes de conmutar.
"""

import pytest

from src.models.base import ChatMessage, LLMResponse
from src.models.capabilities import (
    SOLO_TEXTO,
    TEXTO_Y_VISION,
    CapacidadNoDisponible,
    Capability,
    capacidades_de_modelo_groq,
)
from src.models.fallback import FallbackProvider
from src.models.mock import MockLLMProvider


class ConVision(MockLLMProvider):
    @property
    def capabilities(self) -> frozenset[Capability]:
        return TEXTO_Y_VISION

    @property
    def model_name(self) -> str:
        return "con-vision"


class SoloTexto(MockLLMProvider):
    @property
    def model_name(self) -> str:
        return "solo-texto"


class Roto(SoloTexto):
    def generate(self, messages, tools=None, system_prompt=None):
        raise RuntimeError("este proveedor no responde")


class TestElValorPorDefectoEsSeguro:
    """Misma decisión que `requires_local` en las herramientas: quien olvide
    declararlo se queda del lado seguro."""

    def test_un_proveedor_que_no_declara_nada_es_solo_texto(self):
        assert MockLLMProvider().capabilities == SOLO_TEXTO

    def test_y_por_tanto_no_admite_imagenes(self):
        assert not MockLLMProvider().supports(Capability.VISION)

    def test_los_proveedores_reales_declaran_lo_suyo(self):
        """Se comprueba la clase, no una instancia: crearla exige credenciales."""
        from unittest.mock import patch

        with patch("src.models.gemini.genai"):
            from src.models.gemini import GeminiProvider

            gemini = GeminiProvider(api_key="clave-de-prueba")

        assert gemini.supports(Capability.VISION)


class TestLasCapacidadesDeGroqDependenDelModelo:
    """Groq sirve familias muy distintas bajo la misma API."""

    @pytest.mark.parametrize("modelo", ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"])
    def test_los_modelos_de_texto_no_declaran_vision(self, modelo):
        assert Capability.VISION not in capacidades_de_modelo_groq(modelo)

    @pytest.mark.parametrize("modelo", [
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "meta-llama/llama-4-maverick-17b",
        "llava-v1.5-7b",
    ])
    def test_los_modelos_con_vision_si(self, modelo):
        assert Capability.VISION in capacidades_de_modelo_groq(modelo)

    def test_un_modelo_desconocido_se_queda_en_texto(self):
        """Preferible negarse a enviar una imagen que el modelo habría entendido,
        que mandársela a uno que no."""
        assert capacidades_de_modelo_groq("modelo-que-nadie-conoce") == SOLO_TEXTO

    def test_un_nombre_vacio_no_revienta(self):
        assert capacidades_de_modelo_groq("") == SOLO_TEXTO


class TestLaCadenaFiltraPorCapacidad:
    def test_declara_la_union_de_sus_eslabones(self):
        """Union y no interseccion: la cadena puede atender una imagen mientras
        exista un eslabón que la entienda, aunque el principal no."""
        cadena = FallbackProvider(SoloTexto(), ConVision())

        assert cadena.capabilities == TEXTO_Y_VISION

    def test_para_texto_valen_todos(self):
        cadena = FallbackProvider(SoloTexto(), ConVision())

        assert len(cadena.providers_for(Capability.TEXT)) == 2

    def test_para_vision_solo_el_que_la_admite(self):
        cadena = FallbackProvider(SoloTexto(), ConVision())

        candidatos = cadena.providers_for(Capability.VISION)

        assert [p.model_name for p in candidatos] == ["con-vision"]

    def test_no_se_le_pide_una_imagen_al_que_no_puede(self):
        """El punto entero de esta abstracción."""
        principal = SoloTexto()
        llamadas = []
        principal.generate = lambda *a, **k: llamadas.append("principal")  # type: ignore[method-assign]

        FallbackProvider(principal, ConVision()).generate(
            [ChatMessage(role="user", content="mira esto")],
            requires=Capability.VISION,
        )

        assert llamadas == [], "se llamó al proveedor sin visión"

    def test_sin_ningun_candidato_falla_rapido_y_claro(self):
        cadena = FallbackProvider(SoloTexto(), SoloTexto())

        with pytest.raises(CapacidadNoDisponible) as fallo:
            cadena.generate([], requires=Capability.VISION)

        assert fallo.value.capacidad is Capability.VISION
        assert "solo-texto" in str(fallo.value)

    def test_sin_candidatos_no_se_gasta_ninguna_llamada(self):
        """No hay a quién conmutar: agotar la cadena probando sería tiempo tirado."""
        roto = Roto()
        intentos = []
        roto.generate = lambda *a, **k: intentos.append(1)  # type: ignore[method-assign]

        with pytest.raises(CapacidadNoDisponible):
            FallbackProvider(roto).generate([], requires=Capability.VISION)

        assert intentos == []

    def test_entre_los_candidatos_sigue_habiendo_respaldo(self):
        """Filtrar por capacidad no puede cargarse la tolerancia a fallos."""
        cadena = FallbackProvider(ConVision(), Roto(), ConVision())

        respuesta = cadena.generate([], requires=Capability.VISION)

        assert isinstance(respuesta, LLMResponse)

    def test_el_comportamiento_de_texto_no_cambia(self):
        """La V1.3 no debe notar nada: `requires` vale TEXT por defecto."""
        cadena = FallbackProvider(Roto(), SoloTexto())

        assert isinstance(cadena.generate([]), LLMResponse)


class TestElMensajeParaLaPersona:
    def test_no_se_confunde_con_un_fallo_del_proveedor(self):
        from src.models.errors import describe_llm_error

        mensaje = describe_llm_error(CapacidadNoDisponible(Capability.VISION))

        assert "imágenes" in mensaje
        assert "log" not in mensaje, "no es un error técnico que consultar"

    def test_el_de_audio_dice_lo_suyo(self):
        from src.models.errors import describe_llm_error

        assert "audio" in describe_llm_error(CapacidadNoDisponible(Capability.AUDIO))
