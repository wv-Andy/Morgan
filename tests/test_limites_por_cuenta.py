"""
Los límites por cuenta, revisados (4.22).

Lo que una cuenta puede gastar de dinero ya tenía tope (mensajes, imágenes y transcripciones al
día, el cupo global, los archivos, los tokens de API, los PC y las automatizaciones). La revisión
buscó lo que **no** cuesta cupo pero llena la base, y que el modelo o un script pueden crear en
bucle: recuerdos, espacios y documentos de conocimiento. Ahora tienen tope, holgado, y al
llegar se dice qué hacer en vez de fallar raro.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.memory import manager as gestor
from src.memory.manager import MemoriaLlena, MemoryManager


@pytest.fixture
def cliente():
    return TestClient(app)


@pytest.fixture
def memoria(tmp_path):
    from src.memory.memoria_sobre_repositorio import sobre_sqlite

    return MemoryManager(sobre_sqlite(tmp_path / "m.db"))


class TestLosRecuerdos:
    def test_uno_nuevo_no_cabe_pasado_el_tope(self, memoria, monkeypatch):
        monkeypatch.setattr(gestor, "MAX_RECUERDOS", 3)
        for i in range(3):
            memoria.comprobar_que_cabe(f"k{i}", "v")
            memoria.remember(f"k{i}", "v")
        with pytest.raises(MemoriaLlena, match="3 recuerdos"):
            memoria.comprobar_que_cabe("otro", "v")

    def test_actualizar_uno_que_existe_siempre_cabe(self, memoria, monkeypatch):
        monkeypatch.setattr(gestor, "MAX_RECUERDOS", 1)
        memoria.remember("k", "v")
        memoria.comprobar_que_cabe("k", "nuevo valor")

    def test_uno_demasiado_largo_no(self, memoria):
        with pytest.raises(MemoriaLlena, match="2000 caracteres"):
            memoria.comprobar_que_cabe("k", "x" * (gestor.LARGO_MAXIMO_RECUERDO + 1))
        memoria.comprobar_que_cabe("k", "x" * gestor.LARGO_MAXIMO_RECUERDO)

    def test_la_herramienta_lo_dice_y_no_guarda(self, memoria, monkeypatch):
        from src.tools.memory import RememberFactTool

        monkeypatch.setattr(gestor, "MAX_RECUERDOS", 1)
        herramienta = RememberFactTool(memoria)
        assert herramienta.execute(key="a", value="1")["success"]
        r = herramienta.execute(key="b", value="2")
        assert not r["success"] and "máximo" in r["error"]
        assert [x["key"] for x in memoria.recall()] == ["a"]

    def test_la_api_contesta_409(self, cliente, monkeypatch):
        monkeypatch.setattr(gestor, "MAX_RECUERDOS", 1)
        assert cliente.post("/memory", json={"key": "a", "value": "1"}).status_code == 200
        r = cliente.post("/memory", json={"key": "b", "value": "2"})
        assert r.status_code == 409
        assert (r.json().get("error") or r.json().get("detail"))["code"] == "MEMORIA_LLENA"


class TestLosEspacios:
    def test_pasado_el_tope_no_se_crea_otro(self, cliente, monkeypatch):
        from src.espacios import modelos

        monkeypatch.setattr(modelos, "MAX_ESPACIOS", 2)
        monkeypatch.setattr("src.espacios.repositorio.MAX_ESPACIOS", 2)
        for nombre in ("Uno", "Dos"):
            assert cliente.post("/espacios", json={"nombre": nombre}).status_code == 201
        r = cliente.post("/espacios", json={"nombre": "Tres"})
        assert r.status_code == 409
        cuerpo = r.json().get("error") or r.json().get("detail")
        assert cuerpo["code"] == "ESPACIOS_DEMASIADOS" and "máximo" in cuerpo["message"]

    def test_el_tope_es_de_cincuenta(self):
        from src.espacios.modelos import MAX_ESPACIOS

        assert MAX_ESPACIOS == 50


class TestLosDocumentos:
    def test_uno_nuevo_no_cabe_pero_reemplazar_si(self, tmp_path, monkeypatch):
        from src.conocimiento import almacen as modulo
        from src.memory.db import Database

        monkeypatch.setattr(modulo, "MAX_DOCUMENTOS", 2)
        almacen = modulo.AlmacenDeConocimiento(Database(tmp_path / "c.db"))
        almacen.añadir("Uno", "texto")
        almacen.añadir("Dos", "texto")
        with pytest.raises(ValueError, match="2 documentos"):
            almacen.añadir("Tres", "texto")
        almacen.añadir("Uno", "texto nuevo")          # mismo título: se reemplaza

    def test_la_herramienta_lo_dice(self, tmp_path, monkeypatch):
        from src.conocimiento import almacen as modulo
        from src.memory.db import Database
        from src.tools.conocimiento import AddKnowledgeTool

        monkeypatch.setattr(modulo, "MAX_DOCUMENTOS", 1)
        herramienta = AddKnowledgeTool(modulo.AlmacenDeConocimiento(Database(tmp_path / "c.db")))
        assert herramienta.execute(titulo="Uno", contenido="a")["success"]
        r = herramienta.execute(titulo="Dos", contenido="b")
        assert not r["success"] and "máximo" in r["error"]

    def test_en_supabase_cuenta_los_de_la_cuenta(self, monkeypatch):
        from src.conocimiento import almacen as modulo
        from src.conocimiento.almacen_supabase import AlmacenDeConocimientoSupabase

        monkeypatch.setattr(modulo, "MAX_DOCUMENTOS", 2)

        class Cliente:
            def __init__(self):
                self.consultas = []

            def select(self, tabla, consulta):
                self.consultas.append(consulta)
                if "select=id&limit=2" in consulta and "titulo_norm" not in consulta:
                    return [{"id": "a"}, {"id": "b"}]
                return []

        cliente = Cliente()
        almacen = AlmacenDeConocimientoSupabase(cliente)
        with pytest.raises(ValueError, match="2 documentos"):
            almacen.añadir("Tres", "texto")
        contar = [c for c in cliente.consultas if "titulo_norm" not in c]
        assert contar and "user_id=eq." in contar[0]
