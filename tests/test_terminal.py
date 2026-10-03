"""
Pruebas unitarias para las herramientas de terminal y procesos (V0.3).
"""

import os
import pytest

from src.tools.terminal import (
    ExecuteCommandTool,
    GetProcessesTool,
    GetEnvironmentTool,
    KillProcessTool,
)


class TestTerminalTools:
    def test_permission_levels(self):
        assert ExecuteCommandTool().permission_level == "sensitive"
        assert GetProcessesTool().permission_level == "safe"
        assert GetEnvironmentTool().permission_level == "safe"
        assert KillProcessTool().permission_level == "sensitive"

    def test_execute_command_success(self):
        tool = ExecuteCommandTool()
        result = tool.execute(command="Write-Output 'Morgan PowerShell Test'")
        assert result["success"] is True
        assert "Morgan PowerShell Test" in result["data"]["stdout"]
        assert result["data"]["returncode"] == 0

    def test_execute_command_failure(self):
        tool = ExecuteCommandTool()
        result = tool.execute(command="ComandoTotalmenteInexistente123")
        assert result["success"] is False
        assert result["data"]["returncode"] != 0

    def test_execute_command_timeout(self):
        tool = ExecuteCommandTool()
        result = tool.execute(command="Start-Sleep -Seconds 4", timeout=1)
        assert result["success"] is False
        assert "tiempo límite" in result["error"].lower()

    def test_get_processes(self):
        tool = GetProcessesTool()
        result = tool.execute(limit=5)
        assert result["success"] is True
        data = result["data"]
        assert len(data["processes"]) > 0
        assert "pid" in data["processes"][0]
        assert "name" in data["processes"][0]

    def test_get_processes_filter(self):
        tool = GetProcessesTool()
        result = tool.execute(filter_name="python", limit=5)
        assert result["success"] is True
        for proc in result["data"]["processes"]:
            assert "python" in proc["name"].lower()

    def test_get_environment_all(self):
        tool = GetEnvironmentTool()
        result = tool.execute()
        assert result["success"] is True
        assert "PATH" in result["data"]["variables"] or "Path" in result["data"]["variables"]

    def test_get_environment_masks_secrets(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET_KEY", "super_secret_value")
        tool = GetEnvironmentTool()
        result = tool.execute(variable_name="MY_SECRET_KEY")
        assert result["success"] is True
        assert "super_secret_value" not in result["data"]["value"]
        assert "oculta" in result["data"]["value"].lower()

    def test_kill_process_protected(self):
        tool = KillProcessTool()
        result = tool.execute(name="explorer.exe")
        assert result["success"] is False
        assert "protegido" in result["error"].lower()

    def test_kill_process_no_args(self):
        tool = KillProcessTool()
        result = tool.execute()
        assert result["success"] is False
        assert "Debes especificar" in result["error"]
