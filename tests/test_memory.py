"""
Pruebas unitarias para el sistema de memoria persistente con SQLite (V0.5).
"""

import pytest
from src.memory.memoria_sobre_repositorio import sobre_sqlite
from src.memory.manager import MemoryManager
from src.tools.memory import RememberFactTool, RecallMemoryTool, ForgetFactTool


@pytest.fixture
def memory_manager(tmp_path):
    db_file = tmp_path / "test_memory.db"
    storage = sobre_sqlite(db_path=db_file)
    return MemoryManager(storage=storage)


class TestMemorySystemV05:
    def test_remember_and_recall(self, memory_manager):
        memory_manager.remember("tema_preferido", "modo_oscuro", category="preference")
        memory_manager.remember("nombre_proyecto", "Morgan", category="project")

        all_memories = memory_manager.recall()
        assert len(all_memories) == 2

        filtered = memory_manager.recall(category="preference")
        assert len(filtered) == 1
        assert filtered[0]["key"] == "tema_preferido"
        assert filtered[0]["value"] == "modo_oscuro"

    def test_query_search(self, memory_manager):
        memory_manager.remember("version_actual", "v0.5", category="project")
        memory_manager.remember("autor", "Ana", category="user")

        results = memory_manager.recall(query="Ana")
        assert len(results) == 1
        assert results[0]["key"] == "autor"

    def test_update_existing_fact(self, memory_manager):
        memory_manager.remember("ciudad", "Madrid")
        memory_manager.remember("ciudad", "Barcelona")

        results = memory_manager.recall(query="ciudad")
        assert len(results) == 1
        assert results[0]["value"] == "Barcelona"

    def test_forget_fact(self, memory_manager):
        memory_manager.remember("dato_temporal", "123")
        assert len(memory_manager.recall()) == 1

        deleted = memory_manager.forget("dato_temporal")
        assert deleted is True
        assert len(memory_manager.recall()) == 0

        # Intentar borrar de nuevo debe retornar False
        assert memory_manager.forget("dato_temporal") is False

    def test_context_summary(self, memory_manager):
        memory_manager.remember("framework", "React", category="preference")
        summary = memory_manager.get_context_summary()
        assert "Memoria Persistente" in summary
        assert "React" in summary

    def test_memory_tools(self, memory_manager):
        rem_tool = RememberFactTool(memory_manager=memory_manager)
        recall_tool = RecallMemoryTool(memory_manager=memory_manager)
        forget_tool = ForgetFactTool(memory_manager=memory_manager)

        assert rem_tool.permission_level == "safe"
        assert recall_tool.permission_level == "safe"
        assert forget_tool.permission_level == "moderate"

        # 1. Guardar con herramienta
        res1 = rem_tool.execute(key="herramienta_test", value="funciona", category="general")
        assert res1["success"] is True

        # 2. Consultar con herramienta
        res2 = recall_tool.execute(query="herramienta_test")
        assert res2["success"] is True
        assert res2["data"]["total_found"] == 1

        # 3. Olvidar con herramienta
        res3 = forget_tool.execute(key="herramienta_test")
        assert res3["success"] is True

        # 4. Verificar que se borró
        res4 = recall_tool.execute(query="herramienta_test")
        assert res4["data"]["total_found"] == 0
