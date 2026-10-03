"""
Pruebas unitarias para Seguridad Avanzada, Sandboxing, Validación y Auditoría (V0.9).
"""

import pytest
from pathlib import Path
from src.security.validator import CommandValidator, PathValidator
from src.security.audit import AuditLogger
from src.security.permissions import PermissionManager
from src.tools.system import SystemInfoTool
from src.tools.terminal import ExecuteCommandTool
from src.tools.filesystem import DeleteFileTool


class TestSecurityAdvancedV09:
    def test_command_validator_dangerous_patterns(self):
        dangerous = [
            "format c:",
            "rmdir /s /q C:\\",
            "del /f /s /q C:\\",
            "reg delete HKLM\\Software",
            "shutdown /s /t 0",
        ]
        for cmd in dangerous:
            is_safe, err = CommandValidator.validate(cmd)
            assert is_safe is False
            assert "bloqueado" in err.lower()

    def test_command_validator_safe_patterns(self):
        safe_commands = [
            "Get-ChildItem",
            "python --version",
            "git status",
            "pytest tests/",
        ]
        for cmd in safe_commands:
            is_safe, err = CommandValidator.validate(cmd)
            assert is_safe is True
            assert err is None

    def test_path_validator_protected(self):
        assert PathValidator.is_protected("C:/Windows") is True
        assert PathValidator.is_protected("C:/Windows/System32") is True
        assert PathValidator.is_protected("C:\\") is True
        assert PathValidator.is_protected("C:/Program Files") is True

    def test_audit_logger(self, tmp_path):
        log_file = tmp_path / "audit_test.log"
        logger = AuditLogger(log_path=log_file)

        logger.log(
            tool_name="test_tool",
            risk_level="safe",
            authorized=True,
            args={"param": "valor", "MY_API_KEY": "secreto123"},
            success=True,
            duration_ms=45.2,
        )

        entries = logger.get_recent()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["tool"] == "test_tool"
        assert entry["authorized"] is True
        # El secreto debe haber sido sanitizado
        assert entry["args"]["MY_API_KEY"] == "********"

    def test_permission_manager_blocks_dangerous_command(self, tmp_path):
        logger = AuditLogger(log_path=tmp_path / "audit.log")
        pm = PermissionManager(audit_logger=logger)

        tool = ExecuteCommandTool()
        # Intentar ejecutar comando destructivo
        res = pm.check_permission(tool, tool_args={"command": "format c:"})
        assert res is False

        # Debe haberse registrado en la auditoría
        entries = logger.get_recent()
        assert len(entries) == 1
        assert entries[0]["authorized"] is False
        assert "bloqueado" in entries[0]["error"].lower()

    def test_permission_manager_session_memory(self, tmp_path):
        logger = AuditLogger(log_path=tmp_path / "audit.log")
        pm = PermissionManager(audit_logger=logger)

        # Si una herramienta se añade a sesión, no pide confirmación
        pm._session_allowed.add("execute_command")
        tool = ExecuteCommandTool()
        assert pm.check_permission(tool, tool_args={"command": "Get-Date"}) is True
