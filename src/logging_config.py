"""
Configuración de logging de Morgan.

Hasta ahora el único diagnóstico eran los `console.print` de Rich (acoplados a la
CLI e invisibles al ejecutar el servidor) y el log de auditoría. Un fallo dentro de
un proveedor LLM o de la base de datos no dejaba ningún rastro consultable.

Este módulo instala un log rotativo en `logs/morgan.log` más salida por consola,
y filtra los valores que no deben acabar escritos en disco.
"""

import logging
import logging.handlers
import re
from pathlib import Path

# Patrones de secretos que nunca deben quedar registrados, aunque alguien los
# incluya por error en un mensaje de log.
_SECRET_PATTERNS = (
    re.compile(r"(gsk_[A-Za-z0-9]{10,})"),
    re.compile(r"(AIza[A-Za-z0-9_\-]{10,})"),
    re.compile(r"(sk-[A-Za-z0-9]{10,})"),
    re.compile(r"((?:api[_-]?key|token|password|secret)\s*[=:]\s*)(\S+)", re.IGNORECASE),
)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class SecretRedactingFilter(logging.Filter):
    """Sustituye credenciales por '***' antes de que el registro llegue a disco."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - un mensaje mal formado no debe romper el log
            return True

        redacted = message
        for pattern in _SECRET_PATTERNS:
            if pattern.groups == 2:
                redacted = pattern.sub(r"\1***", redacted)
            else:
                redacted = pattern.sub("***", redacted)

        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    console: bool = True,
) -> logging.Logger:
    """Instala el logging de Morgan. Es idempotente: repetirlo no duplica handlers."""
    root = logging.getLogger("src")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if getattr(root, "_morgan_configured", False):
        return root

    formatter = logging.Formatter(_LOG_FORMAT)
    redactor = SecretRedactingFilter()

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "morgan.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redactor)
            root.addHandler(file_handler)
        except OSError:
            # Sin permisos de escritura seguimos con la consola: el logging nunca
            # debe ser el motivo por el que Morgan no arranca.
            pass

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.WARNING)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redactor)
        root.addHandler(stream_handler)

    root.propagate = False
    root._morgan_configured = True  # type: ignore[attr-defined]
    return root
