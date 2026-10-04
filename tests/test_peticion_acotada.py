"""
Lo que se manda al modelo en un turno con búsquedas (4.20).

Medido con el modelo real (`scripts/prueba_comprimida.py`): las preguntas largas con varias
búsquedas o una página leída pedían 8.299 y 10.941 tokens; Groq admite 8.000 por minuto, las
rechazaba (413) y el turno caía en el respaldo. Y el respaldo, Gemini, recibía las llamadas de
Groq como **suyas** («He usado estas herramientas: …») y las imitaba: a «tres noticias sobre IA»
contestó con la lista de llamadas.

Lo que haría daño si fallase: una petición que vuelve a crecer sin tope, el historial
recortado (el resultado entero tiene que seguir guardado), o Gemini viendo otra vez las
llamadas como un mensaje suyo.
"""

import json

from src.agent.core import RESULTADO_ANTERIOR, RESULTADO_RECIENTE, recortar_resultados
from src.models.base import ChatMessage, ToolCallRequest


def _turno(paginas: int, largo: int = 12_000) -> list[ChatMessage]:
    mensajes = [ChatMessage(role="user", content="Investiga esto a fondo")]
    for n in range(paginas):
        mensajes.append(ChatMessage(role="model", tool_calls=[
            ToolCallRequest(name="read_webpage", arguments={"url": f"https://ejemplo.co/{n}"}, id=f"c{n}")]))
        mensajes.append(ChatMessage(role="tool", tool_name="read_webpage", tool_call_id=f"c{n}",
                                    tool_result={"success": True, "content": "x" * largo}))
    return mensajes


def _tamano(m: ChatMessage) -> int:
    return len(json.dumps(m.tool_result, ensure_ascii=False))


class TestElRecorte:
    def test_el_ultimo_resultado_cabe_entero_hasta_su_tope(self):
        enviados = recortar_resultados(_turno(1))
        assert RESULTADO_RECIENTE <= _tamano(enviados[-1]) < RESULTADO_RECIENTE + 200

    def test_los_anteriores_del_turno_mas_cortos(self):
        enviados = recortar_resultados(_turno(3))
        resultados = [m for m in enviados if m.role == "tool"]
        assert all(_tamano(m) < RESULTADO_ANTERIOR + 200 for m in resultados[:-1])
        assert _tamano(resultados[-1]) >= RESULTADO_RECIENTE

    def test_tres_paginas_ya_no_pasan_del_tope_de_groq(self):
        """Lo medido: unos 3,5 caracteres por token y una base fija de ~4.900 tokens."""
        enviados = recortar_resultados(_turno(3))
        caracteres = sum(_tamano(m) for m in enviados if m.role == "tool")
        assert 4900 + caracteres / 3.5 < 8000, caracteres

    def test_lo_corto_no_se_toca(self):
        mensajes = _turno(2, largo=300)
        assert recortar_resultados(mensajes) == mensajes

    def test_el_historial_no_cambia(self):
        mensajes = _turno(2)
        recortar_resultados(mensajes)
        assert all(len(m.tool_result["content"]) == 12_000 for m in mensajes if m.role == "tool")

    def test_dice_que_esta_recortado(self):
        enviado = recortar_resultados(_turno(1))[-1]
        assert "acortó" in enviado.tool_result["recortado"]


class TestGeminiNoVeLasLlamadasComoSuyas:
    def _contenidos(self, mensajes):
        from src.models.gemini import GeminiProvider

        proveedor = GeminiProvider.__new__(GeminiProvider)       # sin cliente: solo traduce
        return proveedor._build_contents(mensajes)

    def test_las_llamadas_de_groq_no_son_mensajes_del_modelo(self):
        mensajes = [
            ChatMessage(role="user", content="Tres noticias sobre IA"),
            ChatMessage(role="model", tool_calls=[ToolCallRequest(name="search_web", arguments={"query": "avances 2026"}, id="a")]),
            ChatMessage(role="tool", tool_name="search_web", tool_call_id="a", tool_result={"resultados": ["uno"]}),
        ]
        contenidos = self._contenidos(mensajes)
        textos_del_modelo = [p.text for c in contenidos if c.role == "model" for p in c.parts if p.text]
        assert not any("He usado" in t or "search_web" in t for t in textos_del_modelo)
        nota = " ".join(p.text for c in contenidos if c.role == "user" for p in c.parts if p.text)
        assert "avances 2026" in nota, "qué se buscó, no solo con qué"
        assert "uno" in nota and "Nota de Morgan" in nota

    def test_los_turnos_siguen_alternando(self):
        mensajes = [ChatMessage(role="user", content="Busca dos cosas")]
        for n in range(2):
            mensajes.append(ChatMessage(role="model", tool_calls=[ToolCallRequest(name="search_web", arguments={"q": n}, id=str(n))]))
            mensajes.append(ChatMessage(role="tool", tool_name="search_web", tool_call_id=str(n), tool_result={"n": n}))
        roles = [c.role for c in self._contenidos(mensajes)]
        assert all(a != b for a, b in zip(roles, roles[1:])), roles

    def test_lo_que_dijo_el_modelo_antes_de_llamar_se_conserva(self):
        mensajes = [
            ChatMessage(role="user", content="Hola"),
            ChatMessage(role="model", content="Voy a buscarlo.", tool_calls=[ToolCallRequest(name="search_web", id="a")]),
            ChatMessage(role="tool", tool_name="search_web", tool_call_id="a", tool_result={}),
        ]
        contenidos = self._contenidos(mensajes)
        assert any(c.role == "model" and c.parts[0].text == "Voy a buscarlo." for c in contenidos)


class TestElBucleMandaLoRecortado:
    def test_el_modelo_recibe_el_resultado_acotado_y_el_historial_lo_guarda_entero(self, monkeypatch):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry
        from src.tools.system import SystemInfoTool

        monkeypatch.setattr(SystemInfoTool, "execute", lambda self, **kw: {"success": True, "data": "x" * 30_000})
        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        llm = MockLLMProvider()
        llm.queue_tool_call("system_info", {})
        llm.queue_text("Listo.")
        agente = Agent(model=llm, tool_registry=registry, permission_manager=PermissionManager())
        agente.process("¿Cómo está mi equipo?")

        enviado = [m for m in llm._calls[-1] if m.role == "tool"][0]
        assert len(json.dumps(enviado.tool_result)) < RESULTADO_RECIENTE + 200, "al modelo, acotado"
        guardado = [m for m in agente.messages if m.role == "tool"][0]
        assert len(guardado.tool_result["data"]) == 30_000, "en el historial, entero"
