"""
Sistema de eventos desacoplado para el Agent Loop de Morgan.
Permite que la CLI, Web UI o logs observen lo que sucede sin acoplar el núcleo.
"""

from typing import Any


class AgentEventHandler:
    """Manejador de eventos del agente."""

    def on_thinking_start(self, iteration: int) -> None:
        pass

    def on_tool_call_start(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        pass

    def on_tool_call_result(self, tool_name: str, success: bool, error: str | None) -> None:
        pass

    def on_turn_complete(self, total_iterations: int) -> None:
        pass
