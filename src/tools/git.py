"""
Herramientas de integración con Git para Morgan (V0.7).

Permite consultar el estado del repositorio, ver diffs y crear commits
de puntos de control (checkpoints) para soporte de rollback y seguridad.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

from src.tools.base import Tool


class GitStatusTool(Tool):
    """Consulta el estado del repositorio Git (archivos modificados, staged, branch)."""

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return "Consulta el estado actual de Git: rama actual, archivos modificados, en staging o sin seguimiento."

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def category(self) -> str:
        return "git"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cwd": {
                    "type": "string",
                    "description": "Directorio del repositorio Git. Por defecto '.' (directorio actual).",
                },
            },
            "required": [],
        }

    def execute(self, cwd: str = ".", **kwargs: Any) -> dict:
        target_dir = Path(os.path.expandvars(os.path.expanduser(cwd))).resolve()
        try:
            proc = subprocess.run(
                ["git", "status", "--short", "--branch"],
                capture_output=True,
                text=True,
                cwd=str(target_dir),
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                return {"success": False, "data": None, "error": f"Error Git: {proc.stderr.strip()}"}

            # `--branch` imprime SIEMPRE una linea de cabecera («## master»),
            # asi que `proc.stdout` nunca esta vacio y el mensaje de «arbol
            # limpio» de abajo era codigo muerto: sin nada que confirmar, el
            # modelo recibia «## master» a secas y tenia que deducirlo.
            #
            # Se mira si queda algo APARTE de la cabecera, que es la pregunta
            # que se estaba intentando responder.
            salida = proc.stdout.strip()
            cambios = [l for l in salida.splitlines() if not l.startswith("##")]
            limpio = "Árbol de trabajo limpio: no hay nada que confirmar."

            return {
                "success": True,
                "data": {
                    "directory": str(target_dir),
                    "status_output": salida if cambios else (
                        f"{salida}" + chr(10) + limpio if salida else limpio
                    ),
                    "hay_cambios": bool(cambios),
                },
                "error": None,
            }
        except FileNotFoundError:
            return {"success": False, "data": None, "error": "Git no está instalado o no se encuentra en el PATH del sistema."}
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al ejecutar git status: {e}"}


class GitDiffTool(Tool):
    """Muestra las diferencias (diff) de cambios no confirmados en el código."""

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return "Muestra las modificaciones exactas de código pendientes de confirmación en Git (git diff)."

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def category(self) -> str:
        return "git"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Ruta de un archivo específico a inspeccionar (opcional).",
                },
                "cwd": {
                    "type": "string",
                    "description": "Directorio del repositorio Git. Por defecto '.' (directorio actual).",
                },
            },
            "required": [],
        }

    def execute(self, file_path: str | None = None, cwd: str = ".", **kwargs: Any) -> dict:
        target_dir = Path(os.path.expandvars(os.path.expanduser(cwd))).resolve()
        cmd = ["git", "diff"]
        if file_path:
            # El `--` separa opciones de rutas, y aqui no es cosmetico: sin el,
            # lo que el modelo escriba como «ruta» lo interpreta git como
            # OPCION. Medido: con file_path='--output=/donde/sea', esta
            # herramienta —declarada `safe`, sin confirmacion— escribia un
            # fichero en cualquier sitio donde el proceso pueda escribir, y
            # respondia «no hay cambios sin confirmar». Nada parecia raro.
            #
            # Es la misma familia que la fuga de las rutas de GitHub: un
            # argumento que compone el modelo dejando de ser dato y pasando a
            # ser instruccion. Con el `--`, un nombre que empiece por guion es
            # solo un nombre.
            cmd.extend(["--", file_path])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(target_dir),
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                return {"success": False, "data": None, "error": f"Error Git: {proc.stderr.strip()}"}

            diff_text = proc.stdout.strip()
            return {
                "success": True,
                "data": {
                    "has_changes": len(diff_text) > 0,
                    "diff": diff_text[:5000] if diff_text else "No hay cambios sin confirmar.",
                    "is_truncated": len(diff_text) > 5000,
                },
                "error": None,
            }
        except FileNotFoundError:
            return {"success": False, "data": None, "error": "Git no está disponible en el PATH."}
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al obtener git diff: {e}"}


class GitCommitTool(Tool):
    """Crea un commit de punto de control en Git con los cambios actuales."""

    @property
    def name(self) -> str:
        return "git_commit"

    @property
    def description(self) -> str:
        return (
            "Agrega cambios y crea un commit en Git con un mensaje descriptivo. "
            "Sirve para crear checkpoints de código antes de refactorizar."
        )

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def category(self) -> str:
        return "git"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Mensaje descriptivo del commit (ej. 'checkpoint: refactorizar base.py').",
                },
                "cwd": {
                    "type": "string",
                    "description": "Directorio del repositorio Git. Por defecto '.' (directorio actual).",
                },
            },
            "required": ["message"],
        }

    def execute(self, message: str, cwd: str = ".", **kwargs: Any) -> dict:
        target_dir = Path(os.path.expandvars(os.path.expanduser(cwd))).resolve()
        try:
            # 1. git add .
            add_proc = subprocess.run(
                ["git", "add", "."],
                capture_output=True,
                text=True,
                cwd=str(target_dir),
                encoding="utf-8",
                errors="replace",
            )
            if add_proc.returncode != 0:
                return {"success": False, "data": None, "error": f"Error en git add: {add_proc.stderr.strip()}"}

            # 2. git commit -m
            commit_proc = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True,
                text=True,
                cwd=str(target_dir),
                encoding="utf-8",
                errors="replace",
            )
            if commit_proc.returncode != 0:
                # Puede ser que no haya cambios para commitear
                if "nothing to commit" in commit_proc.stdout.lower():
                    return {"success": True, "data": {"message": "No había cambios nuevos para confirmar en Git.", "committed": False}}
                return {"success": False, "data": None, "error": f"Error en git commit: {commit_proc.stderr.strip()}"}

            return {
                "success": True,
                "data": {
                    "message": f"Commit creado con éxito: '{message}'",
                    "committed": True,
                    "output": commit_proc.stdout.strip(),
                },
                "error": None,
            }
        except FileNotFoundError:
            return {"success": False, "data": None, "error": "Git no está disponible en el PATH."}
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error en git commit: {e}"}
