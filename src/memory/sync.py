"""
Cola de sincronización local → nube.

Modelo **outbox**: toda escritura va primero a SQLite y, si la sincronización
está activada, deja constancia en `sync_queue`. Un trabajador la vacía cuando el
servicio remoto está disponible.

La regla que lo gobierna todo:

> **La escritura local nunca espera al remoto.**

Si Supabase no responde, el usuario no lo nota: su dato ya está guardado y la
operación queda pendiente. Es lo que permite que Morgan siga siendo útil sin
conexión (§5) y que la nube sea una mejora, no un requisito.

Alcance de la V1.3 (§11): detectar pendientes, registrar operaciones, preparar la
cola y **subir** cuando vuelva la conexión.

**Solo sube.** La bajada y fusión desde la nube se aplazó a la V1.4, nunca se hizo y
**se retiró el 2026-09-18** por decisión mía: la nube es la fuente de verdad, y el
agente local de la 3.0 trabajará contra ella (docs/decisions.md, docs/datos.md).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.memory.db import Database, MemoryStorageError, guard

logger = logging.getLogger(__name__)

# Si la cola crece sin límite estando mucho tiempo sin conexión, acabaría
# ocupando el disco. Se descartan las entradas más antiguas.
MAX_QUEUE_SIZE = 5000

# Tras este número de intentos fallidos se deja de reintentar una operación
# concreta: si falla siempre, es un dato problemático, no un corte de red.
MAX_ATTEMPTS = 5


class SyncOperation(str, Enum):
    UPSERT_SESSION = "upsert_session"
    INSERT_MESSAGE = "insert_message"
    UPSERT_MEMORY = "upsert_memory"
    DELETE_SESSION = "delete_session"
    DELETE_MEMORY = "delete_memory"


@dataclass
class SyncEntry:
    """Una operación pendiente de subir."""

    id: int
    operation: str
    payload: dict
    created_at: str
    attempts: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "operation": self.operation,
            "payload": self.payload,
            "created_at": self.created_at,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }


class SyncQueue:
    """Bitácora local de operaciones pendientes de sincronizar."""

    def __init__(self, database: Database):
        self.db = database

    @guard("registrar la operación pendiente")
    def enqueue(self, operation: SyncOperation, payload: dict) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sync_queue (operation, payload, created_at) VALUES (?, ?, ?)",
                (
                    operation.value,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                ),
            )
            self._trim(conn)
            return cursor.lastrowid

    def _trim(self, conn) -> None:
        """Acota el tamaño de la cola descartando lo más antiguo."""
        sobrantes = conn.execute(
            "SELECT COUNT(*) AS n FROM sync_queue"
        ).fetchone()["n"] - MAX_QUEUE_SIZE

        if sobrantes > 0:
            conn.execute(
                "DELETE FROM sync_queue WHERE id IN "
                "(SELECT id FROM sync_queue ORDER BY id ASC LIMIT ?)",
                (sobrantes,),
            )
            logger.warning(
                "La cola de sincronización superó %d entradas; se descartaron las %d más antiguas",
                MAX_QUEUE_SIZE,
                sobrantes,
            )

    @guard("consultar las operaciones pendientes")
    def pending(self, limit: int = 100) -> list[SyncEntry]:
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM sync_queue WHERE attempts < ? ORDER BY id ASC LIMIT ?",
                (MAX_ATTEMPTS, limit),
            ).fetchall()

        return [
            SyncEntry(
                id=f["id"],
                operation=f["operation"],
                payload=json.loads(f["payload"]) if f["payload"] else {},
                created_at=f["created_at"],
                attempts=f["attempts"],
                last_error=f["last_error"],
            )
            for f in filas
        ]

    @guard("contar las operaciones pendientes")
    def count(self, include_failed: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM sync_queue"
        params: tuple = ()
        if not include_failed:
            sql += " WHERE attempts < ?"
            params = (MAX_ATTEMPTS,)

        with self.db.connect() as conn:
            return conn.execute(sql, params).fetchone()["n"]

    @guard("marcar la operación como completada")
    def mark_done(self, entry_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM sync_queue WHERE id = ?", (entry_id,))

    @guard("registrar el fallo de la operación")
    def mark_failed(self, entry_id: int, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE sync_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                (error[:300], entry_id),
            )

    @guard("limpiar la cola")
    def clear(self) -> int:
        with self.db.connect() as conn:
            return conn.execute("DELETE FROM sync_queue").rowcount

    @guard("consultar las operaciones descartadas")
    def failed(self) -> list[SyncEntry]:
        """Operaciones que agotaron sus intentos. No se borran: son evidencia."""
        with self.db.connect() as conn:
            filas = conn.execute(
                "SELECT * FROM sync_queue WHERE attempts >= ? ORDER BY id ASC", (MAX_ATTEMPTS,)
            ).fetchall()

        return [
            SyncEntry(
                id=f["id"],
                operation=f["operation"],
                payload=json.loads(f["payload"]) if f["payload"] else {},
                created_at=f["created_at"],
                attempts=f["attempts"],
                last_error=f["last_error"],
            )
            for f in filas
        ]


class SyncWorker:
    """Vacía la cola contra el proveedor remoto.

    No corre en segundo plano de forma automática: se dispara explícitamente
    (al arrancar y cuando el servicio remoto vuelve a estar disponible). Un hilo
    sincronizando sin control es difícil de razonar y de probar; una llamada
    explícita es predecible.
    """

    def __init__(self, queue: SyncQueue, remote_factory, health=None):
        self.queue = queue
        self.remote = remote_factory
        self.health = health

    def run_once(self, limit: int = 100) -> dict:
        """Procesa hasta `limit` operaciones. Devuelve un resumen."""
        resumen = {"procesadas": 0, "completadas": 0, "fallidas": 0, "omitidas": 0}

        if self.remote is None:
            resumen["omitidas"] = self.queue.count()
            return resumen

        # Si el remoto no responde, no se gasta un intento por cada entrada:
        # se deja la cola intacta para el próximo ciclo.
        ok, error = self.remote.health()
        if not ok:
            logger.info("Sincronización aplazada: el remoto no responde (%s)", error)
            resumen["omitidas"] = self.queue.count()
            return resumen

        for entrada in self.queue.pending(limit=limit):
            resumen["procesadas"] += 1
            try:
                self._apply(entrada)
                self.queue.mark_done(entrada.id)
                resumen["completadas"] += 1
            except MemoryStorageError as exc:
                self.queue.mark_failed(entrada.id, str(exc))
                resumen["fallidas"] += 1
                logger.warning("Falló la operación %s (#%d): %s", entrada.operation, entrada.id, exc)

        if resumen["completadas"]:
            logger.info("Sincronizadas %d operaciones con la nube", resumen["completadas"])

        return resumen

    def _apply(self, entrada: SyncEntry) -> None:
        from src.memory.models import Message

        datos = entrada.payload
        operacion = entrada.operation

        if operacion == SyncOperation.UPSERT_SESSION.value:
            self.remote.sessions.create(
                datos["id"], title=datos.get("title"), metadata=datos.get("metadata")
            )
            if datos.get("title"):
                self.remote.sessions.touch(datos["id"], title=datos["title"])

        elif operacion == SyncOperation.INSERT_MESSAGE.value:
            self.remote.messages.add(Message(**datos))

        elif operacion == SyncOperation.UPSERT_MEMORY.value:
            self.remote.memories.upsert(
                datos["key"], datos["value"], datos.get("category", "general")
            )

        elif operacion == SyncOperation.DELETE_SESSION.value:
            self.remote.sessions.delete(datos["id"])

        elif operacion == SyncOperation.DELETE_MEMORY.value:
            self.remote.memories.delete(datos["key"])

        else:
            raise MemoryStorageError(f"Operación de sincronización desconocida: {operacion}")
