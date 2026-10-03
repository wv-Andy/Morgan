"""
Los cambios pre-aprobados (4.15): automatizaciones con **pasos fijos** que cambian algo,
aprobados una vez. Mis decisiones (2026-10-01): solo verdes y amarillos; `{fecha}` y
`{hora}` como único cambio entre ejecuciones; pasos fijos, sin que el modelo elija nada.

Lo que haría daño si fallase: que se ejecute algo **distinto** de lo aprobado (otros
argumentos, otra herramienta), que entre un paso **rojo** o uno que pide «Permitir», que el
modelo **elija** qué hacer o reintente por otro camino, o que la persona apruebe sin **ver**
los pasos enteros.
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from pathlib import Path

import pytest

from src.tools.base import RiskLevel, Tool, ToolCategory

from src.automatizacion.contexto import en_automatizacion, permitida
from src.automatizacion.pasos import MAX_PASOS, expandir, normalizar
from tests.test_automatizaciones import MADRID, _crear, _repo, web  # noqa: F401


def _paso(ruta, contenido="hecho"):
    return {"herramienta": "escribir_nota", "argumentos": {"path": str(ruta), "content": contenido},
            "descripcion": "Crear la nota"}


class EscribirNota(Tool):
    """Como las del PC: amarilla y **solo con un plan aprobado** (`exige_plan`). Escribe en
    la carpeta temporal de la prueba y nunca pisa."""

    name = "escribir_nota"
    description = "Escribe una nota nueva."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                                    "overwrite": {"type": "boolean"}},
                  "required": ["path", "content"]}
    permission_level = RiskLevel.MODERATE.value
    category = ToolCategory.FILESYSTEM.value
    requires_local = False

    @property
    def exige_plan(self) -> bool:
        return True

    def execute(self, path: str = "", content: str = "", overwrite: bool = False, **_):
        destino = Path(path)
        if destino.exists() and not overwrite:
            return {"success": False, "data": None, "error": f"Ya existe {destino.name}: no se pisa."}
        destino.write_text(content, encoding="utf-8")
        return {"success": True, "data": {"escrito": destino.name}, "error": None}


@pytest.fixture
def con_nota(web):
    web["container"].tool_registry.register(EscribirNota())
    return web


class TestFechaYHora:
    def test_se_sustituyen_en_su_zona(self):
        momento = datetime(2026, 10, 1, 23, 30, tzinfo=ZoneInfo("UTC")).timestamp()  # 01:30 en Madrid
        pasos = [{"herramienta": "x", "argumentos": {"dst": "C:/Copias/p-{fecha}_{hora}.zip", "n": 3,
                                                       "lista": ["{fecha}"]}}]
        (paso,) = expandir(pasos, momento, MADRID)
        assert paso["argumentos"] == {"dst": "C:/Copias/p-2026-10-02_01-30.zip", "n": 3, "lista": ["2026-10-02"]}

    def test_lo_demas_no_se_toca(self):
        assert expandir("{nombre} y {fecha_x}", 0, MADRID) == "{nombre} y {fecha_x}"

    @pytest.mark.parametrize("pasos", [None, [], [{"argumentos": {}}], [{"herramienta": "x", "argumentos": "y"}],
                                       [{"herramienta": "x"}] * (MAX_PASOS + 1)])
    def test_pasos_que_no_valen(self, pasos):
        assert normalizar(pasos)[1]


class TestSoloLoAprobado:
    def test_solo_una_llamada_identica(self):
        pasos = [{"herramienta": "create_file", "argumentos": {"path": "C:/a.txt", "content": "x"}}]
        with en_automatizacion({"id": "a", "_pasos": pasos}):
            assert permitida("create_file", {"path": "C:/a.txt", "content": "x"})
            assert not permitida("create_file", {"path": "C:/b.txt", "content": "x"}), "otros argumentos"
            assert not permitida("create_file", {"path": "C:/a.txt", "content": "x", "overwrite": True})
            assert not permitida("delete_file", {"path": "C:/a.txt"}), "otra herramienta"
            assert not permitida("search_web", {"query": "x"}), "ni consultas: el modelo solo redacta"
            assert not permitida("create_file"), "al armar el catálogo, ninguna"


class TestCrearlas:
    @pytest.fixture
    def crear(self, con_nota):
        from src.identidad import como_usuario

        web = con_nota
        _, ana = web["cuenta"]("ana")
        h = web["container"].tool_registry.get("create_automation")

        def prevalidar(pasos):
            with como_usuario(ana):
                return h.prevalidar({"horario": {"tipo": "diaria", "hora": "23:00"}, "pasos": pasos})
        return prevalidar

    def test_un_paso_amarillo_vale(self, crear, tmp_path):
        assert crear([_paso(tmp_path / "nota-{fecha}.txt")]) is None

    @pytest.mark.parametrize("herramienta, argumentos, por", [
        ("delete_file", {"path": "C:/x"}, "rojo"), ("kill_process", {"pid": 1}, "rojo"),
        ("execute_command", {"command": "dir"}, "rojo"),
        ("read_file", {"path": "C:/x"}, "consulta"), ("search_web", {"query": "x"}, "consulta"),
        ("remember_fact", {"key": "k", "value": "v"}, "memoria"),
        ("create_plan", {"objetivo": "x", "pasos": []}, "memoria"),
        ("create_automation", {"nombre": "x", "horario": {}}, "memoria"),
        ("no_existe", {}, "no existe")])
    def test_lo_que_no_va(self, crear, herramienta, argumentos, por):
        """Cada uno con argumentos que valen: lo rechaza SU comprobación, no otra."""
        motivo = crear([{"herramienta": herramienta, "argumentos": argumentos}])
        assert motivo and motivo.startswith("Paso 1") and por in motivo, motivo

    @pytest.mark.parametrize("contenido", ["{fecha_actual} revisado", "copia {date}", "{FECHA}", "{{fecha}} revisado"])
    def test_una_marca_que_no_se_sustituye(self, crear, tmp_path, contenido):
        """Medido con el modelo real: `{fecha_actual}` llenó el registro de «{fecha_actual} revisado»."""
        motivo = crear([_paso(tmp_path / "r.txt", contenido)])
        assert motivo and "solo {fecha}" in motivo

    @pytest.mark.parametrize("contenido", ['{"dia": "{fecha}", "ok": 1}', '{"ok": 1}', "{1}", "{ x }"])
    def test_un_json_no_es_una_marca(self, crear, tmp_path, contenido):
        assert crear([_paso(tmp_path / "r.txt", contenido)]) is None

    def test_argumentos_que_faltan_o_sobran(self, crear, tmp_path):
        assert "content" in crear([{"herramienta": "escribir_nota", "argumentos": {"path": str(tmp_path / "a")}}])
        motivo = crear([{"herramienta": "escribir_nota",
                         "argumentos": {"path": str(tmp_path / "a"), "content": "x", "contenido": "y"}}])
        assert motivo and "contenido" in motivo

    def test_ni_lo_que_pide_permitir(self, web):
        from src.tools.automatizaciones import PIDEN_PERMITIR

        assert {"clipboard", "screenshot"} <= PIDEN_PERMITIR

    def test_el_paso_se_prevalida_con_su_fecha_puesta(self, crear, tmp_path, con_nota, monkeypatch):
        visto = []
        h = con_nota["container"].tool_registry.get("escribir_nota")
        monkeypatch.setattr(h, "prevalidar", lambda a: visto.append(a) or None, raising=False)
        crear([_paso(tmp_path / "nota-{fecha}.txt")])
        assert "{fecha}" not in visto[0]["path"] and time.strftime("%Y") in visto[0]["path"]

    def test_con_pasos_la_instruccion_no_hace_falta(self, crear, tmp_path):
        assert crear([_paso(tmp_path / "a.txt")]) is None

    def test_una_instruccion_que_cambia_algo_apunta_a_los_pasos(self, web):
        from src.identidad import como_usuario

        _, ana = web["cuenta"]("ana")
        h = web["container"].tool_registry.get("create_automation")
        with como_usuario(ana):
            motivo = h.prevalidar({"horario": {"tipo": "diaria", "hora": "09:00"}, "instruccion": "Borra lo viejo"})
        assert "pasos fijos" in motivo

    def test_un_paso_del_pc_la_marca_como_que_necesita_el_pc(self, web):
        from src.canal.herramientas import EscribirEnElEquipo
        from src.identidad import como_usuario
        from src.tools.registry import ToolRegistry

        _, ana = web["cuenta"]("ana")
        h = web["container"].tool_registry.get("create_automation")
        registro = ToolRegistry()
        registro.register(EscribirEnElEquipo("append_file"))
        h.registro = registro
        with como_usuario(ana):
            r = h.execute(nombre="Registro", horario={"tipo": "diaria", "hora": "09:00"}, necesita_pc=False,
                          pasos=[{"herramienta": "append_file", "argumentos": {"path": "C:/r.txt", "content": "x"}}])
        assert r["success"], r
        assert _repo(web["container"]).obtener(ana, r["data"]["id"])["necesita_pc"] is True

    def test_al_aprobar_se_ven_los_pasos_enteros(self):
        """Recortados a 120 caracteres todos juntos, la persona no veía qué aprobaba."""
        from src.tasks.plan import _resumir

        pasos = [{"herramienta": "compress", "argumentos": {"accion": "comprimir", "paths": ["C:/Proyectos"],
                                                            "dst": "C:/Copias/proyectos-{fecha}.zip"}},
                 {"herramienta": "append_file", "argumentos": {"path": "C:/Copias/registro.txt",
                                                               "content": "copia del {fecha}"}}]
        texto = _resumir({"pasos": pasos})["pasos"]
        assert texto.splitlines() == [
            "1. compress: accion=comprimir; paths=['C:/Proyectos']; dst=C:/Copias/proyectos-{fecha}.zip",
            "2. append_file: path=C:/Copias/registro.txt; content=copia del {fecha}"]


class TestEjecutarlas:
    def test_hace_exactamente_los_pasos_sin_llamar_al_modelo(self, con_nota, tmp_path):
        web = con_nota
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        _crear(c, ana, proxima=time.time() - 5, pasos=[_paso(tmp_path / "nota-{fecha}.txt", "copia")])
        antes = modelo.call_count
        assert c.reloj.tic() == 1
        hoy = datetime.now(ZoneInfo(MADRID)).strftime("%Y-%m-%d")
        assert (tmp_path / f"nota-{hoy}.txt").read_text(encoding="utf-8") == "copia"
        aviso = _repo(c).avisos(ana)[0]
        assert aviso["estado"] == "hecha" and aviso["herramientas"] == ["escribir_nota"]
        assert modelo.call_count == antes, "si todo sale, ni se llama al modelo"
        from src.identidad import como_usuario

        with como_usuario(ana):
            assert c.planificador.listar() == [], "el plan de la ejecución no se queda"

    def test_si_falla_el_aviso_es_el_informe_sin_modelo(self, con_nota, tmp_path):
        """Medido con el modelo real: al redactar el aviso se inventó lo que no había pasado."""
        web = con_nota
        _, ana = web["cuenta"]("ana")
        c, modelo = web["container"], web["modelo"]
        (tmp_path / "ya.txt").write_text("de antes", encoding="utf-8")
        _crear(c, ana, proxima=time.time() - 5, pasos=[_paso(tmp_path / "ya.txt"), _paso(tmp_path / "otra.txt")])
        modelo.queue_tool_call("escribir_nota", {"path": str(tmp_path / "ya.txt"), "content": "pisado",
                                               "overwrite": True})
        modelo.queue_text("No salió: la nota ya existía.")
        antes = modelo.call_count
        c.reloj.tic()
        assert modelo.call_count == antes, "ni para redactar"
        assert (tmp_path / "ya.txt").read_text(encoding="utf-8") == "de antes", "no pisa por otro camino"
        assert not (tmp_path / "otra.txt").exists(), "se para en el primer fallo"
        aviso = _repo(c).avisos(ana)[0]
        assert aviso["estado"] == "fallo" and "Ya existe ya.txt: no se pisa." in aviso["texto"]
        assert "NO se ejecutó" in aviso["texto"], "y dice qué no se hizo"
        assert "UN intento" not in aviso["texto"], "lo que es para el modelo no llega a la persona"

    def test_el_modelo_no_puede_cambiar_los_argumentos(self, con_nota, tmp_path):
        """Aunque pida la misma herramienta: solo pasa la llamada idéntica a lo aprobado."""
        from src.identidad import como_usuario

        web = con_nota
        _, ana = web["cuenta"]("ana")
        c = web["container"]
        pasos = [_paso(tmp_path / "a.txt")]
        with como_usuario(ana), en_automatizacion({"id": "x", "_pasos": pasos}):
            r = c.agent._ejecutar_una(c.agent.sessions.get("s"), "escribir_nota",
                                      {"path": str(tmp_path / "b.txt"), "content": "x"}, _estado())
        assert not r["success"] and "solo consulta" in r["error"] and not (tmp_path / "b.txt").exists()


def _estado():
    from src.agent.core import _EstadoDelTurno

    return _EstadoDelTurno()
