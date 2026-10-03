"""
Sistema de permisos contextuales avanzado para Morgan (V0.9).

Implementa 5 niveles de riesgo:
- 🟢 SAFE      — Ejecución automática sin prompts.
- 🟢 LOW_RISK  — Operaciones de bajo impacto.
- 🟡 MODERATE  — Modificaciones locales controladas.
- 🟠 HIGH_RISK  — Modificaciones de código, tests o commits.
- 🔴 CRITICAL  — Comandos PowerShell arbitrarios, eliminación o finalización de procesos.
"""

import sys
from typing import Any
from rich.console import Console
from rich.prompt import Confirm

from src.config import get_settings
from src.tools.base import Tool, RiskLevel
from src.security.validator import CommandValidator, PathValidator
from src.security.audit import AuditLogger

console = Console()


def _detect_interactive() -> bool:
    """Determina si el proceso tiene una consola capaz de responder confirmaciones."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


# Herramientas que escriben en disco y los argumentos que contienen la ruta de destino.
# Toda ruta aquí listada pasa por PathValidator antes de pedir ninguna confirmación:
# proteger solo el borrado dejaba abierto el camino de sobrescritura del sistema.
WRITE_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "create_file": ("path",),
    "copy_file": ("dst",),
    "move_file": ("src", "dst"),
    "rename_file": ("src",),
    "patch_file": ("path",),
    "delete_file": ("path",),
}


class PermissionManager:
    """Gestiona los permisos y autorizaciones avanzadas del agente Morgan."""

    LEVELS = ("safe", "low_risk", "moderate", "high_risk", "sensitive", "critical")

    def __init__(
        self,
        audit_logger: AuditLogger | None = None,
        interactive: bool | None = None,
    ):
        self.moderate_mode = get_settings().moderate_permission_mode
        self.audit = audit_logger or AuditLogger()
        # Si no hay consola con la que confirmar, la política es denegar, nunca bloquear
        # esperando una entrada que no llegará (colgaría el servidor HTTP).
        self.interactive = _detect_interactive() if interactive is None else interactive

        self._blocked: set[str] = set()
        self._allowed: set[str] = set()
        # Sesión actual de autorizaciones temporales
        self._session_allowed: set[str] = set()

    @staticmethod
    def _sin_confirmaciones() -> bool:
        """Si quien pide esto no necesita confirmar.

        Se consulta al contexto de la petición, que ya trae el rol: preguntarle a
        la base una vez por herramienta ejecutada sería un coste absurdo.

        Nunca lanza. Si la identidad no está disponible —una prueba, un script
        suelto—, se responde que **sí hacen falta** confirmaciones, que es el lado
        seguro del error.
        """
        try:
            from src.identidad import rol_actual, usuario_actual
            from src.identidad.roles import propietario_con_cuenta, sin_confirmaciones

            rol = rol_actual()
            return sin_confirmaciones(rol) and propietario_con_cuenta(
                usuario_actual(), rol
            )
        except Exception:
            return False

    def check_permission(self, tool: Tool, tool_args: dict[str, Any] | None = None) -> bool:
        """
        Evalúa permisos aplicando validación de comandos, sandboxing y matriz de riesgo.
        """
        tool_name = tool.name
        risk = getattr(tool, "risk_level", "moderate")
        raw_level = getattr(tool, "permission_level", None)
        args = tool_args or {}

        # 0. Validar si el nivel de permiso o riesgo es desconocido
        if (raw_level is not None and raw_level not in self.LEVELS) or (risk not in self.LEVELS):
            self.audit.log(tool_name, str(risk), False, args, False, f"Nivel de permiso no reconocido: {raw_level or risk}")
            return False

        # 1. Validación de seguridad previa para comandos
        if tool_name == "execute_command" and "command" in args:
            is_safe, err_msg = CommandValidator.validate(args["command"])
            if not is_safe:
                console.print(f"  [red]✖ SEGURIDAD:[/red] {err_msg}")
                self.audit.log(tool_name, risk, False, args, False, err_msg)
                return False

        # 2. Validación de rutas protegidas para cualquier operación de escritura
        for arg_name in WRITE_PATH_ARGS.get(tool_name, ()):
            value = args.get(arg_name)
            if value and PathValidator.is_protected(value):
                err_msg = f"Ruta protegida por el sistema: '{value}' (argumento '{arg_name}')."
                console.print(f"  [red]✖ SEGURIDAD:[/red] {err_msg}")
                self.audit.log(tool_name, risk, False, args, False, err_msg)
                return False

        # 3. Herramienta explícitamente bloqueada
        if tool_name in self._blocked:
            console.print(f"  [red]✖[/red] Herramienta [bold]{tool_name}[/bold] está bloqueada.")
            self.audit.log(tool_name, risk, False, args, False, "Herramienta bloqueada")
            return False

        # 4. Herramienta permitida de forma explícita o por sesión
        if tool_name in self._allowed or tool_name in self._session_allowed:
            self.audit.log(tool_name, risk, True, args, True)
            return True

        # 4.b El propietario no espera confirmaciones.
        #
        # Va DESPUÉS de los validadores y del bloqueo explícito, y eso es lo que
        # hace que sea defendible: lo que se levanta es la pregunta «¿autorizas
        # esto?», que en un entorno sin consola no se puede formular y hoy se
        # resuelve denegando. Lo que sigue en pie es lo que protege del modelo,
        # no del dueño: `format C:` se rechaza igual, y todo queda auditado.
        if self._sin_confirmaciones():
            self.audit.log(tool_name, risk, True, args, True)
            return True

        # 5. Niveles SAFE y LOW_RISK
        if risk in (RiskLevel.SAFE.value, RiskLevel.LOW_RISK.value, "safe"):
            self.audit.log(tool_name, risk, True, args, True)
            return True

        # 6. Nivel MODERATE
        if risk in (RiskLevel.MODERATE.value, "moderate"):
            if self.moderate_mode == "auto":
                self.audit.log(tool_name, risk, True, args, True)
                return True
            authorized = self._request_permission(tool, args)
            self.audit.log(tool_name, risk, authorized, args, authorized)
            return authorized

        # 7. Niveles HIGH_RISK y CRITICAL / SENSITIVE
        if risk in (RiskLevel.HIGH_RISK.value, RiskLevel.CRITICAL.value, "sensitive", "critical"):
            authorized = self._request_permission(tool, args)
            self.audit.log(tool_name, risk, authorized, args, authorized)
            return authorized

        # Fallback de seguridad
        self.audit.log(tool_name, risk, False, args, False, "Nivel de riesgo desconocido")
        return False

    def _request_permission(self, tool: Tool, tool_args: dict[str, Any]) -> bool:
        # Sin consola interactiva no se puede pedir confirmación: se deniega de inmediato.
        # Preguntar aquí bloquearía el hilo leyendo de un stdin que nadie va a atender.
        if not self.interactive:
            return False

        level_icons = {
            "moderate": "🟡",
            "high_risk": "🟠",
            "critical": "🔴",
            "sensitive": "🔴",
        }
        risk = getattr(tool, "risk_level", tool.permission_level)
        icon = level_icons.get(risk, "⚠️")

        console.print()
        console.print(f"  {icon} [bold yellow]Permiso requerido ({risk.upper()})[/bold yellow]")
        console.print(f"     Herramienta: [bold cyan]{tool.name}[/bold cyan] (Dominio: {getattr(tool, 'category', 'general')})")
        console.print(f"     Descripción: {tool.description}")

        if tool_args:
            console.print("     [bold]Parámetros:[/bold]")
            for k, v in tool_args.items():
                val_str = str(v)
                if len(val_str) > 120:
                    val_str = val_str[:120] + "..."
                console.print(f"       • [dim]{k}:[/dim] [yellow]{val_str}[/yellow]")

        try:
            decision = Confirm.ask(
                "     ¿Autorizar ejecución?",
                default=False,
            )
        except (EOFError, OSError):
            return False

        # Si el usuario aprueba una operación moderada o de desarrollo, ofrecer recordar durante la sesión
        if decision and risk in ("moderate", "high_risk"):
            try:
                remember_session = Confirm.ask(
                    "     ¿Recordar autorización para esta herramienta durante la sesión activa?",
                    default=False,
                )
                if remember_session:
                    self._session_allowed.add(tool.name)
            except (EOFError, OSError):
                # Sin consola con la que preguntar —la API, un servicio, una
                # tuberia—. Se sigue SIN recordar la autorizacion, que es el
                # lado seguro: recordarla por defecto convertiria un fallo de
                # entrada en un permiso permanente.
                pass

        return decision

    def block_tool(self, tool_name: str) -> None:
        self._blocked.add(tool_name)
        self._allowed.discard(tool_name)
        self._session_allowed.discard(tool_name)

    def allow_tool(self, tool_name: str) -> None:
        self._allowed.add(tool_name)
        self._blocked.discard(tool_name)

    def reset_tool(self, tool_name: str) -> None:
        self._blocked.discard(tool_name)
        self._allowed.discard(tool_name)
        self._session_allowed.discard(tool_name)
