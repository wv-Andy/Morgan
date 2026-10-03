"""
Qué herramientas se le mandan al modelo en cada turno, y qué no.

**Por qué importa, con los números.** El catálogo son ~3.326 tokens de los
~3.716 que pesa una petición real en producción: **el 90%**. Y el cupo diario
de Groq son 200.000 tokens, unos 54 turnos.

Y hay un segundo motivo que puede ser el mayor: **menos herramientas significa
más acierto**. Medido con un modelo pequeño, con una sola herramienta declarada
acertaba, y con 49 delante se perdía y llegaba a inventarse la respuesta.
Ahorrar cuota y acertar más apuntan al mismo sitio.

## La regla que gobierna este archivo

Solo se quita lo que **no puede usarse en este turno**, y eso es distinto de
adivinar qué va a hacer falta. Una precondición comprobable —no hay tarea, así
que `get_task` no tiene a qué apuntar— es segura. Una corazonada sobre lo que
el usuario va a pedir no lo es.

La mitad de las pruebas de aquí comprueban justo eso: que **no** se quite nada
que el modelo pudiera necesitar. Ahorrar cuota a costa de que Morgan no pueda
trabajar no sería un ahorro.
"""

import pytest

from src.agent.core import Agent


class SesionFalsa:
    def __init__(self, session_id="ses-1", temporary=False):
        self.session_id = session_id
        self.temporary = temporary
        self.messages = []


class HerramientasFalsas:
    """Devuelve los esquemas por su nombre, que es lo único que se filtra."""

    def __init__(self, nombres):
        self._nombres = list(nombres)

    def get_schemas(self):
        return [
            {"name": n, "description": f"hace {n}", "parameters": {}}
            for n in self._nombres
        ]

    def list_tools(self):
        return []


class TareasFalsas:
    def __init__(self, activas=()):
        self._activas = list(activas)

    def listar(self, session_id=None, solo_activas=False):
        return list(self._activas)


TODAS = (
    "remember_fact", "forget_fact", "recall_memory",
    "create_task", "get_task", "list_tasks", "cancel_task", "retry_task",
    "complete_task", "fail_task",
    "create_plan", "get_plan", "list_plans",
    "search_web",
)


def agente(nombres=TODAS, tareas=None):
    """Un agente con lo mínimo para preguntarle por su catálogo."""
    a = object.__new__(Agent)
    a.tools = HerramientasFalsas(nombres)
    a.tasks = tareas
    return a


def nombres_de(agente_, sesion):
    return {e["name"] for e in agente_._schemas_for(sesion)}


class TestSinTareasAbiertasSeQuitanLasQueNoSirven:
    def test_se_quitan_las_ocho(self, ):
        a = agente(tareas=TareasFalsas(activas=[]))

        visibles = nombres_de(a, SesionFalsa())

        for nombre in Agent._SOLO_CON_TAREA_ABIERTA:
            assert nombre not in visibles, (
                f"{nombre} sigue en el catálogo sin ninguna tarea abierta, y "
                "no hay llamada válida que hacer con ella"
            )

    def test_y_el_ahorro_es_de_verdad(self):
        """Ocho de quince en este catálogo de prueba. En el de producción son
        ocho de veintinueve, el 17% de los caracteres.
        """
        a = agente(tareas=TareasFalsas(activas=[]))

        visibles = nombres_de(a, SesionFalsa())

        assert len(visibles) == len(TODAS) - 8


class TestLoQueNoSeQuitaNUNCA:
    """La otra mitad, y la que impide que esto se convierta en un problema.

    Si se quitara `create_task`, no habría forma de crear la primera tarea: el
    recorte se volvería permanente y la funcionalidad, inalcanzable. Es el mismo
    error que dejó a NVIDIA sin poder activarse al sacarlo del orden por
    defecto.
    """

    @pytest.mark.parametrize("imprescindible", [
        "create_task",   # sin ella no nace ninguna tarea
        "create_plan",   # sin ella no nace ningún plan
    ])
    def test_las_que_empiezan_algo_estan_siempre(self, imprescindible):
        a = agente(tareas=TareasFalsas(activas=[]))

        assert imprescindible in nombres_de(a, SesionFalsa())

    def test_lo_que_no_es_de_tareas_no_se_toca(self):
        a = agente(tareas=TareasFalsas(activas=[]))

        visibles = nombres_de(a, SesionFalsa())

        assert "search_web" in visibles
        assert "recall_memory" in visibles


class TestConUnaTareaAbiertaVuelveElCatalogoEntero:
    def test_estan_todas(self):
        a = agente(tareas=TareasFalsas(activas=[{"id": "t1"}]))

        visibles = nombres_de(a, SesionFalsa())

        assert visibles == set(TODAS), (
            "Con una tarea abierta hacen falta todas, y falta alguna"
        )

    def test_el_filtro_pregunta_por_ESTA_conversacion(self):
        """Una tarea abierta en otra conversación no habilita las herramientas
        aquí: `get_task` seguiría sin tener a qué apuntar en este hilo.
        """
        pedidos = []

        class TareasQueApuntan(TareasFalsas):
            def listar(self, session_id=None, solo_activas=False):
                pedidos.append((session_id, solo_activas))
                return []

        a = agente(tareas=TareasQueApuntan())
        nombres_de(a, SesionFalsa(session_id="ses-42"))

        assert pedidos, "no se ha consultado por las tareas"
        assert pedidos[0][0] == "ses-42"
        assert pedidos[0][1] is True, "solo cuentan las tareas ABIERTAS"


class TestAnteLaDudaNoSeQuitaNada:
    """El coste de equivocarse no es simétrico: hacia un lado son unos tokens,
    hacia el otro es que Morgan no pueda cancelar una tarea que sí existe.
    """

    def test_si_el_almacen_falla_se_deja_el_catalogo_entero(self):
        class TareasQueRevientan:
            def listar(self, session_id=None, solo_activas=False):
                raise RuntimeError("la base dice que no")

        a = agente(tareas=TareasQueRevientan())

        assert nombres_de(a, SesionFalsa()) == set(TODAS)

    def test_sin_gestor_de_tareas_se_recorta(self):
        """Distinto del caso de arriba: aquí no es que falle la consulta, es que
        este Morgan no tiene tareas en absoluto. Entonces no hay ninguna abierta
        con certeza, y quitarlas es correcto.
        """
        a = agente(tareas=None)

        visibles = nombres_de(a, SesionFalsa())

        assert "get_task" not in visibles
        assert "create_task" in visibles


class TestSeSigueQuitandoLaMemoriaEnLasTemporales:
    """Lo que ya hacía antes, y que este cambio no podía romper."""

    def test_en_una_conversacion_temporal(self):
        a = agente(tareas=TareasFalsas(activas=[{"id": "t1"}]))

        visibles = nombres_de(a, SesionFalsa(temporary=True))

        assert "remember_fact" not in visibles
        assert "forget_fact" not in visibles

    def test_y_los_dos_filtros_se_suman(self):
        """Temporal y sin tareas: se quitan las de memoria Y las de tarea. La
        primera versión usaba un `return` temprano y aplicaba solo uno.
        """
        a = agente(tareas=TareasFalsas(activas=[]))

        visibles = nombres_de(a, SesionFalsa(temporary=True))

        assert "remember_fact" not in visibles
        assert "get_task" not in visibles
        assert "search_web" in visibles


class TestLasListasNoSeSolapan:
    def test_ninguna_imprescindible_esta_en_la_lista_de_recorte(self):
        """Un nombre en las dos listas sería un recorte permanente."""
        recortables = set(Agent._SOLO_CON_TAREA_ABIERTA)
        imprescindibles = {"create_task", "create_plan"}

        assert not (recortables & imprescindibles)

    def test_las_recortables_existen_de_verdad(self):
        """Un nombre mal escrito en la lista no recorta nada y no avisa."""
        import os
        import tempfile

        os.environ.setdefault("MORGAN_DATA_DIR", tempfile.mkdtemp())
        os.environ.setdefault("MORGAN_LOG_DIR", tempfile.mkdtemp())
        os.environ["MORGAN_SERVE_WEB"] = "false"

        from src.api.dependencies import get_container
        from src.config import reset_settings

        reset_settings()
        reales = {t.name for t in get_container().tool_registry.list_tools()}

        fantasmas = set(Agent._SOLO_CON_TAREA_ABIERTA) - reales
        assert not fantasmas, f"En la lista de recorte y sin existir: {fantasmas}"


class TestVerifyStepRetirada:
    """4.1.5: `verify_step` pedía una llamada más en cada cambio (la verificación ya es
    automática) y, medido, daba por fallido lo creado en el PC mirando el disco del
    servidor. Retirada del catálogo, como `AdvanceTaskTool` en la 2.0.11."""

    def test_no_esta_entre_las_de_tareas(self, tmp_path):
        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tasks.manager import TaskManager
        from src.tools.tareas import task_tools

        repos = SQLiteRepositoryFactory(Database(tmp_path / "t.db"))
        nombres = {h.name for h in task_tools(TaskManager(repos.tasks))}
        assert "verify_step" not in nombres and "create_task" in nombres


class TestSeMiraUnaVezPorTurno:
    """4.3: `_hay_algo_abierto` son dos consultas a la base (tareas y planes), y se hacían en
    **cada** vuelta del modelo. Ahora una vez por turno, y otra solo tras una herramienta
    que abre o cierra tareas o planes."""

    class Contando:
        def __init__(self):
            self.veces = 0

        def listar(self, session_id=None, solo_activas=False):
            self.veces += 1
            return []

    def _turno(self, llamadas):
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        tareas = self.Contando()
        modelo = MockLLMProvider()
        for nombre in llamadas:
            modelo.queue_tool_call(nombre, {})
        modelo.queue_text("fin")
        agente = Agent(model=modelo, tool_registry=ToolRegistry(),
                       permission_manager=PermissionManager(interactive=False), tasks=tareas)
        agente.chat("hazlo", session_id="s")
        return tareas.veces

    def test_varias_vueltas_una_consulta(self):
        assert self._turno(["mirar", "mirar_otra", "mirar_mas"]) == 1

    def test_tras_crear_una_tarea_se_vuelve_a_mirar(self):
        assert self._turno(["create_task", "mirar"]) == 2
