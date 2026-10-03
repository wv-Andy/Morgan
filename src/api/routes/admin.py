"""
Rutas administrativas (identidad, V2.0 adelantada).

Lo que solo puede hacer quien administra Morgan: ver las cuentas, cambiar roles y
suspender a alguien. Todas exigen un **permiso**, no un rol, para que ampliar o
recortar lo que puede cada uno no obligue a tocar estas rutas.

Tres reglas que gobiernan este módulo:

- **Privilegio no es ausencia de seguridad.** Un propietario puede cambiar roles;
  no puede saltarse las validaciones ni dejar de aparecer en la auditoría. Cada
  acción de aquí queda registrada con quién la hizo y sobre quién.
- **Nadie se eleva a sí mismo.** Cambiarse el propio rol está prohibido incluso
  teniendo el permiso: es la vía por la que una cuenta comprometida se
  consolidaría, y no hay ningún caso legítimo que lo necesite.
- **La propiedad no se traspasa por descuido.** Crear un segundo propietario está
  bloqueado aquí; el traspaso es una operación deliberada que hoy no existe, y es
  mejor que no exista a que exista a medias.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.autorizacion import exigir, usuario_de_la_peticion
from src.api.dependencies import CoreContainer, get_container
from src.identidad.modelos import MorganUser
from src.identidad.repositorio import repositorio_de_cuentas
from src.identidad.roles import Permiso, Rol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Administración"])

ESTADOS = ("activo", "suspendido")


class CambiarRolRequest(BaseModel):
    rol: str = Field(..., description="'user' o 'admin'")


class CambiarEstadoRequest(BaseModel):
    status: str = Field(..., description="'activo' o 'suspendido'")


def _repo(container: CoreContainer):
    return repositorio_de_cuentas(container.repositories)


def _publicable(fila: dict) -> dict:
    """Lo que se puede enseñar de una cuenta ajena.

    Se construye por lista blanca, no quitando campos: así, una columna nueva en
    la tabla —un token, un hash, lo que sea— no se publica sola el día que
    alguien la añada sin acordarse de esta ruta.
    """
    return {
        "id": fila.get("id"),
        "username": fila.get("username"),
        "email": fila.get("email"),
        "display_name": fila.get("display_name"),
        "rol": Rol.desde(fila.get("role")).value,
        "status": fila.get("status"),
        "email_verificado": bool(fila.get("email_verificado")),
        "creado_en": fila.get("creado_en"),
        "ultima_actividad": fila.get("ultima_actividad"),
    }


@router.get("/usuarios", summary="Listar las cuentas")
def listar_usuarios(
    actor: MorganUser = Depends(exigir(Permiso.USUARIOS_LEER)),
    container: CoreContainer = Depends(get_container),
) -> dict:
    filas = _repo(container).listar_usuarios(limite=200)
    return {"success": True, "usuarios": [_publicable(f) for f in filas]}


@router.get("/yo/permisos", summary="Qué puedo hacer")
def mis_permisos(usuario: MorganUser = Depends(usuario_de_la_peticion)) -> dict:
    """Lo que la interfaz necesita para decidir qué enseñar.

    No es una comprobación de seguridad: es para no ofrecer botones que van a dar
    403. Quien vale es la comprobación de cada ruta.
    """
    from src.identidad.roles import permisos_de

    return {
        "success": True,
        "rol": usuario.rol_efectivo.value,
        "permisos": sorted(p.value for p in permisos_de(usuario.rol_efectivo)),
    }


@router.post("/usuarios/{user_id}/rol", summary="Cambiar el rol de una cuenta")
def cambiar_rol(
    user_id: str,
    datos: CambiarRolRequest,
    actor: MorganUser = Depends(exigir(Permiso.USUARIOS_GESTIONAR)),
    container: CoreContainer = Depends(get_container),
) -> dict:
    if user_id == actor.id:
        # Ni para subir ni para bajar. Es la via por la que una cuenta
        # comprometida se consolidaria, y no hay caso legitimo que lo necesite.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "ROL_PROPIO",
                "message": "No puedes cambiar tu propio rol.",
            },
        )

    nuevo = Rol.desde(datos.rol)

    if nuevo is Rol.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "OWNER_UNICO",
                "message": "Solo puede haber un propietario, y no se asigna por aquí.",
            },
        )

    if datos.rol.strip().lower() not in (Rol.USER.value, Rol.ADMIN.value):
        # `Rol.desde` convierte lo desconocido en USER de forma silenciosa, que
        # esta bien al leer de la base pero no al recibir una orden: aqui hay que
        # decir que el valor no vale, no degradar a alguien por una errata.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ROL_INVALIDO", "message": "El rol debe ser 'user' o 'admin'."},
        )

    repo = _repo(container)
    objetivo = repo.obtener(user_id)

    if objetivo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_ENCONTRADO", "message": "No hay ninguna cuenta con ese identificador."},
        )

    if Rol.desde(objetivo.get("role")) is Rol.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "OWNER_INTOCABLE",
                "message": "No se puede cambiar el rol del propietario.",
            },
        )

    repo.actualizar(user_id, {"role": nuevo.value})
    container.audit_logger.registrar_evento(
        accion="user.update_role",
        actor=actor.id,
        objetivo=user_id,
        detalle=f"{Rol.desde(objetivo.get('role')).value} -> {nuevo.value}",
    )

    return {"success": True, "usuario": _publicable({**objetivo, "role": nuevo.value})}


@router.post("/usuarios/{user_id}/estado", summary="Suspender o reactivar una cuenta")
def cambiar_estado(
    user_id: str,
    datos: CambiarEstadoRequest,
    actor: MorganUser = Depends(exigir(Permiso.USUARIOS_GESTIONAR)),
    container: CoreContainer = Depends(get_container),
) -> dict:
    nuevo = datos.status.strip().lower()

    if nuevo not in ESTADOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ESTADO_INVALIDO", "message": f"El estado debe ser uno de: {', '.join(ESTADOS)}."},
        )

    if user_id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ESTADO_PROPIO", "message": "No puedes suspender tu propia cuenta."},
        )

    repo = _repo(container)
    objetivo = repo.obtener(user_id)

    if objetivo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_ENCONTRADO", "message": "No hay ninguna cuenta con ese identificador."},
        )

    if Rol.desde(objetivo.get("role")) is Rol.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "OWNER_INTOCABLE",
                "message": "No se puede suspender al propietario.",
            },
        )

    repo.actualizar(user_id, {"status": nuevo})

    if nuevo == "suspendido":
        # Las sesiones abiertas caen ahora, no dentro de treinta dias. Suspender
        # a alguien que sigue dentro no es suspender a nadie.
        repo.revocar_sesiones(user_id, None)

    container.audit_logger.registrar_evento(
        accion="user.update_status",
        actor=actor.id,
        objetivo=user_id,
        detalle=f"{objetivo.get('status')} -> {nuevo}",
    )

    return {"success": True, "usuario": _publicable({**objetivo, "status": nuevo})}
