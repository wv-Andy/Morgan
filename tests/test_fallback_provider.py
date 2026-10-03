"""
Pruebas unitarias para FallbackProvider y GroqProvider.
"""

import pytest
from src.models.base import LLMResponse, ToolCallRequest, ChatMessage, LLMProvider
from src.models.mock import MockLLMProvider
from src.models.fallback import FallbackProvider


class FailingMockProvider(LLMProvider):
    @property
    def model_name(self) -> str:
        return "failing-mock"

    def generate(self, messages, tools=None, system_prompt=None):
        raise RuntimeError("Fallo simulado de proveedor")


class TestFallbackProvider:
    def test_primary_succeeds(self):
        primary = MockLLMProvider()
        primary.queue_text("Respuesta del primario")
        fallback = MockLLMProvider()
        fallback.queue_text("Respuesta del respaldo")

        fb = FallbackProvider(primary=primary, fallback=fallback)
        resp = fb.generate(messages=[ChatMessage(role="user", content="hola")])

        assert resp.content == "Respuesta del primario"
        assert primary.call_count == 1
        assert fallback.call_count == 0

    def test_primary_fails_switches_to_fallback(self):
        primary = FailingMockProvider()
        fallback = MockLLMProvider()
        fallback.queue_text("Respuesta exitosa del respaldo")

        fb = FallbackProvider(primary=primary, fallback=fallback)
        resp = fb.generate(messages=[ChatMessage(role="user", content="hola")])

        assert resp.content == "Respuesta exitosa del respaldo"
        assert fallback.call_count == 1

    def test_model_name_includes_both(self):
        primary = MockLLMProvider()
        fallback = MockLLMProvider()
        fb = FallbackProvider(primary=primary, fallback=fallback)
        assert "mock-model" in fb.model_name
        assert "Respaldo" in fb.model_name
