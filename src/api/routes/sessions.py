"""
Rutas de sesiones e historial de conversación para Morgan API (V1.2).
"""

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies import CoreContainer, get_container
from src.api.schemas import (
    ErrorResponse,
    MessageItem,
    MessageListResponse,
    SessionCreateRequest,
    SessionItem,
    SessionListResponse,
    SessionUpdateRequest,
)
from src.espacios.contexto import espacio_actual
from src.memory.db import MemoryStorageError
from src.memory.repositories import CUALQUIER_ESPACIO

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["Sessions"])


def _storage_error(action: str, exc: Exception) -> HTTPException:
    logger.error("Fallo de persistencia al %s: %s", action, exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "STORAGE_ERROR",
            "message": f"No se pudo {action}.",
        },
    )


@router.get("", response_model=SessionListResponse, summary="Listar conversaciones")
def list_sessions(
    limit: int = Query(50, ge=1, le=200, description="Número máximo de sesiones"),
    offset: int = Query(0, ge=0, description="Desplazamiento para paginar"),
    archived: Literal["false", "true", "all"] = Query(
        "false",
        description="false: solo activas (por defecto) · true: solo archivadas · "
                    "all: ambas. Un booleano no serviria: harian falta tres "
                    "estados y HTTP no transporta None.",
    ),
    group_name: str | None = Query(None, description="Filtrar por grupo"),
    q: str | None = Query(None, max_length=100, description="Buscar en el título"),
    espacio: Literal["actual", "todos"] = Query(
        "actual",
        description="actual: las del espacio de trabajo de la petición (cabecera "
                    "X-Morgan-Espacio; sin ella, General) · todos: de todos los espacios",
    ),
    container: CoreContainer = Depends(get_container),
) -> SessionListResponse:
    """Devuelve las conversaciones: las fijadas primero, luego por recientes."""
    try:
        sessions = container.repositories.sessions.list(
            limit=limit,
            offset=offset,
            archived={"false": False, "true": True, "all": None}[archived],
            group_name=group_name,
            query=q,
            espacio=espacio_actual() if espacio == "actual" else CUALQUIER_ESPACIO,
        )
    except MemoryStorageError as exc:
        raise _storage_error("listar las conversaciones", exc)

    return SessionListResponse(
        success=True,
        count=len(sessions),
        sessions=[SessionItem(**s.to_dict()) for s in sessions],
    )


@router.post("", response_model=SessionItem, summary="Crear una conversación")
def create_session(
    request: SessionCreateRequest,
    container: CoreContainer = Depends(get_container),
) -> SessionItem:
    """Crea una conversación. Si no se indica id, se genera uno."""
    session_id = (request.session_id or f"ses-{uuid.uuid4().hex[:16]}").strip()

    try:
        # Nace en el espacio de la petición. El middleware ya comprobó que es
        # de quien pide.
        created = container.repositories.sessions.create(
            session_id, title=request.title, metadata=request.metadata,
            espacio_id=espacio_actual(),
        )
    except MemoryStorageError as exc:
        raise _storage_error("crear la conversación", exc)

    return SessionItem(**created.to_dict())


@router.get(
    "/{session_id}",
    response_model=SessionItem,
    summary="Detalle de una conversación",
    responses={404: {"model": ErrorResponse, "description": "Conversación no encontrada"}},
)
def get_session(
    session_id: str,
    container: CoreContainer = Depends(get_container),
) -> SessionItem:
    try:
        found = container.repositories.sessions.get(session_id)
    except MemoryStorageError as exc:
        raise _storage_error("recuperar la conversación", exc)

    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SESSION_NOT_FOUND",
                "message": f"No existe la conversación '{session_id}'.",
            },
        )

    return SessionItem(**found.to_dict())


@router.get(
    "/{session_id}/messages",
    response_model=MessageListResponse,
    summary="Mensajes de una conversación",
)
def get_session_messages(
    session_id: str,
    limit: int = Query(100, ge=1, le=500, description="Número máximo de mensajes recientes"),
    container: CoreContainer = Depends(get_container),
) -> MessageListResponse:
    """Devuelve los mensajes más recientes de la conversación, en orden cronológico."""
    try:
        messages = container.repositories.messages.list_for_session(session_id, limit=limit)
        total = container.repositories.messages.count(session_id)
    except MemoryStorageError as exc:
        raise _storage_error("recuperar los mensajes", exc)

    return MessageListResponse(
        success=True,
        session_id=session_id,
        count=len(messages),
        total=total,
        messages=[MessageItem(**m.to_dict()) for m in messages],
    )


@router.patch(
    "/{session_id}",
    response_model=SessionItem,
    summary="Renombrar, archivar, fijar o agrupar una conversación",
    responses={404: {"model": ErrorResponse, "description": "Conversación no encontrada"}},
)
def update_session(
    session_id: str,
    cambios: SessionUpdateRequest,
    container: CoreContainer = Depends(get_container),
) -> SessionItem:
    """Aplica solo los campos enviados; los omitidos se dejan como estaban.

    Un único endpoint para las cuatro operaciones porque todas son lo mismo:
    escribir campos de una fila. Cuatro rutas separadas repetirían el manejo de
    errores sin aportar nada.
    """
    # Mover a un espacio exige que ese espacio exista y sea de quien pide. Sin
    # esta comprobación, una conversación podía acabar apuntando a un espacio
    # ajeno o inventado: no se vería en ningún sitio.
    #
    # El código de error es distinto del del middleware a propósito: la web,
    # al recibir aquel, olvida el espacio seleccionado. Un destino mal elegido
    # al mover no dice nada del espacio en el que está.
    if cambios.espacio_id and cambios.espacio_id.strip():
        from src.espacios.repositorio import repositorio_de_espacios

        repositorio = repositorio_de_espacios(container.repositories)
        if repositorio is None or repositorio.obtener(cambios.espacio_id.strip()) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "ESPACIO_DESTINO_NO_ENCONTRADO",
                    "message": "El espacio de trabajo al que quieres moverla no existe.",
                },
            )

    try:
        sesion = container.repositories.sessions.update(
            session_id,
            title=cambios.title,
            archived=cambios.archived,
            pinned=cambios.pinned,
            group_name=cambios.group_name,
            espacio_id=cambios.espacio_id,
        )
    except MemoryStorageError as exc:
        raise _storage_error("actualizar la conversación", exc)

    if sesion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SESSION_NOT_FOUND",
                "message": f"No existe la conversación '{session_id}'.",
            },
        )

    return SessionItem(**sesion.to_dict())


@router.delete(
    "/{session_id}",
    summary="Eliminar una conversación",
    responses={404: {"model": ErrorResponse, "description": "Conversación no encontrada"}},
)
def delete_session(
    session_id: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Elimina la conversación y todos sus mensajes."""
    try:
        deleted = container.repositories.sessions.delete(session_id)
    except MemoryStorageError as exc:
        raise _storage_error("eliminar la conversación", exc)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SESSION_NOT_FOUND",
                "message": f"No existe la conversación '{session_id}'.",
            },
        )

    # La sesión viva en memoria también debe desaparecer, o seguiría respondiendo
    # con el contexto de una conversación que el usuario acaba de borrar.
    if container.agent is not None:
        container.agent.sessions.drop(session_id)

    return {"success": True, "message": f"Conversación '{session_id}' eliminada."}
