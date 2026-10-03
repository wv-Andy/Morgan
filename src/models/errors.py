"""
Traducción de fallos de los proveedores LLM a mensajes para la persona.

Los SDK de Groq y de Gemini lanzan excepciones cuyo `str()` es el cuerpo crudo de
la respuesta HTTP: un JSON con códigos internos, nombres de campos de la API y, a
veces, rutas del servidor. Eso llegaba entero a la ventana de chat. Un usuario que
pide «busca esto en internet» no debería leer

    400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Function call is
    missing a thought_signature in functionCall parts...'}}

El detalle técnico no se pierde: va al log de aplicación, donde sirve para
diagnosticar. Lo que cambia es qué se le enseña a quien está usando Morgan.
"""

import logging
import re

from src.models.capabilities import CapacidadNoDisponible, Capability

logger = logging.getLogger(__name__)

# Qué se le dice a la persona según lo que haya fallado. El orden importa: se
# aplica la primera regla que case.
_REGLAS: list[tuple[tuple[str, ...], tuple[int, ...], str]] = [
    (
        ("rate limit", "quota", "resource_exhausted", "too many requests"),
        (429,),
        "He alcanzado el límite de peticiones del modelo. Espera un momento y "
        "vuelve a intentarlo.",
    ),
    (
        ("api key", "unauthenticated", "permission denied", "invalid_api_key", "forbidden"),
        (401, 403),
        "La clave de acceso al modelo no es válida o ha caducado. Revisa las "
        "credenciales en la configuración.",
    ),
    (
        ("insufficient balance", "insufficient_quota", "payment", "billing"),
        (402,),
        "La cuenta del proveedor del modelo no tiene saldo disponible.",
    ),
    (
        ("unavailable", "overloaded", "high demand", "service_unavailable",
         "bad gateway", "internal server error"),
        (500, 502, 503),
        "El proveedor del modelo no está disponible en este momento. Suele ser "
        "temporal: inténtalo de nuevo en unos segundos.",
    ),
    (
        ("timeout", "timed out", "deadline exceeded", "read operation"),
        (408, 504),
        "El modelo ha tardado demasiado en responder y he cancelado la espera. "
        "Vuelve a intentarlo.",
    ),
    (
        ("connection", "network", "dns", "getaddrinfo", "ssl", "unreachable"),
        (),
        "No he podido conectar con el proveedor del modelo. Comprueba la "
        "conexión a internet.",
    ),
    (
        ("invalid_argument", "invalid argument", "bad request"),
        (400, 422),
        "El proveedor del modelo ha rechazado la petición. Ya lo he registrado; "
        "prueba a empezar una conversación nueva.",
    ),
]

_GENERICO = (
    "No he podido comunicarme con el modelo. El detalle técnico ha quedado "
    "registrado en el log."
)


def _codigo_http(exc: Exception) -> int | None:
    """Extrae el código de estado, mirando primero los atributos del SDK."""
    for atributo in ("status_code", "code", "http_status"):
        valor = getattr(exc, atributo, None)
        if isinstance(valor, int) and 100 <= valor <= 599:
            return valor

    # Ni Groq ni Gemini lo exponen siempre; en ese caso queda el texto.
    coincidencia = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(coincidencia.group(1)) if coincidencia else None


def describe_llm_error(exc: Exception) -> str:
    """Devuelve un mensaje apto para enseñar en la interfaz.

    Nunca incluye el texto original de la excepción: ese es justamente el que
    filtraba el cuerpo de la respuesta HTTP a la ventana de chat.
    """
    # Que ningún modelo configurado entienda imágenes no es un fallo del
    # proveedor: no hay nada que reintentar y el usuario necesita saber
    # exactamente eso. Su mensaje ya está escrito para leerse.
    if isinstance(exc, CapacidadNoDisponible):
        if exc.capacidad is Capability.VISION:
            return (
                "Ninguno de los modelos configurados entiende imágenes. "
                "Configura un proveedor con visión para poder analizarlas."
            )
        if exc.capacidad is Capability.AUDIO:
            return (
                "Ninguno de los modelos configurados procesa audio. "
                "Configura un proveedor de transcripción para poder usarlo."
            )
        return "Morgan no tiene ningún modelo capaz de atender esa petición."

    texto = str(exc).lower()
    codigo = _codigo_http(exc)

    for señales, codigos, mensaje in _REGLAS:
        if codigo in codigos or any(s in texto for s in señales):
            return mensaje

    return _GENERICO


def log_llm_error(exc: Exception, contexto: str = "") -> str:
    """Registra el fallo completo y devuelve el mensaje para la persona.

    Un único punto para las dos mitades: que el detalle no se pierda y que no se
    escape a la interfaz.
    """
    logger.error(
        "Fallo del proveedor LLM%s: %s: %s",
        f" ({contexto})" if contexto else "",
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    return describe_llm_error(exc)
