"""
Lo que se guarda de un mensaje es lo que escribió la persona (4.3).

Al modelo se le añade texto al mensaje: la lista de adjuntos con sus identificadores, y en
el turno que ejecuta un plan aprobado, el informe («Morgan ya ejecutó sus pasos… No los
repitas…»). Se guardaba así, y al recargar la conversación la persona lo veía en su propio
mensaje, y en el título de la conversación si era el primero. Reproducido antes de
arreglarlo.
"""

from src.agent.core import Agent
from src.agent.history import ConversationHistory
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tasks.planificador import Planificador
from src.tools.base import Tool
from src.tools.planificacion import plan_tools
from src.tools.registry import ToolRegistry


class Anotar(Tool):
    name = "anotar"
    description = "anotar"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    permission_level = "moderate"
    requires_local = False

    def execute(self, **kw):
        return {"success": True, "data": {}, "error": None}


def _montar(tmp_path, monkeypatch):
    repos = SQLiteRepositoryFactory(Database(tmp_path / "h.db"))
    registro = ToolRegistry()
    registro.register(Anotar())
    planificador = Planificador(repos.planes, registro)
    for h in plan_tools(planificador):
        registro.register(h)
    permisos = PermissionManager(interactive=False)
    monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
    modelo = MockLLMProvider()
    agente = Agent(model=modelo, tool_registry=registro, permission_manager=permisos,
                   planes=planificador, conversation_history=ConversationHistory(repos))
    return agente, modelo, planificador, repos


def _guardado(repos):
    return [(m.role, m.content) for m in repos.messages.list_for_session("s", limit=20, roles=("user", "assistant"))]


def test_el_informe_de_un_plan_aprobado_va_al_modelo_y_no_a_la_base(tmp_path, monkeypatch):
    agente, modelo, planificador, repos = _montar(tmp_path, monkeypatch)
    plan = planificador.crear("anotar", [{"descripcion": "anotar", "herramienta": "anotar",
                                          "argumentos": {"x": "1"}}, {"descripcion": "contárselo"}], session_id="s")
    planificador.aprobar(plan.id)
    modelo.queue_text("Hecho.")
    agente.chat("✅ Plan aprobado: anotar", session_id="s", ejecutar_plan=plan.id)
    assert "Morgan ya ejecutó sus pasos" in modelo._calls[0][-1].content
    assert _guardado(repos) == [("user", "✅ Plan aprobado: anotar"), ("assistant", "Hecho.")]


def test_la_lista_de_adjuntos_va_al_modelo_y_no_a_la_base_ni_al_titulo(tmp_path, monkeypatch):
    agente, modelo, _, repos = _montar(tmp_path, monkeypatch)
    modelo.queue_text("Es una lista.")
    agente.chat("¿qué es esto?", session_id="s",
                attachments=[{"id": "abc123", "nombre": "lista.png", "mime": "image/png", "familia": "imagen"}])
    enviado = modelo._calls[0][-1].content
    assert enviado.startswith("Archivos adjuntos a este mensaje:") and "(id: abc123)" in enviado
    assert _guardado(repos)[0] == ("user", "¿qué es esto?")
    titulo = repos.sessions.get("s").title
    assert "Archivos adjuntos" not in (titulo or "") and "abc123" not in (titulo or "")


def test_sin_nada_que_añadir_se_guarda_igual(tmp_path, monkeypatch):
    agente, modelo, _, repos = _montar(tmp_path, monkeypatch)
    modelo.queue_text("Hola.")
    agente.chat("hola", session_id="s")
    assert _guardado(repos) == [("user", "hola"), ("assistant", "Hola.")]



class TestUnPlanEscritoNoEsUnPlan:
    """Medido con el modelo real (4.3): a partir del tercer turno de una conversación, el
    modelo imitaba la plantilla de la 4.2 («Te propongo este plan: …» con sus pasos) sin
    llamar a `create_plan`, y no había nada que aprobar."""

    IMITACION = "Te propongo este plan: **Añadir una línea**.\n\n1. append_file – añadir «hola»."

    def test_se_le_recuerda_y_si_lo_crea_la_persona_ve_el_plan_de_verdad(self, tmp_path, monkeypatch):
        from src.agent.core import RECUERDA_CREATE_PLAN

        agente, modelo, planificador, repos = _montar(tmp_path, monkeypatch)
        modelo.queue_text(self.IMITACION)
        modelo.queue_tool_call("create_plan", {"objetivo": "añadir una línea", "pasos": [
            {"descripcion": "anotar", "herramienta": "anotar", "argumentos": {"x": "1"}}]})
        r = agente.chat("añade una línea", session_id="s")
        assert r.startswith("Te propongo este plan: «añadir una línea»")
        assert RECUERDA_CREATE_PLAN in modelo._calls[1][-1].content
        assert len(planificador.listar(session_id="s")) == 1
        assert _guardado(repos) == [("user", "añade una línea"), ("assistant", r)]

    def test_si_insiste_se_dice_que_no_hay_plan(self, tmp_path, monkeypatch):
        from src.agent.core import NOTA_SIN_PLAN

        agente, modelo, _, _ = _montar(tmp_path, monkeypatch)
        modelo.queue_text(self.IMITACION)
        modelo.queue_text(self.IMITACION)
        r = agente.chat("añade una línea", session_id="s")
        assert r.endswith(NOTA_SIN_PLAN) and modelo.call_count == 2

    def test_hablar_de_planes_no_es_proponer_uno(self, tmp_path, monkeypatch):
        agente, modelo, _, _ = _montar(tmp_path, monkeypatch)
        modelo.queue_text("Tu plan de vuelo sale a las 9; el plan de pensiones, mañana.")
        assert agente.chat("¿qué tengo?", session_id="s").startswith("Tu plan de vuelo") and modelo.call_count == 1

    def test_la_nota_no_pasa_al_turno_siguiente(self, tmp_path, monkeypatch):
        from src.agent.core import RECUERDA_CREATE_PLAN

        agente, modelo, _, _ = _montar(tmp_path, monkeypatch)
        modelo.queue_text(self.IMITACION)
        modelo.queue_tool_call("create_plan", {"objetivo": "x", "pasos": [
            {"descripcion": "anotar", "herramienta": "anotar", "argumentos": {"x": "1"}}]})
        agente.chat("añade una línea", session_id="s")
        modelo.queue_text("De nada.")
        agente.chat("gracias", session_id="s")
        assert all(RECUERDA_CREATE_PLAN not in (m.content or "") for m in modelo._calls[-1])
