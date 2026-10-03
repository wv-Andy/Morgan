"""
Solo viajan al modelo las herramientas que pueden usarse (4.2).

Con los adjuntos y el conocimiento de una cuenta vacíos, sus herramientas solo podían
contestar «no hay nada», y sus esquemas viajaban en cada llamada al modelo: ~750 tokens
de los ~3.400 fijos (medido). Es la misma regla que ya usaba el núcleo con las del PC y
las de una tarea abierta: se quita lo que **no puede** usarse, nunca lo que «quizá no haga
falta».
"""

import pytest

from src.identidad import como_usuario


@pytest.fixture
def subidas(tmp_path):
    from src.memory.db import Database
    from src.memory.sqlite_repositories import SQLiteRepositoryFactory
    from src.uploads.store import UploadStore
    from src.uploads.almacenamiento import AlmacenEnDisco

    repos = SQLiteRepositoryFactory(Database(tmp_path / "s.db"))
    return UploadStore(repositorio=repos.uploads, almacen=AlmacenEnDisco(tmp_path / "subidas"))


@pytest.fixture
def conocimiento(tmp_path):
    from src.conocimiento.almacen import AlmacenDeConocimiento
    from src.memory.db import Database
    from src.tools import conocimiento as modulo

    modulo._con_documentos().olvidar()
    yield AlmacenDeConocimiento(Database(tmp_path / "c.db"))
    modulo._con_documentos().olvidar()


class TestLosAdjuntos:
    def test_sin_archivos_no_se_ofrecen_y_al_subir_uno_si(self, subidas):
        from src.tools.multimodal import AnalyzeImageTool, ListUploadsTool, ReadUploadTool

        herramientas = [ListUploadsTool(subidas), ReadUploadTool(subidas), AnalyzeImageTool(subidas)]
        with como_usuario("usr-ana"):
            assert not any(h.disponible() for h in herramientas)
            subidas.guardar("notas.txt", b"hola")
            assert all(h.disponible() for h in herramientas)

    def test_son_de_cada_persona(self, subidas):
        from src.tools.multimodal import ReadUploadTool

        with como_usuario("usr-ana"):
            subidas.guardar("notas.txt", b"hola")
            assert ReadUploadTool(subidas).disponible()      # recordado para Ana…
        with como_usuario("usr-bea"):
            assert not ReadUploadTool(subidas).disponible()  # …y no para Bea

    def test_al_borrar_el_ultimo_dejan_de_ofrecerse(self, subidas):
        from src.tools.multimodal import ReadUploadTool

        with como_usuario("usr-ana"):
            archivo = subidas.guardar("notas.txt", b"hola")
            assert ReadUploadTool(subidas).disponible()
            subidas.eliminar(archivo.id)
            assert not ReadUploadTool(subidas).disponible()

    def test_se_recuerda_un_rato(self, subidas, monkeypatch):
        consultas = []
        original = subidas.repositorio.list

        def contando(*a, **k):
            consultas.append(1)
            return original(*a, **k)

        monkeypatch.setattr(subidas.repositorio, "list", contando)
        with como_usuario("usr-ana"):
            for _ in range(5):
                subidas.tiene_alguno()
        assert len(consultas) == 1


class TestElConocimiento:
    def test_sin_documentos_no_se_busca_y_al_guardar_uno_si(self, conocimiento):
        from src.tools.conocimiento import AddKnowledgeTool, RemoveKnowledgeTool, SearchKnowledgeTool

        buscar = SearchKnowledgeTool(conocimiento)
        with como_usuario("usr-ana"):
            assert not buscar.disponible() and not RemoveKnowledgeTool(conocimiento).disponible()
            assert AddKnowledgeTool(conocimiento).disponible(), "guardar el primero siempre se puede"
            r = AddKnowledgeTool(conocimiento).execute(titulo="Guía", contenido="Se hace así.")
            assert r["success"] and buscar.disponible()

    def test_al_borrar_el_ultimo_deja_de_ofrecerse(self, conocimiento):
        from src.tools.conocimiento import AddKnowledgeTool, RemoveKnowledgeTool, SearchKnowledgeTool

        with como_usuario("usr-ana"):
            doc = AddKnowledgeTool(conocimiento).execute(titulo="Guía", contenido="Se hace así.")["data"]
            assert SearchKnowledgeTool(conocimiento).disponible()
            RemoveKnowledgeTool(conocimiento).execute(documento_id=doc["id"])
            assert not SearchKnowledgeTool(conocimiento).disponible()

    def test_si_no_se_puede_saber_se_ofrece(self):
        from src.tools.conocimiento import SearchKnowledgeTool

        class Caido:
            def titulos(self, limite):
                raise RuntimeError("base caída")

        with como_usuario("usr-ana"):
            assert SearchKnowledgeTool(Caido()).disponible()


class TestEnElTurno:
    def test_una_cuenta_nueva_no_recibe_esos_esquemas(self, subidas, conocimiento):
        from src.agent.core import Agent
        from src.agent.sessions import SessionStore
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.conocimiento import knowledge_tools
        from src.tools.multimodal import AnalyzeImageTool, ReadUploadTool
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        for h in [*knowledge_tools(conocimiento, subidas), ReadUploadTool(subidas), AnalyzeImageTool(subidas)]:
            registro.register(h)
        agente = Agent(model=MockLLMProvider(), tool_registry=registro,
                       permission_manager=PermissionManager(interactive=False), sessions=SessionStore())
        with como_usuario("usr-nueva"):
            nombres = {e["name"] for e in agente._schemas_for(agente.sessions.get("s"))}
        assert nombres == {"add_knowledge"}


class TestSiLaPideIgualSeDiceElMotivoDeVerdad:
    """Antes, pedir una que no se ofrece daba siempre «el PC de la persona no está
    conectado»: también sin adjuntos, sin documentos o sin GitHub, y Morgan se lo habría
    dicho a la persona."""

    def _pedir(self, herramienta, nombre, argumentos):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(herramienta)
        modelo = MockLLMProvider()
        modelo.queue_tool_call(nombre, argumentos)
        modelo.queue_text("vale")
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False))
        with como_usuario("usr-nueva"):
            agente.chat("hazlo", session_id="s")
        return [m.tool_result for m in agente.sessions.get("s").messages if m.role == "tool"][0]["error"]

    def test_un_adjunto_que_no_hay(self, subidas):
        from src.tools.multimodal import ReadUploadTool

        error = self._pedir(ReadUploadTool(subidas), "read_upload", {"upload_id": "x"})
        assert "ningún archivo subido" in error and "PC" not in error

    def test_un_documento_que_no_hay(self, conocimiento):
        from src.tools.conocimiento import SearchKnowledgeTool

        error = self._pedir(SearchKnowledgeTool(conocimiento), "search_knowledge", {"consulta": "x"})
        assert "ningún documento" in error and "PC" not in error

    def test_las_del_pc_siguen_diciendo_lo_del_pc(self):
        from src.canal.herramientas import HerramientaDelEquipo
        from src.tools.filesystem import ListFilesTool

        error = self._pedir(HerramientaDelEquipo(ListFilesTool()), "list_files", {})
        assert "PC de la persona no está conectado" in error


class TestUnaSolaConsultaDeTitulos:
    """4.3: el índice del turno (4.1) y la disponibilidad (4.2) leían los títulos por
    separado, y el índice en **cada** turno. Ahora es una consulta, recordada un rato."""

    def test_indice_y_disponibilidad_comparten_la_consulta(self):
        from src.tools.conocimiento import SearchKnowledgeTool, indice_para_el_turno

        class Contando:
            def __init__(self):
                self.veces = 0

            def titulos(self, limite):
                self.veces += 1
                return ["Guía"]

        almacen = Contando()
        with como_usuario("usr-ana"):
            for _ in range(3):
                assert "«Guía»" in indice_para_el_turno(almacen)
                assert SearchKnowledgeTool(almacen).disponible()
        assert almacen.veces == 1


class TestConElPcEmparejadoPeroDesconectado:
    """4.3, medido con el modelo real: con el agente sin responder, Morgan contestó «no tengo
    acceso al sistema de archivos de tu ordenador» y pidió copiar la lista a mano."""

    def _servicio(self, agentes):
        class Servicio:
            def __init__(self):
                self.veces = 0

            def listar(self, user_id):
                self.veces += 1
                return agentes

        return Servicio()

    def test_con_un_pc_emparejado_se_avisa_y_se_recuerda(self):
        from src.identidad.agentes import AVISO_PC_DESCONECTADO, aviso_si_esta_desconectado

        servicio = self._servicio([{"id": "agt-1"}])
        with como_usuario("usr-ana"):
            assert aviso_si_esta_desconectado(servicio) == AVISO_PC_DESCONECTADO
            aviso_si_esta_desconectado(servicio)
        assert servicio.veces == 1

    def test_sin_pc_emparejado_nada(self):
        from src.identidad.agentes import aviso_si_esta_desconectado

        with como_usuario("usr-bea"):
            assert aviso_si_esta_desconectado(self._servicio([])) == ""

    def test_va_en_el_turno_solo_si_no_hay_ninguna_del_pc_disponible(self):
        from src.agent.core import Agent
        from src.canal.herramientas import HerramientaDelEquipo
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.filesystem import ListFilesTool
        from src.tools.registry import ToolRegistry

        pc = HerramientaDelEquipo(ListFilesTool())
        registro = ToolRegistry()
        registro.register(pc)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False))
        agente.aviso_del_equipo = lambda: "AVISO-DEL-EQUIPO"

        agente.chat("hola", session_id="a")
        assert "AVISO-DEL-EQUIPO" in modelo.prompts[-1], "PC desconectado: se avisa"

        pc.disponible = lambda: True
        agente.chat("hola", session_id="b")
        assert "AVISO-DEL-EQUIPO" not in modelo.prompts[-1], "PC conectado: no"


    def test_el_aviso_sustituye_el_no_tienes_acceso(self):
        """4.4, medido: con los dos juntos, el modelo seguía diciendo «no tengo acceso»."""
        from src.agent.core import Agent
        from src.agent.prompt import PARRAFO_SIN_ACCESO
        from src.canal.herramientas import HerramientaDelEquipo
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.filesystem import ListFilesTool
        from src.tools.registry import ToolRegistry
        from src.tools.web import SearchWebTool

        registro = ToolRegistry()
        registro.register(HerramientaDelEquipo(ListFilesTool()))
        registro.register(SearchWebTool())
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False))
        agente.aviso_del_equipo = lambda: "AVISO-DEL-EQUIPO"
        agente.chat("hola", session_id="a")
        assert "AVISO-DEL-EQUIPO" in modelo.prompts[-1]
        assert PARRAFO_SIN_ACCESO not in modelo.prompts[-1]


class TestLoOfrecidoSeMantieneEnElTurno:
    """4.5, medido en producción: el PC se desconectó a mitad de un turno, `list_files`
    desapareció de la llamada siguiente, el modelo la pidió igual y Groq rechazó la
    petición entera («attempted to call tool 'list_files' which was not in request.tools»)."""

    def test_si_deja_de_estar_disponible_a_mitad_sigue_ofreciendose(self):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.base import Tool
        from src.tools.registry import ToolRegistry

        class DelPc(Tool):
            conectado = True
            name = "list_files"
            description = "lista"
            category = "filesystem"
            permission_level = "safe"
            parameters = {"type": "object", "properties": {}}

            def disponible(self):
                return self.conectado

            def execute(self, **kwargs):
                DelPc.conectado = False  # se desconecta mientras lo hace
                return {"success": False, "data": None, "error": "Tu equipo se desconectó."}

        class Anotando(MockLLMProvider):
            def __init__(self):
                super().__init__()
                self.ofrecidas = []

            def generate(self, messages, tools=None, system_prompt=None, **kw):
                self.ofrecidas.append({t["name"] for t in tools or []})
                return super().generate(messages, tools=tools, system_prompt=system_prompt, **kw)

        registro = ToolRegistry()
        registro.register(DelPc())
        modelo = Anotando()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False))
        modelo.queue_tool_call("list_files", {})
        modelo.queue_text("Tu equipo se desconectó.")
        agente.chat("¿qué hay en mi escritorio?", session_id="s")
        assert modelo.ofrecidas == [{"list_files"}, {"list_files"}]

        # En el turno siguiente, ya desconectado, no se ofrece.
        modelo.queue_text("Tu PC no está conectado ahora.")
        agente.chat("otra vez", session_id="s")
        assert "list_files" not in modelo.ofrecidas[-1]
