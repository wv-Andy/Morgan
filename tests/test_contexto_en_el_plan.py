"""
El contexto en el plan (4.0-D).

Medido con el modelo real antes de tocar nada (nube en local, agente instalado, Groq):
con una lista adjunta y «pásala a compras.txt», el plan fue `read_upload` y después
`create_file` con el contenido `<CONTENIDO>`. Aprobado, se escribió `<CONTENIDO>` en el
disco y el modelo dijo que había guardado la lista. Desde la 4.0-A un plan aprobado se
ejecuta tal cual, y lo que lee un paso no llega a los siguientes; el prompt, en cambio,
aún pedía poner las consultas «como primer paso del plan».
"""

import pytest

from src.agent.core import UN_INTENTO, Agent
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.models.mock import MockLLMProvider
from src.security.permissions import PermissionManager
from src.tasks.planificador import Planificador
from src.tools.base import Tool
from src.tools.planificacion import plan_tools
from src.tools.registry import ToolRegistry


class Falsa(Tool):
    def __init__(self, nombre, riesgo="safe", ok=True):
        self._nombre, self._riesgo, self.ok = nombre, riesgo, ok

    @property
    def name(self):
        return self._nombre

    @property
    def description(self):
        return self._nombre

    @property
    def parameters(self):
        return {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}

    @property
    def permission_level(self):
        return self._riesgo

    @property
    def requires_local(self):
        return False

    def execute(self, **kw):
        return {"success": self.ok, "data": {}, "error": None if self.ok else "no salió"}


@pytest.fixture
def planes(tmp_path):
    registro = ToolRegistry()
    for h in (Falsa("read_upload"), Falsa("read_file"), Falsa("create_file", "moderate"),
              Falsa("remember_fact")):
        registro.register(h)
    planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
    herramientas = {h.name: h for h in plan_tools(planificador)}
    return herramientas["create_plan"], planificador


def _paso(herramienta, **args):
    return {"descripcion": herramienta, "herramienta": herramienta, "argumentos": args}


class TestUnaConsultaAntesDeUnCambio:
    def test_se_rechaza_y_no_se_guarda(self, planes):
        crear, planificador = planes
        r = crear.execute(objetivo="compras", pasos=[_paso("read_upload", upload_id="u1"),
                                                     _paso("create_file", path="c.txt", content="<CONTENIDO>")],
                          session_id="s")
        assert r["success"] is False
        assert "read_upload" in r["error"] and "NO llega" in r["error"]
        assert planificador.listar(session_id="s") == []

    def test_con_un_paso_sin_herramienta_en_medio_tambien(self, planes):
        crear, _ = planes
        r = crear.execute(objetivo="x", pasos=[_paso("read_file", path="a"), {"descripcion": "pensar"},
                                               _paso("create_file", path="b")], session_id="s")
        assert r["success"] is False


class TestLoQueSigueValiendo:
    def test_solo_cambios(self, planes):
        crear, _ = planes
        assert crear.execute(objetivo="x", pasos=[_paso("create_file", path="c.txt", content="tomates")],
                             session_id="s")["success"]

    def test_solo_consultas(self, planes):
        crear, _ = planes
        assert crear.execute(objetivo="x", pasos=[_paso("read_file", path="a")], session_id="s")["success"]

    def test_una_consulta_despues_de_los_cambios(self, planes):
        """No deja ningún cambio esperando un dato."""
        crear, _ = planes
        assert crear.execute(objetivo="x", pasos=[_paso("create_file", path="c"), _paso("read_file", path="c")],
                             session_id="s")["success"]

    def test_lo_seguro_que_no_es_consulta_no_cuenta(self, planes):
        """`remember_fact` es seguro pero no trae datos: no bloquea."""
        crear, _ = planes
        assert crear.execute(objetivo="x", pasos=[_paso("remember_fact"), _paso("create_file", path="c")],
                             session_id="s")["success"]


class TestEnElPc:
    def test_una_consulta_antes_de_escribir_en_el_pc(self, tmp_path):
        from src.canal.herramientas import EscribirEnElEquipo

        registro = ToolRegistry()
        registro.register(Falsa("read_upload"))
        registro.register(EscribirEnElEquipo("create_file"))
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        crear = {h.name: h for h in plan_tools(planificador)}["create_plan"]
        r = crear.execute(objetivo="x", pasos=[_paso("read_upload"), _paso("create_file", path="C:/c.txt")],
                          session_id="s")
        assert r["success"] is False


    def test_lo_que_exige_plan_cuenta_como_cambio_aunque_sea_seguro(self, tmp_path):
        class ExigePlan(Falsa):
            @property
            def exige_plan(self):
                return True

        registro = ToolRegistry()
        registro.register(Falsa("read_file"))
        registro.register(ExigePlan("run_change_command", "safe"))
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        crear = {h.name: h for h in plan_tools(planificador)}["create_plan"]
        r = crear.execute(objetivo="x", pasos=[_paso("read_file"), _paso("run_change_command")], session_id="s")
        assert r["success"] is False


class TestElPrompt:
    def test_ya_no_pide_consultar_dentro_del_plan(self):
        from src.agent.prompt import SYSTEM_PROMPT

        assert "primer paso del plan" not in SYSTEM_PROMPT
        assert "se ejecuta tal cual" in SYSTEM_PROMPT and "léelo antes, sin plan" in SYSTEM_PROMPT


class TestLoSensibleCambia:
    """`delete_file`, `execute_command` y `kill_process` locales declaran `sensitive`, el
    nombre antiguo de `critical`: la 4.0-C no los contaba como cambios."""

    def test_un_borrado_que_falla_da_su_intento(self, monkeypatch):
        from src.agent.core import SIN_MAS_INTENTOS

        registro = ToolRegistry()
        for h in (Falsa("borrar", "sensitive", ok=False), Falsa("otra", "sensitive", ok=False),
                  Falsa("tercera", "sensitive")):
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=permisos)
        for n in ("borrar", "otra", "tercera"):
            modelo.queue_tool_call(n, {})
        modelo.queue_text("fin")
        agente.chat("hazlo", session_id="s")
        r = [m.tool_result for m in agente.sessions.get("s").messages if m.role == "tool"]
        assert UN_INTENTO in r[0]["error"]
        assert r[2]["error"] == SIN_MAS_INTENTOS


class TestTrasEjecutarUnPlanAprobado:
    """Medido con el modelo real: tras el informe de un plan que salió bien, a veces proponía
    otro plan para lo mismo. Quedaba pendiente y, aprobado, repetía el cambio."""

    def _montar(self, tmp_path, monkeypatch, ok):
        registro = ToolRegistry()
        registro.register(Falsa("anotar", "moderate", ok=ok))
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=permisos, planes=planificador)
        # Con un paso sin herramienta el modelo sí interviene tras ejecutar (desde la 4.1.5,
        # si todo sale y no hay nada que explicar, el resumen lo escribe el núcleo).
        plan = planificador.crear("anotar", [_paso("anotar", path="r.txt"),
                                             {"descripcion": "decirle cómo quedó"}], session_id="s")
        planificador.aprobar(plan.id)
        modelo.queue_tool_call("create_plan", {"objetivo": "anotar otra vez", "pasos": [_paso("anotar", path="r.txt")]})
        modelo.queue_text("Hecho.")
        agente.chat("✅ Plan aprobado: anotar", session_id="s", ejecutar_plan=plan.id)
        r = [m.tool_result for m in agente.sessions.get("s").messages if m.role == "tool"]
        return r, planificador

    def test_si_salio_bien_no_se_propone_otro(self, tmp_path, monkeypatch):
        from src.agent.core import PLAN_YA_HECHO

        r, planificador = self._montar(tmp_path, monkeypatch, ok=True)
        assert r[-1]["error"] == PLAN_YA_HECHO
        assert len(planificador.listar(session_id="s")) == 1, "solo el que se aprobó"

    def test_si_algo_no_salio_si_puede_proponer_el_intento(self, tmp_path, monkeypatch):
        r, planificador = self._montar(tmp_path, monkeypatch, ok=False)
        assert r[-1]["success"]
        assert len(planificador.listar(session_id="s")) == 2


class TestComprobarNoVaEnElPlan:
    def test_verify_step_antes_de_un_cambio_se_rechaza(self, tmp_path):
        """Medido en la 4.0.5: el modelo puso «verificar el paso 1» antes de crear."""
        registro = ToolRegistry()
        for h in (Falsa("verify_step"), Falsa("create_file", "moderate")):
            registro.register(h)
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        crear = {h.name: h for h in plan_tools(planificador)}["create_plan"]
        r = crear.execute(objetivo="x", pasos=[_paso("verify_step"), _paso("create_file", path="c")], session_id="s")
        assert r["success"] is False


class TestNoDiceQueHayPlanSinPlan:
    """Medido en la 4.0.5: con el plan rechazado por `create_plan`, el modelo contestó «He
    preparado el plan…» y no había nada que aprobar. Lo dice el núcleo, no el modelo."""

    def _turno(self, tmp_path, monkeypatch, llamadas, texto):
        registro = ToolRegistry()
        for h in (Falsa("read_file"), Falsa("create_file", "moderate")):
            registro.register(h)
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=permisos, planes=planificador)
        for pasos in llamadas:
            modelo.queue_tool_call("create_plan", {"objetivo": "x", "pasos": pasos})
        modelo.queue_text(texto)
        return agente.chat("hazlo", session_id="s")

    def test_si_se_rechazo_y_no_hay_plan_se_dice(self, tmp_path, monkeypatch):
        from src.agent.core import NOTA_SIN_PLAN

        r = self._turno(tmp_path, monkeypatch, [[_paso("read_file"), _paso("create_file", path="c")]],
                        "He preparado el plan.")
        assert r.endswith(NOTA_SIN_PLAN)

    def test_si_al_final_se_creo_no_se_dice_nada(self, tmp_path, monkeypatch):
        from src.agent.core import NOTA_SIN_PLAN

        r = self._turno(tmp_path, monkeypatch, [[_paso("read_file"), _paso("create_file", path="c")],
                                                [_paso("create_file", path="c", content="hola")]],
                        "He preparado el plan.")
        assert NOTA_SIN_PLAN not in r

    def test_sin_intentar_planificar_tampoco(self, tmp_path, monkeypatch):
        from src.agent.core import NOTA_SIN_PLAN

        assert NOTA_SIN_PLAN not in self._turno(tmp_path, monkeypatch, [], "Hola.")


class TestAnadirEnLaNube:
    def test_exige_plan_como_las_demas_de_escritura(self):
        from src.canal.herramientas import EscribirEnElEquipo

        h = EscribirEnElEquipo("append_file")
        assert h.exige_plan and h.en_el_pc
        assert set(h.parameters["required"]) == {"path", "content"}

    def test_el_origen_lo_pone_el_nucleo_y_llega_al_agente(self, monkeypatch):
        from src.canal import herramientas

        enviado = {}

        def enviar(capacidad, argumentos, plazo=None, equipo=None):
            enviado.update(argumentos)
            return {"success": True, "data": {"path": "C:/r.txt", "sha256": "ab" * 32}}

        monkeypatch.setattr(herramientas, "enviar", enviar)
        herramientas.EscribirEnElEquipo("append_file").execute(path="C:/r.txt", content="x", _origen="plan-a#2")
        assert enviado == {"path": "C:/r.txt", "content": "x", "origen": "plan-a#2"}
        enviado.clear()
        herramientas.EscribirEnElEquipo("append_file").execute(path="C:/r.txt", content="x")
        assert "origen" not in enviado


class TestElPromptDiceComoFuncionaAprobar:
    """4.1.5: el prompt aún decía, de antes de la 4.0-A, «cuando lo apruebe, get_plan te lo
    dirá y podrás continuar» y «solo entonces ejecútalos». Empujaba a llamar a get_plan y a
    re-ejecutar tras aprobar (visto en la 4.0.5); ahora el núcleo lo ejecuta solo."""

    def test_sin_las_instrucciones_viejas(self):
        from src.agent.prompt import PUEDE_ESCRIBIR, SYSTEM_PROMPT

        for texto in (SYSTEM_PROMPT, PUEDE_ESCRIBIR):
            assert "podrás continuar" not in texto and "ejecútalos" not in texto
        assert "se ejecuta solo" in SYSTEM_PROMPT

    def test_nunca_borrar_para_recrear(self):
        """Medido dos veces (4.1 y 4.1.5): para añadir una línea a un registro, el modelo
        propuso borrarlo y crearlo de nuevo."""
        from src.agent.prompt import PUEDE_ESCRIBIR

        assert "Nunca borres un archivo para crearlo de nuevo" in PUEDE_ESCRIBIR


class TestAlProponerUnPlan:
    """4.1.5: tras `create_plan` con un plan pendiente, el turno lo cierra el núcleo: la web
    enseña el plan entero encima del chat, y la llamada al modelo solo lo repetía en una
    tabla (medido: ~4.300 tokens de entrada y 400-870 de salida)."""

    def _montar(self, tmp_path, monkeypatch, pasos, texto="lo que diría el modelo"):
        registro = ToolRegistry()
        for h in (Falsa("read_file"), Falsa("create_file", "moderate"), Falsa("delete_file", "critical")):
            registro.register(h)
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=permisos, planes=planificador)
        modelo.queue_tool_call("create_plan", {"objetivo": "crear las notas", "pasos": pasos})
        modelo.queue_text(texto)
        return agente.chat("crea mis notas", session_id="s"), modelo

    def test_lo_cierra_el_nucleo_sin_otra_llamada(self, tmp_path, monkeypatch):
        r, modelo = self._montar(tmp_path, monkeypatch, [_paso("create_file", path="n.txt", content="x")])
        assert r == "Te propongo este plan: «crear las notas».\n\n1. create_file\n\nRevísalo y apruébalo si te parece bien."
        assert modelo.call_count == 1

    def test_dice_los_pasos_y_cuenta_los_que_no_caben(self):
        """La terminal no tiene panel de planes: los pasos van en el texto (4.2)."""
        from src.agent.core import _texto_de_propuesta

        texto = _texto_de_propuesta({"objetivo": "ordenar", "pasos": [
            {"descripcion": f"mover el {i}", "herramienta": "move_file"} for i in range(1, 11)]})
        assert "1. mover el 1" in texto and "8. mover el 8" in texto
        assert "mover el 9" not in texto and "… y 2 más." in texto

    def test_si_algo_se_confirma_en_el_pc_lo_avisa(self, tmp_path, monkeypatch):
        r, _ = self._montar(tmp_path, monkeypatch, [_paso("delete_file", path="n.txt")])
        assert "tu PC te pedirá confirmar" in r

    def test_si_el_plan_trae_un_aviso_sigue_el_modelo(self, tmp_path, monkeypatch):
        r, modelo = self._montar(tmp_path, monkeypatch, [_paso("no_existe", x=1)], texto="Lo corrijo.")
        assert r == "Lo corrijo." and modelo.call_count == 2

    def test_si_se_rechazo_sigue_el_modelo(self, tmp_path, monkeypatch):
        r, modelo = self._montar(tmp_path, monkeypatch, [_paso("read_file"), _paso("create_file", path="n.txt")],
                                 texto="Primero lo leo.")
        assert r.startswith("Primero lo leo.") and modelo.call_count == 2

    def test_la_descripcion_pide_llamarla_y_no_escribirlo(self):
        """Medido: una vez de cuatro, con la descripción corta, el modelo escribió el plan en
        una tabla sin llamar a `create_plan`: sin plan no hay botón de aprobar."""
        from src.tools.planificacion import CreatePlanTool

        assert "no lo escribas en tu respuesta" in CreatePlanTool(None).description

    def test_un_plan_que_no_necesita_aprobacion_no_cierra_el_turno(self, tmp_path, monkeypatch):
        """Uno solo de lectura queda aprobado al nacer: el modelo sigue con su trabajo."""
        r, modelo = self._montar(tmp_path, monkeypatch, [_paso("read_file", path="n.txt")], texto="Lo leo y te cuento.")
        assert r == "Lo leo y te cuento." and modelo.call_count == 2


class TestLaPlantillaNoTapaUnFallo:
    """Visto al auditar la 4.2: si un paso de un plan aprobado falla y el modelo propone otro
    camino (4.0-C), la plantilla cerraba el turno con «Te propongo este plan» y nadie
    contaba qué había fallado."""

    def _agente(self, tmp_path, monkeypatch, herramientas):
        registro = ToolRegistry()
        for h in herramientas:
            registro.register(h)
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        for h in plan_tools(planificador):
            registro.register(h)
        permisos = PermissionManager(interactive=False)
        monkeypatch.setattr(permisos, "check_permission", lambda tool, args: True)
        modelo = MockLLMProvider()
        return Agent(model=modelo, tool_registry=registro, permission_manager=permisos, planes=planificador), \
            modelo, planificador

    def test_si_un_paso_aprobado_falla_el_modelo_cuenta_el_fallo(self, tmp_path, monkeypatch):
        agente, modelo, planificador = self._agente(
            tmp_path, monkeypatch, [Falsa("create_file", "moderate", ok=False), Falsa("otra_forma", "moderate")])
        plan = planificador.crear("notas", [_paso("create_file", path="n.txt")], session_id="s")
        planificador.aprobar(plan.id)
        modelo.queue_tool_call("create_plan", {"objetivo": "otro camino", "pasos": [_paso("otra_forma", path="n.txt")]})
        modelo.queue_text("No pude crearlo; te propongo otro camino.")
        r = agente.chat("✅ Plan aprobado: notas", session_id="s", ejecutar_plan=plan.id)
        assert r == "No pude crearlo; te propongo otro camino."

    def test_si_otra_herramienta_fallo_antes_tambien(self, tmp_path, monkeypatch):
        agente, modelo, _ = self._agente(
            tmp_path, monkeypatch, [Falsa("mirar", ok=False), Falsa("create_file", "moderate")])
        modelo.queue_tool_call("mirar", {})
        modelo.queue_tool_call("create_plan", {"objetivo": "crear", "pasos": [_paso("create_file", path="n.txt")]})
        modelo.queue_text("No pude mirarlo antes; te propongo crearlo.")
        assert agente.chat("hazlo", session_id="s") == "No pude mirarlo antes; te propongo crearlo."

    def test_un_create_plan_rechazado_y_luego_bien_si_se_cierra(self, tmp_path, monkeypatch):
        """El rechazo de la guarda (4.0-D) no es un fallo que contar: el modelo lo rehízo."""
        agente, modelo, _ = self._agente(tmp_path, monkeypatch, [Falsa("read_file"), Falsa("create_file", "moderate")])
        modelo.queue_tool_call("create_plan", {"objetivo": "crear", "pasos": [_paso("read_file"), _paso("create_file", path="n")]})
        modelo.queue_tool_call("create_plan", {"objetivo": "crear", "pasos": [_paso("create_file", path="n", content="x")]})
        modelo.queue_text("no debería llegar")
        assert agente.chat("hazlo", session_id="s").startswith("Te propongo este plan: «crear»")
