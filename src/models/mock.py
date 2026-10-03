"""
Proveedor LLM simulado para pruebas unitarias deterministas y sin red.
"""

from src.models.base import LLMProvider, ChatMessage, LLMResponse, ToolCallRequest


class MockLLMProvider(LLMProvider):
    """Permite programar respuestas predeterminadas para verificar el ciclo del agente."""

    def __init__(self, canned_responses: list[LLMResponse] | None = None):
        self._responses = list(canned_responses) if canned_responses else []
        self._calls: list[list[ChatMessage]] = []
        #: El prompt de sistema de cada llamada, para comprobar qué ve el modelo.
        self.prompts: list[str | None] = []
        #: Los nombres de las herramientas ofrecidas en cada llamada (4.14).
        self.herramientas: list[list[str]] = []
        self._model_name = "mock-model"

    @property
    def model_name(self) -> str:
        return self._model_name

    def queue_response(self, response: LLMResponse) -> None:
        """Agrega una respuesta programada a la cola."""
        self._responses.append(response)

    def queue_text(self, text: str) -> None:
        """Atajo para programar una respuesta de texto."""
        self._responses.append(LLMResponse(type="text", content=text))

    def queue_tool_call(self, name: str, args: dict | None = None) -> None:
        """Atajo para programar una llamada a herramienta."""
        self._responses.append(
            LLMResponse(
                type="tool_call",
                tool_calls=[ToolCallRequest(name=name, arguments=args or {})],
            )
        )

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        self._calls.append(list(messages))
        self.prompts.append(system_prompt)
        self.herramientas.append([t.get("name") for t in tools or []])
        if not self._responses:
            return LLMResponse(type="text", content="[Mock Default Response]")
        return self._responses.pop(0)

    @property
    def call_count(self) -> int:
        return len(self._calls)
