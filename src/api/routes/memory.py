"""
Rutas para la gestión de memoria persistente SQLite en Morgan API (V1.0).
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
import logging

from src.api.dependencies import CoreContainer, get_container
from src.memory.db import MemoryStorageError
from src.api.schemas import (
    MemoryItem,
    MemoryListResponse,
    MemoryCreateRequest,
    ErrorResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.get("", response_model=MemoryListResponse, summary="Listar recuerdos persistentes")
def list_memories(
    query: Optional[str] = Query(None, description="Búsqueda por palabra clave en recuerdos"),
    category: Optional[str] = Query(None, description="Filtrar por categoría (ej: user_preferences, facts)"),
    container: CoreContainer = Depends(get_container),
) -> MemoryListResponse:
    """Devuelve los recuerdos almacenados en la base de datos SQLite."""
    try:
        memories = container.memory_manager.recall(query=query)
    except MemoryStorageError as exc:
        logger.error("No se pudo leer la memoria persistente: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "MEMORY_READ_ERROR",
                "message": "No se pudo leer la memoria persistente.",
            },
        )

    if category:
        memories = [m for m in memories if m.get("category", "").lower() == category.lower()]

    items = [
        MemoryItem(
            category=m["category"],
            key=m["key"],
            value=m["value"],
            updated_at=m["updated_at"],
        )
        for m in memories
    ]

    return MemoryListResponse(
        success=True,
        count=len(items),
        memories=items,
    )


@router.post("", response_model=MemoryItem, summary="Guardar o actualizar un recuerdo")
def remember_fact(
    request: MemoryCreateRequest,
    container: CoreContainer = Depends(get_container),
) -> MemoryItem:
    """Almacena o actualiza un hecho en la memoria persistente de Morgan."""
    from src.memory.manager import MemoriaLlena

    try:
        container.memory_manager.comprobar_que_cabe(request.key, request.value)
    except MemoriaLlena as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MEMORIA_LLENA", "message": str(e)},
        ) from None
    try:
        updated = container.memory_manager.remember(
            category=request.category,
            key=request.key,
            value=request.value,
        )
        # La memoria entra en el prompt en cada turno, para quien pregunta (4.0): no hay nada
        # que refrescar. Antes se escribía aquí en el prompt compartido, y la de esta persona
        # iba en los turnos de las demás.

        return MemoryItem(
            category=updated["category"],
            key=updated["key"],
            value=updated["value"],
            updated_at=updated["updated_at"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "MEMORY_SAVE_ERROR",
                "message": f"No se pudo guardar el recuerdo: {str(e)}",
            },
        )


@router.delete(
    "/{key}",
    summary="Olvidar un recuerdo por su clave",
    responses={404: {"model": ErrorResponse, "description": "Recuerdo no encontrado"}},
)
def forget_fact(
    key: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Elimina un hecho de la memoria persistente."""
    success = container.memory_manager.forget(key)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "MEMORY_KEY_NOT_FOUND",
                "message": f"No existe ningún recuerdo con la clave '{key}'.",
            },
        )

    return {"success": True, "message": f"Recuerdo '{key}' eliminado correctamente."}
