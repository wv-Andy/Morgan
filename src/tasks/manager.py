"""
Gestor de tareas (V1.5).

Concentra las transiciones de estado en un sitio. Repartirlas por las herramientas
y la API garantizaría que en algún camino se olvidara actualizar `actualizado_en`,
o que una tarea completada volviera a marcarse como en marcha.

**Las transiciones se validan.** No es burocracia: sin ello, un reintento sobre una
tarea ya completada la reabriría y el usuario vería «en marcha» algo que ya tiene
resultado. Las reglas se derivan de una sola idea — un estado terminal solo se
abandona reintentando explícitamente — en lugar de escribirse una a una.
"""

import logging
import secrets
import threading
import time

from src.memory.repositories import TaskRepository
from src.tasks.modelos import REINTENTABLES, Task, TaskState, TaskStep

logger = logging.getLogger(__name__)

# Cuántas veces puede reintentarse una tarea. No es infinito a propósito: una
# tarea que falla siempre debe acabar diciéndolo, no consumir la cuota del modelo
# indefinidamente.
MAX_INTENTOS = 3

# Tras cuanto tiempo sin novedades se da por estancada una tarea viva. Si el turno
# que la ejecutaba murio —por el limite de tiempo, por un reinicio o por un fallo
# del proveedor—, nadie va a moverla nunca mas, y dejarla girando en la interfaz
# para siempre es peor que decir que se corto. Diez minutos deja margen de sobra:
# el limite de un turno son dos.
MINUTOS_PARA_ESTANCARSE = 10


class TransicionInvalida(RuntimeError):
    """Se pidió un cambio de estado que no tiene sentido.

    El mensaje está escrito para enseñarse: quien lo provoca suele ser el usuario
    pulsando «reintentar» sobre algo que ya terminó.
    """


class TaskManager:
    """Crea y hace avanzar las tareas."""

    def __init__(self, repositorio: TaskRepository):
        self.repositorio = repositorio
        # Dos peticiones sobre la misma tarea pueden llegar a la vez desde el
        # threadpool de FastAPI. Sin esto, dos avances simultaneos se pisan y uno
        # de los dos pasos se pierde.
        self._lock = threading.RLock()

    # --- Creación ------------------------------------------------------------

    def crear(
        self,
        objetivo: str,
        session_id: str | None = None,
        pasos: list[str] | None = None,
    ) -> Task:
        if not objetivo or not objetivo.strip():
            raise ValueError("Una tarea necesita un objetivo.")

        ahora = time.time()
        tarea = Task(
            id=f"task-{secrets.token_urlsafe(9)}",
            objetivo=objetivo.strip()[:2000],
            session_id=session_id,
            pasos=[
                TaskStep(orden=i, descripcion=d.strip()[:500])
                for i, d in enumerate(pasos or [], start=1)
                if d and d.strip()
            ],
            creado_en=ahora,
            actualizado_en=ahora,
        )
        self.repositorio.create(tarea)
        logger.info("Tarea creada: %s (%d pasos)", tarea.id, len(tarea.pasos))
        return tarea

    # --- Consulta ------------------------------------------------------------

    def obtener(self, task_id: str) -> Task | None:
        return self.repositorio.get(task_id)

    def listar(
        self,
        session_id: str | None = None,
        solo_activas: bool = False,
        limit: int = 50,
    ) -> list[Task]:
        self.cerrar_estancadas()
        estados = None
        if solo_activas:
            estados = tuple(
                e.value for e in TaskState if not e.terminal
            )
        return self.repositorio.list(session_id=session_id, estados=estados, limit=limit)

    def cerrar_estancadas(self, minutos: int = MINUTOS_PARA_ESTANCARSE) -> int:
        """Marca como fallidas las tareas vivas que nadie ha tocado en un rato.

        Una tarea queda en `running` cuando el turno que la ejecutaba muere: por
        el limite de tiempo, por un reinicio o por un fallo del proveedor. Nadie
        va a moverla nunca mas. Dejarla girando en la interfaz para siempre es
        mentir sobre lo que esta pasando.

        Se hace al listar, que es cuando alguien va a mirar: un hilo de fondo
        solo para esto seria mas piezas de las necesarias.
        """
        limite = time.time() - minutos * 60
        cerradas = 0

        for tarea in self.repositorio.list(
            estados=(TaskState.RUNNING.value, TaskState.PENDING.value), limit=200
        ):
            if tarea.actualizado_en >= limite:
                continue

            tarea.estado = TaskState.FAILED.value
            tarea.error = (
                f"Se interrumpio sin terminar: lleva mas de {minutos} minutos sin "
                "avanzar. Puede que el turno se agotara o que Morgan se reiniciara."
            )
            self._guardar(tarea)
            cerradas += 1

        if cerradas:
            logger.info("Cerradas %d tareas estancadas", cerradas)
        return cerradas

    # --- Transiciones --------------------------------------------------------

    def _guardar(self, tarea: Task) -> Task:
        tarea.actualizado_en = time.time()
        actualizada = self.repositorio.update(tarea)
        if actualizada is None:
            raise TransicionInvalida(f"La tarea '{tarea.id}' ya no existe.")
        return actualizada

    def _cargar(self, task_id: str) -> Task:
        tarea = self.repositorio.get(task_id)
        if tarea is None:
            raise TransicionInvalida(f"No existe la tarea '{task_id}'.")
        return tarea

    def iniciar(self, task_id: str) -> Task:
        with self._lock:
            tarea = self._cargar(task_id)
            estado = TaskState(tarea.estado)

            if estado is TaskState.RUNNING:
                return tarea  # ya estaba: no es un error, no hay nada que hacer

            if estado.terminal:
                raise TransicionInvalida(
                    f"La tarea ya {self._participio(estado)}. Para volver a "
                    "intentarla, usa 'retry_task'."
                )

            tarea.estado = TaskState.RUNNING.value
            return self._guardar(tarea)

    def avanzar(
        self,
        task_id: str,
        descripcion: str,
        herramienta: str | None = None,
        resultado: str | None = None,
        error: str | None = None,
        verificacion: str | None = None,
        verificacion_motivo: str | None = None,
    ) -> Task:
        """Registra un paso ya ejecutado.

        Se registra **aunque haya fallado**: el historial de lo que no funcionó es
        justo lo que hace falta para diagnosticar, y es sobre lo que construirá la
        V1.7.
        """
        with self._lock:
            tarea = self._cargar(task_id)
            if TaskState(tarea.estado).terminal:
                raise TransicionInvalida(
                    f"La tarea ya {self._participio(TaskState(tarea.estado))}: "
                    "no admite pasos nuevos."
                )

            ahora = time.time()
            estado_paso = (TaskState.FAILED if error else TaskState.COMPLETED).value

            # Si la tarea se creo con pasos previstos, avanzar CONSUME el
            # siguiente pendiente en lugar de anadir otro. Antes se duplicaban:
            # tres pasos planificados mas tres ejecutados daban seis, y el
            # progreso salia a la mitad de lo real.
            siguiente = next(
                (p for p in tarea.pasos if p.estado == TaskState.PENDING.value), None
            )
            if siguiente is not None:
                # Vacia significa 'conserva la que traia el plan', que es la legible.
                siguiente.descripcion = descripcion.strip()[:500] or siguiente.descripcion
                siguiente.estado = estado_paso
                siguiente.herramienta = herramienta
                siguiente.resultado = (resultado or "")[:2000] or None
                siguiente.error = (error or "")[:1000] or None
                siguiente.iniciado_en = ahora
                siguiente.terminado_en = ahora
                siguiente.verificacion = verificacion
                siguiente.verificacion_motivo = (verificacion_motivo or "")[:300] or None
            else:
                tarea.pasos.append(TaskStep(
                    orden=len(tarea.pasos) + 1,
                    descripcion=descripcion.strip()[:500],
                    estado=estado_paso,
                    herramienta=herramienta,
                    resultado=(resultado or "")[:2000] or None,
                    error=(error or "")[:1000] or None,
                    iniciado_en=ahora,
                    terminado_en=ahora,
                    verificacion=verificacion,
                    verificacion_motivo=(verificacion_motivo or "")[:300] or None,
                ))
            tarea.estado = TaskState.RUNNING.value
            return self._guardar(tarea)

    def completar(self, task_id: str, resultado: str) -> Task:
        """Cierra la tarea como conseguida.

        **Se niega si algún paso se comprobó y salió mal** (V1.7). Es el corazón
        de esta versión: sin esto, Morgan puede escribir «listo, archivo creado»
        sobre un archivo que no existe, y quien lea el resumen no tiene forma de
        saberlo.

        Un paso sin verificar no impide completar: no comprobado no es lo mismo
        que incorrecto, y tratarlo igual haría imposible terminar cualquier tarea
        que use herramientas cuyo efecto Morgan no sabe mirar.
        """
        with self._lock:
            tarea = self._cargar(task_id)
            if TaskState(tarea.estado).terminal:
                raise TransicionInvalida(
                    f"La tarea ya {self._participio(TaskState(tarea.estado))}."
                )

            fallidos = tarea.pasos_incorrectos
            if fallidos:
                detalle = "; ".join(
                    f"paso {p.orden} ({p.descripcion[:60]}): {p.verificacion_motivo}"
                    for p in fallidos[:3]
                )
                raise TransicionInvalida(
                    "No se puede dar por completada: hay pasos que se "
                    f"comprobaron y no salieron bien. {detalle}. "
                    "Corrígelos y vuelve a intentarlo, o marca la tarea como "
                    "fallida explicando qué pasó."
                )

            tarea.estado = TaskState.COMPLETED.value
            tarea.resultado = (resultado or "")[:5000]
            tarea.error = None
            return self._guardar(tarea)

    def fallar(self, task_id: str, error: str) -> Task:
        """Marca la tarea como fallida.

        Que Morgan pueda decir «no pude completar la tarea» en lugar de afirmar
        falsamente que la completó es medio objetivo de la V1.7, y empieza aquí.
        """
        with self._lock:
            tarea = self._cargar(task_id)
            if TaskState(tarea.estado).terminal:
                return tarea  # ya acabó; no se reescribe el desenlace

            tarea.estado = TaskState.FAILED.value
            tarea.error = (error or "")[:2000]
            return self._guardar(tarea)

    def esperar(self, task_id: str, motivo: str) -> Task:
        """La tarea queda a la espera de algo externo, típicamente una confirmación."""
        with self._lock:
            tarea = self._cargar(task_id)
            if TaskState(tarea.estado).terminal:
                raise TransicionInvalida(
                    f"La tarea ya {self._participio(TaskState(tarea.estado))}."
                )

            tarea.estado = TaskState.WAITING.value
            tarea.resultado = (motivo or "")[:2000]
            return self._guardar(tarea)

    def cancelar(self, task_id: str) -> Task:
        with self._lock:
            tarea = self._cargar(task_id)
            if TaskState(tarea.estado).terminal:
                raise TransicionInvalida(
                    f"La tarea ya {self._participio(TaskState(tarea.estado))}."
                )

            tarea.estado = TaskState.CANCELLED.value
            return self._guardar(tarea)

    def reintentar(self, task_id: str) -> Task:
        """Vuelve a poner en marcha una tarea fallida o cancelada.

        Los pasos anteriores **se conservan**: son el registro de lo que ya se
        intentó, y borrarlos haría que el segundo intento repitiera los mismos
        errores sin saberlo.
        """
        with self._lock:
            tarea = self._cargar(task_id)
            estado = TaskState(tarea.estado)

            if estado not in REINTENTABLES:
                raise TransicionInvalida(
                    "Solo se pueden reintentar las tareas que fallaron o se "
                    f"cancelaron; esta {self._participio(estado, presente=True)}."
                )

            if tarea.intentos >= MAX_INTENTOS:
                raise TransicionInvalida(
                    f"Esta tarea ya se ha intentado {tarea.intentos} veces. "
                    "Merece la pena replantear el objetivo antes de insistir."
                )

            tarea.intentos += 1
            tarea.estado = TaskState.RUNNING.value
            tarea.error = None
            return self._guardar(tarea)

    def eliminar(self, task_id: str) -> bool:
        with self._lock:
            return self.repositorio.delete(task_id)

    # --- Texto ---------------------------------------------------------------

    @staticmethod
    def _participio(estado: TaskState, presente: bool = False) -> str:
        """Cómo se nombra ese estado dentro de una frase."""
        formas = {
            TaskState.COMPLETED: ("se completó", "está completada"),
            TaskState.FAILED: ("falló", "ha fallado"),
            TaskState.CANCELLED: ("se canceló", "está cancelada"),
            TaskState.RUNNING: ("está en marcha", "está en marcha"),
            TaskState.WAITING: ("está esperando", "está esperando"),
            TaskState.PENDING: ("está pendiente", "está pendiente"),
        }
        pasado, ahora = formas[estado]
        return ahora if presente else pasado
