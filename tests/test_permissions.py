"""
Tests para el sistema de permisos.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.base import Tool
from src.security.permissions import PermissionManager


# ============================================================
# Herramientas de prueba con diferentes niveles de permiso
# ============================================================

class SafeTool(Tool):
    @property
    def name(self):
        return "safe_tool"

    @property
    def description(self):
        return "A safe tool"

    @property
    def permission_level(self):
        return "safe"

    def execute(self, **kwargs):
        return {"success": True, "data": "ok", "error": None}


class ModerateTool(Tool):
    @property
    def name(self):
        return "moderate_tool"

    @property
    def description(self):
        return "A moderate tool"

    @property
    def permission_level(self):
        return "moderate"

    def execute(self, **kwargs):
        return {"success": True, "data": "ok", "error": None}


class SensitiveTool(Tool):
    @property
    def name(self):
        return "sensitive_tool"

    @property
    def description(self):
        return "A sensitive tool"

    @property
    def permission_level(self):
        return "sensitive"

    def execute(self, **kwargs):
        return {"success": True, "data": "ok", "error": None}


# ============================================================
# Tests para PermissionManager
# ============================================================

class TestPermissionManager:
    """Tests para el gestor de permisos."""

    def setup_method(self):
        # Usar modo "auto" para moderados en tests (sin input interactivo)
        os.environ["MODERATE_PERMISSION_MODE"] = "auto"
        self.pm = PermissionManager()

    def test_safe_tool_allowed(self):
        """Herramientas seguras siempre se permiten."""
        tool = SafeTool()
        assert self.pm.check_permission(tool) is True

    def test_moderate_tool_auto_mode(self):
        """Herramientas moderadas se permiten en modo auto."""
        self.pm.moderate_mode = "auto"
        tool = ModerateTool()
        assert self.pm.check_permission(tool) is True

    def test_blocked_tool_denied(self):
        """Herramientas bloqueadas se deniegan."""
        tool = SafeTool()
        self.pm.block_tool("safe_tool")
        assert self.pm.check_permission(tool) is False

    def test_explicitly_allowed_tool(self):
        """Herramientas explícitamente permitidas se aceptan sin importar el nivel."""
        tool = ModerateTool()
        self.pm.allow_tool("moderate_tool")
        assert self.pm.check_permission(tool) is True

    def test_block_overrides_allow(self):
        """Bloquear una herramienta anula el permiso explícito."""
        self.pm.allow_tool("safe_tool")
        self.pm.block_tool("safe_tool")
        tool = SafeTool()
        assert self.pm.check_permission(tool) is False

    def test_allow_overrides_block(self):
        """Permitir una herramienta anula el bloqueo."""
        self.pm.block_tool("safe_tool")
        self.pm.allow_tool("safe_tool")
        tool = SafeTool()
        assert self.pm.check_permission(tool) is True

    def test_reset_tool(self):
        """Reset elimina reglas explícitas."""
        self.pm.block_tool("safe_tool")
        self.pm.reset_tool("safe_tool")
        tool = SafeTool()
        # Después de reset, vuelve al comportamiento por nivel
        assert self.pm.check_permission(tool) is True

    def test_unknown_level_denied(self):
        """Niveles desconocidos se deniegan por seguridad."""

        class UnknownLevelTool(Tool):
            @property
            def name(self):
                return "unknown_tool"

            @property
            def description(self):
                return "Unknown level tool"

            @property
            def permission_level(self):
                return "unknown_level"

            def execute(self, **kwargs):
                return {"success": True, "data": "ok", "error": None}

        tool = UnknownLevelTool()
        assert self.pm.check_permission(tool) is False
