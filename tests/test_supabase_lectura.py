"""
Lo que Supabase devuelve, traducido a objetos de Morgan.

**El hueco que cierran.** `test_supabase_aislamiento.py` recorre las 32
operaciones y comprueba la **forma de la petición**: que filtre por usuario, que
el `on_conflict` nombre una restricción que existe. Para eso le basta un cliente
que conteste siempre una lista vacía.

Y ahí estaba el problema: con la respuesta vacía, todos los métodos de lectura
salen por su rama corta. Nada de lo que pasa **después** de que Supabase
conteste con datos se ejecutaba nunca: el recuento de mensajes de cada
conversación, la traducción de una fila a un `Task`, la lectura de los pasos de
un plan. 63 líneas del fichero que corre en producción, sin pisar.

Es exactamente el patrón que ya costó ocho defectos: la mitad de Supabase no se
ejecutaba en las pruebas, y los defectos vivían justo ahí. La otra mitad era la
forma de la petición y ya está cubierta; esta es la de la respuesta.
"""

import pytest

from src.identidad.contexto import como_usuario
from src.memory.supabase_repositories import (
    SupabaseMemoryRepository,
    SupabaseMessageRepository,
    SupabasePlanRepository,
    SupabaseSessionRepository,
    SupabaseTaskRepository,
    SupabaseUploadRepository,
)
from src.tasks.modelos import Task
from src.tasks.plan import Plan

ANA = "usr-ana"


class ClienteConDatos:
    """Un PostgREST de mentira que contesta según la tabla y la consulta.

    Se diferencia del espía de `test_supabase_aislamiento.py` en lo único que
    importa aquí: **devuelve filas**. Las respuestas se dan como una lista de
    `(tabla, contiene_en_la_consulta, filas)` y se usa la primera que encaje, así
    que se puede hacer que `sessions` conteste una cosa y `messages` otra dentro
    de la misma llamada — que es justo lo que hace `get()` de conversaciones.
    """

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.peticiones: list[tuple[str, str, str]] = []

    def _responder(self, metodo, tabla, consulta=""):
        self.peticiones.append((metodo, tabla, consulta))
        for t, trozo, filas in self.respuestas:
            if t == tabla and (trozo is None or trozo in consulta):
                return filas
        return []

    def select(self, tabla, consulta=""):
        return self._responder("GET", tabla, consulta)

    def update(self, tabla, consulta, valores):
        return self._responder("PATCH", tabla, consulta)

    def insert(self, tabla, filas):
        return self._responder("POST", tabla)

    def upsert(self, tabla, filas, on_conflict):
        return self._responder("POST", tabla)

    def delete(self, tabla, consulta):
        return self._responder("DELETE", tabla, consulta)

    def rpc(self, funcion, args):
        return self._responder("POST", f"rpc/{funcion}")


def fila_de_sesion(id_sesion="s1", **extra) -> dict:
    base = {
        "id": id_sesion,
        "title": "Una conversación",
        "created_at": "2026-09-01T10:00:00+00:00",
        "updated_at": "2026-09-02T10:00:00+00:00",
        "metadata": {"origen": "web"},
        "archived": False,
        "pinned": False,
        "group_name": None,
    }
    return {**base, **extra}


class TestElRecuentoDeMensajes:
    """PostgREST no da agregados sin una vista, así que el recuento se pide en
    una segunda consulta. Esa segunda consulta no se ejecutaba nunca en las
    pruebas, y es la que decide el número que se ve en la lista de
    conversaciones.
    """

    def test_una_conversacion_trae_sus_mensajes_contados(self):
        cliente = ClienteConDatos([
            ("sessions", None, [fila_de_sesion()]),
            ("messages", None, [{"id": 1}, {"id": 2}, {"id": 3}]),
        ])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            sesion = repo.get("s1")

        assert sesion is not None
        assert sesion.id == "s1"
        assert sesion.title == "Una conversación"
        assert sesion.metadata == {"origen": "web"}
        assert sesion.message_count == 3

    def test_el_recuento_tambien_filtra_por_usuario(self):
        """Sin el filtro contaría los mensajes de todo el mundo. No es un fallo
        de privacidad —solo se ve un número— pero sí un número falso, y el filtro
        cuesta lo mismo.
        """
        cliente = ClienteConDatos([
            ("sessions", None, [fila_de_sesion()]),
            ("messages", None, [{"id": 1}]),
        ])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            repo.get("s1")

        consulta_mensajes = next(
            consulta for _, tabla, consulta in cliente.peticiones if tabla == "messages"
        )
        assert f"user_id=eq.{ANA}" in consulta_mensajes

    def test_en_la_lista_cada_una_cuenta_los_suyos(self):
        """El recuento se hace con **una** consulta para todas y se reparte en
        memoria. Repartirlo mal es fácil y no se nota: los números salen, solo
        que en la conversación equivocada.
        """
        cliente = ClienteConDatos([
            ("sessions", None, [fila_de_sesion("s1"), fila_de_sesion("s2"), fila_de_sesion("s3")]),
            ("messages", None, [
                {"session_id": "s1"}, {"session_id": "s1"}, {"session_id": "s1"},
                {"session_id": "s3"},
            ]),
        ])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            sesiones = repo.list()

        por_id = {s.id: s.message_count for s in sesiones}
        assert por_id == {"s1": 3, "s2": 0, "s3": 1}

    def test_una_lista_vacia_no_pide_los_mensajes(self):
        """Con `in.()` vacío, PostgREST responde un error de sintaxis. Preguntar
        por los mensajes de ninguna conversación no tiene sentido de todos modos.
        """
        cliente = ClienteConDatos([("sessions", None, [])])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            assert repo.list() == []

        assert not any(tabla == "messages" for _, tabla, _ in cliente.peticiones)


class TestLoQueSeGuardaSeVuelveALeer:
    def test_crear_una_que_ya_existe_devuelve_la_de_antes(self):
        """Y no inserta. Es lo que hace que abrir la misma conversación dos
        veces no la duplique.
        """
        cliente = ClienteConDatos([
            ("sessions", None, [fila_de_sesion()]),
            ("messages", None, []),
        ])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            sesion = repo.create("s1", "otro título")

        assert sesion.title == "Una conversación", "Se ha quedado el título nuevo"
        assert not any(metodo == "POST" for metodo, _, _ in cliente.peticiones)

    def test_actualizar_devuelve_como_quedo(self):
        cliente = ClienteConDatos([
            ("sessions", None, [fila_de_sesion(title="Renombrada", pinned=True)]),
            ("messages", None, []),
        ])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            sesion = repo.update("s1", title="Renombrada", pinned=True)

        assert sesion is not None
        assert sesion.title == "Renombrada"
        assert sesion.pinned is True

    def test_actualizar_algo_que_no_existe_devuelve_nada(self):
        cliente = ClienteConDatos([])
        repo = SupabaseSessionRepository(cliente)

        with como_usuario(ANA):
            assert repo.update("no-existe", title="x") is None

    def test_un_mensaje_vuelve_con_sus_partes(self):
        cliente = ClienteConDatos([("messages", None, [{
            "id": 7,
            "session_id": "s1",
            "role": "assistant",
            "content": "Listo",
            "tool_name": "search_web",
            "tool_call_id": "c1",
            "created_at": "2026-09-01T10:00:00+00:00",
            "metadata": {"tool_result": {"success": True}},
        }])])
        repo = SupabaseMessageRepository(cliente)

        with como_usuario(ANA):
            mensajes = repo.list_for_session("s1")

        assert len(mensajes) == 1
        assert mensajes[0].role == "assistant"
        assert mensajes[0].content == "Listo"
        assert mensajes[0].tool_name == "search_web"

    def test_un_recuerdo_vuelve_con_su_valor(self):
        cliente = ClienteConDatos([("memories", None, [{
            "key": "color_favorito",
            "value": "verde",
            "category": "gustos",
            "created_at": "2026-09-01T10:00:00+00:00",
            "updated_at": "2026-09-01T10:00:00+00:00",
        }])])
        repo = SupabaseMemoryRepository(cliente)

        with como_usuario(ANA):
            recuerdo = repo.get("color_favorito")

        assert recuerdo is not None
        assert recuerdo.value == "verde"
        assert recuerdo.category == "gustos"

    def test_un_adjunto_vuelve_con_su_tamano(self):
        cliente = ClienteConDatos([("uploads", None, [{
            "id": "up-1",
            "nombre_original": "foto.png",
            "mime": "image/png",
            "familia": "imagen",
            "tamano": 2048,
            "creado_en": 1.0,
        }])])
        repo = SupabaseUploadRepository(cliente)

        with como_usuario(ANA):
            adjunto = repo.get("up-1")

        assert adjunto is not None
        assert adjunto.nombre_original == "foto.png"
        assert adjunto.tamano == 2048


class TestLosPasosDeUnaTareaSobrevivenAlViaje:
    """El campo `pasos` es lo que hace que una tarea sea algo más que un título.

    Vuelve de Supabase de dos formas según el tipo de la columna: como lista ya
    deserializada si es `jsonb`, o como cadena si es `text`. El código admite las
    dos, y ninguna de las dos se ejecutaba en las pruebas.
    """

    @staticmethod
    def fila(pasos) -> dict:
        return {
            "id": "t1",
            "objetivo": "Revisar el proyecto",
            "estado": "pendiente",
            "creado_en": 1.0,
            "actualizado_en": 1.0,
            "session_id": "s1",
            "pasos": pasos,
            "intentos": 0,
            "error": None,
        }

    def test_una_lista_llega_tal_cual(self):
        cliente = ClienteConDatos([("tasks", None, [self.fila(
            [{"descripcion": "leer", "estado": "hecho"}]
        )])])
        repo = SupabaseTaskRepository(cliente)

        with como_usuario(ANA):
            tarea = repo.get("t1")

        assert tarea is not None
        assert len(tarea.pasos) == 1

    def test_una_cadena_json_se_deserializa(self):
        cliente = ClienteConDatos([("tasks", None, [self.fila(
            '[{"descripcion": "leer", "estado": "hecho"}]'
        )])])
        repo = SupabaseTaskRepository(cliente)

        with como_usuario(ANA):
            tarea = repo.get("t1")

        assert tarea is not None
        assert len(tarea.pasos) == 1, (
            "Los pasos llegaron como cadena y no se deserializaron: la tarea "
            "aparece vacía sin que nada falle"
        )

    def test_una_cadena_ilegible_no_tumba_la_tarea(self):
        """Vale más una tarea sin pasos que una excepción que se lleva la lista
        entera por delante. Pero **se anota**: un dato que desaparece en
        silencio es el peor de los dos males que ya costó este proyecto.
        """
        cliente = ClienteConDatos([("tasks", None, [self.fila("{roto")])])
        repo = SupabaseTaskRepository(cliente)

        with como_usuario(ANA):
            tarea = repo.get("t1")

        assert tarea is not None
        assert tarea.objetivo == "Revisar el proyecto"
        assert tarea.pasos == []

    def test_se_avisa_de_los_pasos_que_se_pierden(self, caplog):
        cliente = ClienteConDatos([("tasks", None, [self.fila("{roto")])])
        repo = SupabaseTaskRepository(cliente)

        with caplog.at_level("WARNING"), como_usuario(ANA):
            repo.get("t1")

        assert any("paso" in r.message.lower() for r in caplog.records), (
            "Los pasos se perdieron sin dejar rastro. Una tarea que aparece "
            "vacía y una tarea vacía de verdad se ven igual"
        )

    def test_actualizar_devuelve_la_tarea_como_quedo(self):
        cliente = ClienteConDatos([("tasks", None, [self.fila([])])])
        repo = SupabaseTaskRepository(cliente)
        tarea = Task(id="t1", objetivo="Revisar el proyecto")

        with como_usuario(ANA):
            devuelta = repo.update(tarea)

        assert devuelta is not None
        assert devuelta.id == "t1"

    def test_listar_traduce_todas(self):
        cliente = ClienteConDatos([("tasks", None, [self.fila([]), self.fila([])])])
        repo = SupabaseTaskRepository(cliente)

        with como_usuario(ANA):
            tareas = repo.list()

        assert len(tareas) == 2
        assert all(isinstance(t, Task) for t in tareas)


class TestLosPasosDeUnPlanTambien:
    @staticmethod
    def fila(pasos) -> dict:
        return {
            "id": "p1",
            "objetivo": "Poner Morgan en la nube",
            "estado": "propuesto",
            "creado_en": 1.0,
            "actualizado_en": 1.0,
            "session_id": "s1",
            "pasos": pasos,
            "riesgo": "low",
        }

    def test_una_cadena_json_se_deserializa(self):
        cliente = ClienteConDatos([("planes", None, [self.fila(
            '[{"descripcion": "desplegar", "herramienta": null}]'
        )])])
        repo = SupabasePlanRepository(cliente)

        with como_usuario(ANA):
            plan = repo.get("p1")

        assert plan is not None
        assert len(plan.pasos) == 1

    def test_una_cadena_ilegible_deja_el_plan_sin_pasos_pero_avisa(self, caplog):
        cliente = ClienteConDatos([("planes", None, [self.fila("{roto")])])
        repo = SupabasePlanRepository(cliente)

        with caplog.at_level("WARNING"), como_usuario(ANA):
            plan = repo.get("p1")

        assert plan is not None
        assert plan.pasos == []
        assert any("paso" in r.message.lower() for r in caplog.records), (
            "Un plan sin pasos es un plan que no se puede aprobar. Que se "
            "pierdan en silencio deja a la persona mirando una lista vacía"
        )

    def test_listar_traduce_todos(self):
        cliente = ClienteConDatos([("planes", None, [self.fila([]), self.fila([])])])
        repo = SupabasePlanRepository(cliente)

        with como_usuario(ANA):
            planes = repo.list()

        assert len(planes) == 2
        assert all(isinstance(p, Plan) for p in planes)


class TestLosFiltrosDeLaLista:
    """Los filtros se construyen como texto de una consulta de PostgREST, y ahí
    un carácter suelto cambia la sintaxis en lugar de buscar.

    Es la misma familia del defecto que se arregló en las herramientas de
    GitHub: lo que escribe una persona acaba dentro de una URL.
    """

    def _consulta(self, **kwargs) -> str:
        cliente = ClienteConDatos([("sessions", None, [])])
        repo = SupabaseSessionRepository(cliente)
        with como_usuario(ANA):
            repo.list(**kwargs)
        return next(c for _, tabla, c in cliente.peticiones if tabla == "sessions")

    def test_solo_las_archivadas(self):
        assert "archived=is.true" in self._consulta(archived=True)

    def test_solo_las_no_archivadas(self):
        assert "archived=is.false" in self._consulta(archived=False)

    def test_por_grupo(self):
        assert "group_name=eq.Trabajo" in self._consulta(group_name="Trabajo")

    def test_un_grupo_con_espacios_se_codifica(self):
        """Sin codificar, el espacio parte la consulta y el filtro se pierde."""
        consulta = self._consulta(group_name="Cosas de casa")

        assert "Cosas%20de%20casa" in consulta
        assert "group_name=eq.Cosas de casa" not in consulta

    def test_buscar_por_titulo_usa_ilike_con_comodines(self):
        consulta = self._consulta(query="informe")

        assert "title=ilike." in consulta
        assert "*informe*" in consulta, (
            "Sin los comodines, `ilike` busca el título exacto y no encuentra "
            "nada salvo coincidencia completa"
        )

    def test_una_coma_en_la_busqueda_no_rompe_el_filtro(self):
        """En PostgREST la coma separa filtros dentro de un `or`/`in`. Sin
        codificar, buscar «hola, mundo» deja de ser un término y pasa a ser
        sintaxis.
        """
        consulta = self._consulta(query="hola, mundo")

        assert "%2C" in consulta, "La coma viaja sin codificar"

    def test_y_un_parentesis_tampoco(self):
        consulta = self._consulta(query="factura (2026)")

        assert "%28" in consulta and "%29" in consulta

    def test_una_busqueda_en_blanco_no_añade_filtro(self):
        """Buscar nada tiene que devolver todo, no una consulta con un `ilike`
        vacío que no encuentra nada.
        """
        assert "ilike" not in self._consulta(query="   ")
