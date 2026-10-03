"""
La consola usa el mismo Morgan que la API (2.0.11).

Hasta la 2.0.10 la consola armaba su propio catálogo de herramientas, copiado del
de la API. La copia se quedó atrás: le faltaban **12 de 49** —el conocimiento, los
planes y GitHub— mientras un comentario en `main.py` aseguraba que las dos
compartían catálogo. La prueba que debía verlo comparaba en la dirección
equivocada: comprobaba que la consola no tuviera ninguna de más.

Ahora la consola se monta sobre el contenedor. Estas pruebas fijan las tres cosas
que ese cambio no puede perder:

- **el catálogo es el mismo objeto**, no uno igual;
- **la consola pregunta** antes de lo arriesgado, si hay alguien delante, aunque
  el contenedor esté pensado para la API, que deniega;
- **el registro no sale por pantalla**, que se mezclaría con la conversación.
"""

import logging

import pytest

from src.api import dependencies


@pytest.fixture
def consola(monkeypatch):
    from src import main
    from src.config import reset_settings

    monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
    reset_settings()
    dependencies.reset_container()
    return main


class TestElCatalogo:
    def test_es_el_mismo_que_el_de_la_api(self, consola):
        container = consola.preparar_consola()

        assert container.tool_registry is dependencies.get_container().tool_registry

    @pytest.mark.parametrize("herramienta", [
        "search_knowledge", "add_knowledge", "create_plan", "get_plan", "github_listar_repos",
    ])
    def test_tiene_las_que_le_faltaban(self, consola, herramienta):
        nombres = {t.name for t in consola.preparar_consola().tool_registry.list_tools()}

        assert herramienta in nombres

    def test_no_queda_una_construccion_propia(self, consola):
        """Si vuelve a aparecer, vuelve a poder divergir."""
        assert not hasattr(consola, "build_tool_registry")


class TestLosPermisos:
    def test_con_alguien_delante_pregunta(self, consola, monkeypatch):
        monkeypatch.setattr(consola, "_detect_interactive", lambda: True)

        container = consola.preparar_consola()

        assert container.permission_manager.interactive is True
        if container.agent is not None:
            assert container.agent.permissions is container.permission_manager

    def test_sin_consola_deniega_como_la_api(self, consola, monkeypatch):
        monkeypatch.setattr(consola, "_detect_interactive", lambda: False)

        assert consola.preparar_consola().permission_manager.interactive is False


class TestElRegistro:
    def test_no_sale_por_pantalla(self, consola, monkeypatch):
        raiz = logging.getLogger("src")
        monkeypatch.setattr(raiz, "handlers", [])
        monkeypatch.delattr(raiz, "_morgan_configured", raising=False)

        consola.preparar_consola()

        por_pantalla = [
            h for h in raiz.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert not por_pantalla, "el registro se mezclaría con la conversación"
