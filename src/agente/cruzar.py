"""
Cruzar las dos puntas (3.7): lo que la nube apuntó de este PC contra su propio diario.

La «auditoría en las dos puntas» del §14 de agente-local.md: la nube anota qué pidió y
el PC qué recibió y qué hizo, con el mismo `command_id`. Una orden que la nube da por
contestada y el PC no tiene, o que el PC hizo y la nube no apuntó, es un **incidente**
(§21 del plan). Hasta la 3.7 no había forma de verlo: la parte de la nube se perdía con
cada despliegue de Render.

    python -m src.agente cruzar

**Lee el diario sin tocarlo**: el agente puede estar escribiendo en él. Cargarlo como lo
carga el agente al arrancar marcaría como fallidas las órdenes que están en marcha.
"""

import json
import time
from typing import Callable

from src.agente import estado as almacen
from src.agente.diario import DURA

#: Lo que la nube apunta sin que la orden llegara al PC (o sin saber si llegó).
SIN_LLEGAR = {"no_conectado", "ocupado", "sin_respuesta", "desconectado"}


def leer_diario() -> dict:
    """El diario tal como está, **solo leyendo** (gana la última línea de cada orden)."""
    entradas: dict = {}
    try:
        lineas = (almacen.carpeta() / "diario.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return entradas
    for linea in lineas:
        try:
            registro = json.loads(linea)
        except ValueError:
            continue
        if isinstance(registro, dict) and isinstance(registro.get("command_id"), str):
            entradas[registro.pop("command_id")] = registro
    limite = time.time() - DURA
    return {k: v for k, v in entradas.items() if v.get("recibida", 0) > limite}


def comparar(nube: list[dict], diario: dict) -> dict:
    """Las diferencias entre las dos listas. Una orden recuperada tras un corte puede estar
    en el PC con `-r` detrás (se repitió con otro `command_id`, 3.4)."""
    en_la_nube = {o["command_id"]: o for o in nube if o.get("command_id")}
    solo_nube, distintas = [], []
    for cid, orden in en_la_nube.items():
        estado_nube = str(orden.get("estado") or "").lower()
        if estado_nube in SIN_LLEGAR:
            continue
        local = diario.get(cid) or diario.get(f"{cid}-r")
        if local is None:
            solo_nube.append(orden)
        elif str(local.get("estado") or "").lower() != estado_nube:
            distintas.append({**orden, "estado_pc": local.get("estado")})
    conocidas = set(en_la_nube) | {f"{c}-r" for c in en_la_nube}
    solo_pc = [{"command_id": cid, **e} for cid, e in diario.items()
               if cid not in conocidas and e.get("estado") != "REJECTED"]
    return {"solo_nube": solo_nube, "solo_pc": solo_pc, "distintas": distintas,
            "comparadas": len(en_la_nube)}


def cruzar(decir: Callable[[str], None] = print, http=None, horas: int = 24) -> int:
    """0 si las dos puntas cuadran; 1 si hay incidentes; 2 si no se pudo preguntar."""
    import httpx

    emparejamiento, credencial = almacen.cargar(), almacen.credencial()
    if emparejamiento is None or credencial is None:
        decir("Este PC no está emparejado.")
        return 2
    try:
        cliente = http or httpx.Client(timeout=30)
        r = cliente.get(f"{emparejamiento.nube}/agente/historial", params={"horas": horas},
                        headers={"Authorization": f"Bearer {credencial}"})
        r.raise_for_status()
        nube = r.json().get("ordenes") or []
    except Exception as exc:
        decir(f"No se pudo pedir el historial a la nube: {type(exc).__name__}.")
        return 2
    diferencias = comparar(nube, leer_diario())
    decir(f"Comparadas {diferencias['comparadas']} órdenes de las últimas {horas} h.")
    for orden in diferencias["solo_nube"]:
        decir(f"  INCIDENTE: la nube dice {orden.get('estado')} de {orden.get('capability')} "
              f"({orden['command_id'][:8]}) y este PC no tiene rastro.")
    for orden in diferencias["solo_pc"]:
        decir(f"  INCIDENTE: este PC hizo {orden.get('capability')} ({orden['command_id'][:8]}, "
              f"{orden.get('estado')}) y la nube no lo apuntó.")
    for orden in diferencias["distintas"]:
        decir(f"  Distinto: {orden.get('capability')} ({orden['command_id'][:8]}): la nube dice "
              f"{orden.get('estado')}, el PC {orden.get('estado_pc')}.")
    total = len(diferencias["solo_nube"]) + len(diferencias["solo_pc"]) + len(diferencias["distintas"])
    decir("Las dos puntas cuadran." if not total else f"{total} diferencias.")
    return 1 if total else 0
