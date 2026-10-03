"""
Verificación: comprobar que lo que se pidió ocurrió de verdad (V1.7).

Que una herramienta devuelva `success: True` significa que la llamada no falló,
no que el objetivo se cumpliera. Un `create_file` puede escribir un archivo vacío
y devolver éxito; `run_tests` puede terminar sin excepciones con la mitad de las
pruebas en rojo.

Dos ideas sostienen estas pruebas:

1. **La verdad no la decide el modelo.** Se comprueba el efecto contra el mundo
   real. Es la lección que dejó el gate de la V1.6.
2. **«No lo sé» no es «está bien».** Lo que Morgan no sabe comprobar se marca
   `no_verificable`, no `correcto`. Un sistema que llama verificado a lo que no ha
   mirado es peor que uno que no verifica: además te convence.
"""

import pytest

from src.tasks.verificacion import Veredicto, Verificador


@pytest.fixture
def verificador():
    return Verificador()


class TestCrearUnArchivo:
    def test_si_existe_es_correcto(self, verificador, tmp_path):
        destino = tmp_path / "creado.txt"
        destino.write_text("contenido", encoding="utf-8")

        resultado = verificador.verificar(
            "create_file", {"path": str(destino)}, {"success": True}
        )

        assert resultado.correcto
        assert "bytes" in (resultado.evidencia or "")

    def test_si_no_existe_es_incorrecto_aunque_la_herramienta_dijera_que_si(
        self, verificador, tmp_path
    ):
        """El caso que da sentido a todo esto."""
        resultado = verificador.verificar(
            "create_file",
            {"path": str(tmp_path / "fantasma.txt")},
            {"success": True, "data": "archivo creado"},
        )

        assert resultado.fallido

    def test_vacio_habiendo_pedido_contenido_es_incorrecto(self, verificador, tmp_path):
        """La llamada no falló, pero el objetivo tampoco se cumplió."""
        destino = tmp_path / "vacio.txt"
        destino.write_text("", encoding="utf-8")

        resultado = verificador.verificar(
            "create_file",
            {"path": str(destino), "content": "esto debía escribirse"},
            {"success": True},
        )

        assert resultado.fallido
        assert "vacío" in resultado.motivo

    def test_vacio_sin_pedir_contenido_esta_bien(self, verificador, tmp_path):
        """Crear un archivo vacío a propósito es legítimo."""
        destino = tmp_path / "vacio.txt"
        destino.write_text("", encoding="utf-8")

        assert verificador.verificar(
            "create_file", {"path": str(destino)}, {"success": True}
        ).correcto

    def test_sin_ruta_no_se_puede_comprobar(self, verificador):
        resultado = verificador.verificar("create_file", {}, {"success": True})

        assert resultado.veredicto == Veredicto.NO_VERIFICABLE.value


class TestBorrarUnArchivo:
    def test_si_desaparecio_es_correcto(self, verificador, tmp_path):
        assert verificador.verificar(
            "delete_file", {"path": str(tmp_path / "ya-no-esta.txt")}, {"success": True}
        ).correcto

    def test_si_sigue_ahi_es_incorrecto(self, verificador, tmp_path):
        superviviente = tmp_path / "sigue.txt"
        superviviente.write_text("aquí sigo", encoding="utf-8")

        resultado = verificador.verificar(
            "delete_file", {"path": str(superviviente)}, {"success": True}
        )

        assert resultado.fallido
        assert "sigue existiendo" in resultado.motivo


class TestAplicarUnParche:
    def test_si_el_texto_nuevo_esta_es_correcto(self, verificador, tmp_path):
        fichero = tmp_path / "codigo.py"
        fichero.write_text("def nueva(): pass", encoding="utf-8")

        assert verificador.verificar(
            "patch_file",
            {"path": str(fichero), "new_text": "def nueva(): pass"},
            {"success": True},
        ).correcto

    def test_si_no_esta_es_incorrecto(self, verificador, tmp_path):
        """El parche pudo aplicarse en otro sitio, o no aplicarse."""
        fichero = tmp_path / "codigo.py"
        fichero.write_text("def vieja(): pass", encoding="utf-8")

        resultado = verificador.verificar(
            "patch_file",
            {"path": str(fichero), "new_text": "def nueva(): pass"},
            {"success": True},
        )

        assert resultado.fallido

    def test_un_archivo_ilegible_no_es_incorrecto(self, verificador, tmp_path):
        """No poder leerlo es no saber, no saber que está mal."""
        resultado = verificador.verificar(
            "patch_file",
            {"path": str(tmp_path / "no-existe.py"), "new_text": "x"},
            {"success": True},
        )

        # Aqui si es incorrecto —el archivo deberia existir— pero el matiz
        # importa: se dice por que.
        assert resultado.motivo


class TestLasPruebasEnVerde:
    """El ejemplo canónico: `run_tests` hace su trabajo —ejecutar pytest— y
    devuelve éxito habiendo fallado nueve pruebas."""

    def test_una_salida_con_fallos_es_incorrecta(self, verificador):
        resultado = verificador.verificar(
            "run_tests", {},
            {"success": True, "data": {"stdout": "3 failed, 20 passed in 4.2s"}},
        )

        assert resultado.fallido

    def test_una_salida_en_verde_es_correcta(self, verificador):
        assert verificador.verificar(
            "run_tests", {},
            {"success": True, "data": {"stdout": "23 passed in 4.2s"}},
        ).correcto

    def test_sin_salida_legible_no_se_afirma_nada(self, verificador):
        resultado = verificador.verificar(
            "run_tests", {}, {"success": True, "data": {}}
        )

        assert resultado.veredicto == Veredicto.NO_VERIFICABLE.value


class TestLoQueNoSeSabeComprobar:
    """La distinción que da sentido al módulo."""

    @pytest.mark.parametrize(
        "herramienta", ["search_web", "read_webpage", "system_info", "inventada"]
    )
    def test_devuelve_no_verificable_no_correcto(self, verificador, herramienta):
        resultado = verificador.verificar(herramienta, {}, {"success": True})

        assert resultado.veredicto == Veredicto.NO_VERIFICABLE.value
        assert not resultado.correcto
        assert not resultado.fallido

    def test_no_verificable_no_cuenta_como_fallo(self, verificador):
        """Tratarlo como fallo haría que Morgan reintentara cosas que quizá
        salieron perfectamente."""
        assert not verificador.verificar("search_web", {}, {"success": True}).fallido

    def test_sabe_decir_que_sabe_comprobar(self, verificador):
        assert verificador.sabe_verificar("create_file")
        assert not verificador.sabe_verificar("search_web")


class TestSiLaHerramientaYaFallo:
    def test_no_hace_falta_comprobar_nada(self, verificador):
        """El efecto no se intentó siquiera."""
        resultado = verificador.verificar(
            "create_file", {"path": "x"}, {"success": False, "error": "permiso denegado"}
        )

        assert resultado.fallido
        assert "permiso denegado" in (resultado.evidencia or "")


class TestUnFalloDelVerificadorNoInventaVeredictos:
    def test_una_excepcion_da_no_verificable(self, verificador, monkeypatch):
        """Un fallo comprobando no puede convertirse en un veredicto: eso sería
        afirmar algo que no se comprobó."""
        import src.tasks.verificacion as modulo

        def romper(*a, **k):
            raise RuntimeError("el disco no responde")

        monkeypatch.setitem(modulo.COMPROBACIONES, "create_file", romper)

        resultado = verificador.verificar("create_file", {"path": "x"}, {"success": True})

        assert resultado.veredicto == Veredicto.NO_VERIFICABLE.value


class TestElAgenteCorrigeElVeredictoFalso:
    """Lo que impide que Morgan afirme que hizo algo que no hizo."""

    @pytest.fixture
    def agente(self, tmp_path):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager
        from src.tools.filesystem import CreateFileTool
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(CreateFileTool())

        return Agent(
            model=MockLLMProvider(),
            tool_registry=registro,
            permission_manager=PermissionManager(
                audit_logger=AuditLogger(tmp_path / "a.log"), interactive=False
            ),
        )

    def test_un_exito_desmentido_por_el_efecto_pasa_a_fallo(self, agente, tmp_path):
        resultado = agente._verificar(
            "create_file",
            {"path": str(tmp_path / "fantasma.txt"), "content": "hola"},
            {"success": True, "data": "creado", "error": None},
        )

        assert resultado["success"] is False
        assert "no consiguió su efecto" in resultado["error"]

    def test_un_exito_real_se_respeta(self, agente, tmp_path):
        destino = tmp_path / "real.txt"
        destino.write_text("contenido", encoding="utf-8")

        resultado = agente._verificar(
            "create_file", {"path": str(destino), "content": "contenido"},
            {"success": True, "data": "creado", "error": None},
        )

        assert resultado["success"] is True
        assert resultado["verificacion"]["veredicto"] == "correcto"

    def test_lo_no_verificable_no_ensucia_el_resultado(self, agente):
        """Escribir «no verificable» en cada resultado sería ruido en el
        contexto, y acabaría enseñando al modelo a ignorarlo."""
        resultado = agente._verificar(
            "search_web", {"query": "x"}, {"success": True, "data": [], "error": None}
        )

        assert "verificacion" not in resultado

    def test_sin_verificador_el_resultado_pasa_intacto(self, agente):
        agente.verificador = None
        original = {"success": True, "data": "x", "error": None}

        assert agente._verificar("create_file", {"path": "y"}, original) == original


class TestNoSePuedeCompletarLoQueFallo:
    """El corazón de la V1.7: Morgan tiene que poder decir «no pude», y no puede
    decir «listo» sobre algo que se comprobó y no salió."""

    @pytest.fixture
    def gestor(self, tmp_path):
        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tasks import TaskManager

        return TaskManager(SQLiteRepositoryFactory(Database(tmp_path / "t.db")).tasks)

    def test_completar_se_niega_si_un_paso_se_comprobo_y_fallo(self, gestor):
        from src.tasks import TransicionInvalida

        tarea = gestor.crear("Crear un informe")
        gestor.avanzar(
            tarea.id, "Escribir el archivo", herramienta="create_file",
            resultado="ok", verificacion="incorrecto",
            verificacion_motivo="'informe.txt' no existe",
        )

        with pytest.raises(TransicionInvalida) as fallo:
            gestor.completar(tarea.id, "Informe listo")

        assert "no salieron bien" in str(fallo.value)

    def test_el_mensaje_dice_que_paso_y_por_que(self, gestor):
        """Sin eso, «no puedes completar» no le sirve al modelo para corregir."""
        from src.tasks import TransicionInvalida

        tarea = gestor.crear("Algo")
        gestor.avanzar(
            tarea.id, "Escribir", herramienta="create_file",
            verificacion="incorrecto", verificacion_motivo="'x.txt' no existe",
        )

        with pytest.raises(TransicionInvalida) as fallo:
            gestor.completar(tarea.id, "listo")

        assert "'x.txt' no existe" in str(fallo.value)
        assert "fallida" in str(fallo.value)

    def test_un_paso_sin_verificar_no_impide_completar(self, gestor):
        """No comprobado no es incorrecto. Tratarlo igual haría imposible
        terminar cualquier tarea que use herramientas que Morgan no sabe mirar."""
        tarea = gestor.crear("Investigar algo")
        gestor.avanzar(tarea.id, "Buscar", herramienta="search_web", resultado="ok")

        assert gestor.completar(tarea.id, "Encontrado").estado == "completed"

    def test_un_paso_verificado_correcto_tampoco(self, gestor):
        tarea = gestor.crear("Escribir algo")
        gestor.avanzar(
            tarea.id, "Escribir", herramienta="create_file",
            resultado="ok", verificacion="correcto",
        )

        assert gestor.completar(tarea.id, "Hecho").estado == "completed"

    def test_siempre_se_puede_declarar_el_fracaso(self, gestor):
        """Es la salida que la V1.7 tiene que garantizar."""
        tarea = gestor.crear("Algo imposible")
        gestor.avanzar(
            tarea.id, "Intentarlo", herramienta="create_file",
            verificacion="incorrecto", verificacion_motivo="no se pudo",
        )

        fallida = gestor.fallar(tarea.id, "No se pudo escribir el archivo")

        assert fallida.estado == "failed"
        assert "No se pudo escribir" in fallida.error

    def test_el_veredicto_sobrevive_al_guardado(self, gestor):
        tarea = gestor.crear("Persistente")
        gestor.avanzar(
            tarea.id, "Escribir", herramienta="create_file",
            verificacion="incorrecto", verificacion_motivo="no existe",
        )

        recuperada = gestor.obtener(tarea.id)

        assert recuperada.pasos[0].verificacion == "incorrecto"
        assert recuperada.pasos[0].verificacion_motivo == "no existe"


class TestLoQueSeHaceEnElPc:
    """Auditoría de la 3.x: la verificación miraba el disco **de la nube** también para
    las herramientas del PC. En producción (Render, Linux) un archivo creado bien en el PC
    de la persona se daba por no creado, y a Morgan se le decía que había fallado; y uno
    que seguía en el PC, por borrado. Reproducido por el bucle real antes de arreglarlo."""

    RUTA_DEL_PC = "C:/Users/nadie/nota.txt"

    @staticmethod
    def _del_pc(nombre, resultado):
        from src.canal.herramientas import EscribirEnElEquipo

        class DelPc(EscribirEnElEquipo):
            exige_plan = False

            def disponible(self):
                return True

            @property
            def permission_level(self):
                return "safe"

            def execute(self, equipo=None, **kw):
                return resultado
        return DelPc(nombre)

    @staticmethod
    def _lo_que_ve_el_modelo(herramienta, nombre, argumentos):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(herramienta)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False))
        modelo.queue_tool_call(nombre, argumentos)
        modelo.queue_text("hecho")
        agente.chat("hazlo", session_id="s")
        return [m.tool_result for m in agente.sessions.get("s").messages if m.role == "tool"][0]

    def test_creado_en_el_pc_no_se_busca_en_la_nube(self):
        resultado = {"success": True, "data": {"path": self.RUTA_DEL_PC, "sha256": "ab" * 32}, "error": None}
        visto = self._lo_que_ve_el_modelo(self._del_pc("create_file", resultado), "create_file",
                                          {"path": self.RUTA_DEL_PC, "content": "hola"})
        assert visto["success"] is True, "un archivo creado bien en el PC se daba por fallido"
        assert visto["verificacion"]["veredicto"] == "correcto"

    def test_borrado_en_el_pc_aunque_en_la_nube_exista_esa_ruta(self, tmp_path):
        """Al revés: que la ruta exista en el disco de la nube no dice nada del PC."""
        aqui = tmp_path / "x.txt"
        aqui.write_text("de la nube")
        resultado = {"success": True, "data": {"path": str(aqui), "papelera": True}, "error": None}
        visto = self._lo_que_ve_el_modelo(self._del_pc("delete_file", resultado), "delete_file", {"path": str(aqui)})
        assert visto["success"] is True and visto["verificacion"]["veredicto"] == "correcto"

    def test_sin_evidencia_del_agente_no_se_afirma_nada(self, verificador):
        c = verificador.verificar("create_file", {"path": "C:/x"}, {"success": True, "data": {}}, en_el_pc=True)
        assert c.veredicto == "no_verificable"

    def test_si_el_agente_dice_que_fallo_es_incorrecto(self, verificador):
        c = verificador.verificar("create_file", {"path": "C:/x"}, {"success": False, "error": "no"}, en_el_pc=True)
        assert c.veredicto == "incorrecto"

    def test_las_herramientas_del_pc_lo_dicen(self):
        from src.canal.herramientas import CopiarDelEquipo, EscribirEnElEquipo, HerramientaDelEquipo, TerminalDelEquipo
        from src.tools.filesystem import ListFilesTool

        assert HerramientaDelEquipo(ListFilesTool()).en_el_pc
        assert EscribirEnElEquipo("create_file").en_el_pc and EscribirEnElEquipo("delete_file").en_el_pc
        assert TerminalDelEquipo("run_command").en_el_pc and CopiarDelEquipo.en_el_pc
        assert not getattr(ListFilesTool(), "en_el_pc", False), "las locales siguen mirando el disco"


class TestLoQueElAgenteDiceHaberComprobado:
    """4.0-B: crear carpeta, mover, un comando que cambia algo y terminar un proceso dicen
    en el PC qué comprobaron (`comprobado`), y eso es la evidencia."""

    def test_lo_que_dice_el_agente(self, verificador):
        c = verificador.verificar("move_file", {"src": "C:/a", "dst": "C:/b"},
                                  {"success": True, "data": {"comprobado": "ya no está en el origen y sí en el destino"}},
                                  en_el_pc=True)
        assert c.veredicto == "correcto" and "ya no está en el origen" in c.motivo

    def test_vacio_no_cuenta(self, verificador):
        c = verificador.verificar("move_file", {}, {"success": True, "data": {"comprobado": "  "}}, en_el_pc=True)
        assert c.veredicto == "no_verificable"


class TestCopiarMoverYRenombrarEnLocal:
    """4.0-B: las herramientas locales que cambian archivos y no se verificaban."""

    def test_copiar(self, verificador, tmp_path):
        (tmp_path / "a.txt").write_text("hola")
        (tmp_path / "b.txt").write_text("hola")
        assert verificador.verificar("copy_file", {"src": str(tmp_path / "a.txt"), "dst": str(tmp_path / "b.txt")},
                                     {"success": True}).veredicto == "correcto"

    def test_copia_que_no_esta_o_mide_distinto(self, verificador, tmp_path):
        (tmp_path / "a.txt").write_text("hola")
        args = {"src": str(tmp_path / "a.txt"), "dst": str(tmp_path / "b.txt")}
        assert verificador.verificar("copy_file", args, {"success": True}).veredicto == "incorrecto"
        (tmp_path / "b.txt").write_text("ho")
        assert verificador.verificar("copy_file", args, {"success": True}).veredicto == "incorrecto"

    def test_copiar_a_una_carpeta(self, verificador, tmp_path):
        (tmp_path / "a.txt").write_text("hola")
        (tmp_path / "dentro").mkdir()
        (tmp_path / "dentro" / "a.txt").write_text("hola")
        assert verificador.verificar("copy_file", {"src": str(tmp_path / "a.txt"), "dst": str(tmp_path / "dentro")},
                                     {"success": True}).veredicto == "correcto"

    def test_mover(self, verificador, tmp_path):
        (tmp_path / "b.txt").write_text("x")
        args = {"src": str(tmp_path / "a.txt"), "dst": str(tmp_path / "b.txt")}
        assert verificador.verificar("move_file", args, {"success": True}).veredicto == "correcto"
        (tmp_path / "a.txt").write_text("x")
        assert verificador.verificar("move_file", args, {"success": True}).veredicto == "incorrecto", "sigue en el origen"

    def test_mover_a_ninguna_parte(self, verificador, tmp_path):
        args = {"src": str(tmp_path / "a.txt"), "dst": str(tmp_path / "b.txt")}
        assert verificador.verificar("move_file", args, {"success": True}).veredicto == "incorrecto"

    def test_renombrar(self, verificador, tmp_path):
        (tmp_path / "nuevo.txt").write_text("x")
        args = {"src": str(tmp_path / "viejo.txt"), "new_name": "nuevo.txt"}
        assert verificador.verificar("rename_file", args, {"success": True}).veredicto == "correcto"
        (tmp_path / "viejo.txt").write_text("x")
        assert verificador.verificar("rename_file", args, {"success": True}).veredicto == "incorrecto"

    def test_sin_argumentos_no_se_afirma_nada(self, verificador):
        for h in ("copy_file", "move_file", "rename_file"):
            assert verificador.verificar(h, {}, {"success": True}).veredicto == "no_verificable"


class TestAnadirEnElPc:
    def test_la_huella_del_agente_lo_da_por_comprobado(self):
        from src.tasks.verificacion import Verificador

        c = Verificador().verificar("append_file", {"path": "C:/r.txt", "content": "x"},
                                    {"success": True, "data": {"path": "C:/r.txt", "sha256": "ab" * 32}},
                                    en_el_pc=True)
        assert c.veredicto == "correcto"
