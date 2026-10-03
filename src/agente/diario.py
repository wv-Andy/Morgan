"""
El diario de órdenes del PC (3.4): qué pidió la nube, en qué estado está y cómo acabó.

**En disco**, en la carpeta del agente (`diario.json`): sobrevive a un corte y a
reiniciar el agente. Es lo que permite que la nube, tras un corte, **pregunte** qué pasó
con una orden en vez de adivinarlo (antes decía «no se sabe si llegó a hacerse»), y que
una orden repetida con el mismo `command_id` no se haga dos veces (reproducido en la
3.4-A: la memoria de órdenes era de RAM y se apuntaba al terminar; con un corte a
mitad, la orden repetida se ejecutaba otra vez).

## Qué guarda, decidido por mí (2026-09-24)

**24 horas.** De cada orden: la capacidad, los argumentos recortados (qué se pidió),
el estado y sus tiempos. **El resultado, solo de las que cambian cosas** (escrituras):
de una lectura se guarda cómo acabó, **nunca lo leído**. Repetir una lectura no hace
daño; dejar copias de tus archivos en el diario, sí.

## Solo se añade (3.4.5)

Cada cambio de estado **añade una línea** (`diario.jsonl`); al arrancar se lee entero,
gana la última línea de cada orden, se tira lo de más de 24 h y se reescribe compacto.
**Medido en la 3.4.5**: la primera versión reescribía el fichero entero en cada cambio,
y el coste crecía con el diario —una orden `estado` tardaba 4,3 ms en el agente con el
diario vacío, 11 ms con 1.000 órdenes y **39 ms con 5.000**—. Añadir una línea no
depende de cuánto haya.
"""

import json
import os
import threading
import time

from src.agente import estado as almacen

DURA = 24 * 3600
LARGO_ARGUMENTOS = 250

#: Las que no cambian nada: repetirlas es inofensivo, y su resultado no se guarda.
REPETIBLES = frozenset({"estado", "system_info", "list_files", "read_file", "search_files", "copy_file"})

#: Estados del plan (§V3.4). `REJECTED`: ni siquiera entró.
PENDING, RUNNING, COMPLETED, FAILED = "PENDING", "RUNNING", "COMPLETED", "FAILED"
CANCEL_REQUESTED, CANCELLED, REJECTED = "CANCEL_REQUESTED", "CANCELLED", "REJECTED"
TERMINALES = frozenset({COMPLETED, FAILED, CANCELLED, REJECTED})


def _recortar(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)[:LARGO_ARGUMENTOS]


class Diario:
    def __init__(self) -> None:
        self._cerrojo = threading.Lock()
        self._entradas = self._leer()
        # Lo que estaba a medias cuando el agente se paró **no terminó aquí**: el
        # proceso que lo corría ya no existe. Se dice tal cual, no se deja «corriendo»
        # para siempre.
        cambiadas = False
        for entrada in self._entradas.values():
            if entrada.get("estado") not in TERMINALES:
                entrada.update(estado=FAILED, motivo="agente_reiniciado", termina=time.time())
                cambiadas = True
        if cambiadas:
            self._compactar(self._entradas)

    @staticmethod
    def _fichero():
        return almacen.carpeta() / "diario.jsonl"

    def _leer(self) -> dict:
        entradas: dict = {}
        # El de la 3.4.0 (un JSON entero), si lo hay: se toma y se deja de usar.
        viejo = almacen.carpeta() / "diario.json"
        try:
            datos = json.loads(viejo.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                entradas.update({k: v for k, v in datos.items() if isinstance(v, dict)})
        except (OSError, ValueError):
            pass
        try:
            lineas = self._fichero().read_text(encoding="utf-8").splitlines()
        except OSError:
            lineas = []
        for linea in lineas:
            try:
                registro = json.loads(linea)
            except ValueError:
                continue                # una línea a medias (un corte de luz): se salta
            if isinstance(registro, dict) and isinstance(registro.get("command_id"), str):
                entradas[registro.pop("command_id")] = registro
        limite = time.time() - DURA
        entradas = {k: v for k, v in entradas.items() if v.get("recibida", 0) > limite}
        self._compactar(entradas)
        viejo.unlink(missing_ok=True)
        return entradas

    def _compactar(self, entradas: dict) -> None:
        """El diario reescrito con una línea por orden: al arrancar, no en cada cambio."""
        fichero = self._fichero()
        fichero.parent.mkdir(parents=True, exist_ok=True)
        temporal = fichero.with_suffix(".tmp")
        temporal.write_text("".join(json.dumps({"command_id": k, **v}, ensure_ascii=False) + "\n"
                                    for k, v in entradas.items()), encoding="utf-8")
        os.replace(temporal, fichero)            # nunca un diario a medio escribir
        self._lineas = len(entradas)

    def _apuntar(self, command_id: str) -> None:
        """Una línea con cómo está ahora esa orden. Si el fichero ha crecido mucho más que
        las órdenes que guarda, se compacta."""
        with open(self._fichero(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"command_id": command_id, **self._entradas[command_id]},
                               ensure_ascii=False) + "\n")
        self._lineas += 1
        if self._lineas > 4 * len(self._entradas) + 1000:
            limite = time.time() - DURA
            self._entradas = {k: v for k, v in self._entradas.items() if v.get("recibida", 0) > limite}
            self._compactar(self._entradas)

    def de(self, command_id: str) -> dict | None:
        with self._cerrojo:
            entrada = self._entradas.get(command_id)
            if entrada and entrada.get("recibida", 0) <= time.time() - DURA:
                return None             # más de 24 h: como si no estuviera
            return dict(entrada) if entrada else None

    def nueva(self, command_id: str, orden: dict) -> None:
        with self._cerrojo:
            self._entradas[command_id] = {
                "capability": str(orden.get("capability")),
                "request_id": orden.get("request_id"),
                "argumentos": _recortar(orden.get("arguments")),
                "estado": PENDING,
                "recibida": time.time(),
            }
            self._apuntar(command_id)

    def pasar(self, command_id: str, estado: str, *, resultado: dict | None = None, **campos) -> None:
        with self._cerrojo:
            entrada = self._entradas.get(command_id)
            if entrada is None:
                return
            entrada["estado"] = estado
            entrada.update({k: v for k, v in campos.items() if v is not None})
            if estado == RUNNING:
                entrada["empieza"] = time.time()
            if estado in TERMINALES:
                entrada["termina"] = time.time()
                if resultado is not None and entrada["capability"] not in REPETIBLES:
                    entrada["resultado"] = resultado
            self._apuntar(command_id)
