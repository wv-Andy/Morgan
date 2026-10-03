"""
Modelo del sistema de tareas (V1.5).

Morgan pasa de `pregunta → respuesta` a
`objetivo → tarea → pasos → ejecución → resultado`.

La diferencia práctica no es cosmética. Hoy, si le pides algo que lleva ocho
llamadas a herramientas, ves un indicador girando durante minuto y medio y luego
un texto. Con tareas, cada paso queda registrado: qué se intentó, qué devolvió,
qué falló y qué quedó pendiente. Eso es lo que permite después planificar (V1.6) y
verificar (V1.7) — y también decir «no pude completar la tarea» en lugar de
afirmar falsamente que se completó.

Los estados salen del plan tal cual. `WAITING` es el que menos se explica solo:
es una tarea que no ha fallado ni terminado, sino que **espera algo externo** —
una confirmación del usuario, típicamente.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    PENDING = "pending"       # creada, aún no empezada
    RUNNING = "running"       # ejecutándose ahora
    WAITING = "waiting"       # detenida esperando algo externo (una confirmación)
    FAILED = "failed"         # terminó sin conseguirlo
    COMPLETED = "completed"   # terminó bien
    CANCELLED = "cancelled"   # la detuvo el usuario

    def __str__(self) -> str:
        return self.value

    @property
    def terminal(self) -> bool:
        """Si ya no va a cambiar por sí sola.

        Sirve para no reanudar lo que ya acabó y para saber qué se puede reintentar.
        """
        return self in (TaskState.FAILED, TaskState.COMPLETED, TaskState.CANCELLED)


# Estados desde los que tiene sentido reintentar. Una tarea completada no se
# reintenta —ya está hecha— y una en marcha tampoco: habría dos a la vez.
REINTENTABLES = (TaskState.FAILED, TaskState.CANCELLED)


@dataclass
class TaskStep:
    """Un paso dentro de una tarea.

    Se registra aunque falle: el historial de lo que no funcionó es justo lo que
    hace falta para diagnosticar, y es sobre lo que se construirá la V1.7.
    """

    orden: int
    descripcion: str
    estado: str = TaskState.PENDING.value
    herramienta: str | None = None
    resultado: str | None = None
    error: str | None = None
    iniciado_en: float | None = None
    terminado_en: float | None = None
    # Veredicto de la verificacion (V1.7). `None` significa que no se comprobo,
    # que es distinto de que saliera bien: sin esa distincion, "no lo se" acaba
    # contandose como "correcto".
    verificacion: str | None = None
    verificacion_motivo: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "orden": self.orden,
            "descripcion": self.descripcion,
            "estado": self.estado,
            "herramienta": self.herramienta,
            "resultado": self.resultado,
            "error": self.error,
            "iniciado_en": self.iniciado_en,
            "terminado_en": self.terminado_en,
            "verificacion": self.verificacion,
            "verificacion_motivo": self.verificacion_motivo,
        }

    @classmethod
    def from_dict(cls, datos: dict[str, Any]) -> "TaskStep":
        return cls(
            orden=int(datos.get("orden", 0)),
            descripcion=datos.get("descripcion", ""),
            estado=datos.get("estado", TaskState.PENDING.value),
            herramienta=datos.get("herramienta"),
            resultado=datos.get("resultado"),
            error=datos.get("error"),
            iniciado_en=datos.get("iniciado_en"),
            terminado_en=datos.get("terminado_en"),
            verificacion=datos.get("verificacion"),
            verificacion_motivo=datos.get("verificacion_motivo"),
        )


@dataclass
class Task:
    """Una unidad de trabajo con objetivo, pasos y resultado."""

    id: str
    objetivo: str
    estado: str = TaskState.PENDING.value
    session_id: str | None = None
    pasos: list[TaskStep] = field(default_factory=list)
    resultado: str | None = None
    error: str | None = None
    intentos: int = 0
    creado_en: float = 0.0
    actualizado_en: float = 0.0

    @property
    def pasos_incorrectos(self) -> list["TaskStep"]:
        """Los pasos que se comprobaron y NO salieron bien.

        Solo los comprobados: un paso sin verificar no cuenta como fallido, o
        Morgan reintentaría cosas que quizá salieron perfectamente.
        """
        return [p for p in self.pasos if p.verificacion == "incorrecto"]

    @property
    def progreso(self) -> float:
        """Fracción de pasos terminados, entre 0 y 1.

        Se calcula, no se almacena: un contador guardado se desincroniza en cuanto
        alguien añade un paso sin acordarse de actualizarlo.
        """
        # Una tarea completada esta al 100 % por definicion. Sin esto, una que
        # termina antes de agotar sus pasos previstos —porque el objetivo ya se
        # cumplio— mostraba un progreso a medias junto al cartel de "completada",
        # que es exactamente lo que confunde a quien mira.
        if self.estado == TaskState.COMPLETED.value:
            return 1.0

        if not self.pasos:
            return 0.0

        hechos = sum(1 for p in self.pasos if p.estado == TaskState.COMPLETED.value)
        return hechos / len(self.pasos)

    @property
    def paso_actual(self) -> TaskStep | None:
        """El que se está ejecutando, o el siguiente pendiente."""
        for paso in self.pasos:
            if paso.estado == TaskState.RUNNING.value:
                return paso
        for paso in self.pasos:
            if paso.estado == TaskState.PENDING.value:
                return paso
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objetivo": self.objetivo,
            "estado": self.estado,
            "session_id": self.session_id,
            "pasos": [p.to_dict() for p in self.pasos],
            "resultado": self.resultado,
            "error": self.error,
            "intentos": self.intentos,
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            # Derivados: la interfaz los necesita en cada pintado y calcularlos
            # allí duplicaría la regla en dos lenguajes.
            "progreso": round(self.progreso, 3),
            "paso_actual": self.paso_actual.descripcion if self.paso_actual else None,
        }
