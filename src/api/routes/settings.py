"""
Perfil y preferencias del usuario (Etapa B de la especificación web).

Los ajustes se guardan como recuerdos y por tanto acaban en el system prompt: lo
que se configura aquí, Morgan lo cumple. Tras cada escritura se refresca el
contexto del agente, o el cambio no tendría efecto hasta reiniciar.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import CoreContainer, get_container
from src.api.schemas import SettingsResponse, SettingsUpdateRequest
from src.memory.db import MemoryStorageError
from src.memory.settings import SettingsStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


def _store(container: CoreContainer) -> SettingsStore:
    return SettingsStore(container.memory_manager)


@router.get("", response_model=SettingsResponse, summary="Perfil y preferencias")
def get_settings(container: CoreContainer = Depends(get_container)) -> SettingsResponse:
    return SettingsResponse(success=True, settings=_store(container).load().to_dict())


@router.put("", response_model=SettingsResponse, summary="Guardar perfil y preferencias")
def update_settings(
    cambios: SettingsUpdateRequest,
    container: CoreContainer = Depends(get_container),
) -> SettingsResponse:
    """Guarda los campos enviados. Los omitidos se dejan como estaban.

    Un campo enviado vacío borra ese dato: es la forma de quitarlo.
    """
    try:
        actualizados = _store(container).save(cambios.model_dump(exclude_unset=True))
    except MemoryStorageError as exc:
        logger.error("Fallo al guardar los ajustes: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "STORAGE_ERROR", "message": "No se pudieron guardar los ajustes."},
        )

    # El cambio llega al modelo en el turno siguiente: la memoria (y con ella los ajustes)
    # se lee en cada turno, para quien pregunta (4.0). No se escribe en el prompt compartido.

    return SettingsResponse(success=True, settings=actualizados.to_dict())
