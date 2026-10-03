"""
Ventanas y capturas (4.11). Las dos capacidades nacen apagadas.

Lo que haría daño si fallase: capturar la pantalla sin «Permitir» (ve todo lo que hay a la
vista), mandar algo que no es una imagen, o tocar una ventana que no es la pedida. Las
ventanas de verdad de quien pasa las pruebas no se tocan: se simulan.
"""

import struct
import sys
import zlib

import pytest

from src.agente import aviso, pantalla
from src.agente.politica import DE_PANTALLA, Politica


def _leer_png(datos: bytes):
    """Descomprime un PNG RGB de 8 bits: (ancho, alto, filas de bytes RGB)."""
    assert datos[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ancho, alto = 8, b"", 0, 0
    while pos < len(datos):
        largo, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + largo]
        assert struct.unpack(">I", datos[pos + 8 + largo:pos + 12 + largo])[0] == zlib.crc32(tipo + cuerpo) & 0xFFFFFFFF
        if tipo == b"IHDR":
            ancho, alto, bits, color = struct.unpack(">IIBB", cuerpo[:10])
            assert (bits, color) == (8, 2)
        elif tipo == b"IDAT":
            idat += cuerpo
        pos += 12 + largo
    crudo = zlib.decompress(idat)
    fila = 1 + ancho * 3
    assert len(crudo) == alto * fila
    return ancho, alto, [crudo[i * fila + 1:(i + 1) * fila] for i in range(alto)]


class TestElPng:
    def test_colores_y_tamano(self):
        # 2×1: un píxel rojo y uno azul, en BGRA.
        ancho, alto, filas = _leer_png(pantalla._png(2, 1, bytes([0, 0, 255, 255, 255, 0, 0, 255])))
        assert (ancho, alto) == (2, 1) and filas[0] == bytes([255, 0, 0, 0, 0, 255])


@pytest.fixture
def pc(monkeypatch):
    politica = Politica()
    for c in DE_PANTALLA:
        politica.capacidades[c] = True
    politica.guardar()
    monkeypatch.setattr(pantalla.sys, "platform", "win32")
    falsas = [
        {"id": 101, "titulo": "Informe.docx - Word", "programa": "WINWORD.EXE", "estado": "normal",
         "x": 10, "y": 10, "ancho": 800, "alto": 600},
        {"id": 102, "titulo": "Morgan - Firefox", "programa": "firefox.exe", "estado": "minimizada",
         "x": 0, "y": 0, "ancho": 1200, "alto": 800},
        {"id": 103, "titulo": "Informe viejo.docx - Word", "programa": "WINWORD.EXE", "estado": "normal",
         "x": 50, "y": 50, "ancho": 800, "alto": 600},
    ]
    monkeypatch.setattr(pantalla, "ventanas", lambda: [dict(v) for v in falsas])
    llamadas = []

    class User32:
        def __getattr__(self, nombre):
            return lambda *a: llamadas.append((nombre, *a)) or 0

        def IsIconic(self, hwnd):
            return 0

    monkeypatch.setattr(pantalla, "_win32", lambda: (None, None, User32(), None))
    monkeypatch.setattr(pantalla, "_area_de_trabajo", lambda: (0, 0, 1920, 1040))
    monkeypatch.setattr(pantalla, "_escritorio", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(pantalla.time, "sleep", lambda s: None)
    capturas = []
    monkeypatch.setattr(pantalla, "capturar",
                        lambda x, y, a, l: capturas.append((x, y, a, l)) or (pantalla._png(2, 2, bytes(16)), 2, 2))
    preguntas, respuesta = [], {"valor": aviso.PERMITIDA}
    monkeypatch.setattr(aviso, "preguntar",
                        lambda t, d, espera=aviso.ESPERA, **k: preguntas.append((t, d)) or respuesta["valor"])
    return {"llamadas": llamadas, "capturas": capturas, "preguntas": preguntas, "respuesta": respuesta}


class TestNacen:
    def test_apagadas(self):
        politica = Politica()
        assert not politica.capacidades["windows"] and not politica.capacidades["screenshot"]
        politica.guardar()
        assert pantalla.windows("listar")["motivo"] == "capacidad_apagada"
        assert pantalla.screenshot()["motivo"] == "capacidad_apagada"


class TestVentanas:
    def test_listar(self, pc):
        d = pantalla.windows("listar")["data"]
        assert d["total"] == 3 and d["ventanas"][0]["programa"] == "WINWORD.EXE"

    @pytest.mark.parametrize("accion, codigo", [("minimizar", 6), ("maximizar", 3), ("restaurar", 9)])
    def test_por_id(self, pc, accion, codigo):
        assert pantalla.windows(accion, id=101)["success"]
        assert ("ShowWindow", 101, codigo) in pc["llamadas"]

    def test_por_titulo_si_es_una_sola(self, pc):
        assert pantalla.windows("enfocar", titulo="firefox")["success"]
        assert ("SetForegroundWindow", 102) in pc["llamadas"]

    def test_si_hay_varias_pregunta_cual(self, pc):
        r = pantalla.windows("minimizar", titulo="informe")
        assert not r["success"] and r["motivo"] == "ambigua" and not pc["llamadas"]

    def test_una_que_no_existe(self, pc):
        assert pantalla.windows("minimizar", id=999)["motivo"] == "no_existe"
        assert pantalla.windows("minimizar", titulo="Photoshop")["motivo"] == "no_existe"
        assert not pc["llamadas"]

    def test_mover(self, pc):
        assert pantalla.windows("mover", id=101, x=0, y=0, ancho=960, alto=1040)["success"]
        assert ("MoveWindow", 101, 0, 0, 960, 1040, True) in pc["llamadas"]

    def test_tan_pequena_no(self, pc):
        assert not pantalla.windows("mover", id=101, ancho=10, alto=10)["success"]
        assert not pc["llamadas"]

    def test_lado_a_lado(self, pc):
        assert pantalla.windows("lado_a_lado", id=101, otra_id=102)["success"]
        assert ("MoveWindow", 101, 0, 0, 960, 1040, True) in pc["llamadas"]
        assert ("MoveWindow", 102, 960, 0, 960, 1040, True) in pc["llamadas"]

    def test_lado_a_lado_sin_la_otra(self, pc):
        assert not pantalla.windows("lado_a_lado", id=101)["success"] and not pc["llamadas"]


class TestCapturas:
    def test_pide_permitir_siempre(self, pc):
        r = pantalla.screenshot("pantalla")
        assert r["success"], r
        assert len(pc["preguntas"]) == 1 and "CAPTURA" in pc["preguntas"][0][0]
        assert r["_archivo"].endswith(".png") and r["data"]["tipo"] == "image/png"
        assert pc["capturas"] == [(0, 0, 1920, 1080)]

    def test_la_politica_no_quita_la_pregunta(self, pc):
        politica = Politica.cargar()
        politica.confirmar["captura"] = False
        politica.guardar()
        pantalla.screenshot("pantalla")
        assert pc["preguntas"]

    def test_con_un_no_no_se_captura(self, pc):
        pc["respuesta"]["valor"] = "rechazada"
        r = pantalla.screenshot("pantalla")
        assert not r["success"] and r["motivo"] == "no_confirmada" and not pc["capturas"]

    def test_una_ventana(self, pc):
        assert pantalla.screenshot("ventana", id=101)["success"]
        assert pc["capturas"] == [(10, 10, 800, 600)]
        assert "Informe.docx" in pc["preguntas"][0][1]

    def test_una_minimizada_no_se_pregunta(self, pc):
        r = pantalla.screenshot("ventana", titulo="firefox")
        assert not r["success"] and r["motivo"] == "minimizada" and not pc["preguntas"]

    def test_una_region_se_recorta_a_la_pantalla(self, pc):
        assert pantalla.screenshot("region", x=1800, y=1000, ancho=500, alto=500)["success"]
        assert pc["capturas"] == [(1800, 1000, 120, 80)]

    def test_una_region_fuera_no(self, pc):
        assert not pantalla.screenshot("region", x=5000, y=5000, ancho=100, alto=100)["success"]
        assert not pc["preguntas"]


@pytest.mark.skipif(sys.platform != "win32", reason="la pantalla de Windows")
@pytest.mark.escritorio
def test_la_captura_de_verdad_es_un_png_entero():
    x, y, a, l = pantalla._escritorio()
    datos, ancho, alto = pantalla.capturar(x, y, min(a, 400), min(l, 300))
    assert _leer_png(datos)[:2] == (ancho, alto)


class TestEnLaNube:
    def _captura(self, monkeypatch, contenido):
        from src.canal import herramientas

        monkeypatch.setattr(herramientas, "enviar", lambda *a, **k: {
            "estado": "COMPLETED", "_contenido": contenido,
            "resultado": {"success": True, "data": {"name": "captura.png", "de": "la pantalla entera"}}})
        eventos = []
        monkeypatch.setattr(herramientas, "emitir", lambda *a, **k: eventos.append((a, k)))

        class Almacen:
            guardadas = []

            def guardar_copia_del_equipo(self, nombre, datos, mime):
                self.guardadas.append((nombre, mime))

                class Archivo:
                    id, nombre_original, tamano = "up-1", nombre, len(datos)
                return Archivo()

        almacen = Almacen()
        (h,) = [x for x in herramientas.herramientas_del_equipo(store=almacen) if x.name == "screenshot"]
        return h.execute(alcance="pantalla"), almacen, eventos

    def test_se_guarda_como_imagen_y_se_dice_como_verla(self, monkeypatch):
        r, almacen, eventos = self._captura(monkeypatch, pantalla._png(2, 2, bytes(16)))
        assert r["success"] and r["data"]["upload_id"] == "up-1" and "analyze_image" in r["data"]["aviso"]
        assert almacen.guardadas == [("captura.png", "image/png")] and eventos

    def test_lo_que_no_es_una_imagen_no_se_guarda(self, monkeypatch):
        r, almacen, _ = self._captura(monkeypatch, b"MZ ejecutable")
        assert not r["success"] and not almacen.guardadas

    def test_cuentan_como_leido_del_pc(self):
        from src.agent import fuga

        assert {"windows", "screenshot"} <= fuga.DEL_PC


class TestLoQueMidioElModelo411:
    def test_lado_a_lado_por_titulos(self, pc):
        assert pantalla.windows("lado_a_lado", titulo="Informe.docx", otro_titulo="firefox")["success"]
        assert ("MoveWindow", 102, 960, 0, 960, 1040, True) in pc["llamadas"]

    def test_un_plan_que_lista_y_luego_actua_se_rechaza(self):
        """Medido (4.11): planeó listar las ventanas y moverlas en el mismo plan, sin sus ids."""
        from src.tools.planificacion import CreatePlanTool, es_consulta

        assert es_consulta("windows", {"accion": "listar"}) and not es_consulta("windows", {"accion": "mover"})

        class Registro:
            def get(self, nombre):
                return object()

        class Planificador:
            tool_registry = Registro()

        herramienta = CreatePlanTool.__new__(CreatePlanTool)
        herramienta.planificador = Planificador()
        pasos = [{"herramienta": "windows", "argumentos": {"accion": "listar"}},
                 {"herramienta": "windows", "argumentos": {"accion": "lado_a_lado"}}]
        assert herramienta._consultas_antes_de_un_cambio(pasos) == ["windows"]
        assert herramienta._consultas_antes_de_un_cambio(pasos[1:]) == []
