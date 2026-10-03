"""
MemoryManager — Capa de alto nivel para gestión de memoria en Morgan.
"""

import logging

from src.memory.base import MemoryStorage
from src.memory.db import MemoryStorageError

logger = logging.getLogger(__name__)

# Encabezado del bloque de memoria inyectado en el system prompt. Es el marcador que
# permite sustituir el bloque anterior en lugar de ir acumulando copias.
MEMORY_SUMMARY_HEADER = "## Memoria Persistente (Información recordada del usuario y proyectos):"


def _memoria_local() -> MemoryStorage:
    """La memoria sobre la capa de repositorios de SQLite.

    **Una sola implementación, la misma que en la nube** (V2.0.13). Hasta
    entonces el Morgan local usaba `SQLiteMemoryStorage`, con su propio SQL sobre
    la misma tabla, y no se comportaba igual que el repositorio:

    - olvidaba **por clave o por id numérico de la fila**, así que
      `forget('1')` borraba el recuerdo de la fila 1 aunque ninguna clave se
      llamara «1». `recall_memory` le enseña el `id` al modelo;
    - guardaba la hora con resolución de segundo, y dos recuerdos del mismo
      segundo salían en cualquier orden.

    Está medido y fijado en `tests/test_memoria_unificada.py`.

    La importación va aquí dentro porque `sqlite_repositories` arrastra las
    tareas y los planes, y este módulo lo importan las herramientas.
    """
    from src.memory.memoria_sobre_repositorio import sobre_sqlite

    return sobre_sqlite()


#: Cuántos recuerdos van en el prompt de cada turno.
RECUERDOS_EN_EL_PROMPT = 15

class MemoryManager:
    """Gestiona la memoria persistente del agente, integrando preferencias y perfil."""

    def __init__(self, storage: MemoryStorage | None = None):
        self.storage = storage or _memoria_local()

    def remember(self, key: str, value: str, category: str = "general") -> dict:
        return self.storage.remember(key=key, value=value, category=category)

    def recall(self, query: str | None = None, category: str | None = None) -> list[dict]:
        return self.storage.recall(query=query, category=category)

    def forget(self, key: str) -> bool:
        return self.storage.forget(key=key)

    def clear(self, category: str | None = None) -> int:
        return self.storage.clear(category=category)

    def health(self) -> tuple[bool, str | None]:
        """Comprueba que el almacenamiento responde. Devuelve (ok, error)."""
        db = getattr(self.storage, "db", None)
        if db is None:
            return True, None
        return db.healthy()

    def get_context_summary(self) -> str:
        """
        Genera un resumen compacto de hechos y preferencias memorizados
        para ser inyectado automáticamente en el System Prompt de Morgan.

        Un fallo de la base de datos no debe impedir que Morgan arranque: en ese
        caso se devuelve un resumen vacío y se registra el problema.
        """
        try:
            # Solo los que se usan (4.3): se pedían 100 en cada turno para quedarse con 15.
            memories = self.storage.recall(limit=RECUERDOS_EN_EL_PROMPT)
        except MemoryStorageError:
            logger.warning("No se pudo leer la memoria persistente para el prompt", exc_info=True)
            return ""

        if not memories:
            return ""

        lines = [MEMORY_SUMMARY_HEADER]
        for m in memories[:RECUERDOS_EN_EL_PROMPT]:
            cat = f"[{m['category'].upper()}]" if m.get("category") else ""
            lines.append(f"- {cat} {m['key']}: {m['value']}")

        return "\n".join(lines)
