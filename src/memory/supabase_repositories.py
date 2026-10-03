"""
Implementación Supabase (PostgREST) de los repositorios de Morgan.

Cumple las **mismas interfaces** que la implementación SQLite, así que el Core no
distingue una de otra: es exactamente el desacoplamiento que pide la V1.3 (§4).

Dos avisos importantes:

- **Se usa la clave de servicio**, que salta las políticas RLS. Es correcto para
  un backend, y es la razón por la que esta clase nunca debe ejecutarse en el
  navegador. La clave vive en `.env` y no se registra en ningún log.
- **No sustituye a SQLite.** En el entorno local, SQLite sigue siendo la fuente
  de verdad y esto es el destino de la sincronización. Solo cuando Morgan corre
  en la nube (`MORGAN_ENVIRONMENT=cloud`) pasa a ser el almacén principal.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from src.observabilidad import contar, etapa
from src.espacios.contexto import espacio_actual
from src.identidad import usuario_actual
from src.memory.db import MemoryStorageError
from src.memory.models import MemoryRecord, Message, Session, Upload
from src.tasks.modelos import Task, TaskStep
from src.tasks.plan import Plan
from src.memory.repositories import (
    CUALQUIER_ESPACIO,
    MemoryRepository,
    MessageRepository,
    RepositoryFactory,
    SessionRepository,
    TaskRepository,
    UploadRepository,
)

logger = logging.getLogger(__name__)

# Un fallo de red al hablar con la nube no debe colgar una petición del usuario.
DEFAULT_TIMEOUT = 15.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _esc(valor: str) -> str:
    """Codifica un valor para que no actue como sintaxis del filtro.

    Un identificador con una coma o un `&` partiria la consulta en dos y
    podria colar un filtro ajeno. Se codifica entero, sin excepciones.
    """
    return quote(str(valor), safe="")


def _mio() -> str:
    """Filtro que acota una consulta a quien esta haciendo la peticion.

    La implementacion de SQLite lleva `AND user_id = ?` en **todas** sus
    consultas desde que existen las cuentas. Esta no lo llevaba en ninguna
    salvo en los planes, que se escribieron despues. En la nube eso
    significaba que `list()` devolvia las conversaciones de todo el mundo y
    que `get()` abria la de cualquiera con solo saber su identificador.

    El usuario **no** se pasa por parametro a proposito: si fuera un
    argumento, una ruta nueva podria olvidarlo y seguir compilando. Leyendolo
    del contexto de la peticion, olvidarlo no es posible.
    """
    return f"user_id=eq.{_esc(usuario_actual())}"


def _filtro_de_espacio(espacio: str | None) -> str:
    """El filtro de PostgREST para un espacio. `None` es «General».

    `is.null` y no `eq.`: PostgREST no casa nada con `eq.null`, así que
    «General» saldría siempre vacía.
    """
    return "espacio_id=is.null" if espacio is None else f"espacio_id=eq.{_esc(espacio)}"


def _de_este_espacio() -> str:
    """Acota una consulta al espacio de trabajo actual (V2.2).

    Leído del contexto, igual que `_mio()` y por el mismo motivo: si fuera un
    argumento, una consulta nueva podría olvidarlo y enseñar los archivos de un
    proyecto dentro de otro.
    """
    return _filtro_de_espacio(espacio_actual())


class SupabaseClient:
    """Cliente mínimo de PostgREST.

    Se implementa a mano en lugar de traer el SDK completo de Supabase: aquí solo
    hacen falta cuatro operaciones sobre tres tablas, y el SDK arrastraría media
    docena de dependencias transitivas (auth, realtime, storage) que Morgan no usa.
    """

    def __init__(self, url: str, key: str, timeout: float = DEFAULT_TIMEOUT):
        if not url or not key:
            raise MemoryStorageError("Faltan la URL o la clave de Supabase.")

        self.base_url = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        self.timeout = timeout

        # UNA conexion, reutilizada. Antes se usaba `httpx.request()`, la funcion
        # de modulo, que abre un cliente nuevo por llamada: conexion TCP nueva y
        # apreton de manos TLS nuevo cada vez.
        #
        # Con Supabase al lado eso se nota poco. Con Render en Frankfurt y
        # Supabase en us-east-1 —que es como esta hoy— cada consulta pagaba tres
        # viajes de ida y vuelta por el Atlantico en lugar de uno:
        #
        #   TCP  90 ms + TLS  90 ms + peticion  90 ms  =  ~340 ms
        #
        # Medido con la instrumentacion: un turno hacia 11 consultas y tardaba
        # 3.750 ms en la base, o sea 341 ms cada una. Reutilizando la conexion,
        # solo la primera paga el apreton y las demas cuestan un viaje.
        #
        # `httpx.Client` es seguro entre hilos para hacer peticiones, y hace
        # falta que lo sea: la API atiende varias a la vez y el turno del agente
        # corre en su propio hilo.
        self._http = httpx.Client(
            base_url=self.base_url,
            headers=self._headers,
            timeout=timeout,
            # Tantas vivas como abiertas, y sitio para la concurrencia real (V2.0.38).
            #
            # Antes eran 20 abiertas y solo 10 vivas. Medido en un despliegue
            # como el de producción (2.3-C): con 25 personas a la vez aparecía
            # `httpx.ReadError: [Errno 9] Bad file descriptor` y los turnos daban
            # 500. Con más conexiones en uso que vivas permitidas, el pool cierra
            # las que sobran al devolverlas, y ese cierre se cruzaba con otro hilo
            # que ya la estaba usando. Igualando los dos límites el pool no cierra
            # nada por exceso. Y 40 porque la reserva de hilos de Starlette es de
            # 40 y cada turno corre además en el suyo: con 20 los hilos esperaban
            # turno para hablar con la base.
            limits=httpx.Limits(max_keepalive_connections=40, max_connections=40),
        )

    def cerrar(self) -> None:
        """Suelta las conexiones. Para las pruebas y el apagado ordenado."""
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> Any:
        # Cada peticion a Supabase es un viaje de ida y vuelta por internet. En
        # produccion cuesta ~0,3 s, y una lectura de la web hace dos o tres: es
        # la mitad del segundo que tarda cualquier endpoint. Sin medirlo no hay
        # forma de saber si una ruta lenta es la base de datos o es CPU.
        # Se cuenta por tabla y operacion para poder responder «¿por que once
        # consultas en un turno?». El tiempo total ya se medía; lo que faltaba
        # era de donde salen.
        tabla = path.lstrip("/").split("?")[0].split("/")[0] or "?"
        contar(f"bd.{tabla}.{method.lower()}")

        with etapa("bd"):
            return self._request_sin_medir(method, path, **kwargs)

    def _request_sin_medir(self, method: str, path: str, **kwargs) -> Any:
        try:
            cabeceras = kwargs.pop("headers", None)
            response = self._http.request(
                method,
                path,
                headers=cabeceras or None,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            # Se traduce al mismo error que usa la capa local, para que quien
            # llama no tenga que saber si habló con SQLite o con la nube.
            raise MemoryStorageError(f"No se pudo contactar con Supabase: {exc}") from exc

        if response.status_code >= 400:
            # El cuerpo puede traer detalles del esquema, pero nunca la clave.
            raise MemoryStorageError(
                f"Supabase respondió {response.status_code}: {response.text[:200]}"
            )

        if not response.content:
            return []

        try:
            return response.json()
        except ValueError:
            return []

    def select(self, table: str, query: str = "") -> list[dict]:
        return self._request("GET", f"/{table}?{query}") or []

    def upsert(self, table: str, rows: list[dict], on_conflict: str) -> list[dict]:
        return self._request(
            "POST",
            f"/{table}?on_conflict={on_conflict}",
            json=rows,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        ) or []

    def insert(self, table: str, rows: list[dict]) -> list[dict]:
        return self._request(
            "POST", f"/{table}", json=rows, headers={"Prefer": "return=representation"}
        ) or []

    def update(self, table: str, query: str, values: dict) -> list[dict]:
        return self._request(
            "PATCH", f"/{table}?{query}", json=values, headers={"Prefer": "return=representation"}
        ) or []

    def delete(self, table: str, query: str) -> list[dict]:
        return self._request(
            "DELETE", f"/{table}?{query}", headers={"Prefer": "return=representation"}
        ) or []

    def rpc(self, funcion: str, args: dict) -> Any:
        """Llama a una función de Postgres.

        Hace falta para lo que PostgREST no sabe expresar: en concreto,
        incrementar un contador y comprobar su límite en una sola operación.
        Separarlos dejaría una ventana por la que dos peticiones simultáneas
        pasarían las dos.
        """
        return self._request("POST", f"/rpc/{funcion}", json=args)

    def ping(self) -> None:
        """Comprobación barata de disponibilidad."""
        self._request("GET", "/sessions?select=id&limit=1")


class SupabaseSessionRepository(SessionRepository):
    def __init__(self, client: SupabaseClient, device: str | None = None):
        self.client = client
        self.device = device

    def _to_session(self, row: dict) -> Session:
        return Session(
            id=row["id"],
            title=row.get("title"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            metadata=row.get("metadata") or {},
            message_count=row.get("message_count", 0) or 0,
            archived=bool(row.get("archived", False)),
            pinned=bool(row.get("pinned", False)),
            group_name=row.get("group_name"),
            espacio_id=row.get("espacio_id"),
        )

    def create(
        self,
        session_id: str,
        title: str | None = None,
        metadata: dict | None = None,
        *,
        espacio_id: str | None = None,
    ) -> Session:
        if not session_id or not session_id.strip():
            raise MemoryStorageError("El identificador de sesión no puede estar vacío.")

        session_id = session_id.strip()
        existente = self.get(session_id)
        if existente:
            return existente

        filas = self.client.upsert(
            "sessions",
            [{
                "id": session_id,
                "title": title,
                "metadata": metadata or {},
                "origin_device": self.device,
                "updated_at": _now(),
                "user_id": usuario_actual(),
                # Solo al crearla: arriba se devuelve la existente sin tocarla.
                "espacio_id": (espacio_id or "").strip() or None,
            }],
            # La clave primaria es (user_id, id). Con `on_conflict=id`,
            # Postgres respondia «no unique or exclusion constraint
            # matching» y crear una conversacion fallaba con un 500.
            on_conflict="user_id,id",
        )
        return self._to_session(filas[0]) if filas else Session(id=session_id, title=title)

    def get(self, session_id: str) -> Session | None:
        filas = self.client.select(
            "sessions", f"id=eq.{_esc(session_id)}&{_mio()}&select=*&limit=1"
        )
        if not filas:
            return None

        sesion = self._to_session(filas[0])
        # PostgREST no da agregados en la misma consulta sin una vista; se pide aparte.
        sesion.message_count = len(
            self.client.select(
                "messages", f"session_id=eq.{_esc(session_id)}&{_mio()}&select=id"
            )
        )
        return sesion

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        archived: bool | None = False,
        group_name: str | None = None,
        query: str | None = None,
        espacio: str | None = CUALQUIER_ESPACIO,
    ) -> list[Session]:
        filtros = [_mio()]
        if archived is not None:
            filtros.append(f"archived=is.{'true' if archived else 'false'}")
        if group_name is not None:
            filtros.append(f"group_name=eq.{quote(group_name, safe='')}")
        if espacio != CUALQUIER_ESPACIO:
            filtros.append(_filtro_de_espacio(espacio))
        if query and query.strip():
            # ilike de PostgREST: sin distinguir mayusculas, con '*' de comodin.
            # Se codifica el termino para que una coma o un parentesis no rompan
            # la sintaxis del filtro.
            termino = quote(f"*{query.strip()}*", safe="*")
            filtros.append(f"title=ilike.{termino}")

        consulta = "&".join(
            [*filtros, "select=*", "order=pinned.desc,updated_at.desc",
             f"limit={limit}", f"offset={offset}"]
        )
        filas = self.client.select("sessions", consulta)
        sesiones = [self._to_session(f) for f in filas]
        if not sesiones:
            return []

        # El número de mensajes se resuelve con UNA consulta para todas las
        # sesiones listadas, no una por sesión: PostgREST no da agregados por
        # grupo sin crear una vista, y N+1 peticiones de red sería peor remedio.
        ids = ",".join(_esc(s.id) for s in sesiones)
        mensajes = self.client.select(
            "messages", f"session_id=in.({ids})&{_mio()}&select=session_id"
        )

        conteos: dict[str, int] = {}
        for m in mensajes:
            conteos[m["session_id"]] = conteos.get(m["session_id"], 0) + 1

        for s in sesiones:
            s.message_count = conteos.get(s.id, 0)

        return sesiones

    def touch(self, session_id: str, title: str | None = None) -> None:
        valores: dict[str, Any] = {"updated_at": _now()}
        if title is not None:
            # Solo se pone título si aún no tenía, igual que en la versión local.
            actual = self.get(session_id)
            if actual and not actual.title:
                valores["title"] = title
        self.client.update("sessions", f"id=eq.{_esc(session_id)}&{_mio()}", valores)

    def update(
        self,
        session_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
        group_name: str | None = None,
        espacio_id: str | None = None,
    ) -> Session | None:
        valores: dict[str, Any] = {}
        if title is not None:
            valores["title"] = title.strip() or None
        if archived is not None:
            valores["archived"] = archived
        if pinned is not None:
            valores["pinned"] = pinned
        if group_name is not None:
            valores["group_name"] = group_name.strip() or None
        if espacio_id is not None:
            valores["espacio_id"] = espacio_id.strip() or None

        if not valores:
            return self.get(session_id)

        # updated_at no se toca, igual que en la version local: renombrar o
        # archivar no es usar la conversacion.
        filas = self.client.update(
            "sessions", f"id=eq.{_esc(session_id)}&{_mio()}", valores
        )
        return self.get(session_id) if filas else None

    def delete(self, session_id: str) -> bool:
        # Los mensajes caen con ella: la clave foranea de `messages` es
        # (user_id, session_id) con ON DELETE CASCADE.
        borradas = self.client.delete("sessions", f"id=eq.{_esc(session_id)}&{_mio()}")
        return len(borradas) > 0


class SupabaseMessageRepository(MessageRepository):
    def __init__(self, client: SupabaseClient, device: str | None = None):
        self.client = client
        self.device = device

    def _to_message(self, row: dict) -> Message:
        return Message(
            id=row.get("id"),
            session_id=row["session_id"],
            role=row["role"],
            content=row.get("content") or "",
            tool_name=row.get("tool_name"),
            tool_call_id=row.get("tool_call_id"),
            created_at=row.get("created_at"),
            metadata=row.get("metadata") or {},
        )

    def _row(self, message: Message) -> dict:
        from src.memory.sqlite_repositories import normalize_role

        return {
            "session_id": message.session_id,
            "role": normalize_role(message.role),
            "content": message.content or "",
            "tool_name": message.tool_name,
            "tool_call_id": message.tool_call_id,
            "metadata": message.metadata or {},
            "origin_device": self.device,
            "updated_at": _now(),
            # NOT NULL en el esquema: sin esto, guardar un mensaje fallaba
            # y la conversacion se perdia entera al recargar.
            "user_id": usuario_actual(),
            # Identidad estable entre equipos: el id local es autoincremental y
            # distinto en cada uno, así que no sirve para deduplicar.
            "client_id": f"{self.device or 'local'}:{message.session_id}:{message.id}"
            if message.id is not None else None,
        }

    def add(self, message: Message) -> Message:
        self._ensure_session(message.session_id)
        filas = self.client.insert("messages", [self._row(message)])
        if filas:
            message.id = filas[0].get("id", message.id)
        return message

    def add_many(self, messages: list[Message]) -> int:
        if not messages:
            return 0
        self._ensure_session(messages[0].session_id)
        self.client.insert("messages", [self._row(m) for m in messages])
        return len(messages)

    def _ensure_session(self, session_id: str) -> None:
        self.client.upsert(
            "sessions",
            [{
                "id": session_id,
                "origin_device": self.device,
                "user_id": usuario_actual(),
            }],
            on_conflict="user_id,id",
        )

    def list_for_session(
        self, session_id: str, limit: int = 100, roles: tuple[str, ...] | None = None
    ) -> list[Message]:
        from src.memory.sqlite_repositories import normalize_role

        query = (
            f"session_id=eq.{_esc(session_id)}&{_mio()}"
            f"&select=*&order=id.desc&limit={limit}"
        )
        if roles:
            normalizados = ",".join(normalize_role(r) for r in roles)
            query += f"&role=in.({normalizados})"

        filas = self.client.select("messages", query)
        return [self._to_message(f) for f in reversed(filas)]

    def count(self, session_id: str) -> int:
        return len(self.client.select(
            "messages", f"session_id=eq.{_esc(session_id)}&{_mio()}&select=id"
        ))

    def delete_for_session(self, session_id: str) -> int:
        return len(self.client.delete(
            "messages", f"session_id=eq.{_esc(session_id)}&{_mio()}"
        ))


class SupabaseMemoryRepository(MemoryRepository):
    def __init__(self, client: SupabaseClient, device: str | None = None):
        self.client = client
        self.device = device

    def _to_record(self, row: dict) -> MemoryRecord:
        return MemoryRecord(
            id=row.get("id"),
            key=row["key"],
            value=row.get("value") or "",
            category=row.get("category") or "general",
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def upsert(self, key: str, value: str, category: str = "general") -> MemoryRecord:
        clean_key = (key or "").strip()
        if not clean_key:
            raise MemoryStorageError("La clave del recuerdo no puede estar vacía.")

        filas = self.client.upsert(
            "memories",
            [{
                "key": clean_key,
                "value": (value or "").strip(),
                "category": category,
                "origin_device": self.device,
                "updated_at": _now(),
                "user_id": usuario_actual(),
            }],
            # La restriccion unica es (user_id, key): dos personas pueden
            # recordar cosas distintas bajo la misma clave.
            on_conflict="user_id,key",
        )
        return self._to_record(filas[0]) if filas else MemoryRecord(key=clean_key, value=value)

    def get(self, key: str) -> MemoryRecord | None:
        filas = self.client.select(
            "memories", f"key=eq.{_esc((key or '').strip())}&{_mio()}&select=*&limit=1"
        )
        return self._to_record(filas[0]) if filas else None

    def search(self, query: str | None = None, category: str | None = None, limit: int = 100) -> list[MemoryRecord]:
        partes = [_mio(), "select=*", "order=updated_at.desc", f"limit={limit}"]
        if category:
            partes.append(f"category=eq.{_esc(category)}")
        if query:
            # PostgREST: or=(key.ilike.*q*,value.ilike.*q*)
            partes.append(f"or=(key.ilike.*{query}*,value.ilike.*{query}*)")

        return [self._to_record(f) for f in self.client.select("memories", "&".join(partes))]

    def delete(self, key: str) -> bool:
        return len(self.client.delete(
            "memories", f"key=eq.{_esc((key or '').strip())}&{_mio()}"
        )) > 0

    def clear(self, category: str | None = None) -> int:
        query = f"category=eq.{_esc(category)}" if category else "key=not.is.null"
        return len(self.client.delete("memories", f"{query}&{_mio()}"))


class SupabaseUploadRepository(UploadRepository):
    def __init__(self, client: SupabaseClient, device: str | None = None):
        self.client = client
        self.device = device

    @staticmethod
    def _a_upload(row: dict) -> Upload:
        return Upload(
            id=row["id"],
            nombre_original=row.get("nombre_original", ""),
            mime=row.get("mime", ""),
            familia=row.get("familia", ""),
            tamano=row.get("tamano", 0) or 0,
            creado_en=row.get("creado_en", 0.0) or 0.0,
            almacenamiento=row.get("almacenamiento", "supabase"),
            espacio_id=row.get("espacio_id"),
        )

    def add(self, upload: Upload) -> Upload:
        self.client.upsert("uploads", [{
            "id": upload.id,
            "nombre_original": upload.nombre_original,
            "mime": upload.mime,
            "familia": upload.familia,
            "tamano": upload.tamano,
            "creado_en": upload.creado_en,
            "almacenamiento": upload.almacenamiento,
            "origin_device": self.device,
            "user_id": usuario_actual(),
            "espacio_id": espacio_actual(),
        }], on_conflict="id")
        upload.espacio_id = espacio_actual()
        return upload

    def get(self, upload_id: str) -> Upload | None:
        filas = self.client.select(
            "uploads",
            f"id=eq.{_esc(upload_id)}&{_mio()}&{_de_este_espacio()}&select=*&limit=1",
        )
        return self._a_upload(filas[0]) if filas else None

    def list(self, limit: int = 100) -> list[Upload]:
        filas = self.client.select(
            "uploads",
            f"{_mio()}&{_de_este_espacio()}&select=*&order=creado_en.desc&limit={limit}",
        )
        return [self._a_upload(f) for f in filas]

    def list_todos(self, limit: int = 100) -> list[Upload]:
        filas = self.client.select(
            "uploads", f"{_mio()}&select=*&order=creado_en.desc&limit={limit}"
        )
        return [self._a_upload(f) for f in filas]

    def delete(self, upload_id: str) -> bool:
        borrados = self.client.delete(
            "uploads", f"id=eq.{_esc(upload_id)}&{_mio()}&{_de_este_espacio()}"
        )
        return len(borrados) > 0

    def delete_older_than(self, momento: float) -> list[str]:
        # Se piden los ids ANTES de borrar: quien llama necesita saber cuales
        # eran para poder borrar tambien sus bytes del almacenamiento.
        filas = self.client.select(
            "uploads", f"creado_en=lt.{momento}&{_mio()}&select=id"
        )
        ids = [f["id"] for f in filas]
        if ids:
            self.client.delete("uploads", f"creado_en=lt.{momento}&{_mio()}")
        return ids


def _pasos_de(row: dict, que: str) -> list:
    """Los pasos de una tarea o de un plan, vengan como vengan.

    En Postgres la columna es `jsonb`, asi que llegan ya deserializados. Se
    acepta tambien texto por si algun cliente la escribio como cadena.

    **Si no se pueden leer, se anota.** Antes se devolvia una lista vacia en
    silencio, y eso es el peor de los dos males que este proyecto ya conoce: una
    tarea sin pasos y una tarea cuyos pasos se perdieron **se ven igual**. La
    persona mira una lista vacia y no tiene forma de saber que habia algo. Es lo
    mismo que paso con la memoria en la nube: cumplia en apariencia y no
    cumplia, y se encontro contando filas.

    Se sigue devolviendo la lista vacia y no se lanza: una tarea sin pasos vale
    mas que una excepcion que se lleva la lista entera por delante. Lo que
    cambia es que queda dicho.
    """
    crudos = row.get("pasos") or []
    if isinstance(crudos, str):
        try:
            crudos = json.loads(crudos)
        except (ValueError, TypeError):
            logger.warning(
                "Los pasos de %s '%s' no se pudieron leer y se pierden: %r",
                que, row.get("id"), crudos[:120],
            )
            crudos = []
    return crudos


class SupabaseTaskRepository(TaskRepository):
    def __init__(self, client: SupabaseClient, device: str | None = None):
        self.client = client
        self.device = device

    @staticmethod
    def _a_task(row: dict) -> Task:
        crudos = _pasos_de(row, "la tarea")

        return Task(
            id=row["id"],
            objetivo=row.get("objetivo", ""),
            estado=row.get("estado", "pending"),
            session_id=row.get("session_id"),
            pasos=[TaskStep.from_dict(p) for p in crudos],
            resultado=row.get("resultado"),
            error=row.get("error"),
            intentos=row.get("intentos", 0) or 0,
            creado_en=row.get("creado_en", 0.0) or 0.0,
            actualizado_en=row.get("actualizado_en", 0.0) or 0.0,
        )

    def _fila(self, task: Task) -> dict:
        return {
            "id": task.id,
            "objetivo": task.objetivo,
            "estado": task.estado,
            "session_id": task.session_id,
            "pasos": [p.to_dict() for p in task.pasos],
            "resultado": task.resultado,
            "error": task.error,
            "intentos": task.intentos,
            "creado_en": task.creado_en,
            "actualizado_en": task.actualizado_en,
            "origin_device": self.device,
            "user_id": usuario_actual(),
        }

    def create(self, task: Task) -> Task:
        self.client.upsert("tasks", [self._fila(task)], on_conflict="id")
        return task

    def get(self, task_id: str) -> Task | None:
        filas = self.client.select(
            "tasks", f"id=eq.{_esc(task_id)}&{_mio()}&select=*&limit=1"
        )
        return self._a_task(filas[0]) if filas else None

    def update(self, task: Task) -> Task | None:
        filas = self.client.update(
            "tasks", f"id=eq.{_esc(task.id)}&{_mio()}", self._fila(task)
        )
        return task if filas else None

    def list(
        self,
        session_id: str | None = None,
        estados: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[Task]:
        filtros = [_mio()]
        if session_id is not None:
            filtros.append(f"session_id=eq.{_esc(session_id)}")
        if estados:
            lista = ",".join(_esc(e) for e in estados)
            filtros.append(f"estado=in.({lista})")

        consulta = "&".join([*filtros, "select=*", "order=creado_en.desc", f"limit={limit}"])
        return [self._a_task(f) for f in self.client.select("tasks", consulta)]

    def delete(self, task_id: str) -> bool:
        return len(self.client.delete("tasks", f"id=eq.{_esc(task_id)}&{_mio()}")) > 0


class SupabasePlanRepository:
    """Los planes en la nube (V1.6).

    Mismo patrón que las tareas. Los pasos van como `jsonb`, así que llegan ya
    deserializados; se acepta también texto por si algún cliente los escribió
    como cadena.
    """

    def __init__(self, client: SupabaseClient, device: str | None = None):
        self.client = client
        self.device = device

    @staticmethod
    def _a_plan(row: dict) -> Plan:
        return Plan.from_dict({**row, "pasos": _pasos_de(row, "el plan")})

    def _fila(self, plan: Plan) -> dict:
        # Los argumentos van ENTEROS, no el `to_dict` publicable: ese los recorta
        # y enmascara para ensenarlos, y guardar eso significaria ejecutar
        # despues con argumentos truncados.
        return {
            "id": plan.id,
            "objetivo": plan.objetivo,
            "estado": plan.estado,
            "pasos": [p.to_almacen() for p in plan.pasos],
            "session_id": plan.session_id,
            "task_id": plan.task_id,
            "creado_en": plan.creado_en,
            "decidido_en": plan.decidido_en,
            "decidido_por": plan.decidido_por,
            "motivo_rechazo": plan.motivo_rechazo,
            "user_id": usuario_actual(),
        }

    def create(self, plan: Plan) -> Plan:
        self.client.upsert("planes", [self._fila(plan)], on_conflict="user_id,id")
        return plan

    def get(self, plan_id: str) -> Plan | None:
        filas = self.client.select(
            "planes",
            f"id=eq.{quote(plan_id, safe='')}"
            f"&{_mio()}&limit=1",
        )
        return self._a_plan(filas[0]) if filas else None

    def update(self, plan: Plan) -> Plan | None:
        filas = self.client.update(
            "planes",
            f"id=eq.{quote(plan.id, safe='')}"
            f"&{_mio()}",
            self._fila(plan),
        )
        return plan if filas else None

    def list(
        self,
        session_id: str | None = None,
        estados: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[Plan]:
        filtros = [_mio()]
        if session_id is not None:
            filtros.append(f"session_id=eq.{quote(session_id, safe='')}")
        if estados:
            lista = ",".join(quote(e, safe="") for e in estados)
            filtros.append(f"estado=in.({lista})")

        filtros.append(f"order=creado_en.desc&limit={int(limit)}")
        return [self._a_plan(f) for f in self.client.select("planes", "&".join(filtros))]

    def delete(self, plan_id: str) -> bool:
        return bool(self.client.delete(
            "planes",
            f"id=eq.{quote(plan_id, safe='')}"
            f"&{_mio()}",
        ))


class SupabaseRepositoryFactory(RepositoryFactory):
    """Reúne los repositorios sobre un proyecto de Supabase."""

    def __init__(self, url: str, key: str, device: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.client = SupabaseClient(url, key, timeout=timeout)
        self.device = device
        self._sessions = SupabaseSessionRepository(self.client, device)
        self._messages = SupabaseMessageRepository(self.client, device)
        self._memories = SupabaseMemoryRepository(self.client, device)
        self._uploads = SupabaseUploadRepository(self.client, device)
        self._tasks = SupabaseTaskRepository(self.client, device)
        self._planes = SupabasePlanRepository(self.client, device)

    @property
    def sessions(self) -> SessionRepository:
        return self._sessions

    @property
    def messages(self) -> MessageRepository:
        return self._messages

    @property
    def memories(self) -> MemoryRepository:
        return self._memories

    @property
    def uploads(self) -> UploadRepository:
        return self._uploads

    @property
    def tasks(self) -> TaskRepository:
        return self._tasks

    @property
    def planes(self) -> SupabasePlanRepository:
        return self._planes

    def health(self) -> tuple[bool, str | None]:
        try:
            self.client.ping()
            return True, None
        except MemoryStorageError as exc:
            return False, str(exc)
