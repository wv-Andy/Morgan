"""
Pruebas unitarias para el Agent Loop multi-turno, validaciones y LLMProvider (V0.4).
"""

import pytest
from src.agent.core import Agent
from src.models.base import LLMResponse, ToolCallRequest
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tools.registry import ToolRegistry
from src.tools.system import SystemInfoTool
from src.tools.filesystem import ListFilesTool, CreateFileTool


class TestAgentLoopV04:
    def test_mock_provider_text_response(self):
        mock_llm = MockLLMProvider()
        mock_llm.queue_text("Hola, soy Morgan.")

        registry = ToolRegistry()
        pm = PermissionManager()
        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm)

        response = agent.process("Hola")
        assert response == "Hola, soy Morgan."
        assert len(agent.messages) == 2
        assert agent.messages[0].role == "user"
        assert agent.messages[1].role == "model"

    def test_single_tool_call_flow(self):
        mock_llm = MockLLMProvider()
        # 1. Solicita system_info
        mock_llm.queue_tool_call("system_info", {})
        # 2. Responde con texto final interpretando el resultado
        mock_llm.queue_text("Tu sistema está funcionando con CPU al 15%.")

        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        pm = PermissionManager()
        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm)

        response = agent.process("¿Cómo está mi equipo?")
        assert "CPU al 15%" in response
        # Mensajes esperados: user -> model (call) -> tool (result) -> model (final)
        assert len(agent.messages) == 4
        assert agent.messages[1].role == "model"
        assert agent.messages[2].role == "tool"
        assert agent.messages[2].tool_result["success"] is True

    def test_multi_step_tool_call_flow(self, tmp_path):
        mock_llm = MockLLMProvider()
        # Paso 1: Crea un archivo
        target_file = str(tmp_path / "saludo.txt")
        mock_llm.queue_tool_call("create_file", {"path": target_file, "content": "Hola mundo"})
        # Paso 2: Lista los archivos de la carpeta
        mock_llm.queue_tool_call("list_files", {"path": str(tmp_path)})
        # Paso 3: Respuesta final
        mock_llm.queue_text("He creado el archivo y confirmado su existencia en la carpeta.")

        registry = ToolRegistry()
        registry.register(CreateFileTool())
        registry.register(ListFilesTool())
        pm = PermissionManager()
        pm.moderate_mode = "auto"  # Para permitir create_file sin prompt

        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm)
        response = agent.process("Crea el archivo y verifica que esté ahí")

        assert "He creado el archivo" in response
        assert (tmp_path / "saludo.txt").exists()
        # Verificar que se hayan ejecutado ambas herramientas en secuencia
        tool_msgs = [m for m in agent.messages if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0].tool_name == "create_file"
        assert tool_msgs[1].tool_name == "list_files"

    def test_max_iterations_limit(self):
        mock_llm = MockLLMProvider()
        # Encolar llamadas continuas a system_info más de las permitidas
        for _ in range(10):
            mock_llm.queue_tool_call("system_info", {})

        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        pm = PermissionManager()
        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm, max_iterations=4)

        response = agent.process("Bucle infinito")
        assert "límite máximo" in response.lower()

    def test_tool_loop_detection(self):
        mock_llm = MockLLMProvider()
        # Encolar exactamente la misma llamada 4 veces
        for _ in range(4):
            mock_llm.queue_tool_call("system_info", {"arg": 1})

        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        pm = PermissionManager()
        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm)

        agent.process("Repite esto")
        # Debe haber detectado el bucle en los mensajes
        tool_results = [m.tool_result for m in agent.messages if m.role == "tool"]
        assert any("bucle repetitivo detectado" in (res.get("error") or "") for res in tool_results)

    def test_missing_required_arguments(self):
        mock_llm = MockLLMProvider()
        # create_file requiere 'path' y 'content'
        mock_llm.queue_tool_call("create_file", {"path": "test.txt"})  # falta content
        mock_llm.queue_text("Lo siento, faltó el contenido.")

        registry = ToolRegistry()
        registry.register(CreateFileTool())
        pm = PermissionManager()
        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm)

        agent.process("Crea un archivo")
        tool_msg = [m for m in agent.messages if m.role == "tool"][0]
        assert tool_msg.tool_result["success"] is False
        assert "parámetro obligatorio" in tool_msg.tool_result["error"].lower()

    def test_tool_permission_denied(self):
        mock_llm = MockLLMProvider()
        mock_llm.queue_tool_call("system_info", {})
        mock_llm.queue_text("No pude obtener la información porque denegaste el permiso.")

        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        pm = PermissionManager()
        pm.block_tool("system_info")  # Bloquear explícitamente

        agent = Agent(model=mock_llm, tool_registry=registry, permission_manager=pm)
        response = agent.process("Revisa mi sistema")

        tool_msg = [m for m in agent.messages if m.role == "tool"][0]
        assert tool_msg.tool_result["success"] is False
        assert "denegó" in tool_msg.tool_result["error"].lower()


class TestNoRepetirLoQueYaFallo:
    """Medido conmigo el 2026-09-19: `copy_file` falló por un error interno y el modelo
    la repitió **cuatro veces** con los mismos argumentos hasta agotar las vueltas y
    acabar en «he alcanzado el límite máximo de pasos». Repetir lo que acaba de fallar da
    el mismo error; lo útil es decírselo para que pruebe otra cosa."""

    def _agente(self, herramienta, mock_llm):
        registry = ToolRegistry()
        registry.register(herramienta)
        return Agent(model=mock_llm, tool_registry=registry, permission_manager=PermissionManager())

    def _rota(self, veces_llamada):
        from src.tools.base import RiskLevel, Tool, ToolCategory

        class Rota(Tool):
            name = "copia_rota"
            description = "Una herramienta que falla siempre, como la mía."
            parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
            permission_level = RiskLevel.SAFE.value
            category = ToolCategory.FILESYSTEM.value
            requires_local = False

            def execute(self, **kwargs):
                veces_llamada.append(kwargs)
                raise ImportError("cannot import name 'RiskLevel' from 'src.security.validator'")

        return Rota()

    def test_la_segunda_vez_ni_se_ejecuta_y_se_le_dice_que_pruebe_otra_cosa(self):
        llamadas: list = []
        mock_llm = MockLLMProvider()
        mock_llm.queue_tool_call("copia_rota", {"path": "C:/x.pdf"})
        mock_llm.queue_tool_call("copia_rota", {"path": "C:/x.pdf"})     # la repite igual
        mock_llm.queue_text("No he podido: fallo interno de la herramienta.")

        agente = self._agente(self._rota(llamadas), mock_llm)
        respuesta = agente.process("pásame el pdf")

        assert len(llamadas) == 1, "se ejecutó otra vez lo que ya había fallado"
        assert "No he podido" in respuesta
        segundo = [m for m in agente.messages if m.role == "tool"][1]
        assert "No lo repitas" in segundo.tool_result["error"]
        assert "cannot import name" in segundo.tool_result["error"]   # por qué falló

    def test_con_otros_argumentos_si_se_intenta(self):
        """No se bloquea la herramienta, solo **esa** llamada: otra ruta puede funcionar."""
        llamadas: list = []
        mock_llm = MockLLMProvider()
        mock_llm.queue_tool_call("copia_rota", {"path": "C:/x.pdf"})
        mock_llm.queue_tool_call("copia_rota", {"path": "C:/otro.pdf"})
        mock_llm.queue_text("Nada que hacer.")

        agente = self._agente(self._rota(llamadas), mock_llm)
        agente.process("pásame el pdf")

        assert [l["path"] for l in llamadas] == ["C:/x.pdf", "C:/otro.pdf"]

    def test_lo_que_sale_bien_se_puede_repetir(self):
        from src.tools.base import RiskLevel, Tool, ToolCategory

        llamadas: list = []

        class Buena(Tool):
            name = "mira"
            description = "Una que funciona siempre."
            parameters = {"type": "object", "properties": {}}
            permission_level = RiskLevel.SAFE.value
            category = ToolCategory.SYSTEM.value
            requires_local = False

            def execute(self, **kwargs):
                llamadas.append(1)
                return {"success": True, "data": {"ok": 1}, "error": None}

        mock_llm = MockLLMProvider()
        mock_llm.queue_tool_call("mira", {})
        mock_llm.queue_tool_call("mira", {})
        mock_llm.queue_text("listo")

        self._agente(Buena(), mock_llm).process("mira dos veces")
        assert len(llamadas) == 2


class TestSinMasPasos:
    """4.20: cuando se acaban las vueltas, una última llamada SIN herramientas para contestar
    con lo encontrado. Antes se tiraba todo y salía «He alcanzado el límite…»: medido en
    producción el 30-09, en 3 de 30 preguntas, casi todas búsquedas de varias partes."""

    def _agente(self, llm, vueltas=3):
        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        return Agent(model=llm, tool_registry=registry, permission_manager=PermissionManager(),
                     max_iterations=vueltas)

    def _sin_parar(self, llm, n=3):
        for i in range(n):
            llm.queue_tool_call("system_info", {"vuelta": i})     # distintas: no es un bucle

    def test_contesta_con_lo_que_encontro(self):
        llm = MockLLMProvider()
        self._sin_parar(llm)
        llm.queue_text("Con lo que encontré: tu equipo tiene 16 GB. No llegué a mirar el disco.")
        respuesta = self._agente(llm).process("Dime todo de mi equipo")

        assert respuesta.startswith("Con lo que encontré")
        assert llm.call_count == 4, "tres vueltas y una última"
        assert llm.herramientas[-1] == [], "la última, sin herramientas"
        assert "límite" not in respuesta.lower()

    def test_la_nota_no_se_guarda_en_el_historial(self):
        from src.agent.core import SIN_MAS_PASOS

        llm = MockLLMProvider()
        self._sin_parar(llm)
        llm.queue_text("Esto es lo que hay.")
        agente = self._agente(llm)
        agente.process("Dime todo de mi equipo")

        assert SIN_MAS_PASOS in [m.content for m in llm._calls[-1]], "el modelo sí la ve"
        assert all(m.content != SIN_MAS_PASOS for m in agente.messages), "el historial no"

    def test_si_la_ultima_tampoco_contesta_el_aviso_de_siempre(self):
        llm = MockLLMProvider()
        self._sin_parar(llm, 4)              # la última llamada también pide una herramienta
        assert "límite máximo" in self._agente(llm).process("Bucle").lower()

    def test_si_la_ultima_falla_el_aviso_de_siempre(self):
        llm = MockLLMProvider()
        self._sin_parar(llm)
        agente = self._agente(llm)
        original = llm.generate

        def generate(messages, tools=None, system_prompt=None):
            if tools is None and llm.call_count >= 3:
                raise RuntimeError("Request timed out.")
            return original(messages, tools=tools, system_prompt=system_prompt)

        llm.generate = generate
        assert "límite máximo" in agente.process("Bucle").lower()

    def test_sin_tiempo_no_se_intenta(self, monkeypatch):
        import src.agent.core as core

        monkeypatch.setattr(core, "MARGEN_ULTIMA_RESPUESTA", 10**9)
        llm = MockLLMProvider()
        self._sin_parar(llm)
        llm.queue_text("no debería salir")
        assert "límite máximo" in self._agente(llm).process("Bucle").lower()
        assert llm.call_count == 3
