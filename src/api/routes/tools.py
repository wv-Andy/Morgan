"""
Rutas de catálogo y ejecución de herramientas para Morgan API (V1.0).
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from src.api.dependencies import CoreContainer, get_container
from src.tools.validation import validate_tool_args
from src.api.schemas import (
    ToolSchema,
    ToolListResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ErrorResponse,
)

router = APIRouter(prefix="/tools", tags=["Tools"])


@router.get("", response_model=ToolListResponse, summary="Catálogo de herramientas")
def list_tools(
    category: Optional[str] = Query(None, description="Filtrar por dominio (ej: filesystem, git, coding)"),
    container: CoreContainer = Depends(get_container),
) -> ToolListResponse:
    """Devuelve el catálogo de herramientas de Morgan organizadas por dominios."""
    registry = container.tool_registry

    if category:
        tools = registry.get_by_category(category)
    else:
        tools = registry.list_tools()

    tool_items = [
        ToolSchema(
            name=t.name,
            category=t.category,
            risk_level=t.risk_level,
            description=t.description,
            parameters=t.parameters,
        )
        for t in tools
    ]

    return ToolListResponse(
        success=True,
        total=len(tool_items),
        categories=registry.get_categories(),
        tools=tool_items,
    )


@router.get(
    "/{tool_name}",
    response_model=ToolSchema,
    summary="Detalle de una herramienta específica",
    responses={404: {"model": ErrorResponse, "description": "Herramienta no encontrada"}},
)
def get_tool(
    tool_name: str,
    container: CoreContainer = Depends(get_container),
) -> ToolSchema:
    """Consulta los metadatos y el JSON schema de una herramienta específica."""
    tool = container.tool_registry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "TOOL_NOT_FOUND",
                "message": f"La herramienta '{tool_name}' no existe en el registro.",
            },
        )

    return ToolSchema(
        name=tool.name,
        category=tool.category,
        risk_level=tool.risk_level,
        description=tool.description,
        parameters=tool.parameters,
    )


@router.post(
    "/{tool_name}",
    response_model=ToolExecuteResponse,
    summary="Ejecución directa de una herramienta",
    responses={
        403: {"model": ErrorResponse, "description": "Permiso denegado por política de seguridad"},
        404: {"model": ErrorResponse, "description": "Herramienta no encontrada"},
        400: {"model": ErrorResponse, "description": "Error en parámetros de ejecución"},
    },
)
def execute_tool(
    tool_name: str,
    request: ToolExecuteRequest,
    container: CoreContainer = Depends(get_container),
) -> ToolExecuteResponse:
    """
    Ejecuta una herramienta de Morgan directamente.
    PASA OBLIGATORIAMENTE por la capa de permisos y auditoría para garantizar seguridad.
    """
    tool = container.tool_registry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "TOOL_NOT_FOUND",
                "message": f"La herramienta '{tool_name}' no existe en el registro.",
            },
        )

    # 1. Validación de argumentos contra el esquema de la herramienta.
    # La API ejecuta herramientas directamente, así que debe aplicar las mismas
    # comprobaciones que el bucle del agente.
    args = request.arguments or {}
    validation_error = validate_tool_args(tool, args)
    if validation_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_ARGUMENTS",
                "message": f"Parámetros inválidos para '{tool_name}': {validation_error}",
            },
        )

    # 2. Verificación en la capa de seguridad
    is_authorized = container.permission_manager.check_permission(tool, args)

    if not is_authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PERMISSION_DENIED",
                "message": f"Ejecución de la herramienta '{tool_name}' denegada por política de seguridad.",
                "details": {"risk_level": tool.risk_level, "arguments": args},
            },
        )

    # 3. Ejecución de la herramienta
    try:
        result = tool.execute(**args)
        return ToolExecuteResponse(
            success=result.get("success", False),
            tool=tool_name,
            risk_level=tool.risk_level,
            authorized=True,
            data=result.get("data"),
            error=result.get("error"),
        )
    except TypeError as te:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_ARGUMENTS",
                "message": f"Parámetros inválidos para '{tool_name}': {str(te)}",
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "TOOL_EXECUTION_ERROR",
                "message": f"Error interno ejecutando '{tool_name}': {str(e)}",
            },
        )
