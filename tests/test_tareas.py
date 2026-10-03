"""
Sistema de tareas (V1.5).

Morgan pasa de `pregunta → respuesta` a `objetivo → tarea → pasos → resultado`.
Lo que se comprueba aquí no es que se guarden filas, sino que el estado no pueda
mentir: una tarea completada no vuelve a ponerse en marcha, un paso que falló
queda registrado, y el progreso refleja lo que de verdad ocurrió.
"""

import pytest

from src.memory.db import Database
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.tasks import MAX_INTENTOS, TaskManager, TaskState, TransicionInvalida


@pytest.fixture
def gestor(tmp_path):
    return TaskManager(SQLiteRepositoryFactory(Database(tmp_path / "tareas.db")).tasks)


class TestCrear:
    def test_una_tarea_nace_pendiente(self, gestor):
        tarea = gestor.crear("Investigar Supabase")

        assert tarea.estado == TaskState.PENDING.value
        assert tarea.progreso == 0.0

    def test_puede_nacer_con_pasos_previstos(self, gestor):
        tarea = gestor.crear("Investigar", pasos=["Buscar", "Leer", "Resumir"])

        assert [p.descripcion for p in tarea.pasos] == ["Buscar", "Leer", "Resumir"]
        assert tarea.paso_actual.descripcion == "Buscar"

    def test_los_pasos_en_blanco_se_descartan(self, gestor):
        tarea = gestor.crear("Algo", pasos=["Uno", "   ", "", "Dos"])

        assert len(tarea.pasos) == 2

    def test_sin_objetivo_no_hay_tarea(self, gestor):
        with pytest.raises(ValueError):
            gestor.crear("   ")

    def test_se_asocia_a_la_conversacion(self, gestor):
        gestor.crear("De esta charla", session_id="s1")
        gestor.crear("De otra", session_id="s2")

        assert len(gestor.listar(session_id="s1")) == 1


class TestElProgresoNoMiente:
    def test_avanzar_consume_el_paso_previsto_en_vez_de_duplicarlo(self, gestor):
        """El defecto que apareció al ejercitar el gestor: tres pasos previstos
        más tres ejecutados daban seis, y el progreso salía a la mitad de lo real."""
        tarea = gestor.crear("Investigar", pasos=["Buscar", "Leer", "Resumir"])
        gestor.iniciar(tarea.id)

        tarea = gestor.avanzar(tarea.id, "Búsqueda hecha", herramienta="search_web")

        assert len(tarea.pasos) == 3, "no debe añadirse un paso nuevo"
        assert tarea.progreso == pytest.approx(1 / 3)
        assert tarea.paso_actual.descripcion == "Leer"

    def test_un_paso_no_previsto_si_se_anade(self, gestor):
        tarea = gestor.crear("Sin plan previo")
        gestor.iniciar(tarea.id)

        tarea = gestor.avanzar(tarea.id, "Algo que surgió")

        assert len(tarea.pasos) == 1

    def test_una_tarea_completada_esta_al_cien_por_cien(self, gestor):
        """Aunque termine antes de agotar sus pasos previstos, porque el objetivo
        ya se cumplió. Ver «completada» junto a un 33 % confunde a quien mira."""
        tarea = gestor.crear("Con atajo", pasos=["Uno", "Dos", "Tres"])
        gestor.iniciar(tarea.id)
        gestor.avanzar(tarea.id, "Uno", resultado="ya estaba hecho")

        tarea = gestor.completar(tarea.id, "no hacía falta el resto")

        assert tarea.progreso == 1.0

    def test_un_paso_fallido_queda_registrado(self, gestor):
        """Es lo que permitirá diagnosticar en la V1.7."""
        tarea = gestor.crear("Algo")
        gestor.iniciar(tarea.id)

        tarea = gestor.avanzar(tarea.id, "Leer la web", error="404 al abrir la página")

        assert tarea.pasos[0].estado == TaskState.FAILED.value
        assert "404" in tarea.pasos[0].error

    def test_un_paso_fallido_no_cuenta_como_progreso(self, gestor):
        tarea = gestor.crear("Algo", pasos=["Uno", "Dos"])
        gestor.iniciar(tarea.id)

        tarea = gestor.avanzar(tarea.id, "Uno", error="falló")

        assert tarea.progreso == 0.0


class TestElEstadoNoPuedeMentir:
    def test_una_tarea_completada_no_admite_pasos_nuevos(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.iniciar(tarea.id)
        gestor.completar(tarea.id, "hecho")

        with pytest.raises(TransicionInvalida, match="completó"):
            gestor.avanzar(tarea.id, "otro paso")

    def test_no_se_reescribe_un_desenlace(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.completar(tarea.id, "primer resultado")

        with pytest.raises(TransicionInvalida):
            gestor.completar(tarea.id, "otro resultado")

        assert gestor.obtener(tarea.id).resultado == "primer resultado"

    def test_no_se_cancela_lo_que_ya_termino(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.completar(tarea.id, "hecho")

        with pytest.raises(TransicionInvalida):
            gestor.cancelar(tarea.id)

    def test_iniciar_dos_veces_no_es_un_error(self, gestor):
        """No hay nada que hacer, pero tampoco nada que reprochar."""
        tarea = gestor.crear("Algo")
        gestor.iniciar(tarea.id)

        assert gestor.iniciar(tarea.id).estado == TaskState.RUNNING.value

    def test_fallar_lo_ya_terminado_no_altera_nada(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.completar(tarea.id, "salió bien")

        resultado = gestor.fallar(tarea.id, "un fallo tardío")

        assert resultado.estado == TaskState.COMPLETED.value
        assert resultado.error is None

    def test_una_tarea_inexistente_se_dice_claramente(self, gestor):
        with pytest.raises(TransicionInvalida, match="No existe"):
            gestor.iniciar("task-inventada")


class TestReintentar:
    def test_una_tarea_fallida_se_reintenta(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.fallar(tarea.id, "el proveedor no respondió")

        reintentada = gestor.reintentar(tarea.id)

        assert reintentada.estado == TaskState.RUNNING.value
        assert reintentada.intentos == 1
        assert reintentada.error is None

    def test_se_conservan_los_pasos_del_intento_anterior(self, gestor):
        """Borrarlos haría que el segundo intento repitiera los mismos errores
        sin saberlo."""
        tarea = gestor.crear("Algo")
        gestor.iniciar(tarea.id)
        gestor.avanzar(tarea.id, "Lo que se probó", error="y falló")
        gestor.fallar(tarea.id, "no salió")

        reintentada = gestor.reintentar(tarea.id)

        assert len(reintentada.pasos) == 1
        assert reintentada.pasos[0].error == "y falló"

    def test_una_completada_no_se_reintenta(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.completar(tarea.id, "hecho")

        with pytest.raises(TransicionInvalida, match="fallaron"):
            gestor.reintentar(tarea.id)

    def test_una_en_marcha_tampoco(self, gestor):
        """Habría dos ejecuciones de la misma tarea a la vez."""
        tarea = gestor.crear("Algo")
        gestor.iniciar(tarea.id)

        with pytest.raises(TransicionInvalida):
            gestor.reintentar(tarea.id)

    def test_hay_un_limite_de_intentos(self, gestor):
        """Una tarea que falla siempre debe acabar diciéndolo, no consumir la
        cuota del modelo indefinidamente."""
        tarea = gestor.crear("Algo imposible")

        for _ in range(MAX_INTENTOS):
            gestor.fallar(tarea.id, "otra vez no")
            gestor.reintentar(tarea.id)

        gestor.fallar(tarea.id, "y van...")
        with pytest.raises(TransicionInvalida, match="replantear"):
            gestor.reintentar(tarea.id)


class TestListarYPersistir:
    def test_solo_las_activas(self, gestor):
        viva = gestor.crear("En marcha")
        gestor.iniciar(viva.id)
        acabada = gestor.crear("Terminada")
        gestor.completar(acabada.id, "hecho")

        activas = gestor.listar(solo_activas=True)

        assert [t.id for t in activas] == [viva.id]

    def test_una_tarea_sobrevive_al_proceso(self, gestor):
        """Si Morgan se reinicia a mitad de un trabajo largo, lo hecho no debe
        perderse y el usuario tiene que poder ver en qué se quedó."""
        tarea = gestor.crear("Larga", pasos=["Uno", "Dos"])
        gestor.iniciar(tarea.id)
        gestor.avanzar(tarea.id, "Uno", resultado="hecho")

        otro_gestor = TaskManager(gestor.repositorio)
        recuperada = otro_gestor.obtener(tarea.id)

        assert recuperada.progreso == 0.5
        assert recuperada.pasos[0].resultado == "hecho"

    def test_eliminar(self, gestor):
        tarea = gestor.crear("Algo")

        assert gestor.eliminar(tarea.id) is True
        assert gestor.obtener(tarea.id) is None
        assert gestor.eliminar(tarea.id) is False


class TestEsperandoAlgoExterno:
    def test_una_tarea_puede_quedar_esperando(self, gestor):
        """`WAITING` no es un fallo: es una confirmación pendiente."""
        tarea = gestor.crear("Borrar unos archivos")
        gestor.iniciar(tarea.id)

        esperando = gestor.esperar(tarea.id, "Hace falta que confirmes el borrado")

        assert esperando.estado == TaskState.WAITING.value
        assert not TaskState(esperando.estado).terminal

    def test_y_luego_continuar(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.iniciar(tarea.id)
        gestor.esperar(tarea.id, "confirma")

        assert gestor.iniciar(tarea.id).estado == TaskState.RUNNING.value

    def test_esperando_cuenta_como_activa(self, gestor):
        tarea = gestor.crear("Algo")
        gestor.esperar(tarea.id, "confirma")

        assert [t.id for t in gestor.listar(solo_activas=True)] == [tarea.id]


# --- Herramientas -------------------------------------------------------------


class TestLasHerramientas:
    @staticmethod
    def _por_nombre(gestor):
        from src.tools.tareas import task_tools

        return {h.name: h for h in task_tools(gestor)}

    def test_ninguna_necesita_el_ordenador_del_usuario(self, gestor):
        """Operan sobre la base de datos: existen igual en la nube."""
        for herramienta in self._por_nombre(gestor).values():
            assert herramienta.requires_local is False, herramienta.name

    def test_no_hay_un_update_task_generico(self, gestor):
        """El plan lo listaba, pero un método que cambia cualquier campo permite
        escribir estados incoherentes. Cada transición válida tiene su nombre."""
        nombres = set(self._por_nombre(gestor))

        assert "update_task" not in nombres
        assert {"create_task", "complete_task", "fail_task"} <= nombres

    def test_tampoco_se_ofrece_advance_task(self, gestor):
        """El agente anota los pasos por su cuenta al ejecutar cada herramienta.
        Ofrecersela al modelo duplicaria cada paso y, sobre todo, los viajes al
        proveedor: con ella, un encargo de tres pasos tardaba 126 s y se cortaba."""
        assert "advance_task" not in set(self._por_nombre(gestor))

    def test_crear_y_consultar(self, gestor):
        herramientas = self._por_nombre(gestor)

        creada = herramientas["create_task"].execute(objetivo="Investigar", pasos=["Uno", "Dos"])
        assert creada["success"] is True

        consultada = herramientas["get_task"].execute(task_id=creada["data"]["id"])
        assert consultada["data"]["objetivo"] == "Investigar"

    def test_una_transicion_invalida_es_un_error_de_herramienta_no_una_excepcion(self, gestor):
        """Devolverlo así permite al modelo corregirse; lanzarlo abortaría el turno."""
        herramientas = self._por_nombre(gestor)
        creada = herramientas["create_task"].execute(objetivo="Algo")
        identificador = creada["data"]["id"]
        herramientas["complete_task"].execute(task_id=identificador, resultado="hecho")

        resultado = herramientas["complete_task"].execute(task_id=identificador, resultado="otra vez")

        assert resultado["success"] is False
        assert "completó" in resultado["error"]

    def test_todas_devuelven_el_contrato_habitual(self, gestor):
        for herramienta in self._por_nombre(gestor).values():
            resultado = herramienta.execute(task_id="inexistente", objetivo="x", descripcion="y",
                                            resultado="z", error="w")
            assert set(resultado.keys()) == {"success", "data", "error"}, herramienta.name

    def test_listar_solo_activas(self, gestor):
        herramientas = self._por_nombre(gestor)
        viva = herramientas["create_task"].execute(objetivo="En marcha")["data"]["id"]
        muerta = herramientas["create_task"].execute(objetivo="Terminada")["data"]["id"]
        herramientas["complete_task"].execute(task_id=muerta, resultado="ya")

        activas = herramientas["list_tasks"].execute(solo_activas=True)

        assert [t["id"] for t in activas["data"]["tasks"]] == [viva]


# --- API ----------------------------------------------------------------------


class TestLaAPIDeTareas:
    TOKEN = "secreto-de-prueba"

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import src.config as config

        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_API_TOKEN", self.TOKEN)
        config.reset_settings()
        from src.api.app import create_app

        yield TestClient(create_app())
        config.reset_settings()

    @property
    def cabecera(self):
        return {"Authorization": f"Bearer {self.TOKEN}"}

    def _crear(self, api, objetivo="Investigar", pasos=None):
        from src.api.dependencies import get_container

        return get_container().tasks.crear(objetivo, pasos=pasos or ["Uno", "Dos"])

    def test_exige_token(self, api):
        assert api.get("/tasks").status_code == 401

    def test_el_listado_trae_el_progreso_calculado(self, api):
        """La interfaz lo necesita en cada pintado; calcularlo allí duplicaría la
        regla en dos lenguajes."""
        from src.api.dependencies import get_container

        tarea = self._crear(api)
        gestor = get_container().tasks
        gestor.iniciar(tarea.id)
        gestor.avanzar(tarea.id, "Uno", resultado="ok")

        cuerpo = api.get("/tasks", headers=self.cabecera).json()

        assert cuerpo["tasks"][0]["progreso"] == 0.5
        assert cuerpo["tasks"][0]["paso_actual"] == "Dos"

    def test_cancelar_y_reintentar(self, api):
        tarea = self._crear(api)

        cancelada = api.post(f"/tasks/{tarea.id}/cancel", headers=self.cabecera)
        assert cancelada.json()["estado"] == "cancelled"

        reintentada = api.post(f"/tasks/{tarea.id}/retry", headers=self.cabecera)
        assert reintentada.json()["estado"] == "running"

    def test_una_transicion_imposible_da_409_y_no_400(self, api):
        """La petición es válida; es el estado actual el que no la admite."""
        tarea = self._crear(api)
        api.post(f"/tasks/{tarea.id}/cancel", headers=self.cabecera)

        repetida = api.post(f"/tasks/{tarea.id}/cancel", headers=self.cabecera)

        assert repetida.status_code == 409
        assert repetida.json()["error"]["code"] == "TASK_STATE_CONFLICT"

    def test_una_tarea_inexistente_da_404(self, api):
        assert api.get("/tasks/no-existe", headers=self.cabecera).status_code == 404
        assert api.post("/tasks/no-existe/retry", headers=self.cabecera).status_code == 404

    def test_no_se_pueden_crear_tareas_desde_la_api(self, api):
        """Crearlas y hacerlas avanzar es trabajo del agente: exponerlo aquí
        permitiría escribir estados que no corresponden a nada ejecutado."""
        assert api.post("/tasks", json={"objetivo": "x"}, headers=self.cabecera).status_code == 405
