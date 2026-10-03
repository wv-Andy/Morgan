"""
Espacios de trabajo (V2.2).

Un espacio agrupa conversaciones, archivos y documentos de conocimiento, y tiene
unas instrucciones propias que Morgan tiene en cuenta en cada turno. Ver
`docs/datos.md`.

Todo lo de aquí filtra por el usuario de la petición, como el resto de la API: un
espacio de otra persona responde 404, no 403. Decir «existe pero no es tuyo» ya
filtraría que existe.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import CoreContainer, get_container
from src.espacios.modelos import (
    MAX_INSTRUCCIONES,
    MAX_NOMBRE,
    EspacioDuplicado,
    EspacioInvalido,
)
from src.memory.db import MemoryStorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/espacios", tags=["Espacios de trabajo"])


class EspacioItem(BaseModel):
    id: str
    nombre: str
    instrucciones: str = ""
    creado_en: float = 0.0
    actualizado_en: float = 0.0


class EspacioListResponse(BaseModel):
    success: bool = True
    count: int = 0
    espacios: list[EspacioItem] = Field(default_factory=list)
    max_instrucciones: int = MAX_INSTRUCCIONES


class EspacioCreateRequest(BaseModel):
    # Los límites se vuelven a comprobar en el modelo. Aquí están para que la
    # documentación de la API los enseñe; allí, para que ningún otro camino se
    # los salte.
    nombre: str = Field(..., min_length=1, max_length=MAX_NOMBRE)
    instrucciones: str = Field("", max_length=MAX_INSTRUCCIONES)


class EspacioUpdateRequest(BaseModel):
    """Solo los campos enviados; los omitidos se dejan como estaban."""

    nombre: str | None = Field(None, min_length=1, max_length=MAX_NOMBRE)
    instrucciones: str | None = Field(None, max_length=MAX_INSTRUCCIONES)


def _repositorio(container: CoreContainer):
    from src.espacios.repositorio import repositorio_de_espacios

    repositorio = repositorio_de_espacios(container.repositories)
    if repositorio is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ESPACIOS_NO_DISPONIBLES",
                    "message": "Los espacios de trabajo no están disponibles."},
        )
    return repositorio


def _no_existe(espacio_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "ESPACIO_NO_ENCONTRADO",
                "message": f"No existe el espacio de trabajo '{espacio_id}'."},
    )


def _traducir(exc: Exception, accion: str) -> HTTPException:
    if isinstance(exc, EspacioDuplicado):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ESPACIO_DUPLICADO", "message": str(exc)},
        )
    if isinstance(exc, EspacioInvalido):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ESPACIO_INVALIDO", "message": str(exc)},
        )
    logger.error("Fallo de persistencia al %s: %s", accion, exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "STORAGE_ERROR", "message": f"No se pudo {accion}."},
    )


@router.get("", response_model=EspacioListResponse, summary="Listar espacios de trabajo")
def listar(container: CoreContainer = Depends(get_container)) -> EspacioListResponse:
    try:
        espacios = _repositorio(container).listar()
    except MemoryStorageError as exc:
        raise _traducir(exc, "listar los espacios de trabajo")
    return EspacioListResponse(
        count=len(espacios),
        espacios=[EspacioItem(**e.to_dict()) for e in espacios],
    )


@router.post(
    "",
    response_model=EspacioItem,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un espacio de trabajo",
)
def crear(
    peticion: EspacioCreateRequest,
    container: CoreContainer = Depends(get_container),
) -> EspacioItem:
    try:
        espacio = _repositorio(container).crear(peticion.nombre, peticion.instrucciones)
    except (EspacioDuplicado, EspacioInvalido, MemoryStorageError) as exc:
        raise _traducir(exc, "crear el espacio de trabajo")
    return EspacioItem(**espacio.to_dict())


@router.get("/{espacio_id}", response_model=EspacioItem, summary="Detalle de un espacio")
def obtener(
    espacio_id: str,
    container: CoreContainer = Depends(get_container),
) -> EspacioItem:
    try:
        espacio = _repositorio(container).obtener(espacio_id)
    except MemoryStorageError as exc:
        raise _traducir(exc, "recuperar el espacio de trabajo")
    if espacio is None:
        raise _no_existe(espacio_id)
    return EspacioItem(**espacio.to_dict())


@router.patch("/{espacio_id}", response_model=EspacioItem, summary="Renombrar o cambiar instrucciones")
def actualizar(
    espacio_id: str,
    cambios: EspacioUpdateRequest,
    container: CoreContainer = Depends(get_container),
) -> EspacioItem:
    try:
        espacio = _repositorio(container).actualizar(
            espacio_id, nombre=cambios.nombre, instrucciones=cambios.instrucciones,
        )
    except (EspacioDuplicado, EspacioInvalido, MemoryStorageError) as exc:
        raise _traducir(exc, "actualizar el espacio de trabajo")
    if espacio is None:
        raise _no_existe(espacio_id)
    return EspacioItem(**espacio.to_dict())


@router.delete("/{espacio_id}", summary="Eliminar un espacio de trabajo")
def eliminar(
    espacio_id: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Borra el espacio. **Lo que había dentro vuelve a «General»**, no se borra."""
    try:
        borrado = _repositorio(container).eliminar(espacio_id)
    except MemoryStorageError as exc:
        raise _traducir(exc, "eliminar el espacio de trabajo")
    if not borrado:
        raise _no_existe(espacio_id)
    return {"success": True, "deleted": espacio_id}
