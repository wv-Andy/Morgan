"""
El prompt de sistema no manda usar herramientas que el modelo no tiene.

## El defecto

El prompt pedía seguir un «ciclo de autocorrección» con `search_code`,
`inspect_project`, `patch_file`, `create_file` y `run_tests`. Las cinco tocan el
disco, así que en la nube se excluyen del catálogo. El modelo recibía en **cada
llamada** instrucciones de usar herramientas que no existían, y pagaba los
tokens de leerlas.

## La regla que se fija aquí

Una sección se quita si cita **alguna** herramienta que no está disponible. Una
que no cita ninguna no se toca nunca.

La primera versión quitaba solo las secciones donde no quedaba **ninguna**, y en
la nube no habría quitado nada: esa sección cita también `create_plan`, que sí
existe. Hay una prueba para eso.
"""

import re

import pytest

from src.agent.prompt import SYSTEM_PROMPT, prompt_para

LOCALES = {"search_code", "inspect_project", "patch_file", "create_file", "run_tests"}
CITADAS = set(re.findall(r"`([a-z_]+)`", SYSTEM_PROMPT))


def _titulos(prompt):
    return [s.strip().splitlines()[0] for s in re.split(r"\n(?=## )", prompt)]


class TestLaRegla:
    def test_con_todas_disponibles_el_prompt_no_cambia(self):
        """En local no se quita nada ni se añade nada: están todas, y también las
        del equipo, que es lo que evita la sección «en la nube» (V2.0.33)."""
        assert prompt_para(CITADAS | {"read_file", "execute_command"}) == SYSTEM_PROMPT

    def test_en_la_nube_no_cita_ninguna_herramienta_ausente(self):
        recortado = prompt_para(CITADAS - LOCALES)

        for nombre in LOCALES:
            assert nombre not in recortado, (
                f"el prompt de la nube sigue mandando usar `{nombre}`, que no existe"
            )

    def test_basta_con_que_falte_una(self):
        """El error de la primera versión.

        La sección de programación cita las cinco locales **y** `create_plan`.
        Con la regla «solo si no queda ninguna», `create_plan` la salvaba y en
        la nube no se quitaba nada.
        """
        seccion = next(
            s for s in re.split(r"\n(?=## )", SYSTEM_PROMPT)
            if "search_code" in s
        )
        assert "create_plan" in seccion, (
            "la prueba parte de que esa sección cita también una herramienta "
            "que sí existe; si ya no es así, revísala"
        )

        recortado = prompt_para(CITADAS - LOCALES)

        assert "## Ciclo de autocorrección en programación" not in recortado

    def test_en_la_nube_solo_se_quita_esa_seccion(self):
        """El recorte no puede llevarse por delante instrucciones que sí sirven."""
        antes = _titulos(SYSTEM_PROMPT)
        despues = _titulos(prompt_para(CITADAS - LOCALES))

        assert [t for t in antes if t not in despues] == [
            "## Ciclo de autocorrección en programación"
        ]

    def test_lo_que_no_cita_herramientas_no_se_toca_nunca(self):
        """Identidad, seguridad e idioma no dependen del catálogo. Con un
        catálogo vacío siguen ahí."""
        recortado = prompt_para(set())

        assert "Tu nombre es Morgan" in recortado
        assert "untrusted_file_data" in recortado
        assert "no manda" in recortado

    @pytest.mark.parametrize("fragmento", [
        "create_plan",          # planificar sigue siendo lo primero
        "list_uploads",         # los archivos existen en la nube
        "untrusted_web_data",   # la política de seguridad
        "no manda",             # el idioma de un audio no manda
    ])
    def test_en_la_nube_se_conserva_lo_importante(self, fragmento):
        assert fragmento in prompt_para(CITADAS - LOCALES)


class TestLoQueCitaElPromptSonHerramientasDeVerdad:
    def test_cada_nombre_citado_es_una_herramienta_real(self):
        """La regla trata como herramienta todo nombre entre comillas
        invertidas. Si alguien cita en el prompt algo que no es una
        herramienta —un parámetro, una variable—, no estaría nunca disponible y
        **se quitaría su sección entera sin avisar**. Esta prueba lo impide.
        """
        import os
        import tempfile

        os.environ.setdefault("MORGAN_DATA_DIR", tempfile.mkdtemp())
        os.environ.setdefault("MORGAN_LOG_DIR", tempfile.mkdtemp())
        os.environ["MORGAN_SERVE_WEB"] = "false"

        from src.api.dependencies import get_container
        from src.config import reset_settings

        reset_settings()
        reales = {t.name for t in get_container().tool_registry.list_tools()}

        fantasmas = CITADAS - reales
        assert not fantasmas, (
            f"El prompt cita {fantasmas} entre comillas invertidas y no son "
            "herramientas: su sección se quitaría siempre"
        )


class TestElAgenteUsaElPromptDeSuCatalogo:
    def test_un_agente_sin_las_locales_no_las_cita(self, tmp_path):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.filesystem import ListFilesTool
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(ListFilesTool())

        agente = Agent(
            model=MockLLMProvider(),
            tool_registry=registro,
            permission_manager=PermissionManager(interactive=False),
        )

        for nombre in LOCALES:
            assert nombre not in agente.system_prompt

    def test_con_el_catalogo_ilegible_se_manda_entero(self):
        """Ante la duda, entero: equivocarse hacia ese lado son unos tokens, y
        hacia el otro es quitarle al modelo instrucciones que le servían."""
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager

        class CatalogoRoto:
            def list_tools(self):
                raise RuntimeError("no se puede leer")

        agente = Agent(
            model=MockLLMProvider(),
            tool_registry=CatalogoRoto(),
            permission_manager=PermissionManager(interactive=False),
        )

        assert agente.system_prompt == SYSTEM_PROMPT

    def test_la_memoria_se_anade_sobre_el_prompt_recortado(self):
        """La memoria del turno se añade al prompt del agente. Si partiera del
        original, volverían las secciones quitadas."""
        from src.agent.core import Agent
        from src.memory.manager import MEMORY_SUMMARY_HEADER
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.filesystem import ListFilesTool
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(ListFilesTool())
        modelo = MockLLMProvider()
        agente = Agent(
            model=modelo,
            tool_registry=registro,
            permission_manager=PermissionManager(interactive=False),
        )
        agente.memoria = lambda: f"{MEMORY_SUMMARY_HEADER}\n- le gusta el café"

        agente.chat("hola")

        assert "le gusta el café" in modelo.prompts[-1]
        assert "search_code" not in modelo.prompts[-1]


class TestSabeQueNoTieneElEquipo:
    """Medido en el recorrido de alguien nuevo (auditoría 2.3): en la nube, a «¿qué
    puedes hacer?» contestó que no ejecuta comandos «sin tu autorización» y
    ofreció procesar CSV con pandas. Sin decírselo, no sabe dónde está."""

    def test_sin_herramientas_del_equipo_se_le_dice(self):
        from src.agent.prompt import prompt_para

        prompt = prompt_para({"search_web", "read_webpage", "remember_fact", "create_plan"})

        assert "No tienes acceso al equipo de la persona" in prompt
        assert "ni siquiera con su permiso" in prompt

    def test_con_ellas_no(self):
        from src.agent.prompt import prompt_para

        prompt = prompt_para({"read_file", "execute_command", "create_plan"})

        assert "No tienes acceso al equipo" not in prompt

    def test_el_contenedor_de_la_nube_la_incluye(self, monkeypatch):
        """Conectado de verdad: el catálogo que monta la nube no tiene las
        herramientas del equipo, y el prompt que llega al modelo lo dice."""
        from src.api import dependencies
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        try:
            registro = dependencies.get_container().tool_registry
            from src.agent.prompt import prompt_para

            # Como lo calcula el núcleo: solo lo disponible en este turno. Las del PC
            # (3.0-E) están registradas, pero sin agente conectado no lo están.
            disponibles = {h.name for h in registro.list_tools() if h.disponible()}
            assert "No tienes acceso al equipo" in prompt_para(disponibles)
        finally:
            reset_settings()
            dependencies.reset_container()
