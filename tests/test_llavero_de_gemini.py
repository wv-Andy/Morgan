"""
Varias claves de Gemini (4.1.5, decisión mía).

La capa gratuita de Gemini da 20 peticiones al día por proyecto y modelo, y es el único
proveedor con visión (medido: Groq ya no sirve modelos con visión). Cada clave de otro
proyecto es otro cupo: se gastan en serie, como las de Groq, y un rechazo por cuota pasa a
la siguiente sin cambiar de modelo.
"""

from types import SimpleNamespace

import pytest

from src.models.base import ChatMessage
from src.models.cuota import CUOTAS

DIARIO = ("429 RESOURCE_EXHAUSTED. {'error': {'message': 'You exceeded your current quota', "
          "'details': [{'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}")


def _respuesta(texto):
    parte = SimpleNamespace(text=texto, function_call=None)
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[parte]))],
                           usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=2))


class Cliente:
    def __init__(self, nombre, falla=None):
        self.nombre, self.falla, self.llamadas = nombre, falla, 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.llamadas += 1
        if self.falla:
            raise RuntimeError(self.falla)
        return _respuesta(f"hola desde {self.nombre}")


@pytest.fixture
def gemini(monkeypatch):
    from src.models import gemini as modulo

    for etiqueta in ("Gemini#1", "Gemini#2"):
        CUOTAS._estados.pop(etiqueta, None)
    proveedor = modulo.GeminiProvider(api_key=("clave-uno", "clave-dos"))
    yield proveedor
    for etiqueta in ("Gemini#1", "Gemini#2"):
        CUOTAS._estados.pop(etiqueta, None)


def _preguntar(proveedor):
    return proveedor.generate([ChatMessage(role="user", content="hola")])


class TestSeGastanEnSerie:
    def test_si_la_primera_no_tiene_cupo_contesta_la_segunda(self, gemini):
        uno, dos = Cliente("uno", falla=DIARIO), Cliente("dos")
        gemini._clientes = {"Gemini#1": uno, "Gemini#2": dos}
        assert _preguntar(gemini).content == "hola desde dos"
        assert CUOTAS.agotado("Gemini#1") and not CUOTAS.agotado("Gemini#2")

    def test_la_agotada_no_se_vuelve_a_probar_enseguida(self, gemini):
        uno, dos = Cliente("uno", falla=DIARIO), Cliente("dos")
        gemini._clientes = {"Gemini#1": uno, "Gemini#2": dos}
        _preguntar(gemini)
        _preguntar(gemini)
        assert uno.llamadas == 1 and dos.llamadas == 2

    def test_sin_cupo_en_ninguna_se_lanza_para_que_la_cadena_siga(self, gemini):
        gemini._clientes = {"Gemini#1": Cliente("uno", falla=DIARIO), "Gemini#2": Cliente("dos", falla=DIARIO)}
        with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
            _preguntar(gemini)

    def test_otro_error_no_gasta_la_segunda(self, gemini):
        uno, dos = Cliente("uno", falla="400 INVALID_ARGUMENT"), Cliente("dos")
        gemini._clientes = {"Gemini#1": uno, "Gemini#2": dos}
        with pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
            _preguntar(gemini)
        assert dos.llamadas == 0


class TestLaConfiguracion:
    def test_gemini_api_key_2_suma_una_clave(self, monkeypatch):
        from src.config import reset_settings, get_settings

        monkeypatch.setenv("GEMINI_API_KEY", "clave-uno")
        monkeypatch.setenv("GEMINI_API_KEY_2", "clave-dos")
        reset_settings()
        try:
            ajustes = get_settings()
            assert ajustes.gemini_api_keys == ("clave-uno", "clave-dos")
            assert ajustes.gemini_api_key == "clave-uno"
        finally:
            reset_settings()

    def test_la_revision_de_claves_nombra_cada_variable(self, monkeypatch):
        from src.config import reset_settings, get_settings
        from src.models.chain import _claves_de

        monkeypatch.setenv("GEMINI_API_KEY", "clave-uno")
        monkeypatch.setenv("GEMINI_API_KEY_2", "clave-dos")
        reset_settings()
        try:
            assert [v for v, _ in _claves_de("gemini", get_settings())] == ["GEMINI_API_KEY", "GEMINI_API_KEY_2"]
        finally:
            reset_settings()
