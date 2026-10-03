"""
Herramientas de Terminal y Procesos para Morgan (V0.3).

Proporciona ejecución de comandos en PowerShell y monitoreo de procesos en Windows:
- execute_command: 🔴 Sensible (requiere confirmación)
- get_processes: 🟢 Seguro
- get_environment: 🟢 Seguro (con enmascaramiento de secretos)
- kill_process: 🔴 Sensible (requiere confirmación)
"""

import os
import time
import subprocess
from typing import Any
import psutil

from src.tools.base import Tool, ToolCategory
from src.tools.shell import ps_args

# Palabras clave para detectar variables de entorno sensibles y ocultarlas
SECRET_KEYWORDS = (
    "KEY",
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "CREDENTIAL",
    "AUTH",
    "APIKEY",
    "PRIVATE",
)

# Procesos críticos del sistema que nunca deben ser terminados
PROTECTED_PROCESSES = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "svchost.exe",
    "explorer.exe",
}


class ExecuteCommandTool(Tool):
    """Ejecuta un comando en PowerShell con timeout y captura de salida."""

    @property
    def name(self) -> str:
        return "execute_command"

    @property
    def description(self) -> str:
        return (
            "Ejecuta un comando o script en PowerShell de Windows. "
            "Operación SENSIBLE que requiere confirmación explícita del usuario."
        )

    @property
    def category(self) -> str:
        return ToolCategory.TERMINAL.value

    @property
    def permission_level(self) -> str:
        return "sensitive"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Comando a ejecutar en PowerShell.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Tiempo límite en segundos antes de abortar la ejecución. Por defecto 30.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Directorio de trabajo donde ejecutar el comando. Por defecto el actual.",
                },
            },
            "required": ["command"],
        }

    def execute(
        self,
        command: str,
        timeout: int = 30,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> dict:
        start_time = time.time()
        working_dir = os.path.abspath(cwd) if cwd else os.getcwd()

        if not os.path.exists(working_dir):
            return {
                "success": False,
                "data": None,
                "error": f"El directorio de trabajo no existe: {working_dir}",
            }

        try:
            # Ejecutar con PowerShell
            process = subprocess.run(
                ps_args(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
                encoding="utf-8",
                errors="replace",
            )

            elapsed = round(time.time() - start_time, 2)

            return {
                "success": process.returncode == 0,
                "data": {
                    "command": command,
                    "returncode": process.returncode,
                    "stdout": process.stdout.strip(),
                    "stderr": process.stderr.strip(),
                    "elapsed_seconds": elapsed,
                    "cwd": working_dir,
                },
                "error": process.stderr.strip() if process.returncode != 0 else None,
            }

        except subprocess.TimeoutExpired:
            elapsed = round(time.time() - start_time, 2)
            return {
                "success": False,
                "data": {
                    "command": command,
                    "elapsed_seconds": elapsed,
                },
                "error": f"El comando excedió el tiempo límite de {timeout} segundos.",
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al ejecutar comando: {e}",
            }


class GetProcessesTool(Tool):
    """Lista procesos en ejecución con métricas de uso de recursos."""

    @property
    def name(self) -> str:
        return "get_processes"

    @property
    def description(self) -> str:
        return (
            "Obtiene la lista de procesos activos en Windows, incluyendo PID, "
            "nombre, uso de CPU y memoria RAM. Permite filtrar y ordenar."
        )

    @property
    def category(self) -> str:
        return ToolCategory.TERMINAL.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filter_name": {
                    "type": "string",
                    "description": "Texto para filtrar procesos por nombre (ej. 'python', 'chrome').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Número máximo de procesos a devolver. Por defecto 20.",
                },
                "sort_by": {
                    "type": "string",
                    "description": "Criterio de orden: 'cpu' o 'memory'. Por defecto 'cpu'.",
                },
            },
            "required": [],
        }

    def execute(
        self,
        filter_name: str | None = None,
        limit: int = 20,
        sort_by: str = "cpu",
        **kwargs: Any,
    ) -> dict:
        try:
            processes = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
                try:
                    info = proc.info
                    name = info.get("name") or "Desconocido"

                    if filter_name and filter_name.lower() not in name.lower():
                        continue

                    mem_info = info.get("memory_info")
                    mem_rss_mb = round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0

                    processes.append({
                        "pid": info.get("pid"),
                        "name": name,
                        "cpu_percent": info.get("cpu_percent") or 0.0,
                        "memory_mb": mem_rss_mb,
                        "status": info.get("status", "unknown"),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Ordenar según el criterio elegido
            if sort_by.lower() == "memory":
                processes.sort(key=lambda p: p["memory_mb"], reverse=True)
            else:
                processes.sort(key=lambda p: p["cpu_percent"], reverse=True)

            top_processes = processes[:limit]

            return {
                "success": True,
                "data": {
                    "total_found": len(processes),
                    "returned": len(top_processes),
                    "sort_by": sort_by,
                    "processes": top_processes,
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al listar procesos: {e}",
            }


class GetEnvironmentTool(Tool):
    """Consulta variables de entorno del sistema de forma segura."""

    @property
    def name(self) -> str:
        return "get_environment"

    @property
    def description(self) -> str:
        return (
            "Obtiene variables de entorno de Windows. "
            "Enmascara automáticamente credenciales o secretos por seguridad."
        )

    @property
    def category(self) -> str:
        return ToolCategory.TERMINAL.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "variable_name": {
                    "type": "string",
                    "description": "Nombre específico de la variable de entorno a consultar (opcional).",
                },
            },
            "required": [],
        }

    def execute(self, variable_name: str | None = None, **kwargs: Any) -> dict:
        try:
            def _is_secret(key: str) -> bool:
                key_upper = key.upper()
                return any(sec in key_upper for sec in SECRET_KEYWORDS)

            if variable_name:
                val = os.environ.get(variable_name)
                if val is None:
                    return {
                        "success": False,
                        "data": None,
                        "error": f"La variable de entorno '{variable_name}' no existe.",
                    }

                display_val = "******** (oculta por seguridad)" if _is_secret(variable_name) else val
                return {
                    "success": True,
                    "data": {
                        "variable": variable_name,
                        "value": display_val,
                        "is_masked": _is_secret(variable_name),
                    },
                    "error": None,
                }

            # Listar todas sanitizadas
            env_data = {}
            for k, v in os.environ.items():
                if _is_secret(k):
                    env_data[k] = "******** (oculta por seguridad)"
                else:
                    env_data[k] = v

            return {
                "success": True,
                "data": {
                    "total_variables": len(env_data),
                    "variables": env_data,
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al consultar entorno: {e}",
            }


class KillProcessTool(Tool):
    """Termina un proceso en ejecución por PID o nombre (operación sensible)."""

    @property
    def name(self) -> str:
        return "kill_process"

    @property
    def description(self) -> str:
        return (
            "Termina un proceso en ejecución por su PID o por su nombre. "
            "Operación SENSIBLE que requiere confirmación explícita del usuario. "
            "Protege procesos críticos del sistema operativo."
        )

    @property
    def category(self) -> str:
        return ToolCategory.TERMINAL.value

    @property
    def permission_level(self) -> str:
        return "sensitive"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "ID del proceso (PID) a finalizar.",
                },
                "name": {
                    "type": "string",
                    "description": "Nombre del ejecutable del proceso (ej. 'notepad.exe').",
                },
            },
            "required": [],
        }

    def execute(
        self,
        pid: int | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> dict:
        if pid is None and not name:
            return {
                "success": False,
                "data": None,
                "error": "Debes especificar al menos un 'pid' o un 'name' de proceso a terminar.",
            }

        try:
            terminated = []

            if pid is not None:
                if not psutil.pid_exists(pid):
                    return {
                        "success": False,
                        "data": None,
                        "error": f"No existe ningún proceso con PID {pid}.",
                    }

                p = psutil.Process(pid)
                p_name = p.name().lower()

                if p_name in PROTECTED_PROCESSES:
                    return {
                        "success": False,
                        "data": None,
                        "error": f"Acción rechazada: '{p.name()}' (PID {pid}) es un proceso crítico protegido de Windows.",
                    }

                p.terminate()
                p.wait(timeout=3)
                terminated.append({"pid": pid, "name": p.name()})

            elif name:
                target_name = name.lower()
                if target_name in PROTECTED_PROCESSES:
                    return {
                        "success": False,
                        "data": None,
                        "error": f"Acción rechazada: '{name}' es un proceso crítico protegido de Windows.",
                    }

                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        if proc.info["name"] and proc.info["name"].lower() == target_name:
                            p_pid = proc.info["pid"]
                            p = psutil.Process(p_pid)
                            p.terminate()
                            terminated.append({"pid": p_pid, "name": proc.info["name"]})
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                if not terminated:
                    return {
                        "success": False,
                        "data": None,
                        "error": f"No se encontró ningún proceso activo con nombre '{name}'.",
                    }

            return {
                "success": True,
                "data": {
                    "terminated_count": len(terminated),
                    "processes": terminated,
                },
                "error": None,
            }

        except psutil.TimeoutExpired:
            return {
                "success": False,
                "data": None,
                "error": "El proceso no respondió a la solicitud de terminación en el tiempo previsto.",
            }
        except psutil.AccessDenied:
            return {
                "success": False,
                "data": None,
                "error": "Permiso denegado por Windows para finalizar este proceso (requiere privilegios elevados).",
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al finalizar proceso: {e}",
            }
