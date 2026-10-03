"""
La resiliencia del agente (3.6), con el agente real, la nube real y un proxy que falla
a voluntad entre los dos (`proxy_fallos.py`).

Gate del plan: **sin ejecuciones duplicadas, estado corrupto, peticiones entre usuarios
ni peticiones fantasma**.
"""

import asyncio
import threading
import time

import pytest

from src.agente import control
from src.agente.ejecutor import Ejecutor
from src.canal import despacho
from src.canal.registro import REGISTRO
from src.identidad.contexto import como_usuario
from tests.proxy_fallos import ProxyDeFallos
from tests.test_canal_agente import _cuenta, _esperar, nube, servidor  # noqa: F401


def _agente_por(proxy, agent_id, credencial, capacidades):
    from src.agente.canal import Canal

    canal = Canal(f"http://127.0.0.1:{proxy.puerto}", credencial, Ejecutor(agent_id, capacidades),
                  espera_minima=0.1)
    hilo = threading.Thread(target=lambda: asyncio.run(canal.correr()), daemon=True)
    hilo.start()
    return canal, hilo


@pytest.fixture
def montaje(nube, servidor, monkeypatch):
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 2.0)
    _, _, user_id, agent_id, credencial = _cuenta(nube)
    proxy = ProxyDeFallos(int(servidor.rsplit(":", 1)[1]))
    hechas = []

    def apunta(marca: str):
        hechas.append((marca, time.monotonic()))
        return {"success": True, "data": {"marca": marca}, "error": None}

    canal, hilo = _agente_por(proxy, agent_id, credencial, {"apunta": apunta})
    assert _esperar(lambda: REGISTRO.de(user_id) is not None, 15)
    yield {"user_id": user_id, "proxy": proxy, "hechas": hechas, "canal": canal}
    canal.parar()
    hilo.join(10)
    proxy.cerrar()


def _enviar(user_id, capability, arguments, plazo):
    with como_usuario(user_id):
        return despacho.enviar(capability, arguments, plazo=plazo)


class TestPeticionesFantasma:
    def test_una_orden_retenida_mas_que_su_plazo_no_se_ejecuta_al_llegar(self, montaje):
        """Una conexión medio abierta retiene la orden; la nube se rinde; luego llega.

        Reproducido antes de arreglarlo (3.6): el agente medía el plazo desde que la
        recibía y la ejecutaba como nueva, segundos después de que la nube hubiera dicho
        a la persona que su PC no respondía.
        """
        proxy = montaje["proxy"]
        proxy.retener("nube→pc")
        inicio = time.monotonic()
        try:
            _enviar(montaje["user_id"], "apunta", {"marca": "fantasma"}, plazo=3)
            dijo = "respondió"
        except despacho.ErrorDelCanal as exc:
            dijo = type(exc).__name__
        rendida = time.monotonic() - inicio
        time.sleep(1.5)
        proxy.soltar()
        time.sleep(2)
        ejecutada = [t - inicio for m, t in montaje["hechas"] if m == "fantasma"]
        print(f"\nla nube dijo {dijo} a los {rendida:.1f} s; "
              f"el agente la ejecutó: {'a los %.1f s' % ejecutada[0] if ejecutada else 'no'}")
        assert dijo != "respondió"
        assert not ejecutada, "orden fantasma: se ejecutó después de que la nube se rindiera"
        # Y en la auditoría del PC queda por qué: llegó tarde, no «vencida» sin más.
        from src.agente import auditoria

        assert any(e.get("motivo") == "vencida_en_camino" for e in auditoria.leer(200))

    def test_la_hora_se_refresca_con_cada_latido(self, montaje):
        """Si la estimación de la conexión fuera mala, el siguiente latido la corrige."""
        ejecutor = montaje["canal"].ejecutor
        ejecutor.desfase = -1000.0
        assert _esperar(lambda: ejecutor.desfase > -10, 12)

    def test_una_retencion_corta_no_hace_nada_dos_veces(self, montaje):
        """Retenida menos que su plazo: llega, se hace una vez, y la nube recibe el resultado."""
        proxy = montaje["proxy"]
        proxy.retener("nube→pc")
        resultado = {}
        hilo = threading.Thread(target=lambda: resultado.update(
            r=_enviar(montaje["user_id"], "apunta", {"marca": "corta"}, plazo=8)))
        hilo.start()
        time.sleep(1.5)
        proxy.soltar()
        hilo.join(15)
        assert resultado["r"]["estado"] == "COMPLETED"
        assert [m for m, _ in montaje["hechas"]] == ["corta"]


class TestCortes:
    def _lenta(self, montaje, veces):
        def lenta(marca: str):
            for _ in range(20):
                control.punto_seguro()
                time.sleep(0.05)
            veces.append(marca)
            return {"success": True, "data": {"marca": marca}, "error": None}
        return lenta

    def test_un_corte_de_red_con_una_orden_en_marcha(self, nube, servidor, monkeypatch):
        """Se va la red (el proxy cierra todo) a mitad de una orden de 1 s."""
        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 8.0)
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        proxy = ProxyDeFallos(int(servidor.rsplit(":", 1)[1]))
        veces = []
        canal, hilo = _agente_por(proxy, agent_id, credencial, {"lenta": self._lenta(None, veces)})
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None, 15)
            resultado, inicio = {}, time.monotonic()
            h = threading.Thread(target=lambda: resultado.update(r=_enviar(user_id, "lenta", {"marca": "x"}, plazo=20)))
            h.start()
            time.sleep(0.4)
            proxy.cortar()
            h.join(30)
            print(f"\ncorte de red: {resultado['r']['estado']} a los {time.monotonic() - inicio:.1f} s, hecha {len(veces)} vez")
            assert resultado["r"]["estado"] == "COMPLETED" and veces == ["x"]
        finally:
            canal.parar(); hilo.join(10); proxy.cerrar()

    def test_la_red_muerta_mas_que_el_latido(self, nube, servidor, monkeypatch):
        """Nada pasa en 15 s (ni latidos): el agente da la conexión por muerta a los 10 s y
        no puede reconectar hasta que vuelve la red. Luego, la nube pregunta y recoge."""
        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 25.0)
        _, _, user_id, agent_id, credencial = _cuenta(nube)
        proxy = ProxyDeFallos(int(servidor.rsplit(":", 1)[1]))
        veces = []
        canal, hilo = _agente_por(proxy, agent_id, credencial, {"lenta": self._lenta(None, veces)})
        try:
            assert _esperar(lambda: REGISTRO.de(user_id) is not None, 15)
            resultado, inicio = {}, time.monotonic()
            h = threading.Thread(target=lambda: resultado.update(r=_enviar(user_id, "lenta", {"marca": "y"}, plazo=40)))
            h.start()
            time.sleep(0.3)
            proxy.retener("ambos")
            time.sleep(15)
            proxy.soltar()
            h.join(60)
            print(f"\nred muerta 15 s: {resultado.get('r', {}).get('estado')} a los "
                  f"{time.monotonic() - inicio:.1f} s, hecha {len(veces)} vez")
            assert resultado["r"]["estado"] == "COMPLETED" and veces == ["y"]
        finally:
            canal.parar(); hilo.join(10); proxy.cerrar()


class TestEntreDosPersonas:
    def test_con_cortes_ninguna_orden_llega_al_pc_de_otro(self, nube, servidor, monkeypatch):
        monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 8.0)
        cuentas = [_cuenta(nube, nombre) for nombre in ("ana", "bea")]
        proxy = ProxyDeFallos(int(servidor.rsplit(":", 1)[1]))
        hechas = {"ana": [], "bea": []}
        agentes = []
        for (_, _, user_id, agent_id, credencial), nombre in zip(cuentas, ("ana", "bea")):
            def apunta(marca: str, _n=nombre):
                hechas[_n].append(marca)
                return {"success": True, "data": {"marca": marca}, "error": None}
            agentes.append(_agente_por(proxy, agent_id, credencial, {"apunta": apunta}))
        try:
            for _, _, user_id, _, _ in cuentas:
                assert _esperar(lambda u=user_id: REGISTRO.de(u) is not None, 15)
            for i in range(6):
                hilos = []
                for (_, _, user_id, _, _), nombre in zip(cuentas, ("ana", "bea")):
                    t = threading.Thread(target=lambda u=user_id, n=nombre, k=i: _try(
                        lambda: _enviar(u, "apunta", {"marca": f"{n}-{k}"}, plazo=15)))
                    t.start()
                    hilos.append(t)
                time.sleep(random_corto())
                proxy.cortar()
                for t in hilos:
                    t.join(30)
            assert all(m.startswith("ana-") for m in hechas["ana"])
            assert all(m.startswith("bea-") for m in hechas["bea"])
            assert len(hechas["ana"]) == len(set(hechas["ana"])) and len(hechas["bea"]) == len(set(hechas["bea"]))
            print(f"\ndos personas, 6 cortes: ana {len(hechas['ana'])}, bea {len(hechas['bea'])}, sin cruces ni repetidas")
        finally:
            for canal, hilo in agentes:
                canal.parar(); hilo.join(10)
            proxy.cerrar()


def random_corto():
    import random
    return random.uniform(0.0, 0.3)


def _try(funcion):
    try:
        return funcion()
    except Exception as exc:
        return exc


# --- Estado corrupto: escrituras atómicas ------------------------------------------------

class TestSinEstadoCorrupto:
    def test_una_politica_cortada_a_mitad_deja_la_de_antes(self, monkeypatch):
        """Un apagón justo al guardar: antes quedaba el fichero a medias y el PC cerrado."""
        import os

        from src.agente import estado as almacen
        from src.agente.politica import Politica

        politica = Politica()
        politica.bloqueadas = ["C:\Privado"]
        politica.guardar()

        def apagon(*a, **k):
            raise OSError("se fue la luz")

        politica.bloqueadas = ["C:\Otra"]
        # Un contexto acotado, no `monkeypatch.undo()`: eso deshace también el aislamiento
        # de la carpeta del agente de conftest, y la prueba leería la de verdad del PC.
        with monkeypatch.context() as m:
            m.setattr(os, "replace", apagon)
            with pytest.raises(OSError):
                politica.guardar()
        assert Politica.cargar().bloqueadas == ["C:\Privado"]
        assert not [p for p in almacen.carpeta().iterdir() if p.name == "politica.json" and p.stat().st_size == 0]

    def test_el_emparejamiento_tambien(self, monkeypatch):
        import os

        from src.agente import estado as almacen

        almacen.escribir_atomico(almacen.carpeta() / "agente.json", b'{"a": 1}')
        with monkeypatch.context() as m:
            m.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("luz")))
            with pytest.raises(OSError):
                almacen.escribir_atomico(almacen.carpeta() / "agente.json", b'{"a": 2')
        assert (almacen.carpeta() / "agente.json").read_bytes() == b'{"a": 1}'


# --- La salud, a la vista ---------------------------------------------------------------

class TestLaSalud:
    @pytest.mark.parametrize("datos, en_marcha, dice", [
        ({"pulso": 100, "estado": "READY", "desde": 40, "ultimo_latido": 98}, True, "Sano"),
        ({"pulso": 100, "estado": "RECONNECTING", "desde": 60}, True, "Reconectando desde hace 40 s"),
        ({"pulso": -100, "estado": "READY"}, True, "Atascado"),
        ({}, True, "Arrancando"),
        ({"estado": "RENDIDO"}, False, "demasiadas veces"),
        ({}, False, "No está en marcha"),
    ])
    def test_el_diagnostico(self, datos, en_marcha, dice):
        from src.agente import salud

        assert dice in salud.diagnostico(datos, ahora=100, en_marcha=en_marcha)

    def test_el_agente_de_verdad_late(self, montaje):
        from src.agente import salud

        assert _esperar(lambda: salud.leer().get("estado") == "READY", 10)
        assert _esperar(lambda: time.time() - salud.leer().get("pulso", 0) < 10, 10)


# --- El vigilante --------------------------------------------------------------------------

class _Hijo:
    """Un agente simulado: acaba con `codigo` tras `vueltas` comprobaciones (None: nunca)."""

    def __init__(self, codigo, vueltas=1, pid=1):
        self.codigo, self.vueltas, self.pid, self.matado = codigo, vueltas, pid, False

    def poll(self):
        if self.matado:
            return -9
        if self.vueltas is None:
            return None
        self.vueltas -= 1
        return None if self.vueltas >= 0 else self.codigo

    def wait(self):
        return -9 if self.matado else self.codigo

    def kill(self):
        self.matado = True


class TestElVigilante:
    def _vigilar(self, hijos, reloj=None):
        from src.agente import vigilante

        lanzados = []
        cola = list(hijos)
        t = {"ahora": 1000.0}

        def lanzar():
            hijo = cola.pop(0)
            lanzados.append(hijo)
            return hijo

        def dormir(segundos):
            t["ahora"] += segundos

        codigo = vigilante.vigilar(lanzar, dormir, lambda: t["ahora"])
        return codigo, lanzados

    def test_si_se_cae_lo_levanta(self):
        codigo, lanzados = self._vigilar([_Hijo(1), _Hijo(1), _Hijo(0)])
        assert codigo == 0 and len(lanzados) == 3

    @pytest.mark.parametrize("final", [0, 2, 3, 4])
    def test_si_acaba_bien_no(self, final):
        codigo, lanzados = self._vigilar([_Hijo(final), _Hijo(0)])
        assert codigo == final and len(lanzados) == 1

    def test_si_se_atasca_lo_reinicia(self):
        atascado = _Hijo(None)
        codigo, lanzados = self._vigilar([atascado, _Hijo(0)])
        assert atascado.matado and len(lanzados) == 2 and codigo == 0

    def test_con_pulso_no_se_toca(self, monkeypatch):
        from src.agente import salud, vigilante

        vivo = _Hijo(0, vueltas=60, pid=42)
        monkeypatch.setattr(salud, "leer", lambda: {"pid": 42, "pulso": 10**12})
        codigo, lanzados = self._vigilar([vivo])
        assert not vivo.matado and codigo == 0

    def test_mas_de_cinco_caidas_en_diez_minutos_se_rinde(self):
        from src.agente import salud

        codigo, lanzados = self._vigilar([_Hijo(1) for _ in range(10)])
        assert codigo == 5 and len(lanzados) == 6
        assert salud.leer()["estado"] == "RENDIDO"

    def test_si_la_persona_para_mientras_esta_caido_no_lo_levanta(self):
        from src.agente import arranque

        class SeCaeYPiden(_Hijo):
            def wait(self):
                arranque.pedir_parada()
                return 1

        codigo, lanzados = self._vigilar([SeCaeYPiden(1), _Hijo(0)])
        assert codigo == 0 and len(lanzados) == 1

    def test_con_un_proceso_de_verdad_que_se_cae(self):
        import subprocess
        import sys

        from src.agente import vigilante

        lanzados = []

        def lanzar():
            p = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
            lanzados.append(p)
            return p

        codigo = vigilante.vigilar(lanzar, lambda s: time.sleep(min(s, 0.05)))
        assert codigo == 5 and len(lanzados) == 6

    def test_el_arranque_lanza_el_vigilante(self):
        import inspect

        from src.agente import arranque

        assert "src.agente vigilar" in inspect.getsource(arranque.activar)
        assert '"vigilar"' in inspect.getsource(arranque.lanzar_en_segundo_plano)

    def test_parar_con_solo_el_vigilante_en_marcha(self, monkeypatch):
        """El agente caído y el vigilante esperando para levantarlo: `parar` tiene que
        dejar la señal igual, o el vigilante lo levantaría. (Este vigilante de mentira no
        la atiende, así que `parar`, que desde la 3.8 espera, dice que sigue en marcha.)"""
        from src.agente import __main__ as consola, arranque

        monkeypatch.setattr(arranque, "ESPERA_PARADA", 0.3)
        candado = arranque.Candado("vigilante.lock").tomar()
        try:
            assert consola.main(["parar"]) == 1
            assert arranque.senal_de_parada().exists()
        finally:
            candado.soltar()
            arranque.senal_de_parada().unlink(missing_ok=True)



class TestLosRelojes:
    def test_un_latido_retrasado_no_rejuvenece_las_ordenes(self):
        """Se queda la mejor estimación: un latido que llega tarde da una peor, y si se
        tomara, una orden vieja pasaría por joven."""
        from src.agente.ejecutor import Ejecutor
        from src.agente.protocolo import PROTOCOLO_ACTUAL

        ej = Ejecutor("ag", {"apunta": lambda: {"success": True}})
        ahora = time.time()
        ej.observar_hora_nube(ahora, nueva_conexion=True)
        ej.observar_hora_nube(ahora - 5)            # retenido 5 s
        assert abs(ej.desfase) < 1
        orden = {"tipo": "orden", "protocol_version": PROTOCOLO_ACTUAL, "request_id": "r",
                 "command_id": "c", "agent_id": "ag", "capability": "apunta", "arguments": {},
                 "vence_en_ms": 3000, "enviada_en": ahora - 4}
        assert asyncio.run(ej.procesar(orden))["motivo"] == "vencida_en_camino"

    def test_con_cada_conexion_se_empieza_de_nuevo(self):
        from src.agente.ejecutor import Ejecutor

        ej = Ejecutor("ag")
        ej.observar_hora_nube(time.time() + 100, nueva_conexion=True)
        ej.observar_hora_nube(time.time(), nueva_conexion=True)      # el reloj del PC cambió
        assert abs(ej.desfase) < 1


class TestElVigilanteMasFino:
    def test_el_pulso_de_otro_proceso_no_cuenta(self, monkeypatch):
        """Un salud.json con el pulso del agente anterior (ya muerto) no hace pasar por vivo
        al nuevo que se atasca."""
        from src.agente import salud, vigilante

        monkeypatch.setattr(salud, "leer", lambda: {"pid": 999, "pulso": 10**12})
        atascado = _Hijo(0, vueltas=100, pid=1)
        hijos = [atascado, _Hijo(0, pid=2)]
        t = {"ahora": 1000.0}
        lanzados = []

        def lanzar():
            lanzados.append(hijos.pop(0))
            return lanzados[-1]

        vigilante.vigilar(lanzar, lambda s: t.__setitem__("ahora", t["ahora"] + s), lambda: t["ahora"])
        assert atascado.matado and len(lanzados) == 2

    def test_caidas_repartidas_en_el_tiempo_no_lo_rinden(self):
        """Seis caídas, pero cada una tras 5 minutos funcionando: no es un bucle."""
        from src.agente import vigilante

        hijos = [_Hijo(1, vueltas=60) for _ in range(7)] + [_Hijo(0)]
        t = {"ahora": 1000.0}
        lanzados = []

        def lanzar():
            lanzados.append(hijos.pop(0))
            return lanzados[-1]

        import src.agente.salud as salud_mod

        original = salud_mod.leer
        salud_mod.leer = lambda: {"pid": lanzados[-1].pid, "pulso": t["ahora"]}
        try:
            codigo = vigilante.vigilar(lanzar, lambda s: t.__setitem__("ahora", t["ahora"] + s), lambda: t["ahora"])
        finally:
            salud_mod.leer = original
        assert codigo == 0 and len(lanzados) == 8


class TestElVigilanteConProcesosDeVerdad:
    def test_con_el_lanzador_del_entorno_virtual_no_da_por_atascado_a_uno_sano(self, monkeypatch):
        """Medido en producción el 2026-09-25: el `pythonw.exe` de un venv es un lanzador, el
        pulso lo escribía el nieto y el vigilante reiniciaba un agente sano cada 120 s.
        Aquí, un agente de mentira que late como el de verdad, lanzado igual."""
        import subprocess
        import sys

        from src.agente import salud, vigilante

        monkeypatch.setattr(salud, "ATASCADO", 3.0)
        monkeypatch.setattr(vigilante, "CADA", 0.3)
        codigo_hijo = (
            "import time, sys; sys.path.insert(0, '.');"
            "from src.agente import salud;"
            "import os;"
            "[ (salud.apuntar(**({'pid': os.getpid(), 'ppid': os.getppid()} if i == 0 else {}), pulso=time.time()), time.sleep(0.3)) for i in range(25) ]"
        )
        lanzados = []

        def lanzar():
            p = subprocess.Popen([sys.executable, "-c", codigo_hijo])
            lanzados.append(p)
            return p

        codigo = vigilante.vigilar(lanzar, time.sleep)
        assert codigo == 0 and len(lanzados) == 1, "dio por atascado a un agente que latía"


class TestTrasUnaSuspension:
    """Medido en mi PC (3.6.5): al despertar de una suspensión, el vigilante veía
    el último pulso de hace 276 y 4.770 s y reiniciaba un agente que no estaba atascado."""

    def _vigilar(self, pasos):
        """`pasos`: lo que dura cada `dormir` (el vigilante duerme 5 s; una suspensión dura más)."""
        from src.agente import salud, vigilante

        t = {"ahora": 1000.0}
        hijo = _Hijo(0, vueltas=len(pasos), pid=7)
        pulsos = {"ultimo": t["ahora"]}
        original = salud.leer
        salud.leer = lambda: {"pid": 7, "pulso": pulsos["ultimo"]}
        pasos = list(pasos)

        def dormir(_segundos):
            dura, late = pasos.pop(0) if pasos else (5, True)
            t["ahora"] += dura
            if late:
                pulsos["ultimo"] = t["ahora"]

        try:
            vigilante.vigilar(lambda: hijo, dormir, lambda: t["ahora"])
        finally:
            salud.leer = original
        return hijo

    def test_una_suspension_larga_no_es_un_atasco(self):
        # Late con normalidad, luego 80 minutos suspendido (nadie late), y al despertar
        # tarda unos segundos en volver a latir.
        hijo = self._vigilar([(5, True)] * 3 + [(4800, False), (5, False), (5, True)] + [(5, True)] * 3)
        assert not hijo.matado

    def test_un_atasco_de_verdad_sigue_viendose(self):
        hijo = self._vigilar([(5, True)] * 3 + [(5, False)] * 30)
        assert hijo.matado

    def test_y_tras_despertar_si_no_vuelve_a_latir_tambien(self):
        hijo = self._vigilar([(5, True), (4800, False)] + [(5, False)] * 30)
        assert hijo.matado


class TestUnaLecturaVacia:
    """3.8: leer `salud.json` justo mientras el agente lo sustituye da {} (medido: 962 de
    70.134 lecturas con un escritor sin pausa). El vigilante lo tomaba por «sin pulso» y,
    pasados 120 s desde el lanzamiento, reiniciaba un agente sano (visto en mi PC:
    «1470 s sin pulso» a los 24 minutos de lanzarlo)."""

    def _vigilar(self, lecturas, vueltas=400):
        """`lecturas(t)`: lo que devuelve leer() en el instante t. El agente acaba bien tras
        `vueltas` comprobaciones si nadie lo mata."""
        from src.agente import salud, vigilante

        t = {"ahora": 1000.0}
        hijo = _Hijo(0, vueltas=vueltas, pid=7)
        original = salud.leer
        salud.leer = lambda: lecturas(t["ahora"])
        try:
            vigilante.vigilar(lambda: hijo, lambda s: t.__setitem__("ahora", t["ahora"] + s), lambda: t["ahora"])
        finally:
            salud.leer = original
        return hijo

    def test_sano_y_una_lectura_vacia_no_lo_reinicia(self):
        vacia = {1000.0 + 24 * 60 + 5 * k for k in range(1)}
        hijo = self._vigilar(lambda t: {} if int(t) % 1440 == 0 or t in vacia else {"pid": 7, "pulso": t})
        assert not hijo.matado

    def test_muchas_lecturas_vacias_sueltas_tampoco(self):
        hijo = self._vigilar(lambda t: {} if int(t) % 15 == 0 else {"pid": 7, "pulso": t})
        assert not hijo.matado

    def test_si_nunca_escribe_su_salud_se_reinicia(self):
        hijo = self._vigilar(lambda t: {})
        assert hijo.matado

    def test_si_deja_de_latir_se_ve_aunque_haya_vacias(self):
        hijo = self._vigilar(lambda t: {} if int(t) % 10 == 0 else {"pid": 7, "pulso": min(t, 1300.0)})
        assert hijo.matado


class TestLeerLaSalud:
    def test_reintenta_si_esta_a_medio_sustituir(self, monkeypatch):
        from pathlib import Path

        from src.agente import salud

        salud.apuntar(pid=1, estado="READY")
        fallos = {"n": 2}
        original = Path.read_text

        def lee(self, *a, **k):
            if self == salud.fichero() and fallos["n"]:
                fallos["n"] -= 1
                raise PermissionError("se está sustituyendo")
            return original(self, *a, **k)
        monkeypatch.setattr(Path, "read_text", lee)
        assert salud.leer()["estado"] == "READY"

    def test_sin_fichero_no_espera(self):
        from src.agente import salud

        salud.fichero().unlink(missing_ok=True)
        assert salud.leer() == {}


class TestUnaVersionPorConfirmar:
    """3.8.5: si el PC se apaga a mitad de una actualización, nadie juzga a la versión
    nueva (el que la juzgaba era `actualizar`, que murió con el PC). Su propio vigilante lo
    hace: si en PLAZO_ESTRENO no llega a sana, o se rinde por caídas, vuelve a la anterior."""

    def _vigilar(self, hijos, por_confirmar, lecturas=lambda t: {}):
        from src.agente import salud, vigilante

        t = {"ahora": 1000.0}
        lanzados, vueltas = [], []
        cola = list(hijos)

        def lanzar():
            lanzados.append(cola.pop(0))
            return lanzados[-1]
        original = salud.leer
        salud.leer = lambda: lecturas(t["ahora"])
        try:
            codigo = vigilante.vigilar(lanzar, lambda s: t.__setitem__("ahora", t["ahora"] + s),
                                       lambda: t["ahora"], por_confirmar=lambda: por_confirmar(t["ahora"]),
                                       volver_atras=lambda motivo: vueltas.append(motivo) or vigilante.VUELTA_ATRAS)
        finally:
            salud.leer = original
        return codigo, lanzados, vueltas

    def test_si_no_llega_a_sana_vuelve_atras(self):
        from src.agente import vigilante

        # Vivo (late) pero nunca se confirma: no conecta.
        hijo = _Hijo(0, vueltas=1000, pid=7)
        codigo, lanzados, vueltas = self._vigilar([hijo], lambda t: True, lambda t: {"pid": 7, "pulso": t})
        assert codigo == vigilante.VUELTA_ATRAS and hijo.matado and len(vueltas) == 1

    def test_si_se_cae_una_y_otra_vez_vuelve_atras_en_vez_de_rendirse(self):
        from src.agente import salud, vigilante

        codigo, lanzados, vueltas = self._vigilar([_Hijo(1) for _ in range(10)], lambda t: True)
        assert codigo == vigilante.VUELTA_ATRAS and len(vueltas) == 1
        assert salud.leer().get("estado") != "RENDIDO"

    def test_si_se_confirma_a_tiempo_sigue(self):
        hijo = _Hijo(0, vueltas=1000, pid=7)
        codigo, _, vueltas = self._vigilar([hijo], lambda t: t < 1060, lambda t: {"pid": 7, "pulso": t})
        assert vueltas == [] and not hijo.matado and codigo == 0

    def test_una_version_ya_confirmada_no_se_toca(self):
        hijo = _Hijo(0, vueltas=1000, pid=7)
        codigo, _, vueltas = self._vigilar([hijo], lambda t: False, lambda t: {"pid": 7, "pulso": t})
        assert vueltas == [] and not hijo.matado

    def test_sin_anterior_a_la_que_volver_sigue_como_siempre(self):
        from src.agente import salud, vigilante

        t = {"ahora": 1000.0}
        cola = [_Hijo(1) for _ in range(10)]
        codigo = vigilante.vigilar(lambda: cola.pop(0), lambda s: t.__setitem__("ahora", t["ahora"] + s),
                                   lambda: t["ahora"], por_confirmar=lambda: True, volver_atras=lambda m: None)
        assert codigo == 5 and salud.leer()["estado"] == "RENDIDO"


    def test_sin_anterior_y_sin_llegar_a_sana_se_relanza_una_vez(self):
        """Sin anterior a la que volver, se relanza como un atasco y no se insiste."""
        from src.agente import vigilante

        t = {"ahora": 1000.0}
        cola = [_Hijo(0, vueltas=1000, pid=7), _Hijo(0, vueltas=10, pid=8)]
        lanzados, vueltas = [], []

        def lanzar():
            lanzados.append(cola.pop(0))
            return lanzados[-1]
        from src.agente import salud

        original = salud.leer
        salud.leer = lambda: {"pid": lanzados[-1].pid, "pulso": t["ahora"]}
        try:
            codigo = vigilante.vigilar(lanzar, lambda s: t.__setitem__("ahora", t["ahora"] + s), lambda: t["ahora"],
                                       por_confirmar=lambda: True, volver_atras=lambda m: vueltas.append(m))
        finally:
            salud.leer = original
        assert codigo == 0 and len(vueltas) == 1 and len(lanzados) == 2 and lanzados[0].matado


class TestElRelevoDelVigilante:
    def test_espera_a_que_el_que_se_va_suelte_el_candado(self):
        """Al volver atrás, el vigilante que se va lanza el de la anterior antes de soltar su
        candado: el nuevo espera unos segundos en vez de irse con «ya hay otro»."""
        import threading

        from src.agente import __main__ as consola, arranque, vigilante

        candado = arranque.Candado("vigilante.lock").tomar()
        threading.Timer(1.0, candado.soltar).start()
        original = vigilante.vigilar
        vigilante.vigilar = lambda: 0
        try:
            assert consola._vigilar() == 0
        finally:
            vigilante.vigilar = original


class TestElRelevoALaActiva:
    """3.8.5: un apagón entre escribir `activa.json` y reescribir el acceso directo dejaba
    arrancando la versión de antes. El vigilante que no es la activa le pasa el relevo."""

    @pytest.fixture
    def dos_versiones(self, monkeypatch):
        from src.agente import arranque, instalacion

        for v in ("9.0.1", "9.0.7"):
            (instalacion.carpeta_de(v) / "venv" / "Scripts").mkdir(parents=True)
            instalacion.python_de(v).write_text("")
        monkeypatch.setattr(instalacion, "instalada", lambda raiz=None: True)
        lanzados = []
        monkeypatch.setattr(arranque, "lanzar_en_segundo_plano",
                            lambda python=None, carpeta=None: lanzados.append(carpeta.name))
        monkeypatch.setattr(arranque, "activado", lambda: False)
        return lanzados

    def test_si_la_activa_es_otra_le_pasa_el_relevo(self, dos_versiones, monkeypatch):
        import src
        from src.agente import instalacion, vigilante

        monkeypatch.setattr(src, "__version__", "9.0.1")
        instalacion._guardar_activa({"version": "9.0.7", "anterior": "9.0.1", "pendiente": {"desde": 0}})
        hijos = []
        codigo = vigilante.vigilar(lambda: hijos.append(1), lambda s: None, time.time)
        assert codigo == vigilante.RELEVO and dos_versiones == ["9.0.7"] and hijos == []

    def test_si_es_la_activa_sigue(self, dos_versiones, monkeypatch):
        import src
        from src.agente import instalacion, vigilante

        monkeypatch.setattr(src, "__version__", "9.0.7")
        instalacion._guardar_activa({"version": "9.0.7"})
        assert vigilante._relevo_a_la_activa() is None and dos_versiones == []

    def test_si_la_activa_no_esta_instalada_no(self, dos_versiones, monkeypatch):
        import src
        from src.agente import instalacion, vigilante

        monkeypatch.setattr(src, "__version__", "9.0.1")
        instalacion._guardar_activa({"version": "9.9.9"})
        assert vigilante._relevo_a_la_activa() is None


class TestUnaSuspensionMientrasSeEstrena:
    def test_no_vuelve_atras_desde_una_version_buena(self):
        """Auditoría de la 3.x: el plazo de estreno (3 min) se medía con el reloj de pared;
        una suspensión nada más actualizar lo gastaba, y al despertar se volvía atrás."""
        from src.agente import salud, vigilante

        t = {"ahora": 1000.0, "n": 0}
        hijo = _Hijo(0, vueltas=40, pid=7)
        confirmada = {"ya": False}

        def dormir(s):
            t["n"] += 1
            t["ahora"] += 3600 if t["n"] == 2 else s              # una hora suspendido
            if t["n"] == 6:
                confirmada["ya"] = True                             # al despertar, llega a READY
        vueltas = []
        original = salud.leer
        salud.leer = lambda: {"pid": 7, "pulso": t["ahora"]}
        try:
            vigilante.vigilar(lambda: hijo, dormir, lambda: t["ahora"],
                              por_confirmar=lambda: not confirmada["ya"],
                              volver_atras=lambda m: vueltas.append(m) or vigilante.VUELTA_ATRAS,
                              relevo=lambda: None)
        finally:
            salud.leer = original
        assert vueltas == [] and not hijo.matado
