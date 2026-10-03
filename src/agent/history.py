"""
Puente entre el bucle del agente y el historial persistido.

Aísla dos decisiones que conviene tener en un solo sitio:

1. **Qué se recupera.** Al reanudar una sesión solo se cargan los mensajes de
   texto (`user` y `assistant`) y solo una ventana reciente. Cargar la
   conversación entera infla el contexto enviado al LLM en cada petición, que es
   justo el coste que hay que evitar. Y cargar mensajes de rol `tool` sin la
   llamada que los originó rompe el protocolo: los proveedores rechazan un
   resultado de herramienta huérfano.

2. **Qué pasa si la base falla.** Nunca debe impedir conversar. Un fallo de
   persistencia se registra y se sigue: Morgan pierde memoria de la conversación,
   no la capacidad de responder.
"""

import logging

from src.memory.db import MemoryStorageError
from src.memory.models import Message
from src.memory.repositories import RepositoryFactory
from src.models.base import ChatMessage

logger = logging.getLogger(__name__)

# Roles que se reinyectan al contexto al reanudar una conversación.
HYDRATABLE_ROLES = ("user", "assistant")


class ConversationHistory:
    """Carga y guarda el historial de una sesión."""

    def __init__(self, repositories: RepositoryFactory, window: int = 20, sync_queue=None):
        self.repositories = repositories
        self.window = window
        # Si hay cola, cada turno persistido deja constancia para subirlo después.
        # La escritura local no espera a la nube: solo anota la intención.
        self.sync_queue = sync_queue

    def load(self, session_id: str) -> list[ChatMessage]:
        """Devuelve la ventana reciente de una sesión, lista para el contexto."""
        if self.window <= 0:
            return []

        try:
            stored = self.repositories.messages.list_for_session(
                session_id, limit=self.window, roles=HYDRATABLE_ROLES
            )
        except MemoryStorageError:
            logger.warning("No se pudo recuperar el historial de '%s'", session_id, exc_info=True)
            return []

        # 'assistant' es el vocabulario de la base; el nucleo del agente usa 'model'.
        return [
            ChatMessage(role="model" if m.role == "assistant" else m.role, content=m.content)
            for m in stored
            if m.content
        ]

    def save_turn(self, session_id: str, messages: list[ChatMessage], title_hint: str | None = None) -> int:
        """Persiste los mensajes generados en un turno. Devuelve cuántos se guardaron."""
        pending = [self._to_record(session_id, m) for m in messages]
        pending = [m for m in pending if m is not None]

        if not pending:
            return 0

        try:
            # No se llama a `sessions.create()`: `add_many` ya se asegura de que
            # la sesion exista —lo hacen las dos implementaciones, SQLite y
            # Supabase— asi que hacerlo aqui era repetirlo.
            #
            # Y no era gratis. `create()` empieza con un `get()`, que en Supabase
            # son DOS viajes de red: la fila y, aparte, el recuento de mensajes,
            # que aqui no le importa a nadie. Mas el upsert. Total: tres viajes
            # por turno para dejar la base como ya la iba a dejar la linea
            # siguiente. Medido a ~220 ms cada uno.
            #
            # Lo encontro la instrumentacion contando las consultas por tabla.
            saved = self.repositories.messages.add_many(pending)
            self.repositories.sessions.touch(session_id, title=title_hint)
            self._enqueue_sync(session_id, pending, title_hint)
            return saved
        except MemoryStorageError:
            # Un fallo de persistencia no debe cortar la conversación.
            logger.warning("No se pudo persistir el turno de '%s'", session_id, exc_info=True)
            return 0

    def _enqueue_sync(self, session_id: str, mensajes: list, title_hint: str | None) -> None:
        """Anota el turno en la cola de sincronización, si está activa.

        Un fallo aquí no puede afectar a la conversación: el dato ya está
        guardado en local, que es la fuente de verdad.
        """
        if self.sync_queue is None:
            return

        try:
            from src.memory.sync import SyncOperation

            self.sync_queue.enqueue(
                SyncOperation.UPSERT_SESSION,
                {"id": session_id, "title": title_hint},
            )
            for m in mensajes:
                self.sync_queue.enqueue(
                    SyncOperation.INSERT_MESSAGE,
                    {
                        "session_id": m.session_id,
                        "role": m.role,
                        "content": m.content,
                        "tool_name": m.tool_name,
                        "tool_call_id": m.tool_call_id,
                    },
                )
        except Exception:
            logger.warning("No se pudo encolar el turno para sincronizar", exc_info=True)

    @staticmethod
    def _to_record(session_id: str, message: ChatMessage) -> Message | None:
        """Traduce un mensaje del agente a un registro persistible."""
        if message.role == "tool":
            # Se guarda para poder auditar la conversación, aunque no se reinyecte.
            return Message(
                session_id=session_id,
                role="tool",
                content=str(message.tool_result) if message.tool_result is not None else "",
                tool_name=message.tool_name,
                tool_call_id=message.tool_call_id,
            )

        if not message.content or message.guardar == "":
            # Un mensaje del modelo sin texto es solo el envoltorio de una llamada
            # a herramienta: no aporta nada al historial legible. Y uno con
            # `guardar=""` es una nota del núcleo para el modelo (4.3), no de la persona.
            return None

        return Message(
            session_id=session_id,
            role=message.role,
            content=message.guardar if message.guardar is not None else message.content,
            # El vinculo con los archivos vive aqui y no solo en el texto: asi
            # sobrevive a que el usuario edite el mensaje.
            metadata={"attachments": message.attachments} if message.attachments else {},
        )


def build_title(text: str, max_length: int = 60) -> str:
    """Título corto para una sesión, a partir del primer mensaje del usuario."""
    clean = " ".join((text or "").split())
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "…"
