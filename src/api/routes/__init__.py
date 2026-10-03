"""
Módulo de rutas para Morgan API.
"""

from src.api.routes.health import router as health_router
from src.api.routes.chat import router as chat_router
from src.api.routes.tools import router as tools_router
from src.api.routes.memory import router as memory_router
from src.api.routes.audit import router as audit_router

__all__ = [
    "health_router",
    "chat_router",
    "tools_router",
    "memory_router",
    "audit_router",
]
