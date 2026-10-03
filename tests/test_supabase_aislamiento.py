"""
Aislamiento entre usuarios en la implementación de Supabase.

**El agujero que estas pruebas cierran.** La versión de SQLite lleva
`AND user_id = ?` en todas sus consultas desde que existen las cuentas, y hay
pruebas de aislamiento que lo comprueban. La de Supabase —la que corre en
producción— no lo llevaba en ninguna salvo en los planes, escritos más tarde.

Las consecuencias eran de dos tipos, y ninguna se veía en local:

1. **Fuga.** `list()` devolvía las conversaciones de todo el mundo, y `get()`
   abría la de cualquiera con solo conocer su identificador. Igual para
   recuerdos, adjuntos y tareas.
2. **Caída.** `sessions.user_id` y `messages.user_id` son `NOT NULL`, así que
   crear una conversación respondía 500 y ningún mensaje llegaba a guardarse. Y
   el `on_conflict` nombraba columnas que ya no formaban una restricción única:
   la clave primaria de `sessions` es `(user_id, id)` y la única de `memories`
   es `(user_id, key)`.

Estas pruebas no necesitan red: capturan las peticiones que se construyen y
comprueban su forma. Recorren **todos** los métodos de **todos** los
repositorios, así que un método nuevo que olvide el filtro las rompe.
"""

import pytest

from src.identidad.contexto import como_usuario
from src.memory.models import Message, Upload
from src.memory.supabase_repositories import (
    SupabaseMemoryRepository,
    SupabaseMessageRepository,
    SupabasePlanRepository,
    SupabaseSessionRepository,
    SupabaseTaskRepository,
    SupabaseUploadRepository,
)
from src.espacios.repositorio import RepositorioDeEspaciosSupabase
from src.tasks.modelos import Task
from src.tasks.plan import Plan

ANA = "usr-ana"
BEA = "usr-bea"

# Las restricciones que existen de verdad en el proyecto de Supabase. Un
# `on_conflict` que nombre otra cosa hace que Postgres responda «no unique or
# exclusion constraint matching the ON CONFLICT specification», que es como se
# manifestó el fallo en producción: un 500 al crear una conversación.
RESTRICCIONES_REALES = {
    "sessions": {"user_id,id"},
    "memories": {"user_id,key"},
    "planes": {"user_id,id"},
    "tasks": {"id"},
    "uploads": {"id"},
    "messages": {"client_id"},
    "espacios": {"user_id,id"},
}


class ClienteEspia:
    """Un PostgREST de mentira que apunta método, tabla, consulta y filas."""

    def __init__(self, respuesta=None):
        self.peticiones: list[dict] = []
        self.respuesta = respuesta if respuesta is not None else []

    def _apuntar(self, metodo, tabla, consulta="", filas=None, on_conflict=None):
        self.peticiones.append({
            "metodo": metodo,
            "tabla": tabla,
            "consulta": consulta,
            "filas": filas or [],
            "on_conflict": on_conflict,
        })
        return self.respuesta

    def select(self, tabla, consulta=""):
        return self._apuntar("GET", tabla, consulta)

    def update(self, tabla, consulta, valores):
        return self._apuntar("PATCH", tabla, consulta, [valores])

    def insert(self, tabla, filas):
        return self._apuntar("POST", tabla, filas=filas)

    def upsert(self, tabla, filas, on_conflict):
        return self._apuntar("POST", tabla, filas=filas, on_conflict=on_conflict)

    def delete(self, tabla, consulta):
        return self._apuntar("DELETE", tabla, consulta)

    def rpc(self, funcion, args):
        return self._apuntar("POST", f"rpc/{funcion}", filas=[args])


def _upload() -> Upload:
    return Upload(
        id="up-1", nombre_original="foto.png", mime="image/png",
        familia="imagen", tamano=10, creado_en=1.0,
    )


# Cada entrada: (nombre del repositorio, clase, método, argumentos).
#
# Se listan a mano en lugar de descubrirlos con `dir()` a propósito: así, añadir
# un método sin añadirlo aquí deja la lista incompleta de forma visible, y hay
# una prueba más abajo que comprueba justo eso.
OPERACIONES = [
    ("sessions", SupabaseSessionRepository, "create", ("s1", "titulo", {})),
    ("sessions", SupabaseSessionRepository, "get", ("s1",)),
    ("sessions", SupabaseSessionRepository, "list", ()),
    ("sessions", SupabaseSessionRepository, "touch", ("s1", "titulo")),
    ("sessions", SupabaseSessionRepository, "update", ("s1",)),
    ("sessions", SupabaseSessionRepository, "delete", ("s1",)),
    ("messages", SupabaseMessageRepository, "add",
     (Message(session_id="s1", role="user", content="hola"),)),
    ("messages", SupabaseMessageRepository, "add_many",
     ([Message(session_id="s1", role="user", content="hola")],)),
    ("messages", SupabaseMessageRepository, "list_for_session", ("s1",)),
    ("messages", SupabaseMessageRepository, "count", ("s1",)),
    ("messages", SupabaseMessageRepository, "delete_for_session", ("s1",)),
    ("memories", SupabaseMemoryRepository, "upsert", ("clave", "valor")),
    ("memories", SupabaseMemoryRepository, "get", ("clave",)),
    ("memories", SupabaseMemoryRepository, "search", ("busca", "general")),
    ("memories", SupabaseMemoryRepository, "delete", ("clave",)),
    ("memories", SupabaseMemoryRepository, "clear", ()),
    ("memories", SupabaseMemoryRepository, "clear", ("general",)),
    ("uploads", SupabaseUploadRepository, "add", (_upload(),)),
    ("uploads", SupabaseUploadRepository, "get", ("up-1",)),
    ("uploads", SupabaseUploadRepository, "list", ()),
    ("uploads", SupabaseUploadRepository, "delete", ("up-1",)),
    ("uploads", SupabaseUploadRepository, "delete_older_than", (1.0,)),
    ("uploads", SupabaseUploadRepository, "list_todos", ()),
    ("tasks", SupabaseTaskRepository, "create", (Task(id="t1", objetivo="x"),)),
    ("tasks", SupabaseTaskRepository, "get", ("t1",)),
    ("tasks", SupabaseTaskRepository, "update", (Task(id="t1", objetivo="x"),)),
    ("tasks", SupabaseTaskRepository, "list", ()),
    ("tasks", SupabaseTaskRepository, "delete", ("t1",)),
    ("planes", SupabasePlanRepository, "create", (Plan(id="p1", objetivo="x"),)),
    ("planes", SupabasePlanRepository, "get", ("p1",)),
    ("planes", SupabasePlanRepository, "update", (Plan(id="p1", objetivo="x"),)),
    ("planes", SupabasePlanRepository, "list", ()),
    ("planes", SupabasePlanRepository, "delete", ("p1",)),
    # Espacios de trabajo (V2.2).
    ("espacios", RepositorioDeEspaciosSupabase, "crear", ("Tesis", "Cita en APA.")),
    ("espacios", RepositorioDeEspaciosSupabase, "obtener", ("esp-1",)),
    ("espacios", RepositorioDeEspaciosSupabase, "listar", ()),
    ("espacios", RepositorioDeEspaciosSupabase, "actualizar", ("esp-1",)),
    ("espacios", RepositorioDeEspaciosSupabase, "eliminar", ("esp-1",)),
]

IDS = [f"{nombre}.{metodo}" for nombre, _, metodo, _ in OPERACIONES]


def _ejecutar(clase, metodo, argumentos, usuario=ANA, respuesta=None):
    cliente = ClienteEspia(respuesta)
    with como_usuario(usuario):
        getattr(clase(cliente, "equipo"), metodo)(*argumentos)
    return cliente


@pytest.mark.parametrize("nombre, clase, metodo, argumentos", OPERACIONES, ids=IDS)
class TestNadaSaleDelUsuario:
    """El barrido completo. Es lo que faltaba: no una prueba por método, sino
    una regla aplicada a todos."""

    def test_toda_lectura_o_borrado_filtra_por_usuario(
        self, nombre, clase, metodo, argumentos
    ):
        """Sin esto, `list()` devuelve las conversaciones de otra persona."""
        cliente = _ejecutar(clase, metodo, argumentos)

        for peticion in cliente.peticiones:
            if peticion["metodo"] in ("GET", "PATCH", "DELETE"):
                assert f"user_id=eq.{ANA}" in peticion["consulta"], (
                    f"{nombre}.{metodo} consulta {peticion['tabla']} sin filtrar "
                    f"por usuario: {peticion['consulta']}"
                )

    def test_toda_escritura_deja_el_usuario_puesto(
        self, nombre, clase, metodo, argumentos
    ):
        """Una fila sin `user_id` es una fila que nadie podrá volver a leer —o,
        en las tablas donde la columna es NOT NULL, un 500."""
        cliente = _ejecutar(clase, metodo, argumentos)

        for peticion in cliente.peticiones:
            if peticion["metodo"] != "POST" or peticion["tabla"].startswith("rpc/"):
                continue
            for fila in peticion["filas"]:
                assert fila.get("user_id") == ANA, (
                    f"{nombre}.{metodo} escribe en {peticion['tabla']} sin "
                    f"user_id: {sorted(fila)}"
                )

    def test_el_on_conflict_nombra_una_restriccion_que_existe(
        self, nombre, clase, metodo, argumentos
    ):
        """El fallo exacto de producción: `on_conflict=id` sobre una tabla cuya
        clave primaria es `(user_id, id)`."""
        cliente = _ejecutar(clase, metodo, argumentos)

        for peticion in cliente.peticiones:
            conflicto = peticion["on_conflict"]
            if conflicto is None:
                continue
            validas = RESTRICCIONES_REALES[peticion["tabla"]]
            assert conflicto in validas, (
                f"{nombre}.{metodo} hace upsert en {peticion['tabla']} con "
                f"on_conflict={conflicto!r}, que no es una restricción única "
                f"real. Las que hay: {sorted(validas)}"
            )

    def test_dos_personas_construyen_consultas_distintas(
        self, nombre, clase, metodo, argumentos
    ):
        """La comprobación de verdad: lo mismo pedido por otra persona no puede
        producir la misma petición."""
        de_ana = _ejecutar(clase, metodo, argumentos, usuario=ANA)
        de_bea = _ejecutar(clase, metodo, argumentos, usuario=BEA)

        huella_ana = [(p["tabla"], p["consulta"], p["filas"]) for p in de_ana.peticiones]
        huella_bea = [(p["tabla"], p["consulta"], p["filas"]) for p in de_bea.peticiones]

        assert huella_ana != huella_bea, (
            f"{nombre}.{metodo} produce la misma petición para dos usuarios "
            "distintos: no distingue de quién son los datos"
        )


class TestLaListaDeOperacionesEstaCompleta:
    """Una lista escrita a mano se queda atrás sola. Esto lo impide."""

    @pytest.mark.parametrize(
        "clase",
        [
            SupabaseSessionRepository,
            SupabaseMessageRepository,
            SupabaseMemoryRepository,
            SupabaseUploadRepository,
            SupabaseTaskRepository,
            SupabasePlanRepository,
            RepositorioDeEspaciosSupabase,
        ],
        ids=lambda c: c.__name__,
    )
    def test_todo_metodo_publico_esta_cubierto(self, clase):
        publicos = {
            nombre for nombre in vars(clase)
            if not nombre.startswith("_") and callable(getattr(clase, nombre))
        }
        cubiertos = {m for _, c, m, _ in OPERACIONES if c is clase}

        faltan = publicos - cubiertos
        assert not faltan, (
            f"{clase.__name__} tiene métodos sin comprobar el aislamiento: "
            f"{sorted(faltan)}. Añádelos a OPERACIONES."
        )


class TestLosMensajesCuelganDeLaConversacionDeSuDueno:
    """La clave foránea de `messages` es `(user_id, session_id)` contra
    `sessions(user_id, id)`. Si el mensaje se guarda con un usuario y la
    conversación con otro, Postgres lo rechaza."""

    def test_la_sesion_se_asegura_con_el_mismo_usuario_que_el_mensaje(self):
        cliente = _ejecutar(
            SupabaseMessageRepository, "add",
            (Message(session_id="s1", role="user", content="hola"),),
        )

        usuarios = {
            fila["user_id"]
            for peticion in cliente.peticiones
            for fila in peticion["filas"]
            if "user_id" in fila
        }

        assert usuarios == {ANA}, (
            f"El mensaje y su conversación se guardan con usuarios distintos: {usuarios}"
        )

    def test_se_asegura_la_conversacion_antes_de_insertar(self):
        """Al revés, la inserción del mensaje falla por la clave foránea."""
        cliente = _ejecutar(
            SupabaseMessageRepository, "add",
            (Message(session_id="s1", role="user", content="hola"),),
        )

        tablas = [p["tabla"] for p in cliente.peticiones]
        assert tablas.index("sessions") < tablas.index("messages")


class TestLosValoresPeligrososNoRompenElFiltro:
    """Un identificador con `&` podría añadir un filtro propio a la consulta —o
    quitar el del usuario."""

    @pytest.mark.parametrize(
        "peligroso",
        ["s1&user_id=eq.usr-bea", "s1,s2", "s1)or(1=1", "s1&select=*"],
    )
    def test_un_identificador_manipulado_viaja_codificado(self, peligroso):
        cliente = _ejecutar(SupabaseSessionRepository, "get", (peligroso,))

        consulta = cliente.peticiones[0]["consulta"]
        assert "user_id=eq.usr-bea" not in consulta
        assert f"user_id=eq.{ANA}" in consulta


class TestLosArchivosYElConocimientoFiltranPorEspacio:
    """Espacios de trabajo (V2.2): lo mismo que el usuario, para el espacio.

    Dentro de un espacio solo se ve lo suyo, y fuera de todos —«General»— solo lo
    que no está en ninguno. En PostgREST eso último es `espacio_id=is.null`: con
    `eq.null` no casaría nada y «General» saldría siempre vacía.
    """

    @pytest.mark.parametrize("metodo, argumentos", [
        ("get", ("up-1",)),
        ("list", ()),
        ("delete", ("up-1",)),
    ])
    def test_los_archivos_filtran_por_el_espacio_actual(self, metodo, argumentos):
        from src.espacios.contexto import en_espacio

        dentro = ClienteEspia()
        with como_usuario(ANA), en_espacio("esp-a"):
            getattr(SupabaseUploadRepository(dentro, "equipo"), metodo)(*argumentos)
        fuera = ClienteEspia()
        with como_usuario(ANA):
            getattr(SupabaseUploadRepository(fuera, "equipo"), metodo)(*argumentos)

        assert all("espacio_id=eq.esp-a" in p["consulta"] for p in dentro.peticiones)
        assert all("espacio_id=is.null" in p["consulta"] for p in fuera.peticiones)

    def test_list_todos_no_filtra_por_espacio_y_si_por_usuario(self):
        """Es la lista del cupo. Filtrar por espacio dejaría saltarse el límite
        repartiendo archivos entre espacios."""
        from src.espacios.contexto import en_espacio

        cliente = ClienteEspia()
        with como_usuario(ANA), en_espacio("esp-a"):
            SupabaseUploadRepository(cliente, "equipo").list_todos()

        consulta = cliente.peticiones[0]["consulta"]
        assert f"user_id=eq.{ANA}" in consulta
        assert "espacio_id" not in consulta

    def test_un_archivo_nuevo_se_guarda_en_su_espacio(self):
        from src.espacios.contexto import en_espacio

        cliente = ClienteEspia()
        with como_usuario(ANA), en_espacio("esp-a"):
            SupabaseUploadRepository(cliente, "equipo").add(_upload())

        assert cliente.peticiones[0]["filas"][0]["espacio_id"] == "esp-a"

    def test_el_conocimiento_busca_y_lista_en_su_espacio(self):
        from src.conocimiento.almacen_supabase import AlmacenDeConocimientoSupabase
        from src.espacios.contexto import en_espacio

        cliente = ClienteEspia()
        with como_usuario(ANA), en_espacio("esp-a"):
            almacen = AlmacenDeConocimientoSupabase(cliente)
            almacen.buscar("murciélagos")
            almacen.listar()

        rpc = next(p for p in cliente.peticiones if p["tabla"] == "rpc/buscar_conocimiento")
        assert rpc["filas"][0]["p_espacio_id"] == "esp-a"
        listado = next(p for p in cliente.peticiones if p["tabla"] == "conocimiento")
        assert "espacio_id=eq.esp-a" in listado["consulta"]

    def test_borrar_un_espacio_devuelve_lo_de_dentro_a_general_solo_del_usuario(self):
        cliente = ClienteEspia()
        with como_usuario(ANA):
            RepositorioDeEspaciosSupabase(cliente, "equipo").eliminar("esp-a")

        devoluciones = [p for p in cliente.peticiones if p["metodo"] == "PATCH"]
        assert {p["tabla"] for p in devoluciones} == {"sessions", "uploads", "conocimiento"}
        for p in devoluciones:
            assert "espacio_id=eq.esp-a" in p["consulta"]
            assert f"user_id=eq.{ANA}" in p["consulta"]
            assert p["filas"][0] == {"espacio_id": None}
