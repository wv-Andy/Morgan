"""
Tests para el sistema de herramientas.
"""

import sys
import os

# Agregar el directorio raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.base import Tool
from src.tools.system import SystemInfoTool
from src.tools.registry import ToolRegistry


# ============================================================
# Tests para SystemInfoTool
# ============================================================

class TestSystemInfoTool:
    """Tests para la herramienta system_info."""

    def setup_method(self):
        self.tool = SystemInfoTool()

    def test_tool_has_correct_name(self):
        assert self.tool.name == "system_info"

    def test_tool_has_description(self):
        assert len(self.tool.description) > 0

    def test_tool_is_safe(self):
        assert self.tool.permission_level == "safe"

    def test_execute_returns_success(self):
        result = self.tool.execute()
        assert result["success"] is True
        assert result["error"] is None

    def test_execute_returns_os_info(self):
        result = self.tool.execute()
        data = result["data"]
        assert "os" in data
        assert "system" in data["os"]
        assert "hostname" in data["os"]

    def test_execute_returns_cpu_info(self):
        result = self.tool.execute()
        data = result["data"]
        assert "cpu" in data
        assert "physical_cores" in data["cpu"]
        assert "usage_percent" in data["cpu"]
        assert isinstance(data["cpu"]["physical_cores"], int)

    def test_execute_returns_memory_info(self):
        result = self.tool.execute()
        data = result["data"]
        assert "memory" in data
        assert "total_gb" in data["memory"]
        assert data["memory"]["total_gb"] > 0

    def test_execute_returns_disk_info(self):
        result = self.tool.execute()
        data = result["data"]
        assert "disk" in data
        assert "total_gb" in data["disk"]
        assert data["disk"]["total_gb"] > 0

    def test_execute_returns_python_info(self):
        result = self.tool.execute()
        data = result["data"]
        assert "python" in data
        assert "version" in data["python"]

    def test_execute_returns_uptime(self):
        result = self.tool.execute()
        data = result["data"]
        assert "uptime" in data
        assert "h" in data["uptime"]

    def test_get_schema(self):
        schema = self.tool.get_schema()
        assert schema["name"] == "system_info"
        assert "description" in schema
        assert "parameters" in schema

    def test_repr(self):
        repr_str = repr(self.tool)
        assert "system_info" in repr_str
        assert "🟢" in repr_str


# ============================================================
# Tests para ToolRegistry
# ============================================================

class TestToolRegistry:
    """Tests para el registro de herramientas."""

    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_tool(self):
        tool = SystemInfoTool()
        self.registry.register(tool)
        assert len(self.registry) == 1

    def test_get_tool(self):
        tool = SystemInfoTool()
        self.registry.register(tool)
        retrieved = self.registry.get("system_info")
        assert retrieved is tool

    def test_get_nonexistent_tool(self):
        assert self.registry.get("nonexistent") is None

    def test_duplicate_registration_raises(self):
        tool = SystemInfoTool()
        self.registry.register(tool)
        try:
            self.registry.register(SystemInfoTool())
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_list_tools(self):
        tool = SystemInfoTool()
        self.registry.register(tool)
        tools_list = self.registry.list_tools()
        assert len(tools_list) == 1
        assert tools_list[0] is tool

    def test_get_schemas(self):
        self.registry.register(SystemInfoTool())
        schemas = self.registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "system_info"

    def test_contains(self):
        self.registry.register(SystemInfoTool())
        assert "system_info" in self.registry
        assert "nonexistent" not in self.registry

    def test_repr(self):
        self.registry.register(SystemInfoTool())
        repr_str = repr(self.registry)
        assert "system_info" in repr_str
