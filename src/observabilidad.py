"""
En qué se le va el tiempo a Morgan (V2.0).

**Por qué existe.** Una pregunta trivial tardaba 64 segundos en producción y
1,85 en local, con el mismo código. Averiguar por qué costó una hora:
levantar Morgan en local, medir lo mismo, medir el proveedor de modelo aparte y
restar la red. Salió un diagnóstico razonable, y no era una medición: era un
razonamiento. Está en `docs/mediciones.md`.

Ese trabajo hay que hacerlo una vez por cada cosa lenta que aparezca. Con
tiempos por etapa se responde leyendo una línea, y eso es todo lo que hace este
módulo.

## Cómo se usa

Se abre una medición por petición y se marcan las etapas donde estén:

```python
with midiendo() as medicion:
    with etapa("modelo"):
        respuesta = proveedor.generate(...)
    print(medicion.resumen())
```

Las etapas **se acumulan**: tres llamadas al modelo en un turno suman en
`modelo` y anotan que fueron tres. Es lo que interesa saber —cuánto tiempo y
cuántas veces— y no el detalle de cada una.

## Por qué un `contextvars` y no un parámetro

Porque si fuera un parámetro habría que pasarlo por la firma de todo lo que
esté en el camino: la ruta, el agente, cada herramienta, el cliente de la base
de datos. Eso convierte una medición en un cambio que toca treinta ficheros, y
lo que es peor: cada función nueva tendría que acordarse de propagarlo, y la que
se olvide desaparece de la medición sin decirlo.

Es el mismo motivo por el que el usuario de la petición vive en un
`contextvars` y no en un argumento, y está explicado en `src/identidad/`.

## Qué NO es esto

No es un sistema de métricas. No hay Prometheus, ni OpenTelemetry, ni un panel.
Eso resuelve el problema de observar muchas máquinas a lo largo del tiempo, y
Morgan tiene una. Cuando eso deje de ser cierto, este módulo se sustituye por lo
que haga falta; hasta entonces, añadirlo sería complejidad sin causa.

Tampoco mide nada cuando no hay una medición abierta: en la CLI, en las pruebas
y en los trabajos de fondo, `etapa()` no cuesta más que una comprobación.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger(__name__)

#: A partir de aquí, una petición se anota en el registro aunque termine bien.
#: 3 segundos porque en producción una lectura cuesta ~1 s: por debajo de eso no
#: hay nada que investigar, y por encima empieza a notarse al usarlo.
LENTA_SEGUNDOS = 3.0


@dataclass
class Medicion:
    """Lo que ha tardado cada parte de una petición.

    Es un objeto mutable compartido a propósito. El turno del agente puede
    correr en otro hilo —ver `_sin_esperar_de_mas` en la ruta de chat— y el hilo
    recibe una copia del contexto, pero **el mismo objeto**: así lo que mida
    sigue llegando aquí.

    La contrapartida es que se puede leer mientras alguien escribe, y por eso
    `resumen()` copia antes de recorrer.
    """

    inicio: float = field(default_factory=time.perf_counter)
    #: nombre de la etapa -> [segundos acumulados, cuántas veces]
    etapas: dict[str, list] = field(default_factory=dict)
    #: Datos sueltos que no son tiempos: qué proveedor respondió, por ejemplo.
    datos: dict[str, str] = field(default_factory=dict)
    #: Cuántas veces ha pasado algo. Se cuenta aparte de `etapas` porque un
    #: contador no lleva tiempo asociado y sumarlo al reparto lo falsearía:
    #: `resto` se calcula restando lo medido del total.
    conteos: dict[str, int] = field(default_factory=dict)
    #: Tokens que costó la petición, sumando todas las llamadas al modelo (4.1.5): entrada
    #: y salida. El objetivo de la 4.x es un Morgan eficiente, y sin esto el coste de un
    #: turno solo se sabía por clave y por día, nunca por turno.
    tokens: list = field(default_factory=lambda: [0, 0])

    def sumar(self, nombre: str, segundos: float) -> None:
        acumulado = self.etapas.setdefault(nombre, [0.0, 0])
        acumulado[0] += segundos
        acumulado[1] += 1

    def contar(self, nombre: str) -> None:
        self.conteos[nombre] = self.conteos.get(nombre, 0) + 1

    @property
    def total(self) -> float:
        return time.perf_counter() - self.inicio

    def resumen(self) -> dict:
        """Los tiempos en milisegundos, con el resto ya calculado.

        `resto` es lo que no está en ninguna etapa: el trabajo de Morgan que no
        es esperar a nadie. En producción salió que era la mayor parte, y ese
        fue el hallazgo.
        """
        # Copia antes de recorrer: puede haber un hilo escribiendo.
        etapas = {n: list(v) for n, v in list(self.etapas.items())}
        total = self.total
        medido = sum(segundos for segundos, _ in etapas.values())

        salida = {
            "total_ms": round(total * 1000),
            "resto_ms": max(0, round((total - medido) * 1000)),
        }
        for nombre, (segundos, veces) in sorted(etapas.items()):
            salida[f"{nombre}_ms"] = round(segundos * 1000)
            if veces > 1:
                salida[f"{nombre}_veces"] = veces

        conteos = dict(list(self.conteos.items()))
        if conteos:
            # Anidados y no sueltos: son once claves en un turno y ensucian el
            # resumen si se mezclan con los tiempos.
            salida["conteos"] = dict(sorted(conteos.items()))
        if self.tokens[0] or self.tokens[1]:
            salida["tokens_entrada"], salida["tokens_salida"] = self.tokens[0], self.tokens[1]
        return {**salida, **self.datos}

    def cabecera(self) -> str:
        """El resumen en una línea, para una cabecera HTTP.

        Formato: `total=1420;bd=310x11;modelo=520x3;resto=590`. Se puede leer de
        un vistazo en las herramientas del navegador, que es de donde salió la
        necesidad: ver por qué la web va lenta sin tener que instrumentar la web.

        **La `x3` es el número de veces**, y solo sale cuando es más de una.

        Se añadió porque sin ella el primer punto de la 2.1 —«cuántas llamadas
        al modelo hace un turno de verdad»— **no se podía contestar desde
        fuera**. El dato existía en `resumen()`, que solo se registra cuando la
        petición pasa de lenta, así que un turno normal no lo decía en ninguna
        parte. Contar tres llamadas de 170 ms no es lo mismo que una de 510: lo
        primero se arregla haciendo menos viajes y lo segundo no se arregla.
        """
        r = self.resumen()
        trozos = [f"total={r['total_ms']}"]
        for clave, valor in r.items():
            if not clave.endswith("_ms") or clave == "total_ms":
                continue
            nombre = clave[:-3]
            veces = r.get(f"{nombre}_veces")
            trozos.append(f"{nombre}={valor}" + (f"x{veces}" if veces else ""))
        if self.tokens[0] or self.tokens[1]:
            trozos.append(f"tokens={self.tokens[0]}+{self.tokens[1]}")
        return ";".join(trozos)


_medicion: ContextVar[Medicion | None] = ContextVar("medicion_morgan", default=None)


def medicion_actual() -> Medicion | None:
    return _medicion.get()


@contextmanager
def midiendo() -> Iterator[Medicion]:
    """Abre una medición para todo lo que pase dentro."""
    medicion = Medicion()
    testigo = _medicion.set(medicion)
    try:
        yield medicion
    finally:
        _medicion.reset(testigo)


@contextmanager
def etapa(nombre: str) -> Iterator[None]:
    """Suma a `nombre` el tiempo de este bloque.

    Si no hay medición abierta no hace nada, y eso importa: la CLI, las pruebas
    y los trabajos de fondo pasan por el mismo código y no tienen por qué pagar
    una medición que nadie va a leer.
    """
    medicion = _medicion.get()
    if medicion is None:
        yield
        return

    inicio = time.perf_counter()
    try:
        yield
    finally:
        # En el `finally` a propósito: una herramienta que falla también ha
        # gastado tiempo, y esconderlo dejaría un hueco en `resto` sin explicar.
        medicion.sumar(nombre, time.perf_counter() - inicio)


def anotar(nombre: str, valor: str) -> None:
    """Deja un dato que no es un tiempo. Por ejemplo, quién respondió."""
    medicion = _medicion.get()
    if medicion is not None:
        medicion.datos[nombre] = valor


def sumar_tokens(entrada: int, salida: int) -> None:
    """Suma lo que costó una llamada al modelo a la petición en curso (4.1.5)."""
    medicion = _medicion.get()
    if medicion is not None:
        medicion.tokens[0] += max(0, int(entrada or 0))
        medicion.tokens[1] += max(0, int(salida or 0))


def contar(nombre: str) -> None:
    """Suma uno a un contador.

    Existe para responder «¿por qué once consultas?». El tiempo total en la base
    ya se medía; lo que faltaba era **de dónde salen**, y para eso hace falta
    contar por tabla y operación, no medir once cosas por separado.
    """
    medicion = _medicion.get()
    if medicion is not None:
        medicion.contar(nombre)


# --- Objetivos de tiempo por tipo de turno (2.3-D) -----------------------------
#
# p95 que un turno no debería pasar, **salidos de medir** (docs/mediciones.md,
# apartado 5): 15 turnos reales con el modelo real, más ~0,5 s de lo que añade
# producción (la base por HTTP) y margen. Medido:
#
#   simple        p95 1,3 s  →  objetivo  5 s  (ver abajo)
#   herramienta   p95 3,0 s  →  objetivo  6 s
#   plan          p95 5,8 s  →  objetivo 10 s
#
# El de simple empezó en 3 s y **saltó dos veces con turnos normales**: la
# primera pregunta de alguien nuevo («¿qué puedes hacer y qué no?») tiene una
# respuesta larga, y el modelo solo tardó 3,0 y 3,3 s en escribirla. Los 1,3 s
# salían de una pregunta que pedía dos frases. Un aviso que salta en lo normal
# enseña a ignorarlo.
#
# Un turno que los pasa deja un aviso en el registro con su reparto, que es lo
# que hace falta para saber por qué sin tener que reproducirlo. `scripts/
# medir_turnos.py --objetivos` falla si el p95 medido los incumple.

OBJETIVOS_TURNO: dict[str, float] = {"simple": 5.0, "herramienta": 6.0, "plan": 10.0}


def tipo_de_turno(medicion: "Medicion") -> str:
    """Qué clase de turno fue, por lo que pasó y no por lo que se pidió.

    Las herramientas se miden como etapas `herramienta.<nombre>`, así que basta
    con mirarlas: si se creó un plan, es de plan; si se usó cualquier otra, de
    herramienta; si ninguna, simple.
    """
    usadas = [n.split(".", 1)[1] for n in list(medicion.etapas) if n.startswith("herramienta.")]
    if "create_plan" in usadas:
        return "plan"
    return "herramienta" if usadas else "simple"


def vigilar_turno(medicion: "Medicion | None", segundos: float) -> str | None:
    """Avisa en el registro si el turno pasó de su objetivo. Devuelve el tipo si avisó."""
    if medicion is None:
        return None
    tipo = tipo_de_turno(medicion)
    objetivo = OBJETIVOS_TURNO[tipo]
    if segundos <= objetivo:
        return None
    logging.getLogger(__name__).warning(
        "Turno fuera de objetivo: %s, %.1f s (objetivo %.0f s). Reparto: %s",
        tipo, segundos, objetivo, medicion.cabecera(),
    )
    return tipo
