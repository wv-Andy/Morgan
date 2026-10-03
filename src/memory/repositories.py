"""
Interfaces de repositorio de Morgan.

El Core habla con estas interfaces, nunca con SQL ni con un SDK concreto. Añadir
un backend nuevo (PostgreSQL, Supabase) consiste en implementar estas tres clases
y registrarlo en la fábrica: nada por encima de esta capa debería cambiar.

    Morgan Core
         │
         ▼
    Repositorios (esta interfaz)
         │
         ▼
    Implementación por proveedor  ──►  SQLite (por defecto) / PostgreSQL / ...
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.memory.models import MemoryRecord, Message, Session, Upload

#: Valor de `espacio` en `SessionRepository.list` que significa «no filtres por
#: espacio». No puede ser `None`, porque `None` ya significa «General»: lo que no
#: está en ningún espacio. Un asterisco no choca con ningún identificador real,
#: que siempre empiezan por `esp-`.
CUALQUIER_ESPACIO = "*"

if TYPE_CHECKING:
    # Solo para las anotaciones. Importarlo en tiempo de ejecucion crearia un
    # ciclo: src.tasks importa este modulo para declarar su repositorio.
    from src.tasks.modelos import Task


class SessionRepository(ABC):
    """Gestiona las conversaciones de Morgan."""

    @abstractmethod
    def create(
        self,
        session_id: str,
        title: str | None = None,
        metadata: dict | None = None,
        *,
        espacio_id: str | None = None,
    ) -> Session:
        """Crea la sesión, o devuelve la existente si ya estaba.

        `espacio_id` solo se usa al crearla: una conversación existente no
        cambia de espacio por volver a pedirla. Para moverla está `update`.
        """

    @abstractmethod
    def get(self, session_id: str) -> Session | None:
        """Recupera una sesión, o None si no existe."""

    @abstractmethod
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
        """Lista conversaciones, las fijadas primero y luego por recientes.

        `espacio`: `CUALQUIER_ESPACIO` no filtra, `None` es «General» y un
        identificador es ese espacio.

        `archived=False` (el valor por defecto) devuelve solo las activas, que es
        lo que espera el historial principal; `True`, solo las archivadas; `None`,
        todas. `query` filtra por título, sin distinguir mayúsculas.
        """

    @abstractmethod
    def touch(self, session_id: str, title: str | None = None) -> None:
        """Marca la sesión como usada ahora; opcionalmente le pone título."""

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """Elimina la sesión y sus mensajes. True si existía."""

    @abstractmethod
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
        """Cambia los campos indicados; los omitidos se dejan como están.

        `espacio_id` sigue la misma regla que `group_name`: la cadena vacía la
        devuelve a «General» y `None` no la toca.

        Devuelve la sesión actualizada, o None si no existe. Es un único método en
        vez de uno por campo: renombrar, archivar, fijar y agrupar son la misma
        operación sobre la misma fila, y separarlos multiplicaría el código de los
        dos repositorios sin ganar nada.

        Para vaciar `group_name` se pasa la cadena vacía; `None` significa "no lo
        toques", que es distinto.
        """


class MessageRepository(ABC):
    """Gestiona el historial de mensajes."""

    @abstractmethod
    def add(self, message: Message) -> Message:
        """Persiste un mensaje y devuelve el registro con su id."""

    @abstractmethod
    def add_many(self, messages: list[Message]) -> int:
        """Persiste varios mensajes en una sola transacción."""

    @abstractmethod
    def list_for_session(self, session_id: str, limit: int = 100, roles: tuple[str, ...] | None = None) -> list[Message]:
        """Devuelve los mensajes **más recientes** de la sesión, en orden cronológico.

        Se limita a propósito: cargar una conversación entera en cada petición
        infla el contexto enviado al LLM y su coste.
        """

    @abstractmethod
    def count(self, session_id: str) -> int:
        """Número de mensajes de la sesión."""

    @abstractmethod
    def delete_for_session(self, session_id: str) -> int:
        """Elimina los mensajes de una sesión. Devuelve cuántos."""


class MemoryRepository(ABC):
    """Gestiona la memoria persistente (hechos y preferencias)."""

    @abstractmethod
    def upsert(self, key: str, value: str, category: str = "general") -> MemoryRecord:
        """Crea o actualiza un recuerdo por su clave."""

    @abstractmethod
    def get(self, key: str) -> MemoryRecord | None:
        """Recupera un recuerdo por su clave."""

    @abstractmethod
    def search(self, query: str | None = None, category: str | None = None, limit: int = 100) -> list[MemoryRecord]:
        """Busca recuerdos por texto libre y/o categoría."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Elimina un recuerdo. True si existía."""

    @abstractmethod
    def clear(self, category: str | None = None) -> int:
        """Elimina todos los recuerdos, o los de una categoría."""


class UploadRepository(ABC):
    """Indice de los archivos que el usuario ha subido.

    Solo los metadatos: qué archivos hay, cómo se llaman y dónde están sus bytes.
    El contenido vive en `AlmacenBytes`, porque no cabe cómodamente en una fila.
    """

    @abstractmethod
    def add(self, upload: "Upload") -> "Upload":
        """Registra un archivo recién guardado."""

    @abstractmethod
    def get(self, upload_id: str) -> "Upload | None":
        """Recupera uno, o None si no existe."""

    @abstractmethod
    def list(self, limit: int = 100) -> list["Upload"]:
        """Los más recientes primero, **del espacio de trabajo actual**."""

    @abstractmethod
    def list_todos(self, limit: int = 100) -> list["Upload"]:
        """Los de todos los espacios de quien pide.

        Existe por el cupo. Si el cupo contara solo el espacio actual, bastaría
        con repartir los archivos entre espacios para saltarse el límite.
        """

    @abstractmethod
    def delete(self, upload_id: str) -> bool:
        """Borra el registro. True si existía."""

    @abstractmethod
    def delete_older_than(self, momento: float) -> list[str]:
        """Borra los anteriores a ese instante y devuelve sus identificadores.

        Devuelve los ids, y no solo cuántos, porque quien llama debe poder borrar
        también sus bytes: si no, el índice queda limpio y el almacenamiento lleno.
        """


class TaskRepository(ABC):
    """Persistencia de las tareas (V1.5).

    Una tarea sobrevive al proceso a propósito: si Morgan se reinicia a mitad de
    un trabajo largo, lo hecho hasta ahí no debe perderse, y el usuario tiene que
    poder ver en qué se quedó.
    """

    @abstractmethod
    def create(self, task: "Task") -> "Task": ...

    @abstractmethod
    def get(self, task_id: str) -> "Task | None": ...

    @abstractmethod
    def update(self, task: "Task") -> "Task | None":
        """Guarda el estado actual. Devuelve None si la tarea ya no existe."""

    @abstractmethod
    def list(
        self,
        session_id: str | None = None,
        estados: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list["Task"]:
        """Las más recientes primero, opcionalmente filtradas."""

    @abstractmethod
    def delete(self, task_id: str) -> bool: ...


class RepositoryFactory(ABC):
    """Punto único donde se decide qué implementación se usa."""

    @property
    @abstractmethod
    def sessions(self) -> SessionRepository: ...

    @property
    @abstractmethod
    def messages(self) -> MessageRepository: ...

    @property
    @abstractmethod
    def memories(self) -> MemoryRepository: ...

    @property
    @abstractmethod
    def uploads(self) -> UploadRepository: ...

    @property
    @abstractmethod
    def tasks(self) -> TaskRepository: ...

    @abstractmethod
    def health(self) -> tuple[bool, str | None]:
        """Comprueba que el almacenamiento responde. Devuelve (ok, error)."""
