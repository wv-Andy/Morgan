"""
Una sola memoria persistente, igual en tu equipo y en la nube (V2.0.13).

**Por qué existe.** Había dos implementaciones de la memoria sobre la misma tabla
`memories` de SQLite, cada una con su propio SQL:

- `SQLiteMemoryStorage`, la que usaba el agente **en local**;
- `SQLiteMemoryRepository`, la capa de repositorios, que es el mismo contrato que
  cumple Supabase y lo que ya usa **la nube** a través de `MemoriaSobreRepositorio`.

La revisión arquitectónica de la 2.0.11 las dejó sin unificar porque no se
comportaban igual, y pidió fijar el comportamiento con una prueba antes de tocar
nada. Al medirlo, una de las diferencias resultó ser un defecto:

    forget('1')  ->  True  y desaparece 'editor'   (su fila tenía id 1)
    forget('3')  ->  True  y desaparece 'lenguaje' (su fila tenía id 3)

`SQLiteMemoryStorage.forget` borraba por clave **o por id numérico de la fila**.
Nadie olvida por id —la herramienta, la ruta, los ajustes y la web mandan la
clave—, pero `recall_memory` le enseña el `id` al modelo, así que confundirlo con
una clave borraba en local un recuerdo que no era el pedido. En la nube el mismo
`forget('3')` no borraba nada. La otra diferencia medida: marcas de tiempo con
resolución de segundo, así que dos recuerdos guardados en el mismo segundo
empataban y `recall` no devolvía primero el último.

Estas pruebas fijan **el contrato**, no una implementación: se ejecutan contra lo
que el contenedor monta de verdad en local.
"""

import pytest

from src.identidad.contexto import como_usuario
from src.memory.manager import MemoryManager


@pytest.fixture
def memoria(monkeypatch, tmp_path) -> MemoryManager:
    """La memoria tal como la monta el contenedor del Morgan local."""
    monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))

    from src.config import reset_settings

    reset_settings()
    try:
        from src.api.dependencies import CoreContainer

        yield CoreContainer().memory_manager
    finally:
        reset_settings()


def _claves(memoria: MemoryManager) -> list[str]:
    return sorted(r["key"] for r in memoria.recall())


class TestOlvidarEsPorClave:
    def test_un_numero_que_no_es_clave_no_borra_nada(self, memoria):
        """El defecto medido: el id de la fila no es una clave."""
        memoria.remember("editor", "vscode", "preferencias")
        memoria.remember("lenguaje", "python", "preferencias")

        assert memoria.forget("1") is False
        assert memoria.forget("2") is False
        assert _claves(memoria) == ["editor", "lenguaje"]

    def test_una_clave_numerica_borra_solo_esa(self, memoria):
        memoria.remember("editor", "vscode")      # fila 1
        memoria.remember("1", "clave que es un número")

        assert memoria.forget("1") is True
        assert _claves(memoria) == ["editor"]

    def test_olvidar_por_clave_con_espacios(self, memoria):
        memoria.remember("editor", "vscode")

        assert memoria.forget("  editor ") is True
        assert memoria.recall() == []

    def test_olvidar_lo_que_no_existe(self, memoria):
        assert memoria.forget("nada") is False


class TestRecordar:
    def test_lo_mas_reciente_primero_aunque_sea_el_mismo_segundo(self, memoria):
        """Con resolución de segundo, dos recuerdos seguidos empataban."""
        for clave in ("uno", "dos", "tres", "cuatro"):
            memoria.remember(clave, "x")

        assert [r["key"] for r in memoria.recall()] == ["cuatro", "tres", "dos", "uno"]

    def test_la_forma_que_usan_la_api_y_la_consola(self, memoria):
        """`routes/memory.py` y `main.py` leen estos cuatro campos."""
        memoria.remember("editor", "vscode", "preferencias")

        registro = memoria.recall()[0]
        for campo in ("key", "value", "category", "updated_at"):
            assert campo in registro

    def test_remember_devuelve_lo_guardado(self, memoria):
        guardado = memoria.remember("  editor ", "  vscode ", "preferencias")

        assert {k: guardado[k] for k in ("key", "value", "category")} == {
            "key": "editor", "value": "vscode", "category": "preferencias",
        }
        assert guardado["updated_at"]

    def test_misma_clave_actualiza(self, memoria):
        memoria.remember("editor", "vim")
        memoria.remember("editor", "vscode")

        assert [(r["key"], r["value"]) for r in memoria.recall()] == [("editor", "vscode")]

    def test_filtros(self, memoria):
        memoria.remember("editor", "vscode", "preferencias")
        memoria.remember("proyecto", "morgan", "trabajo")

        assert [r["key"] for r in memoria.recall(category="trabajo")] == ["proyecto"]
        assert [r["key"] for r in memoria.recall(query="vsc")] == ["editor"]

    def test_devuelve_como_mucho_cien(self, memoria):
        """El límite de la nube. Antes, en local no había ninguno."""
        for i in range(105):
            memoria.remember(f"k{i}", "v")

        assert len(memoria.recall()) == 100


class TestCadaUnoLoSuyo:
    def test_ana_no_ve_ni_borra_lo_de_bea(self, memoria):
        with como_usuario("usr-ana"):
            memoria.remember("color", "azul")
        with como_usuario("usr-bea"):
            memoria.remember("color", "verde")
            assert [r["value"] for r in memoria.recall()] == ["verde"]
            assert memoria.clear() == 1
        with como_usuario("usr-ana"):
            assert [r["value"] for r in memoria.recall()] == ["azul"]

    def test_limpiar_una_categoria(self, memoria):
        memoria.remember("editor", "vscode", "preferencias")
        memoria.remember("proyecto", "morgan", "trabajo")

        assert memoria.clear("preferencias") == 1
        assert _claves(memoria) == ["proyecto"]


class TestUnaSolaImplementacion:
    def test_local_usa_la_misma_capa_que_la_nube(self, monkeypatch, tmp_path):
        """Dos implementaciones del mismo contrato divergen solas: es lo que pasó."""
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))

        from src.config import reset_settings
        from src.memory.memoria_sobre_repositorio import MemoriaSobreRepositorio

        reset_settings()
        try:
            from src.api.dependencies import CoreContainer

            contenedor = CoreContainer()
            storage = contenedor.memory_manager.storage

            assert isinstance(storage, MemoriaSobreRepositorio)
            # El mismo repositorio que el resto de la capa, no uno aparte sobre
            # otra conexión: la memoria y las conversaciones son la misma base.
            assert storage.repositorio is contenedor.repositories.memories
        finally:
            reset_settings()

    def test_la_salud_responde(self, memoria):
        assert memoria.health() == (True, None)

    def test_el_gestor_por_defecto_tambien(self, monkeypatch, tmp_path):
        """Las herramientas construyen `MemoryManager()` si no se les pasa uno."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))

        from src.config import reset_settings
        from src.memory.memoria_sobre_repositorio import MemoriaSobreRepositorio

        reset_settings()
        try:
            gestor = MemoryManager()
            assert isinstance(gestor.storage, MemoriaSobreRepositorio)
            gestor.remember("editor", "vscode")
            assert gestor.forget("1") is False
        finally:
            reset_settings()
