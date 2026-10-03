"""
Capa de compatibilidad hacia atrás para ModelProvider.
Reenvía llamadas a GeminiProvider manteniendo la interfaz previa si es necesario.
"""

from src.models.gemini import GeminiProvider
from src.models.base import LLMProvider, ChatMessage, LLMResponse, ToolCallRequest

# ModelProvider es un alias directo a GeminiProvider para compatibilidad
class ModelProvider(GeminiProvider):
    """Alias retrocompatible para GeminiProvider."""
    pass

__all__ = ["ModelProvider", "GeminiProvider", "LLMProvider", "ChatMessage", "LLMResponse", "ToolCallRequest"]
