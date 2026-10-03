"""
La auditoría del agente, en el PC (3.0-D).

La otra mitad del §21 del plan: la nube anota qué pidió y el agente qué recibió, qué
autorizó o rechazó y qué ejecutó, con el mismo `request_id` y `command_id`. Si la
nube dice que algo se ejecutó y aquí no hay rastro, es un incidente.

Un fichero de líneas JSON en la carpeta del agente (`auditoria.jsonl`). Los argumentos
se recortan como en la auditoría de la nube: aquí se anota qué se hizo, no se copia
lo que se leyó.
"""

import json
import time

from src.agente import estado as almacen

LARGO_MAXIMO = 250


def _recortar(valor) -> str:
    texto = json.dumps(valor, ensure_ascii=False) if not isinstance(valor, str) else valor
    return texto[:LARGO_MAXIMO]


def anotar(fase: str, orden: dict | None = None, **datos) -> None:
    """Una línea: `recibida`, `rechazada`, `ejecutada`, `repetida`, `conectado`…"""
    orden = orden or {}
    entrada = {
        "momento": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fase": fase,
        "request_id": orden.get("request_id"),
        "command_id": orden.get("command_id"),
        "capability": orden.get("capability"),
    }
    if "arguments" in orden:
        entrada["arguments"] = _recortar(orden["arguments"])
    entrada.update({k: (_recortar(v) if isinstance(v, (dict, list)) else v) for k, v in datos.items()})
    carpeta = almacen.carpeta()
    carpeta.mkdir(parents=True, exist_ok=True)
    with open(carpeta / "auditoria.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def leer(ultimas: int = 50) -> list[dict]:
    try:
        lineas = (almacen.carpeta() / "auditoria.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(linea) for linea in lineas[-ultimas:] if linea.strip()]
