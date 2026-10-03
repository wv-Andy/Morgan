"""
Herramienta: system_info

Primera herramienta del agente. Obtiene información del sistema operativo,
CPU, memoria RAM, disco y entorno Python.

Nivel de permiso: 🟢 Seguro (solo lectura de información del sistema).
"""

import platform
import sys
from datetime import datetime, timezone

import psutil

from src.tools.base import Tool, ToolCategory


class SystemInfoTool(Tool):
    """Obtiene información del sistema operativo y hardware."""

    @property
    def name(self) -> str:
        return "system_info"

    @property
    def description(self) -> str:
        return (
            "Obtiene información detallada del sistema: "
            "sistema operativo, CPU, memoria RAM, disco y Python. "
            "Útil para diagnosticar el estado del computador."
        )

    @property
    def category(self) -> str:
        return ToolCategory.SYSTEM.value

    @property
    def permission_level(self) -> str:
        return "safe"

    def execute(self, **kwargs) -> dict:
        """Recopila y devuelve información del sistema."""
        try:
            # --- Sistema operativo ---
            os_info = {
                "system": platform.system(),
                "version": platform.version(),
                "release": platform.release(),
                "machine": platform.machine(),
                "hostname": platform.node(),
            }

            # --- CPU ---
            cpu_freq = psutil.cpu_freq()
            cpu_info = {
                "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True),
                "usage_percent": psutil.cpu_percent(interval=0.5),
                "frequency_mhz": round(cpu_freq.current) if cpu_freq else None,
            }

            # --- Memoria RAM ---
            mem = psutil.virtual_memory()
            memory_info = {
                "total_gb": round(mem.total / (1024**3), 2),
                "used_gb": round(mem.used / (1024**3), 2),
                "available_gb": round(mem.available / (1024**3), 2),
                "usage_percent": mem.percent,
            }

            # --- Disco ---
            disk = psutil.disk_usage("/")
            disk_info = {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "usage_percent": disk.percent,
            }

            # --- Python ---
            python_info = {
                "version": sys.version.split()[0],
                "executable": sys.executable,
            }

            # --- Uptime ---
            boot_time = datetime.fromtimestamp(
                psutil.boot_time(), tz=timezone.utc
            )
            now = datetime.now(tz=timezone.utc)
            uptime = now - boot_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes = remainder // 60

            return {
                "success": True,
                "data": {
                    "os": os_info,
                    "cpu": cpu_info,
                    "memory": memory_info,
                    "disk": disk_info,
                    "python": python_info,
                    "uptime": f"{hours}h {minutes}m",
                    "timestamp": now.isoformat(),
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e),
            }
