"""
Modelos y esquemas Pydantic para Morgan API (V1.0).
"""

from src import __version__

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# --- Chat Schemas ---
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Mensaje del usuario para el agente Morgan")
    session_id: Optional[str] = Field(None, description="Identificador de sesión opcional")
    temporary: bool = Field(
        False,
        description="Conversación temporal: no se guarda en la base de datos ni "
                    "puede escribir en la memoria permanente",
    )
    attachments: List[str] = Field(
        default_factory=list,
        description="Identificadores de archivos subidos que acompañan a este mensaje",
        max_length=10,
    )
    ejecutar_plan: Optional[str] = Field(
        None,
        max_length=80,
        description="Un plan de esta conversación que la persona acaba de aprobar: Morgan "
                    "ejecuta sus pasos, con los argumentos aprobados, y cuenta el resultado (4.0)",
    )


class ChatResponse(BaseModel):
    success: bool = True
    response: str
    model: str
    elapsed_seconds: float
    #: En qué se fue el tiempo, en milisegundos: `modelo`, `bd`,
    #: `herramienta.<nombre>` y `resto`. Y `proveedor`, que dice cuál de la
    #: cadena respondió de verdad — `model` devuelve la cadena entera.
    #:
    #: Va aquí además de en la cabecera porque es donde lo puede leer la web sin
    #: tener que exponer y parsear una cabecera.
    etapas: dict[str, object] | None = None


# --- Health & Status Schemas ---
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = __version__
    timestamp: str


class ComponentStatus(BaseModel):
    status: str
    details: Optional[str] = None


class ServiceStatusItem(BaseModel):
    """Estado de una dependencia concreta (V1.3 §8)."""
    name: str
    state: str  # available | degraded | unavailable | unknown
    detail: Optional[str] = None
    age_seconds: Optional[float] = None
    required_for: List[str] = Field(default_factory=list)


class CapabilityItem(BaseModel):
    """Qué puede hacer Morgan ahora mismo (V1.3 §14)."""
    name: str
    available: bool
    reason: Optional[str] = None


class SyncStatus(BaseModel):
    """Estado de la sincronización con la nube (V1.3 §11)."""
    enabled: bool = False
    pending: int = 0
    failed: int = 0
    remote_available: Optional[bool] = None


class StatusResponse(BaseModel):
    status: str
    mode: str
    tools_count: int
    domains_count: int
    components: Dict[str, ComponentStatus]
    environment: str = "local"  # local | cloud
    services: List[ServiceStatusItem] = Field(default_factory=list)
    capabilities: List[CapabilityItem] = Field(default_factory=list)
    sync: SyncStatus = Field(default_factory=SyncStatus)


# --- Tools Schemas ---
class ToolSchema(BaseModel):
    name: str
    category: str
    risk_level: str
    description: str
    parameters: Dict[str, Any]


class ToolListResponse(BaseModel):
    success: bool = True
    total: int
    categories: List[str]
    tools: List[ToolSchema]


class ToolExecuteRequest(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Argumentos para la herramienta")


class ToolExecuteResponse(BaseModel):
    success: bool
    tool: str
    risk_level: str
    authorized: bool
    data: Optional[Any] = None
    error: Optional[str] = None


# --- Memory Schemas ---
class MemoryItem(BaseModel):
    category: str
    key: str
    value: str
    updated_at: str


class MemoryCreateRequest(BaseModel):
    category: str = Field(default="general", description="Categoría del recuerdo")
    key: str = Field(..., min_length=1, description="Clave del recuerdo")
    value: str = Field(..., min_length=1, description="Valor a recordar")


class MemoryListResponse(BaseModel):
    success: bool = True
    count: int
    memories: List[MemoryItem]


# --- Audit Schemas ---
class AuditItem(BaseModel):
    timestamp: str
    tool: str
    risk_level: str
    authorized: bool
    arguments: Dict[str, Any]
    success: bool
    error: Optional[str] = None


class AuditListResponse(BaseModel):
    success: bool = True
    count: int
    records: List[AuditItem]


# --- Session & Message Schemas (V1.2) ---
class SessionItem(BaseModel):
    id: str
    title: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    message_count: int = 0
    archived: bool = False
    pinned: bool = False
    group_name: Optional[str] = None
    espacio_id: Optional[str] = None


class SessionUpdateRequest(BaseModel):
    """Cambios sobre una conversación. Lo que no se envía no se toca.

    Renombrar, archivar, fijar y agrupar son la misma operación sobre la misma
    fila, así que comparten endpoint. Un campo ausente (None) significa "déjalo
    como está"; para quitar una conversación de su grupo se envía `group_name`
    como cadena vacía.
    """

    title: Optional[str] = Field(None, max_length=200, description="Nuevo título")
    archived: Optional[bool] = Field(None, description="Archivar o desarchivar")
    pinned: Optional[bool] = Field(None, description="Fijar o dejar de fijar")
    group_name: Optional[str] = Field(
        None, max_length=100, description="Grupo; cadena vacía para quitarlo"
    )
    espacio_id: Optional[str] = Field(
        None, max_length=100,
        description="Mover a otro espacio de trabajo; cadena vacía para devolverla a General",
    )


class SessionCreateRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Identificador propio; si se omite se genera uno")
    title: Optional[str] = Field(None, description="Título descriptivo de la conversación")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionListResponse(BaseModel):
    success: bool = True
    count: int
    sessions: List[SessionItem]


class MessageItem(BaseModel):
    id: Optional[int] = None
    session_id: str
    role: str
    content: str = ""
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MessageListResponse(BaseModel):
    success: bool = True
    session_id: str
    count: int
    total: int
    messages: List[MessageItem]


# --- Error Standard Schemas ---
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail


# --- Ajustes del usuario (Etapa B) -------------------------------------------


class SettingsUpdateRequest(BaseModel):
    """Campos a guardar. Los que no se envian no se tocan; enviados vacios, se borran.

    Los limites no son arbitrarios: todo esto viaja en cada peticion al modelo, y
    un texto largo desplaza contexto util.
    """

    nombre: Optional[str] = Field(None, max_length=500)
    ocupacion: Optional[str] = Field(None, max_length=500)
    sobre_mi: Optional[str] = Field(None, max_length=1000)
    idioma: Optional[str] = Field(None, max_length=500)
    estilo_respuesta: Optional[str] = Field(None, max_length=500)


class SettingsResponse(BaseModel):
    success: bool = True
    settings: Dict[str, str] = Field(default_factory=dict)


# --- Archivos subidos (V1.4) --------------------------------------------------


class UploadItem(BaseModel):
    id: str
    nombre: str
    mime: str
    familia: str
    tamano: int
    creado_en: float
    espacio_id: Optional[str] = None


class UploadListResponse(BaseModel):
    success: bool = True
    count: int = 0
    uploads: List[UploadItem] = Field(default_factory=list)
    # Los limites viajan con la lista para que la interfaz pueda avisar antes de
    # subir, en lugar de dejar que el usuario espere a que se rechace.
    max_mb: int = 20
    ttl_hours: int = 24


# --- Tareas (V1.5) ------------------------------------------------------------


class TaskStepItem(BaseModel):
    orden: int
    descripcion: str
    estado: str
    herramienta: Optional[str] = None
    resultado: Optional[str] = None
    error: Optional[str] = None
    iniciado_en: Optional[float] = None
    terminado_en: Optional[float] = None
    # Si se comprobo el efecto (V1.7). None significa que NO se comprobo, que es
    # distinto de que saliera bien: la interfaz solo marca lo comprobado.
    verificacion: Optional[str] = None
    verificacion_motivo: Optional[str] = None


class TaskItem(BaseModel):
    id: str
    objetivo: str
    estado: str
    session_id: Optional[str] = None
    pasos: List[TaskStepItem] = Field(default_factory=list)
    resultado: Optional[str] = None
    error: Optional[str] = None
    intentos: int = 0
    creado_en: float = 0.0
    actualizado_en: float = 0.0
    # Derivados en el servidor: calcularlos tambien en la interfaz duplicaria la
    # regla en dos lenguajes, y acabarian discrepando.
    progreso: float = 0.0
    paso_actual: Optional[str] = None


class TaskListResponse(BaseModel):
    success: bool = True
    count: int = 0
    tasks: List[TaskItem] = Field(default_factory=list)
