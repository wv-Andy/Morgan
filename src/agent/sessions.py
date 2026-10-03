"""
Gestión de sesiones de conversación para Morgan.

El agente mantiene el historial en memoria. Cuando se le habla desde la API, varias
peticiones pueden llegar a la vez desde el threadpool de FastAPI: sin aislamiento,
todas comparten la misma lista de mensajes y las conversaciones se mezclan.

Este módulo asocia cada `session_id` a su propio historial y protege cada sesión con
un lock, de forma que dos peticiones de la misma sesión se serializan y dos de
sesiones distintas no se ven entre sí.
"""

import threading
from collections import OrderedDict

from src.models.base import ChatMessage

DEFAULT_SESSION_ID = "default"


class ConversationSession:
    """Historial de una conversación y el lock que protege su acceso."""

    def __init__(self, session_id: str, max_messages: int = 200):
        self.session_id = session_id
        self.max_messages = max_messages
        self.messages: list[ChatMessage] = []
        self.lock = threading.RLock()
        # El historial persistido se carga una sola vez por sesion viva, no en
        # cada turno: consultar la base en cada peticion seria trabajo repetido.
        self.hydrated = False
        # Una conversacion temporal vive solo aqui: no se hidrata desde la base de
        # datos ni se persiste al terminar el turno. Se marca en la sesion y no en
        # cada llamada porque debe mantenerse durante toda la conversacion.
        self.temporary = False

    def trim(self) -> None:
        """Recorta el historial conservando los mensajes más recientes.

        El recorte respeta la coherencia del protocolo de herramientas: nunca deja un
        mensaje de rol 'tool' huérfano al principio del historial, porque un resultado
        sin su llamada asociada hace que los proveedores rechacen la petición.
        """
        if len(self.messages) <= self.max_messages:
            return

        excess = len(self.messages) - self.max_messages
        while excess < len(self.messages) and self.messages[excess].role == "tool":
            excess += 1
        self.messages = self.messages[excess:]

    def clear(self) -> None:
        with self.lock:
            self.messages.clear()


class SessionStore:
    """Almacén acotado de sesiones de conversación, seguro entre hilos."""

    def __init__(self, max_sessions: int = 50, max_messages_per_session: int = 200):
        self.max_sessions = max_sessions
        self.max_messages_per_session = max_messages_per_session
        self._sessions: "OrderedDict[str, ConversationSession]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str | None = None) -> ConversationSession:
        """Devuelve la sesión indicada, creándola si es la primera vez que se usa."""
        key = session_id or DEFAULT_SESSION_ID
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = ConversationSession(key, self.max_messages_per_session)
                self._sessions[key] = session
                # Descartar las sesiones más antiguas para acotar el uso de memoria.
                while len(self._sessions) > self.max_sessions:
                    self._sessions.popitem(last=False)
            else:
                self._sessions.move_to_end(key)
            return session

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
