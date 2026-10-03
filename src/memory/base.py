"""
Abstracción base para almacenamiento de memoria en Morgan.
"""

from abc import ABC, abstractmethod


class MemoryStorage(ABC):
    """Interfaz abstracta para motores de almacenamiento de memoria."""

    @abstractmethod
    def remember(self, key: str, value: str, category: str = "general") -> dict:
        """Almacena o actualiza un hecho o preferencia en la memoria permanente."""
        ...

    @abstractmethod
    def recall(self, query: str | None = None, category: str | None = None, limit: int = 100) -> list[dict]:
        """Recupera hechos almacenados filtrados opcionalmente por consulta o categoría."""
        ...

    @abstractmethod
    def forget(self, key: str) -> bool:
        """Elimina un hecho o preferencia por su clave."""
        ...

    @abstractmethod
    def clear(self, category: str | None = None) -> int:
        """Limpia hechos de una categoría o todos los hechos."""
        ...
