"""
Estrés del motor de ejecución (3.4.5), con el agente de verdad y la nube de verdad.

**Solo con `MORGAN_ESTRES=1`**: tarda minutos. Se queda en el repositorio para
repetirlo en la 3.9-C. El plan (§V3.4.5) pide: operación lenta, petición duplicada,
desconexión, timeout, cola saturada, cancelación y recuperación.

Lo que tiene que cumplirse **siempre**, pase lo que pase:

1. **Cada orden se hace como mucho una vez.**
2. **Lo que la nube dice que pasó es lo que pasó en el disco**: nunca «cancelada» con el
   archivo escrito, ni «hecha» sin él.
3. **Ninguna orden se queda colgada**: todas acaban con una respuesta, en su plazo.
"""

import asyncio
import json
import os
import random
import statistics
import threading
import time
from pathlib import Path

import pytest

from src.agente import control, escritura
from src.agente.ejecutor import Ejecutor
from src.agente.politica import DE_ESCRITURA, Politica
from src.canal import despacho
from src.canal.registro import REGISTRO
from src.identidad.contexto import como_usuario
from tests.test_canal_agente import _cuenta, _esperar, nube, servidor  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("MORGAN_ESTRES") != "1",
                                reason="estrés de la 3.4.5: solo con MORGAN_ESTRES=1")

INFORME: list[str] = []


def informe(linea: str) -> None:
    INFORME.append(linea)
    print(linea, flush=True)


@pytest.fixture(autouse=True)
def _sin_appdata(tmp_path, monkeypatch):
    for variable in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
        (tmp_path / "_sistema" / variable).mkdir(parents=True)
        monkeypatch.setenv(variable, str(tmp_path / "_sistema" / variable))


class Veces:
    """Cuántas veces se ejecutó de verdad cada orden, por su marca."""

    def __init__(self):
        self.cerrojo = threading.Lock()
        self.por_marca: dict[str, int] = {}

    def una(self, marca: str) -> None:
        with self.cerrojo:
            self.por_marca[marca] = self.por_marca.get(marca, 0) + 1


def _agente(servidor, agent_id, credencial, capacidades):
    from src.agente.canal import Canal

    ejecutor = Ejecutor(agent_id, capacidades)
    canal = Canal(servidor, credencial, ejecutor, espera_minima=0.1)
    hilo = threading.Thread(target=lambda: asyncio.run(canal.correr()), daemon=True)
    hilo.start()
    return canal, hilo, ejecutor


def _enviar(user_id, capability, arguments, plazo=20.0, parada=None):
    from src.canal import paradas

    marca = paradas.PARADA.set(parada) if parada is not None else None
    try:
        with como_usuario(user_id):
            return despacho.enviar(capability, arguments, plazo=plazo)
    finally:
        if marca is not None:
            paradas.PARADA.reset(marca)


def _cortar(user_id):
    conexion = REGISTRO.de(user_id)
    if conexion is not None:
        asyncio.run_coroutine_threadsafe(REGISTRO.quitar(conexion, 1012, "corte"), REGISTRO.loop).result(5)


@pytest.fixture
def montaje(nube, servidor, tmp_path, monkeypatch):
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 8.0)
    _, _, user_id, agent_id, credencial = _cuenta(nube)
    notas = tmp_path / "Notas"
    notas.mkdir()
    politica = Politica()
    politica.anadir(str(notas))
    politica.permitir_escritura(str(notas))
    for c in DE_ESCRITURA:
        politica.capacidades[c] = True
    politica.guardar()
    veces = Veces()

    def lenta(marca: str, segundos: float = 0.5):
        for _ in range(int(segundos / 0.05)):
            control.punto_seguro()
            time.sleep(0.05)
        control.sin_vuelta()
        veces.una(marca)
        return {"success": True, "data": {"marca": marca}, "error": None}

    def terca(marca: str, segundos: float = 3.0):
        """Sin puntos seguros: no para aunque se lo pidan."""
        time.sleep(segundos)
        veces.una(marca)
        return {"success": True, "data": {"marca": marca}, "error": None}

    from src.agente import capacidades

    caps = {**capacidades.disponibles(), "lenta": lenta, "terca": terca}
    canal, hilo, ejecutor = _agente(servidor, agent_id, credencial, caps)
    assert _esperar(lambda: REGISTRO.de(user_id) is not None, 15)
    yield {"user_id": user_id, "notas": notas, "veces": veces, "ejecutor": ejecutor, "canal": canal,
           "servidor": servidor, "agent_id": agent_id, "credencial": credencial}
    canal.parar()
    hilo.join(10)


def test_1_operacion_lenta_y_latencia(montaje):
    tiempos = []
    for i in range(20):
        t = time.monotonic()
        r = _enviar(montaje["user_id"], "lenta", {"marca": f"l{i}", "segundos": 0.2})
        tiempos.append((time.monotonic() - t) * 1000)
        assert r["estado"] == "COMPLETED"
    rapidas = []
    for _ in range(30):
        t = time.monotonic()
        _enviar(montaje["user_id"], "estado", {})
        rapidas.append((time.monotonic() - t) * 1000)
    assert all(v == 1 for v in montaje["veces"].por_marca.values())
    informe(f"1 lenta: 20 de 0,2 s p50 {statistics.median(tiempos):.0f} ms · "
            f"estado p50 {statistics.median(rapidas):.1f} ms p95 {sorted(rapidas)[28]:.1f} ms")


def test_2_peticion_duplicada(montaje):
    """La misma orden, mandada varias veces por la nube (una nube que se repite)."""
    conexion = REGISTRO.de(montaje["user_id"])
    ejecutadas = 0
    for i in range(10):
        orden = json.dumps({"tipo": "orden", "protocol_version": conexion.protocolo, "request_id": "r",
                            "command_id": f"dup-{i}", "agent_id": conexion.agent_id, "capability": "lenta",
                            "arguments": {"marca": f"d{i}", "segundos": 0.2}, "vence_en_ms": 10_000})
        for _ in range(3):
            asyncio.run_coroutine_threadsafe(conexion.enviar(orden), REGISTRO.loop).result(5)
            time.sleep(random.uniform(0, 0.15))
    time.sleep(4)
    ejecutadas = sum(montaje["veces"].por_marca.values())
    informe(f"2 duplicada: 10 órdenes × 3 envíos → {ejecutadas} ejecuciones "
            f"(máx por orden {max(montaje['veces'].por_marca.values())})")
    assert all(v == 1 for v in montaje["veces"].por_marca.values()) and ejecutadas == 10


def test_3_desconexion_a_mitad(montaje):
    resultados, recuperacion = {}, []

    def una(i):
        t = time.monotonic()
        try:
            resultados[i] = _enviar(montaje["user_id"], "lenta", {"marca": f"c{i}", "segundos": 0.8}, plazo=25)
        except Exception as exc:
            resultados[i] = exc
        recuperacion.append(time.monotonic() - t)

    for i in range(15):
        hilo = threading.Thread(target=una, args=(i,))
        hilo.start()
        time.sleep(random.uniform(0.1, 0.7))
        _cortar(montaje["user_id"])
        hilo.join(40)
    bien = [i for i, r in resultados.items() if isinstance(r, dict) and r["estado"] == "COMPLETED"]
    informe(f"3 desconexión: 15 cortes a mitad → {len(bien)} completadas, "
            f"{len(resultados) - len(bien)} otras ({[type(r).__name__ if not isinstance(r, dict) else r['estado'] for r in resultados.values() if not (isinstance(r, dict) and r['estado'] == 'COMPLETED')]}); "
            f"duración p50 {statistics.median(recuperacion):.1f} s máx {max(recuperacion):.1f} s")
    assert all(v == 1 for v in montaje["veces"].por_marca.values())
    # Lo que la nube dice hecho, se hizo; lo que no, no.
    for i, r in resultados.items():
        hecha = montaje["veces"].por_marca.get(f"c{i}", 0) == 1
        if isinstance(r, dict) and r["estado"] == "COMPLETED":
            assert hecha, i
    assert len(bien) == 15


def test_4_plazo_vencido(montaje):
    r = _enviar(montaje["user_id"], "lenta", {"marca": "t1", "segundos": 5}, plazo=3)
    assert r["estado"] == "CANCELLED" and "t1" not in montaje["veces"].por_marca
    t = time.monotonic()
    try:
        r2 = _enviar(montaje["user_id"], "terca", {"marca": "t2", "segundos": 4}, plazo=2.5)
        estado2 = r2["estado"]
    except despacho.AgenteSinRespuesta:
        estado2 = "SinRespuesta"
    espera = time.monotonic() - t
    time.sleep(3)
    entrada = [e for e in montaje["ejecutor"].diario._entradas.values() if "t2" in e.get("argumentos", "")]
    informe(f"4 plazo: con puntos seguros → CANCELLED; sin ellos → {estado2} a los {espera:.1f} s, "
            f"y el diario dice {entrada[0]['estado'] if entrada else '?'} al acabar")
    assert estado2 in ("RUNNING", "SinRespuesta") and entrada and entrada[0]["estado"] == "COMPLETED"


def test_5_cola_saturada(montaje):
    resultados = []

    def una(i):
        t = time.monotonic()
        try:
            r = _enviar(montaje["user_id"], "lenta", {"marca": f"q{i}", "segundos": 0.5}, plazo=20)
            resultados.append(("ok", r["estado"], time.monotonic() - t))
        except despacho.AgenteOcupado:
            resultados.append(("ocupado", "", time.monotonic() - t))
        except Exception as exc:
            resultados.append((type(exc).__name__, "", time.monotonic() - t))

    hilos = [threading.Thread(target=una, args=(i,)) for i in range(12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    ok = [r for r in resultados if r[0] == "ok"]
    ocupado = [r for r in resultados if r[0] == "ocupado"]
    informe(f"5 cola: 12 a la vez → {len(ok)} atendidas, {len(ocupado)} «ocupado» "
            f"(en {max((r[2] for r in ocupado), default=0) * 1000:.0f} ms como mucho), otras {len(resultados) - len(ok) - len(ocupado)}")
    assert len(ok) == 5 and len(ocupado) == 7
    assert all(r[2] < 0.5 for r in ocupado)

    # Y el tope del propio agente: una nube que no respeta el suyo.
    conexion = REGISTRO.de(montaje["user_id"])
    antes = sum(montaje["veces"].por_marca.values())
    for i in range(10):
        asyncio.run_coroutine_threadsafe(conexion.enviar(json.dumps({
            "tipo": "orden", "protocol_version": conexion.protocolo, "request_id": "r",
            "command_id": f"inunda-{i}", "agent_id": conexion.agent_id, "capability": "lenta",
            "arguments": {"marca": f"i{i}", "segundos": 0.3}, "vence_en_ms": 20_000})), REGISTRO.loop).result(5)
    time.sleep(6)
    hechas = sum(montaje["veces"].por_marca.values()) - antes
    informe(f"5 cola del agente: 10 órdenes de golpe saltándose la nube → {hechas} hechas (1 en marcha + 5 en cola)")
    assert hechas == 6


def test_6_cancelacion_de_escrituras(montaje, monkeypatch):
    """La propiedad que importa: lo que se dice es lo que hay en el disco."""
    from src.agente import motor

    lento, evaluar = escritura._escribir_temporal, motor.evaluar

    def despacio(destino, datos):
        time.sleep(0.3)                 # después del punto sin vuelta
        return lento(destino, datos)

    def pensando(*a, **k):
        time.sleep(0.3)                 # antes: la política (como una confirmación corta)
        return evaluar(*a, **k)

    monkeypatch.setattr(escritura, "_escribir_temporal", despacio)
    monkeypatch.setattr(motor, "evaluar", pensando)
    cuentas = {"CANCELLED": 0, "COMPLETED": 0, "COMPLETED_tarde": 0}
    latencias = []
    for i in range(40):
        parada = threading.Event()
        ruta = montaje["notas"] / f"c{i}.txt"
        resultado = {}
        hilo = threading.Thread(target=lambda: resultado.update(r=_enviar(
            montaje["user_id"], "create_file", {"path": str(ruta), "content": "x"}, parada=parada)))
        hilo.start()
        time.sleep(random.uniform(0, 0.9))
        parada.set()
        pulsado = time.monotonic()
        hilo.join(20)
        latencias.append(time.monotonic() - pulsado)
        r = resultado["r"]
        existe = ruta.exists()
        if r["estado"] == "CANCELLED":
            assert not existe, f"dijo cancelada y el archivo {i} existe"
            cuentas["CANCELLED"] += 1
        else:
            assert r["estado"] == "COMPLETED" and existe, f"dijo {r['estado']} y existe={existe}"
            tarde = "cancelacion" in (r["resultado"].get("data") or {})
            cuentas["COMPLETED_tarde" if tarde else "COMPLETED"] += 1
    informe(f"6 cancelación: 40 «Detener» a destiempo en create_file → {cuentas}; ninguna discrepancia "
            f"entre lo dicho y el disco; de pulsar a tener respuesta p50 {statistics.median(latencias):.2f} s "
            f"máx {max(latencias):.2f} s")
    assert cuentas["CANCELLED"] and cuentas["COMPLETED_tarde"]


def test_7_el_agente_cae_mas_de_lo_que_la_nube_espera(montaje, monkeypatch):
    """El agente se para con una orden a medias y no vuelve a tiempo: la nube dice que se
    desconectó (sin inventar), y el trabajo, que siguió en el PC, queda en su diario, hecho
    una sola vez."""
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 1.0)
    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(r=_try(lambda: _enviar(
        montaje["user_id"], "lenta", {"marca": "caida", "segundos": 1.5}, plazo=20))))
    hilo.start()
    time.sleep(0.4)
    montaje["canal"].parar()            # el agente se va y no vuelve
    hilo.join(30)
    r = resultado["r"]
    time.sleep(2)
    entrada = [e for e in montaje["ejecutor"].diario._entradas.values() if "caida" in e.get("argumentos", "")]
    informe(f"7 caída larga: la nube dice {type(r).__name__ if isinstance(r, Exception) else r['estado']}; "
            f"el diario del PC dice {entrada[0]['estado'] if entrada else '?'}; "
            f"ejecutada {montaje['veces'].por_marca.get('caida', 0)} vez")
    assert isinstance(r, despacho.AgenteDesconectado)
    assert entrada and entrada[0]["estado"] in ("COMPLETED", "CANCELLED")
    assert montaje["veces"].por_marca.get("caida", 0) == (1 if entrada[0]["estado"] == "COMPLETED" else 0)


def _try(funcion):
    try:
        return funcion()
    except Exception as exc:
        return exc


def test_zz_informe():
    print("\n".join(["", "== Informe de estrés 3.4.5 ==", *INFORME]))
