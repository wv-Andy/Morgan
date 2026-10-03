"""
Planificación: decidir qué hacer antes de hacerlo (V1.6).

La V1.6 mete un paso entre pensar y ejecutar, y eso permite tres cosas que antes
no se podían: enseñar el trabajo entero antes de empezarlo, calcular el riesgo de
lo que **viene** y rechazarlo sin dejar nada a medias.

La prueba que sostiene todo lo demás es la primera: **el riesgo lo pone el
registro de herramientas, no el modelo**. Si viniera en la propuesta, bastaría con
escribir `"riesgo": "safe"` junto a un borrado para saltarse la aprobación.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.tasks.plan import EstadoPlan, PasoPlaneado, Plan, TransicionInvalida
from src.tasks.planificador import MAX_PASOS, Planificador
from src.tools.filesystem import CreateFileTool, DeleteFileTool, ListFilesTool, ReadFileTool
from src.tools.registry import ToolRegistry
from src.tools.web import SearchWebTool


@pytest.fixture
def registro():
    reg = ToolRegistry()
    for herramienta in (
        ListFilesTool(), ReadFileTool(), CreateFileTool(),
        DeleteFileTool(), SearchWebTool(),
    ):
        reg.register(herramienta)
    return reg


@pytest.fixture
def planificador(tmp_path, registro):
    fabrica = SQLiteRepositoryFactory(Database(tmp_path / "planes.db"))
    return Planificador(fabrica.planes, registro)


class TestElRiesgoLoPoneMorganNoElModelo:
    """La decisión que sostiene todo lo demás."""

    def test_un_riesgo_mentido_en_la_propuesta_se_ignora(self, planificador):
        plan = planificador.crear("Limpiar", [
            {"descripcion": "Borrar", "herramienta": "delete_file", "riesgo": "safe"},
        ])

        assert plan.pasos[0].riesgo != "safe"
        assert plan.necesita_aprobacion is True

    def test_el_riesgo_sale_del_registro(self, planificador, registro):
        plan = planificador.crear("Mirar", [
            {"descripcion": "Listar", "herramienta": "list_files"},
        ])

        assert plan.pasos[0].riesgo == str(registro.get("list_files").risk_level)

    def test_una_herramienta_inventada_se_trata_como_critica(self, planificador):
        """Fail-safe. Lo peor que puede pasar así es pedir una aprobación de más;
        al revés, se ejecutaría sin preguntar algo que nadie ha clasificado."""
        plan = planificador.crear("Raro", [
            {"descripcion": "Vete a saber", "herramienta": "herramienta_inventada"},
        ])

        assert plan.pasos[0].riesgo == "critical"
        assert plan.necesita_aprobacion is True

    def test_un_paso_sin_herramienta_tambien(self, planificador):
        plan = planificador.crear("Vago", [{"descripcion": "Hacer algo"}])

        assert plan.necesita_aprobacion is True


class TestElRiesgoDelPlanEsElDelPeorPaso:
    def test_no_es_un_promedio(self, planificador):
        """Un plan que lee dos archivos y luego borra un directorio es un plan
        que borra un directorio. Presentarlo como «riesgo bajo con algún paso
        delicado» sería engañar a quien decide."""
        plan = planificador.crear("Mixto", [
            {"descripcion": "Listar", "herramienta": "list_files"},
            {"descripcion": "Leer", "herramienta": "read_file"},
            {"descripcion": "Borrar", "herramienta": "delete_file"},
        ])

        assert plan.riesgo == "critical"

    def test_todo_lectura_es_seguro(self, planificador):
        plan = planificador.crear("Consultar", [
            {"descripcion": "Listar", "herramienta": "list_files"},
            {"descripcion": "Buscar", "herramienta": "search_web"},
        ])

        assert plan.necesita_aprobacion is False
        assert plan.estado == EstadoPlan.APROBADO.value

    def test_un_plan_sin_pasos_no_se_crea(self, planificador):
        with pytest.raises(ValueError):
            planificador.crear("Nada", [])

    def test_un_plan_sin_objetivo_no_se_crea(self, planificador):
        with pytest.raises(ValueError):
            planificador.crear("   ", [{"descripcion": "Algo"}])

    def test_hay_un_tope_de_pasos(self, planificador):
        """Un plan con más pasos que esto casi nunca es un plan: es el modelo
        enumerando."""
        with pytest.raises(ValueError):
            planificador.crear("Interminable", [
                {"descripcion": f"Paso {i}"} for i in range(MAX_PASOS + 1)
            ])


class TestLoSimpleNoEsperaANadie:
    """Hacer aprobar «voy a leer tres archivos» convierte la aprobación en un
    trámite que se acepta sin mirar, y entonces deja de proteger de nada."""

    def test_un_plan_de_solo_lectura_nace_aprobado(self, planificador):
        plan = planificador.crear("Mirar", [
            {"descripcion": "Listar", "herramienta": "list_files"},
        ])

        assert plan.estado == EstadoPlan.APROBADO.value
        assert plan.ejecutable is True

    def test_uno_con_riesgo_nace_pendiente(self, planificador):
        plan = planificador.crear("Tocar", [
            {"descripcion": "Escribir", "herramienta": "create_file"},
        ])

        assert plan.estado == EstadoPlan.PENDIENTE.value
        assert plan.ejecutable is False


class TestLaDecision:
    @pytest.fixture
    def pendiente(self, planificador):
        return planificador.crear("Borrar cosas", [
            {"descripcion": "Borrar", "herramienta": "delete_file"},
        ])

    def test_aprobar_lo_hace_ejecutable(self, planificador, pendiente):
        plan = planificador.aprobar(pendiente.id)

        assert plan.estado == EstadoPlan.APROBADO.value
        assert plan.ejecutable is True
        assert plan.decidido_en is not None

    def test_rechazar_lo_cierra(self, planificador, pendiente):
        plan = planificador.rechazar(pendiente.id, motivo="No hace falta")

        assert plan.estado == EstadoPlan.RECHAZADO.value
        assert plan.motivo_rechazo == "No hace falta"
        assert plan.ejecutable is False

    def test_un_plan_rechazado_no_se_aprueba_despues(self, planificador, pendiente):
        planificador.rechazar(pendiente.id)

        with pytest.raises(TransicionInvalida):
            planificador.aprobar(pendiente.id)

    def test_un_plan_aprobado_no_se_rechaza_a_medias(self, planificador, pendiente):
        """Se puede rechazar antes de ejecutar, pero una vez en marcha ya no: eso
        dejaría el trabajo a medias, que es justo lo que esta capa evita."""
        planificador.aprobar(pendiente.id)
        planificador.marcar_ejecutando(pendiente.id)

        with pytest.raises(TransicionInvalida):
            planificador.rechazar(pendiente.id)

    def test_no_se_ejecuta_uno_pendiente(self, planificador, pendiente):
        from src.tasks.planificador import PlanNoAprobado

        with pytest.raises(PlanNoAprobado):
            planificador.marcar_ejecutando(pendiente.id)

    def test_el_motivo_se_recorta(self, planificador, pendiente):
        plan = planificador.rechazar(pendiente.id, motivo="x" * 500)

        assert len(plan.motivo_rechazo) <= 300


class TestLoQueSeLePublicaAQuienDecide:
    def test_los_secretos_se_enmascaran(self, planificador):
        """Quien aprueba necesita ver qué archivo o qué comando, no una clave."""
        plan = planificador.crear("Con secretos", [
            {"descripcion": "Escribir", "herramienta": "create_file",
             "argumentos": {"path": "x.txt", "API_KEY": "esto-es-secreto"}},
        ])

        publicado = plan.to_dict()["pasos"][0]["argumentos"]

        assert publicado["API_KEY"] == "********"
        assert publicado["path"] == "x.txt"

    def test_los_argumentos_largos_se_recortan(self, planificador):
        plan = planificador.crear("Grande", [
            {"descripcion": "Escribir", "herramienta": "create_file",
             "argumentos": {"content": "y" * 500}},
        ])

        publicado = plan.to_dict()["pasos"][0]["argumentos"]["content"]

        assert len(publicado) < 200

    def test_pero_se_guardan_enteros(self, planificador):
        """Guardar la versión recortada significaría ejecutar después con
        argumentos truncados."""
        plan = planificador.crear("Grande", [
            {"descripcion": "Escribir", "herramienta": "create_file",
             "argumentos": {"content": "y" * 500}},
        ])

        recuperado = planificador.obtener(plan.id)

        assert len(recuperado.pasos[0].argumentos["content"]) == 500

    def test_el_resumen_se_lee_en_una_consola_de_windows(self, planificador):
        """Sin caracteres que cp1252 no sepa escribir: este texto acaba en el log
        y en la consola, y un símbolo raro revienta la línea entera."""
        plan = planificador.crear("Mixto", [
            {"descripcion": "Listar", "herramienta": "list_files"},
            {"descripcion": "Borrar", "herramienta": "delete_file"},
        ])

        plan.resumen().encode("cp1252")


class TestElPlanSobreviveAlReinicio:
    def test_se_recupera_igual(self, tmp_path, registro):
        """Un plan pendiente que se perdiera al reiniciar dejaría a la persona
        sin poder decidir sobre un trabajo que Morgan ya había preparado."""
        base = Database(tmp_path / "persistente.db")

        primero = Planificador(SQLiteRepositoryFactory(base).planes, registro)
        creado = primero.crear("Persistir", [
            {"descripcion": "Borrar", "herramienta": "delete_file",
             "argumentos": {"path": "importante.txt"}},
        ])

        # Otro proceso, misma base.
        segundo = Planificador(SQLiteRepositoryFactory(base).planes, registro)
        recuperado = segundo.obtener(creado.id)

        assert recuperado is not None
        assert recuperado.estado == EstadoPlan.PENDIENTE.value
        assert recuperado.pasos[0].argumentos["path"] == "importante.txt"
        assert recuperado.riesgo == creado.riesgo

    def test_los_pasos_corruptos_no_impiden_ver_el_plan(self, tmp_path, registro):
        """Sin pasos queda inservible, pero visible y borrable, que es mejor que
        una excepción en mitad del listado."""
        base = Database(tmp_path / "corrupta.db")
        fabrica = SQLiteRepositoryFactory(base)
        plan = Planificador(fabrica.planes, registro).crear("Roto", [
            {"descripcion": "Algo", "herramienta": "list_files"},
        ])

        with base.connect() as conn:
            conn.execute("UPDATE planes SET pasos = 'esto no es json'")

        recuperado = fabrica.planes.get(plan.id)

        assert recuperado is not None
        assert recuperado.pasos == []


class TestLoQueLeeElModelo:
    """El mensaje importa tanto como los datos: es lo que decide si sigue o se
    detiene, y tiene que ser inequívoco."""

    @pytest.fixture
    def herramientas(self, planificador):
        from src.tools.planificacion import plan_tools

        return {h.name: h for h in plan_tools(planificador)}

    def test_al_crear_uno_pendiente_se_le_dice_que_pare(self, herramientas):
        resultado = herramientas["create_plan"].execute(
            objetivo="Borrar", pasos=[{"descripcion": "Borrar", "herramienta": "delete_file"}]
        )

        assert "NO EJECUTES NADA" in resultado["data"]["siguiente_paso"]

    def test_al_crear_uno_simple_se_le_dice_que_siga(self, herramientas):
        resultado = herramientas["create_plan"].execute(
            objetivo="Mirar", pasos=[{"descripcion": "Listar", "herramienta": "list_files"}]
        )

        assert "no necesita aprobación" in resultado["data"]["siguiente_paso"]

    def test_tras_rechazarlo_se_le_dice_que_no_insista(self, herramientas, planificador):
        """Sin esto, lo natural es que proponga el mismo plan con otras palabras."""
        creado = herramientas["create_plan"].execute(
            objetivo="Borrar", pasos=[{"descripcion": "Borrar", "herramienta": "delete_file"}]
        )["data"]
        planificador.rechazar(creado["id"])

        leido = herramientas["get_plan"].execute(plan_id=creado["id"])["data"]

        assert "RECHAZÓ" in leido["siguiente_paso"]
        assert "ni propongas uno equivalente" in leido["siguiente_paso"]

    def test_si_usa_herramientas_que_aqui_no_existen_se_le_dice(self, herramientas):
        """Medido en la 2.3-D con el modelo real en la nube: para ordenar apuntes
        propuso `move_file` y `make_directory`, que allí no existen. El plan
        sigue siendo seguro (pide aprobación), pero la persona aprobaba pasos
        que nadie podía ejecutar."""
        resultado = herramientas["create_plan"].execute(
            objetivo="Ordenar",
            pasos=[
                {"descripcion": "Ver qué hay", "herramienta": "list_files"},
                {"descripcion": "Mover", "herramienta": "move_file"},
                {"descripcion": "Crear carpeta", "herramienta": "make_directory"},
            ],
        )["data"]

        assert resultado["herramientas_que_no_existen"] == ["make_directory", "move_file"]
        assert "no existen estas herramientas: make_directory, move_file" in resultado["siguiente_paso"]
        assert "NO EJECUTES NADA" in resultado["siguiente_paso"]

    def test_si_todas_existen_no_hay_aviso(self, herramientas):
        resultado = herramientas["create_plan"].execute(
            objetivo="Mirar", pasos=[{"descripcion": "Listar", "herramienta": "list_files"}]
        )["data"]

        assert "herramientas_que_no_existen" not in resultado
        assert "ATENCIÓN" not in resultado["siguiente_paso"]

    def test_un_plan_mal_formado_es_un_resultado_no_una_excepcion(self, herramientas):
        """Devolverlo como error de herramienta le permite corregirse; lanzarlo
        abortaría el turno."""
        resultado = herramientas["create_plan"].execute(objetivo="", pasos=[])

        assert resultado["success"] is False
        assert resultado["error"]

    def test_un_plan_que_no_existe_no_revienta(self, herramientas):
        resultado = herramientas["get_plan"].execute(plan_id="no-existe")

        assert resultado["success"] is False

    def test_planificar_no_pide_permiso(self, herramientas):
        """Pedir permiso para pensar sería absurdo, y además haría que el modelo
        evitara planificar."""
        for herramienta in herramientas.values():
            assert herramienta.permission_level == "safe"

    def test_las_herramientas_de_plan_existen_en_la_nube(self, herramientas):
        """Trabajan sobre la base, no sobre el disco."""
        for herramienta in herramientas.values():
            assert herramienta.requires_local is False


class TestPorHttp:
    @pytest.fixture
    def cliente(self):
        from src.api import dependencies

        dependencies.reset_container()
        return TestClient(create_app())

    @pytest.fixture
    def contenedor(self, cliente):
        from src.api import dependencies

        return dependencies.get_container()

    def _crear(self, contenedor, herramienta="delete_file"):
        return contenedor.tool_registry.get("create_plan").execute(
            objetivo="Prueba", pasos=[{"descripcion": "Paso", "herramienta": herramienta}]
        )["data"]

    def test_se_listan_los_pendientes(self, cliente, contenedor):
        self._crear(contenedor)

        respuesta = cliente.get("/planes?solo_pendientes=true")

        assert respuesta.status_code == 200
        assert respuesta.json()["count"] == 1

    def test_se_aprueba(self, cliente, contenedor):
        plan = self._crear(contenedor)

        respuesta = cliente.post(f"/planes/{plan['id']}/aprobar")

        assert respuesta.status_code == 200
        assert respuesta.json()["plan"]["estado"] == "aprobado"

    def test_se_rechaza_con_motivo(self, cliente, contenedor):
        plan = self._crear(contenedor)

        respuesta = cliente.post(
            f"/planes/{plan['id']}/rechazar", json={"motivo": "No quiero"}
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["plan"]["motivo_rechazo"] == "No quiero"

    def test_aprobar_uno_rechazado_da_409(self, cliente, contenedor):
        """La ruta y el plan existen, pero no en ese estado."""
        plan = self._crear(contenedor)
        cliente.post(f"/planes/{plan['id']}/rechazar")

        respuesta = cliente.post(f"/planes/{plan['id']}/aprobar")

        assert respuesta.status_code == 409

    def test_uno_que_no_existe_da_404(self, cliente):
        assert cliente.post("/planes/inventado/aprobar").status_code == 404

    def test_no_se_pueden_crear_planes_por_la_api(self, cliente):
        """Los crea el agente al planificar. Exponerlo permitiría escribir planes
        que no corresponden a nada que Morgan haya pensado."""
        assert cliente.post("/planes", json={"objetivo": "x"}).status_code in (404, 405)


class TestElRechazoEncaminaHaciaElPlan:
    """El hallazgo del gate de la V1.6, y el más instructivo.

    Se probó con dos modelos el mismo encargo —«borra los .tmp de esta
    carpeta»—. Groq creaba el plan; el otro iba **directo a `delete_file`**, que
    se denegaba por no haber consola, y ahí se quedaba todo.

    Poner la regla en el system prompt no bastó: con un modelo funcionaba y con
    otro no. Lo que sí funciona es decírselo **en el momento del rechazo**, dentro
    del bucle, donde no depende de que se acuerde de algo que leyó al principio.
    """

    @staticmethod
    def _agente(interactivo: bool, con_planes: bool = True, tmp_path=None):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(DeleteFileTool())

        if con_planes:
            from src.memory.db import Database
            from src.memory.sqlite_repositories import SQLiteRepositoryFactory
            from src.tasks.planificador import Planificador
            from src.tools.planificacion import plan_tools

            fabrica = SQLiteRepositoryFactory(Database(tmp_path / "p.db"))
            for h in plan_tools(Planificador(fabrica.planes, registro)):
                registro.register(h)

        return Agent(
            model=MockLLMProvider(),
            tool_registry=registro,
            permission_manager=PermissionManager(
                audit_logger=AuditLogger(tmp_path / "a.log"), interactive=interactivo
            ),
        )

    def test_sin_consola_se_le_ofrece_planificar(self, tmp_path):
        agente = self._agente(interactivo=False, tmp_path=tmp_path)

        mensaje = agente._por_que_no_se_pudo("delete_file")

        assert "create_plan" in mensaje
        assert "aprobar" in mensaje

    def test_con_consola_se_le_dice_que_alguien_dijo_que_no(self, tmp_path):
        """Ahí no hay nada que rodear: una negativa es una respuesta, no un
        obstáculo."""
        agente = self._agente(interactivo=True, tmp_path=tmp_path)

        mensaje = agente._por_que_no_se_pudo("delete_file")

        assert "denegó" in mensaje
        assert "create_plan" not in mensaje

    def test_sin_planes_disponibles_no_se_promete_lo_que_no_hay(self, tmp_path):
        """Si las herramientas de plan no están registradas, ofrecerlas mandaría
        al modelo a llamar a algo que no existe."""
        agente = self._agente(interactivo=False, con_planes=False, tmp_path=tmp_path)

        mensaje = agente._por_que_no_se_pudo("delete_file")

        assert "create_plan" not in mensaje

    def test_el_mensaje_nombra_la_herramienta_rechazada(self, tmp_path):
        agente = self._agente(interactivo=False, tmp_path=tmp_path)

        assert "delete_file" in agente._por_que_no_se_pudo("delete_file")


class TestUnPasoSinHerramientaNoRompeLaLlamada:
    """Medido en la 2.3-D con el modelo real: en 4 de 5 planes, un paso como
    «preguntar a la persona» llegaba con `"herramienta": null`. Groq valida el
    esquema en su lado y rechazaba la llamada ENTERA con un 400; el turno caía al
    respaldo y tardaba 24-35 s, o fallaba. El esquema tiene que admitir el nulo en
    los campos opcionales de un paso, y Gemini, el respaldo, tiene que entenderlo."""

    @staticmethod
    def _paso():
        from src.tools.planificacion import CreatePlanTool

        esquema = CreatePlanTool.__dict__["parameters"].fget(None)
        return esquema, esquema["properties"]["pasos"]["items"]["properties"]

    @pytest.mark.parametrize("campo", ["herramienta", "argumentos", "depende_de", "motivo"])
    def test_los_campos_opcionales_admiten_null(self, campo):
        _, paso = self._paso()

        assert "null" in paso[campo]["type"], f"«{campo}» no admite null: Groq rechazará la llamada"

    def test_la_descripcion_sigue_siendo_obligatoria_y_de_texto(self):
        esquema, paso = self._paso()

        assert paso["descripcion"]["type"] == "string"
        assert esquema["properties"]["pasos"]["items"]["required"] == ["descripcion"]

    def test_gemini_lo_traduce_como_anulable(self):
        from src.models.gemini import GeminiProvider

        esquema, _ = self._paso()
        paso = GeminiProvider._a_schema(esquema).properties["pasos"].items.properties

        assert paso["herramienta"].nullable is True
        assert str(paso["herramienta"].type).endswith("STRING")
        assert str(paso["depende_de"].items.type).endswith("INTEGER")

    def test_y_un_paso_con_null_se_guarda_sin_herramienta(self, planificador):
        from src.tools.planificacion import plan_tools

        crear = {h.name: h for h in plan_tools(planificador)}["create_plan"]
        datos = crear.execute(
            objetivo="Ordenar", pasos=[{"descripcion": "Preguntar", "herramienta": None, "argumentos": None,
                                        "depende_de": None, "motivo": None}],
        )["data"]

        assert datos["pasos"][0]["herramienta"] is None
