"""
Espacios de trabajo (V2.2): lo que tiene que cumplir la capa de datos y el agente.

Un espacio agrupa conversaciones, archivos y documentos de conocimiento, y tiene
instrucciones propias. La memoria sobre la persona no pertenece a ningún espacio.

## Lo que no se puede perder, y cada clase de aquí fija una cosa

1. **Un espacio de otra persona no existe.** Ni listándolo, ni pidiéndolo por su
   identificador, ni editándolo, ni borrándolo.
2. **Dentro de un espacio solo se ve lo suyo**, y en «General» solo lo que no está
   en ninguno. Nada cruza de un proyecto a otro.
3. **El cupo de archivos cuenta todos los espacios.** Si contara solo el actual,
   bastaría con repartir archivos para saltarse el límite.
4. **Borrar un espacio no borra nada de dentro**: vuelve a «General».
5. **Las instrucciones entran solo en el prompt de su turno.** El agente es uno
   para todo el mundo, y escribirlas en su prompt compartido las colaría en el
   turno de otra persona.
"""

import time

import pytest

from src.espacios import (
    MAX_INSTRUCCIONES,
    EspacioDuplicado,
    EspacioInvalido,
    en_espacio,
    espacio_actual,
)
from src.espacios.modelos import Espacio, bloque_para_el_prompt
from src.espacios.repositorio import RepositorioDeEspaciosSQLite
from src.identidad import como_usuario
from src.memory.db import Database
from src.memory.models import Upload
from src.memory.repositories import CUALQUIER_ESPACIO
from src.memory.sqlite_repositories import SQLiteRepositoryFactory

ANA = "user-ana"
BEA = "user-bea"


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "espacios.db")


@pytest.fixture
def repos(db):
    return SQLiteRepositoryFactory(db)


@pytest.fixture
def espacios(db):
    return RepositorioDeEspaciosSQLite(db)


def _upload(identificador: str) -> Upload:
    return Upload(
        id=identificador, nombre_original=f"{identificador}.txt", mime="text/plain",
        familia="texto", tamano=10, creado_en=time.time(),
    )


class TestElContexto:
    def test_por_defecto_es_general(self):
        assert espacio_actual() is None

    def test_se_restaura_aunque_algo_falle(self):
        """Un espacio que se quedara puesto tras una excepción haría que la
        siguiente petición de ese hilo trabajara en el proyecto equivocado."""
        with pytest.raises(RuntimeError):
            with en_espacio("esp-1"):
                assert espacio_actual() == "esp-1"
                raise RuntimeError("algo salió mal")

        assert espacio_actual() is None

    def test_una_cadena_vacia_es_general(self):
        """Es lo que manda un formulario vacío. Convertirla en un espacio
        llamado «» sería inventarse uno."""
        with en_espacio("   "):
            assert espacio_actual() is None


class TestElRepositorio:
    def test_crear_y_listar(self, espacios):
        with como_usuario(ANA):
            espacios.crear("Tesis", "Cita en formato APA.")
            espacios.crear("Casa")

            nombres = [e.nombre for e in espacios.listar()]

        assert nombres == ["Casa", "Tesis"]

    def test_el_nombre_se_limpia(self, espacios):
        with como_usuario(ANA):
            espacio = espacios.crear("   Mi   proyecto  ")

        assert espacio.nombre == "Mi proyecto"

    def test_dos_con_el_mismo_nombre_no(self, espacios):
        """Dos iguales en la barra lateral no se distinguen."""
        with como_usuario(ANA):
            espacios.crear("Tesis")
            with pytest.raises(EspacioDuplicado):
                espacios.crear("Tesis")

    @pytest.mark.parametrize("nombre", ["", "   ", "x" * 61])
    def test_un_nombre_imposible_se_rechaza(self, espacios, nombre):
        with como_usuario(ANA):
            with pytest.raises(EspacioInvalido):
                espacios.crear(nombre)

    def test_las_instrucciones_tienen_tope(self, espacios):
        """Van en cada llamada al modelo del turno: el tope es de coste."""
        with como_usuario(ANA):
            with pytest.raises(EspacioInvalido, match="cuota"):
                espacios.crear("Largo", "x" * (MAX_INSTRUCCIONES + 1))

    def test_actualizar(self, espacios):
        with como_usuario(ANA):
            espacio = espacios.crear("Tesis")
            cambiado = espacios.actualizar(
                espacio.id, nombre="Tesis doctoral", instrucciones="Sé breve.",
            )
            releido = espacios.obtener(espacio.id)

        assert cambiado.nombre == releido.nombre == "Tesis doctoral"
        assert releido.instrucciones == "Sé breve."

    def test_renombrar_al_nombre_de_otro_no(self, espacios):
        with como_usuario(ANA):
            espacios.crear("Tesis")
            casa = espacios.crear("Casa")
            with pytest.raises(EspacioDuplicado):
                espacios.actualizar(casa.id, nombre="Tesis")

    def test_renombrar_a_su_propio_nombre_si(self, espacios):
        """Guardar sin cambiar el nombre no puede chocar consigo mismo."""
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            assert espacios.actualizar(tesis.id, nombre="Tesis").nombre == "Tesis"

    def test_uno_que_no_existe_es_none(self, espacios):
        with como_usuario(ANA):
            assert espacios.obtener("esp-inventado") is None
            assert espacios.actualizar("esp-inventado", nombre="x") is None
            assert espacios.eliminar("esp-inventado") is False


class TestUnEspacioDeOtraPersonaNoExiste:
    def test_no_se_lista(self, espacios):
        with como_usuario(ANA):
            espacios.crear("Lo de Ana")
        with como_usuario(BEA):
            assert espacios.listar() == []

    def test_no_se_obtiene_ni_se_edita_ni_se_borra(self, espacios):
        with como_usuario(ANA):
            de_ana = espacios.crear("Lo de Ana", "privado")

        with como_usuario(BEA):
            assert espacios.obtener(de_ana.id) is None
            assert espacios.actualizar(de_ana.id, instrucciones="hackeado") is None
            assert espacios.eliminar(de_ana.id) is False

        with como_usuario(ANA):
            intacto = espacios.obtener(de_ana.id)
        assert intacto is not None and intacto.instrucciones == "privado"

    def test_dos_personas_pueden_usar_el_mismo_nombre(self, espacios):
        """El nombre es único por persona, no en todo Morgan."""
        with como_usuario(ANA):
            espacios.crear("Trabajo")
        with como_usuario(BEA):
            espacios.crear("Trabajo")


class TestLasConversaciones:
    def test_nacen_en_su_espacio_y_se_listan_por_espacio(self, repos, espacios):
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            repos.sessions.create("s-general")
            repos.sessions.create("s-tesis", espacio_id=tesis.id)

            general = [s.id for s in repos.sessions.list(espacio=None)]
            de_tesis = [s.id for s in repos.sessions.list(espacio=tesis.id)]
            todas = {s.id for s in repos.sessions.list(espacio=CUALQUIER_ESPACIO)}

        assert general == ["s-general"]
        assert de_tesis == ["s-tesis"]
        assert todas == {"s-general", "s-tesis"}

    def test_volver_a_crearla_no_la_cambia_de_espacio(self, repos, espacios):
        """Moverla es una decisión explícita. Si pedirla otra vez desde otro
        espacio la moviera, abrirla desde el sitio equivocado la perdería."""
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            repos.sessions.create("s1", espacio_id=tesis.id)
            repos.sessions.create("s1", espacio_id=None)

            assert repos.sessions.get("s1").espacio_id == tesis.id

    def test_moverla_y_devolverla_a_general(self, repos, espacios):
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            repos.sessions.create("s1")

            repos.sessions.update("s1", espacio_id=tesis.id)
            assert repos.sessions.get("s1").espacio_id == tesis.id

            repos.sessions.update("s1", espacio_id="")
            assert repos.sessions.get("s1").espacio_id is None


class TestLosArchivos:
    def test_cada_espacio_ve_solo_los_suyos(self, repos, espacios):
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            with en_espacio(tesis.id):
                repos.uploads.add(_upload("up-tesis"))
            repos.uploads.add(_upload("up-general"))

            assert [u.id for u in repos.uploads.list()] == ["up-general"]
            with en_espacio(tesis.id):
                assert [u.id for u in repos.uploads.list()] == ["up-tesis"]

    def test_pedirlo_desde_otro_espacio_no_lo_encuentra(self, repos, espacios):
        """Ni leerlo ni borrarlo: conocer el identificador no basta."""
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            with en_espacio(tesis.id):
                repos.uploads.add(_upload("up-tesis"))

            assert repos.uploads.get("up-tesis") is None
            assert repos.uploads.delete("up-tesis") is False

            with en_espacio(tesis.id):
                assert repos.uploads.get("up-tesis") is not None
                assert repos.uploads.delete("up-tesis") is True

    def test_list_todos_ve_todos_los_espacios_pero_no_los_de_otra_persona(
        self, repos, espacios
    ):
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            with en_espacio(tesis.id):
                repos.uploads.add(_upload("up-tesis"))
            repos.uploads.add(_upload("up-general"))
        with como_usuario(BEA):
            repos.uploads.add(_upload("up-bea"))

        with como_usuario(ANA):
            assert {u.id for u in repos.uploads.list_todos()} == {"up-tesis", "up-general"}


class TestElCupoCuentaTodosLosEspacios:
    def test_repartir_archivos_entre_espacios_no_salta_el_limite(
        self, repos, espacios, tmp_path
    ):
        from src.uploads import store as modulo

        almacen = modulo.AlmacenEnDisco(tmp_path / "bytes")
        tienda = modulo.UploadStore(repositorio=repos.uploads, almacen=almacen)
        tienda.max_archivos = 2

        with como_usuario(ANA):
            a = espacios.crear("A")
            b = espacios.crear("B")
            with en_espacio(a.id):
                tienda.guardar("uno.txt", b"hola uno")
                tienda.guardar("dos.txt", b"hola dos")

            with en_espacio(b.id):
                with pytest.raises(modulo.ArchivoRechazado, match="límite"):
                    tienda.guardar("tres.txt", b"hola tres")


class TestElConocimiento:
    @pytest.fixture
    def almacen(self, db):
        from src.conocimiento import AlmacenDeConocimiento

        return AlmacenDeConocimiento(db)

    def test_se_busca_dentro_del_espacio(self, almacen, espacios):
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            with en_espacio(tesis.id):
                almacen.añadir("Bibliografía", "Los murciélagos usan ecolocalización.")

            assert almacen.buscar("murciélagos") == []
            assert almacen.listar() == []
            with en_espacio(tesis.id):
                assert almacen.buscar("murciélagos")
                assert len(almacen.listar()) == 1

    def test_el_mismo_titulo_en_dos_espacios_son_dos_documentos(self, almacen, espacios):
        """Añadir con un título existente reemplaza el documento. Dentro de un
        espacio es lo que se quiere; entre dos, sería pisar el de otro proyecto."""
        with como_usuario(ANA):
            a = espacios.crear("A")
            b = espacios.crear("B")
            with en_espacio(a.id):
                almacen.añadir("Notas", "contenido de A sobre ballenas")
            with en_espacio(b.id):
                almacen.añadir("Notas", "contenido de B sobre jirafas")

            with en_espacio(a.id):
                assert almacen.buscar("ballenas")
                assert almacen.buscar("jirafas") == []

    def test_borrar_desde_otro_espacio_no_toca_el_documento(self, almacen, espacios):
        """El hueco que se cerró al añadir los espacios.

        Los fragmentos no llevan espacio. Borrarlos por su documento sin
        comprobar antes que el documento está en el espacio actual habría
        vaciado un documento de otro proyecto dejando su ficha en pie.
        """
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            with en_espacio(tesis.id):
                documento = almacen.añadir("Bibliografía", "Los murciélagos cazan de noche.")

            assert almacen.eliminar(documento.id) is False

            with en_espacio(tesis.id):
                assert almacen.buscar("murciélagos"), (
                    "borrar desde General ha vaciado los fragmentos del documento"
                )


class TestBorrarUnEspacioNoBorraNadaDeDentro:
    def test_todo_vuelve_a_general(self, db, repos, espacios):
        from src.conocimiento import AlmacenDeConocimiento

        almacen = AlmacenDeConocimiento(db)
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
            repos.sessions.create("s-tesis", espacio_id=tesis.id)
            with en_espacio(tesis.id):
                repos.uploads.add(_upload("up-tesis"))
                almacen.añadir("Bibliografía", "Los murciélagos usan ecolocalización.")

            assert espacios.eliminar(tesis.id) is True

            assert repos.sessions.get("s-tesis").espacio_id is None
            assert [u.id for u in repos.uploads.list()] == ["up-tesis"]
            assert almacen.buscar("murciélagos")
            assert espacios.obtener(tesis.id) is None

    def test_no_toca_lo_de_otra_persona_con_el_mismo_identificador(self, db, repos, espacios):
        """El borrado filtra por usuario también al devolver cosas a General."""
        with como_usuario(ANA):
            tesis = espacios.crear("Tesis")
        with como_usuario(BEA):
            # Una fila de Bea que apunta al mismo identificador, como si lo
            # hubiera escrito a mano.
            repos.sessions.create("s-bea", espacio_id=tesis.id)

        with como_usuario(ANA):
            espacios.eliminar(tesis.id)

        with como_usuario(BEA):
            assert repos.sessions.get("s-bea").espacio_id == tesis.id


class TestLaMigracionConvierteLosGrupos:
    def test_cada_grupo_de_cada_persona_es_un_espacio(self, tmp_path, monkeypatch):
        """`group_name` era la media versión: texto libre en cada conversación.
        Al migrar, cada grupo distinto de cada persona pasa a ser un espacio y
        sus conversaciones quedan dentro."""
        import src.memory.db as modulo

        sin_espacios = [m for m in modulo.MIGRATIONS if m[0] < 18]
        monkeypatch.setattr(modulo, "MIGRATIONS", sin_espacios)
        base = modulo.Database(tmp_path / "vieja.db")
        assert base.version() == 17

        filas = [
            ("s1", "local", "Proyecto X"),
            ("s2", "local", "  Proyecto X "),
            ("s3", "local", "Otro"),
            ("s4", ANA, "Proyecto X"),
            ("s5", "local", None),
            ("s6", "local", "   "),
        ]
        with base.connect() as conn:
            for sid, usuario, grupo in filas:
                conn.execute(
                    "INSERT INTO sessions (id, created_at, updated_at, metadata, "
                    "user_id, group_name) VALUES (?, '2026', '2026', '{}', ?, ?)",
                    (sid, usuario, grupo),
                )

        monkeypatch.undo()
        base.migrate()

        with base.connect() as conn:
            creados = {
                (f["user_id"], f["nombre"]): f["id"]
                for f in conn.execute("SELECT * FROM espacios")
            }
            espacio_de = {
                f["id"]: f["espacio_id"]
                for f in conn.execute("SELECT id, espacio_id FROM sessions")
            }

        assert set(creados) == {
            ("local", "Proyecto X"), ("local", "Otro"), (ANA, "Proyecto X"),
        }, "un grupo con espacios alrededor ha dado dos espacios, o uno vacío ha dado uno"
        assert espacio_de["s1"] == espacio_de["s2"] == creados[("local", "Proyecto X")]
        assert espacio_de["s3"] == creados[("local", "Otro")]
        assert espacio_de["s4"] == creados[(ANA, "Proyecto X")], (
            "la conversación de Ana ha ido al espacio del mismo nombre de otra persona"
        )
        assert espacio_de["s5"] is None and espacio_de["s6"] is None


class TestLasInstruccionesEntranSoloEnSuTurno:
    def test_sin_instrucciones_no_se_añade_nada(self):
        """Decir el nombre del espacio sin nada más no cambia lo que hace el
        modelo, y cuesta tokens en cada llamada."""
        assert bloque_para_el_prompt(None) is None
        assert bloque_para_el_prompt(Espacio("esp-1", "Tesis", "   ")) is None

    def test_con_instrucciones_van_con_su_nombre_y_sin_saltarse_la_seguridad(self):
        bloque = bloque_para_el_prompt(Espacio("esp-1", "Tesis", "Cita en APA."))

        assert "Tesis" in bloque and "Cita en APA." in bloque
        assert "seguridad" in bloque, (
            "las instrucciones son texto libre: el prompt tiene que dejar claro que "
            "no pasan por encima de las políticas de seguridad"
        )

    def test_el_agente_las_usa_en_su_turno_y_no_toca_el_prompt_compartido(self):
        from src.agent.core import Agent
        from src.models.base import LLMProvider, LLMResponse
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        vistos: list[str] = []

        class Capturador(LLMProvider):
            @property
            def model_name(self):
                return "capturador"

            def generate(self, messages, tools=None, system_prompt=None, **kwargs):
                vistos.append(system_prompt or "")
                return LLMResponse(type="text", content="ok")

        agente = Agent(
            model=Capturador(),
            tool_registry=ToolRegistry(),
            permission_manager=PermissionManager(interactive=False),
        )
        original = agente.system_prompt

        agente.chat("hola", session_id="s-tesis",
                    contexto_espacio="## Espacio de trabajo: Tesis\n\nCita en APA.")
        agente.chat("hola", session_id="s-otra")

        assert "Cita en APA." in vistos[0]
        assert "Cita en APA." not in vistos[1], (
            "las instrucciones de un espacio se han colado en el turno de otra "
            "conversación"
        )
        assert agente.system_prompt == original, (
            "el agente ha escrito las instrucciones en su prompt compartido"
        )
