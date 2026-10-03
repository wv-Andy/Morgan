"""
Varios agentes por persona y observabilidad (3.7).

`User A ├── Agent A1, Agent A2` y `User B └── Agent B1`, con agentes reales y la nube
real. Gate del plan: **los agentes coexisten sin mezclar peticiones, capacidades,
estado, auditoría ni credenciales**. Mis decisiones (2026-09-25): con varios
conectados, **cada orden dice el equipo y, si no está claro, se pregunta** (nunca se
adivina); el historial de órdenes, **30 días y sin argumentos ni contenido**.
"""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.agente.ejecutor import Ejecutor
from src.canal import despacho
from src.canal.registro import REGISTRO
from src.identidad.contexto import como_usuario
from tests.test_canal_agente import _cuenta, _emparejar, _esperar, nube, servidor  # noqa: F401


def _agente(servidor, agent_id, credencial, capacidades):
    from src.agente.canal import Canal

    canal = Canal(servidor, credencial, Ejecutor(agent_id, capacidades), espera_minima=0.1)
    hilo = threading.Thread(target=lambda: asyncio.run(canal.correr()), daemon=True)
    hilo.start()
    return canal, hilo


@pytest.fixture
def tres(nube, servidor, monkeypatch):
    """Ana con Portátil y Sobremesa; Bea con el suyo. Cada uno apunta lo que hace."""
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 3.0)
    web_ana, csrf_ana, ana, portatil_id, portatil_cred = _cuenta(nube, "ana")
    sobremesa_id, sobremesa_cred = _emparejar(nube, web_ana, csrf_ana, "Sobremesa")
    web_bea, csrf_bea, bea, bea_id, bea_cred = _cuenta(nube, "bea")
    hechas = {"Portátil": [], "Sobremesa": [], "Bea": []}
    agentes = []
    for nombre, agent_id, cred, extra in (("Portátil", portatil_id, portatil_cred, True),
                                          ("Sobremesa", sobremesa_id, sobremesa_cred, False),
                                          ("Bea", bea_id, bea_cred, True)):
        def apunta(marca: str, segundos: float = 0.0, _n=nombre):
            time.sleep(segundos)
            hechas[_n].append(marca)
            return {"success": True, "data": {"marca": marca, "equipo": _n}, "error": None}
        def larga(marca: str, _n=nombre):
            from src.agente import control

            for _ in range(60):
                control.punto_seguro()
                time.sleep(0.05)
            hechas[_n].append(marca)
            return {"success": True, "data": {"marca": marca, "equipo": _n}, "error": None}

        caps = {"apunta": apunta, "larga": larga}
        if extra:
            caps["solo_algunos"] = lambda **k: {"success": True, "data": {}, "error": None}
        if nombre == "Sobremesa":
            caps["solo_sobremesa"] = lambda **k: {"success": True, "data": {}, "error": None}
        agentes.append(_agente(servidor, agent_id, cred, caps))
    assert _esperar(lambda: len(REGISTRO.todas(ana)) == 2 and len(REGISTRO.todas(bea)) == 1, 20)
    yield {"nube": nube, "ana": ana, "bea": bea, "web_ana": web_ana, "csrf_ana": csrf_ana,
           "web_bea": web_bea, "hechas": hechas, "ids": {"Portátil": portatil_id,
           "Sobremesa": sobremesa_id, "Bea": bea_id}, "creds": {"Portátil": portatil_cred}}
    for canal, hilo in agentes:
        canal.parar()
        hilo.join(10)


def _enviar(user_id, capability, arguments, equipo=None, plazo=10.0):
    with como_usuario(user_id):
        return despacho.enviar(capability, arguments, plazo=plazo, equipo=equipo)


class TestElegirElEquipo:
    def test_por_nombre_va_a_ese_y_solo_a_ese(self, tres):
        r = _enviar(tres["ana"], "apunta", {"marca": "a"}, equipo="Sobremesa")
        assert r["resultado"]["data"]["equipo"] == "Sobremesa"
        assert tres["hechas"] == {"Portátil": [], "Sobremesa": ["a"], "Bea": []}

    def test_el_nombre_sin_mayusculas_ni_espacios(self, tres):
        r = _enviar(tres["ana"], "apunta", {"marca": "b"}, equipo="  portátil ")
        assert r["resultado"]["data"]["equipo"] == "Portátil"

    def test_con_varios_y_sin_decir_cual_no_se_adivina(self, tres):
        with pytest.raises(despacho.EquipoAmbiguo) as exc:
            _enviar(tres["ana"], "apunta", {"marca": "c"})
        assert "Portátil" in str(exc.value) and "Sobremesa" in str(exc.value)
        assert tres["hechas"]["Portátil"] == tres["hechas"]["Sobremesa"] == []

    def test_un_corte_de_uno_no_convierte_al_otro_en_el_unico(self, tres):
        """3.7.5: con el Sobremesa cortado unos segundos, una orden sin decir el equipo iba
        al Portátil. Para Ana sigue teniendo dos: se pregunta."""
        sobremesa = REGISTRO.de(tres["ana"], tres["ids"]["Sobremesa"])
        asyncio.run_coroutine_threadsafe(REGISTRO.quitar(sobremesa, 1012, "corte"), REGISTRO.loop).result(5)
        with pytest.raises(despacho.EquipoAmbiguo) as exc:
            _enviar(tres["ana"], "apunta", {"marca": "h"})
        assert "Sobremesa" in str(exc.value)
        assert tres["hechas"]["Portátil"] == tres["hechas"]["Sobremesa"] == []

    def test_el_que_vuelve_no_cuenta_dos_veces(self, tres):
        """Bea tiene uno: tras un corte y su vuelta, sigue siendo el único."""
        suyo = REGISTRO.de(tres["bea"])
        asyncio.run_coroutine_threadsafe(REGISTRO.quitar(suyo, 1012, "corte"), REGISTRO.loop).result(5)
        assert _esperar(lambda: REGISTRO.de(tres["bea"]) not in (None, suyo), 10)
        assert _enviar(tres["bea"], "apunta", {"marca": "j"})["estado"] == "COMPLETED"

    def test_el_cortado_cuenta_solo_un_rato(self):
        from src.canal.registro import CERRADO_CREDENCIAL, Conexion, RegistroDeConexiones

        async def probar():
            async def nada(*a):
                return None
            registro = RegistroDeConexiones()
            for pc in ("a1", "a2"):
                c = Conexion("u", pc, pc.upper(), frozenset(), nada, nada, asyncio.get_running_loop())
                await registro.registrar(c)
            await registro.quitar(registro.de("u", "a1"), 1012, "corte")
            await registro.quitar(registro.de("u", "a2"), CERRADO_CREDENCIAL, "revocado")
            return registro.recientes("u", 60), registro.recientes("u", 0)
        assert asyncio.run(probar()) == (["A1"], [])

    def test_uno_revocado_si_deja_al_otro_como_el_unico(self, tres):
        r = tres["web_ana"].delete(f"/auth/agentes/{tres['ids']['Sobremesa']}",
                                   headers={"x-morgan-csrf": tres["csrf_ana"]})
        assert r.status_code == 200
        assert _enviar(tres["ana"], "apunta", {"marca": "i"})["estado"] == "COMPLETED"
        assert tres["hechas"]["Portátil"] == ["i"]

    def test_con_uno_no_hace_falta_decirlo(self, tres):
        assert _enviar(tres["bea"], "apunta", {"marca": "d"})["estado"] == "COMPLETED"
        assert tres["hechas"]["Bea"] == ["d"]

    def test_un_equipo_que_no_esta(self, tres):
        with pytest.raises(despacho.EquipoNoConectado) as exc:
            _enviar(tres["ana"], "apunta", {"marca": "e"}, equipo="Tablet", plazo=2)
        assert "Portátil" in str(exc.value)

    def test_el_equipo_de_otra_persona_no_existe_para_ti(self, tres):
        """El nombre se busca solo entre los PC de quien pregunta."""
        with pytest.raises(despacho.EquipoNoConectado):
            _enviar(tres["bea"], "apunta", {"marca": "f"}, equipo="Sobremesa", plazo=2)
        assert tres["hechas"]["Sobremesa"] == []

    def test_ni_por_su_agent_id(self, tres):
        with pytest.raises(despacho.EquipoNoConectado):
            _enviar(tres["bea"], "apunta", {"marca": "g"}, equipo=tres["ids"]["Portátil"], plazo=2)

    def test_una_capacidad_que_ese_equipo_no_ofrece(self, tres):
        with pytest.raises(despacho.EquipoSinCapacidad):
            _enviar(tres["ana"], "solo_algunos", {}, equipo="Sobremesa")


class TestCadaUnoLoSuyo:
    def test_tras_un_corte_la_orden_se_busca_en_el_mismo_pc(self, tres, monkeypatch):
        """Con dos, «el agente de la persona» podría ser el otro."""
        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 10.0)
        resultado = {}
        hilo = threading.Thread(target=lambda: resultado.update(
            r=_enviar(tres["ana"], "apunta", {"marca": "corte", "segundos": 1.0}, equipo="Portátil", plazo=20)))
        hilo.start()
        time.sleep(0.4)
        portatil = REGISTRO.de(tres["ana"], tres["ids"]["Portátil"])
        asyncio.run_coroutine_threadsafe(REGISTRO.quitar(portatil, 1012, "corte"), REGISTRO.loop).result(5)
        hilo.join(25)
        assert resultado["r"]["resultado"]["data"]["equipo"] == "Portátil"
        assert tres["hechas"]["Portátil"] == ["corte"] and tres["hechas"]["Sobremesa"] == []

    def test_revocar_uno_solo_cierra_ese(self, tres):
        r = tres["web_ana"].delete(f"/auth/agentes/{tres['ids']['Sobremesa']}",
                                   headers={"x-morgan-csrf": tres["csrf_ana"]})
        assert r.status_code == 200
        assert _esperar(lambda: [c.nombre for c in REGISTRO.todas(tres["ana"])] == ["Portátil"], 10)
        assert len(REGISTRO.todas(tres["bea"])) == 1

    def test_detener_cancela_en_el_pc_que_la_tiene(self, tres):
        from src.canal import paradas

        parada, resultado = threading.Event(), {}

        def turno():
            marca = paradas.PARADA.set(parada)
            try:
                resultado["r"] = _enviar(tres["ana"], "larga", {"marca": "larga"}, equipo="Sobremesa")
            except Exception as exc:
                resultado["r"] = exc
            finally:
                paradas.PARADA.reset(marca)

        hilo = threading.Thread(target=turno)
        hilo.start()
        time.sleep(0.5)
        sobremesa = REGISTRO.de(tres["ana"], tres["ids"]["Sobremesa"])
        assert any(True for _ in sobremesa.esperando)
        parada.set()
        hilo.join(15)
        # Si la cancelación fuera al otro PC, esta acabaría COMPLETED.
        assert resultado["r"]["estado"] == "CANCELLED"
        assert tres["hechas"] == {"Portátil": [], "Sobremesa": [], "Bea": []}


class TestLasHerramientasYElPrompt:
    def test_el_argumento_equipo_solo_con_varios(self, tres):
        from src.canal.herramientas import HerramientaDelEquipo
        from src.tools.filesystem import ReadFileTool

        herramienta = HerramientaDelEquipo(ReadFileTool())
        with como_usuario(tres["ana"]):
            assert "equipo" in herramienta.parameters["properties"]
        with como_usuario(tres["bea"]):
            assert "equipo" not in herramienta.parameters["properties"]

    def test_disponible_si_alguno_la_ofrece(self, tres):
        """La ofrece solo el segundo PC: mirar solo el primero diría que no."""
        from src.canal.herramientas import HerramientaDelEquipo

        class Local:
            name = "solo_sobremesa"
            description = parameters = permission_level = category = None

        with como_usuario(tres["ana"]):
            assert HerramientaDelEquipo(Local()).disponible()
        with como_usuario(tres["bea"]):
            assert not HerramientaDelEquipo(Local()).disponible()

    def test_el_prompt_nombra_los_equipos_solo_con_varios(self):
        from src.agent.prompt import _CITA, prompt_para, varios_equipos

        con = prompt_para({"read_file"}, equipo_remoto=True, equipos=["Portátil", "Sobremesa"])
        sin = prompt_para({"read_file"}, equipo_remoto=True, equipos=["Portátil"])
        assert "«Sobremesa»" in con and "Varios equipos" not in sin
        assert _CITA.findall(varios_equipos(["a", "b"])) == []


class TestElHistorial:
    def _repo(self, tres):
        from src.api.dependencies import get_container
        from src.identidad.repositorio import repositorio_de_cuentas

        return repositorio_de_cuentas(get_container().repositories)

    def test_cada_orden_queda_con_su_pc_y_sin_argumentos(self, tres):
        _enviar(tres["ana"], "apunta", {"marca": "SECRETO-EN-ARGUMENTOS"}, equipo="Portátil")
        _enviar(tres["ana"], "apunta", {"marca": "x"}, equipo="Sobremesa")
        repo = self._repo(tres)
        # Se escriben en otro hilo, después de contestar: se espera a las dos.
        assert _esperar(lambda: repo.ordenes_de_agente(tres["ana"], tres["ids"]["Portátil"], 0, 50)
                        and repo.ordenes_de_agente(tres["ana"], tres["ids"]["Sobremesa"], 0, 50), 5)
        portatil = repo.ordenes_de_agente(tres["ana"], tres["ids"]["Portátil"], 0, 50)
        sobremesa = repo.ordenes_de_agente(tres["ana"], tres["ids"]["Sobremesa"], 0, 50)
        assert len(portatil) == 1 and len(sobremesa) == 1
        assert portatil[0]["estado"] == "completed" and portatil[0]["capability"] == "apunta"
        assert "SECRETO" not in str(portatil)

    def test_la_web_solo_ve_el_de_sus_equipos(self, tres):
        _enviar(tres["ana"], "apunta", {"marca": "y"}, equipo="Portátil")
        time.sleep(0.5)
        r = tres["web_ana"].get(f"/auth/agentes/{tres['ids']['Portátil']}/ordenes")
        assert r.status_code == 200 and len(r.json()["ordenes"]) == 1
        assert tres["web_bea"].get(f"/auth/agentes/{tres['ids']['Portátil']}/ordenes").status_code == 404

    def test_el_agente_solo_ve_el_suyo(self, tres):
        _enviar(tres["ana"], "apunta", {"marca": "z"}, equipo="Sobremesa")
        _enviar(tres["ana"], "apunta", {"marca": "w"}, equipo="Portátil")
        time.sleep(0.5)
        r = TestClient(tres["nube"]).get("/agente/historial",
                                         headers={"Authorization": f"Bearer {tres['creds']['Portátil']}"})
        assert r.status_code == 200
        assert {o["agent_id"] for o in r.json()["ordenes"]} == {tres["ids"]["Portátil"]}
        assert TestClient(tres["nube"]).get("/agente/historial",
                                            headers={"Authorization": "Bearer mga_falsa"}).status_code == 401

    def test_la_lista_dice_quien_esta_conectado(self, tres):
        _emparejar(tres["nube"], tres["web_ana"], tres["csrf_ana"], "Tablet")    # emparejado, sin conectar
        agentes = tres["web_ana"].get("/auth/agentes").json()["agentes"]
        assert {a["nombre"]: a["conectado"] for a in agentes} == {"Portátil": True, "Sobremesa": True, "Tablet": False}
        assert next(a for a in agentes if a["nombre"] == "Tablet")["capacidades"] == []
        assert "apunta" in next(a for a in agentes if a["nombre"] == "Sobremesa")["capacidades"]

    def test_lo_de_mas_de_30_dias_se_borra(self, tres):
        repo = self._repo(tres)
        viejo = time.time() - 31 * 86400
        repo.anotar_orden({"command_id": "vieja", "user_id": tres["ana"], "agent_id": tres["ids"]["Portátil"],
                           "capability": "apunta", "estado": "completed", "creado_en": viejo})
        repo.limpiar(time.time())
        assert not any(o["command_id"] == "vieja"
                       for o in repo.ordenes_de_agente(tres["ana"], tres["ids"]["Portátil"], 0, 50))


class TestCruzar:
    def test_cuadran_y_no_cuadran(self):
        from src.agente.cruzar import comparar

        nube = [{"command_id": "a", "estado": "completed", "capability": "x"},
                {"command_id": "b", "estado": "completed", "capability": "x"},      # el PC no la tiene
                {"command_id": "c", "estado": "completed", "capability": "x"},      # recuperada como c-r
                {"command_id": "d", "estado": "failed", "capability": "x"},         # el PC dice otra cosa
                {"command_id": "e", "estado": "no_conectado", "capability": "x"}]   # nunca llegó
        diario = {"a": {"estado": "COMPLETED"}, "c-r": {"estado": "COMPLETED"},
                  "d": {"estado": "COMPLETED"}, "f": {"estado": "COMPLETED", "capability": "x"}}
        r = comparar(nube, diario)
        assert [o["command_id"] for o in r["solo_nube"]] == ["b"]
        assert [o["command_id"] for o in r["solo_pc"]] == ["f"]
        assert [o["command_id"] for o in r["distintas"]] == ["d"]

    def test_de_punta_a_punta(self, tres, monkeypatch):
        """El agente real, su diario real y el historial real de la nube: cuadran."""
        from src.agente import cruzar as modulo
        from src.agente import estado as almacen

        _enviar(tres["ana"], "apunta", {"marca": "c1"}, equipo="Portátil")
        time.sleep(0.5)

        class Cliente:
            def get(self, url, params=None, headers=None):
                return TestClient(tres["nube"]).get("/agente/historial", params=params, headers=headers)

        monkeypatch.setattr(almacen, "cargar", lambda: type("E", (), {"nube": ""})())
        monkeypatch.setattr(almacen, "credencial", lambda: tres["creds"]["Portátil"])
        dicho = []
        # Las tres agentes comparten la carpeta del agente en las pruebas: solo se cruzan
        # las órdenes de este PC con las del diario que son suyas.
        diario = modulo.leer_diario()
        propias = {k: v for k, v in diario.items() if "c1" in v.get("argumentos", "")}
        monkeypatch.setattr(modulo, "leer_diario", lambda: propias)
        assert modulo.cruzar(dicho.append, http=Cliente()) == 0, dicho
        assert "cuadran" in dicho[-1]
