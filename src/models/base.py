"""
Abstracción base para proveedores de Modelos de Lenguaje (LLM).

Permite desacoplar el núcleo de Morgan de cualquier API o proveedor específico
(Gemini, Groq, Ollama, Anthropic, OpenAI o simuladores de prueba).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.models.capabilities import SOLO_TEXTO, Capability


@dataclass
class ToolCallRequest:
    """Representa una solicitud estructurada de ejecución de herramienta por parte del LLM."""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class ChatMessage:
    """Representa un mensaje normalizado dentro del historial de conversación."""
    role: str  # "user" | "model" | "system" | "tool"
    content: str = ""
    tool_calls: list[ToolCallRequest] | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None  # Correlaciona el resultado con el tool_call que lo originó
    tool_result: dict[str, Any] | None = None
    raw_parts: Any = None  # Preserva las partes nativas del proveedor (ej. thought_signature de Gemini)
    # Imagen adjunta (V1.4): {"mime": str, "base64": str}. Va en un campo propio y
    # no incrustada en 'content' porque cada proveedor la codifica a su manera, y
    # meterla en el texto la convertiria en cientos de miles de caracteres de
    # contexto inutil para los que no la entienden.
    image: dict[str, str] | None = None
    # Archivos que el usuario adjunto a ESTE mensaje (V1.5). Se persisten en los
    # metadatos, no solo en el texto: pegar el identificador en el contenido
    # funciona, pero si el usuario borra esa linea Morgan pierde el vinculo y
    # tiene que adivinar cual de los archivos disponibles es el relevante.
    attachments: list[dict] | None = None
    # Lo que se guarda en la base cuando no es `content` (4.3). El turno que ejecuta un plan
    # aprobado le pega al mensaje de la persona el informe para el modelo («Morgan ya
    # ejecutó sus pasos… No los repitas…»); guardado así, al recargar la conversación la
    # persona lo veía en su propio mensaje. El modelo lo necesita; la base, no.
    # `""` es «no se guarda»: una nota del núcleo para el modelo, que tampoco pasa a los
    # turnos siguientes.
    guardar: str | None = None


@dataclass
class LLMResponse:
    """Respuesta normalizada de un proveedor LLM."""
    type: str  # "text" | "tool_call"
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    raw_parts: Any = None  # Preserva las partes nativas generadas por el modelo


class LLMProvider(ABC):
    """Clase base abstracta que debe implementar cualquier proveedor de LLM para Morgan."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Nombre o identificador del modelo en uso."""
        ...

    @property
    def capabilities(self) -> frozenset[Capability]:
        """Qué admite este proveedor como entrada.

        Por defecto **solo texto**, igual que `requires_local` en las herramientas:
        si alguien añade un proveedor y olvida declararlo, el fallo va del lado
        seguro. Nunca se le enviará una imagen a un modelo que no pueda leerla.
        """
        return SOLO_TEXTO

    def supports(self, capability: "Capability") -> bool:
        return capability in self.capabilities

    @abstractmethod
    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """
        Genera una respuesta a partir del historial de mensajes y herramientas disponibles.
        """
        ...
