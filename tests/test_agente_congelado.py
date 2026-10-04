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
