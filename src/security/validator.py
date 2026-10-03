"""
Validadores de seguridad para comandos y rutas del sistema (V0.9).
Implementa sandboxing básico y protección proactiva contra comandos destructivos.
"""

import re
import os
from pathlib import Path

# Comandos y patrones estrictamente prohibidos en PowerShell
DANGEROUS_COMMAND_PATTERNS = [
    r"format\s+[a-z]:",
    r"rmdir\s+/[sS]\s+/[qQ]\s+[a-zA-Z]:\\",
    r"del\s+/[fF]\s+/[sS]\s+/[qQ]\s+[a-zA-Z]:\\",
    r"remove-item\s+.*-recurse.*[a-zA-Z]:\\",
    r"reg\s+delete\s+hklm",
    r"bcdedit",
    r"diskpart",
    r"shutdown\s+/[sS]",
    r":\(\)\s*{\s*:\|:&\s*};:",
]

# Rutas del sistema que jamás deben ser manipuladas ni borradas
PROTECTED_SYSTEM_PATHS = {
    Path(os.environ.get("SystemRoot", "C:/Windows")).resolve(),
    Path(os.environ.get("ProgramFiles", "C:/Program Files")).resolve(),
    Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")).resolve(),
}


class CommandValidator:
    """Valida que un comando no contenga instrucciones destructivas para el sistema operativo."""

    @staticmethod
    def validate(command: str) -> tuple[bool, str | None]:
        """
        Verifica si un comando es seguro para su evaluación.

        Returns:
            (is_safe, error_message)
        """
        clean_cmd = command.strip().lower()

        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, clean_cmd):
                return False, f"Comando bloqueado por política de seguridad crítica: coincide con el patrón '{pattern}'."

        return True, None


class PathValidator:
    """Valida rutas de archivos para evitar Path Traversal y modificaciones fuera del sandbox."""

    @staticmethod
    def is_protected(target_path: Path | str) -> bool:
        """Determina si una ruta corresponde al sistema operativo protegido o a raíces de unidad."""
        try:
            resolved = Path(os.path.expandvars(os.path.expanduser(str(target_path)))).resolve()

            # Raíz de disco (ej: C:\)
            if resolved == Path(resolved.anchor) or str(resolved).rstrip("/\\") in ("C:", "D:", "E:"):
                return True

            for sys_dir in PROTECTED_SYSTEM_PATHS:
                if resolved == sys_dir or sys_dir in resolved.parents:
                    return True

            return False
        except Exception:
            return True
