"""
Herramientas de sistema de archivos para Morgan (V0.2).

Proporciona operaciones seguras y controladas sobre el sistema de archivos de Windows:
- list_files: 🟢 Seguro
- read_file: 🟢 Seguro
- search_files: 🟢 Seguro
- create_file: 🟡 Moderado
- copy_file: 🟡 Moderado
- move_file: 🟡 Moderado
- rename_file: 🟡 Moderado
- delete_file: 🔴 Sensible
"""

import os
import shutil
import fnmatch
from pathlib import Path
from datetime import datetime
from typing import Any

from src.tools.base import Tool, ToolCategory

# Carpetas críticas del sistema protegidas contra eliminación accidental.
# La lista vive en el validador de seguridad para no mantener dos copias.
from src.security.validator import PROTECTED_SYSTEM_PATHS as PROTECTED_SYSTEM_DIRS


# Carpetas de artefactos que nunca aportan resultados útiles en una búsqueda y que
# obligan a recorrer decenas de miles de ficheros (node_modules, venv...).
IGNORED_SEARCH_DIRS = {
    ".git", ".pytest_cache", "__pycache__", "node_modules",
    "venv", ".venv", "env", "dist", "build", ".mypy_cache", ".ruff_cache",
}


def _resolve_path(path_str: str) -> Path:
    """Resuelve una ruta expandiendo ~ y variables de entorno."""
    expanded = os.path.expandvars(os.path.expanduser(path_str))
    return Path(expanded).resolve()


def _format_size(size_bytes: int) -> str:
    """Convierte bytes a formato legible."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


class ListFilesTool(Tool):
    """Lista archivos y directorios en una ruta específica."""

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return (
            "Lista una carpeta: archivos y subcarpetas, con tamaño, fecha y tipo."
        )

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Carpeta",
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Mostrar los ocultos",
                },
            },
            "required": [],
        }

    def execute(self, path: str = ".", show_hidden: bool = False, **kwargs: Any) -> dict:
        try:
            target_path = _resolve_path(path)

            if not target_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta no existe: {target_path}",
                }

            if not target_path.is_dir():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta no es un directorio: {target_path}",
                }

            items = []
            for entry in target_path.iterdir():
                if not show_hidden and entry.name.startswith("."):
                    continue

                try:
                    stat = entry.stat()
                    is_dir = entry.is_dir()
                    size_bytes = 0 if is_dir else stat.st_size
                    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

                    items.append({
                        "name": entry.name,
                        "type": "directory" if is_dir else "file",
                        "size": _format_size(size_bytes) if not is_dir else "-",
                        "size_bytes": size_bytes,
                        "modified": modified,
                    })
                except (PermissionError, OSError):
                    items.append({
                        "name": entry.name,
                        "type": "directory" if entry.is_dir() else "file",
                        "size": "Acceso denegado",
                        "size_bytes": 0,
                        "modified": "-",
                    })

            # Ordenar: primero carpetas, luego archivos alfabéticamente
            items.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))

            return {
                "success": True,
                "data": {
                    "path": str(target_path),
                    "total_items": len(items),
                    "items": items,
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al listar archivos: {e}",
            }


class ReadFileTool(Tool):
    """Lee el contenido de un archivo de texto."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Lee un archivo de texto; se puede limitar el número de líneas y empezar en "
            "otra."
        )

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta del archivo a leer.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Como mucho (500)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Primera línea (desde 1)",
                },
            },
            "required": ["path"],
        }

    def execute(self, path: str, max_lines: int = 500, offset: int = 1, **kwargs: Any) -> dict:
        try:
            target_path = _resolve_path(path)

            if not target_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"El archivo no existe: {target_path}",
                }

            if not target_path.is_file():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta no corresponde a un archivo: {target_path}",
                }

            # Intentar decodificar como UTF-8 con fallback a Latin-1
            content_lines = []
            encoding_used = "utf-8"
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    content_lines = f.readlines()
            except UnicodeDecodeError:
                encoding_used = "latin-1"
                with open(target_path, "r", encoding="latin-1", errors="replace") as f:
                    content_lines = f.readlines()

            total_lines = len(content_lines)
            start_idx = max(0, offset - 1)
            end_idx = min(total_lines, start_idx + max_lines)
            selected_lines = content_lines[start_idx:end_idx]

            return {
                "success": True,
                "data": {
                    "path": str(target_path),
                    "total_lines": total_lines,
                    "lines_returned": len(selected_lines),
                    "offset": offset,
                    "is_truncated": end_idx < total_lines,
                    "encoding": encoding_used,
                    "content": "".join(selected_lines),
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al leer archivo: {e}",
            }


class SearchFilesTool(Tool):
    """Busca archivos o carpetas por patrón de nombre."""

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return (
            "Busca archivos y carpetas por nombre o patrón ('*.txt', 'informe*')."
        )

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Patrón ('*.txt')",
                },
                "path": {
                    "type": "string",
                    "description": "Dónde buscar",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "También en subcarpetas",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Como mucho (50)",
                },
            },
            "required": ["query"],
        }

    def execute(
        self,
        query: str,
        path: str = ".",
        recursive: bool = True,
        max_results: int = 50,
        **kwargs: Any,
    ) -> dict:
        try:
            root_path = _resolve_path(path)

            if not root_path.exists() or not root_path.is_dir():
                return {
                    "success": False,
                    "data": None,
                    "error": f"Ruta inválida para búsqueda: {root_path}",
                }

            pattern = query if any(c in query for c in "*?[]") else f"*{query}*"
            matches = []

            if recursive:
                for dirpath, dirnames, filenames in os.walk(root_path):
                    # Poda in-place: evita descender a carpetas de artefactos.
                    dirnames[:] = [d for d in dirnames if d not in IGNORED_SEARCH_DIRS]
                    for filename in filenames:
                        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                            full_p = Path(dirpath) / filename
                            matches.append({
                                "name": filename,
                                "path": str(full_p),
                                "type": "file",
                            })
                            if len(matches) >= max_results:
                                break
                    if len(matches) >= max_results:
                        break
            else:
                for entry in root_path.iterdir():
                    if fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                        matches.append({
                            "name": entry.name,
                            "path": str(entry.resolve()),
                            "type": "directory" if entry.is_dir() else "file",
                        })
                        if len(matches) >= max_results:
                            break

            return {
                "success": True,
                "data": {
                    "search_root": str(root_path),
                    "pattern": pattern,
                    "total_matches": len(matches),
                    "matches": matches,
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al buscar archivos: {e}",
            }


class CreateFileTool(Tool):
    """Crea un nuevo archivo con el contenido especificado."""

    @property
    def name(self) -> str:
        return "create_file"

    @property
    def description(self) -> str:
        return (
            "Crea un archivo nuevo con el contenido de texto proporcionado. "
            "Crea las carpetas intermedias si no existen."
        )

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta del archivo a crear.",
                },
                "content": {
                    "type": "string",
                    "description": "Contenido de texto a escribir en el archivo.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Si es True, sobrescribe el archivo si ya existe. Por defecto False.",
                },
            },
            "required": ["path", "content"],
        }

    def execute(self, path: str, content: str, overwrite: bool = False, **kwargs: Any) -> dict:
        try:
            target_path = _resolve_path(path)

            if target_path.exists() and not overwrite:
                return {
                    "success": False,
                    "data": None,
                    "error": (
                        f"El archivo ya existe: {target_path}. "
                        "Usa overwrite=True para sobrescribirlo explícitamente."
                    ),
                }

            # Asegurar directorios padres
            target_path.parent.mkdir(parents=True, exist_ok=True)

            with open(target_path, "w", encoding="utf-8") as f:
                bytes_written = f.write(content)

            return {
                "success": True,
                "data": {
                    "path": str(target_path),
                    "bytes_written": bytes_written,
                    "overwritten": target_path.exists() and overwrite,
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al crear archivo: {e}",
            }


class CopyFileTool(Tool):
    """Copia un archivo o carpeta a otra ubicación."""

    @property
    def name(self) -> str:
        return "copy_file"

    @property
    def description(self) -> str:
        return "Copia un archivo o directorio a una nueva ubicación de destino."

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Ruta de origen del archivo o carpeta.",
                },
                "dst": {
                    "type": "string",
                    "description": "Ruta de destino.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Si es True, permite sobrescribir si el destino existe. Por defecto False.",
                },
            },
            "required": ["src", "dst"],
        }

    def execute(self, src: str, dst: str, overwrite: bool = False, **kwargs: Any) -> dict:
        try:
            src_path = _resolve_path(src)
            dst_path = _resolve_path(dst)

            if not src_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta de origen no existe: {src_path}",
                }

            if dst_path.exists() and not overwrite:
                return {
                    "success": False,
                    "data": None,
                    "error": f"El destino ya existe: {dst_path}. Usa overwrite=True si deseas sobrescribir.",
                }

            # Si el destino es un directorio existente y copiamos un archivo, colocarlo dentro
            if dst_path.is_dir() and src_path.is_file():
                final_dst = dst_path / src_path.name
            else:
                final_dst = dst_path

            final_dst.parent.mkdir(parents=True, exist_ok=True)

            if src_path.is_dir():
                if final_dst.exists() and overwrite:
                    shutil.rmtree(final_dst)
                shutil.copytree(src_path, final_dst)
            else:
                shutil.copy2(src_path, final_dst)

            return {
                "success": True,
                "data": {
                    "src": str(src_path),
                    "dst": str(final_dst),
                    "type": "directory" if src_path.is_dir() else "file",
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al copiar: {e}",
            }


class MoveFileTool(Tool):
    """Mueve un archivo o carpeta a otra ubicación."""

    @property
    def name(self) -> str:
        return "move_file"

    @property
    def description(self) -> str:
        return "Mueve un archivo o directorio a una nueva ubicación."

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Ruta de origen a mover.",
                },
                "dst": {
                    "type": "string",
                    "description": "Ruta de destino.",
                },
            },
            "required": ["src", "dst"],
        }

    def execute(self, src: str, dst: str, **kwargs: Any) -> dict:
        try:
            src_path = _resolve_path(src)
            dst_path = _resolve_path(dst)

            if not src_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta de origen no existe: {src_path}",
                }

            dst_path.parent.mkdir(parents=True, exist_ok=True)
            result_path = shutil.move(str(src_path), str(dst_path))

            return {
                "success": True,
                "data": {
                    "src": str(src_path),
                    "dst": str(result_path),
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al mover: {e}",
            }


class RenameFileTool(Tool):
    """Renombra un archivo o carpeta."""

    @property
    def name(self) -> str:
        return "rename_file"

    @property
    def description(self) -> str:
        return "Renombra un archivo o directorio existente con un nuevo nombre."

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "moderate"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Ruta del archivo o carpeta a renombrar.",
                },
                "new_name": {
                    "type": "string",
                    "description": "Nuevo nombre (solo el nombre, sin ruta).",
                },
            },
            "required": ["src", "new_name"],
        }

    def execute(self, src: str, new_name: str, **kwargs: Any) -> dict:
        try:
            src_path = _resolve_path(src)

            if not src_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta no existe: {src_path}",
                }

            # Asegurarse de que new_name sea solo un nombre y no una ruta
            clean_name = Path(new_name).name
            if not clean_name:
                return {
                    "success": False,
                    "data": None,
                    "error": f"Nombre inválido: '{new_name}'",
                }

            target_path = src_path.parent / clean_name

            if target_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"Ya existe un elemento con el nombre '{clean_name}' en esa carpeta.",
                }

            src_path.rename(target_path)

            return {
                "success": True,
                "data": {
                    "old_path": str(src_path),
                    "new_path": str(target_path),
                    "new_name": clean_name,
                },
                "error": None,
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al renombrar: {e}",
            }


class DeleteFileTool(Tool):
    """Elimina un archivo o directorio (operación sensible)."""

    @property
    def name(self) -> str:
        return "delete_file"

    @property
    def description(self) -> str:
        return (
            "Elimina permanentemente un archivo o carpeta. "
            "Operación SENSIBLE que requiere confirmación explícita del usuario."
        )

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def permission_level(self) -> str:
        return "sensitive"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta del archivo o carpeta a eliminar.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Si es True, elimina una carpeta y todo su contenido. Por defecto False.",
                },
            },
            "required": ["path"],
        }

    def execute(self, path: str, recursive: bool = False, **kwargs: Any) -> dict:
        try:
            target_path = _resolve_path(path)

            if not target_path.exists():
                return {
                    "success": False,
                    "data": None,
                    "error": f"La ruta no existe: {target_path}",
                }

            # 1. Proteger la raíz de cualquier unidad (ej. C:\, D:\)
            if target_path == Path(target_path.anchor) or str(target_path).rstrip("/\\") in ("C:", "D:", "E:"):
                return {
                    "success": False,
                    "data": None,
                    "error": f"Operación denegada por seguridad: '{target_path}' es una raíz de unidad.",
                }

            # 2. Proteger directorios del sistema de Windows y su contenido
            for protected in PROTECTED_SYSTEM_DIRS:
                if target_path == protected or protected in target_path.parents:
                    return {
                        "success": False,
                        "data": None,
                        "error": f"Operación denegada por seguridad: '{target_path}' está dentro de una ruta protegida del sistema ({protected}).",
                    }

            if target_path.is_file():
                target_path.unlink()
                item_type = "file"
            elif target_path.is_dir():
                if recursive:
                    shutil.rmtree(target_path)
                    item_type = "directory (recursivo)"
                else:
                    target_path.rmdir()
                    item_type = "directory"
            else:
                return {
                    "success": False,
                    "data": None,
                    "error": f"Tipo de elemento no soportado para eliminación: {target_path}",
                }

            return {
                "success": True,
                "data": {
                    "deleted_path": str(target_path),
                    "type": item_type,
                },
                "error": None,
            }

        except OSError as e:
            return {
                "success": False,
                "data": None,
                "error": f"No se pudo eliminar el elemento (puede estar en uso o requerir recursive=True): {e}",
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"Error al eliminar: {e}",
            }
