"""
El contexto del PC (4.13): `pc_context`, los proyectos de código y los editores instalados,
para que «abre el proyecto Morgan» no necesite la ruta.

Lo que haría daño si fallase: enseñar carpetas fuera de lo permitido o bloqueadas, colarse
en las de credenciales o en la del propio agente, recorrer el disco entero sin tope, o que
el escritorio (con un `package.json` suelto) tape los proyectos que tiene dentro.
"""

import os
import time

import pytest

from src.agente import capacidades, contexto
from src.agente.politica import DE_LECTURA, Politica


@pytest.fixture
def casa(tmp_path, monkeypatch):
    """Una casa de mentira con proyectos, permitida entera."""
    raiz = tmp_path / "Usuario"
    escritorio = raiz / "Desktop"
    for carpeta, marca in [("Desktop/morgan", ".git"), ("Desktop/tienda-web", "package.json"),
                           ("source/repos/Calculadora", "Calculadora.sln"),
                           ("Documents/viejo/cosas/datos", "pyproject.toml")]:
        destino = raiz / carpeta
        destino.mkdir(parents=True)
        if marca == ".git":
            (destino / ".git").mkdir()
            (destino / ".git" / "HEAD").write_text("ref: refs/heads/feature/voz\n", encoding="utf-8")
        else:
            (destino / marca).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(raiz))
    monkeypatch.setattr(capacidades, "carpetas_personales", lambda: {"escritorio": str(escritorio)})
    monkeypatch.setattr("src.agente.aplicaciones.instaladas", lambda: [
        {"nombre": n} for n in ("Visual Studio Code", "Spotify", "PyCharm Community Edition 2024.1",
                                "Customized Launcher", "Atomic Wallet", "Notepad++")])
    monkeypatch.setattr(contexto, "_RECORDADO", {})
    politica = Politica()
    politica.anadir(str(raiz))
    politica.guardar()
    return raiz


def _nombres(r):
    return {p["nombre"] for p in r["data"]["proyectos"]}


class TestNace:
    def test_encendida_y_solo_con_carpetas(self):
        assert "pc_context" in DE_LECTURA and Politica().capacidades["pc_context"]
        Politica().guardar()
        assert "pc_context" not in capacidades.disponibles(Politica.cargar())

    def test_se_anuncia_con_carpetas(self, casa):
        assert "pc_context" in capacidades.disponibles(Politica.cargar())

    def test_apagada_no_mira_nada(self, casa):
        politica = Politica.cargar()
        politica.capacidades["pc_context"] = False
        politica.guardar()
        assert contexto.pc_context()["motivo"] == "capacidad_apagada"


class TestProyectos:
    def test_encuentra_cada_tipo_con_su_rama(self, casa):
        r = contexto.pc_context()
        assert r["success"], r
        por_nombre = {p["nombre"]: p for p in r["data"]["proyectos"]}
        assert set(por_nombre) == {"morgan", "tienda-web", "Calculadora", "datos"}
        assert por_nombre["morgan"]["tipo"] == "git" and por_nombre["morgan"]["rama"] == "voz"
        assert por_nombre["tienda-web"]["tipo"] == "node" and por_nombre["tienda-web"]["rama"] is None
        assert por_nombre["Calculadora"]["tipo"] == ".net"
        assert por_nombre["morgan"]["path"] == str(casa / "Desktop" / "morgan")

    def test_el_mas_reciente_primero(self, casa):
        viejo = time.time() - 86400 * 30
        for p in (casa / "Desktop" / "tienda-web", casa / "Desktop" / "tienda-web" / "package.json"):
            os.utime(p, (viejo, viejo))
        (casa / "Desktop" / "morgan" / "nuevo.py").write_text("", encoding="utf-8")
        nombres = [p["nombre"] for p in contexto.pc_context()["data"]["proyectos"]]
        assert nombres.index("morgan") < nombres.index("tienda-web")

    def test_buscar_filtra(self, casa):
        assert _nombres(contexto.pc_context(buscar="TIENDA")) == {"tienda-web"}

    def test_un_package_json_suelto_en_el_escritorio_no_tapa_lo_de_dentro(self, casa):
        (casa / "Desktop" / "package.json").write_text("{}", encoding="utf-8")
        assert {"morgan", "tienda-web"} <= _nombres(contexto.pc_context())

    def test_dentro_de_un_proyecto_no_busca_mas(self, casa):
        (casa / "Desktop" / "morgan" / "web").mkdir()
        (casa / "Desktop" / "morgan" / "web" / "package.json").write_text("{}", encoding="utf-8")
        assert "web" not in _nombres(contexto.pc_context())

    def test_una_carpeta_permitida_a_proposito_si_puede_ser_un_proyecto(self, tmp_path, monkeypatch):
        """Quien permite solo `D:\\code\\app` tiene que poder abrirla."""
        app = tmp_path / "solo" / "app"
        app.mkdir(parents=True)
        (app / "Cargo.toml").write_text("", encoding="utf-8")
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "otra"))
        monkeypatch.setattr(capacidades, "carpetas_personales", lambda: {})
        monkeypatch.setattr("src.agente.aplicaciones.instaladas", lambda: [])
        monkeypatch.setattr(contexto, "_RECORDADO", {})
        politica = Politica()
        politica.anadir(str(app))
        politica.guardar()
        r = contexto.pc_context()
        assert [p["tipo"] for p in r["data"]["proyectos"]] == ["rust"]


class TestLimites:
    def test_no_entra_en_lo_bloqueado(self, casa):
        politica = Politica.cargar()
        politica.bloquear(str(casa / "Desktop" / "morgan"))
        politica.guardar()
        assert "morgan" not in _nombres(contexto.pc_context())

    def test_ni_en_las_ruidosas_ni_en_las_de_credenciales(self, casa):
        for oculta in ("node_modules/paquete", ".ssh/repo", "AppData/Local/algo", "venv/lib"):
            destino = casa / "Desktop" / oculta
            destino.mkdir(parents=True)
            (destino / "package.json").write_text("{}", encoding="utf-8")
        assert _nombres(contexto.pc_context()) == {"morgan", "tienda-web", "Calculadora", "datos"}

    def test_nada_fuera_de_lo_permitido(self, casa, tmp_path):
        fuera = tmp_path / "Fuera" / "secreto"
        fuera.mkdir(parents=True)
        (fuera / "package.json").write_text("{}", encoding="utf-8")
        politica = Politica()
        politica.anadir(str(casa / "Desktop"))
        politica.guardar()
        contexto._RECORDADO.clear()
        nombres = _nombres(contexto.pc_context())
        assert nombres == {"morgan", "tienda-web"}, "ni Documents ni source: no están permitidas"

    def test_hasta_la_profundidad(self, casa, monkeypatch):
        monkeypatch.setattr(contexto, "PROFUNDIDAD", 1)
        assert "datos" not in _nombres(contexto.pc_context())

    def test_con_tope_de_tiempo_lo_dice(self, casa, monkeypatch):
        monkeypatch.setattr(contexto, "MAX_SEGUNDOS", -1)
        r = contexto.pc_context()
        assert r["success"] and "aviso" in r["data"]

    def test_con_tope_de_proyectos(self, casa, monkeypatch):
        monkeypatch.setattr(contexto, "MAX_PROYECTOS", 2)
        r = contexto.pc_context()
        assert len(r["data"]["proyectos"]) == 2 and "aviso" in r["data"]


class TestEditores:
    def test_solo_los_editores_y_por_palabra_entera(self, casa):
        assert contexto.pc_context()["data"]["editores"] == [
            "Notepad++", "PyCharm Community Edition 2024.1", "Visual Studio Code"]


class TestRecuerda:
    def test_la_segunda_vez_no_recorre(self, casa, monkeypatch):
        contexto.pc_context()
        monkeypatch.setattr(contexto, "proyectos", lambda p: pytest.fail("volvió a recorrer"))
        assert contexto.pc_context(buscar="morgan")["success"]

    def test_si_cambia_la_politica_si(self, casa, monkeypatch):
        contexto.pc_context()
        llamadas = []
        original = contexto.proyectos
        monkeypatch.setattr(contexto, "proyectos", lambda p: llamadas.append(1) or original(p))
        time.sleep(0.05)
        politica = Politica.cargar()
        politica.bloquear(str(casa / "Desktop" / "morgan"))
        politica.guardar()
        assert "morgan" not in _nombres(contexto.pc_context()) and llamadas

    def test_pasado_el_rato_si(self, casa, monkeypatch):
        contexto.pc_context()
        monkeypatch.setattr(contexto, "RECUERDA", -1)
        llamadas = []
        original = contexto.proyectos
        monkeypatch.setattr(contexto, "proyectos", lambda p: llamadas.append(1) or original(p))
        contexto.pc_context()
        assert llamadas


class TestEnLaNube:
    def test_verde_sin_plan_consulta_y_del_pc(self):
        from src.agent import fuga
        from src.canal.herramientas import DiagnosticoDelEquipo, herramientas_del_equipo
        from src.tools.planificacion import CONSULTAS

        h = next(x for x in herramientas_del_equipo() if isinstance(x, DiagnosticoDelEquipo) and x.name == "pc_context")
        assert h.permission_level == "safe" and not h.exige_plan
        assert "pc_context" in CONSULTAS and "pc_context" in fuga.DEL_PC
