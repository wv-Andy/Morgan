"""
Rutas de planes: ver lo que Morgan piensa hacer, y decidir (V1.6).

La razón de que estas rutas existan es que **aprobar un plan no puede depender de
que haya una consola delante**. En la CLI, `PermissionManager` pregunta y espera;
en la web no hay a quién preguntar de forma síncrona, así que la decisión tiene
que poder tomarse después, desde la interfaz, sobre un plan que quedó guardado.

Eso convierte la aprobación en algo mejor que una confirmación suelta: se decide
sobre **el trabajo entero**, no sobre el paso que toca ahora.

No hay ruta para *crear* planes. Los crea el agente al planificar, igual que las
tareas: exponerlo aquí permitiría escribir planes que no corresponden a nada que
Morgan haya pensado.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import CoreContainer, get_container
from src.tasks.plan import EstadoPlan, TransicionInvalida

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planes", tags=["Planes"])


class RechazoRequest(BaseModel):
    motivo: str | None = Field(
        None,
        max_length=300,
        description="Por qué se rechaza. Se le enseña al modelo para que no "
                    "vuelva a proponer lo mismo.",
    )


def _plan_o_404(container: CoreContainer, plan_id: str):
    plan = container.planificador.obtener(plan_id)

    if plan is None:
        # 404 y no 403 aunque sea de otro: el repositorio filtra por usuario, asi
        # que "no es tuyo" y "no existe" son indistinguibles desde fuera. Es lo
        # correcto — decir "existe pero no es tuyo" delataria su existencia.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_ENCONTRADO", "message": "No existe ese plan."},
        )

    return plan


@router.get("", summary="Listar planes")
def listar(
    session_id: str | None = Query(None, description="Filtrar por conversación"),
    solo_pendientes: bool = Query(False, description="Solo los que esperan decisión"),
    limit: int = Query(50, ge=1, le=200),
    container: CoreContainer = Depends(get_container),
) -> dict:
    estados = (EstadoPlan.PENDIENTE.value,) if solo_pendientes else None
    planes = container.planificador.listar(
        session_id=session_id, estados=estados, limite=limit
    )

    return {
        "success": True,
        "count": len(planes),
        "planes": [p.to_dict() for p in planes],
    }


@router.get("/{plan_id}", summary="Detalle de un plan")
def obtener(
    plan_id: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    return {"success": True, "plan": _plan_o_404(container, plan_id).to_dict()}


@router.post("/{plan_id}/aprobar", summary="Aprobar un plan")
def aprobar(
    plan_id: str,
    container: CoreContainer = Depends(get_container),
) -> dict:
    _plan_o_404(container, plan_id)

    try:
        plan = container.planificador.aprobar(plan_id)
    except TransicionInvalida as exc:
        # 409: la ruta y el plan existen, pero no en ese estado. Un plan ya
        # rechazado no se aprueba luego.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ESTADO_INVALIDO", "message": str(exc)},
        ) from exc

    return {"success": True, "plan": plan.to_dict()}


@router.post("/{plan_id}/rechazar", summary="Rechazar un plan")
def rechazar(
    plan_id: str,
    datos: RechazoRequest | None = None,
    container: CoreContainer = Depends(get_container),
) -> dict:
    """Rechaza un plan. **No deja nada a medias**, porque nada se ha ejecutado.

    Es justo la ventaja de decidir antes: rechazar a mitad de un trabajo deja
    archivos escritos y otros no; rechazar un plan no deja nada.
    """
    _plan_o_404(container, plan_id)

    try:
        plan = container.planificador.rechazar(
            plan_id, motivo=(datos.motivo if datos else None)
        )
    except TransicionInvalida as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ESTADO_INVALIDO", "message": str(exc)},
        ) from exc

    return {"success": True, "plan": plan.to_dict()}
