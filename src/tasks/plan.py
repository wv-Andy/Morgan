"""
Planificación: separar decidir qué hacer de hacerlo (V1.6).

Hasta aquí, Morgan ejecutaba mientras pensaba. Cada herramienta se decidía y se
lanzaba en el mismo movimiento, así que cuando algo salía mal ya estaba hecho, y
para autorizar había que ir preguntando de una en una — o denegar, si no había
consola.

La V1.6 mete un paso en medio:

    Petición → Comprensión → PLANIFICACIÓN → Plan → Permiso → Ejecución

El plan es una lista de pasos que **todavía no se han ejecutado**, cada uno con la
herramienta que usará y lo que le va a pasar. Eso permite tres cosas que antes no
se podían:

1. **Enseñarlo entero antes de empezar.** Una persona puede decidir sobre «voy a
   borrar estos tres archivos y reescribir este otro» mucho mejor que sobre tres
   preguntas sueltas separadas por minutos.
2. **Calcular el riesgo de lo que viene**, no del paso actual. Un plan cuyo último
   paso borra algo es un plan arriesgado desde el principio, aunque empiece
   leyendo.
3. **Rechazarlo sin efectos.** Rechazar a mitad deja el trabajo hecho a medias;
   rechazar un plan no deja nada.

## Lo que NO hace

**No obliga a planificar lo trivial.** Responder una pregunta, leer un archivo o
buscar en la web se ejecuta directo, como siempre. Meter un plan de un paso entre
la pregunta y la respuesta no aporta control y sí fricción: `requiere_plan` es lo
que decide, y su criterio es el riesgo, no el número de pasos.

**No sustituye al `PermissionManager`.** Reutiliza sus cinco niveles y su decisión,
que ya distinguen lo confirmable de lo que no. Aquí se agrega ese juicio a nivel de
plan; quien manda sobre cada ejecución concreta sigue siendo él.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.tools.base import RiskLevel

# Orden de gravedad. Hace falta explícito porque el enum no lo trae, y sin él
# «el riesgo del plan» no se puede calcular: no habría con qué comparar.
GRAVEDAD: dict[str, int] = {
    RiskLevel.SAFE.value: 0,
    "safe": 0,
    RiskLevel.LOW_RISK.value: 1,
    RiskLevel.MODERATE.value: 2,
    "moderate": 2,
    RiskLevel.HIGH_RISK.value: 3,
    RiskLevel.CRITICAL.value: 4,
    "critical": 4,
    "sensitive": 4,
}

# A partir de aquí, un plan no se ejecuta sin que alguien diga que sí.
UMBRAL_CONFIRMACION = GRAVEDAD[RiskLevel.MODERATE.value]


class EstadoPlan(str, Enum):
    BORRADOR = "borrador"          # se está armando
    PENDIENTE = "pendiente"        # esperando que alguien lo apruebe
    APROBADO = "aprobado"          # listo para ejecutarse
    EJECUTANDO = "ejecutando"
    COMPLETADO = "completado"
    FALLIDO = "fallido"
    RECHAZADO = "rechazado"        # alguien dijo que no

    def __str__(self) -> str:
        return self.value

    @property
    def terminal(self) -> bool:
        return self in (
            EstadoPlan.COMPLETADO, EstadoPlan.FALLIDO, EstadoPlan.RECHAZADO
        )


@dataclass
class PasoPlaneado:
    """Un paso que **todavía no se ha ejecutado**.

    La diferencia con `TaskStep` es justo esa, y es la razón de que sean tipos
    distintos: uno describe una intención y el otro un hecho. Mezclarlos llevaría
    a preguntarse, mirando una lista, cuáles ya pasaron.
    """

    orden: int
    descripcion: str
    herramienta: str | None = None
    argumentos: dict[str, Any] = field(default_factory=dict)
    riesgo: str = RiskLevel.SAFE.value
    # Órdenes de los pasos de los que depende. Sirve para no ejecutar el tercero
    # si el primero falló, y para poder explicar por qué no se ejecutó.
    depende_de: list[int] = field(default_factory=list)
    motivo: str | None = None
    #: Si este paso ya se ejecutó con la autorización del plan (V2.0.16). Es lo que
    #: impide que una aprobación sirva dos veces: aprobar «crear el evento de las
    #: 10» no autoriza a crearlo dos veces.
    ejecutado: bool = False

    @property
    def gravedad(self) -> int:
        return GRAVEDAD.get(str(self.riesgo).lower(), UMBRAL_CONFIRMACION)

    @property
    def necesita_confirmacion(self) -> bool:
        return self.gravedad >= UMBRAL_CONFIRMACION

    def to_dict(self) -> dict[str, Any]:
        return {
            "orden": self.orden,
            "descripcion": self.descripcion,
            "herramienta": self.herramienta,
            # Los argumentos NO se publican en crudo: pueden traer rutas del
            # equipo, contenido de archivos o cualquier cosa que el modelo haya
            # metido ahi. Se resumen para poder decidir sin exponerlo todo.
            "argumentos": _resumir(self.argumentos),
            "riesgo": self.riesgo,
            "depende_de": list(self.depende_de),
            "motivo": self.motivo,
            "necesita_confirmacion": self.necesita_confirmacion,
            "ejecutado": self.ejecutado,
        }

    def to_almacen(self) -> dict[str, Any]:
        """Lo que se guarda: los argumentos **enteros**, no el `to_dict` publicable.

        Ese recorta los argumentos y enmascara secretos para enseñarlos, y guardar
        eso significaría ejecutar después con argumentos truncados. Estaba escrito
        dos veces, una por repositorio; un campo nuevo habría tenido que acordarse
        de las dos.
        """
        return {
            "orden": self.orden,
            "descripcion": self.descripcion,
            "herramienta": self.herramienta,
            "argumentos": self.argumentos,
            "riesgo": self.riesgo,
            "depende_de": self.depende_de,
            "motivo": self.motivo,
            "ejecutado": self.ejecutado,
        }

    @classmethod
    def from_dict(cls, datos: dict[str, Any]) -> "PasoPlaneado":
        return cls(
            orden=int(datos.get("orden", 0)),
            descripcion=datos.get("descripcion", ""),
            herramienta=datos.get("herramienta"),
            argumentos=datos.get("argumentos") or {},
            riesgo=datos.get("riesgo", RiskLevel.SAFE.value),
            depende_de=list(datos.get("depende_de") or []),
            motivo=datos.get("motivo"),
            ejecutado=bool(datos.get("ejecutado", False)),
        )


def _resumir(argumentos: dict[str, Any], limite: int = 120) -> dict[str, str]:
    """Los argumentos, recortados y sin secretos, para poder enseñarlos.

    Quien aprueba un plan necesita ver **qué archivo** o **qué comando**, no el
    contenido entero de nada.
    """
    from src.tools.terminal import SECRET_KEYWORDS

    resumen: dict[str, str] = {}
    for clave, valor in (argumentos or {}).items():
        if any(secreto in str(clave).upper() for secreto in SECRET_KEYWORDS):
            resumen[clave] = "********"
            continue
        if _son_pasos(valor):
            # Los pasos fijos de una automatización (4.15): lo que se aprueba es justo eso, así
            # que se enseñan enteros, uno por línea, con cada argumento recortado aparte.
            # Recortado todo junto a 120 caracteres, la persona no veía qué aprobaba.
            resumen[clave] = "\n".join(
                f"{i}. {p.get('herramienta')}: " + "; ".join(
                    f"{k}={_resumir({k: v}, limite)[k]}" for k, v in (p.get("argumentos") or {}).items())
                for i, p in enumerate(valor, start=1))
            continue
        texto = str(valor)
        resumen[clave] = texto if len(texto) <= limite else texto[:limite] + "..."

    return resumen


def _son_pasos(valor: Any) -> bool:
    return (isinstance(valor, list) and bool(valor)
            and all(isinstance(p, dict) and "herramienta" in p for p in valor))


@dataclass
class Plan:
    """Lo que Morgan piensa hacer, antes de hacerlo."""

    id: str
    objetivo: str
    pasos: list[PasoPlaneado] = field(default_factory=list)
    estado: str = EstadoPlan.BORRADOR.value
    session_id: str | None = None
    task_id: str | None = None
    creado_en: float = field(default_factory=time.time)
    decidido_en: float | None = None
    decidido_por: str | None = None
    motivo_rechazo: str | None = None

    @property
    def riesgo(self) -> str:
        """El riesgo del plan es **el del paso más arriesgado**.

        No un promedio ni el del primero: un plan que lee dos archivos y luego
        borra un directorio es un plan que borra un directorio, y presentarlo
        como «riesgo bajo con algún paso delicado» sería engañar a quien decide.
        """
        if not self.pasos:
            return RiskLevel.SAFE.value

        peor = max(self.pasos, key=lambda p: p.gravedad)
        return peor.riesgo

    @property
    def necesita_aprobacion(self) -> bool:
        return any(p.necesita_confirmacion for p in self.pasos)

    @property
    def ejecutable(self) -> bool:
        """Si puede ejecutarse ya.

        Un plan sin pasos arriesgados no espera a nadie: hacer aprobar «voy a
        leer tres archivos» convierte la aprobación en un trámite que se acepta
        sin mirar, y entonces deja de proteger de nada.
        """
        return self.estado == EstadoPlan.APROBADO.value or (
            self.estado in (EstadoPlan.BORRADOR.value, EstadoPlan.PENDIENTE.value)
            and not self.necesita_aprobacion
        )

    def resumen(self) -> str:
        """El plan en texto, para enseñárselo al modelo o escribirlo en un log."""
        lineas = [f"Plan: {self.objetivo}"]
        for paso in sorted(self.pasos, key=lambda p: p.orden):
            # Marcas ASCII a proposito: este texto acaba en el log y en la
            # consola de Windows, donde cp1252 no sabe escribir un simbolo de
            # aviso y revienta la linea entera.
            marca = "!" if paso.necesita_confirmacion else "-"
            herramienta = f" [{paso.herramienta}]" if paso.herramienta else ""
            lineas.append(f"  {marca} {paso.orden}. {paso.descripcion}{herramienta}")
        return "\n".join(lineas)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objetivo": self.objetivo,
            "pasos": [p.to_dict() for p in sorted(self.pasos, key=lambda x: x.orden)],
            "estado": self.estado,
            "riesgo": self.riesgo,
            "necesita_aprobacion": self.necesita_aprobacion,
            "ejecutable": self.ejecutable,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "creado_en": self.creado_en,
            "decidido_en": self.decidido_en,
            "decidido_por": self.decidido_por,
            "motivo_rechazo": self.motivo_rechazo,
        }

    @classmethod
    def from_dict(cls, datos: dict[str, Any]) -> "Plan":
        return cls(
            id=datos["id"],
            objetivo=datos.get("objetivo", ""),
            pasos=[PasoPlaneado.from_dict(p) for p in datos.get("pasos") or []],
            estado=datos.get("estado", EstadoPlan.BORRADOR.value),
            session_id=datos.get("session_id"),
            task_id=datos.get("task_id"),
            creado_en=datos.get("creado_en") or 0.0,
            decidido_en=datos.get("decidido_en"),
            decidido_por=datos.get("decidido_por"),
            motivo_rechazo=datos.get("motivo_rechazo"),
        )


# --- Transiciones -------------------------------------------------------------
#
# Las mismas razones que en las tareas: sin esto, un plan podría pasar de
# rechazado a ejecutando y nadie se enteraría hasta ver el destrozo.

TRANSICIONES: dict[str, tuple[str, ...]] = {
    EstadoPlan.BORRADOR.value: (
        EstadoPlan.PENDIENTE.value, EstadoPlan.APROBADO.value,
        EstadoPlan.EJECUTANDO.value, EstadoPlan.RECHAZADO.value,
    ),
    EstadoPlan.PENDIENTE.value: (
        EstadoPlan.APROBADO.value, EstadoPlan.RECHAZADO.value,
    ),
    EstadoPlan.APROBADO.value: (
        EstadoPlan.EJECUTANDO.value, EstadoPlan.RECHAZADO.value,
    ),
    EstadoPlan.EJECUTANDO.value: (
        EstadoPlan.COMPLETADO.value, EstadoPlan.FALLIDO.value,
    ),
    EstadoPlan.COMPLETADO.value: (),
    EstadoPlan.FALLIDO.value: (),
    EstadoPlan.RECHAZADO.value: (),
}


class TransicionInvalida(ValueError):
    """Se intentó llevar un plan a un estado al que no puede ir desde donde está."""

    def __init__(self, desde: str, hasta: str):
        super().__init__(
            f"Un plan '{desde}' no puede pasar a '{hasta}'."
        )


def puede_pasar(desde: str, hasta: str) -> bool:
    return hasta in TRANSICIONES.get(desde, ())


def exigir_transicion(desde: str, hasta: str) -> None:
    if not puede_pasar(desde, hasta):
        raise TransicionInvalida(desde, hasta)
