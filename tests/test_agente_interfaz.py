"""
El control de la interfaz (4.12): `ui_read` (🟢) y `ui_control` (🔴, plan y «Permitir» al
empezar el plan). Sobre una ventana de Windows **propia de las pruebas**
(`tests/ventana_de_prueba.py`), nunca sobre las aplicaciones de quien las pasa.

Lo que haría daño si fallase: escribir en una contraseña, manejar una consola o el
Explorador, pulsar la tecla Windows (Win+R abre «Ejecutar»), seguir escribiendo cuando otra
ventana se ha puesto delante, o hacerlo sin «Permitir».
"""

import sys
import time

import pytest

from src.agente import aviso, interfaz
from src.agente.politica import DE_INTERFAZ, DE_PANTALLA, Politica

pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="la interfaz de Windows"),
              pytest.mark.escritorio]


@pytest.fixture
def pc(monkeypatch):
    from tests.ventana_de_prueba import VentanaDePrueba

    politica = Politica()
    for c in (*DE_INTERFAZ, *DE_PANTALLA):
        politica.capacidades[c] = True
    politica.guardar()
    # La ventana de prueba es de python.exe, que está prohibido: aquí se permite.
    monkeypatch.setattr(interfaz, "PROGRAMAS_PROHIBIDOS", interfaz.PROGRAMAS_PROHIBIDOS - {"python.exe"})
    monkeypatch.setattr(interfaz, "_PERMITIDOS", {})
    preguntas, respuesta = [], {"valor": aviso.PERMITIDA}
    monkeypatch.setattr(aviso, "preguntar",
                        lambda t, d, espera=aviso.ESPERA, **k: preguntas.append((t, d)) or respuesta["valor"])
    with VentanaDePrueba() as ventana:
        time.sleep(0.4)
        yield {"ventana": ventana, "preguntas": preguntas, "respuesta": respuesta}


from tests.ventana_de_prueba import TITULO  # noqa: E402


class TestNacen:
    def test_apagadas(self):
        politica = Politica()
        assert not politica.capacidades["ui_read"] and not politica.capacidades["ui_control"]
        politica.guardar()
        assert interfaz.ui_read(titulo="x")["motivo"] == "capacidad_apagada"
        assert interfaz.ui_control("clic", titulo="x", elemento="y")["motivo"] == "capacidad_apagada"


class TestLeer:
    def test_los_controles_con_su_tipo(self, pc):
        controles = interfaz.ui_read(titulo=TITULO)["data"]["controles"]
        assert {"tipo": "boton", "nombre": "Guardar", "activo": True, "contrasena": False} in controles
        assert sum(1 for c in controles if c["tipo"] == "campo" and c["contrasena"]) == 1
        assert all("valor" not in c for c in controles), "nunca el contenido"

    def test_una_consola_o_el_explorador_no(self, pc, monkeypatch):
        monkeypatch.setattr(interfaz, "PROGRAMAS_PROHIBIDOS", interfaz.PROGRAMAS_PROHIBIDOS | {"python.exe"})
        assert interfaz.ui_read(titulo=TITULO)["motivo"] == "prohibida"


class TestManejar:
    def test_escribir_y_comprobarlo(self, pc):
        r = interfaz.ui_control("escribir", titulo=TITULO, elemento="", tipo="campo", texto="hola ñ", origen="p1#1")
        assert r["success"] and r["data"]["comprobado"] == "el campo tiene ese texto"
        assert pc["ventana"].texto() == "hola ñ" and pc["ventana"].texto(pc["ventana"].clave) == ""

    def test_en_una_contrasena_nunca(self, pc, monkeypatch):
        original = interfaz._uno
        monkeypatch.setattr(interfaz, "_uno", lambda c, n, t, sin_contrasenas=False: original(
            [x for x in c if x["contrasena"]], n, t, False))
        r = interfaz.ui_control("escribir", titulo=TITULO, elemento="", tipo="campo", texto="clave", origen="p1#1")
        assert not r["success"] and r["motivo"] == "contrasena"
        assert pc["ventana"].texto(pc["ventana"].clave) == "" and not pc["preguntas"]

    def test_clic_en_un_boton(self, pc):
        assert interfaz.ui_control("clic", titulo=TITULO, elemento="Guardar", origen="p1#1")["success"]
        assert pc["ventana"].pulsado.wait(2)

    @pytest.mark.parametrize("teclas", ["win+r", "alt+f4", "ctrl+alt+supr", "win", "ctrl+shift+esc", "f12"])
    def test_teclas_fuera_de_la_lista(self, pc, teclas):
        r = interfaz.ui_control("teclas", titulo=TITULO, teclas=teclas, origen="p1#1")
        assert not r["success"] and r["motivo"] == "tecla_prohibida" and not pc["preguntas"]

    def test_teclas_de_la_lista(self, pc):
        r = interfaz.ui_control("teclas", titulo=TITULO, teclas="Ctrl+A", origen="p1#1")
        if r.get("motivo") == "no_delante":
            # Medido en mi PC: con el Administrador de tareas (elevado) delante,
            # Windows no deja a un programa normal quitarle el primer plano. Es lo correcto.
            pytest.skip("Windows no deja traer la ventana delante ahora (hay una elevada delante)")
        assert r["success"], r

    def test_un_elemento_que_no_esta(self, pc):
        assert interfaz.ui_control("clic", titulo=TITULO, elemento="Borrar todo", origen="p1#1")["motivo"] == "no_existe"


class TestPermitir:
    def test_una_vez_por_plan(self, pc):
        interfaz.ui_control("teclas", titulo=TITULO, teclas="ctrl+a", origen="plan-a#1")
        interfaz.ui_control("clic", titulo=TITULO, elemento="Guardar", origen="plan-a#2")
        assert len(pc["preguntas"]) == 1 and "todo este plan" in pc["preguntas"][0][1]
        interfaz.ui_control("teclas", titulo=TITULO, teclas="ctrl+a", origen="plan-b#1")
        assert len(pc["preguntas"]) == 2

    def test_sin_plan_cada_vez(self, pc):
        interfaz.ui_control("teclas", titulo=TITULO, teclas="ctrl+a")
        interfaz.ui_control("teclas", titulo=TITULO, teclas="ctrl+a")
        assert len(pc["preguntas"]) == 2

    def test_con_un_no_no_se_hace_nada(self, pc):
        pc["respuesta"]["valor"] = "rechazada"
        r = interfaz.ui_control("clic", titulo=TITULO, elemento="Guardar", origen="plan-c#1")
        assert not r["success"] and r["motivo"] == "no_confirmada"
        assert not pc["ventana"].pulsado.wait(0.5)
        r = interfaz.ui_control("escribir", titulo=TITULO, elemento="", tipo="campo", texto="x", origen="plan-c#2")
        assert not r["success"] and pc["ventana"].texto() == "", "un no no deja el plan permitido"


def test_si_otra_ventana_se_cruza_se_para():
    with pytest.raises(interfaz._Cruzada):
        interfaz._escribir_teclas("hola", hwnd=1)                   # no es la de primer plano


class TestEnLaNube:
    def test_leer_verde_y_manejar_rojo_con_plan(self):
        from src.canal.herramientas import InterfazDelEquipo, herramientas_del_equipo

        h = {x.name: x for x in herramientas_del_equipo() if isinstance(x, InterfazDelEquipo)}
        assert h["ui_read"].permission_level == "safe" and not h["ui_read"].exige_plan
        assert h["ui_control"].permission_level == "high_risk" and h["ui_control"].exige_plan

    def test_leer_es_una_consulta_y_del_pc(self):
        from src.agent import fuga
        from src.tools.planificacion import CONSULTAS

        assert "ui_read" in CONSULTAS and "ui_read" in fuga.DEL_PC


class TestLoImposibleNiSePropone:
    """Medido con el modelo real (4.12): propuso planes para pulsar Win+R y escribir en una
    contraseña; el PC los habría rechazado, pero la persona aprobaba algo que no podía salir."""

    @pytest.fixture
    def nube(self):
        from src.canal.herramientas import InterfazDelEquipo

        return InterfazDelEquipo("ui_control")

    @pytest.mark.parametrize("argumentos", [{"accion": "teclas", "teclas": "win+r"},
                                            {"accion": "escribir", "elemento": "Contraseña", "texto": "1234"},
                                            {"accion": "escribir", "elemento": "Password", "texto": "x"}])
    def test_la_nube_lo_sabe(self, nube, argumentos):
        assert nube.prevalidar(argumentos)

    @pytest.mark.parametrize("argumentos", [{"accion": "teclas", "teclas": "Ctrl+S"},
                                            {"accion": "escribir", "elemento": "Buscar", "texto": "x"},
                                            {"accion": "clic", "elemento": "Guardar"}])
    def test_lo_posible_pasa(self, nube, argumentos):
        assert nube.prevalidar(argumentos) is None

    def test_el_plan_no_se_guarda(self, tmp_path, nube):
        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.tasks.planificador import Planificador
        from src.tools.planificacion import plan_tools
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(nube)
        planificador = Planificador(SQLiteRepositoryFactory(Database(tmp_path / "p.db")).planes, registro)
        crear = next(h for h in plan_tools(planificador) if h.name == "create_plan")
        r = crear.execute(objetivo="abrir ejecutar", pasos=[
            {"descripcion": "Win+R", "herramienta": "ui_control", "argumentos": {"accion": "teclas", "teclas": "win+r"}}],
            session_id="s")
        assert not r["success"] and "tecla Windows" in r["error"]
        assert planificador.listar(session_id="s") == []

    def test_sin_plan_se_dice_que_no_se_puede_y_no_que_falta_plan(self, tmp_path, nube):
        from src.agent.core import Agent
        from src.models.mock import MockLLMProvider
        from src.security.permissions import PermissionManager
        from src.tools.registry import ToolRegistry

        registro = ToolRegistry()
        registro.register(nube)
        modelo = MockLLMProvider()
        agente = Agent(model=modelo, tool_registry=registro, permission_manager=PermissionManager(interactive=False))
        nube.disponible = lambda: True
        modelo.queue_tool_call("ui_control", {"accion": "teclas", "teclas": "win+r"})
        modelo.queue_text("No puedo.")
        agente.chat("pulsa win+r", session_id="s")
        resultado = [m for m in agente.sessions.get("s").messages if m.role == "tool"][0].tool_result
        assert "tecla Windows" in resultado["error"] and "create_plan" not in resultado["error"]


def test_si_el_campo_no_se_queda_con_el_texto_no_lo_da_por_hecho(pc, monkeypatch):
    original = interfaz._texto
    monkeypatch.setattr(interfaz, "_texto", lambda obj, i: "otra cosa" if i == 4 else original(obj, i))
    r = interfaz.ui_control("escribir", titulo=TITULO, elemento="", tipo="campo", texto="hola", origen="p9#1")
    assert not (r["success"] and r["data"]["comprobado"] == "el campo tiene ese texto")
