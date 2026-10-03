"""
Pruebas unitarias para el registro y dominios de herramientas (V0.8).
"""

import pytest
from src.tools.registry import ToolRegistry
from src.tools.system import SystemInfoTool
from src.tools.filesystem import ListFilesTool
from src.tools.terminal import ExecuteCommandTool
from src.tools.coding import InspectProjectTool
from src.tools.git import GitStatusTool
from src.tools.base import ToolCategory


class TestToolDomainsV08:
    def test_tool_categories_and_domains(self):
        registry = ToolRegistry()
        registry.register(SystemInfoTool())
        registry.register(ListFilesTool())
        registry.register(ExecuteCommandTool())
        registry.register(InspectProjectTool())
        registry.register(GitStatusTool())

        categories = registry.get_categories()
        assert "coding" in categories
        assert "git" in categories

        coding_tools = registry.get_by_category(ToolCategory.CODING)
        assert len(coding_tools) == 1
        assert coding_tools[0].name == "inspect_project"

        git_tools = registry.get_by_category("git")
        assert len(git_tools) == 1
        assert git_tools[0].name == "git_status"
