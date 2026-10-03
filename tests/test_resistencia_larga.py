"""
Resistencia larga (3.6.5): dos personas mandando órdenes sin parar mientras la red falla.

**Solo con `MORGAN_ESTRES=1`**, y dura `MORGAN_ESTRES_MINUTOS` (30 por defecto). Con el
agente real, la nube real y el proxy de fallos (`proxy_fallos.py`), que cada pocos
segundos corta, retiene poco (menos que el plazo) o retiene mucho (más que el latido).

Lo que se comprueba al final, sobre todas las órdenes:

1. **Ninguna hecha dos veces.**
2. **Ninguna fantasma**: ninguna hecha después de que la nube dijera que falló.
3. **Ninguna en el PC de otra persona.**
4. **Ninguna colgada**: todas acaban con una respuesta.
5. **Nada que se acumule**: los registros de la nube (órdenes en vuelo, estados,
   consultas) y del agente (controles, órdenes en marcha) vuelven a cero, y la memoria
   del proceso no crece sin freno.
"""

import os
import random
import threading
import time
from collections import Counter

import pytest

from src.canal import despacho
from src.canal.registro import REGISTRO
from tests.proxy_fallos import ProxyDeFallos
from tests.test_canal_agente import _cuenta, _esperar, nube, servidor  # noqa: F401
from tests.test_resiliencia import _agente_por, _enviar

pytestmark = pytest.mark.skipif(os.environ.get("MORGAN_ESTRES") != "1",
                                reason="resistencia larga de la 3.6.5: solo con MORGAN_ESTRES=1")


def test_media_hora_de_fallos(nube, servidor, monkeypatch):
    import psutil

    minutos = float(os.environ.get("MORGAN_ESTRES_MINUTOS", "30"))
    monkeypatch.setattr(despacho, "ESPERA_REAPARICION", 20.0)
    proxy = ProxyDeFallos(int(servidor.rsplit(":", 1)[1]))
    personas = ("ana", "bea")
    cuentas = {n: _cuenta(nube, n) for n in personas}
    hechas: dict[str, list] = {n: [] for n in personas}      # (marca, cuándo)
    cerrojo = threading.Lock()
    agentes = {}
    for nombre, (_, _, user_id, agent_id, credencial) in cuentas.items():
        def apunta(marca: str, _n=nombre):
            time.sleep(random.uniform(0, 0.3))
            with cerrojo:
                hechas[_n].append((marca, time.monotonic()))
            return {"success": True, "data": {"marca": marca}, "error": None}
        agentes[nombre] = _agente_por(proxy, agent_id, credencial, {"apunta": apunta})
    for _, _, user_id, _, _ in cuentas.values():
        assert _esperar(lambda u=user_id: REGISTRO.de(u) is not None, 20)

    respuestas: dict[str, tuple] = {}                         # marca → (resultado, cuándo)
    fallos = Counter()
    proceso = psutil.Process()
    memoria_inicio = proceso.memory_info().rss / 2**20
    fin = time.monotonic() + minutos * 60
    parar = threading.Event()

    def mandar(nombre: str) -> None:
        user_id = cuentas[nombre][2]
        i = 0
        while not parar.is_set():
            i += 1
            marca = f"{nombre}-{i}"
            try:
                r = _enviar(user_id, "apunta", {"marca": marca}, plazo=random.choice((4, 8, 15)))
                respuestas[marca] = (r.get("estado"), time.monotonic())
            except despacho.ErrorDelCanal as exc:
                respuestas[marca] = (type(exc).__name__, time.monotonic())
            time.sleep(random.uniform(0.1, 0.6))

    hilos = [threading.Thread(target=mandar, args=(n,), daemon=True) for n in personas]
    for h in hilos:
        h.start()
    while time.monotonic() < fin:
        time.sleep(random.uniform(5, 15))
        fallo = random.choice(("cortar", "retener_poco", "retener_mucho"))
        fallos[fallo] += 1
        if fallo == "cortar":
            proxy.cortar()
        else:
            proxy.retener("ambos" if fallo == "retener_mucho" else "nube→pc")
            time.sleep(random.uniform(1, 3) if fallo == "retener_poco" else random.uniform(11, 16))
            proxy.soltar()
    parar.set()
    for h in hilos:
        h.join(120)
    time.sleep(3)

    # 1-3: sobre todas las órdenes.
    todas = {m: t for n in personas for m, t in hechas[n]}
    veces = Counter(m for n in personas for m, _ in hechas[n])
    duplicadas = [m for m, c in veces.items() if c > 1]
    cruzadas = [m for n in personas for m, _ in hechas[n] if not m.startswith(n)]
    fantasmas = [m for m, (estado, cuando) in respuestas.items()
                 if estado != "COMPLETED" and m in todas and todas[m] > cuando]
    colgadas = sum(1 for n in personas for _ in range(0))       # las hay si un hilo no acabó
    estados = Counter(e for e, _ in respuestas.values())
    memoria_fin = proceso.memory_info().rss / 2**20

    # 5: nada acumulado en la nube ni en los agentes.
    en_vuelo = {n: (len(c.esperando), len(c.estados), len(c.consultas))
                for n, c in ((n, REGISTRO.de(cuentas[n][2])) for n in personas) if c}
    en_agentes = {n: (len(canal.ejecutor._controles), len(canal.ejecutor._corriendo))
                  for n, (canal, _) in agentes.items()}

    informe = (f"\n{minutos:.0f} min · fallos {dict(fallos)} · órdenes {len(respuestas)}: {dict(estados)}"
               f"\nhechas {len(todas)} · duplicadas {len(duplicadas)} · fantasmas {len(fantasmas)} · "
               f"cruzadas {len(cruzadas)} · hilos colgados {sum(h.is_alive() for h in hilos)}"
               f"\nen vuelo en la nube {en_vuelo} · en los agentes {en_agentes}"
               f"\nmemoria del proceso {memoria_inicio:.0f} → {memoria_fin:.0f} MB")
    print(informe)

    for canal, hilo in agentes.values():
        canal.parar()
        hilo.join(10)
    proxy.cerrar()

    assert not duplicadas, duplicadas[:5]
    assert not fantasmas, fantasmas[:5]
    assert not cruzadas, cruzadas[:5]
    assert not any(h.is_alive() for h in hilos), "órdenes colgadas"
    assert all(v == (0, 0, 0) for v in en_vuelo.values()), en_vuelo
    assert all(v == (0, 0) for v in en_agentes.values()), en_agentes
    assert memoria_fin - memoria_inicio < 150, "la memoria crece sin freno"
