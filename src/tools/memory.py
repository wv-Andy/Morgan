"""
Herramientas de memoria para Morgan (V0.5).
Permiten que el LLM guarde, consulte y olvide datos explícitos del usuario.
"""

from typing import Any
from src.tools.base import Tool, ToolCategory
from src.memory.manager import MemoryManager


class RememberFactTool(Tool):
    """Guarda un hecho, preferencia o detalle del usuario en la memoria persistente."""

    def __init__(self, memory_manager: MemoryManager | None = None):
        self.memory = memory_manager or MemoryManager()

    @property
    def name(self) -> str:
        return "remember_fact"

    @property
    def description(self) -> str:
        return (
            "Guarda para siempre un hecho o preferencia de la persona o de sus "
            "proyectos."
        )

    @property
    def category(self) -> str:
        return ToolCategory.MEMORY.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def requires_local(self) -> bool:
        # No toca la máquina del usuario: opera sobre la base de datos o la red.
        return False

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Título corto ('nombre_proyecto')",
                },
                "value": {
                    "type": "string",
                    "description": "Lo que hay que recordar",
                },
                "category": {
                    "type": "string",
                    "description": "'preference', 'project', 'user' o 'general'",
                },
            },
            "required": ["key", "value"],
        }

    def execute(self, key: str, value: str, category: str = "general", **kwargs: Any) -> dict:
        from src.memory.manager import MemoriaLlena

        try:
            self.memory.comprobar_que_cabe(key, value)
        except MemoriaLlena as e:
            return {"success": False, "data": None, "error": str(e)}
        try:
            stored = self.memory.remember(key=key, value=value, category=category)
            return {
                "success": True,
                "data": {
                    "message": f"Hecho '{key}' guardado en memoria con éxito.",
                    "item": stored,
                },
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al guardar en memoria: {e}"}


class RecallMemoryTool(Tool):
    """Consulta recuerdos o hechos almacenados en la memoria persistente."""

    def __init__(self, memory_manager: MemoryManager | None = None):
        self.memory = memory_manager or MemoryManager()

    @property
    def name(self) -> str:
        return "recall_memory"

    @property
    def description(self) -> str:
        return "Busca en la memoria persistente hechos, preferencias o notas previamente recordadas."

    @property
    def category(self) -> str:
        return ToolCategory.MEMORY.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def requires_local(self) -> bool:
        # No toca la máquina del usuario: opera sobre la base de datos o la red.
        return False

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Palabra clave o término a buscar en la memoria (opcional).",
                },
                "category": {
                    "type": "string",
                    "description": "Filtrar por categoría (opcional): 'preference', 'project', 'user', 'general'.",
                },
            },
            "required": [],
        }

    def execute(self, query: str | None = None, category: str | None = None, **kwargs: Any) -> dict:
        try:
            results = self.memory.recall(query=query, category=category)
            return {
                "success": True,
                "data": {
                    "total_found": len(results),
                    "memories": results,
                },
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al consultar memoria: {e}"}


class ForgetFactTool(Tool):
    """Elimina un hecho o recuerdo de la memoria persistente."""

    def __init__(self, memory_manager: MemoryManager | None = None):
        self.memory = memory_manager or MemoryManager()

    @property
    def name(self) -> str:
        return "forget_fact"

    @property
    def description(self) -> str:
        return "Elimina permanentemente un hecho o preferencia recordada a partir de su clave."

    @property
    def category(self) -> str:
        return ToolCategory.MEMORY.value

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def requires_local(self) -> bool:
        # No toca la máquina del usuario: opera sobre la base de datos o la red.
        return False

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Clave del hecho a olvidar.",
                },
            },
            "required": ["key"],
        }

    def execute(self, key: str, **kwargs: Any) -> dict:
        try:
            deleted = self.memory.forget(key=key)
            if deleted:
                return {
                    "success": True,
                    "data": {"message": f"Se ha eliminado '{key}' de la memoria permanente."},
                    "error": None,
                }
            return {
                "success": False,
                "data": None,
                "error": f"No se encontró ningún recuerdo con la clave '{key}'.",
            }
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al olvidar hecho: {e}"}
