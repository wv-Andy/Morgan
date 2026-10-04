"""
La copia de seguridad de Supabase (4.20): `scripts/copia_supabase.py`.

Lo que haría daño si fallase: una tabla que no se copia (una nueva que nadie añadió a la
lista), una copia rota o cambiada que se restaura igual, una tabla que se restaura antes que
aquella de la que depende, o los contadores sin ajustar (el primer mensaje nuevo chocaría
con uno restaurado). La prueba de verdad, contra un Supabase real, está en docs/datos.md.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from scripts import copia_supabase as copia

RAIZ = Path(__file__).resolve().parents[1]


class BaseFalsa:
    """PostgREST en memoria: lo justo de `select` (orden, límite, desplazamiento) y `upsert`."""

    def __init__(self, tablas=None):
        self.tablas = {t: [dict(f) for f in filas] for t, filas in (tablas or {}).items()}
        self.escrituras: list[str] = []

    def select(self, tabla, consulta=""):
        q = {k: v[0] for k, v in parse_qs(consulta).items()}
        filas = list(self.tablas.get(tabla, []))
        columnas = [c.split(".")[0] for c in q.get("order", "").split(",") if c]
        filas.sort(key=lambda f: tuple(str(f.get(c)) for c in columnas))
        inicio = int(q.get("offset", 0))
        return [dict(f) for f in filas[inicio:inicio + int(q.get("limit", 10**9))]]

    def upsert(self, tabla, filas, on_conflict):
        self.escrituras.append(tabla)
        claves = on_conflict.split(",")
        actuales = self.tablas.setdefault(tabla, [])
        for fila in filas:
            ident = tuple(fila[c] for c in claves)
            actuales[:] = [f for f in actuales if tuple(f[c] for c in claves) != ident] + [dict(fila)]
        return filas


def _datos():
    return {
        "morgan_users": [{"id": "u1", "email": "ana@ejemplo.co"}, {"id": "u2", "email": "bea@ejemplo.co"}],
        "sessions": [{"user_id": "u1", "id": "s1"}],
        "messages": [{"id": i, "session_id": "s1", "content": f"m{i}"} for i in range(1, 2501)],
    }


class TestQueSeCopia:
    def test_todas_las_tablas_de_las_migraciones(self):
        """Una tabla nueva que no esté en TABLAS no se copiaría, y nadie lo vería."""
        creadas = set()
        for sql in (RAIZ / "migraciones" / "supabase").glob("*.sql"):
            texto = sql.read_text(encoding="utf-8").lower()
            creadas |= set(re.findall(r"create table (?:if not exists )?(?:public\.)?([a-z_]+)", texto))
        assert creadas, "no se leyó ninguna migración"
        assert creadas - set(copia.TABLAS) == set(), "tablas sin copiar"

    def test_las_padres_antes_que_las_hijas(self):
        orden = list(copia.TABLAS)
        for hija, padre in [("messages", "sessions"), ("sessions", "morgan_users"),
                            ("conocimiento_fragmentos", "conocimiento"), ("agentes", "morgan_users"),
                            ("ordenes_agente", "morgan_users"), ("avisos", "morgan_users")]:
            assert orden.index(padre) < orden.index(hija), (padre, hija)

    def test_lee_todas_las_paginas(self, tmp_path):
        manifiesto = copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
        assert manifiesto["tablas"]["messages"]["filas"] == 2500
        assert len(json.loads((tmp_path / "c" / "messages.json").read_text(encoding="utf-8"))) == 2500


class TestRestaurar:
    def test_ida_y_vuelta_queda_igual(self, tmp_path):
        origen = BaseFalsa(_datos())
        copia.copiar(origen, tmp_path / "c")
        vacia = BaseFalsa()
        copia.restaurar(vacia, tmp_path / "c")
        assert copia.comprobar(vacia, tmp_path / "c") == []
        assert vacia.escrituras.index("morgan_users") < vacia.escrituras.index("messages")

    def test_restaurar_dos_veces_no_duplica(self, tmp_path):
        copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
        base = BaseFalsa()
        copia.restaurar(base, tmp_path / "c")
        copia.restaurar(base, tmp_path / "c")
        assert len(base.tablas["messages"]) == 2500

    def test_comprobar_ve_lo_que_cambio(self, tmp_path):
        base = BaseFalsa(_datos())
        copia.copiar(base, tmp_path / "c")
        base.tablas["messages"][7]["content"] = "otra cosa"
        assert copia.comprobar(base, tmp_path / "c") == ["messages: 2500 filas en la base, 2500 en la copia"]

    def test_una_copia_cambiada_no_se_restaura(self, tmp_path):
        copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
        fichero = tmp_path / "c" / "morgan_users.json"
        fichero.write_text(fichero.read_text(encoding="utf-8").replace("ana@", "eva@"), encoding="utf-8")
        base = BaseFalsa()
        with pytest.raises(ValueError, match="morgan_users"):
            copia.restaurar(base, tmp_path / "c")
        assert base.escrituras == [], "no escribe nada de una copia que no es la que se hizo"

    def test_una_copia_sin_alguna_tabla_no_se_restaura(self, tmp_path):
        copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
        manifiesto = json.loads((tmp_path / "c" / "manifiesto.json").read_text(encoding="utf-8"))
        del manifiesto["tablas"]["avisos"]
        (tmp_path / "c" / "manifiesto.json").write_text(json.dumps(manifiesto), encoding="utf-8")
        with pytest.raises(ValueError, match="avisos"):
            copia.restaurar(BaseFalsa(), tmp_path / "c")

    def test_deja_el_sql_de_los_contadores(self, tmp_path):
        copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
        copia.restaurar(BaseFalsa(), tmp_path / "c")
        sql = (tmp_path / "c" / "secuencias.sql").read_text(encoding="utf-8")
        for tabla in copia.CON_CONTADOR:
            assert f"pg_get_serial_sequence('public.{tabla}', 'id')" in sql

    def test_no_pisa_una_copia_anterior(self, tmp_path):
        copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
        with pytest.raises(FileExistsError):
            copia.copiar(BaseFalsa(_datos()), tmp_path / "c")
