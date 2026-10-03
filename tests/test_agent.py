"""
Tests para el agente central.

Nota: Los tests del agente no llaman al LLM real.
Solo verifican la inicialización y estructura.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.registry import ToolRegistry
from src.tools.system import SystemInfoTool
from src.security.permissions import PermissionManager


class TestAgentInit:
    """Tests de inicialización del agente (sin LLM)."""

    def test_registry_with_system_tool(self):
        """El registry se inicializa correctamente con SystemInfoTool."""
        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        assert len(registry) == 1
        assert "system_info" in registry

    def test_permission_manager_defaults(self):
        """El PermissionManager tiene defaults correctos."""
        pm = PermissionManager()
        assert pm.moderate_mode in ("ask", "auto")

    def test_system_tool_schema_format(self):
        """El schema de SystemInfoTool es válido para function calling."""
        tool = SystemInfoTool()
        schema = tool.get_schema()

        assert "name" in schema
        assert "description" in schema
        assert "parameters" in schema
        assert isinstance(schema["name"], str)
        assert isinstance(schema["description"], str)
        assert isinstance(schema["parameters"], dict)

    def test_tools_info_format(self):
        """La info de herramientas tiene el formato correcto."""
        registry = ToolRegistry()
        registry.register(SystemInfoTool())

        for tool in registry.list_tools():
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "permission_level")
            assert tool.permission_level in ("safe", "moderate", "sensitive")
