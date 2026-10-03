"""
Lo que el agente guarda en el PC, y en qué estado está (3.0-C).

Vive en `%LOCALAPPDATA%\\Morgan\\agente\\` (o en `MORGAN_AGENTE_DIR`, para pruebas):

| Fichero | Qué | Cómo |
|---|---|---|
| `agente.json` | `agent_id`, a qué nube, cómo se llama, con qué cuenta | Texto: no hay nada secreto |
| `credencial.bin` | La credencial `mga_…` | **Cifrada con DPAPI** (`credencial.py`) |

**Ningún dato de Morgan** (conversaciones, memoria): la nube es la fuente de verdad
(decisión 8). La política local y la auditoría del agente llegan en la 3.0-E y la
3.0-D.
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from src.agente import credencial as almacen


class EstadoAgente(str, Enum):
    """El ciclo de vida del §7 del plan. `CONNECTED` no es `READY`."""

    UNPAIRED = "UNPAIRED"
    PAIRING = "PAIRING"
    PAIRED = "PAIRED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTED = "CONNECTED"
    READY = "READY"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    REVOKED = "REVOKED"
    OUTDATED = "OUTDATED"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass
class Emparejamiento:
    agent_id: str
    nube: str
    nombre: str
    cuenta: str
    emparejado_en: float


def carpeta() -> Path:
    propia = os.environ.get("MORGAN_AGENTE_DIR")
    if propia:
        return Path(propia)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Morgan" / "agente"


def _datos() -> Path:
    return carpeta() / "agente.json"


def _credencial() -> Path:
    return carpeta() / "credencial.bin"


def escribir_atomico(ruta: Path, datos: bytes) -> None:
    """Un temporal junto al destino y el cambio de golpe (3.6): un apagón a mitad deja el
    fichero de antes, nunca uno a medias. Leído en el código al preparar la 3.6: la
    política y el emparejamiento se escribían de una vez, y uno cortado a mitad dejaba el
    PC cerrado (la política no se entendía) o sin emparejar."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    with open(temporal, "wb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporal, ruta)


def guardar(emparejamiento: Emparejamiento, credencial: str) -> None:
    """Deja el agente emparejado. La credencial, cifrada; si no se puede, nada."""
    cifrada = almacen.cifrar(credencial)  # primero: si falla, no queda nada a medias
    escribir_atomico(_credencial(), cifrada)
    escribir_atomico(_datos(), json.dumps(asdict(emparejamiento), ensure_ascii=False, indent=2).encode("utf-8"))


def guardar_credencial(credencial: str) -> None:
    """Solo la credencial (3.8, al rotarla). Cifrada y de una vez: nunca a medias."""
    escribir_atomico(_credencial(), almacen.cifrar(credencial))


def cargar() -> Emparejamiento | None:
    try:
        return Emparejamiento(**json.loads(_datos().read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError, TypeError):
        return None


def credencial() -> str | None:
    try:
        return almacen.descifrar(_credencial().read_bytes())
    except FileNotFoundError:
        return None


def estado() -> EstadoAgente:
    """El estado en reposo: emparejado o no. La conexión (3.0-D) lo lleva más lejos."""
    return EstadoAgente.PAIRED if cargar() and _credencial().exists() else EstadoAgente.UNPAIRED


def borrar() -> None:
    """Olvida el emparejamiento. Primero la credencial, que es lo que abre la puerta."""
    for fichero in (_credencial(), _datos()):
        try:
            fichero.unlink()
        except FileNotFoundError:
            pass


def ahora() -> float:
    return time.time()
