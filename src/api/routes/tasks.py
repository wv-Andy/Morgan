"""
Rutas del sistema de tareas (V1.5).

La interfaz consulta estas rutas para pintar el progreso. Solo hay lectura y dos
acciones que el usuario puede querer sobre una tarea suya —cancelar y reintentar—:
crearlas y hacerlas avanzar es trabajo del agente, y exponerlo aquí permitiría
escribir estados que no se corresponden con nada ejecutado.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies import CoreContainer, get_container
from src.api.schemas import ErrorResponse, TaskItem, TaskListResponse
from src.tasks import TransicionInvalida

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _no_existe(task_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "TASK_NOT_FOUND", "message": f"No existe la tarea '{task_id}'."},
    )


@router.get("", response_model=TaskListResponse, summary="Listar tareas")
def list_tasks(
    session_id: str | None = Query(None, description="Filtrar por conversación"),
    solo_activas: bool = Query(False, description="Excluir las ya terminadas"),
    limit: int = Query(50, ge=1, le=200),
    container: CoreContainer = Depends(get_container),
) -> TaskListResponse:
    tareas = container.tasks.listar(
        session_id=session_id, solo_activas=solo_activas, limit=limit
    )
    return TaskListResponse(
        success=True,
        count=len(tareas),
        tasks=[TaskItem(**t.to_dict()) for t in tareas],
    )


@router.get(
    "/{task_id}",
    response_model=TaskItem,
    summary="Detalle de una tarea",
    responses={404: {"model": ErrorResponse, "description": "Tarea no encontrada"}},
)
def get_task(task_id: str, container: CoreContainer = Depends(get_container)) -> TaskItem:
    tarea = container.tasks.obtener(task_id)
    if tarea is None:
        raise _no_existe(task_id)
    return TaskItem(**tarea.to_dict())


@router.post(
    "/{task_id}/cancel",
    response_model=TaskItem,
    summary="Cancelar una tarea",
    responses={
        404: {"model": ErrorResponse, "description": "Tarea no encontrada"},
        409: {"model": ErrorResponse, "description": "La tarea ya había terminado"},
    },
)
def cancel_task(task_id: str, container: CoreContainer = Depends(get_container)) -> TaskItem:
    if container.tasks.obtener(task_id) is None:
        raise _no_existe(task_id)

    try:
        return TaskItem(**container.tasks.cancelar(task_id).to_dict())
    except TransicionInvalida as exc:
        # 409 y no 400: la petición es válida, es el estado actual el que no la
        # admite. El mensaje ya está escrito para leerse.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_STATE_CONFLICT", "message": str(exc)},
        )


@router.post(
    "/{task_id}/retry",
    response_model=TaskItem,
    summary="Reintentar una tarea",
    responses={
        404: {"model": ErrorResponse, "description": "Tarea no encontrada"},
        409: {"model": ErrorResponse, "description": "La tarea no se puede reintentar"},
    },
)
def retry_task(task_id: str, container: CoreContainer = Depends(get_container)) -> TaskItem:
    if container.tasks.obtener(task_id) is None:
        raise _no_existe(task_id)

    try:
        return TaskItem(**container.tasks.reintentar(task_id).to_dict())
    except TransicionInvalida as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_STATE_CONFLICT", "message": str(exc)},
        )


@router.delete(
    "/{task_id}",
    summary="Eliminar una tarea",
    responses={404: {"model": ErrorResponse, "description": "Tarea no encontrada"}},
)
def delete_task(task_id: str, container: CoreContainer = Depends(get_container)) -> dict:
    if not container.tasks.eliminar(task_id):
        raise _no_existe(task_id)
    return {"success": True, "deleted": task_id}
