"""
Pruebas unitarias para las herramientas del sistema de archivos (V0.2).
"""

import pytest
from pathlib import Path

from src.tools.filesystem import (
    ListFilesTool,
    ReadFileTool,
    SearchFilesTool,
    CreateFileTool,
    CopyFileTool,
    MoveFileTool,
    RenameFileTool,
    DeleteFileTool,
)


@pytest.fixture
def test_dir(tmp_path):
    """Crea una estructura de directorios temporal para pruebas."""
    sub = tmp_path / "subdir"
    sub.mkdir()

    f1 = tmp_path / "archivo1.txt"
    f1.write_text("Hola mundo desde archivo 1", encoding="utf-8")

    f2 = sub / "archivo2.py"
    f2.write_text("print('test')", encoding="utf-8")

    return tmp_path


class TestFilesystemTools:
    def test_permission_levels(self):
        assert ListFilesTool().permission_level == "safe"
        assert ReadFileTool().permission_level == "safe"
        assert SearchFilesTool().permission_level == "safe"
        assert CreateFileTool().permission_level == "moderate"
        assert CopyFileTool().permission_level == "moderate"
        assert MoveFileTool().permission_level == "moderate"
        assert RenameFileTool().permission_level == "moderate"
        assert DeleteFileTool().permission_level == "sensitive"

    def test_list_files(self, test_dir):
        tool = ListFilesTool()
        result = tool.execute(path=str(test_dir))
        assert result["success"] is True
        data = result["data"]
        assert data["total_items"] == 2
        names = [item["name"] for item in data["items"]]
        assert "archivo1.txt" in names
        assert "subdir" in names

    def test_read_file(self, test_dir):
        tool = ReadFileTool()
        f1_path = str(test_dir / "archivo1.txt")
        result = tool.execute(path=f1_path)
        assert result["success"] is True
        assert "Hola mundo desde archivo 1" in result["data"]["content"]
        assert result["data"]["total_lines"] == 1

    def test_read_nonexistent_file(self, test_dir):
        tool = ReadFileTool()
        result = tool.execute(path=str(test_dir / "inexistente.txt"))
        assert result["success"] is False
        assert "no existe" in result["error"].lower()

    def test_search_files(self, test_dir):
        tool = SearchFilesTool()
        result = tool.execute(query="*.py", path=str(test_dir), recursive=True)
        assert result["success"] is True
        assert result["data"]["total_matches"] == 1
        assert "archivo2.py" in result["data"]["matches"][0]["name"]

    def test_create_file(self, test_dir):
        tool = CreateFileTool()
        target = test_dir / "nuevo.txt"
        result = tool.execute(path=str(target), content="Contenido de prueba")
        assert result["success"] is True
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "Contenido de prueba"

    def test_create_file_no_overwrite(self, test_dir):
        tool = CreateFileTool()
        target = test_dir / "archivo1.txt"
        result = tool.execute(path=str(target), content="Sobrescribir?", overwrite=False)
        assert result["success"] is False
        assert "ya existe" in result["error"].lower()

    def test_copy_file(self, test_dir):
        tool = CopyFileTool()
        src = str(test_dir / "archivo1.txt")
        dst = str(test_dir / "copia_archivo1.txt")
        result = tool.execute(src=src, dst=dst)
        assert result["success"] is True
        assert Path(dst).exists()

    def test_move_file(self, test_dir):
        tool = MoveFileTool()
        src = str(test_dir / "archivo1.txt")
        dst = str(test_dir / "subdir" / "movido.txt")
        result = tool.execute(src=src, dst=dst)
        assert result["success"] is True
        assert not Path(src).exists()
        assert Path(dst).exists()

    def test_rename_file(self, test_dir):
        tool = RenameFileTool()
        src = str(test_dir / "archivo1.txt")
        result = tool.execute(src=src, new_name="renombrado.txt")
        assert result["success"] is True
        assert not Path(src).exists()
        assert (test_dir / "renombrado.txt").exists()

    def test_delete_file(self, test_dir):
        tool = DeleteFileTool()
        target = test_dir / "archivo1.txt"
        result = tool.execute(path=str(target))
        assert result["success"] is True
        assert not target.exists()

    def test_delete_protected_path(self):
        tool = DeleteFileTool()
        result = tool.execute(path=r"C:\Windows")
        assert result["success"] is False
        assert "protegida" in result["error"].lower()
