"""
La terminal ampliada (4.9, mi decisión E2: el catálogo crece, **sin shells**): npm,
pip, scripts propios, winget y los servicios de Windows.

Lo que haría daño si fallase: que npm pasara por `cmd.exe` (en Windows es `npm.cmd`:
«BatBadBut»), que un paquete ejecutara su código al instalarse (npm sin
`--ignore-scripts`, pip con un paquete en código fuente), que un script de fuera de la
carpeta de trabajo o código suelto (`-c`, `-e`) se ejecutara, que winget instalara algo sin
un identificador exacto, o que se parara un servicio que sostiene Windows.
"""

import json
import sys

import pytest

from src.agente import equipo, terminal
from src.agente.politica import DE_EJECUCION, DE_SERVICIOS, Politica
from tests.test_agente_terminal import preguntas, sin_appdata  # noqa: F401


@pytest.fixture
def pc(tmp_path, preguntas, monkeypatch):
    repo = tmp_path / "Proyecto"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "copia.py").write_text("print('hecho')", encoding="utf-8")
    (repo / "app.js").write_text("console.log('hola')", encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({"scripts": {"build": "vite build", "test": "vitest run"}}),
                                       encoding="utf-8")
    fuera = tmp_path / "Fuera"
    fuera.mkdir()
    (fuera / "ajeno.py").write_text("print('ajeno')", encoding="utf-8")
    politica = Politica()
    politica.anadir(str(repo))
    politica.permitir_escritura(str(repo))
    for c in DE_EJECUCION:
        politica.capacidades[c] = True
    politica.programas = ["python", "node", "npm", "winget"]
    politica.guardar()

    lanzados = []

    def correr(argv, carpeta, plazo, cancelable):
        lanzados.append((argv, carpeta))
        return {"codigo": 0, "salida": "ok", "cortada": False, "vencido": False, "segundos": 0.1}

    monkeypatch.setattr(terminal, "_correr", correr)
    return {"repo": repo, "fuera": fuera, "lanzados": lanzados, "preguntas": preguntas}


class TestLoQueSeFija:
    def test_npm_instala_sin_scripts(self):
        _, uso, finales = terminal.validar("npm", ["install", "vite@5.4.2", "-D"])
        assert uso.cambia and "--ignore-scripts" in finales

    def test_pip_solo_ruedas(self):
        _, uso, finales = terminal.validar("python", ["-m", "pip", "install", "requests==2.32.3"])
        assert uso.cambia and finales[finales.index("--only-binary") + 1] == ":all:"

    def test_winget_por_identificador_exacto_y_de_su_origen(self):
        _, uso, finales = terminal.validar("winget", ["install", "--id", "Microsoft.VisualStudioCode"])
        assert uso.cambia and "--exact" in finales and finales[finales.index("--source") + 1] == "winget"

    @pytest.mark.parametrize("programa, args", [
        ("npm", ["ls"]), ("npm", ["outdated"]), ("winget", ["search", "vscode"]),
        ("winget", ["list", "--upgrade-available"]), ("python", ["-m", "pip", "list"]),
    ])
    def test_lo_que_consulta_no_cambia(self, programa, args):
        assert not terminal.validar(programa, args)[1].cambia


class TestNpmSinCmd:
    def test_se_lanza_node_con_npm_cli(self, pc):
        r = terminal.run_command("npm", ["ls"], cwd=str(pc["repo"]))
        assert r["success"], r
        argv, _ = pc["lanzados"][0]
        assert argv[0].lower().endswith("node.exe") and argv[1].replace("\\", "/").endswith("npm/bin/npm-cli.js")
        assert not any(a.lower().endswith(("cmd.exe", "npm.cmd")) for a in argv)

    def test_run_enseña_lo_que_ejecuta(self, pc):
        r = terminal.run_change_command("npm", ["run", "build"], cwd=str(pc["repo"]))
        assert r["success"], r
        titulo, detalle = pc["preguntas"]["hechas"][0]
        assert "Ejecuta: vite build" in detalle

    def test_un_script_que_no_existe_no_se_pregunta(self, pc):
        r = terminal.run_change_command("npm", ["run", "deploy"], cwd=str(pc["repo"]))
        assert not r["success"] and r["motivo"] == "script"
        assert not pc["preguntas"]["hechas"] and not pc["lanzados"]

    def test_sin_npm_junto_a_node(self, pc, monkeypatch, tmp_path):
        falso = tmp_path / "nodo" / "node.exe"
        falso.parent.mkdir()
        falso.write_bytes(b"")
        monkeypatch.setattr(terminal, "localizar", lambda exe, prohibidas=(): falso)
        r = terminal.run_command("npm", ["--version"])
        assert not r["success"] and r["motivo"] == "no_instalado"


class TestScriptsPropios:
    def test_uno_de_la_carpeta_de_trabajo(self, pc):
        r = terminal.run_change_command("python", ["scripts/copia.py"], cwd=str(pc["repo"]))
        assert r["success"], r
        assert pc["lanzados"][0][0][-1] == "scripts/copia.py" and pc["preguntas"]["hechas"]

    def test_con_run_command_no(self, pc):
        r = terminal.run_command("python", ["scripts/copia.py"], cwd=str(pc["repo"]))
        assert not r["success"] and r["motivo"] == "otra_herramienta" and not pc["lanzados"]

    def test_uno_que_no_existe(self, pc):
        r = terminal.run_change_command("node", ["no.js"], cwd=str(pc["repo"]))
        assert not r["success"] and r["motivo"] == "script" and not pc["lanzados"]

    def test_uno_de_fuera_por_un_enlace(self, pc):
        enlace = pc["repo"] / "atajo"
        try:
            import _winapi

            _winapi.CreateJunction(str(pc["fuera"]), str(enlace))      # sin permisos especiales
        except (ImportError, OSError):
            pytest.skip("sin forma de crear el enlace")
        r = terminal.run_change_command("python", ["atajo/ajeno.py"], cwd=str(pc["repo"]))
        assert not r["success"] and not pc["lanzados"]

    def test_con_un_no_no_se_ejecuta(self, pc):
        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        r = terminal.run_change_command("node", ["app.js"], cwd=str(pc["repo"]))
        assert not r["success"] and not pc["lanzados"]


class TestServicios:
    def _encender(self):
        politica = Politica.cargar()
        for c in DE_SERVICIOS:
            politica.capacidades[c] = True
        politica.guardar()

    def test_nace_apagada(self, pc):
        assert not Politica().capacidades["service_control"]
        r = equipo.service_control("Spooler", "parar")
        assert not r["success"] and r["motivo"] == "capacidad_apagada"

    def test_accion_rara(self, pc):
        self._encender()
        assert equipo.service_control("Spooler", "borrar")["motivo"] == "argumentos"

    @pytest.mark.skipif(sys.platform != "win32", reason="servicios de Windows")
    def test_uno_que_no_existe(self, pc):
        self._encender()
        assert equipo.service_control("NoExisteNadaAsi", "iniciar")["motivo"] == "no_existe"

    @pytest.mark.skipif(sys.platform != "win32", reason="servicios de Windows")
    @pytest.mark.parametrize("nombre", ["WinDefend", "mpssvc", "EventLog", "Dnscache"])
    def test_los_que_sostienen_windows_no_se_paran(self, pc, nombre):
        self._encender()
        r = equipo.service_control(nombre, "parar")
        assert not r["success"] and r["motivo"] in ("protegido", "no_existe")
        assert not pc["preguntas"]["hechas"]

    @pytest.mark.skipif(sys.platform != "win32", reason="servicios de Windows")
    def test_con_un_no_no_se_toca(self, pc):
        self._encender()
        import psutil

        antes = psutil.win_service_get("Spooler").status()
        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        r = equipo.service_control("Spooler", "parar")
        assert not r["success"] and r["motivo"] == "no_confirmada"
        assert psutil.win_service_get("Spooler").status() == antes


class TestEnLaNube:
    def test_servicios_rojo_y_con_plan(self):
        from src.canal.herramientas import ServicioDelEquipo, herramientas_del_equipo

        (h,) = [h for h in herramientas_del_equipo() if isinstance(h, ServicioDelEquipo)]
        assert h.name == "service_control" and h.permission_level == "critical" and h.exige_plan

    def test_la_terminal_nombra_lo_nuevo(self):
        from src.canal.herramientas import TERMINAL_EN_EL_EQUIPO

        consulta, cambio = TERMINAL_EN_EL_EQUIPO["run_command"][0], TERMINAL_EN_EL_EQUIPO["run_change_command"][0]
        assert "winget search" in consulta and "npm ls" in consulta
        assert all(p in cambio for p in ("npm install", "pip install", "winget install", "python x.py"))


@pytest.mark.parametrize("accion", ["install", "upgrade", "uninstall", "show"])
def test_winget_siempre_con_su_identificador(accion):
    with pytest.raises(terminal.Denegado) as exc:
        terminal.validar("winget", [accion])
    assert exc.value.motivo == "argumentos"


class TestLoQueMidioElModelo49:
    """Con el modelo real (4.9): planeó `python -m pip install requests` y `winget install -e
    --id …` con `cwd` vacío, y los dos habrían fallado al aprobarlos; y rechazado `python -c`,
    propuso crear un script temporal."""

    @pytest.mark.parametrize("programa, args", [
        ("python", ["-m", "pip", "install", "requests"]),
        ("winget", ["install", "-e", "--id", "Microsoft.VisualStudioCode"]),
    ])
    def test_sin_carpeta_se_autoriza_y_corre_en_una_vacia(self, pc, programa, args):
        r = terminal.run_change_command(programa, args, cwd="")
        assert r["success"], r
        argv, carpeta = pc["lanzados"][0]
        assert "sin_hooks" in carpeta and pc["preguntas"]["hechas"]
        assert argv.count("--exact") <= 1 and "-e" not in argv

    def test_lo_que_necesita_carpeta_la_sigue_necesitando(self, pc):
        r = terminal.run_change_command("npm", ["ci"], cwd="")
        assert not r["success"] and r["motivo"] == "falta_carpeta" and not pc["lanzados"]

    @pytest.mark.parametrize("args", [["-c", "print(1)"], ["-m", "http.server"]])
    def test_codigo_suelto_se_dice_sin_rodeos(self, args):
        with pytest.raises(terminal.Denegado) as exc:
            terminal.validar("python", args)
        assert "No busques otra forma" in str(exc.value)
