"""
La terminal y los procesos del PC (3.5).

Mis decisiones (2026-09-25): **catálogo cerrado, sin shell**, apagado hasta
encenderlo en el PC programa a programa; lo que cambia cosas, con plan y «Permitir» en el
PC; de los intérpretes **solo consultas**, y los scripts pasan a lo que Morgan no crea;
terminar **solo procesos de la persona**. Gate del plan: no existe `Cloud → ejecución
arbitraria de comandos`.

Con git, ping y procesos de verdad.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.agente import aviso, capacidades, control, escritura, terminal
from src.agente.ejecutor import Ejecutor
from src.agente.politica import DE_EJECUCION, Politica
from src.agente.protocolo import PROTOCOLO_ACTUAL

en_windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows")
con_git = pytest.mark.skipif(shutil.which("git") is None, reason="sin git")


@pytest.fixture(autouse=True)
def sin_appdata(tmp_path, monkeypatch):
    for variable in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
        (tmp_path / "_sistema" / variable).mkdir(parents=True)
        monkeypatch.setenv(variable, str(tmp_path / "_sistema" / variable))
    for variable, valor in (("GIT_AUTHOR_NAME", "Prueba"), ("GIT_AUTHOR_EMAIL", "p@x.co"),
                            ("GIT_COMMITTER_NAME", "Prueba"), ("GIT_COMMITTER_EMAIL", "p@x.co")):
        monkeypatch.setenv(variable, valor)


@pytest.fixture
def preguntas(monkeypatch):
    hechas, respuesta = [], {"valor": aviso.PERMITIDA}

    def preguntar(titulo, detalle, espera=aviso.ESPERA):
        hechas.append((titulo, detalle))
        return respuesta["valor"]

    monkeypatch.setattr(aviso, "preguntar", preguntar)
    return {"hechas": hechas, "respuesta": respuesta}


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def pc(tmp_path, preguntas):
    """Un repositorio de verdad en una carpeta de lectura y escritura, git encendido."""
    repo = tmp_path / "Proyecto"
    repo.mkdir()
    politica = Politica()
    politica.anadir(str(repo))
    politica.permitir_escritura(str(repo))
    for c in DE_EJECUCION:
        politica.capacidades[c] = True
    politica.programas = ["git", "ping", "ipconfig", "python"]
    politica.guardar()
    if shutil.which("git"):
        _git("init", "-q", "-b", "main", cwd=repo)
        (repo / "leeme.txt").write_text("hola", encoding="utf-8")
        _git("add", "leeme.txt", cwd=repo)
        _git("commit", "-q", "-m", "primero", cwd=repo)
    return {"repo": repo, "preguntas": preguntas}


# --- El catálogo: lo que no está en la regla, no pasa -----------------------------------

class TestElCatalogo:
    @pytest.mark.parametrize("programa, args", [
        ("git", ["status"]), ("git", ["status", "-s"]), ("git", ["log", "-n", "5", "--oneline"]),
        ("git", ["log", "--max-count=3"]), ("git", ["diff", "--stat", "HEAD~1"]),
        ("git", ["add", "leeme.txt", "src/x.txt"]), ("git", ["commit", "-m", "cambio"]),
        ("ping", ["localhost", "-n", "2"]), ("ipconfig", ["/all"]), ("python", ["--version"]),
        ("python", ["-m", "pip", "list"]), ("nslookup", ["example.com"]), ("GIT", ["status"]),
    ])
    def test_lo_permitido(self, programa, args):
        terminal.validar(programa, args)

    @pytest.mark.parametrize("programa, args, motivo", [
        ("powershell", ["-c", "Get-Process"], "programa_prohibido"),
        ("cmd", ["/c", "dir"], "programa_prohibido"),
        ("schtasks", ["/query"], "programa_prohibido"),
        ("curl.exe", ["x"], "fuera_del_catalogo"),
        # npm entra en la 4.9 (sin cmd.exe); lo que ejecuta código a su aire, no.
        ("npm", ["exec", "cowsay"], "uso_no_permitido"),
        ("npx", ["cowsay"], "fuera_del_catalogo"),
        ("npm", ["install", "--foreground-scripts", "x"], "opcion_no_permitida"),
        ("npm", ["run", "build;calc"], "argumentos"),
        ("git", ["push"], "uso_no_permitido"),
        ("git", ["-c", "core.pager=calc", "status"], "uso_no_permitido"),
        ("git", ["reset", "--hard"], "uso_no_permitido"),
        ("git", ["log", "--output=robado.txt"], "opcion_no_permitida"),
        ("git", ["diff", "--ext-diff"], "opcion_no_permitida"),
        ("git", ["log", "-p"], "opcion_no_permitida"),
        ("git", ["log", "--upload-pack=x"], "opcion_no_permitida"),
        ("git", ["commit"], "argumentos"),
        ("git", ["commit", "-m", "linea\ncon salto"], "argumentos"),
        ("git", ["add", "../fuera.txt"], "argumentos"),
        ("git", ["add", "--", "-e"], "argumentos"),
        ("git", ["log", "-n", "abc"], "argumentos"),
        ("ping", ["-t", "localhost"], "opcion_no_permitida"),
        ("ping", ["localhost", "-n", "500"], "argumentos"),
        ("ping", ["localhost;calc"], "argumentos"),
        ("ping", ["localhost", "&", "calc"], "argumentos"),
        ("nslookup", [], "argumentos"),
        # Desde la 4.9 un script propio y pip install entran (con plan y «Permitir»); el
        # código suelto, no.
        ("python", ["-c", "print(1)"], "opcion_no_permitida"),
        ("python", ["-m", "http.server"], "opcion_no_permitida"),
        ("python", ["../fuera.py"], "argumentos"),
        ("python", ["C:\\Users\\ana\\otro.py"], "argumentos"),
        ("python", ["notas.txt"], "argumentos"),
        ("python", ["-m", "pip", "install", "x", "--index-url", "http://malo"], "opcion_no_permitida"),
        ("node", ["-e", "1"], "opcion_no_permitida"),
        ("node", ["--eval", "1"], "opcion_no_permitida"),
        ("node", ["app.txt"], "argumentos"),
        ("winget", ["install", "Microsoft.VisualStudioCode"], "argumentos"),
        ("winget", ["install", "--id", "x;calc"], "argumentos"),
        ("winget", ["install", "--id", "x", "--override", "/S"], "opcion_no_permitida"),
        ("winget", ["configure", "archivo.yaml"], "uso_no_permitido"),
    ])
    def test_lo_que_no(self, programa, args, motivo):
        with pytest.raises(terminal.Denegado) as exc:
            terminal.validar(programa, args)
        assert exc.value.motivo == motivo

    @pytest.mark.parametrize("args", [None, "status", ["status", 1], ["status"] * 41, ["x" * 501]])
    def test_argumentos_que_no_son_una_lista_de_textos_cortos(self, args):
        with pytest.raises(terminal.Denegado):
            terminal.validar("git", args)

    def test_lo_que_se_pone_siempre(self):
        assert terminal.validar("ping", ["localhost"])[2] == ["-n", "4", "localhost"]
        assert terminal.validar("git", ["add", "a.txt"])[2] == ["add", "--", "a.txt"]
        assert terminal.validar("git", ["log"])[2][-2:] == ["-n", "20"]
        assert "--ff-only" in terminal.validar("git", ["pull"])[2]
        assert {"--no-ext-diff", "--no-textconv"} <= set(terminal.validar("git", ["diff"])[2])

    def test_cada_uso_dice_si_cambia(self):
        assert not terminal.validar("git", ["status"])[1].cambia
        assert terminal.validar("git", ["commit", "-m", "x"])[1].cambia

    def test_nada_del_catalogo_esta_en_lo_prohibido(self):
        assert not set(terminal.CATALOGO) & terminal.NUNCA
        assert all(p.ejecutable.lower().endswith(".exe") for p in terminal.CATALOGO.values())


class TestDondeSeBuscaElPrograma:
    def test_nunca_en_la_carpeta_de_trabajo(self, tmp_path, monkeypatch):
        """Windows busca primero en la carpeta actual: un git.exe puesto ahí suplantaría."""
        falso = tmp_path / "git.exe"
        falso.write_bytes(b"MZ")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", "." + os.pathsep + "relativa" + os.pathsep)
        assert terminal.localizar("git.exe") is None

    def test_ni_en_una_carpeta_de_escritura(self, tmp_path, monkeypatch):
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "git.exe").write_bytes(b"MZ")
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        assert terminal.localizar("git.exe") is not None
        assert terminal.localizar("git.exe", [str(tmp_path)]) is None


class TestElEntorno:
    def test_sin_secretos_y_sin_preguntar(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_secreto")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secreto")
        entorno = terminal.entorno_limpio()
        assert "GROQ_API_KEY" not in entorno and "GITHUB_TOKEN" not in entorno
        assert entorno["GIT_TERMINAL_PROMPT"] == "0"

    def test_la_identidad_de_git_se_queda(self):
        """Encontrado al probarlo: buscar «AUTH» dentro del nombre quitaba GIT_AUTHOR_NAME."""
        assert terminal.entorno_limpio()["GIT_AUTHOR_NAME"] == "Prueba"


# --- Ejecutar de verdad -----------------------------------------------------------------

@en_windows
@con_git
class TestGitDeVerdad:
    def test_consultar(self, pc):
        r = terminal.run_command("git", ["status"], str(pc["repo"]))
        assert "comprobado" not in r["data"], "una consulta no cambia nada: no hay efecto que comprobar"
        assert r["success"] and "main" in r["data"]["salida"]
        assert pc["preguntas"]["hechas"] == []

    def test_cambiar_pregunta_con_el_comando(self, pc):
        (pc["repo"] / "nuevo.txt").write_text("x", encoding="utf-8")
        assert terminal.run_change_command("git", ["add", "nuevo.txt"], str(pc["repo"]))["success"]
        r = terminal.run_change_command("git", ["commit", "-m", "desde Morgan"], str(pc["repo"]))
        assert r["success"], r
        assert "desde Morgan" in _git("log", "-1", "--format=%s", cwd=pc["repo"]).stdout
        assert r["data"]["comprobado"] == "terminó con el código 0", "4.0-B: dice lo que comprobó"
        (titulo, detalle), *_ = pc["preguntas"]["hechas"][-1:]
        assert "git commit" in detalle and str(pc["repo"]) in detalle

    def test_con_un_no_no_cambia_nada(self, pc):
        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        (pc["repo"] / "nuevo.txt").write_text("x", encoding="utf-8")
        r = terminal.run_change_command("git", ["add", "nuevo.txt"], str(pc["repo"]))
        assert not r["success"] and r["motivo"] == "no_confirmada"
        assert "nuevo.txt" not in _git("diff", "--cached", "--name-only", cwd=pc["repo"]).stdout

    def test_lo_que_cambia_no_va_por_consultar(self, pc):
        r = terminal.run_command("git", ["pull"], str(pc["repo"]))
        assert r["motivo"] == "otra_herramienta"
        r = terminal.run_change_command("git", ["status"], str(pc["repo"]))
        assert r["motivo"] == "otra_herramienta"

    def test_los_hooks_del_repositorio_no_corren(self, pc):
        """Un repositorio descargado trae sus hooks: con Morgan, no corren."""
        marca = pc["repo"].parent / "hook_corrio.txt"
        gancho = pc["repo"] / ".git" / "hooks" / "pre-commit"
        gancho.write_text(f"#!/bin/sh\necho x > '{marca.as_posix()}'\n", encoding="utf-8")
        (pc["repo"] / "b.txt").write_text("b", encoding="utf-8")
        _git("add", "b.txt", cwd=pc["repo"])
        assert terminal.run_change_command("git", ["commit", "-m", "sin hook"], str(pc["repo"]))["success"]
        assert not marca.exists()
        # Y la prueba de que el hook de verdad corre, sin Morgan:
        (pc["repo"] / "c.txt").write_text("c", encoding="utf-8")
        _git("add", "c.txt", cwd=pc["repo"])
        _git("commit", "-q", "-m", "con hook", cwd=pc["repo"])
        assert marca.exists()

    def test_la_configuracion_que_ejecuta_no_corre(self, pc):
        """`core.fsmonitor` en la configuración del repositorio: git lo lanza en cada status."""
        marca = pc["repo"].parent / "fsmonitor_corrio.txt"
        guion = pc["repo"].parent / "vigia.sh"
        guion.write_text(f"#!/bin/sh\necho x > '{marca.as_posix()}'\n", encoding="utf-8")
        _git("config", "core.fsmonitor", guion.as_posix(), cwd=pc["repo"])
        assert terminal.run_command("git", ["status"], str(pc["repo"]))["success"]
        assert not marca.exists()
        subprocess.run(["git", "status"], cwd=pc["repo"], capture_output=True)
        assert marca.exists()          # sin Morgan, sí corre

    def test_fuera_de_las_carpetas_no(self, pc, tmp_path):
        otro = tmp_path / "Otro"
        otro.mkdir()
        assert terminal.run_command("git", ["status"], str(otro))["motivo"] == "fuera"

    def test_dentro_de_git_no_se_cambia_nada(self, pc):
        r = terminal.run_change_command("git", ["add", "x"], str(pc["repo"] / ".git"))
        assert not r["success"] and r["motivo"] == "zona_prohibida"

    def test_un_programa_apagado(self, pc):
        politica = Politica.cargar()
        politica.programas = ["ping"]
        politica.guardar()
        r = terminal.run_command("git", ["status"], str(pc["repo"]))
        assert r["motivo"] == "programa_apagado" and "programa activar git" in r["error"]


@en_windows
class TestPlazosYSalida:
    def test_cancelar_un_ping_lo_para(self, pc):
        ej = Ejecutor("ag", capacidades.disponibles())

        async def todo():
            orden = {"tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r",
                     "command_id": "c1", "agent_id": "ag", "capability": "run_command",
                     "arguments": {"program": "ping", "args": ["localhost", "-n", "10"]},
                     "vence_en_ms": 30_000}
            tarea = asyncio.create_task(ej.procesar(orden))
            await asyncio.sleep(1)
            ej.cancelar("c1")
            return await tarea

        inicio = time.monotonic()
        r = asyncio.run(todo())
        assert r["estado"] == "CANCELLED" and time.monotonic() - inicio < 5

    def test_la_salida_se_corta(self):
        codigo = "import sys; sys.stdout.write('x' * 300000)"
        r = terminal._correr([sys.executable, "-c", codigo], os.getcwd(), 20, cancelable=True)
        assert r["cortada"] and len(r["salida"]) == terminal.MAX_SALIDA

    def test_un_programa_que_no_acaba_se_para(self):
        r = terminal._correr([sys.executable, "-c", "import time; time.sleep(30)"], os.getcwd(), 1, cancelable=True)
        assert r["vencido"] and r["segundos"] < 8

    def test_los_secretos_de_la_salida_se_tapan(self):
        from src.agente import salida

        resultado = {"success": True, "data": {"salida": "GROQ_API_KEY=gsk_1234567890abcdefghij1234567890"}}
        filtrado, notas = salida.filtrar("run_command", resultado)
        assert "gsk_1234567890" not in str(filtrado) and notas.get("secretos_ocultos")


# --- Procesos ---------------------------------------------------------------------------

@pytest.fixture
def hijo():
    proceso = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.5)
    yield proceso
    proceso.kill()


class TestProcesos:
    def test_la_lista_sin_lineas_de_ordenes(self, pc):
        r = terminal.get_processes(limit=10)
        assert r["success"] and len(r["data"]["procesos"]) <= 10
        assert all("cmdline" not in p for p in r["data"]["procesos"])

    def test_uno_con_detalle(self, pc, hijo):
        r = terminal.get_processes(pid=hijo.pid)
        assert r["data"]["pid"] == hijo.pid and r["data"]["tuyo"]

    def test_terminar_uno_tuyo(self, pc, hijo):
        import psutil

        nombre = psutil.Process(hijo.pid).name()
        r = terminal.kill_process(hijo.pid, nombre)
        assert r["success"], r
        assert hijo.wait(timeout=5) is not None
        assert r["data"]["comprobado"] == "el proceso ya no existe"
        assert "TERMINAR" in pc["preguntas"]["hechas"][-1][0]

    def test_el_nombre_tiene_que_coincidir(self, pc, hijo):
        r = terminal.kill_process(hijo.pid, "otro.exe")
        assert r["motivo"] == "no_coincide" and hijo.poll() is None

    def test_con_un_no_sigue_vivo(self, pc, hijo):
        import psutil

        pc["preguntas"]["respuesta"]["valor"] = "rechazada"
        r = terminal.kill_process(hijo.pid, psutil.Process(hijo.pid).name())
        assert not r["success"] and hijo.poll() is None

    def test_los_del_sistema_no(self, pc, hijo, monkeypatch):
        import psutil

        nombre = psutil.Process(hijo.pid).name()
        monkeypatch.setattr(terminal, "PROTEGIDOS", terminal.PROTEGIDOS | {nombre.lower()})
        assert terminal.kill_process(hijo.pid, nombre)["motivo"] == "protegido"
        assert hijo.poll() is None and pc["preguntas"]["hechas"] == []

    def test_morgan_no(self, pc):
        import psutil

        proceso = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)  # src.agente"])
        try:
            time.sleep(0.5)
            r = terminal.kill_process(proceso.pid, psutil.Process(proceso.pid).name())
            assert r["motivo"] == "protegido" and proceso.poll() is None
        finally:
            proceso.kill()

    def test_ni_el_propio_agente(self, pc):
        import psutil

        r = terminal.kill_process(os.getpid(), psutil.Process(os.getpid()).name())
        assert r["motivo"] == "protegido"

    def test_ni_los_de_otra_persona(self, pc, hijo, monkeypatch):
        import psutil

        monkeypatch.setattr(terminal, "_usuario", lambda: "otra-persona")
        assert terminal.kill_process(hijo.pid, psutil.Process(hijo.pid).name())["motivo"] == "ajeno"


# --- Lo que cambia en la escritura (decisión 3) -------------------------------------------

class TestMorganNoEscribeLoQueEjecuta:
    @pytest.mark.parametrize("nombre", ["script.py", "app.js", "tarea.mjs", "x.sh", "x.rb", ".gitconfig"])
    def test_scripts_y_configuracion(self, tmp_path, preguntas, nombre):
        politica = Politica()
        (tmp_path / "N").mkdir()
        politica.permitir_escritura(str(tmp_path / "N"))
        politica.capacidades["create_file"] = True
        politica.guardar()
        r = escritura.create_file(str(tmp_path / "N" / nombre), "x")
        assert not r["success"] and r["motivo"] == "ejecutable"

    def test_nada_dentro_de_git(self, tmp_path, preguntas):
        politica = Politica()
        (tmp_path / "N" / ".git" / "hooks").mkdir(parents=True)
        politica.permitir_escritura(str(tmp_path / "N"))
        politica.capacidades["create_file"] = True
        politica.guardar()
        r = escritura.create_file(str(tmp_path / "N" / ".git" / "hooks" / "pre-commit"), "x")
        assert not r["success"] and r["motivo"] == "zona_prohibida"


# --- Anunciar y la nube ---------------------------------------------------------------------

class TestAnunciarYLaNube:
    def test_nace_todo_apagado(self):
        politica = Politica()
        assert not any(politica.capacidades[c] for c in DE_EJECUCION) and politica.programas == []
        assert not set(DE_EJECUCION) & set(capacidades.disponibles(politica))

    def test_la_terminal_solo_con_algun_programa(self, pc):
        politica = Politica.cargar()
        assert "run_command" in capacidades.disponibles(politica)
        politica.programas = []
        assert "run_command" not in capacidades.disponibles(politica)
        assert "get_processes" in capacidades.disponibles(politica)

    def test_lo_que_cambia_exige_plan(self):
        from src.canal.herramientas import TerminalDelEquipo, herramientas_del_equipo

        t = {h.name: h for h in herramientas_del_equipo() if isinstance(h, TerminalDelEquipo)}
        assert set(t) == set(DE_EJECUCION)
        assert t["run_change_command"].exige_plan and t["kill_process"].exige_plan
        assert not t["run_command"].exige_plan and not t["get_processes"].exige_plan
        assert t["kill_process"].permission_level == "critical"

    def test_el_prompt_lo_explica_solo_si_se_puede(self):
        from src.agent.prompt import PUEDE_TERMINAL, _CITA, prompt_para

        assert PUEDE_TERMINAL in prompt_para({"read_file", "run_command"}, equipo_remoto=True)
        assert PUEDE_TERMINAL not in prompt_para({"read_file"}, equipo_remoto=True)
        assert _CITA.findall(PUEDE_TERMINAL) == []
