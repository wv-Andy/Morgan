"""
Implementación SQLite de los repositorios de Morgan.

Es la implementación por defecto y la única que se envía: Morgan actúa sobre la
máquina local, así que su memoria debe funcionar sin red y sin que los datos
personales salgan del equipo (ver ADR-006 y ADR-010).

Para añadir PostgreSQL o Supabase basta con otra implementación de las mismas
interfaces; el Core no cambia.
"""

import json
import logging
from datetime import datetime
from typing import Any

from src.espacios.contexto import espacio_actual
from src.identidad.contexto import usuario_actual
from src.memory.db import Database, MemoryStorageError, guard
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

# 'model' es el nombre que usa el núcleo del agente; en la base se guarda como
# 'assistant', que es el vocabulario estándar y el que espera la especificación.
ROLE_ALIASES = {"model": "assistant"}


def _now() -> str:
    """Marca de tiempo con milisegundos.

    Con resolución de segundo, dos sesiones usadas dentro del mismo segundo
    empatan y el listado por recencia sale en orden arbitrario. El formato
    sigue siendo comparable lexicográficamente con las marcas antiguas, que
    son un prefijo de este.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _load_metadata(raw: Any) -> dict:
    """Los metadatos se guardan como JSON; una fila corrupta no debe romper la lectura."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        logger.warning("Metadatos no válidos en la base de datos; se ignoran")
        return {}


def _dump_metadata(metadata: dict | None) -> str:
    try:
        return json.dumps(metadata or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(role, role)


def _ensure_session(conn, session_id: str) -> None:
    """Crea la sesión si un mensaje llega antes que ella.

    Las marcas de tiempo se pasan siempre de forma explícita: el DEFAULT
    CURRENT_TIMESTAMP de SQLite es UTC, y mezclarlo con la hora local rompe el
    orden por recencia (las filas creadas por defecto parecen del futuro).
    """
    now = _now()
    conn.execute(
        # El usuario va aqui tambien. Sin el, una sesion creada por este camino
        # nacia sin dueno y despues nadie podia verla: ni quien la creo. Lo cazo
        # una prueba existente al aplicar el filtro.
        "INSERT OR IGNORE INTO sessions (id, created_at, updated_at, user_id) "
        "VALUES (?, ?, ?, ?)",
        (session_id, now, now, usuario_actual()),
    )


class SQLiteSessionRepository(SessionRepository):
    def __init__(self, db: Database):
        self.db = db

    def _row_to_session(self, row) -> Session:
        return Session(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load_metadata(row["metadata"]),
            message_count=row["message_count"] if "message_count" in row.keys() else 0,
            archived=bool(row["archived"]) if "archived" in row.keys() else False,
            pinned=bool(row["pinned"]) if "pinned" in row.keys() else False,
            group_name=row["group_name"] if "group_name" in row.keys() else None,
            espacio_id=row["espacio_id"] if "espacio_id" in row.keys() else None,
        )

    @guard("crear la sesión")
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
        now = _now()
        with self.db.connect() as conn:
            conn.execute(
                # OR IGNORE: si ya existía, NO cambia de espacio. Moverla es
                # una decisión explícita, y va por `update`.
                "INSERT OR IGNORE INTO sessions "
                "(id, title, created_at, updated_at, metadata, user_id, espacio_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, title, now, now, _dump_metadata(metadata), usuario_actual(),
                 (espacio_id or "").strip() or None),
            )

        existing = self.get(session_id)
        # get() no puede devolver None aquí: la fila acaba de insertarse o ya existía.
        return existing or Session(id=session_id, title=title, created_at=now, updated_at=now)

    @guard("recuperar la sesión")
    def get(self, session_id: str) -> Session | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m "
                "  WHERE m.session_id = s.id AND m.user_id = s.user_id) AS message_count "
                # El filtro por usuario va aqui y no en la ruta: es el unico
                # sitio por el que pasan TODAS las lecturas de sesiones.
                "FROM sessions s WHERE s.id = ? AND s.user_id = ?",
                (session_id, usuario_actual()),
            ).fetchone()
            return self._row_to_session(row) if row else None

    @guard("listar las sesiones")
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
        # Siempre presente: nunca se listan sesiones de otro.
        condiciones: list[str] = ["s.user_id = ?"]
        parametros: list = [usuario_actual()]

        if archived is not None:
            condiciones.append("s.archived = ?")
            parametros.append(1 if archived else 0)

        if group_name is not None:
            condiciones.append("s.group_name = ?")
            parametros.append(group_name)

        if espacio != CUALQUIER_ESPACIO:
            # IS y no =: con =, NULL = NULL no es cierto y «General» saldría
            # siempre vacía.
            condiciones.append("s.espacio_id IS ?")
            parametros.append(espacio)

        if query and query.strip():
            # LIKE con comodines a ambos lados: busca el termino en cualquier
            # parte del titulo. Se escapan los comodines del propio termino para
            # que un '%' escrito por el usuario no lo case todo.
            barra = chr(92)
            termino = (
                query.strip()
                .replace(barra, barra * 2)
                .replace("%", barra + "%")
                .replace("_", barra + "_")
            )
            condiciones.append("s.title LIKE ? ESCAPE '" + barra + "'")
            parametros.append(f"%{termino}%")

        where = f"WHERE {' AND '.join(condiciones)}"
        parametros.extend([limit, offset])

        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m "
                "  WHERE m.session_id = s.id AND m.user_id = s.user_id) AS message_count "
                f"FROM sessions s {where} "
                "ORDER BY s.pinned DESC, s.updated_at DESC, s.rowid DESC LIMIT ? OFFSET ?",
                parametros,
            ).fetchall()
            return [self._row_to_session(r) for r in rows]

    @guard("actualizar la sesión")
    def touch(self, session_id: str, title: str | None = None) -> None:
        with self.db.connect() as conn:
            if title is None:
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (_now(), session_id, usuario_actual()),
                )
            else:
                # Solo se pone título si aún no tenía: el primero describe la conversación.
                conn.execute(
                    "UPDATE sessions SET updated_at = ?, title = COALESCE(title, ?) "
                    "WHERE id = ? AND user_id = ?",
                    (_now(), title, session_id, usuario_actual()),
                )

    @guard("actualizar los datos de la sesión")
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
        campos: list[str] = []
        valores: list = []

        if title is not None:
            campos.append("title = ?")
            valores.append(title.strip() or None)
        if archived is not None:
            campos.append("archived = ?")
            valores.append(1 if archived else 0)
        if pinned is not None:
            campos.append("pinned = ?")
            valores.append(1 if pinned else 0)
        if group_name is not None:
            # La cadena vacia significa "quitalo del grupo"; None es "no lo toques".
            campos.append("group_name = ?")
            valores.append(group_name.strip() or None)
        if espacio_id is not None:
            # Igual que el grupo: la cadena vacía es «devuélvela a General».
            campos.append("espacio_id = ?")
            valores.append(espacio_id.strip() or None)

        if not campos:
            return self.get(session_id)

        # updated_at NO se toca: renombrar o archivar no es usar la conversacion,
        # y moverla al principio del historial por eso seria desconcertante.
        valores.append(session_id)
        with self.db.connect() as conn:
            valores.append(usuario_actual())
            cursor = conn.execute(
                f"UPDATE sessions SET {', '.join(campos)} WHERE id = ? AND user_id = ?",
                valores,
            )
            if cursor.rowcount == 0:
                return None

        return self.get(session_id)

    @guard("eliminar la sesión")
    def delete(self, session_id: str) -> bool:
        with self.db.connect() as conn:
            # Borrado explícito de los mensajes: ON DELETE CASCADE depende de que
            # el PRAGMA foreign_keys esté activo en cada conexión.
            conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND user_id = ?",
                (session_id, usuario_actual()),
            )
            cursor = conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, usuario_actual()),
            )
            return cursor.rowcount > 0


class SQLiteMessageRepository(MessageRepository):
    def __init__(self, db: Database):
        self.db = db

    def _row_to_message(self, row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            tool_name=row["tool_name"],
            tool_call_id=row["tool_call_id"],
            created_at=row["created_at"],
            metadata=_load_metadata(row["metadata"]),
        )

    @guard("guardar el mensaje")
    def add(self, message: Message) -> Message:
        with self.db.connect() as conn:
            _ensure_session(conn, message.session_id)
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, tool_call_id, created_at, metadata, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.session_id,
                    normalize_role(message.role),
                    message.content or "",
                    message.tool_name,
                    message.tool_call_id,
                    message.created_at or _now(),
                    _dump_metadata(message.metadata),
                    usuario_actual(),
                ),
            )
            message.id = cursor.lastrowid
            return message

    @guard("guardar los mensajes")
    def add_many(self, messages: list[Message]) -> int:
        if not messages:
            return 0

        now = _now()
        with self.db.connect() as conn:
            _ensure_session(conn, messages[0].session_id)
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, tool_name, tool_call_id, created_at, metadata, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        m.session_id,
                        normalize_role(m.role),
                        m.content or "",
                        m.tool_name,
                        m.tool_call_id,
                        m.created_at or now,
                        _dump_metadata(m.metadata),
                        usuario_actual(),
                    )
                    for m in messages
                ],
            )
            return len(messages)

    @guard("recuperar los mensajes")
    def list_for_session(
        self,
        session_id: str,
        limit: int = 100,
        roles: tuple[str, ...] | None = None,
    ) -> list[Message]:
        sql = "SELECT * FROM messages WHERE session_id = ? AND user_id = ?"
        params: list[Any] = [session_id, usuario_actual()]

        if roles:
            placeholders = ",".join("?" for _ in roles)
            sql += f" AND role IN ({placeholders})"
            params.extend(normalize_role(r) for r in roles)

        # Se toman los últimos N y luego se reordenan: interesa la cola, no la cabeza.
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [self._row_to_message(r) for r in reversed(rows)]

    @guard("contar los mensajes")
    def count(self, session_id: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND user_id = ?",
                (session_id, usuario_actual()),
            ).fetchone()
            return row["n"]

    @guard("eliminar los mensajes")
    def delete_for_session(self, session_id: str) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND user_id = ?",
                (session_id, usuario_actual()),
            )
            return cursor.rowcount


class SQLiteMemoryRepository(MemoryRepository):
    def __init__(self, db: Database):
        self.db = db

    def _row_to_record(self, row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            key=row["key"],
            value=row["value"],
            category=row["category"],
            created_at=row["created_at"] if "created_at" in row.keys() else None,
            updated_at=row["updated_at"],
        )

    @guard("guardar el recuerdo")
    def upsert(self, key: str, value: str, category: str = "general") -> MemoryRecord:
        clean_key = (key or "").strip()
        if not clean_key:
            raise MemoryStorageError("La clave del recuerdo no puede estar vacía.")

        now = _now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (category, key, value, created_at, updated_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                -- El conflicto es por (user_id, key), no por key a secas:
                -- dos usuarios pueden recordar cosas distintas bajo el
                -- mismo nombre sin pisarse.
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    updated_at = excluded.updated_at
                """,
                (category, clean_key, (value or "").strip(), now, now, usuario_actual()),
            )

        return MemoryRecord(key=clean_key, value=(value or "").strip(), category=category, updated_at=now)

    @guard("recuperar el recuerdo")
    def get(self, key: str) -> MemoryRecord | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE key = ? AND user_id = ?",
                ((key or "").strip(), usuario_actual()),
            ).fetchone()
            return self._row_to_record(row) if row else None

    @guard("buscar recuerdos")
    def search(self, query: str | None = None, category: str | None = None, limit: int = 100) -> list[MemoryRecord]:
        sql = "SELECT * FROM memories WHERE user_id = ?"
        params: list[Any] = [usuario_actual()]

        if category:
            sql += " AND category = ?"
            params.append(category)

        if query:
            sql += " AND (key LIKE ? OR value LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like])

        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        with self.db.connect() as conn:
            return [self._row_to_record(r) for r in conn.execute(sql, params).fetchall()]

    @guard("eliminar el recuerdo")
    def delete(self, key: str) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE key = ? AND user_id = ?",
                ((key or "").strip(), usuario_actual()),
            )
            return cursor.rowcount > 0

    @guard("limpiar los recuerdos")
    def clear(self, category: str | None = None) -> int:
        with self.db.connect() as conn:
            if category:
                cursor = conn.execute(
                    "DELETE FROM memories WHERE category = ? AND user_id = ?",
                    (category, usuario_actual()),
                )
            else:
                # Sin filtro, "olvidalo todo" de un usuario borraria la
                # memoria de todos los demas.
                cursor = conn.execute(
                    "DELETE FROM memories WHERE user_id = ?", (usuario_actual(),)
                )
            return cursor.rowcount


class SQLiteUploadRepository(UploadRepository):
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _a_upload(row) -> Upload:
        return Upload(
            id=row["id"],
            nombre_original=row["nombre_original"],
            mime=row["mime"],
            familia=row["familia"],
            tamano=row["tamano"],
            creado_en=row["creado_en"],
            almacenamiento=row["almacenamiento"],
            espacio_id=row["espacio_id"] if "espacio_id" in row.keys() else None,
        )

    @guard("registrar el archivo subido")
    def add(self, upload: Upload) -> Upload:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO uploads "
                "(id, nombre_original, mime, familia, tamano, creado_en, almacenamiento, "
                " user_id, espacio_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (upload.id, upload.nombre_original, upload.mime, upload.familia,
                 upload.tamano, upload.creado_en, upload.almacenamiento, usuario_actual(),
                 espacio_actual()),
            )
        upload.espacio_id = espacio_actual()
        return upload

    @guard("recuperar el archivo subido")
    def get(self, upload_id: str) -> Upload | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM uploads WHERE id = ? AND user_id = ? AND espacio_id IS ?",
                (upload_id, usuario_actual(), espacio_actual()),
            ).fetchone()
            return self._a_upload(row) if row else None

    @guard("listar los archivos subidos")
    def list(self, limit: int = 100) -> list[Upload]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM uploads WHERE user_id = ? AND espacio_id IS ? "
                "ORDER BY creado_en DESC LIMIT ?",
                (usuario_actual(), espacio_actual(), limit),
            ).fetchall()
            return [self._a_upload(f) for f in filas]

    @guard("listar los archivos subidos de todos los espacios")
    def list_todos(self, limit: int = 100) -> list[Upload]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM uploads WHERE user_id = ? ORDER BY creado_en DESC LIMIT ?",
                (usuario_actual(), limit),
            ).fetchall()
            return [self._a_upload(f) for f in filas]

    @guard("eliminar el archivo subido")
    def delete(self, upload_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "DELETE FROM uploads WHERE id = ? AND user_id = ? AND espacio_id IS ?",
                (upload_id, usuario_actual(), espacio_actual()),
            ).rowcount > 0

    @guard("limpiar los archivos subidos caducados")
    def delete_older_than(self, momento: float) -> list[str]:
        with self.db.connect() as conn:
            ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM uploads WHERE creado_en < ? AND user_id = ?",
                    (momento, usuario_actual()),
                ).fetchall()
            ]
            if ids:
                conn.execute(
                    f"DELETE FROM uploads WHERE id IN ({','.join('?' * len(ids))})", ids
                )
        return ids


class SQLiteTaskRepository(TaskRepository):
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _a_task(row) -> Task:
        try:
            pasos = [TaskStep.from_dict(p) for p in json.loads(row["pasos"] or "[]")]
        except (ValueError, TypeError):
            # Un JSON corrupto no debe impedir ver la tarea: mejor sin pasos que
            # con una excepcion en mitad del listado.
            logger.warning("Pasos ilegibles en la tarea %s", row["id"])
            pasos = []

        return Task(
            id=row["id"],
            objetivo=row["objetivo"],
            estado=row["estado"],
            session_id=row["session_id"],
            pasos=pasos,
            resultado=row["resultado"],
            error=row["error"],
            intentos=row["intentos"],
            creado_en=row["creado_en"],
            actualizado_en=row["actualizado_en"],
        )

    @staticmethod
    def _pasos_json(task: Task) -> str:
        return json.dumps([p.to_dict() for p in task.pasos], ensure_ascii=False)

    @guard("crear la tarea")
    def create(self, task: Task) -> Task:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id, objetivo, estado, session_id, pasos, "
                "resultado, error, intentos, creado_en, actualizado_en, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.objetivo, task.estado, task.session_id,
                 self._pasos_json(task), task.resultado, task.error, task.intentos,
                 task.creado_en, task.actualizado_en, usuario_actual()),
            )
        return task

    @guard("recuperar la tarea")
    def get(self, task_id: str) -> Task | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, usuario_actual()),
            ).fetchone()
            return self._a_task(row) if row else None

    @guard("actualizar la tarea")
    def update(self, task: Task) -> Task | None:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET objetivo = ?, estado = ?, session_id = ?, pasos = ?, "
                "resultado = ?, error = ?, intentos = ?, actualizado_en = ? "
                "WHERE id = ? AND user_id = ?",
                (task.objetivo, task.estado, task.session_id, self._pasos_json(task),
                 task.resultado, task.error, task.intentos, task.actualizado_en,
                 task.id, usuario_actual()),
            )
            return task if cursor.rowcount > 0 else None

    @guard("listar las tareas")
    def list(
        self,
        session_id: str | None = None,
        estados: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[Task]:
        condiciones: list[str] = ["user_id = ?"]
        parametros: list = [usuario_actual()]

        if session_id is not None:
            condiciones.append("session_id = ?")
            parametros.append(session_id)
        if estados:
            condiciones.append(f"estado IN ({','.join('?' * len(estados))})")
            parametros.extend(estados)

        where = f"WHERE {' AND '.join(condiciones)}"
        parametros.append(limit)

        with self.db.connect() as conn:
            filas = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY creado_en DESC LIMIT ?", parametros
            ).fetchall()
            return [self._a_task(f) for f in filas]

    @guard("eliminar la tarea")
    def delete(self, task_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, usuario_actual()),
            ).rowcount > 0


class SQLitePlanRepository:
    """Persistencia de los planes (V1.6).

    Un plan pendiente de aprobación que se perdiera al reiniciar dejaría a la
    persona sin poder decidir sobre un trabajo que Morgan ya había preparado, así
    que sobrevive al proceso igual que las tareas.
    """

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _a_plan(row) -> Plan:
        try:
            pasos = json.loads(row["pasos"] or "[]")
        except (ValueError, TypeError):
            # Un JSON corrupto no debe impedir ver el plan. Sin pasos queda
            # inservible, pero visible y borrable, que es mejor que una
            # excepcion en mitad del listado.
            logger.warning("Pasos ilegibles en el plan %s", row["id"])
            pasos = []

        return Plan.from_dict({
            "id": row["id"],
            "objetivo": row["objetivo"],
            "estado": row["estado"],
            "pasos": pasos,
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "creado_en": row["creado_en"],
            "decidido_en": row["decidido_en"],
            "decidido_por": row["decidido_por"],
            "motivo_rechazo": row["motivo_rechazo"],
        })

    @staticmethod
    def _pasos_json(plan: Plan) -> str:
        # Se guardan los datos crudos, no el `to_dict` publicable: ese recorta
        # los argumentos y enmascara secretos para ensenarlos, y guardar eso
        # significaria ejecutar despues con argumentos truncados.
        return json.dumps([p.to_almacen() for p in plan.pasos], ensure_ascii=False)

    @guard("crear el plan")
    def create(self, plan: Plan) -> Plan:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO planes (id, objetivo, estado, pasos, session_id, "
                "task_id, creado_en, decidido_en, decidido_por, motivo_rechazo, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan.id, plan.objetivo, plan.estado, self._pasos_json(plan),
                 plan.session_id, plan.task_id, plan.creado_en, plan.decidido_en,
                 plan.decidido_por, plan.motivo_rechazo, usuario_actual()),
            )
        return plan

    @guard("recuperar el plan")
    def get(self, plan_id: str) -> Plan | None:
        with self.db.connect() as conn:
            fila = conn.execute(
                "SELECT * FROM planes WHERE id = ? AND user_id = ?",
                (plan_id, usuario_actual()),
            ).fetchone()
            return self._a_plan(fila) if fila else None

    @guard("actualizar el plan")
    def update(self, plan: Plan) -> Plan | None:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE planes SET objetivo = ?, estado = ?, pasos = ?, "
                "session_id = ?, task_id = ?, decidido_en = ?, decidido_por = ?, "
                "motivo_rechazo = ? WHERE id = ? AND user_id = ?",
                (plan.objetivo, plan.estado, self._pasos_json(plan),
                 plan.session_id, plan.task_id, plan.decidido_en,
                 plan.decidido_por, plan.motivo_rechazo,
                 plan.id, usuario_actual()),
            )
            return plan if cursor.rowcount > 0 else None

    @guard("listar los planes")
    def list(
        self,
        session_id: str | None = None,
        estados: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[Plan]:
        condiciones: list[str] = ["user_id = ?"]
        parametros: list = [usuario_actual()]

        if session_id is not None:
            condiciones.append("session_id = ?")
            parametros.append(session_id)
        if estados:
            condiciones.append(f"estado IN ({','.join('?' * len(estados))})")
            parametros.extend(estados)

        parametros.append(limit)

        with self.db.connect() as conn:
            filas = conn.execute(
                f"SELECT * FROM planes WHERE {' AND '.join(condiciones)} "
                "ORDER BY creado_en DESC LIMIT ?",
                parametros,
            ).fetchall()
            return [self._a_plan(f) for f in filas]

    @guard("eliminar el plan")
    def delete(self, plan_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute(
                "DELETE FROM planes WHERE id = ? AND user_id = ?",
                (plan_id, usuario_actual()),
            ).rowcount > 0


class SQLiteRepositoryFactory(RepositoryFactory):
    """Reúne los repositorios sobre una misma base SQLite."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()
        self._sessions = SQLiteSessionRepository(self.db)
        self._messages = SQLiteMessageRepository(self.db)
        self._memories = SQLiteMemoryRepository(self.db)
        self._uploads = SQLiteUploadRepository(self.db)
        self._tasks = SQLiteTaskRepository(self.db)
        self._planes = SQLitePlanRepository(self.db)

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
    def planes(self) -> SQLitePlanRepository:
        return self._planes

    def health(self) -> tuple[bool, str | None]:
        return self.db.healthy()
