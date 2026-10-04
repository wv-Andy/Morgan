"""
Los avisos de la bandeja, también en el PC (5.2, `src/canal/avisos_al_pc.py`): de punta a punta,
con el canal de verdad y agentes de prueba conectados por websocket.

Lo que decidí: notificación de Windows **solo en los PC que tengan encendido «Mandarte
avisos a este PC»** (`notify`). Un PC sin ella no recibe nada; la bandeja, igual.
"""

import time

from src.automatizacion import ejecutor as ejecutor_de_automatizaciones
from src.canal import avisos_al_pc
from src.canal.registro import REGISTRO
from tests.test_canal_agente import SALUDO, _cuenta, _emparejar, _socket, nube  # noqa: F401

CON_AVISOS = {**SALUDO, "capacidades": ["estado", "notify"]}


def _esperar_conectados(user_id: str, cuantos: int) -> None:
    limite = time.monotonic() + 5
    while len(REGISTRO.todas(user_id)) < cuantos and time.monotonic() < limite:
        time.sleep(0.05)
    assert len(REGISTRO.todas(user_id)) == cuantos


class TestSoloAlPcQueLoTieneEncendido:
    def test_el_aviso_llega_como_orden_notify(self, nube):  # noqa: F811
        _, _, user_id, _, credencial = _cuenta(nube)
        with _socket(nube, credencial) as ws:
            ws.send_json(CON_AVISOS)
            ws.receive_json()
            _esperar_conectados(user_id, 1)
            assert avisos_al_pc.avisar(user_id, "Resumen del día", "Tienes 3 correos") == 1
            orden = ws.receive_json()
            ws.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                          "resultado": {"success": True}, "tiempos": {}})
        assert orden["tipo"] == "orden" and orden["capability"] == "notify"
        assert orden["arguments"] == {"titulo": "Morgan · Resumen del día", "mensaje": "Tienes 3 correos"}

    def test_a_un_pc_sin_notify_no_le_llega_nada(self, nube):  # noqa: F811
        web, csrf, user_id, _, con = _cuenta(nube)
        _, sin = _emparejar(nube, web, csrf, nombre="Sobremesa")
        with _socket(nube, con) as ws_con, _socket(nube, sin) as ws_sin:
            ws_con.send_json(CON_AVISOS)
            ws_con.receive_json()
            ws_sin.send_json(SALUDO)                       # solo «estado»
            ws_sin.receive_json()
            _esperar_conectados(user_id, 2)
            assert avisos_al_pc.avisar(user_id, "t", "x") == 1
            orden = ws_con.receive_json()
            ws_con.send_json({"tipo": "resultado", "command_id": orden["command_id"], "estado": "COMPLETED",
                              "resultado": {"success": True}, "tiempos": {}})
        assert orden["capability"] == "notify"

    def test_sin_pc_conectado_no_hace_nada(self, nube):  # noqa: F811
        _, _, user_id, _, _ = _cuenta(nube)
        assert avisos_al_pc.avisar(user_id, "t", "x") == 0

    def test_solo_a_los_pc_de_esa_persona(self, nube):  # noqa: F811
        _, _, ana, _, credencial = _cuenta(nube, "ana")
        _, _, luis, _, _ = _cuenta(nube, "luis")
        with _socket(nube, credencial) as ws:
            ws.send_json(CON_AVISOS)
            ws.receive_json()
            _esperar_conectados(ana, 1)
            assert avisos_al_pc.avisar(luis, "t", "x") == 0


class TestElTexto:
    def test_uno_largo_se_corta_y_dice_donde_esta_entero(self):
        texto = avisos_al_pc._texto("palabra " * 100)
        assert len(texto) < 260 and texto.endswith("(entero, en la bandeja de Morgan)")

    def test_uno_corto_queda_igual_sin_saltos(self):
        assert avisos_al_pc._texto("Hecho.\n\nSin novedades.") == "Hecho. Sin novedades."


class TestQuienLoManda:
    def test_cada_aviso_de_una_automatizacion(self, monkeypatch):
        mandados = []
        monkeypatch.setattr(avisos_al_pc, "avisar", lambda *a: mandados.append(a) or 1)

        class Repo:
            def anotar(self, *a):
                pass

            def crear_aviso(self, datos):
                pass

        ejecutor = ejecutor_de_automatizaciones.Reloj.__new__(ejecutor_de_automatizaciones.Reloj)
        ejecutor.repo = Repo()
        ejecutor.container = None
        ejecutor._cerrar({"id": "au1", "user_id": "ana", "nombre": "Resumen"}, time.time(), "hecha", "Todo bien")
        assert mandados == [("ana", "Resumen", "Todo bien")]

    def test_un_fallo_al_avisar_no_rompe_la_automatizacion(self, monkeypatch):
        def revienta(*a):
            raise RuntimeError("sin canal")

        monkeypatch.setattr(avisos_al_pc, "avisar", revienta)
        avisos = []

        class Repo:
            def anotar(self, *a):
                pass

            def crear_aviso(self, datos):
                avisos.append(datos)

        ejecutor = ejecutor_de_automatizaciones.Reloj.__new__(ejecutor_de_automatizaciones.Reloj)
        ejecutor.repo = Repo()
        ejecutor.container = None
        ejecutor._cerrar({"id": "au1", "user_id": "ana", "nombre": "Resumen"}, time.time(), "hecha", "Todo bien")
        assert len(avisos) == 1

    def test_las_alertas_al_propietario_tambien(self, monkeypatch):
        from src import alertas

        mandados = []
        monkeypatch.setattr(avisos_al_pc, "avisar", lambda *a: mandados.append(a) or 1)

        class Cuentas:
            def propietario(self):
                return {"id": "andy"}

        class Avisos:
            def crear_aviso(self, datos):
                pass

        monkeypatch.setattr("src.identidad.repositorio.repositorio_de_cuentas", lambda repos: Cuentas())
        monkeypatch.setattr("src.automatizacion.repositorio.repositorio_de_automatizaciones", lambda repos: Avisos())
        alertas._avisar_al_propietario("Error en el servidor", "algo se rompió")
        assert mandados == [("andy", "Error en el servidor", "algo se rompió")]

