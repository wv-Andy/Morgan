"""
Los archivos de la 4.6 (mi lista): copiar dentro del PC, comprimir y descomprimir,
los metadatos y la búsqueda por extensión, fecha, tamaño y contenido.

Lo que haría daño si fallase, y se fija aquí: que se pise algo, que se copie o se saque un
programa, que un `.zip` escriba fuera de su carpeta (*zip slip*) o llene el disco (una
«bomba»), que se lea o se copie lo que la política no deja leer (credenciales, carpetas
bloqueadas), o que se escriba fuera de las carpetas de escritura.
"""

import os
import time
import zipfile
from pathlib import Path

import pytest

from src.agente import archivos, capacidades
from src.agente.politica import DE_ESCRITURA, DE_LECTURA, Politica
from tests.test_agente_escritura import _foto, papelera, pc, preguntas, sistema_de_mentira  # noqa: F401


class TestNacen:
    def test_copiar_y_comprimir_nacen_apagadas_y_los_metadatos_encendidos(self):
        politica = Politica()
        assert "copy_path" in DE_ESCRITURA and "compress" in DE_ESCRITURA
        assert not politica.capacidades["copy_path"] and not politica.capacidades["compress"]
        assert "file_info" in DE_LECTURA and politica.capacidades["file_info"]

    def test_se_anuncian_solo_con_carpetas(self, pc):
        anunciadas = capacidades.disponibles(Politica.cargar())
        assert {"copy_path", "compress", "file_info"} <= set(anunciadas)


class TestCopiar:
    def test_copia_un_archivo_de_lectura_a_escritura_y_lo_comprueba(self, pc):
        r = archivos.copy_path(str(pc["lectura"] / "solo_leer.txt"), str(pc["notas"] / "copia.txt"))
        assert r["success"], r
        assert (pc["notas"] / "copia.txt").read_text(encoding="utf-8") == "no me toques"
        assert (pc["lectura"] / "solo_leer.txt").exists(), "el original no se toca"
        assert r["data"]["comprobado"]

    def test_no_pisa(self, pc):
        antes = _foto(pc["notas"])
        r = archivos.copy_path(str(pc["lectura"] / "solo_leer.txt"), str(pc["notas"] / "lista.txt"))
        assert not r["success"] and _foto(pc["notas"]) == antes

    def test_no_escribe_fuera_de_las_carpetas_de_escritura(self, pc):
        r = archivos.copy_path(str(pc["notas"] / "lista.txt"), str(pc["lectura"] / "copia.txt"))
        assert not r["success"] and not (pc["lectura"] / "copia.txt").exists()

    def test_no_lee_lo_que_no_esta_permitido(self, pc):
        r = archivos.copy_path(str(pc["fuera"] / "ajeno.txt"), str(pc["notas"] / "ajeno.txt"))
        assert not r["success"] and not (pc["notas"] / "ajeno.txt").exists()

    def test_no_copia_credenciales_ni_programas(self, pc):
        (pc["lectura"] / ".env").write_text("CLAVE=1", encoding="utf-8")
        (pc["lectura"] / "virus.exe").write_bytes(b"MZ")
        assert not archivos.copy_path(str(pc["lectura"] / ".env"), str(pc["notas"] / "e.txt"))["success"]
        assert not archivos.copy_path(str(pc["lectura"] / "virus.exe"), str(pc["notas"] / "v.txt"))["success"]
        assert not archivos.copy_path(str(pc["lectura"] / "solo_leer.txt"), str(pc["notas"] / "s.bat"))["success"]

    def test_una_carpeta_se_copia_sin_lo_que_no_debe(self, pc):
        origen = pc["lectura"] / "Proyecto"
        (origen / "sub").mkdir(parents=True)
        (origen / "a.txt").write_text("a", encoding="utf-8")
        (origen / "sub" / "b.md").write_text("b", encoding="utf-8")
        (origen / "run.ps1").write_text("rm -rf", encoding="utf-8")
        (origen / "id_rsa").write_text("secreto", encoding="utf-8")
        r = archivos.copy_path(str(origen), str(pc["notas"] / "Copia"))
        assert r["success"], r
        copiados = sorted(str(p.relative_to(pc["notas"] / "Copia")) for p in (pc["notas"] / "Copia").rglob("*")
                          if p.is_file())
        assert copiados == ["a.txt", str(Path("sub") / "b.md")]
        assert len(r["data"]["omitidos"]) == 2

    def test_demasiado_grande_no_se_copia(self, pc, monkeypatch):
        monkeypatch.setattr(archivos, "MAX_BYTES", 5)
        r = archivos.copy_path(str(pc["lectura"] / "solo_leer.txt"), str(pc["notas"] / "c.txt"))
        assert not r["success"] and r["motivo"] == "demasiado_grande"


class TestComprimir:
    def test_comprimir_y_descomprimir_ida_y_vuelta(self, pc):
        r = archivos.compress("comprimir", paths=[str(pc["notas"] / "lista.txt"),
                                                  str(pc["lectura"] / "solo_leer.txt")],
                              dst=str(pc["notas"] / "todo.zip"))
        assert r["success"], r
        with zipfile.ZipFile(pc["notas"] / "todo.zip") as z:
            assert sorted(z.namelist()) == ["lista.txt", "solo_leer.txt"]
        r = archivos.compress("descomprimir", path=str(pc["notas"] / "todo.zip"), dst=str(pc["notas"] / "Sacado"))
        assert r["success"], r
        assert (pc["notas"] / "Sacado" / "lista.txt").read_text(encoding="utf-8") == "pan\nleche\n"

    def test_comprimir_mete_el_codigo_pero_no_las_credenciales(self, pc):
        """Decisión mía (2026-10-01): sin los scripts, la copia de un proyecto de código
        salía sin el código. Un .zip no ejecuta nada; las credenciales siguen fuera."""
        proyecto = pc["lectura"] / "app"
        (proyecto / "src").mkdir(parents=True)
        (proyecto / "src" / "main.py").write_text("print(1)", encoding="utf-8")
        (proyecto / "instala.exe").write_bytes(b"MZ")
        (proyecto / ".env").write_text("CLAVE=1", encoding="utf-8")
        (proyecto / "leeme.md").write_text("hola", encoding="utf-8")
        suelto = pc["lectura"] / "arranca.ps1"
        suelto.write_text("echo hola", encoding="utf-8")
        r = archivos.compress("comprimir", paths=[str(proyecto), str(suelto)], dst=str(pc["notas"] / "app.zip"))
        assert r["success"], r
        with zipfile.ZipFile(pc["notas"] / "app.zip") as z:
            assert sorted(z.namelist()) == ["app/instala.exe", "app/leeme.md", "app/src/main.py", "arranca.ps1"]
        assert "credenciales" in str(r["data"].get("omitidos")), "y se dice qué se dejó fuera"

    def test_y_al_sacarlos_siguen_sin_salir(self, pc):
        (pc["lectura"] / "script.py").write_text("print(1)", encoding="utf-8")
        archivos.compress("comprimir", paths=[str(pc["lectura"] / "script.py")], dst=str(pc["notas"] / "s.zip"))
        r = archivos.compress("descomprimir", path=str(pc["notas"] / "s.zip"), dst=str(pc["notas"] / "Sacado"))
        assert not (pc["notas"] / "Sacado" / "script.py").exists()

    def test_copiar_sigue_sin_programas(self, pc):
        (pc["lectura"] / "script.py").write_text("print(1)", encoding="utf-8")
        assert not archivos.copy_path(str(pc["lectura"] / "script.py"), str(pc["notas"] / "copia.py"))["success"]

    def test_el_destino_tiene_que_ser_zip(self, pc):
        r = archivos.compress("comprimir", paths=[str(pc["notas"] / "lista.txt")], dst=str(pc["notas"] / "a.txt"))
        assert not r["success"]

    def test_accion_desconocida(self, pc):
        assert not archivos.compress("borrar", dst=str(pc["notas"] / "x"))["success"]

    def _zip(self, carpeta: Path, entradas: dict, nombre="malo.zip") -> Path:
        ruta = carpeta / nombre
        with zipfile.ZipFile(ruta, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for n, contenido in entradas.items():
                z.writestr(n, contenido)
        return ruta

    @pytest.mark.parametrize("entrada", ["../fuera.txt", "sub/../../fuera.txt", "/abs.txt", "C:/win.txt",
                                         "..\\fuera.txt"])
    def test_zip_slip_no_saca_nada(self, pc, entrada):
        malo = self._zip(pc["notas"], {"bien.txt": "ok", entrada: "mal"})
        antes = _foto(pc["notas"].parent)
        r = archivos.compress("descomprimir", path=str(malo), dst=str(pc["notas"] / "Sacado"))
        assert not r["success"] and r["motivo"] == "zip_peligroso"
        assert _foto(pc["notas"].parent) == antes

    def test_una_bomba_no_se_saca(self, pc):
        bomba = self._zip(pc["notas"], {"ceros.txt": "0" * (5 * 1024 * 1024)})
        r = archivos.compress("descomprimir", path=str(bomba), dst=str(pc["notas"] / "Sacado"))
        assert not r["success"] and not (pc["notas"] / "Sacado").exists()

    def test_el_tamano_se_mide_al_sacar(self, pc, monkeypatch):
        grande = self._zip(pc["notas"], {"a.txt": "x" * 3000, "b.txt": "y" * 3000})
        monkeypatch.setattr(archivos, "MAX_BYTES_ZIP", 4000)
        monkeypatch.setattr(archivos, "MAX_PROPORCION", 10**9)
        r = archivos.compress("descomprimir", path=str(grande), dst=str(pc["notas"] / "Sacado"))
        assert not r["success"] and not (pc["notas"] / "Sacado").exists()

    def test_programas_y_credenciales_no_se_sacan(self, pc):
        mixto = self._zip(pc["notas"], {"leeme.txt": "hola", "instala.exe": "MZ", ".env": "K=1",
                                        "script.py": "print(1)"})
        r = archivos.compress("descomprimir", path=str(mixto), dst=str(pc["notas"] / "Sacado"))
        assert r["success"], r
        assert sorted(p.name for p in (pc["notas"] / "Sacado").iterdir()) == ["leeme.txt"]
        assert len(r["data"]["omitidos"]) == 3

    def test_no_saca_fuera_de_las_carpetas_de_escritura(self, pc):
        bueno = self._zip(pc["notas"], {"a.txt": "a"}, "bueno.zip")
        r = archivos.compress("descomprimir", path=str(bueno), dst=str(pc["lectura"] / "Sacado"))
        assert not r["success"] and not (pc["lectura"] / "Sacado").exists()

    def test_un_zip_roto_no_deja_nada(self, pc):
        (pc["notas"] / "roto.zip").write_bytes(b"PK\x03\x04 no es un zip")
        r = archivos.compress("descomprimir", path=str(pc["notas"] / "roto.zip"), dst=str(pc["notas"] / "S"))
        assert not r["success"] and not (pc["notas"] / "S").exists()


class TestMetadatos:
    def test_de_un_archivo(self, pc):
        r = archivos.file_info(str(pc["notas"] / "lista.txt"))
        assert r["success"], r
        d = r["data"]
        assert d["type"] == "file" and d["size"] == (pc["notas"] / "lista.txt").stat().st_size
        assert d["extension"] == "txt" and d["mime"] == "text/plain" and d["modified"]

    def test_de_una_carpeta(self, pc):
        (pc["notas"] / "sub").mkdir()
        (pc["notas"] / "sub" / "x.txt").write_text("12345", encoding="utf-8")
        d = archivos.file_info(str(pc["notas"]))["data"]
        assert d["type"] == "directory" and d["files"] == 2 and d["folders"] == 1 and d["complete"]

    def test_no_de_lo_que_no_esta_permitido(self, pc):
        assert not archivos.file_info(str(pc["fuera"] / "ajeno.txt"))["success"]

    def test_apagada_no_contesta(self, pc):
        politica = Politica.cargar()
        politica.capacidades["file_info"] = False
        politica.guardar()
        assert not archivos.file_info(str(pc["notas"] / "lista.txt"))["success"]


class TestBuscar:
    def _buscar(self, pc, **filtros):
        r = capacidades.search_files(path=str(pc["notas"]), **filtros)
        assert r["success"], r
        return sorted(Path(m["path"]).name for m in r["data"]["matches"])

    def test_por_extension(self, pc):
        (pc["notas"] / "a.pdf").write_bytes(b"%PDF")
        assert self._buscar(pc, extension="pdf") == ["a.pdf"]
        assert self._buscar(pc, extension=[".PDF", "txt"]) == ["a.pdf", "lista.txt"]

    def test_por_fecha(self, pc):
        viejo = pc["notas"] / "viejo.txt"
        viejo.write_text("v", encoding="utf-8")
        hace_un_ano = time.time() - 365 * 86400
        os.utime(viejo, (hace_un_ano, hace_un_ano))
        hoy = time.strftime("%Y-%m-%d")
        assert self._buscar(pc, modificado_desde=hoy) == ["lista.txt"]
        assert "viejo.txt" in self._buscar(pc, modificado_hasta=time.strftime("%Y-%m-%d", time.localtime(hace_un_ano)))

    def test_por_tamano(self, pc):
        (pc["notas"] / "grande.txt").write_text("x" * 5000, encoding="utf-8")
        assert self._buscar(pc, tamano_min_kb=4) == ["grande.txt"]
        assert self._buscar(pc, tamano_max_kb=1) == ["lista.txt"]

    def test_por_contenido_sin_mirar_tildes(self, pc):
        (pc["notas"] / "receta.md").write_text("Añade el azúcar", encoding="utf-8")
        (pc["notas"] / "binario.dat").write_bytes(b"azucar\x00\x01")
        assert self._buscar(pc, contenido="AZUCAR") == ["receta.md"]

    def test_el_contenido_de_credenciales_no_se_mira(self, pc):
        (pc["notas"] / ".env").write_text("azucar=1", encoding="utf-8")
        assert self._buscar(pc, contenido="azucar") == []

    def test_nombre_y_filtro_a_la_vez(self, pc):
        (pc["notas"] / "lista.pdf").write_bytes(b"%PDF")
        assert self._buscar(pc, query="lista", extension="pdf") == ["lista.pdf"]

    def test_sin_nada_que_buscar_se_dice(self, pc):
        assert not capacidades.search_files(path=str(pc["notas"]))["success"]

    def test_una_fecha_rara_se_dice(self, pc):
        r = capacidades.search_files(path=str(pc["notas"]), modificado_desde="ayer")
        assert not r["success"] and "AAAA-MM-DD" in r["error"]

    def test_lo_de_antes_sigue_igual(self, pc):
        assert self._buscar(pc, query="lista") == ["lista.txt"]


class TestLosTopes:
    def test_demasiadas_entradas_en_el_zip(self, pc, monkeypatch):
        ruta = pc["notas"] / "muchas.zip"
        with zipfile.ZipFile(ruta, "w") as z:
            z.writestr("a.txt", "a")
            z.writestr("b.txt", "b")
        monkeypatch.setattr(archivos, "MAX_ENTRADAS_ZIP", 1)
        r = archivos.compress("descomprimir", path=str(ruta), dst=str(pc["notas"] / "S"))
        assert not r["success"] and r["motivo"] == "demasiado_grande"

    def test_con_filtros_no_salen_carpetas(self, pc):
        (pc["notas"] / "Lista vieja").mkdir()
        r = capacidades.search_files(path=str(pc["notas"]), query="lista", extension="txt")
        assert [Path(m["path"]).name for m in r["data"]["matches"]] == ["lista.txt"]


class TestLoQueMidioElModelo:
    """Con el modelo real (4.6): creaba la carpeta y luego descomprimía en ella; con un
    solo archivo, usaba `path` para comprimir; y leía «14» bytes como 14 KB."""

    def test_descomprimir_en_una_carpeta_vacia_que_ya_existe(self, pc):
        ruta = pc["notas"] / "f.zip"
        with zipfile.ZipFile(ruta, "w") as z:
            z.writestr("playa.txt", "sol")
        (pc["notas"] / "Fotos").mkdir()
        r = archivos.compress("descomprimir", path=str(ruta), dst=str(pc["notas"] / "Fotos"))
        assert r["success"], r
        assert (pc["notas"] / "Fotos" / "playa.txt").read_text(encoding="utf-8") == "sol"

    def test_pero_no_en_una_con_cosas(self, pc):
        ruta = pc["notas"] / "f.zip"
        with zipfile.ZipFile(ruta, "w") as z:
            z.writestr("playa.txt", "sol")
        (pc["notas"] / "Fotos").mkdir()
        (pc["notas"] / "Fotos" / "mia.txt").write_text("mía", encoding="utf-8")
        antes = _foto(pc["notas"])
        r = archivos.compress("descomprimir", path=str(ruta), dst=str(pc["notas"] / "Fotos"))
        assert not r["success"] and _foto(pc["notas"]) == antes

    def test_comprimir_con_path(self, pc):
        r = archivos.compress("comprimir", path=str(pc["notas"] / "lista.txt"), dst=str(pc["notas"] / "l.zip"))
        assert r["success"], r

    def test_el_tamano_en_palabras(self, pc):
        assert archivos.legible(14) == "14 bytes"
        assert archivos.legible(2048) == "2,0 KB"
        assert archivos.legible(5 * 2**20) == "5,0 MB"
        assert archivos.file_info(str(pc["notas"] / "lista.txt"))["data"]["tamano"].endswith("bytes")
