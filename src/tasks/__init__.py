"""Sistema de tareas de Morgan (V1.5)."""

from src.tasks.manager import MAX_INTENTOS, TaskManager, TransicionInvalida
from src.tasks.modelos import REINTENTABLES, Task, TaskState, TaskStep

__all__ = [
    "MAX_INTENTOS",
    "REINTENTABLES",
    "Task",
    "TaskManager",
    "TaskState",
    "TaskStep",
    "TransicionInvalida",
]
