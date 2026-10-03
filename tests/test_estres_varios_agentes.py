"""
Evaluación de varios agentes (3.7.5), con agentes reales, la nube real y fallos de red.

**Solo con `MORGAN_ESTRES=1`**; dura `MORGAN_ESTRES_MINUTOS` (5 por defecto). El plan
(§V3.7.5): con `User A ├── A1, A2` y `User B └── B1`, enrutado, aislamiento,
concurrencia, auditoría, revocación y desconexión. Lo que tiene que cumplirse al final:

1. **Cada orden se hizo en el PC que nombró, de quien la mandó, y una sola vez.**
2. **Una orden sin decir el equipo, con varios, no se hace en ninguno**, tampoco mientras
   uno está cortado un momento (lo que encontró esta prueba). Después de revocar el
   Sobremesa, Ana tiene uno, y sí se hace en él.
3. **Tras revocar un PC, nada más se hace en él**, y el otro sigue.
4. **Un PC ocupado no frena al otro** de la misma persona.
5. **El historial de la base cuadra** con lo hecho: cada orden completada, con su PC.
6. Nada se queda en vuelo.
"""

import os
import random
import statistics
import threading
import time
from collections import Counter

import pytest

from src.canal import despacho
from src.canal.registro import REGISTRO
from tests.proxy_fallos import ProxyDeFallos
from tests.test_canal_agente import _cuenta, _emparejar, _esperar, nube, servidor  # noqa: F401
from tests.test_resiliencia import _agente_por
from tests.test_varios_agentes import _enviar

pytestmark = pytest.mark.skipif(os.environ.get("MORGAN_ESTRES") != "1",
                                reason="evaluación de varios agentes: solo con MORGAN_ESTRES=1")


def test_tres_agentes_con_fallos(nube, servidor, monkeypatch):
    minutos = float(os.environ.get("MORGAN_ESTRES_MINUTOS", "5"))
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 6.0)
    puerto = int(servidor.rsplit(":", 1)[1])
    web_ana, csrf_ana, ana, portatil_id, portatil_cred = _cuenta(nube, "ana")
    sobremesa_id, sobremesa_cred = _emparejar(nube, web_ana, csrf_ana, "Sobremesa")
    _, _, bea, bea_id, bea_cred = _cuenta(nube, "bea")
    equipos = {"Portátil": (ana, portatil_id, portatil_cred), "Sobremesa": (ana, sobremesa_id, sobremesa_cred),
               "PC de Bea": (bea, bea_id, bea_cred)}
    hechas, cerrojo = [], threading.Lock()                    # (equipo, marca, cuándo)
    proxies, agentes = {}, {}
    for nombre, (_, agent_id, cred) in equipos.items():
        def apunta(marca: str, segundos: float = 0.0, _n=nombre):
            time.sleep(segundos)
            with cerrojo:
                hechas.append((_n, marca, time.monotonic()))
            return {"success": True, "data": {"marca": marca}, "error": None}
        proxies[nombre] = ProxyDeFallos(puerto)
        agentes[nombre] = _agente_por(proxies[nombre], agent_id, cred, {"apunta": apunta})
    assert _esperar(lambda: len(REGISTRO.todas(ana)) == 2 and len(REGISTRO.todas(bea)) == 1, 20)

    respuestas = {}                                               # marca → (estado o error)
    latencias_sobremesa_con_portatil_ocupado = []
    revocado_en = {"t": None}
    parar = threading.Event()
    fin = time.monotonic() + minutos * 60

    def mandar(user_id, equipo, prefijo, lenta=False):
        i = 0
        while not parar.is_set():
            i += 1
            marca = f"{prefijo}-{i}"
            segundos = 2.0 if lenta and i % 5 == 0 else 0.0
            t0 = time.monotonic()
            portatil_ocupado = any(m.startswith("P-") for m in ocupado)
            try:
                r = _enviar(user_id, "apunta", {"marca": marca, "segundos": segundos}, equipo=equipo, plazo=10)
                respuestas[marca] = r.get("estado")
            except despacho.ErrorDelCanal as exc:
                respuestas[marca] = type(exc).__name__
            t1 = time.monotonic()
            # Solo cuenta si el Sobremesa no se cortó entretanto: eso lo frena a él, no el Portátil.
            if (equipo == "Sobremesa" and portatil_ocupado and respuestas[marca] == "COMPLETED"
                    and not any(t0 - 3 <= t <= t1 for t in cortado_en["Sobremesa"])):
                latencias_sobremesa_con_portatil_ocupado.append(t1 - t0)
            time.sleep(random.uniform(0.05, 0.3))

    ocupado: set = set()
    cortado_en = {n: [] for n in equipos}

    def portatil():
        i = 0
        while not parar.is_set():
            i += 1
            marca = f"P-{i}"
            lenta = i % 6 == 0
            if lenta:
                ocupado.add(marca)
            try:
                r = _enviar(ana, "apunta", {"marca": marca, "segundos": 2.0 if lenta else 0.0},
                            equipo="Portátil", plazo=10)
                respuestas[marca] = r.get("estado")
            except despacho.ErrorDelCanal as exc:
                respuestas[marca] = type(exc).__name__
            ocupado.discard(marca)
            time.sleep(random.uniform(0.05, 0.3))

    def sin_equipo():
        i = 0
        while not parar.is_set():
            i += 1
            marca = f"X-{i}"
            try:
                r = _enviar(ana, "apunta", {"marca": marca}, plazo=5)
                respuestas[marca] = r.get("estado")
            except despacho.ErrorDelCanal as exc:
                respuestas[marca] = type(exc).__name__
            time.sleep(random.uniform(1, 3))

    hilos = [threading.Thread(target=portatil, daemon=True),
             threading.Thread(target=mandar, args=(ana, "Sobremesa", "S"), daemon=True),
             threading.Thread(target=mandar, args=(bea, None, "B"), daemon=True),
             threading.Thread(target=sin_equipo, daemon=True)]
    for h in hilos:
        h.start()
    cortes = Counter()
    while time.monotonic() < fin:
        time.sleep(random.uniform(5, 10))
        if revocado_en["t"] is None and time.monotonic() > fin - minutos * 60 * 0.4:
            r = web_ana.delete(f"/auth/agentes/{sobremesa_id}", headers={"x-morgan-csrf": csrf_ana})
            assert r.status_code == 200
            revocado_en["t"] = time.monotonic()
            continue
        nombre = random.choice([n for n in proxies if not (n == "Sobremesa" and revocado_en["t"])])
        cortado_en[nombre].append(time.monotonic())
        proxies[nombre].cortar()
        cortes[nombre] += 1
    parar.set()
    for h in hilos:
        h.join(60)
    time.sleep(3)

    por_equipo = Counter(e for e, _, _ in hechas)
    marcas = Counter(m for _, m, _ in hechas)
    mal_sitio = [(e, m) for e, m, _ in hechas
                 if not ((e == "Portátil" and m[0] in "PX") or (e == "Sobremesa" and m[0] in "SX")
                         or (e == "PC de Bea" and m.startswith("B-")))]   # X: de Ana, sin decir cuál
    duplicadas = [m for m, n in marcas.items() if n > 1]
    ambiguas_hechas = [m for _, m, t in hechas if m.startswith("X-")
                       and (revocado_en["t"] is None or t < revocado_en["t"])]
    sin_equipo_tras_revocar = sum(1 for _, m, t in hechas if m.startswith("X-") and revocado_en["t"]
                                  and t >= revocado_en["t"])
    tras_revocar = [m for e, m, t in hechas if e == "Sobremesa" and revocado_en["t"] and t > revocado_en["t"] + 1]
    estados = Counter(respuestas.values())

    # 5. El historial de la base.
    from src.api.dependencies import get_container
    from src.identidad.repositorio import repositorio_de_cuentas

    repo = repositorio_de_cuentas(get_container().repositories)
    historial = {n: repo.ordenes_de_agente(u, a, 0, 100000) for n, (u, a, _) in equipos.items()}
    completadas_bd = {n: sum(1 for o in h if o["estado"] == "completed") for n, h in historial.items()}
    # Las X completadas son de después de revocar el Sobremesa: en el Portátil, el único.
    completadas_resp = {"Portátil": sum(1 for m, e in respuestas.items() if m[0] in "PX" and e == "COMPLETED"),
                        "Sobremesa": sum(1 for m, e in respuestas.items() if m.startswith("S-") and e == "COMPLETED"),
                        "PC de Bea": sum(1 for m, e in respuestas.items() if m.startswith("B-") and e == "COMPLETED")}
    en_vuelo = [len(c.esperando) + len(c.estados) + len(c.consultas) for u in (ana, bea) for c in REGISTRO.todas(u)]
    lat = latencias_sobremesa_con_portatil_ocupado

    print(f"\n{minutos:.0f} min · cortes {dict(cortes)} · revocado Sobremesa: {'sí' if revocado_en['t'] else 'no'}"
          f"\nórdenes {len(respuestas)}: {dict(estados)}"
          f"\nhechas por equipo {dict(por_equipo)} · en el sitio equivocado {len(mal_sitio)} · duplicadas "
          f"{len(duplicadas)} · sin equipo hechas {len(ambiguas_hechas)} (tras revocar, en el único: {sin_equipo_tras_revocar}) · en Sobremesa tras revocar {len(tras_revocar)}"
          f"\nSobremesa con el Portátil ocupado: {len(lat)} órdenes, p50 "
          f"{statistics.median(lat) * 1000 if lat else 0:.0f} ms, máx {max(lat) * 1000 if lat else 0:.0f} ms"
          f"\nhistorial: completadas en la base {completadas_bd} · según las respuestas {completadas_resp}"
          f"\nen vuelo al acabar {en_vuelo}")

    for proxy in proxies.values():
        proxy.cerrar()
    for canal, hilo in agentes.values():
        canal.parar()
        hilo.join(10)

    assert not mal_sitio, mal_sitio[:5]
    assert not duplicadas, duplicadas[:5]
    assert not ambiguas_hechas, ambiguas_hechas[:5]
    assert not tras_revocar, tras_revocar[:5]
    assert all(n == 0 for n in en_vuelo), en_vuelo
    assert completadas_bd == completadas_resp
    assert not lat or max(lat) < 1.9, "el Portátil ocupado frenó al Sobremesa"
