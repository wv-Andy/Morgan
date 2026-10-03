"""
La memoria de cada persona va solo en sus turnos (4.0, hallazgo de seguridad).

Hasta la 4.0 el resumen de la memoria se escribía en el prompt **compartido** del agente
(`set_memory_context`) cada vez que alguien guardaba un recuerdo o sus ajustes. Con varias
cuentas, el turno de Bea llevaba la memoria de Ana. Se reprodujo así, con dos cuentas por
HTTP, antes de arreglarlo; esta prueba es esa reproducción, ahora al revés.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.sesion_web import CABECERA_CSRF
from src.config import reset_settings

SECRETO = "SECRETO-DE-ANA"


def _cuenta(app, nombre):
    cliente = TestClient(app)
    r = cliente.post("/auth/registro", json={
        "username": nombre, "email": f"{nombre}@ejemplo.co", "password": "contrasena-larga"})
    assert r.status_code == 200, r.text
    cliente.headers[CABECERA_CSRF] = r.json()["csrf"]
    return cliente


@pytest.fixture
def nube(monkeypatch, modelo_simulado):
    """Nube con dos cuentas y el agente de verdad, con un modelo simulado."""
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    app = create_app()
    ana, bea = _cuenta(app, "ana"), _cuenta(app, "bea")
    assert dependencies.get_container().agent is not None
    yield ana, bea, modelo_simulado
    dependencies.reset_container()
    reset_settings()


def _turno(cliente, modelo, texto="hola"):
    modelo.queue_text("vale")
    r = cliente.post("/chat", json={"message": texto})
    assert r.status_code == 200, r.text
    return modelo.prompts[-1]


class TestLaMemoriaEsDeQuienPregunta:
    def test_el_turno_de_bea_no_lleva_la_memoria_de_ana(self, nube):
        ana, bea, modelo = nube
        r = ana.post("/memory", json={"category": "personal", "key": "clave_de_casa", "value": SECRETO})
        assert r.status_code in (200, 201), r.text

        assert SECRETO not in _turno(bea, modelo)

    def test_el_turno_de_ana_si_lleva_la_suya(self, nube):
        ana, _, modelo = nube
        ana.post("/memory", json={"category": "personal", "key": "clave_de_casa", "value": SECRETO})

        assert SECRETO in _turno(ana, modelo)

    def test_tampoco_se_cuela_por_los_ajustes(self, nube):
        """Guardar los ajustes también escribía en el prompt compartido."""
        ana, bea, modelo = nube
        ana.post("/memory", json={"category": "personal", "key": "clave_de_casa", "value": SECRETO})
        r = bea.put("/settings", json={"response_style": "breve"})
        assert r.status_code == 200, r.text

        assert SECRETO not in _turno(bea, modelo)
        assert SECRETO in _turno(ana, modelo)


class TestElIndiceDelConocimientoTambien:
    """4.1: los títulos del conocimiento van en el turno, y solo en el de su dueña."""

    def test_ana_ve_sus_titulos_y_bea_no(self, nube):
        ana, bea, modelo = nube
        r = ana.post("/tools/add_knowledge", json={"arguments": {
            "titulo": "Procedimiento de copias de Ana", "contenido": "Se anotan en registro.txt."}})
        assert r.status_code == 200, r.text

        assert "«Procedimiento de copias de Ana»" in _turno(ana, modelo)
        assert "Procedimiento de copias de Ana" not in _turno(bea, modelo)

    def test_sin_documentos_no_se_anade_nada(self, nube):
        _, bea, modelo = nube
        assert "Documentos guardados en su conocimiento" not in _turno(bea, modelo)


class TestSoloLosRecuerdosQueSeUsan:
    def test_el_resumen_pide_quince_y_no_cien(self):
        """4.3: se pedían 100 recuerdos en cada turno para quedarse con 15."""
        from src.memory.manager import RECUERDOS_EN_EL_PROMPT, MemoryManager

        class Espia:
            def __init__(self):
                self.limites = []

            def recall(self, query=None, category=None, limit=100):
                self.limites.append(limit)
                return [{"key": f"k{i}", "value": "v", "category": "user"} for i in range(limit)]

        espia = Espia()
        gestor = MemoryManager(storage=espia)
        resumen = gestor.get_context_summary()
        assert espia.limites == [RECUERDOS_EN_EL_PROMPT] == [15]
        assert resumen.count("\n- ") == 15
