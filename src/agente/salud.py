"""
Cómo está el agente, a la vista (3.6): `salud.json` en su carpeta.

El agente en marcha apunta aquí **cada 5 s** un pulso (que su bucle sigue vivo), su
estado del canal, cuándo le llegó el último latido de la nube y su cola. Lo leen:

- `python -m src.agente estado`, para decirle a la persona si está **sano**,
  **reconectando** o **atascado** (antes solo decía si estaba en marcha: un agente
  colgado pasaba por sano);
- el **vigilante** (`vigilante.py`), para reiniciarlo si lleva más de 2 minutos sin pulso.
"""

import json
import os
import time

from src.agente import estado as almacen

#: Sin pulso en este tiempo, el agente está atascado (decisión mía: 2 minutos).
ATASCADO = 120.0
#: Cada cuánto apunta el agente su pulso.
PULSO = 5.0


def fichero():
    return almacen.carpeta() / "salud.json"


def leer(intentos: int = 5) -> dict:
    """Lo apuntado, o {} si no hay nada. **Reintenta**: en Windows, leer justo mientras el
    agente sustituye el fichero falla (medido en la 3.8: 962 de 70.134 lecturas con un
    escritor sin pausa), y el vigilante tomaba ese {} por «sin pulso»."""
    for intento in range(intentos):
        try:
            datos = json.loads(fichero().read_text(encoding="utf-8"))
            return datos if isinstance(datos, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            if intento < intentos - 1:
                time.sleep(0.01)
    return {}


def apuntar(**datos) -> None:
    """Añade lo que se sabe ahora. Nunca lanza: la salud no puede tumbar al agente."""
    try:
        actual = leer() if datos.get("pid") is None else {}
        actual.update({k: v for k, v in datos.items() if v is not None})
        actual.setdefault("pid", os.getpid())
        almacen.escribir_atomico(fichero(), json.dumps(actual, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass


def diagnostico(datos: dict | None = None, ahora: float | None = None, en_marcha: bool = True) -> str:
    """Una frase para la persona."""
    datos = leer() if datos is None else datos
    ahora = time.time() if ahora is None else ahora
    if not en_marcha:
        if datos.get("estado") == "RENDIDO":
            return ("Parado: se cayó demasiadas veces seguidas y el vigilante dejó de "
                    "levantarlo. Mira agente.log y arráncalo: python -m src.agente arranque activar")
        return "No está en marcha."
    pulso = datos.get("pulso")
    if not isinstance(pulso, (int, float)):
        return "Arrancando."
    if ahora - pulso > ATASCADO:
        return f"Atascado: sin señales desde hace {int(ahora - pulso)} s. El vigilante lo reiniciará."
    estado = datos.get("estado")
    desde = datos.get("desde")
    hace = f" desde hace {int(ahora - desde)} s" if isinstance(desde, (int, float)) else ""
    if estado == "READY":
        latido = datos.get("ultimo_latido")
        extra = f"; último latido de la nube hace {int(ahora - latido)} s" if isinstance(latido, (int, float)) else ""
        cola = datos.get("cola") or 0
        return f"Sano: conectado{hace}{extra}" + (f"; {cola} en cola" if cola else "") + "."
    if estado in ("RECONNECTING", "CONNECTING", "AUTHENTICATING"):
        return f"Reconectando{hace}. Si dura mucho, mira la red del PC."
    if estado in ("REVOKED", "INCOMPATIBLE"):
        return f"Parado por la nube ({estado}): revocado o desactualizado."
    return f"{estado or 'Sin estado'}{hace}."
