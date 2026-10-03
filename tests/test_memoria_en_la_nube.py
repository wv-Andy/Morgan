"""
La memoria persistente cuando Morgan corre en la nube.

**El fallo.** `MemoryManager` se construía siempre con su almacenamiento por
defecto —SQLite sobre un fichero local— aunque el resto del contenedor ya
hubiera cambiado a Supabase. En Render el disco es efímero, así que todo lo que
Morgan recordaba se perdía en cada reinicio, y en el plan gratuito el servicio
duerme a los quince minutos.

No se veía como una avería. `POST /memory` respondía 200, la lista mostraba el
recuerdo recién creado, y al día siguiente no había ninguno. Se descubrió al
revés: comprobando en la base de producción que la tabla `memories` estaba
**vacía** después de haber guardado recuerdos con éxito desde la web.

`SupabaseMemoryRepository` existía y estaba completo. Lo que faltaba era la
traducción entre las dos interfaces.
"""

import pytest

from src.memory.memoria_sobre_repositorio import MemoriaSobreRepositorio
from src.memory.models import MemoryRecord


class RepositorioFalso:
    """Un `MemoryRepository` que apunta lo que le piden."""

    def __init__(self):
        self.llamadas: list[tuple] = []
        self.registros: list[MemoryRecord] = []

    def upsert(self, key, value, category="general"):
        self.llamadas.append(("upsert", key, value, category))
        registro = MemoryRecord(
            id=1, key=key, value=value, category=category,
            created_at="2026-09-07", updated_at="2026-09-07",
        )
        self.registros.append(registro)
        return registro

    def get(self, key):
        self.llamadas.append(("get", key))
        return next((r for r in self.registros if r.key == key), None)

    def search(self, query=None, category=None, limit=100):
        self.llamadas.append(("search", query, category))
        return list(self.registros)

    def delete(self, key):
        self.llamadas.append(("delete", key))
        return True

    def clear(self, category=None):
        self.llamadas.append(("clear", category))
        return len(self.registros)


@pytest.fixture
def repositorio():
    return RepositorioFalso()


@pytest.fixture
def memoria(repositorio):
    return MemoriaSobreRepositorio(repositorio)


class TestLoQueSeGuardaLlegaAlRepositorio:
    """Lo que no ocurría: se guardaba en otro sitio, y ese sitio desaparecía."""

    def test_recordar_escribe_en_el_repositorio(self, memoria, repositorio):
        memoria.remember("color", "verde", "preferencias")

        assert ("upsert", "color", "verde", "preferencias") in repositorio.llamadas

    def test_consultar_lee_del_repositorio(self, memoria, repositorio):
        memoria.remember("color", "verde")
        memoria.recall("col")

        assert ("search", "col", None) in repositorio.llamadas

    def test_olvidar_borra_en_el_repositorio(self, memoria, repositorio):
        memoria.forget("color")

        assert ("delete", "color") in repositorio.llamadas

    def test_limpiar_por_categoria(self, memoria, repositorio):
        memoria.clear("preferencias")

        assert ("clear", "preferencias") in repositorio.llamadas


class TestElContratoConLaApiNoCambia:
    """La respuesta viaja tal cual a la API. Cambiar su forma rompería la
    interfaz sin que nada avisara: no hay compilador entre Python y el JSON."""

    def test_recordar_devuelve_los_mismos_campos_que_sqlite(self, memoria):
        salida = memoria.remember("color", "verde", "preferencias")

        assert set(salida) == {"key", "value", "category", "updated_at"}
        assert salida["key"] == "color"
        assert salida["value"] == "verde"
        assert salida["category"] == "preferencias"

    def test_consultar_devuelve_diccionarios_no_dataclases(self, memoria):
        memoria.remember("color", "verde")

        recuerdos = memoria.recall()

        assert all(isinstance(r, dict) for r in recuerdos)
        assert recuerdos[0]["key"] == "color"
        assert "id" in recuerdos[0] and "updated_at" in recuerdos[0]


class TestLaSalud:
    """`MemoryManager.health()` llama a `db.healthy()`. La fábrica de Supabase
    expone lo mismo con otro nombre, `health()`, así que pasarla directamente
    reventaba la comprobación de estado — el chequeo de salud fallando él
    mismo."""

    def test_sin_fabrica_no_hay_nada_que_comprobar(self, repositorio):
        assert MemoriaSobreRepositorio(repositorio).db is None

    def test_con_fabrica_se_traduce_el_nombre(self, repositorio):
        class FabricaFalsa:
            def health(self):
                return (True, None)

        memoria = MemoriaSobreRepositorio(repositorio, salud=FabricaFalsa())

        assert memoria.db.healthy() == (True, None)

    def test_una_fabrica_caida_se_propaga(self, repositorio):
        class FabricaCaida:
            def health(self):
                return (False, "sin conexión")

        memoria = MemoriaSobreRepositorio(repositorio, salud=FabricaCaida())

        assert memoria.db.healthy() == (False, "sin conexión")

    def test_el_gestor_la_usa_sin_reventar(self, repositorio):
        """La comprobación de verdad: montado como en producción."""
        from src.memory.manager import MemoryManager

        class FabricaFalsa:
            def health(self):
                return (True, None)

        gestor = MemoryManager(MemoriaSobreRepositorio(repositorio, FabricaFalsa()))

        assert gestor.health() == (True, None)


class TestElContenedorLaConectaEnLaNube:
    """Que el adaptador exista no sirve de nada si nadie lo usa: era exactamente
    la situación anterior con `SupabaseMemoryRepository`."""

    def test_en_la_nube_la_memoria_no_escribe_en_disco(self, monkeypatch, tmp_path):
        from src.memory.sqlite_repositories import SQLiteMemoryRepository

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_CLOUD_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://ejemplo.supabase.co")
        monkeypatch.setenv("SUPABASE_SECRET_KEY", "clave-de-mentira")
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))

        from src.config import reset_settings

        reset_settings()
        try:
            from src.api.dependencies import CoreContainer

            contenedor = CoreContainer()

            storage = contenedor.memory_manager.storage
            assert isinstance(storage, MemoriaSobreRepositorio)
            # Desde la 2.0.13 el local usa el mismo adaptador, así que el tipo
            # del adaptador ya no distingue nada: lo que importa es qué hay DEBAJO.
            assert not isinstance(storage.repositorio, SQLiteMemoryRepository), (
                "En la nube la memoria seguía escribiendo en el disco local, "
                "que Render borra en cada reinicio"
            )
            assert storage.repositorio is contenedor.repositories.memories
        finally:
            reset_settings()

    def test_en_local_sigue_siendo_sqlite(self, monkeypatch, tmp_path):
        """Tu equipo, tu fichero. Cambiar esto dejaría el Morgan de escritorio
        dependiendo de una conexión para recordar algo."""
        from src.memory.sqlite_repositories import SQLiteMemoryRepository

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))

        from src.config import reset_settings

        reset_settings()
        try:
            from src.api.dependencies import CoreContainer

            contenedor = CoreContainer()

            storage = contenedor.memory_manager.storage
            assert isinstance(storage.repositorio, SQLiteMemoryRepository)

            # Y en TU fichero, no en otro: lo que se recuerda está en la base
            # del MORGAN_DATA_DIR de este equipo.
            contenedor.memory_manager.remember("sonda", "en disco")
            assert (tmp_path / "morgan_memory.db").exists()
            assert [r["key"] for r in contenedor.memory_manager.recall()] == ["sonda"]
        finally:
            reset_settings()
