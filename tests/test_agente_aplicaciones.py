"""
Abrir y cerrar aplicaciones (4.8): `open_app` (🟢, sin plan) y `close_app` (🔴, plan y
«Permitir»). Las dos nacen apagadas.

Lo que haría daño si fallase: que se abriera una consola o un intérprete (con otro nombre:
medido en mi PC, Git CMD, MSYS2, Python, IDLE o Node.js), una herramienta de
administración, un script o un programa suelto; un archivo o una carpeta fuera de lo
permitido; una dirección que no es una página; o que cerrar se llevara un proceso del
sistema, de Morgan o de otra persona.
"""

import struct
import subprocess
import sys
import time

import pytest

from src.agente import aplicaciones, aviso
from src.agente.politica import DE_APLICACIONES, Politica
from tests.test_agente_escritura import papelera, pc, preguntas, sistema_de_mentira  # noqa: F401


def lnk(destino: str, unicode: bool = True) -> bytes:
    """Un acceso directo mínimo (MS-SHLLINK) con LinkInfo y la ruta del destino."""
    cabecera = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46",
                           0x02) + b"\x00" * (0x4C - 24)
    ansi = destino.encode("latin-1", errors="replace") + b"\x00"
    uni = destino.encode("utf-16-le") + b"\x00\x00"
    tam_cab = 0x24 if unicode else 0x1C
    desp_ansi = tam_cab
    desp_uni = desp_ansi + len(ansi)
    info = struct.pack("<IIIIIII", 0, tam_cab, 1, 0, desp_ansi, 0, 0)
    if unicode:
        info += struct.pack("<II", 0, desp_uni)
    cuerpo = info + ansi + (uni if unicode else b"")
    cuerpo = struct.pack("<I", len(cuerpo)) + cuerpo[4:]
    return cabecera + cuerpo


@pytest.fixture
def menu(tmp_path, monkeypatch, pc):
    """Un menú Inicio de mentira, las dos capacidades encendidas y `_abrir` apuntado."""
    base = tmp_path / "Menu"
    accesos = {
        "Visual Studio Code/Visual Studio Code.lnk": r"C:\Users\ana\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        "Word.lnk": r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        "Mi editor.lnk": r"C:\Windows\System32\cmd.exe",
        "Windows PowerShell/Windows PowerShell.lnk": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "Windows PowerShell/Windows PowerShell ISE.lnk": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell_ise.exe",
        "Git/Git CMD.lnk": r"C:\Program Files\Git\git-cmd.exe",
        "Herramientas raras/Consola.lnk": r"C:\msys64\ucrt64.exe",
        "Administrative Tools/Task Scheduler.lnk": r"C:\Windows\System32\taskschd.msc",
        "Startup/Morgan agente local.lnk": r"C:\Users\ana\morgan.cmd",
        "Utilidades/Script.lnk": r"C:\Users\ana\limpiar.ps1",
        "Excel.lnk": r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
        "Excel Viewer.lnk": r"C:\Program Files\Viewer\xlview.exe",
    }
    # De la carpeta de herramientas, con un destino que por sí solo no dice nada.
    accesos["Administrative Tools/Resource Monitor.lnk"] = r"C:\Windows\System32\perfmon.exe"
    for relativa, destino in accesos.items():
        ruta = base / relativa
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(lnk(destino))
    # Y uno cuyo destino no se puede leer (en mi PC, varios): decide el nombre.
    (base / "Command Prompt.lnk").write_bytes(b"ilegible")
    monkeypatch.setattr(aplicaciones, "carpetas_del_menu", lambda: [base])
    abiertas = []
    monkeypatch.setattr(aplicaciones, "_abrir", lambda que, argumentos="": abiertas.append((str(que), argumentos)))
    politica = Politica.cargar()
    for c in DE_APLICACIONES:
        politica.capacidades[c] = True
    politica.guardar()
    return {"abiertas": abiertas, "base": base, **pc}


class TestNacen:
    def test_apagadas(self):
        politica = Politica()
        assert not politica.capacidades["open_app"] and not politica.capacidades["close_app"]

    def test_apagada_no_abre(self, pc):
        r = aplicaciones.open_app("url", url="https://example.com")
        assert not r["success"] and r["motivo"] == "capacidad_apagada"


class TestElAcceso:
    @pytest.mark.parametrize("unicode", [True, False])
    def test_lee_el_destino(self, tmp_path, unicode):
        ruta = tmp_path / "a.lnk"
        ruta.write_bytes(lnk(r"C:\Program Files\App\app.exe", unicode))
        assert aplicaciones.destino_del_acceso(ruta) == r"C:\Program Files\App\app.exe"

    def test_uno_roto_no_revienta(self, tmp_path):
        ruta = tmp_path / "roto.lnk"
        ruta.write_bytes(b"no es un acceso")
        assert aplicaciones.destino_del_acceso(ruta) is None


class TestAbrirUnaAplicacion:
    def test_listar_sin_las_prohibidas(self, menu):
        lista = aplicaciones.open_app("listar")["data"]["aplicaciones"]
        assert sorted(lista) == ["Excel", "Excel Viewer", "Visual Studio Code", "Word"]

    def test_abre_una_instalada(self, menu):
        r = aplicaciones.open_app("aplicacion", nombre="visual studio code")
        assert r["success"], r
        assert menu["abiertas"][0][0].endswith("Visual Studio Code.lnk")

    def test_la_exacta_gana_a_las_parecidas(self, menu):
        assert aplicaciones.open_app("aplicacion", nombre="Excel")["success"]
        assert menu["abiertas"][0][0].endswith("Excel.lnk")

    def test_si_hay_varias_pregunta(self, menu):
        r = aplicaciones.open_app("aplicacion", nombre="exc")
        assert not r["success"] and r["motivo"] == "ambigua" and not menu["abiertas"]

    def test_una_que_no_esta(self, menu):
        assert aplicaciones.open_app("aplicacion", nombre="Photoshop")["motivo"] == "no_instalada"

    @pytest.mark.parametrize("nombre", ["Windows PowerShell", "Mi editor", "Git CMD", "Consola", "Script"])
    def test_nunca_una_consola_ni_un_script(self, menu, nombre):
        r = aplicaciones.open_app("aplicacion", nombre=nombre)
        assert not r["success"] and r["motivo"] == "prohibida", r
        assert not menu["abiertas"]

    @pytest.mark.parametrize("nombre", ["Task Scheduler", "Morgan agente local", "Resource Monitor",
                                        "Command Prompt"])
    def test_ni_herramientas_del_sistema_ni_morgan(self, menu, nombre):
        assert not aplicaciones.open_app("aplicacion", nombre=nombre)["success"]
        assert not menu["abiertas"]

    def test_con_una_carpeta_permitida(self, menu):
        r = aplicaciones.open_app("aplicacion", nombre="Visual Studio Code", path=str(menu["notas"]))
        assert r["success"], r
        assert menu["abiertas"][0][1] == f'"{menu["notas"]}"'

    def test_con_una_ruta_de_fuera_no(self, menu):
        r = aplicaciones.open_app("aplicacion", nombre="Visual Studio Code", path=str(menu["fuera"]))
        assert not r["success"] and not menu["abiertas"]

    def test_con_un_programa_no(self, menu):
        (menu["notas"] / "x.bat").write_text("echo", encoding="utf-8")
        r = aplicaciones.open_app("aplicacion", nombre="Word", path=str(menu["notas"] / "x.bat"))
        assert not r["success"] and not menu["abiertas"]


class TestAbrirLoDemas:
    def test_un_archivo_con_su_programa(self, menu):
        (menu["notas"] / "factura.pdf").write_bytes(b"%PDF")
        assert aplicaciones.open_app("archivo", path=str(menu["notas"] / "factura.pdf"))["success"]
        assert menu["abiertas"][0][0].endswith("factura.pdf")

    @pytest.mark.parametrize("nombre", ["virus.exe", "a.bat", "a.ps1", "atajo.lnk", "a.vbs", "a.hta", "a.url"])
    def test_nunca_uno_que_se_ejecuta(self, menu, nombre):
        (menu["notas"] / nombre).write_bytes(b"x")
        r = aplicaciones.open_app("archivo", path=str(menu["notas"] / nombre))
        assert not r["success"] and not menu["abiertas"]

    def test_ni_de_fuera_ni_credenciales(self, menu):
        (menu["notas"] / ".env").write_text("K=1", encoding="utf-8")
        assert not aplicaciones.open_app("archivo", path=str(menu["fuera"] / "ajeno.txt"))["success"]
        assert not aplicaciones.open_app("archivo", path=str(menu["notas"] / ".env"))["success"]
        assert not aplicaciones.open_app("archivo", path="")["success"]
        assert not menu["abiertas"]

    def test_una_carpeta(self, menu):
        assert aplicaciones.open_app("carpeta", path=str(menu["notas"]))["success"]
        assert not aplicaciones.open_app("carpeta", path=str(menu["fuera"]))["success"]
        assert len(menu["abiertas"]) == 1

    @pytest.mark.parametrize("url", ["https://morgan.example/a?b=1", "http://example.com"])
    def test_una_pagina(self, menu, url):
        assert aplicaciones.open_app("url", url=url)["success"]

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/Windows/win.ini", "ms-settings:",
                                     "https://ana:clave@example.com", "http://", "https://a.com/\"x"])
    def test_lo_que_no_es_una_pagina(self, menu, url):
        assert not aplicaciones.open_app("url", url=url)["success"]
        assert not menu["abiertas"]

    def test_con_confirmar_abrir_se_pregunta(self, menu):
        politica = Politica.cargar()
        politica.confirmar["abrir"] = True
        politica.guardar()
        menu["preguntas"]["respuesta"]["valor"] = "rechazada"
        r = aplicaciones.open_app("url", url="https://example.com")
        assert not r["success"] and r["motivo"] == "no_confirmada"
        assert len(menu["preguntas"]["hechas"]) == 1 and not menu["abiertas"]


@pytest.fixture
def durmiente():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.5)
    yield p
    p.kill()


class TestCerrar:
    def _encender(self):
        politica = Politica.cargar()
        politica.capacidades["close_app"] = True
        politica.guardar()

    def test_pid_y_nombre_tienen_que_coincidir(self, pc, durmiente):
        self._encender()
        r = aplicaciones.close_app(pid=durmiente.pid, name="notepad.exe")
        assert not r["success"] and r["motivo"] == "no_coincide" and durmiente.poll() is None

    def test_sin_ventana_no_se_cierra_sin_forzar(self, pc, durmiente, monkeypatch):
        self._encender()
        monkeypatch.setattr(aplicaciones, "_ventanas_de", lambda pid: [])
        import psutil

        nombre = psutil.Process(durmiente.pid).name()
        r = aplicaciones.close_app(pid=durmiente.pid, name=nombre)
        assert not r["success"] and r["motivo"] == "sigue_abierto" and durmiente.poll() is None
        assert pc["preguntas"]["hechas"], "cerrar se confirma en el PC"

    def test_forzando_si(self, pc, durmiente, monkeypatch):
        self._encender()
        monkeypatch.setattr(aplicaciones, "_ventanas_de", lambda pid: [])
        import psutil

        nombre = psutil.Process(durmiente.pid).name()
        r = aplicaciones.close_app(pid=durmiente.pid, name=nombre, forzar=True)
        assert r["success"] and r["data"]["forzado"]
        durmiente.wait(5)

    def test_con_un_no_no_se_toca(self, pc, durmiente):
        self._encender()
        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        import psutil

        r = aplicaciones.close_app(pid=durmiente.pid, name=psutil.Process(durmiente.pid).name(), forzar=True)
        assert not r["success"] and durmiente.poll() is None

    def test_ni_el_sistema(self, pc):
        self._encender()
        r = aplicaciones.close_app(pid=4, name="System")
        assert not r["success"]


class TestEnLaNube:
    def test_abrir_verde_y_cerrar_rojo(self):
        from src.canal.herramientas import AplicacionDelEquipo, herramientas_del_equipo

        h = {x.name: x for x in herramientas_del_equipo() if isinstance(x, AplicacionDelEquipo)}
        assert h["open_app"].permission_level == "safe" and not h["open_app"].exige_plan
        assert h["close_app"].permission_level == "critical" and h["close_app"].exige_plan

    def test_una_pagina_tras_leer_el_pc_pasa_por_la_fuga(self):
        from src.agent import fuga

        assert fuga.SALEN_A_INTERNET["open_app"] == "url"
        assert "open_app" in fuga.DEL_PC


class TestLoQueMidioElModelo:
    def test_powershell_con_variantes_no_pregunta_cual(self, menu):
        """Medido con el modelo real (4.8): «abre PowerShell» encontraba varias, contestaba
        «di cuál» y el modelo se lo preguntaba a la persona, como si alguna se pudiera abrir."""
        r = aplicaciones.open_app("aplicacion", nombre="powershell")
        assert not r["success"] and r["motivo"] == "prohibida" and not menu["abiertas"]
