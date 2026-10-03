"""
Rutas de consulta de logs de auditoría de seguridad para Morgan API (V1.0).
"""

from fastapi import APIRouter, Depends, Query
from src.api.dependencies import CoreContainer, get_container
from src.api.schemas import AuditItem, AuditListResponse

router = APIRouter(prefix="/audit", tags=["Security & Audit"])


@router.get("", response_model=AuditListResponse, summary="Consultar registros de auditoría de seguridad")
def get_audit_logs(
    limit: int = Query(50, ge=1, le=500, description="Número máximo de registros a recuperar"),
    container: CoreContainer = Depends(get_container),
) -> AuditListResponse:
    """Devuelve los registros inmutables de auditoría de herramientas y comandos."""
    logs = container.audit_logger.get_recent(limit=limit)

    items = [
        AuditItem(
            timestamp=item.get("timestamp", ""),
            tool=item.get("tool", ""),
            risk_level=item.get("risk_level", "unknown"),
            authorized=item.get("authorized", False),
            arguments=item.get("args", item.get("arguments", {})),
            success=item.get("success", False),
            error=item.get("error"),
        )
        for item in logs
    ]

    return AuditListResponse(
        success=True,
        count=len(items),
        records=items,
    )
