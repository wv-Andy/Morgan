"""
Registro central de herramientas organizadas por dominios para Morgan (V0.8).
"""

from src.tools.base import Tool, ToolCategory


class ToolRegistry:
    """Registro modular de herramientas disponibles para el agente."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Registra una herramienta en el catálogo."""
        if tool.name in self._tools:
            raise ValueError(f"Ya existe una herramienta registrada con el nombre '{tool.name}'")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Retira una herramienta del catálogo. True si estaba registrada."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        """Obtiene una herramienta por su nombre."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Devuelve la lista completa de herramientas registradas."""
        return list(self._tools.values())

    def get_by_category(self, category: str | ToolCategory) -> list[Tool]:
        """Filtra herramientas pertenecientes a un dominio específico."""
        cat_val = category.value if isinstance(category, ToolCategory) else str(category).lower()
        return [t for t in self._tools.values() if t.category.lower() == cat_val]

    def get_categories(self) -> list[str]:
        """Devuelve las categorías de herramientas que tienen al menos 1 herramienta registrada."""
        cats = {t.category.lower() for t in self._tools.values()}
        return sorted(list(cats))

    def get_schemas(self) -> list[dict]:
        """Devuelve los esquemas de todas las herramientas en formato JSON Schema."""
        return [tool.get_schema() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        tools_str = ", ".join(self._tools.keys())
        return f"<ToolRegistry: [{tools_str}]>"
