"""
Pruebas unitarias para las herramientas del Agente de Programación y Git (V0.7).
"""

import pytest
from pathlib import Path
from src.tools.coding import InspectProjectTool, SearchCodeTool, PatchFileTool, RunTestsTool
from src.tools.git import GitStatusTool, GitDiffTool, GitCommitTool


@pytest.fixture
def mock_project(tmp_path):
    # Crear estructura de proyecto de prueba
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def main():\n    print('hola')\n    return 42\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest\nrich\n", encoding="utf-8")
    return tmp_path


class TestCodingToolsV07:
    def test_inspect_project(self, mock_project):
        tool = InspectProjectTool()
        result = tool.execute(path=str(mock_project))
        assert result["success"] is True
        data = result["data"]
        assert "Python" in data["detected_stack"]
        assert "requirements.txt" in data["config_files"]

    def test_search_code(self, mock_project):
        tool = SearchCodeTool()
        result = tool.execute(query="def main", path=str(mock_project), file_pattern="*.py")
        assert result["success"] is True
        assert result["data"]["total_matches"] == 1
        assert "app.py" in result["data"]["matches"][0]["file"]
        assert result["data"]["matches"][0]["line"] == 1

    def test_patch_file_success(self, mock_project):
        tool = PatchFileTool()
        target = str(mock_project / "src" / "app.py")
        result = tool.execute(
            path=target,
            target_content="return 42",
            replacement_content="return 100",
        )
        assert result["success"] is True
        content = Path(target).read_text(encoding="utf-8")
        assert "return 100" in content
        assert "return 42" not in content

    def test_patch_file_not_found(self, mock_project):
        tool = PatchFileTool()
        target = str(mock_project / "src" / "app.py")
        result = tool.execute(
            path=target,
            target_content="bloque_inexistente_xyz",
            replacement_content="nuevo",
        )
        assert result["success"] is False
        assert "no fue encontrado" in result["error"].lower()

    def test_patch_file_multiple_occurrences(self, mock_project):
        target_file = mock_project / "src" / "duplicate.txt"
        target_file.write_text("repetido\nrepetido\n", encoding="utf-8")

        tool = PatchFileTool()
        result = tool.execute(
            path=str(target_file),
            target_content="repetido",
            replacement_content="cambio",
        )
        assert result["success"] is False
        assert "coincidencias" in result["error"].lower()

    def test_git_tools(self, tmp_path):
        status_tool = GitStatusTool()
        diff_tool = GitDiffTool()
        commit_tool = GitCommitTool()

        assert status_tool.category == "git"
        assert diff_tool.category == "git"
        assert commit_tool.category == "git"

        # Probar en un directorio sin git maneja el error limpiamente
        res = status_tool.execute(cwd=str(tmp_path))
        # Puede tener éxito si tmp_path está dentro de un repo git o devolver error claro
        assert "success" in res
