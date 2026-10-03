"""
Herramientas del sistema de tareas (V1.5).

Permiten a Morgan **llevar cuenta de su propio trabajo**: declarar un objetivo,
anotar lo que va haciendo y cerrar con un resultado. Hasta ahora, un encargo de
ocho llamadas a herramientas era una caja negra de minuto y medio; con esto queda
un registro de qué se intentó, qué devolvió y qué falló.

Todas son `requires_local = False`: operan sobre la base de datos, no sobre el
disco, así que existen igual en la nube.

**No se ofrece `update_task` genérica.** El plan la listaba, pero un método que
cambia cualquier campo permite que el modelo escriba estados incoherentes —marcar
como completada una tarea sin resultado, por ejemplo—. Las transiciones válidas
son pocas y tienen nombre propio: avanzar, completar, fallar, esperar, cancelar y
reintentar. Cada una es una herramienta.
"""

import logging
from typing import Any

from src.tasks import TaskManager, TransicionInvalida
from src.tools.base import RiskLevel, Tool, ToolCategory

logger = logging.getLogger(__name__)


class _HerramientaDeTareas(Tool):
    """Base común: el gestor y el manejo uniforme de las transiciones."""

    def __init__(self, manager: TaskManager):
        self.manager = manager

    @property
    def category(self) -> str:
        return ToolCategory.GENERAL.value

    @property
    def requires_local(self) -> bool:
        return False

    @property
    def permission_level(self) -> str:
        return RiskLevel.SAFE.value

    @staticmethod
    def _ok(datos: Any) -> dict:
        return {"success": True, "data": datos, "error": None}

    def _transicion(self, accion, *args, **kwargs) -> dict:
        """Ejecuta una transición traduciendo su rechazo a un resultado normal.

        Una transición inválida no es un fallo del sistema: es que el modelo pidió
        algo que no procede. Devolverlo como error de herramienta le permite
        corregirse; lanzarlo abortaría el turno.
        """
        try:
            return self._ok(accion(*args, **kwargs).to_dict())
        except TransicionInvalida as exc:
            return {"success": False, "data": None, "error": str(exc)}
        except ValueError as exc:
            return {"success": False, "data": None, "error": str(exc)}


class CreateTaskTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "create_task"

    @property
    def description(self) -> str:
        return (
            "Crea una tarea para un encargo de varios pasos, para que la persona vea el "
            "progreso. Para una pregunta directa, no."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "objetivo": {
                    "type": "string",
                    "description": "Qué se quiere conseguir, en una frase",
                },
                "pasos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Los pasos previstos, si ya los tienes claros",
                },
            },
            "required": ["objetivo"],
        }

    def execute(self, objetivo: str = "", pasos: list | None = None, **kwargs: Any) -> dict:
        try:
            tarea = self.manager.crear(objetivo, pasos=[str(p) for p in (pasos or [])])
        except ValueError as exc:
            return {"success": False, "data": None, "error": str(exc)}

        return self._ok(tarea.to_dict())


class GetTaskTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "get_task"

    @property
    def description(self) -> str:
        return "Consulta el estado, los pasos y el resultado de una tarea."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Identificador de la tarea"}},
            "required": ["task_id"],
        }

    def execute(self, task_id: str = "", **kwargs: Any) -> dict:
        tarea = self.manager.obtener(task_id)
        if tarea is None:
            return {"success": False, "data": None, "error": f"No existe la tarea '{task_id}'."}
        return self._ok(tarea.to_dict())


class ListTasksTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return "Lista las tareas, opcionalmente solo las que siguen en marcha."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "solo_activas": {
                    "type": "boolean",
                    "description": "true para excluir las terminadas, canceladas y fallidas",
                },
            },
            "required": [],
        }

    def execute(self, solo_activas: bool = False, **kwargs: Any) -> dict:
        tareas = self.manager.listar(solo_activas=bool(solo_activas))
        return self._ok({"count": len(tareas), "tasks": [t.to_dict() for t in tareas]})


class CompleteTaskTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return (
            "Cierra una tarea con su resultado. Úsala solo cuando el objetivo se "
            "haya cumplido de verdad; si no se logró, usa 'fail_task'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "resultado": {"type": "string", "description": "Qué se consiguió"},
            },
            "required": ["task_id", "resultado"],
        }

    def execute(self, task_id: str = "", resultado: str = "", **kwargs: Any) -> dict:
        return self._transicion(self.manager.completar, task_id, resultado)


class FailTaskTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "fail_task"

    @property
    def description(self) -> str:
        return (
            "Marca una tarea como no conseguida, explicando por qué. Es preferible "
            "a cerrarla como completada fingiendo un resultado que no se obtuvo."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "error": {"type": "string", "description": "Por qué no se pudo"},
            },
            "required": ["task_id", "error"],
        }

    def execute(self, task_id: str = "", error: str = "", **kwargs: Any) -> dict:
        return self._transicion(self.manager.fallar, task_id, error)


class CancelTaskTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "cancel_task"

    @property
    def description(self) -> str:
        return "Cancela una tarea que ya no tiene sentido continuar."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }

    def execute(self, task_id: str = "", **kwargs: Any) -> dict:
        return self._transicion(self.manager.cancelar, task_id)


class RetryTaskTool(_HerramientaDeTareas):
    @property
    def name(self) -> str:
        return "retry_task"

    @property
    def description(self) -> str:
        return (
            "Vuelve a poner en marcha una tarea que falló o se canceló. Los pasos "
            "del intento anterior se conservan, para no repetir los mismos errores."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }

    def execute(self, task_id: str = "", **kwargs: Any) -> dict:
        return self._transicion(self.manager.reintentar, task_id)


def task_tools(manager: TaskManager) -> list[Tool]:
    """Las herramientas de tareas, listas para registrar."""
    # AdvanceTaskTool NO se incluye: el agente anota los pasos por su cuenta al
    # ejecutar cada herramienta. Ofrecersela al modelo duplicaria cada paso y,
    # sobre todo, duplicaria los viajes de ida y vuelta al proveedor: con ella,
    # un encargo de tres pasos tardaba 126 s y se cortaba por el limite del turno.
    # La clase se retiró en la 2.0.11: nadie la registraba ni la usaba.
    #
    # `verify_step` se retiró en la 4.1.5 por lo mismo, y por algo peor. Su descripción
    # pedía llamarla «antes de decir que has terminado»: una llamada más al modelo en
    # cada cambio, cuando la verificación ya es automática tras cada uno (V1.7) y viaja en
    # su resultado. Y comprobaba **sin saber que el cambio era del PC**: medido, para un
    # archivo creado en el PC contestaba «NO salió, no existe», mirando el disco del
    # servidor (el fallo que la 4.0.0-dev arregló en la verificación automática).
    return [
        CreateTaskTool(manager),
        GetTaskTool(manager),
        ListTasksTool(manager),
        CompleteTaskTool(manager),
        FailTaskTool(manager),
        CancelTaskTool(manager),
        RetryTaskTool(manager),
    ]
