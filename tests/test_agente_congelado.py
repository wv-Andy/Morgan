"""
El agente congelado (5.0, decisión W2: PyInstaller).

`morgan-agente.exe` no tiene Python al lado: todo lo que arrancaba `pythonw -m src.agente …`
(el acceso de Inicio, el vigilante, la ventana de ajustes, la actualización) tiene que
arrancar el propio ejecutable. Y lo que corre de fondo, el que no tiene consola (o se abriría
una ventana negra al iniciar sesión). Lo que haría daño si fallase: que el congelado buscara
un `pythonw.exe` que no existe y Morgan no arrancara con Windows.
"""

import sys
from pathlib import Path

from src.agente import arranque


def _congelar(monkeypatch, tmp_path, con_fondo=True):
    exe = tmp_path / "morgan-agente.exe"
    exe.write_text("")
    if con_fondo:
        (tmp_path / arranque.FONDO).write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


class TestConPython:
    def test_como_siempre(self):
        orden = arranque.orden("vigilar")
        assert orden[1:] == ["-m", "src.agente", "vigilar"]
        assert Path(orden[0]).name.lower().startswith("python")

    def test_con_consola_el_mismo_python(self):
        assert arranque.orden("estado", sin_consola=False)[0] == sys.executable


class TestCongelado:
    def test_de_fondo_el_que_no_tiene_consola(self, monkeypatch, tmp_path):
        _congelar(monkeypatch, tmp_path)
        assert arranque.orden("vigilar") == [str(tmp_path / arranque.FONDO), "vigilar"]

    def test_con_consola_el_de_las_ordenes(self, monkeypatch, tmp_path):
        exe = _congelar(monkeypatch, tmp_path)
        assert arranque.orden("estado", sin_consola=False) == [str(exe), "estado"]

    def test_sin_el_de_fondo_el_otro_antes_que_nada(self, monkeypatch, tmp_path):
        exe = _congelar(monkeypatch, tmp_path, con_fondo=False)
        assert arranque.orden("vigilar") == [str(exe), "vigilar"]

    def test_la_carpeta_es_la_del_programa(self, monkeypatch, tmp_path):
        _congelar(monkeypatch, tmp_path)
        assert arranque.raiz_del_proyecto() == tmp_path

    def test_una_version_instalada_con_su_python_sigue_con_python(self, monkeypatch, tmp_path):
        """El arranque de una versión concreta (3.8) pasa su `python`: ese manda."""
        _congelar(monkeypatch, tmp_path)
        python = tmp_path / "v" / "python.exe"
        assert arranque.orden("vigilar", python=python)[1:] == ["-m", "src.agente", "vigilar"]

    def test_el_vigilante_lanza_el_congelado(self, monkeypatch, tmp_path):
        from src.agente import vigilante

        _congelar(monkeypatch, tmp_path)
        lanzados = []
        monkeypatch.setattr(vigilante.subprocess, "Popen", lambda cmd, **k: lanzados.append(cmd))
        vigilante.lanzar_agente()
        assert lanzados == [[str(tmp_path / arranque.FONDO), "conectar"]]


class TestNoSeActualizaSolo:
    """Congelado no hay Python ni pip: la actualización de hoy (carpetas por versión con su
    entorno) fallaría o dejaría una versión a medias. Se actualiza el programa entero
    (Tauri, 5.3)."""

    def test_actualizar_lo_dice_y_no_toca_nada(self, monkeypatch, tmp_path):
        from src.agente import instalacion

        _congelar(monkeypatch, tmp_path)
        dicho = []
        assert instalacion.actualizar(decir=dicho.append) == 2
        assert dicho == [instalacion.PROGRAMA_DE_WINDOWS]

    def test_no_mira_novedades(self, monkeypatch, tmp_path):
        from src.agente import instalacion

        _congelar(monkeypatch, tmp_path)
        llamadas = []
        instalacion.mirar_novedades(lambda: False, http=lambda *a, **k: llamadas.append(a),
                                    dormir=lambda s: llamadas.append(s))
        assert llamadas == [], "ni espera ni pregunta a la nube"

    def test_volver_e_instalar_lo_dicen(self, monkeypatch, tmp_path, capsys):
        from src.agente import __main__ as consola
        from src.agente.instalacion import PROGRAMA_DE_WINDOWS

        _congelar(monkeypatch, tmp_path)
        for orden in (["volver"], ["instalar"]):
            assert consola.main(orden) == 2
            assert PROGRAMA_DE_WINDOWS in capsys.readouterr().out


class TestElDesinstaladorSoloQuitaLoSuyo:
    """5.0.1. Medido el 2026-10-04: instalar y desinstalar el programa en un PC con el agente de
    la línea de PowerShell (comparten la carpeta del estado) lo desemparejó en la nube y borró
    su política. El desinstalador solo desempareja y borra si el agente lo emparejó el programa."""

    def _estado_de_otro_agente(self):
        from src.agente import estado as almacen

        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        (almacen.carpeta() / "politica.json").write_text("{}", encoding="utf-8")
        return almacen.carpeta()

    def test_si_no_es_suyo_no_toca_nada(self, monkeypatch, tmp_path, capsys):
        from src.agente import __main__ as consola, instalacion

        carpeta = self._estado_de_otro_agente()
        llamadas = []
        monkeypatch.setattr(instalacion, "desinstalar", lambda *a, **k: llamadas.append(1) or 0)
        assert consola.main(["desinstalar", "--si", "--del-programa"]) == 0
        assert llamadas == [] and (carpeta / "politica.json").exists()
        assert instalacion.NO_ES_DEL_PROGRAMA in capsys.readouterr().out

    def test_si_es_suyo_si(self, monkeypatch, tmp_path):
        from src.agente import __main__ as consola, instalacion

        _congelar(monkeypatch, tmp_path)
        instalacion.marcar_del_programa()
        assert instalacion.es_del_programa()
        llamadas = []
        monkeypatch.setattr(instalacion, "desinstalar", lambda *a, **k: llamadas.append(1) or 0)
        assert consola.main(["desinstalar", "--si", "--del-programa"]) == 0
        assert llamadas == [1]

    def test_emparejar_desde_el_programa_lo_marca(self, monkeypatch, tmp_path):
        from src.agente import __main__ as consola, instalacion

        _congelar(monkeypatch, tmp_path)
        monkeypatch.setattr(consola, "emparejar", lambda *a, **k: True)
        consola.main(["emparejar", "--codigo", "ABCD-EFGH", "--si"])
        assert instalacion.es_del_programa()

    def test_con_python_no_se_marca(self, monkeypatch):
        """El agente de la línea no es del programa: su desinstalación es la suya."""
        from src.agente import __main__ as consola, instalacion

        monkeypatch.setattr(consola, "emparejar", lambda *a, **k: True)
        consola.main(["emparejar", "--codigo", "ABCD-EFGH", "--si"])
        assert not instalacion.es_del_programa()

    def test_un_emparejar_fallido_no_marca(self, monkeypatch, tmp_path):
        from src.agente import __main__ as consola, instalacion

        _congelar(monkeypatch, tmp_path)
        monkeypatch.setattr(consola, "emparejar", lambda *a, **k: None)
        consola.main(["emparejar", "--codigo", "ABCD-EFGH", "--si"])
        assert not instalacion.es_del_programa()

    def test_el_gancho_del_instalador_lo_pide(self):
        gancho = (Path(__file__).resolve().parents[1] / "escritorio" / "src-tauri" / "hooks.nsh").read_text(encoding="utf-8")
        assert "desinstalar --si --del-programa" in gancho
