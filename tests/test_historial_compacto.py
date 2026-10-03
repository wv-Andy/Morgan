"""
Los turnos anteriores, solo con su texto (4.1.5).

Medido con el modelo real: en memoria la conversación guardaba hasta 200 mensajes **con**
los resultados de las herramientas (un archivo leído entero) y se reenviaban en cada
llamada de los turnos siguientes; tras un reinicio solo se recuperaban 20 de texto. Y
cuando contestaba Gemini, las llamadas hechas por Groq le llegaban como texto («He usado
estas herramientas: …») y las imitaba como respuesta.
"""

from src.agent.core import Agent
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tools.base import Tool
from src.tools.registry import ToolRegistry


class Leer(Tool):
    @property
    def name(self):
        return "leer"

    @property
    def description(self):
        return "leer"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def permission_level(self):
        return "safe"

    @property
    def requires_local(self):
        return False

    def execute(self, **kw):
        return {"success": True, "data": {"contenido": "x" * 5000}, "error": None}


def _agente():
    registro = ToolRegistry()
    registro.register(Leer())
    modelo = MockLLMProvider()
    return Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False)), modelo


class TestLoDeTurnosAnterioresEsTexto:
    def test_el_resultado_de_una_herramienta_no_viaja_al_turno_siguiente(self):
        agente, modelo = _agente()
        modelo.queue_tool_call("leer", {})
        modelo.queue_text("El archivo habla de ventas.")
        agente.chat("léelo", session_id="s")
        modelo.queue_text("Tiene 300 líneas.")
        agente.chat("¿cuántas líneas?", session_id="s")

        enviados = modelo._calls[-1]
        assert [m.role for m in enviados] == ["user", "model", "user"]
        assert all(not m.tool_calls and m.role != "tool" for m in enviados)
        assert "x" * 100 not in "".join(m.content or "" for m in enviados)
        assert enviados[1].content == "El archivo habla de ventas."

    def test_dentro_del_turno_si_esta(self):
        """Lo que el modelo necesita para terminar su turno no se toca."""
        agente, modelo = _agente()
        modelo.queue_tool_call("leer", {})
        modelo.queue_text("Hecho.")
        agente.chat("léelo", session_id="s")
        segunda = modelo._calls[1]
        assert any(m.role == "tool" for m in segunda)

    def test_como_mucho_la_ventana(self):
        agente, modelo = _agente()
        for i in range(15):
            modelo.queue_text(f"respuesta {i}")
            agente.chat(f"pregunta {i}", session_id="s")
        modelo.queue_text("fin")
        agente.chat("última", session_id="s")
        assert len(modelo._calls[-1]) == 20 + 1, "20 de antes y la de ahora"

    def test_se_guarda_en_la_base_lo_de_este_turno(self, tmp_path):
        """La compactación va antes de marcar dónde empieza el turno: lo que se persiste
        es lo nuevo, ni más ni menos."""
        from src.agent.history import ConversationHistory
        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory

        repos = SQLiteRepositoryFactory(Database(tmp_path / "h.db"))
        registro = ToolRegistry()
        registro.register(Leer())
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False),
                       conversation_history=ConversationHistory(repos))
        modelo.queue_tool_call("leer", {})
        modelo.queue_text("uno")
        agente.chat("a", session_id="s")
        modelo.queue_text("dos")
        agente.chat("b", session_id="s")
        textos = [m.content for m in repos.messages.list_for_session("s", limit=50, roles=("user", "assistant"))]
        assert textos == ["a", "uno", "b", "dos"]

    def test_una_llamada_con_texto_tampoco_viaja(self):
        """Algunos modelos escriben algo junto a la llamada («Voy a leerlo»): ese mensaje es
        parte del trabajo del turno, no de la conversación."""
        from src.models.base import LLMResponse, ToolCallRequest

        agente, modelo = _agente()
        modelo.queue_response(LLMResponse(type="tool_call", content="Voy a leerlo.",
                                          tool_calls=[ToolCallRequest(name="leer", arguments={})]))
        modelo.queue_text("Habla de ventas.")
        agente.chat("léelo", session_id="s")
        modelo.queue_text("Sí.")
        agente.chat("¿seguro?", session_id="s")
        assert "Voy a leerlo." not in [m.content for m in modelo._calls[-1]]
