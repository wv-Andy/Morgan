"""
Autorización de las rutas administrativas (identidad, V2.0 adelantada).

Una sola pieza, para que exigir un permiso sea una línea y no haya dos formas de
hacerlo. La cadena completa, en este orden:

    sesión válida → usuario → rol → permiso → operación

**El rol se resuelve siempre en el servidor**, leyendo la fila del usuario. Nunca
viene del cliente: ni de una cabecera, ni de una cookie, ni de un campo del cuerpo.
Un `is_admin: true` enviado por quien sea no cambia absolutamente nada, porque no
se mira en ningún sitio.

Ocultar un botón en la interfaz no es seguridad. La interfaz esconde lo que no
corresponde por comodidad y por claridad; quien vale es esta comprobación.
"""

import logging

from fastapi import Depends, HTTPException, status

from src.api.dependencies import CoreContainer, get_container
from src.identidad import USUARIO_LOCAL, usuario_actual
from src.identidad.cuentas import ServicioDeCuentas
from src.identidad.modelos import MorganUser, usuario_local
from src.identidad.repositorio import repositorio_de_cuentas
from src.identidad.roles import Permiso, Rol, puede

logger = logging.getLogger(__name__)


def usuario_de_la_peticion(
    container: CoreContainer = Depends(get_container),
) -> MorganUser:
    """Quién hace esta petición, con su rol leído de la base.

    El usuario implícito no está en ninguna tabla, así que se construye: es el
    Morgan de tu equipo, y allí mandas tú.
    """
    user_id = usuario_actual()

    if user_id == USUARIO_LOCAL:
        return usuario_local()

    servicio = ServicioDeCuentas(repositorio_de_cuentas(container.repositories))
    usuario = servicio.obtener(user_id)

    if usuario is None:
        # La sesion era valida al entrar por el middleware pero la cuenta ya no
        # esta. Es raro y no conviene inventarse un rol: se corta.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "SIN_CUENTA", "message": "Tu cuenta ya no está disponible."},
        )

    return usuario


def exigir(permiso: Permiso):
    """Dependencia que exige un permiso concreto.

    Se usa así, y devuelve el usuario para no tener que pedirlo dos veces:

        @router.get("/algo")
        def algo(usuario = Depends(exigir(Permiso.USUARIOS_LEER))):
            ...
    """

    def comprobar(
        usuario: MorganUser = Depends(usuario_de_la_peticion),
    ) -> MorganUser:
        if not puede(usuario.rol_efectivo, permiso):
            # Se registra el intento: alguien probando rutas administrativas es
            # justo lo que conviene poder ver despues.
            logger.warning(
                "Permiso denegado: %s intentó una acción que exige '%s'",
                usuario.id, permiso.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PERMISO_DENEGADO",
                    # Se dice que permiso falta, no quien es quien lo tiene:
                    # a quien llega aqui por error le sirve, y a quien lo prueba
                    # a proposito no le dice nada nuevo.
                    "message": f"Esta acción necesita el permiso '{permiso.value}'.",
                },
            )
        return usuario

    return comprobar


def es_propietario(usuario: MorganUser) -> bool:
    return usuario.rol_efectivo is Rol.OWNER
