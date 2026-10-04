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

#: Los topes de la memoria de una cuenta (4.22, revisión de los límites por cuenta). Los
#: recuerdos los escribe el modelo (`remember_fact`) y la API: sin tope, un bucle o un script
#: llenaban la base sin que el cupo diario lo notara (cada turno puede guardar varios). Holgados
#: para cualquier uso normal: en el prompt solo van los 15 últimos.
MAX_RECUERDOS = 500
LARGO_MAXIMO_RECUERDO = 2000


class MemoriaLlena(ValueError):
    """No cabe un recuerdo nuevo, o es demasiado largo. El mensaje dice qué hacer."""

class MemoryManager:
    """Gestiona la memoria persistente del agente, integrando preferencias y perfil."""

    def __init__(self, storage: MemoryStorage | None = None):
        self.storage = storage or _memoria_local()

    def remember(self, key: str, value: str, category: str = "general") -> dict:
        return self.storage.remember(key=key, value=value, category=category)

    def comprobar_que_cabe(self, key: str, value: str) -> None:
        """Lanza `MemoriaLlena` si este recuerdo no puede guardarse. Actualizar uno que ya
        existe siempre cabe; uno nuevo, si la cuenta no tiene ya `MAX_RECUERDOS`."""
        if len(value or "") > LARGO_MAXIMO_RECUERDO:
            raise MemoriaLlena(f"Un recuerdo puede tener como mucho {LARGO_MAXIMO_RECUERDO} caracteres: "
                               "guarda lo esencial, o pásalo a un documento de conocimiento.")
        repositorio = getattr(self.storage, "repositorio", None)
        if repositorio is None or repositorio.get(key) is not None:
            return
        if len(repositorio.search(limit=MAX_RECUERDOS)) >= MAX_RECUERDOS:
            raise MemoriaLlena(f"Ya hay {MAX_RECUERDOS} recuerdos guardados, el máximo: olvida alguno "
                               "(Ajustes → Memoria) antes de guardar otro.")

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
