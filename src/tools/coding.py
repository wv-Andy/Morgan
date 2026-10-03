"""
Herramientas para el Agente de Programación de Morgan (V0.7).

Permite inspeccionar proyectos, buscar código, aplicar parches quirúrgicos
y ejecutar suites de pruebas automatizadas con captura de trazas de error.
"""

import os
import re
import fnmatch
import subprocess
import time
from pathlib import Path
from typing import Any

from src.tools.base import Tool
from src.tools.shell import ps_args

IGNORED_DIRS = {".git", ".pytest_cache", "__pycache__", "node_modules", "venv", ".venv", "dist", "build"}

# Raíz del repositorio de Morgan (src/tools/coding.py -> src/tools -> src -> raíz)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class InspectProjectTool(Tool):
    """Analiza la estructura de un proyecto de software, dependencias y configuración."""

    @property
    def name(self) -> str:
        return "inspect_project"

    @property
    def description(self) -> str:
        return (
            "Inspecciona un proyecto de software: detecta el lenguaje principal, frameworks, "
            "archivos de configuración (package.json, pyproject.toml, requirements.txt) y directorios clave."
        )

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def category(self) -> str:
        return "coding"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta raíz del proyecto a inspeccionar. Por defecto '.' (directorio actual).",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Profundidad máxima de carpetas a explorar. Por defecto 3.",
                },
            },
            "required": [],
        }

    def execute(self, path: str = ".", max_depth: int = 3, **kwargs: Any) -> dict:
        try:
            target_path = Path(os.path.expandvars(os.path.expanduser(path))).resolve()

            if not target_path.exists() or not target_path.is_dir():
                return {"success": False, "data": None, "error": f"La ruta no es un directorio válido: {target_path}"}

            # Detectar archivos de configuración y dependencias
            configs = []
            structure = []
            extensions_count: dict[str, int] = {}

            base_depth = len(target_path.parts)

            for root, dirs, files in os.walk(target_path):
                # Filtrar carpetas ignoradas
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

                curr_path = Path(root)
                curr_depth = len(curr_path.parts) - base_depth

                if curr_depth > max_depth:
                    continue

                rel_dir = curr_path.relative_to(target_path)
                if str(rel_dir) != ".":
                    structure.append(f"{str(rel_dir)}/")

                for file in files:
                    ext = Path(file).suffix.lower()
                    if ext:
                        extensions_count[ext] = extensions_count.get(ext, 0) + 1

                    if file in ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "tsconfig.json"):
                        configs.append(str(rel_dir / file) if str(rel_dir) != "." else file)

                    if curr_depth <= 2 and len(structure) < 60:
                        structure.append(str(rel_dir / file) if str(rel_dir) != "." else file)

            # Determinar lenguaje predominante
            sorted_exts = sorted(extensions_count.items(), key=lambda x: x[1], reverse=True)
            tech_stack = []
            if ".py" in extensions_count:
                tech_stack.append("Python")
            if ".ts" in extensions_count or ".tsx" in extensions_count:
                tech_stack.append("TypeScript")
            if ".js" in extensions_count or ".jsx" in extensions_count:
                tech_stack.append("JavaScript")

            return {
                "success": True,
                "data": {
                    "project_root": str(target_path),
                    "detected_stack": tech_stack or ["Desconocido"],
                    "config_files": configs,
                    "extensions_summary": dict(sorted_exts[:8]),
                    "structure_preview": structure[:40],
                    "total_structure_items": len(structure),
                },
                "error": None,
            }

        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al inspeccionar proyecto: {e}"}


class SearchCodeTool(Tool):
    """Busca patrones, funciones, clases o texto en los archivos de código del proyecto."""

    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return (
            "Busca definiciones de funciones, clases, variables o texto específico en el código fuente. "
            "Permite filtrar por extensión (ej. '*.py', '*.ts')."
        )

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def category(self) -> str:
        return "coding"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Texto o patrón regex a buscar en los archivos.",
                },
                "path": {
                    "type": "string",
                    "description": "Carpeta donde buscar. Por defecto '.' (directorio actual).",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Patrón de archivos a incluir (ej. '*.py', '*.ts', '*.json'). Por defecto '*'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Máximo número de coincidencias a devolver. Por defecto 30.",
                },
            },
            "required": ["query"],
        }

    def execute(
        self,
        query: str,
        path: str = ".",
        file_pattern: str = "*",
        max_results: int = 30,
        **kwargs: Any,
    ) -> dict:
        try:
            root_path = Path(os.path.expandvars(os.path.expanduser(path))).resolve()

            if not root_path.exists():
                return {"success": False, "data": None, "error": f"Ruta inexistente: {root_path}"}

            pattern_re = re.compile(re.escape(query), re.IGNORECASE)
            matches = []

            for root, dirs, files in os.walk(root_path):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

                for file in files:
                    if not fnmatch.fnmatch(file, file_pattern):
                        continue

                    full_p = Path(root) / file
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                            for line_idx, line in enumerate(f, start=1):
                                if pattern_re.search(line):
                                    matches.append({
                                        "file": str(full_p.relative_to(root_path)),
                                        "line": line_idx,
                                        "content": line.strip(),
                                    })
                                    if len(matches) >= max_results:
                                        break
                    except Exception:
                        continue

                    if len(matches) >= max_results:
                        break
                if len(matches) >= max_results:
                    break

            return {
                "success": True,
                "data": {
                    "query": query,
                    "total_matches": len(matches),
                    "matches": matches,
                },
                "error": None,
            }

        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al buscar código: {e}"}


class PatchFileTool(Tool):
    """Aplica una modificación quirúrgica reemplazando un bloque exacto de código en un archivo."""

    @property
    def name(self) -> str:
        return "patch_file"

    @property
    def description(self) -> str:
        return (
            "Modifica quirúrgicamente un archivo de código reemplazando un bloque exacto de texto "
            "(target_content) por uno nuevo (replacement_content). Verifica que el bloque sea único."
        )

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def category(self) -> str:
        return "coding"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta del archivo a modificar.",
                },
                "target_content": {
                    "type": "string",
                    "description": "Texto o bloque de código exacto que se desea reemplazar.",
                },
                "replacement_content": {
                    "type": "string",
                    "description": "Nuevo texto o bloque de código que sustituirá al target.",
                },
            },
            "required": ["path", "target_content", "replacement_content"],
        }

    def execute(self, path: str, target_content: str, replacement_content: str, **kwargs: Any) -> dict:
        try:
            target_path = Path(os.path.expandvars(os.path.expanduser(path))).resolve()

            if not target_path.exists() or not target_path.is_file():
                return {"success": False, "data": None, "error": f"El archivo no existe: {target_path}"}

            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                original_text = f.read()

            occurrences = original_text.count(target_content)
            if occurrences == 0:
                return {
                    "success": False,
                    "data": None,
                    "error": "El bloque 'target_content' no fue encontrado en el archivo. Verifica los espacios y saltos de línea.",
                }

            if occurrences > 1:
                return {
                    "success": False,
                    "data": None,
                    "error": f"Se encontraron {occurrences} coincidencias del bloque 'target_content'. Proporciona más contexto circundante para garantizar unicidad.",
                }

            patched_text = original_text.replace(target_content, replacement_content, 1)

            with open(target_path, "w", encoding="utf-8") as f:
                f.write(patched_text)

            return {
                "success": True,
                "data": {
                    "file": str(target_path),
                    "message": "Archivo modificado quirúrgicamente con éxito.",
                    "lines_before": len(original_text.splitlines()),
                    "lines_after": len(patched_text.splitlines()),
                },
                "error": None,
            }

        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al aplicar parche: {e}"}


class RunTestsTool(Tool):
    """Ejecuta la suite de pruebas del proyecto y devuelve el diagnóstico detallado."""

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Ejecuta los tests automatizados del proyecto (ej. 'pytest', 'npm test') "
            "capturando el resultado, pruebas fallidas y trazas de error para diagnóstico."
        )

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def category(self) -> str:
        return "coding"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "test_command": {
                    "type": "string",
                    "description": "Comando de prueba a ejecutar (ej. 'pytest', 'pytest tests/test_agent.py', 'npm test'). Por defecto 'pytest'.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Directorio donde ejecutar las pruebas. Por defecto '.' (directorio actual).",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Tiempo límite en segundos antes de abortar. Por defecto 60.",
                },
            },
            "required": [],
        }

    def execute(self, test_command: str = "pytest", cwd: str = ".", timeout: int = 60, **kwargs: Any) -> dict:
        start_time = time.time()

        try:
            working_dir = Path(os.path.expandvars(os.path.expanduser(cwd))).resolve()

            if not working_dir.exists():
                return {"success": False, "data": None, "error": f"Directorio inexistente: {working_dir}"}

            # Si el comando es pytest y hay un venv (en el directorio de trabajo o en la
            # raíz de Morgan), se invoca 'python -m pytest' de ese entorno. Se usa el
            # intérprete y no pytest.exe: el lanzador .exe se rompe si el venv se mueve
            # de sitio, mientras que 'python -m' sigue funcionando.
            cmd = test_command
            venv_python = working_dir / "venv" / "Scripts" / "python.exe"
            if not venv_python.exists():
                venv_python = BASE_DIR / "venv" / "Scripts" / "python.exe"

            # El operador de llamada '&' es obligatorio: PowerShell trata una ruta
            # entrecomillada sin él como una cadena literal, no como un ejecutable.
            stripped = test_command.strip()
            if venv_python.exists() and (stripped == "pytest" or stripped.startswith("pytest ")):
                extra_args = stripped[len("pytest"):].strip()
                cmd = f'& "{venv_python}" -m pytest {extra_args}'.rstrip()

            proc = subprocess.run(
                ps_args(cmd),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(working_dir),
                encoding="utf-8",
                errors="replace",
            )
            elapsed = round(time.time() - start_time, 2)

            passed = proc.returncode == 0
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            return {
                "success": passed,
                "data": {
                    "passed": passed,
                    "returncode": proc.returncode,
                    "elapsed_seconds": elapsed,
                    "stdout": stdout[-3000:],  # Últimos 3000 caracteres de salida
                    "stderr": stderr[-1500:] if stderr else None,
                },
                "error": None if passed else "Las pruebas fallaron. Revisa 'stdout' y 'stderr' para diagnosticar y corregir el error.",
            }

        except subprocess.TimeoutExpired:
            return {"success": False, "data": None, "error": f"La suite de pruebas excedió el tiempo límite de {timeout}s."}
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al ejecutar tests: {e}"}
