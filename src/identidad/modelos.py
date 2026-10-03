"""
Identidad de usuario en Morgan (identidad, V2.0 adelantada).

Una **cuenta de Morgan** no es la cuenta del proveedor. Google o GitHub sirven
para demostrar quién eres; el identificador con el que se relacionan todos tus
datos es de Morgan. Esa separación permite que la misma cuenta se asocie más
adelante con otro método de autenticación sin migrar una sola fila, y evita
quedar atado a un proveedor concreto.

**El usuario local existe siempre.** Morgan en tu ordenador no debe pedir login:
la API escucha en `127.0.0.1` y exigir OAuth para hablar con tu propio asistente
sería absurdo. Pero si en local no hubiera usuario, el aislamiento tendría dos
caminos distintos —uno con filtro y otro sin él— y tarde o temprano el segundo
dejaría escapar datos. Con un usuario implícito hay **un solo camino**: todo se
filtra siempre, y en local ese identificador vale `local`.

Lo mismo servirá para Morgan de escritorio, que es un backend local con la
interfaz envuelta: mismo código, mismo usuario implícito, sin login.
"""

import time
from dataclasses import dataclass
from typing import Any
from src.identidad.roles import Rol

# Identificador del usuario implícito. No es un valor mágico repartido por el
# código: se importa de aquí para que cambiarlo sea una sola edición.
USUARIO_LOCAL = "local"


@dataclass
class MorganUser:
    """Una cuenta de Morgan."""

    id: str
    auth_user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    creado_en: float = 0.0
    ultima_actividad: float | None = None
    rol: Rol = Rol.USER
    #: Si esa direccion se confirmo abriendo el enlace del correo. NO es una
    #: puerta: la cuenta funciona igual sin confirmar. Lo que cambia es que sin
    #: confirmar no se puede recuperar la contrasena, porque el enlace de
    #: recuperacion va justo a esa direccion.
    email_verificado: bool = False

    @property
    def es_local(self) -> bool:
        return self.id == USUARIO_LOCAL

    @property
    def rol_efectivo(self) -> Rol:
        """El rol con el que se decide qué puede hacer.

        El usuario implícito —el Morgan de tu equipo— manda en su instalación:
        allí no hay a quién distinguir y pedirle permisos a uno mismo no protege
        de nada. En la nube ese usuario no existe, porque se exige cuenta.
        """
        return Rol.OWNER if self.es_local else self.rol

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "creado_en": self.creado_en,
            "ultima_actividad": self.ultima_actividad,
            "es_local": self.es_local,
            "rol": self.rol_efectivo.value,
            "email_verificado": self.email_verificado,
            # `auth_user_id` NO se publica: es el identificador del proveedor y
            # no aporta nada al cliente, que ya sabe con qué cuenta entró.
        }


def usuario_local() -> MorganUser:
    """La cuenta implícita del Morgan de escritorio y de línea de comandos."""
    return MorganUser(
        id=USUARIO_LOCAL,
        display_name="Usuario local",
        creado_en=time.time(),
    )
