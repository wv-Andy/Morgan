"""
Entidades de la capa de persistencia de Morgan.

Son dataclases planas, sin dependencias de SQLite ni de ningún proveedor: el Core
trabaja con estos objetos y no con filas de base de datos. Cambiar de motor no
debería obligar a tocar nada por encima de los repositorios.
"""

from dataclasses import dataclass, field
from typing import Any

# Los tres tipos de memoria del sistema, separados a propósito (ver docs/datos.md):
#   - sesión:     el contexto vivo de una conversación
#   - historial:  los mensajes ya intercambiados, persistidos
#   - persistente: hechos que sobreviven a la sesión


@dataclass
class Session:
    """Una conversación de Morgan."""

    id: str
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    message_count: int = 0
    # Organizacion del historial (V1.4). Son columnas propias y no claves dentro
    # de 'metadata' porque se filtran y ordenan en cada listado.
    archived: bool = False
    pinned: bool = False
    group_name: str | None = None
    # El espacio de trabajo (V2.2). None es «General». `group_name` se conserva
    # porque hay filas que lo tienen, pero ya no se escribe: los grupos se
    # convirtieron en espacios al migrar.
    espacio_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "message_count": self.message_count,
            "archived": self.archived,
            "pinned": self.pinned,
            "group_name": self.group_name,
            "espacio_id": self.espacio_id,
        }


@dataclass
class Message:
    """Un mensaje dentro de una sesión."""

    session_id: str
    role: str
    content: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None
    id: int | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class MemoryRecord:
    """Un hecho o preferencia que sobrevive entre sesiones."""

    key: str
    value: str
    category: str = "general"
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Upload:
    """Un archivo subido por el usuario. Solo los metadatos."""

    id: str
    nombre_original: str
    mime: str
    familia: str
    tamano: int
    creado_en: float
    # Dónde están sus bytes: 'disco' o 'supabase'. Se persiste porque el mismo
    # índice puede contener archivos guardados en sitios distintos si el entorno
    # cambia entre reinicios.
    almacenamiento: str = "disco"
    # El espacio de trabajo al que pertenece (V2.2). None es «General».
    espacio_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre_original,
            "mime": self.mime,
            "familia": self.familia,
            "tamano": self.tamano,
            "creado_en": self.creado_en,
            "espacio_id": self.espacio_id,
        }
