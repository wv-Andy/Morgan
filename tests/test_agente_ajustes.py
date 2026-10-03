"""
«Morgan en tu PC» (4.17): la ventana de ajustes del agente, para quien no usa la consola, y
el botón de la web que la abre en el PC. Decisión mía: «las dos».

Lo que haría daño si fallase: que la web pudiera **cambiar** la política (solo puede pedir
que se abra la ventana), que una capacidad nueva no salga en la ventana (y nadie pudiera
encenderla sin consola), que la ventana se salte las reglas de las carpetas de la consola,
o que las pruebas abran ventanas o toquen el menú Inicio de quien las pasa.
"""

import os
import sys

import pytest

from src.agente import ajustes, arranque
from src.agente.ejecutor import Ejecutor
from src.agente.politica import CAPACIDADES, Politica


class TestLaVentanaSabeDeTodo:
    def test_cada_capacidad_sale_una_vez_con_su_color(self):
        nombres = [c for _, grupo in ajustes.GRUPOS for c, _, _ in grupo]
        assert sorted(nombres) == sorted(CAPACIDADES), "una capacidad nueva tiene que salir en la ventana"
        assert all(color in ajustes.COLORES for _, grupo in ajustes.GRUPOS for _, _, color in grupo)

    def test_los_atajos(self):
        politica = Politica()
        ajustes.aplicar_atajo(politica, "Solo mirar")
        encendidas = {c for c, v in politica.capacidades.items() if v}
        assert "read_file" in encendidas and not encendidas & {"create_file", "delete_file", "open_app"}
        ajustes.aplicar_atajo(politica, "Trabajar con archivos")
        encendidas = {c for c, v in politica.capacidades.items() if v}
        assert {"create_file", "compress", "open_app"} <= encendidas
        assert not encendidas & {"delete_file", "kill_process", "ui_control"}, "lo rojo, no"
        ajustes.aplicar_atajo(politica, "Todo")
        assert all(politica.capacidades[c] for c in CAPACIDADES)


class TestCarpetas:
    @pytest.fixture
    def carpeta(self, monkeypatch):
        """Una carpeta donde se puede escribir: bajo la casa (AppData está prohibida)."""
        import shutil
        from pathlib import Path

        ruta = Path(os.environ.get("USERPROFILE") or Path.home()) / "Morgan-prueba-ajustes"
        ruta.mkdir(exist_ok=True)
        yield ruta
        shutil.rmtree(ruta, ignore_errors=True)

    def test_una_de_escritura_tambien_se_lee(self, carpeta):
        politica = Politica()
        assert ajustes.anadir_carpeta(politica, str(carpeta), escribir=True) is None
        assert str(carpeta.resolve()) in politica.carpetas and str(carpeta.resolve()) in politica.escritura

    def test_quitarla_la_quita_de_las_dos(self, carpeta):
        politica = Politica()
        ajustes.anadir_carpeta(politica, str(carpeta), escribir=True)
        ajustes.quitar_carpeta(politica, str(carpeta))
        assert politica.carpetas == [] and politica.escritura == []

    def test_salen_tambien_las_que_solo_son_de_escritura(self, carpeta, tmp_path):
        """Medido al ver la ventana: mi carpeta de escritura, que no era de leer, no salía."""
        politica = Politica()
        politica.anadir(str(tmp_path))
        politica.escritura.append(str(carpeta))
        assert ajustes.filas_de_carpetas(politica) == [(str(tmp_path.resolve()), "leer"), (str(carpeta), "escribir")]

    def test_si_no_vale_para_escribir_no_queda_ni_para_leer(self, tmp_path):
        """Lo cazó esta prueba: una de AppData (zona prohibida) quedaba añadida para leer."""
        politica = Politica()
        assert ajustes.anadir_carpeta(politica, str(tmp_path), escribir=True)
        assert politica.carpetas == [] and politica.escritura == []

    def test_las_mismas_reglas_que_la_consola(self, tmp_path):
        politica = Politica()
        motivo = ajustes.anadir_carpeta(politica, str(tmp_path / "no-existe"))
        assert motivo and politica.carpetas == []

    @pytest.mark.skipif(sys.platform != "win32", reason="las carpetas de Windows")
    def test_donde_no_se_escribe_nunca_tampoco(self):
        politica = Politica()
        motivo = ajustes.anadir_carpeta(politica, os.environ["SystemRoot"], escribir=True)
        assert motivo and politica.escritura == []


class TestLaWebSoloLaAbre:
    def test_abrir_ajustes_es_fija_y_sobrevive_a_un_cambio_de_politica(self):
        ejecutor = Ejecutor("agt-1", {"read_file": lambda **k: {}})
        assert "abrir_ajustes" in ejecutor.nombres() and "estado" in ejecutor.nombres()
        ejecutor.reemplazar({})
        assert "abrir_ajustes" in ejecutor.nombres()

    def test_abrirla_no_cambia_la_politica(self, monkeypatch):
        politica = Politica()
        politica.guardar()
        antes = Politica.firma()
        monkeypatch.setattr(ajustes, "abrir_en_segundo_plano", lambda: True)
        r = Ejecutor._abrir_ajustes()
        assert r["success"] and Politica.firma() == antes

    def test_en_las_pruebas_no_se_abre_ninguna_ventana(self):
        assert os.environ.get("MORGAN_SIN_VENTANAS") == "1"
        assert ajustes.abrir_en_segundo_plano() is False

    def test_el_modelo_no_tiene_como_pedirlo(self):
        from src.canal.herramientas import herramientas_del_equipo

        assert "abrir_ajustes" not in {h.name for h in herramientas_del_equipo(store=object())}


@pytest.mark.skipif(sys.platform != "win32", reason="un acceso .lnk de Windows")
class TestElMenuInicio:
    def test_se_crea_donde_toca_y_se_quita(self):
        destino = arranque.crear_acceso_de_ajustes()
        assert destino == arranque.acceso_de_ajustes() and destino.exists()
        assert os.environ["MORGAN_CARPETA_MENU"] in str(destino), "nunca el menú Inicio de quien pasa las pruebas"
        assert arranque.quitar_acceso_de_ajustes() and not destino.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="la ventana de Windows")
@pytest.mark.escritorio
class TestLaVentanaDeVerdad:
    """La ventana se construye entera (sin mostrarla ni esperar a nadie)."""

    @pytest.fixture
    def ventana(self):
        tkinter = pytest.importorskip("tkinter")
        try:
            raiz = tkinter.Tk()
        except tkinter.TclError as exc:
            pytest.skip(f"sin pantalla: {exc}")
        raiz.withdraw()
        Politica().guardar()
        v = ajustes.Ventana(raiz)
        yield v
        try:
            raiz.destroy()
        except tkinter.TclError:
            pass              # ya la cerró la prueba

    def test_tiene_una_casilla_por_capacidad_y_los_programas(self, ventana):
        from src.agente.terminal import CATALOGO

        assert set(ventana.casillas) == set(CAPACIDADES)
        assert set(ventana.programas) == set(CATALOGO)

    def test_un_atajo_y_guardar(self, ventana):
        ventana._atajo("Solo mirar")
        assert ventana.casillas["read_file"].get() and not ventana.casillas["create_file"].get()
        assert ventana.aviso.cget("text") == "Sin guardar"
        ventana.guardar()
        guardada = Politica.cargar()
        assert guardada.capacidades["read_file"] and not guardada.capacidades["create_file"]
        assert "Guardado" in ventana.aviso.cget("text")

    def test_cerrar_con_cambios_pregunta(self, ventana):
        ventana._atajo("Todo")
        ventana.cerrar(preguntar=lambda *a: None)          # «Cancelar»: sigue abierta
        assert ventana.raiz.winfo_exists()
        ventana.cerrar(preguntar=lambda *a: True)          # «Sí»: guarda y cierra
        assert Politica.cargar().capacidades["ui_control"]

    def test_un_programa(self, ventana):
        ventana.programas["git"].set(True)
        ventana._programa("git", ventana.programas["git"])
        ventana.guardar()
        assert "git" in Politica.cargar().programas


class TestLaRuta:
    @pytest.fixture
    def web(self, monkeypatch, modelo_simulado):
        from fastapi.testclient import TestClient

        from src.api import dependencies
        from src.api.app import create_app
        from src.api.sesion_web import CABECERA_CSRF
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
        reset_settings()
        dependencies.reset_container()
        cliente = TestClient(create_app())
        r = cliente.post("/auth/registro", json={"username": "ana", "email": "ana@ejemplo.co",
                                                 "password": "contrasena-larga"})
        cliente.headers[CABECERA_CSRF] = r.json()["csrf"]
        yield cliente, r.json()["usuario"]["id"]
        dependencies.reset_container()
        reset_settings()

    def _conexion(self, monkeypatch, user_id, capacidades):
        from src.canal import registro

        class Conexion:
            nombre = "Portátil"
            agent_id = "agt-1"

        Conexion.capacidades = frozenset(capacidades)
        monkeypatch.setattr(registro.REGISTRO, "de",
                            lambda u, a=None: Conexion() if u == user_id and a == "agt-1" else None)

    def test_desconectado(self, web):
        cliente, _ = web
        r = cliente.post("/auth/agentes/agt-1/abrir-ajustes")
        assert r.status_code == 409 and r.json()["error"]["code"] == "NO_CONECTADO"

    def test_un_agente_antiguo(self, web, monkeypatch):
        cliente, ana = web
        self._conexion(monkeypatch, ana, {"estado", "read_file"})
        r = cliente.post("/auth/agentes/agt-1/abrir-ajustes")
        assert r.status_code == 409 and r.json()["error"]["code"] == "AGENTE_ANTIGUO"

    def test_se_pide_al_pc_y_nada_mas(self, web, monkeypatch):
        from src.canal import despacho

        cliente, ana = web
        self._conexion(monkeypatch, ana, {"estado", "abrir_ajustes"})
        pedidas = []
        monkeypatch.setattr(despacho, "enviar", lambda cap, args=None, **k: pedidas.append((cap, args, k.get("equipo")))
                            or {"estado": "COMPLETED", "resultado": {"success": True, "data": {"abierta": True}}})
        assert cliente.post("/auth/agentes/agt-1/abrir-ajustes").status_code == 200
        assert pedidas == [("abrir_ajustes", {}, "agt-1")]

    def test_el_pc_de_otra_persona_no(self, web, monkeypatch):
        cliente, _ = web
        self._conexion(monkeypatch, "usr-otra", {"estado", "abrir_ajustes"})
        assert cliente.post("/auth/agentes/agt-1/abrir-ajustes").status_code == 409

    def test_el_token_trae_la_direccion_de_la_api(self, web, monkeypatch):
        cliente, _ = web
        monkeypatch.setenv("MORGAN_API_URL", "https://api.morgan.ejemplo")
        r = cliente.post("/auth/tokens", json={"nombre": "script", "alcances": ["chat"], "dias": 30})
        assert r.status_code == 200, r.text
        assert r.json()["api_url"] == "https://api.morgan.ejemplo"


def test_al_actualizar_aparece_en_el_menu_y_un_fallo_no_rompe(monkeypatch):
    """Quien actualiza desde antes de la 4.17 no lo tenía: se crea al cambiar de versión. Y si
    Windows no deja crearlo, la actualización sigue (no puede quedarse a medias por esto)."""
    from src.agente import instalacion

    creados = []
    monkeypatch.setattr(arranque, "activado", lambda: False)
    monkeypatch.setattr(arranque, "crear_acceso_de_ajustes", lambda python=None, carpeta=None: creados.append(carpeta))
    instalacion.cambiar_a("9.9.0")
    assert creados and creados[0].name == "9.9.0"

    def roto(python=None, carpeta=None):
        raise OSError("sin COM")

    monkeypatch.setattr(arranque, "crear_acceso_de_ajustes", roto)
    instalacion.cambiar_a("9.9.1")
    assert instalacion.leer_activa()["version"] == "9.9.1"


def test_al_confirmarse_una_version_nueva_lo_crea_si_falta(monkeypatch):
    """Medido al actualizar mi PC a la 4.17: la actualización la hace el agente viejo, que no
    sabe crearlo. El nuevo lo crea al confirmarse."""
    from src.agente import instalacion

    creados = []
    monkeypatch.setattr(arranque, "crear_acceso_de_ajustes", lambda python=None, carpeta=None: creados.append(carpeta))
    monkeypatch.setattr(instalacion, "_limpiar", lambda datos: None)
    instalacion._guardar_activa({"version": "9.9.2", "anterior": None, "pendiente": {"desde": 1.0}})
    assert instalacion.confirmar_si_pendiente("9.9.2") and creados and creados[0].name == "9.9.2"

