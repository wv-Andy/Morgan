"""
La escritura en el PC (3.3): crear, editar, crear carpetas, mover y borrar.

Lo que exige el plan (§V3.3: *ninguna mutación remota puede saltarse por accidente la
política, la autorización, la confirmación o la auditoría*) y lo que decidí el
2026-09-24:

- **Carpetas de escritura aparte**, vacías al empezar: leer una carpeta no deja
  cambiarla.
- **Solo borrar se confirma en el PC**; crear, editar y mover pasan por el plan
  aprobado en el móvil.
- **Borrar es mandar a la Papelera.**
- **Ejecutables, nunca**, en ninguna carpeta.

Y lo que no depende de ninguna decisión: la carpeta del agente, la de Inicio, el
sistema y los datos de las aplicaciones no se escriben nunca; crear y mover no pisan;
editar no pisa un cambio hecho entre medias.
"""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from src.agente import archivos, aviso, capacidades, escritura, motor
from src.agente import estado as almacen
from src.agente.politica import DE_ESCRITURA, DE_LECTURA, Politica

en_windows = pytest.mark.skipif(sys.platform != "win32", reason="rutas y Papelera de Windows")


@pytest.fixture(autouse=True)
def sistema_de_mentira(tmp_path, monkeypatch):
    """Las carpetas temporales de las pruebas están dentro de %LOCALAPPDATA%, que es zona
    prohibida: las zonas del sistema se llevan a otro sitio para poder escribir aquí.
    `TestLasZonasDeVerdad` comprueba las de verdad."""
    falso = tmp_path / "_sistema"
    for variable in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
        carpeta = falso / variable
        carpeta.mkdir(parents=True)
        monkeypatch.setenv(variable, str(carpeta))
    return falso


@pytest.fixture
def preguntas(monkeypatch):
    """La notificación, sin enseñarla: se apunta qué se preguntó y se contesta lo que diga
    la prueba (por defecto, «Permitir»)."""
    hechas = []
    respuesta = {"valor": aviso.PERMITIDA}

    def preguntar(titulo, detalle, espera=aviso.ESPERA):
        hechas.append((titulo, detalle))
        return respuesta["valor"]

    monkeypatch.setattr(aviso, "preguntar", preguntar)
    return {"hechas": hechas, "respuesta": respuesta}


@pytest.fixture
def papelera(monkeypatch):
    """La Papelera, sin llenar la de verdad: se apunta qué se mandó y se quita del disco."""
    mandadas = []

    def a_la_papelera(ruta):
        mandadas.append(ruta)
        ruta.rmdir() if ruta.is_dir() else ruta.unlink()

    monkeypatch.setattr(escritura, "a_la_papelera", a_la_papelera)
    return mandadas


@pytest.fixture
def pc(tmp_path, preguntas, papelera):
    """Un PC con una carpeta de solo lectura, una de escritura y todo encendido."""
    lectura = tmp_path / "Lectura"
    notas = tmp_path / "Notas"
    fuera = tmp_path / "Fuera"
    for c in (lectura, notas, fuera):
        c.mkdir()
    (lectura / "solo_leer.txt").write_text("no me toques", encoding="utf-8")
    (notas / "lista.txt").write_text("pan\nleche\n", encoding="utf-8")
    (fuera / "ajeno.txt").write_text("ajeno", encoding="utf-8")
    politica = Politica()
    politica.anadir(str(lectura))
    politica.anadir(str(notas))
    politica.permitir_escritura(str(notas))
    for c in DE_ESCRITURA:
        politica.capacidades[c] = True
    politica.guardar()
    return {"lectura": lectura, "notas": notas, "fuera": fuera,
            "preguntas": preguntas, "papelera": papelera}


def _foto(carpeta: Path) -> dict:
    """Todo lo que hay debajo, con su contenido: para ver que NADA cambió."""
    return {str(p.relative_to(carpeta)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(carpeta.rglob("*"))}


# --- Lo que decidí --------------------------------------------------------------

class TestNaceCerrada:
    def test_las_de_escritura_nacen_apagadas(self):
        politica = Politica()
        assert not any(politica.capacidades[c] for c in DE_ESCRITURA)
        assert all(politica.capacidades[c] for c in DE_LECTURA)

    def test_sin_carpetas_de_escritura_no_se_anuncian(self, tmp_path):
        politica = Politica()
        politica.anadir(str(tmp_path))
        for c in DE_ESCRITURA:
            politica.capacidades[c] = True
        assert not set(DE_ESCRITURA) & set(capacidades.disponibles(politica))

    def test_apagadas_no_se_anuncian_aunque_haya_carpetas(self, tmp_path):
        politica = Politica()
        (tmp_path / "N").mkdir()
        politica.permitir_escritura(str(tmp_path / "N"))
        assert not set(DE_ESCRITURA) & set(capacidades.disponibles(politica))

    def test_encendidas_y_con_carpeta_si(self, pc):
        assert set(DE_ESCRITURA) <= set(capacidades.disponibles())

    def test_apagada_no_escribe_aunque_se_pida(self, pc):
        politica = Politica.cargar()
        politica.capacidades["create_file"] = False
        politica.guardar()
        r = escritura.create_file(str(pc["notas"] / "nuevo.txt"), "x")
        assert not r["success"] and r["motivo"] == "capacidad_apagada"
        assert not (pc["notas"] / "nuevo.txt").exists()


class TestLeerNoEsEscribir:
    def test_una_carpeta_de_lectura_no_se_escribe(self, pc):
        antes = _foto(pc["lectura"])
        r = escritura.create_file(str(pc["lectura"] / "nuevo.txt"), "x")
        assert not r["success"] and r["motivo"] == "fuera"
        r = escritura.edit_file(str(pc["lectura"] / "solo_leer.txt"), "no me", "sí te")
        assert not r["success"]
        r = escritura.delete_file(str(pc["lectura"] / "solo_leer.txt"))
        assert not r["success"]
        assert _foto(pc["lectura"]) == antes

    def test_ni_mover_de_ella_a_una_de_escritura(self, pc):
        r = escritura.move_file(str(pc["lectura"] / "solo_leer.txt"), str(pc["notas"] / "robado.txt"))
        assert not r["success"] and (pc["lectura"] / "solo_leer.txt").exists()

    def test_ni_sacar_algo_fuera(self, pc):
        r = escritura.move_file(str(pc["notas"] / "lista.txt"), str(pc["fuera"] / "lista.txt"))
        assert not r["success"] and (pc["notas"] / "lista.txt").exists()

    def test_subir_con_puntos_no_sale(self, pc):
        r = escritura.create_file(str(pc["notas"] / ".." / "Fuera" / "x.txt"), "x")
        assert not r["success"] and not (pc["fuera"] / "x.txt").exists()

    def test_lo_bloqueado_gana_tambien_al_escribir(self, pc):
        (pc["notas"] / "Privado").mkdir()
        politica = Politica.cargar()
        politica.bloquear(str(pc["notas"] / "Privado"))
        politica.guardar()
        r = escritura.create_file(str(pc["notas"] / "Privado" / "x.txt"), "x")
        assert not r["success"] and r["motivo"] == "bloqueada"


class TestSoloBorrarSeConfirma:
    def test_crear_editar_y_mover_no_preguntan(self, pc):
        assert escritura.create_file(str(pc["notas"] / "a.txt"), "a")["success"]
        assert escritura.edit_file(str(pc["notas"] / "a.txt"), "a", "b")["success"]
        assert escritura.move_file(str(pc["notas"] / "a.txt"), str(pc["notas"] / "b.txt"))["success"]
        assert escritura.create_folder(str(pc["notas"] / "Sub"))["success"]
        assert pc["preguntas"]["hechas"] == []

    def test_mover_y_crear_carpeta_dicen_lo_que_comprobaron(self, pc):
        """4.0-B: la nube verifica lo del PC con lo que comprobó el agente allí."""
        assert escritura.create_file(str(pc["notas"] / "c.txt"), "c")["success"]
        movido = escritura.move_file(str(pc["notas"] / "c.txt"), str(pc["notas"] / "d.txt"))
        carpeta = escritura.create_folder(str(pc["notas"] / "Otra"))
        assert movido["data"]["comprobado"] == "ya no está en el origen y sí en el destino"
        assert carpeta["data"]["comprobado"] == "la carpeta existe"

    def test_borrar_pregunta_con_la_ruta(self, pc):
        r = escritura.delete_file(str(pc["notas"] / "lista.txt"))
        assert r["success"]
        (titulo, detalle), = pc["preguntas"]["hechas"]
        assert "BORRAR" in titulo and "lista.txt" in detalle

    @pytest.mark.parametrize("respuesta", ["rechazada", "cerrada", "sin_respuesta",
                                           "no_disponible", "sin_tiempo", "", "PERMITIDA"])
    def test_cualquier_cosa_que_no_sea_permitir_es_un_no(self, pc, respuesta):
        pc["preguntas"]["respuesta"]["valor"] = respuesta
        antes = _foto(pc["notas"])
        r = escritura.delete_file(str(pc["notas"] / "lista.txt"))
        assert not r["success"] and r["motivo"] == "no_confirmada"
        assert r["_auditoria"]["confirmacion"] == respuesta
        assert _foto(pc["notas"]) == antes and pc["papelera"] == []

    def test_se_puede_pedir_para_todo_en_el_pc(self, pc):
        politica = Politica.cargar()
        politica.confirmar["escribir"] = True
        politica.guardar()
        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        r = escritura.create_file(str(pc["notas"] / "a.txt"), "a")
        assert not r["success"] and not (pc["notas"] / "a.txt").exists()
        assert len(pc["preguntas"]["hechas"]) == 1

    def test_no_se_pregunta_por_algo_que_no_se_podra_hacer(self, pc):
        """Crear lo que ya existe falla igual (el renombrado no pisa), pero se sabe antes
        de preguntar: una notificación para nada enseña a darle a «Permitir» sin leer."""
        politica = Politica.cargar()
        politica.confirmar["escribir"] = True
        politica.guardar()
        (pc["notas"] / "Sub").mkdir()
        assert escritura.create_file(str(pc["notas"] / "lista.txt"), "x")["motivo"] == "ya_existe"
        # Una carpeta que ya existe está hecha (4.6), también sin preguntar.
        assert escritura.create_folder(str(pc["notas"] / "Sub"))["data"]["ya_existia"]
        assert escritura.move_file(str(pc["notas"] / "lista.txt"), str(pc["notas"] / "Sub"))["motivo"] == "ya_existe"
        assert pc["preguntas"]["hechas"] == []

    def test_y_quitar_la_de_borrar(self, pc):
        politica = Politica.cargar()
        politica.confirmar["borrar"] = False
        politica.guardar()
        assert escritura.delete_file(str(pc["notas"] / "lista.txt"))["success"]
        assert pc["preguntas"]["hechas"] == []


class TestNingunaSeSaltaLaConfirmacion:
    """El gate de la 3.3: con todo pidiendo confirmación y la persona diciendo que no,
    **ninguna** de las cinco cambia nada. Si mañana se añade una sexta sin pasar por
    `_autorizar`, esta prueba la pilla por el nombre."""

    LLAMADAS = {
        "create_file": lambda n: escritura.create_file(str(n / "nuevo.txt"), "x"),
        "edit_file": lambda n: escritura.edit_file(str(n / "lista.txt"), "pan", "vino"),
        "append_file": lambda n: escritura.append_file(str(n / "lista.txt"), "y vino"),
        "create_folder": lambda n: escritura.create_folder(str(n / "Sub")),
        "move_file": lambda n: escritura.move_file(str(n / "lista.txt"), str(n / "otra.txt")),
        "delete_file": lambda n: escritura.delete_file(str(n / "lista.txt")),
        # Las de la 4.6 pasan por el mismo `_autorizar`.
        "copy_path": lambda n: archivos.copy_path(str(n / "lista.txt"), str(n / "copia.txt")),
        "compress": lambda n: archivos.compress("comprimir", paths=[str(n / "lista.txt")],
                                                dst=str(n / "todo.zip")),
    }

    def test_estan_todas(self):
        assert set(self.LLAMADAS) == set(escritura.ESCRITURA) | set(archivos.ARCHIVOS_ESCRITURA) \
            == set(DE_ESCRITURA)

    @pytest.mark.parametrize("capacidad", DE_ESCRITURA)
    def test_con_un_no_no_cambia_nada(self, pc, capacidad):
        politica = Politica.cargar()
        politica.confirmar.update({"escribir": True, "borrar": True})
        politica.guardar()
        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        antes = _foto(pc["notas"])

        r = self.LLAMADAS[capacidad](pc["notas"])

        assert not r["success"] and r["motivo"] == "no_confirmada"
        assert len(pc["preguntas"]["hechas"]) == 1
        assert _foto(pc["notas"]) == antes


class TestEjecutablesNunca:
    @pytest.mark.parametrize("nombre", ["virus.exe", "script.PS1", "factura.pdf.exe", "atajo.lnk",
                                        "cosa.bat", "cosa.cmd", "cosa.vbs", "cosa.js", "cosa.reg",
                                        "cosa.hta", "cosa.scr", "cosa.url"])
    def test_no_se_crean(self, pc, nombre):
        r = escritura.create_file(str(pc["notas"] / nombre), "echo hola")
        assert not r["success"] and r["motivo"] == "ejecutable"
        assert not (pc["notas"] / nombre).exists()

    def test_ni_se_renombra_algo_a_ejecutable(self, pc):
        r = escritura.move_file(str(pc["notas"] / "lista.txt"), str(pc["notas"] / "lista.bat"))
        assert not r["success"] and (pc["notas"] / "lista.txt").exists()

    def test_ni_se_edita_uno_que_ya_estaba(self, pc):
        (pc["notas"] / "arranca.bat").write_text("echo hola", encoding="utf-8")
        r = escritura.edit_file(str(pc["notas"] / "arranca.bat"), "hola", "adios")
        assert not r["success"] and r["motivo"] == "ejecutable"
        assert (pc["notas"] / "arranca.bat").read_text(encoding="utf-8") == "echo hola"

    def test_ni_se_le_cambia_el_nombre_a_uno(self, pc):
        (pc["notas"] / "arranca.bat").write_text("echo", encoding="utf-8")
        r = escritura.move_file(str(pc["notas"] / "arranca.bat"), str(pc["notas"] / "arranca.txt"))
        assert not r["success"] and (pc["notas"] / "arranca.bat").exists()


class TestNombresQueNoValen:
    @pytest.mark.parametrize("nombre", [
        "desktop.ini", "autorun.inf", "NUL.txt", "con", "COM1.log", "nota.txt.", "nota.txt ",
        "doc\u202etxt.exe", "a\nb.txt", ".env", "id_rsa", "clave.pem",
    ])
    def test_no_se_crean(self, pc, nombre):
        r = escritura.create_file(str(pc["notas"]) + os.sep + nombre, "x")
        assert not r["success"], nombre
        assert set(os.listdir(pc["notas"])) == {"lista.txt"}

    def test_un_flujo_alternativo_tampoco(self, pc):
        r = escritura.create_file(str(pc["notas"] / "lista.txt") + ":oculto", "x")
        assert not r["success"] and r["motivo"] == "flujo"


class TestDondeNoSeEscribeNunca:
    def test_la_carpeta_del_agente(self, tmp_path, pc):
        """Con una carpeta de escritura que la contiene, sigue sin poder tocarse: si no,
        Morgan podría reescribir su propia política."""
        politica = Politica.cargar()
        assert almacen.carpeta().parent == tmp_path
        politica.escritura.append(str(tmp_path))
        politica.guardar()

        r = escritura.create_file(str(almacen.carpeta() / "politica.json.txt"), "{}")
        assert not r["success"]
        r = escritura.edit_file(str(almacen.carpeta() / "politica.json"), '"escritura"', '"x"')
        assert not r["success"]
        assert json.loads((almacen.carpeta() / "politica.json").read_text(encoding="utf-8"))["escritura"]

    @pytest.mark.parametrize("variable", ["APPDATA", "LOCALAPPDATA", "ProgramData"])
    def test_ni_se_permite_como_carpeta(self, sistema_de_mentira, variable):
        with pytest.raises(ValueError, match="no escribe nunca"):
            Politica().permitir_escritura(str(sistema_de_mentira / variable))

    def test_ni_dentro_de_una_permitida_que_la_contiene(self, tmp_path, sistema_de_mentira, pc):
        politica = Politica.cargar()
        politica.escritura.append(str(tmp_path))
        politica.guardar()
        r = escritura.create_file(str(sistema_de_mentira / "APPDATA" / "config.txt"), "x")
        assert not r["success"] and r["motivo"] == "zona_prohibida"

    def test_la_carpeta_permitida_entera_no_se_borra_ni_se_mueve(self, pc):
        assert escritura.delete_file(str(pc["notas"]))["motivo"] == "raiz"
        assert escritura.move_file(str(pc["notas"]), str(pc["fuera"].parent / "Otra"))["motivo"] == "raiz"
        assert pc["notas"].is_dir()


class TestLasZonasDeVerdad:
    """Sin la simulación: las del Windows de verdad."""

    @en_windows
    def test_appdata_y_el_sistema(self, monkeypatch):
        monkeypatch.undo()
        from src.agente.politica import en_zona_prohibida, zonas_prohibidas

        zonas = zonas_prohibidas()
        for variable in ("SystemRoot", "ProgramFiles", "APPDATA", "LOCALAPPDATA"):
            assert en_zona_prohibida(Path(os.environ[variable]) / "algo.txt"), variable
        assert len(zonas) >= 6

    @en_windows
    def test_la_carpeta_de_inicio(self, monkeypatch):
        monkeypatch.undo()
        from src.agente.politica import INICIO, carpeta_conocida, en_zona_prohibida

        inicio = carpeta_conocida(INICIO[0])
        assert inicio and "Startup" in inicio
        assert en_zona_prohibida(Path(inicio) / "morgan.txt")


# --- Cada una, haciendo lo suyo ---------------------------------------------------------

class TestCrear:
    def test_crea_lo_que_se_pide_y_lo_comprueba(self, pc):
        r = escritura.create_file(str(pc["notas"] / "receta.txt"), "Tortilla: huevos y papas\n")
        assert r["success"]
        contenido = (pc["notas"] / "receta.txt").read_bytes()
        assert contenido == "Tortilla: huevos y papas\n".encode("utf-8")
        assert r["data"]["sha256"] == hashlib.sha256(contenido).hexdigest()

    def test_no_pisa_lo_que_existe(self, pc):
        r = escritura.create_file(str(pc["notas"] / "lista.txt"), "otra cosa")
        assert not r["success"] and r["motivo"] == "ya_existe"
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8") == "pan\nleche\n"

    def test_ni_si_aparece_mientras_se_escribe(self, pc, monkeypatch):
        """Entre comprobar y crear, otro programa lo crea: no se pisa."""
        destino = pc["notas"] / "carrera.txt"
        original = escritura._escribir_temporal

        def y_aparece(d, datos):
            destino.write_text("lo de otro", encoding="utf-8")
            return original(d, datos)

        monkeypatch.setattr(escritura, "_escribir_temporal", y_aparece)
        r = escritura.create_file(str(destino), "lo de Morgan")
        assert not r["success"] and r["motivo"] == "ya_existe"
        assert destino.read_text(encoding="utf-8") == "lo de otro"
        assert not [p for p in pc["notas"].iterdir() if p.suffix == ".tmp"]

    def test_la_carpeta_tiene_que_existir(self, pc):
        r = escritura.create_file(str(pc["notas"] / "NoExiste" / "a.txt"), "x")
        assert not r["success"] and r["motivo"] == "no_existe"

    def test_ni_binarios_ni_enormes(self, pc):
        assert escritura.create_file(str(pc["notas"] / "a.txt"), "a\x00b")["motivo"] == "contenido"
        grande = "x" * (escritura.MAX_CONTENIDO + 1)
        assert escritura.create_file(str(pc["notas"] / "a.txt"), grande)["motivo"] == "demasiado_grande"
        assert escritura.create_file(str(pc["notas"] / "a.txt"), 5)["motivo"] == "contenido"

    def test_una_carpeta(self, pc):
        assert escritura.create_folder(str(pc["notas"] / "Viajes"))["success"]
        assert (pc["notas"] / "Viajes").is_dir()
        (pc["notas"] / "DeAntes").mkdir()
        # Ya existía (4.6): está hecho, sin tocarla. La raíz de escritura, también.
        r = escritura.create_folder(str(pc["notas"] / "DeAntes"))
        assert r["success"] and r["data"]["ya_existia"]
        assert escritura.create_folder(str(pc["notas"]))["data"]["ya_existia"]
        # Pero un archivo con ese nombre no es una carpeta, y fuera de la escritura, no.
        assert escritura.create_folder(str(pc["notas"] / "lista.txt"))["motivo"] == "ya_existe"
        assert not escritura.create_folder(str(pc["lectura"]))["success"]
        assert not escritura.create_folder(str(pc["fuera"]))["success"]


class TestEditar:
    def test_cambia_solo_el_fragmento(self, pc):
        r = escritura.edit_file(str(pc["notas"] / "lista.txt"), "leche", "leche de avena")
        assert r["success"]
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8") == "pan\nleche de avena\n"
        auditoria = r["_auditoria"]
        assert auditoria["sha256_antes"] != auditoria["sha256_despues"]

    def test_guarda_la_version_anterior(self, pc):
        r = escritura.edit_file(str(pc["notas"] / "lista.txt"), "pan", "arroz")
        respaldo = almacen.carpeta() / "respaldos" / r["_auditoria"]["respaldo"]
        assert respaldo.read_text(encoding="utf-8") == "pan\nleche\n"

    def test_los_respaldos_viejos_se_borran(self, pc):
        carpeta = almacen.carpeta() / "respaldos"
        carpeta.mkdir(parents=True)
        viejo = carpeta / "viejo.txt"
        viejo.write_text("x", encoding="utf-8")
        hace = (escritura.DIAS_RESPALDO + 1) * 86400
        os.utime(viejo, (viejo.stat().st_atime - hace, viejo.stat().st_mtime - hace))
        escritura.edit_file(str(pc["notas"] / "lista.txt"), "pan", "arroz")
        assert not viejo.exists()

    def test_si_no_esta_no_cambia_nada(self, pc):
        r = escritura.edit_file(str(pc["notas"] / "lista.txt"), "huevos", "papas")
        assert not r["success"] and r["motivo"] == "no_encontrado"

    def test_si_esta_varias_veces_tampoco(self, pc):
        (pc["notas"] / "doble.txt").write_text("sí\nsí\n", encoding="utf-8")
        r = escritura.edit_file(str(pc["notas"] / "doble.txt"), "sí", "no")
        assert not r["success"] and r["motivo"] == "varias_veces"
        assert (pc["notas"] / "doble.txt").read_text(encoding="utf-8") == "sí\nsí\n"

    def test_un_archivo_de_windows_con_crlf(self, pc):
        (pc["notas"] / "win.txt").write_bytes(b"uno\r\ndos\r\ntres\r\n")
        r = escritura.edit_file(str(pc["notas"] / "win.txt"), "uno\ndos", "uno\n2")
        assert r["success"]
        assert (pc["notas"] / "win.txt").read_bytes() == b"uno\r\n2\r\ntres\r\n"

    def test_respeta_el_bom(self, pc):
        (pc["notas"] / "bom.txt").write_bytes(b"\xef\xbb\xbfhola")
        assert escritura.edit_file(str(pc["notas"] / "bom.txt"), "hola", "adios")["success"]
        assert (pc["notas"] / "bom.txt").read_bytes() == b"\xef\xbb\xbfadios"

    def test_ni_binarios_ni_otras_codificaciones(self, pc):
        (pc["notas"] / "b.dat").write_bytes(b"ab\x00cd")
        assert escritura.edit_file(str(pc["notas"] / "b.dat"), "ab", "x")["motivo"] == "binario"
        (pc["notas"] / "latin.txt").write_bytes("canción".encode("latin-1"))
        assert escritura.edit_file(str(pc["notas"] / "latin.txt"), "can", "x")["motivo"] == "no_es_texto"
        assert (pc["notas"] / "latin.txt").read_bytes() == "canción".encode("latin-1")

    def test_no_pisa_un_cambio_hecho_entre_medias(self, pc, monkeypatch):
        """TOCTOU (§18): la persona guarda el archivo justo mientras Morgan lo edita."""
        ruta = pc["notas"] / "lista.txt"
        original = escritura._respaldar

        def y_la_persona_guarda(r, datos):
            ruta.write_text("pan\nleche\nhuevos\n", encoding="utf-8")
            return original(r, datos)

        monkeypatch.setattr(escritura, "_respaldar", y_la_persona_guarda)
        r = escritura.edit_file(str(ruta), "pan", "arroz")
        assert not r["success"] and r["motivo"] == "cambiado"
        assert ruta.read_text(encoding="utf-8") == "pan\nleche\nhuevos\n"
        assert not [p for p in pc["notas"].iterdir() if p.suffix == ".tmp"]

    def test_falta_el_fragmento(self, pc):
        assert escritura.edit_file(str(pc["notas"] / "lista.txt"), "", "x")["motivo"] == "fragmento"


class TestMover:
    def test_renombra(self, pc):
        r = escritura.move_file(str(pc["notas"] / "lista.txt"), str(pc["notas"] / "compra.txt"))
        assert r["success"]
        assert (pc["notas"] / "compra.txt").read_text(encoding="utf-8") == "pan\nleche\n"
        assert not (pc["notas"] / "lista.txt").exists()

    def test_a_una_subcarpeta(self, pc):
        (pc["notas"] / "Viejas").mkdir()
        assert escritura.move_file(str(pc["notas"] / "lista.txt"),
                                   str(pc["notas"] / "Viejas" / "lista.txt"))["success"]

    def test_no_pisa_el_destino(self, pc):
        (pc["notas"] / "otra.txt").write_text("otra", encoding="utf-8")
        r = escritura.move_file(str(pc["notas"] / "lista.txt"), str(pc["notas"] / "otra.txt"))
        assert not r["success"] and r["motivo"] == "ya_existe"
        assert (pc["notas"] / "otra.txt").read_text(encoding="utf-8") == "otra"
        assert (pc["notas"] / "lista.txt").exists()


class TestBorrar:
    def test_va_a_la_papelera(self, pc):
        r = escritura.delete_file(str(pc["notas"] / "lista.txt"))
        assert r["success"] and r["data"]["papelera"]
        assert pc["papelera"] == [pc["notas"] / "lista.txt"]

    def test_una_carpeta_con_cosas_no_y_sin_preguntar(self, pc):
        (pc["notas"] / "Llena").mkdir()
        (pc["notas"] / "Llena" / "x.txt").write_text("x", encoding="utf-8")
        r = escritura.delete_file(str(pc["notas"] / "Llena"))
        assert not r["success"] and r["motivo"] == "carpeta_no_vacia"
        assert pc["preguntas"]["hechas"] == [] and pc["papelera"] == []

    def test_una_carpeta_vacia_si(self, pc):
        (pc["notas"] / "Vacia").mkdir()
        assert escritura.delete_file(str(pc["notas"] / "Vacia"))["success"]

    def test_si_se_llena_mientras_la_persona_decide_no(self, pc):
        (pc["notas"] / "Vacia").mkdir()

        def y_mientras_tanto(titulo, detalle, espera=aviso.ESPERA):
            (pc["notas"] / "Vacia" / "nuevo.txt").write_text("x", encoding="utf-8")
            return aviso.PERMITIDA

        aviso.preguntar = y_mientras_tanto        # el fixture lo restaura
        r = escritura.delete_file(str(pc["notas"] / "Vacia"))
        # La caza la comprobación de identidad de después de confirmar (3.3.5): la
        # carpeta cambió. Si no, la de «vacía» de justo antes de borrar.
        assert not r["success"] and r["motivo"] in ("cambiado", "carpeta_no_vacia")
        assert (pc["notas"] / "Vacia" / "nuevo.txt").exists()

    def test_un_disco_sin_papelera_no(self, pc, monkeypatch):
        monkeypatch.setattr(escritura, "_disco_con_papelera", lambda ruta: False)
        r = escritura.delete_file(str(pc["notas"] / "lista.txt"))
        assert not r["success"] and r["motivo"] == "sin_papelera"
        assert (pc["notas"] / "lista.txt").exists() and pc["preguntas"]["hechas"] == []

    @en_windows
    def test_el_disco_de_las_pruebas_tiene_papelera(self, tmp_path):
        assert escritura._disco_con_papelera(tmp_path)


class TestRepetirNoLoHaceDosVeces:
    """3.3.5: si la respuesta se pierde, el paso del plan no se gasta y la llamada puede
    repetirse con otro `command_id`. No puede hacerse dos veces."""

    def test_una_edicion_repetida_no_se_aplica_otra_vez(self, pc):
        ruta = str(pc["notas"] / "lista.txt")
        escritura.edit_file(ruta, "pan", "pan integral")
        r = escritura.edit_file(ruta, "pan", "pan integral")
        assert r["success"] and r["data"]["ya_hecho"]
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8") == "pan integral\nleche\n"

    @pytest.mark.parametrize("hacer", [
        lambda n: escritura.create_file(str(n / "a.txt"), "hola"),
        lambda n: escritura.create_folder(str(n / "Sub")),
        lambda n: escritura.move_file(str(n / "lista.txt"), str(n / "compra.txt")),
        lambda n: escritura.delete_file(str(n / "lista.txt")),
    ])
    def test_lo_hecho_se_contesta_como_hecho(self, pc, hacer):
        primera = hacer(pc["notas"])
        antes = _foto(pc["notas"])
        segunda = hacer(pc["notas"])
        assert primera["success"] and segunda["success"] and segunda["data"]["ya_hecho"]
        assert _foto(pc["notas"]) == antes

    def test_la_misma_orden_del_mismo_paso_tampoco(self, pc):
        """El reintento trae el mismo `origen` (plan#paso): sigue siendo «ya estaba hecho»."""
        ruta = str(pc["notas"] / "lista.txt")
        escritura.edit_file(ruta, "pan", "pan integral", origen="plan-a#1")
        r = escritura.edit_file(ruta, "pan", "pan integral", origen="plan-a#1")
        assert r["success"] and r["data"]["ya_hecho"]

    def test_otra_ejecucion_de_una_automatizacion_si_se_hace(self, pc):
        """4.15: cada ejecución trae su propio plan. Con el mismo `origen` ignorado, una
        automatización cada hora contestaba «ya estaba hecho» sin hacer nada."""
        escritura.create_file(str(pc["notas"] / "a.txt"), "hola", origen="plan-lunes#1")
        (pc["notas"] / "a.txt").unlink()
        Path(pc["notas"] / "a.txt").write_text("hola", encoding="utf-8")   # el disco, igual que lo dejó
        r = escritura.create_file(str(pc["notas"] / "a.txt"), "hola", origen="plan-martes#1")
        assert not r["success"] and not (r.get("data") or {}).get("ya_hecho"), "se intenta de verdad (y no pisa)"

    def test_repetir_un_borrado_no_vuelve_a_preguntar(self, pc):
        escritura.delete_file(str(pc["notas"] / "lista.txt"))
        escritura.delete_file(str(pc["notas"] / "lista.txt"))
        assert len(pc["preguntas"]["hechas"]) == 1

    def test_si_el_disco_cambio_se_hace_de_nuevo_con_sus_reglas(self, pc):
        ruta = pc["notas"] / "a.txt"
        escritura.create_file(str(ruta), "hola")
        ruta.write_text("lo cambió la persona", encoding="utf-8")
        r = escritura.create_file(str(ruta), "hola")
        assert not r["success"] and r["motivo"] == "ya_existe"
        assert ruta.read_text(encoding="utf-8") == "lo cambió la persona"

    def test_otros_argumentos_son_otra_orden(self, pc):
        escritura.create_file(str(pc["notas"] / "a.txt"), "hola")
        r = escritura.create_file(str(pc["notas"] / "a.txt"), "adios")
        assert not r["success"] and r["motivo"] == "ya_existe"

    def test_se_olvida_pasada_la_hora(self, pc, monkeypatch):
        ruta = str(pc["notas"] / "lista.txt")
        escritura.edit_file(ruta, "pan", "pan integral")
        ahora = __import__("time").time()
        monkeypatch.setattr(escritura.time, "time", lambda: ahora + escritura.RECUERDA_HECHAS + 1)
        r = escritura.edit_file(ruta, "pan", "pan integral")
        assert r["success"] and not r["data"].get("ya_hecho")

    def test_sobrevive_a_reiniciar_el_agente(self, pc):
        """Está en disco: un agente nuevo (sin memoria de órdenes) lo sabe igual."""
        from src.agente.ejecutor import Ejecutor

        orden = {"tipo": "orden", "request_id": "r", "agent_id": "ag", "vence_en_ms": 30_000,
                 "protocol_version": __import__("src.agente.protocolo", fromlist=["x"]).PROTOCOLO_ACTUAL,
                 "capability": "edit_file",
                 "arguments": {"path": str(pc["notas"] / "lista.txt"), "old_text": "pan", "new_text": "pan integral"}}
        asyncio.run(Ejecutor("ag", capacidades.disponibles()).procesar({**orden, "command_id": "1"}))
        r = asyncio.run(Ejecutor("ag", capacidades.disponibles()).procesar({**orden, "command_id": "2"}))
        assert r["estado"] == "COMPLETED" and r["resultado"]["data"]["ya_hecho"]
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8") == "pan integral\nleche\n"

    def test_estan_las_cinco(self):
        assert all(hasattr(f, "__wrapped__") for f in escritura.ESCRITURA.values())


class TestLoConfirmadoEsLoQueSeHace:
    """3.3.5, «confirmación que no coincide»: la persona tarda en decidir, y en ese rato
    el archivo puede cambiar. Encontrado atacándolo: se borraba igual."""

    def _mientras_decide(self, hacer):
        def preguntar(titulo, detalle, espera=aviso.ESPERA):
            hacer()
            return aviso.PERMITIDA
        aviso.preguntar = preguntar           # el fixture lo restaura

    def test_otro_archivo_con_el_mismo_nombre(self, pc):
        ruta = pc["notas"] / "lista.txt"

        def cambiarlo():
            ruta.unlink()
            ruta.write_text("OTRO ARCHIVO, MÁS LARGO", encoding="utf-8")

        self._mientras_decide(cambiarlo)
        r = escritura.delete_file(str(ruta))
        assert not r["success"] and r["motivo"] == "cambiado" and ruta.exists()
        assert pc["papelera"] == []

    @en_windows
    def test_la_carpeta_pasa_a_ser_un_enlace(self, pc, tmp_path):
        import _winapi

        (pc["notas"] / "Sub").mkdir()
        (pc["notas"] / "Sub" / "x.txt").write_text("x", encoding="utf-8")
        (pc["fuera"] / "x.txt").write_text("FUERA", encoding="utf-8")

        def enlazar():
            (pc["notas"] / "Sub" / "x.txt").unlink()
            (pc["notas"] / "Sub").rmdir()
            _winapi.CreateJunction(str(pc["fuera"]), str(pc["notas"] / "Sub"))

        self._mientras_decide(enlazar)
        r = escritura.delete_file(str(pc["notas"] / "Sub" / "x.txt"))
        assert not r["success"] and (pc["fuera"] / "x.txt").exists()
        os.rmdir(pc["notas"] / "Sub")


class TestSiLaCarpetaCambiaAlEscribir:
    """3.3.5, TOCTOU: justo después de la política, la carpeta se cambia por un enlace a
    otro sitio. Hace falta un programa con los permisos de la persona, pero comprobarlo
    es barato: se pregunta a Windows dónde quedó de verdad lo escrito."""

    @pytest.fixture
    def enlazar_tras_evaluar(self, pc, monkeypatch):
        import _winapi

        evaluar = motor.evaluar

        def preparar(carpeta: Path):
            def y_cambiar(*a, **k):
                d = evaluar(*a, **k)
                if d and carpeta.is_dir() and not os.path.islink(carpeta) and carpeta in d.ruta.parents:
                    for hijo in carpeta.iterdir():
                        hijo.unlink()
                    carpeta.rmdir()
                    _winapi.CreateJunction(str(pc["fuera"]), str(carpeta))
                return d
            monkeypatch.setattr(motor, "evaluar", y_cambiar)
        yield preparar
        for c in pc["notas"].iterdir():
            if c.is_dir() and escritura._ruta_final(c) != str(c):
                os.rmdir(c)

    @en_windows
    def test_crear(self, pc, enlazar_tras_evaluar):
        (pc["notas"] / "Sub").mkdir()
        enlazar_tras_evaluar(pc["notas"] / "Sub")
        r = escritura.create_file(str(pc["notas"] / "Sub" / "colado.txt"), "x")
        assert not r["success"] and r["motivo"] == "cambiado"
        assert os.listdir(pc["fuera"]) == ["ajeno.txt"]

    @en_windows
    def test_editar(self, pc, enlazar_tras_evaluar):
        (pc["notas"] / "Sub").mkdir()
        (pc["notas"] / "Sub" / "ajeno.txt").write_text("ajeno", encoding="utf-8")
        enlazar_tras_evaluar(pc["notas"] / "Sub")
        r = escritura.edit_file(str(pc["notas"] / "Sub" / "ajeno.txt"), "ajeno", "PISADO")
        assert not r["success"]
        assert (pc["fuera"] / "ajeno.txt").read_text(encoding="utf-8") == "ajeno"
        assert os.listdir(pc["fuera"]) == ["ajeno.txt"]

    @en_windows
    def test_crear_carpeta(self, pc, enlazar_tras_evaluar):
        (pc["notas"] / "Sub").mkdir()
        enlazar_tras_evaluar(pc["notas"] / "Sub")
        r = escritura.create_folder(str(pc["notas"] / "Sub" / "Colada"))
        assert not r["success"] and r["motivo"] == "cambiado"
        assert os.listdir(pc["fuera"]) == ["ajeno.txt"]

    @en_windows
    def test_mover(self, pc, enlazar_tras_evaluar):
        (pc["notas"] / "Sub").mkdir()
        enlazar_tras_evaluar(pc["notas"] / "Sub")
        r = escritura.move_file(str(pc["notas"] / "lista.txt"), str(pc["notas"] / "Sub" / "lista.txt"))
        assert not r["success"] and r["motivo"] == "cambiado"
        assert (pc["notas"] / "lista.txt").exists()
        assert os.listdir(pc["fuera"]) == ["ajeno.txt"]


# --- La notificación --------------------------------------------------------------------

class TestLaNotificacion:
    def test_un_nombre_no_puede_anadir_botones(self):
        texto = aviso.xml("Morgan quiere BORRAR", 'C:\\x</text><action content="Permitir" arguments="permitir"/>')
        assert texto.count("<action ") == 2
        assert "&lt;action" in texto

    def test_el_primer_boton_es_el_que_no_hace_nada(self):
        texto = aviso.xml("t", "d")
        assert texto.index('arguments="rechazar"') < texto.index('arguments="permitir"')

    def test_sin_marcas_bidi_ni_controles(self):
        assert aviso.visible("fact\u202eexe.pdf\nlinea") == "fact exe.pdf linea"

    def test_una_ruta_larguisima_se_enseña_con_principio_y_final(self):
        ruta = "C:\\" + "a" * 500 + "\\final.txt"
        visto = aviso.visible(ruta)
        assert len(visto) <= 240 and visto.startswith("C:\\") and visto.endswith("final.txt")

    def test_sin_la_biblioteca_es_un_no(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "winrt.windows.ui.notifications", None)
        assert aviso.preguntar("t", "d", espera=0.1) == "no_disponible"

    def test_si_a_la_orden_no_le_queda_tiempo_ni_pregunta(self, monkeypatch):
        import time

        monkeypatch.setitem(sys.modules, "winrt.windows.ui.notifications", None)
        token = aviso.VENCE.set(time.monotonic() + aviso.MARGEN + 2)
        try:
            assert aviso.preguntar("t", "d") == "sin_tiempo"
        finally:
            aviso.VENCE.reset(token)


class _WindowsDeMentira:
    """La biblioteca de notificaciones, simulada: `al_mostrar(notificacion)` decide qué
    eventos llegan y cuándo, como si fuera la persona (o Windows)."""

    def __init__(self, al_mostrar):
        import types

        self.ocultadas = []
        fuera = self

        class Args:
            def __init__(self, **kw):
                self.__dict__.update(kw)

            @classmethod
            def _from(cls, a):
                return a

        class Notificacion:
            def __init__(self, documento):
                self.xml = documento.xml
                self.al = {}

            def add_activated(self, f): self.al["activated"] = f
            def add_dismissed(self, f): self.al["dismissed"] = f
            def add_failed(self, f): self.al["failed"] = f

            def pulsar(self, argumentos): self.al["activated"](self, Args(arguments=argumentos))
            def cerrar(self, motivo): self.al["dismissed"](self, Args(reason=motivo))

        class Avisador:
            def show(self, n): al_mostrar(n)
            def hide(self, n): fuera.ocultadas.append(n)

        class Documento:
            def load_xml(self, xml): self.xml = xml

        self.notificaciones = types.SimpleNamespace(
            ToastActivatedEventArgs=Args, ToastDismissedEventArgs=Args,
            ToastNotification=Notificacion,
            ToastNotificationManager=types.SimpleNamespace(
                create_toast_notifier_with_id=lambda aumid: Avisador()))
        self.dom = types.SimpleNamespace(XmlDocument=Documento)

    def instalar(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "winrt.windows.ui.notifications", self.notificaciones)
        monkeypatch.setitem(sys.modules, "winrt.windows.data.xml.dom", self.dom)
        monkeypatch.setattr(aviso, "_registrar_nombre", lambda: None)
        return self


class TestLaRespuestaDeLaNotificacion:
    """Qué devuelve `preguntar` según lo que pase en la pantalla. **Solo el botón
    «Permitir» es un sí.**"""

    def _pregunta(self, monkeypatch, al_mostrar, espera=0.3):
        windows = _WindowsDeMentira(al_mostrar).instalar(monkeypatch)
        return aviso.preguntar("t", "d", espera=espera), windows

    def test_permitir(self, monkeypatch):
        assert self._pregunta(monkeypatch, lambda n: n.pulsar("permitir"))[0] == aviso.PERMITIDA

    def test_rechazar(self, monkeypatch):
        assert self._pregunta(monkeypatch, lambda n: n.pulsar("rechazar"))[0] == "rechazada"

    def test_pulsar_la_notificacion_y_no_un_boton_es_un_no(self, monkeypatch):
        assert self._pregunta(monkeypatch, lambda n: n.pulsar(""))[0] == "rechazada"

    def test_cerrarla_es_un_no(self, monkeypatch):
        assert self._pregunta(monkeypatch, lambda n: n.cerrar(0))[0] == "cerrada"

    def test_si_windows_la_retira_sola_se_sigue_esperando(self, monkeypatch):
        """`TimedOut` la manda al centro de notificaciones, donde aún se puede pulsar."""
        def se_retira_y_luego_permite(n):
            n.cerrar(2)
            n.pulsar("permitir")

        assert self._pregunta(monkeypatch, se_retira_y_luego_permite)[0] == aviso.PERMITIDA

    def test_retirada_y_sin_respuesta_es_un_no(self, monkeypatch):
        assert self._pregunta(monkeypatch, lambda n: n.cerrar(2))[0] == "sin_respuesta"

    def test_sin_respuesta_se_quita_de_la_pantalla(self, monkeypatch):
        respuesta, windows = self._pregunta(monkeypatch, lambda n: None, espera=0.1)
        assert respuesta == "sin_respuesta" and len(windows.ocultadas) == 1

    def test_un_permitir_tardio_no_cambia_nada(self, monkeypatch):
        """Un «Permitir» que llega cuando ya se dio por perdida no la convierte en un sí."""
        guardada = {}
        respuesta, _ = self._pregunta(monkeypatch, lambda n: guardada.setdefault("n", n), espera=0.1)
        guardada["n"].pulsar("permitir")
        assert respuesta == "sin_respuesta"

    def test_la_primera_respuesta_manda(self, monkeypatch):
        def rechaza_y_luego_permite(n):
            n.pulsar("rechazar")
            n.pulsar("permitir")

        assert self._pregunta(monkeypatch, rechaza_y_luego_permite)[0] == "rechazada"

    def test_el_xml_que_llega_a_windows_es_el_escapado(self, monkeypatch):
        visto = {}
        _WindowsDeMentira(lambda n: visto.setdefault("xml", n.xml)).instalar(monkeypatch)
        aviso.preguntar("t", '</text><action content="Permitir" arguments="permitir"/>', espera=0.05)
        assert visto["xml"].count("<action ") == 2


# --- El ejecutor y la auditoría ---------------------------------------------------------

class TestLaAuditoria:
    def _orden(self, capacidad, argumentos):
        from src.agente.protocolo import PROTOCOLO_ACTUAL

        return {"tipo": "orden", "command_id": "c-1", "request_id": "r-1", "agent_id": "ag-1",
                "protocol_version": PROTOCOLO_ACTUAL, "capability": capacidad,
                "arguments": argumentos, "vence_en_ms": 30_000}

    def test_queda_en_el_pc_y_no_sale(self, pc):
        from src.agente import auditoria
        from src.agente.ejecutor import Ejecutor

        ejecutor = Ejecutor("ag-1", capacidades.disponibles())
        respuesta = asyncio.run(ejecutor.procesar(self._orden(
            "edit_file", {"path": str(pc["notas"] / "lista.txt"), "old_text": "pan", "new_text": "arroz"})))

        assert respuesta["estado"] == "COMPLETED"
        assert "_auditoria" not in json.dumps(respuesta)
        ejecutada = [e for e in auditoria.leer() if e["fase"] == "ejecutada"][-1]
        assert ejecutada["confirmacion"] == "no_hacia_falta"
        assert ejecutada["sha256_antes"] and ejecutada["sha256_despues"] and ejecutada["respaldo"]

    def test_un_no_tambien_se_anota(self, pc):
        from src.agente import auditoria
        from src.agente.ejecutor import Ejecutor

        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        ejecutor = Ejecutor("ag-1", capacidades.disponibles())
        respuesta = asyncio.run(ejecutor.procesar(self._orden(
            "delete_file", {"path": str(pc["notas"] / "lista.txt")})))
        assert respuesta["estado"] == "FAILED"
        assert [e for e in auditoria.leer() if e["fase"] == "ejecutada"][-1]["confirmacion"] == "rechazada"

    def test_la_confirmacion_no_dura_mas_que_la_orden(self, pc):
        """El ejecutor le pasa a la notificación cuándo vence la orden."""
        from src.agente.ejecutor import Ejecutor

        vistos = []

        def preguntar(titulo, detalle, espera=aviso.ESPERA):
            vistos.append(aviso.VENCE.get())
            return aviso.PERMITIDA

        aviso.preguntar = preguntar
        ejecutor = Ejecutor("ag-1", capacidades.disponibles())
        asyncio.run(ejecutor.procesar(self._orden("delete_file", {"path": str(pc["notas"] / "lista.txt")})))
        assert vistos and vistos[0] is not None


# --- La nube ----------------------------------------------------------------------------

class TestEnLaNube:
    def test_las_cinco_exigen_plan(self):
        from src.canal.herramientas import ArchivoDelEquipo, EscribirEnElEquipo, herramientas_del_equipo

        de_escritura = [h for h in herramientas_del_equipo()
                        if type(h) is EscribirEnElEquipo or (isinstance(h, ArchivoDelEquipo)
                                                              and h.name in DE_ESCRITURA)]
        assert {h.name for h in de_escritura} == set(DE_ESCRITURA)
        assert all(h.exige_plan for h in de_escritura)
        assert all(h.permission_level != "safe" for h in de_escritura)
        assert next(h for h in de_escritura if h.name == "delete_file").permission_level == "critical"

    def test_solo_existen_si_el_pc_las_anuncia(self, monkeypatch):
        from src.canal import herramientas
        from src.canal.herramientas import EscribirEnElEquipo

        class Conexion:
            capacidades = ["read_file", "create_file"]

        monkeypatch.setattr(herramientas.REGISTRO, "todas", lambda usuario: [Conexion()])
        assert EscribirEnElEquipo("create_file").disponible()
        assert not EscribirEnElEquipo("delete_file").disponible()

    def test_una_respuesta_perdida_se_dice(self, monkeypatch):
        from src.canal import herramientas
        from src.canal.despacho import AgenteSinRespuesta

        def sin_respuesta(*a, **k):
            raise AgenteSinRespuesta()

        monkeypatch.setattr(herramientas, "enviar", sin_respuesta)
        r = herramientas.EscribirEnElEquipo("delete_file").execute(path="C:\\x.txt")
        assert not r["success"] and "No se sabe si llegó a hacerse" in r["error"]

    def test_solo_manda_sus_argumentos(self, monkeypatch):
        from src.canal import herramientas

        mandado = {}

        def enviar(capacidad, argumentos, plazo=None, equipo=None):
            mandado.update(argumentos)
            return {"estado": "COMPLETED", "resultado": {"success": True, "data": {}}}

        monkeypatch.setattr(herramientas, "enviar", enviar)
        herramientas.EscribirEnElEquipo("create_file").execute(path="C:\\a.txt", content="x", overwrite=True)
        assert mandado == {"path": "C:\\a.txt", "content": "x"}

    def test_el_prompt_lo_explica_solo_si_se_puede(self):
        from src.agent.prompt import PUEDE_ESCRIBIR, _CITA, prompt_para

        con = prompt_para({"read_file", "create_plan", "edit_file"}, equipo_remoto=True)
        sin = prompt_para({"read_file", "create_plan"}, equipo_remoto=True)
        assert PUEDE_ESCRIBIR in con and "primero un plan" not in sin
        assert "ni escribir" in sin and "ni escribir" not in con
        assert _CITA.findall(PUEDE_ESCRIBIR) == []


# --- El fichero de la política ----------------------------------------------------------

class TestElFichero:
    def test_el_de_la_32_toma_la_confirmacion_nueva(self, tmp_path):
        """Los ficheros de la 3.2 guardaban `escribir: true` sin que nadie lo eligiera."""
        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        (almacen.carpeta() / "politica.json").write_text(json.dumps({
            "version": 2, "carpetas": [], "confirmar": {"escribir": True, "borrar": True, "ejecutar": True},
        }), encoding="utf-8")
        politica = Politica.cargar()
        assert politica.confirmar["escribir"] is False and politica.confirmar["borrar"] is True

    def test_lo_que_elige_la_persona_se_respeta(self, pc):
        politica = Politica.cargar()
        politica.confirmar["escribir"] = True
        politica.guardar()
        assert Politica.cargar().confirmar["escribir"] is True

    def test_la_escritura_se_guarda(self, pc):
        assert Politica.cargar().escritura == [str(pc["notas"].resolve())]

    def test_una_escritura_mal_formada_cierra_todo(self, pc):
        datos = json.loads((almacen.carpeta() / "politica.json").read_text(encoding="utf-8"))
        datos["escritura"] = "C:\\"
        (almacen.carpeta() / "politica.json").write_text(json.dumps(datos), encoding="utf-8")
        politica = Politica.cargar()
        assert politica.escritura == [] and politica.carpetas == []

    def test_ninguna_capacidad_escribe_la_politica(self):
        import inspect

        fuente = inspect.getsource(escritura)
        assert ".guardar()" not in fuente and "permitir_escritura" not in fuente

    def test_el_motor_exige_que_la_capacidad_sea_de_la_operacion(self, pc):
        ruta = str(pc["notas"] / "lista.txt")
        assert motor.evaluar("borrar", ruta, capacidad="create_file").motivo == "capacidad_desconocida"
        assert motor.evaluar("borrar", ruta).motivo == "capacidad_desconocida"
        assert motor.evaluar("escribir", ruta, capacidad="read_file").motivo == "capacidad_desconocida"
        assert motor.evaluar("ejecutar", ruta).motivo == "capacidad_desconocida"
        assert motor.evaluar("borrar", ruta, capacidad="delete_file")


def test_fuera_de_la_escritura_se_dice_como_arreglarlo(pc):
    """Que Morgan pueda decirle a la persona qué hacer, no un «fuera» de lectura."""
    r = escritura.create_file(str(pc["lectura"] / "x.txt"), "x")
    assert r["motivo"] == "fuera" and "escritura añadir" in r["error"]


class TestAnadirAlFinal:
    """4.1, decisión mía. Medido en la 4.1: con la última línea de un registro repetida,
    `edit_file` no podía (su fragmento tiene que estar una vez) y el modelo propuso borrar el
    registro y recrearlo."""

    def test_anade_en_su_propia_linea_aunque_se_repita_la_ultima(self, pc):
        registro = pc["notas"] / "registro.txt"
        registro.write_text("2026-09-27 copia hecha\n2026-09-27 copia hecha\n", encoding="utf-8")
        r = escritura.append_file(str(registro), "2026-09-28 copia hecha")
        assert r["success"], r
        assert registro.read_text(encoding="utf-8") == (
            "2026-09-27 copia hecha\n2026-09-27 copia hecha\n2026-09-28 copia hecha")
        assert r["data"]["sha256"] == escritura._huella(registro.read_bytes())

    def test_si_el_archivo_no_acaba_en_salto_lo_pone(self, pc):
        (pc["notas"] / "sin.txt").write_text("uno", encoding="utf-8")
        escritura.append_file(str(pc["notas"] / "sin.txt"), "dos")
        assert (pc["notas"] / "sin.txt").read_text(encoding="utf-8") == "uno\ndos"

    def test_respeta_los_saltos_de_windows_y_el_bom(self, pc):
        ruta = pc["notas"] / "win.txt"
        ruta.write_bytes(b"\xef\xbb\xbfuno\r\n")
        escritura.append_file(str(ruta), "dos\ntres")
        assert ruta.read_bytes() == b"\xef\xbb\xbfuno\r\ndos\r\ntres"

    def test_lo_de_antes_queda_respaldado(self, pc):
        r = escritura.append_file(str(pc["notas"] / "lista.txt"), "huevos")
        respaldo = almacen.carpeta() / "respaldos" / r["_auditoria"]["respaldo"]
        assert respaldo.read_text(encoding="utf-8") == "pan\nleche\n"

    def test_solo_a_uno_que_existe(self, pc):
        r = escritura.append_file(str(pc["notas"] / "no_existe.txt"), "x")
        assert not r["success"] and not (pc["notas"] / "no_existe.txt").exists()

    def test_nada_que_anadir_no_toca_nada(self, pc):
        antes = _foto(pc["notas"])
        assert not escritura.append_file(str(pc["notas"] / "lista.txt"), "")["success"]
        assert _foto(pc["notas"]) == antes

    def test_fuera_de_las_carpetas_de_escritura_no(self, pc):
        r = escritura.append_file(str(pc["lectura"] / "solo_leer.txt"), "x")
        assert not r["success"]
        assert (pc["lectura"] / "solo_leer.txt").read_text(encoding="utf-8") == "no me toques"

    def test_ni_a_un_ejecutable_ni_a_un_binario(self, pc):
        (pc["notas"] / "a.bat").write_text("echo", encoding="utf-8")
        (pc["notas"] / "b.bin").write_bytes(b"\x00\x01")
        assert escritura.append_file(str(pc["notas"] / "a.bat"), "x")["motivo"] == "ejecutable"
        assert escritura.append_file(str(pc["notas"] / "b.bin"), "x")["motivo"] == "binario"

    def test_nace_apagada(self):
        from src.agente.politica import ENCENDIDA_POR_DEFECTO
        assert ENCENDIDA_POR_DEFECTO["append_file"] is False

    def test_apagada_no_aunque_editar_este_encendida(self, pc):
        politica = Politica.cargar()
        politica.capacidades["append_file"] = False
        politica.guardar()
        antes = _foto(pc["notas"])
        assert not escritura.append_file(str(pc["notas"] / "lista.txt"), "x")["success"]
        assert _foto(pc["notas"]) == antes

    def test_con_lo_anadido_no_puede_pasar_del_tope(self, pc, monkeypatch):
        monkeypatch.setattr(escritura, "MAX_EDITABLE", 12)
        r = escritura.append_file(str(pc["notas"] / "lista.txt"), "mucho más texto")
        assert not r["success"] and r["motivo"] == "demasiado_grande"
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8") == "pan\nleche\n"


class TestAnadirDosVecesLoMismo:
    """4.1, medido: dos «anota la copia de hoy» en dos planes aprobados añadieron una sola
    línea, y la segunda se contó como hecha. El paso del plan (`origen`) las distingue."""

    def test_el_mismo_paso_repetido_no_se_anade_dos_veces(self, pc):
        ruta = str(pc["notas"] / "lista.txt")
        escritura.append_file(ruta, "huevos", origen="plan-a#1")
        r = escritura.append_file(ruta, "huevos", origen="plan-a#1")
        assert r["data"].get("ya_hecho") is True
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8").count("huevos") == 1

    def test_otro_plan_que_pide_lo_mismo_si(self, pc):
        ruta = str(pc["notas"] / "lista.txt")
        escritura.append_file(ruta, "huevos", origen="plan-a#1")
        r = escritura.append_file(ruta, "huevos", origen="plan-b#1")
        assert r["success"] and not r["data"].get("ya_hecho")
        assert (pc["notas"] / "lista.txt").read_text(encoding="utf-8").count("huevos") == 2
