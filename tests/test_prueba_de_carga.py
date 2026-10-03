"""
El modo de prueba de carga (src/prueba_de_carga.py).

Quita frenos y pone un modelo de mentira, así que lo que más importa es **dónde
no puede encenderse**: con claves de modelos reales o contra el Supabase de
producción, el servidor no arranca.
"""

import hashlib

import pytest

from src.config import get_settings, reset_settings

#: Un proyecto inventado que hace de producción: en el código solo está la huella del de
#: verdad (el código es público), así que las pruebas ponen la del inventado.
PRODUCCION = "proyectodeproduccion"


@pytest.fixture
def produccion(monkeypatch):
    from src import prueba_de_carga

    monkeypatch.setattr(prueba_de_carga, "HUELLA_DE_PRODUCCION",
                        hashlib.sha256(PRODUCCION.encode()).hexdigest())
    return f"https://{PRODUCCION}.supabase.co"


@pytest.fixture
def pedido(monkeypatch):
    monkeypatch.setenv("MORGAN_PRUEBA_DE_CARGA", "1")
    reset_settings()
    yield
    reset_settings()


class TestDondeNoSeEnciende:
    @pytest.mark.parametrize("clave", ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"])
    def test_con_una_clave_de_modelo_real_no_arranca(self, pedido, monkeypatch, clave):
        from src.api.server import ArranqueInseguro, check_startup_safety

        monkeypatch.setenv(clave, "clave-de-mentira")
        reset_settings()

        with pytest.raises(ArranqueInseguro, match="claves de modelos reales"):
            check_startup_safety(get_settings())

    def test_contra_el_supabase_de_produccion_no_arranca(self, pedido, produccion, monkeypatch):
        from src.api.server import ArranqueInseguro, check_startup_safety

        monkeypatch.setenv("SUPABASE_URL", produccion)
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "x")
        reset_settings()

        with pytest.raises(ArranqueInseguro, match="producción"):
            check_startup_safety(get_settings())

    def test_la_cadena_tampoco_lo_enciende_a_escondidas(self, pedido, monkeypatch):
        """Aunque alguien se saltara la comprobación de arranque, montar la cadena
        con el modo pedido y una clave real falla en lugar de usar el simulado."""
        from src.models.chain import build_provider_chain
        from src.prueba_de_carga import PruebaDeCargaInsegura

        monkeypatch.setenv("GROQ_API_KEY", "clave-de-mentira")
        reset_settings()

        with pytest.raises(PruebaDeCargaInsegura):
            build_provider_chain(get_settings())


class TestDondeSi:
    def test_sin_claves_monta_el_modelo_simulado(self, pedido):
        from src.models.chain import build_provider_chain
        from src.prueba_de_carga import NOMBRE_DEL_MODELO

        modelo, activos = build_provider_chain(get_settings())

        assert modelo.model_name == NOMBRE_DEL_MODELO
        assert activos == ["prueba_de_carga"]

    def test_y_levanta_los_frenos_de_altas(self, pedido, monkeypatch):
        from src.identidad import cuentas
        from src.models.chain import build_provider_chain

        monkeypatch.setattr(cuentas, "MAX_REGISTROS_EN_TOTAL", 20)
        monkeypatch.setattr(cuentas, "MAX_REGISTROS_POR_ORIGEN", 3)
        build_provider_chain(get_settings())

        assert cuentas.MAX_REGISTROS_EN_TOTAL > 1000
        assert cuentas.MAX_REGISTROS_POR_ORIGEN > 1000

    def test_sin_pedirlo_no_cambia_nada(self):
        from src.identidad import cuentas
        from src.models.chain import build_provider_chain

        antes = cuentas.MAX_REGISTROS_EN_TOTAL
        modelo, _ = build_provider_chain(get_settings())

        assert modelo is None  # sin claves en la suite: modo degradado, como siempre
        assert cuentas.MAX_REGISTROS_EN_TOTAL == antes


class TestElModeloSimulado:
    def test_contesta_o_pide_la_herramienta_segun_el_mensaje(self):
        from src.models.base import ChatMessage
        from src.prueba_de_carga import ModeloDePrueba

        modelo = ModeloDePrueba(latencia_ms=0)

        assert modelo.generate([ChatMessage(role="user", content="hola")]).type == "text"
        pide = modelo.generate([ChatMessage(role="user", content="Recuerda que me gusta el verde")])
        assert pide.type == "tool_call" and pide.tool_calls[0].name == "remember_fact"


class TestLaHuellaDeProduccion:
    """El código no dice cuál es el Supabase de producción, solo su huella (4.19)."""

    def test_reconoce_el_proyecto_por_su_huella(self, produccion):
        from src.prueba_de_carga import es_produccion

        assert es_produccion(produccion)
        assert es_produccion(produccion + "/rest/v1/")

    def test_otro_proyecto_no(self, produccion):
        from src.prueba_de_carga import es_produccion

        assert not es_produccion("https://otroproyecto.supabase.co")
        assert not es_produccion(None) and not es_produccion("")
        # El nombre en otro sitio de la URL no la convierte en la de producción.
        assert not es_produccion(f"https://otro.supabase.co/{PRODUCCION}")

    def test_la_huella_es_una_huella(self):
        from src.prueba_de_carga import HUELLA_DE_PRODUCCION

        assert len(HUELLA_DE_PRODUCCION) == 64 and int(HUELLA_DE_PRODUCCION, 16) >= 0
