"""
Roles y permisos (identidad, V2.0 adelantada).

Morgan distingue tres roles: quien lo usa, quien ayuda a administrarlo y quien es
su propietario. La regla que gobierna este módulo es una sola y es la razón de que
exista:

> **La autorización se decide por permisos, nunca por quién eres.**

Nada de `if user.email == "..."`, `if username == "andy"` ni `if user_id == 1`.
Esas condiciones se reparten por el código, se olvidan al añadir una ruta, y
cambiar de propietario obliga a tocar ficheros. Aquí la cadena es siempre:

    usuario → rol → permisos → autorización

Así, dar o quitar capacidades es cambiar una fila de una tabla, no editar código.

## Privilegio no es ausencia de seguridad

Un Owner puede hacer **más cosas**, no saltarse las comprobaciones de las cosas que
hace. Sigue pasando por la validación de comandos, la protección de rutas, la
auditoría y los límites de integridad. Lo que se le levanta son los topes que
existen para **repartir un recurso compartido** —el cupo diario de llamadas al
modelo, que se paga con sus propias claves—, no las barreras que existen para
evitar destrozos.
"""

from enum import Enum


class Rol(str, Enum):
    """Qué es alguien dentro de esta instalación de Morgan.

    Hereda de `str` para que se guarde y viaje como texto sin conversiones: en la
    base es una columna de texto y en JSON es una cadena.
    """

    USER = "user"
    ADMIN = "admin"
    OWNER = "owner"

    @classmethod
    def desde(cls, valor: str | None) -> "Rol":
        """Interpreta un valor guardado. Lo que no reconoce es `USER`.

        Fail-safe deliberado: una fila corrupta, un rol de una versión futura o
        un valor escrito a mano dan el rol de **menos** privilegio. Lo contrario
        —tratar lo desconocido como administrador— convertiría cualquier error de
        datos en una escalada.
        """
        try:
            return cls(str(valor or "").strip().lower())
        except ValueError:
            return cls.USER


class Permiso(str, Enum):
    """Lo que se puede autorizar, por separado.

    Existen sueltos y no como «es admin» para poder ampliar o recortar sin
    rehacer nada: el día que Morgan necesite un rol que solo lea la auditoría, se
    compone con los permisos que ya están aquí.
    """

    # Lo que hace cualquiera con sus propias cosas no necesita permiso: sus
    # conversaciones, su memoria, sus archivos y sus conexiones ya estan
    # aisladas por usuario. Aqui solo esta lo que va MAS ALLA de uno mismo.
    USUARIOS_LEER = "users.read"
    USUARIOS_GESTIONAR = "users.manage"
    AUDITORIA_LEER = "audit.read"
    SISTEMA_GESTIONAR = "system.manage"
    INTEGRACIONES_GESTIONAR = "integrations.manage"
    AJUSTES_GESTIONAR = "settings.manage"


# Qué puede cada rol. Cada uno incluye lo del anterior, que es lo que la gente
# espera de una escala de privilegios y evita sorpresas del tipo "el owner no
# puede hacer algo que sí puede el admin".
PERMISOS_POR_ROL: dict[Rol, frozenset[Permiso]] = {
    Rol.USER: frozenset(),
    Rol.ADMIN: frozenset({
        Permiso.USUARIOS_LEER,
        Permiso.AUDITORIA_LEER,
    }),
    Rol.OWNER: frozenset({
        Permiso.USUARIOS_LEER,
        Permiso.USUARIOS_GESTIONAR,
        Permiso.AUDITORIA_LEER,
        Permiso.SISTEMA_GESTIONAR,
        Permiso.INTEGRACIONES_GESTIONAR,
        Permiso.AJUSTES_GESTIONAR,
    }),
}


def permisos_de(rol: Rol) -> frozenset[Permiso]:
    return PERMISOS_POR_ROL.get(rol, frozenset())


def puede(rol: Rol, permiso: Permiso) -> bool:
    """Si ese rol tiene ese permiso. Es la única pregunta que hay que hacer."""
    return permiso in permisos_de(rol)


class PermisoDenegado(PermissionError):
    """Falta el permiso necesario.

    El mensaje dice **qué permiso** falta, no quién eres. A quien se lo encuentra
    por error le sirve para entenderlo; a quien lo provoca a propósito no le dice
    nada que no supiera ya al recibir el rechazo.
    """

    def __init__(self, permiso: Permiso):
        self.permiso = permiso
        super().__init__(f"Esta acción necesita el permiso '{permiso.value}'.")


def exigir(rol: Rol, permiso: Permiso) -> None:
    """Lanza si ese rol no basta."""
    if not puede(rol, permiso):
        raise PermisoDenegado(permiso)


# --- Cupo ---------------------------------------------------------------------


def propietario_con_cuenta(user_id: str, rol: Rol) -> bool:
    """Si quien pide algo es el propietario **autenticado**, no el modo sin cuentas.

    La distinción importa y costó diez pruebas descubrirla. El usuario implícito
    —el Morgan de tu equipo— tiene rol de propietario porque allí mandas tú, pero
    eso es el *modo sin cuentas*, no una identidad con privilegios: en la CLI hay
    una consola con la que preguntar, y las confirmaciones ahí no estorban, te
    protegen de que el modelo se equivoque.

    Las exenciones de este módulo son para el dueño que ha entrado con su cuenta
    en un entorno donde no hay a quién preguntar.
    """
    from src.identidad.modelos import USUARIO_LOCAL

    return rol is Rol.OWNER and user_id != USUARIO_LOCAL


def sin_cupo(rol: Rol) -> bool:
    """Si a ese rol no se le aplican los límites de consumo.

    Cubre el cupo diario de llamadas al modelo y también los límites de archivos
    subidos: número, tamaño y espacio total.

    Todos ellos existen por la misma razón —**repartir un recurso que paga el
    dueño**— y aplicárselos al dueño no protege de nada. Es su dinero, su disco y
    su decisión.

    Los administradores sí los tienen. Administrar Morgan no es pagarlo, y no
    conviene que ayudar a moderar traiga barra libre de un recurso ajeno.
    """
    return rol is Rol.OWNER


def sin_confirmaciones(rol: Rol) -> bool:
    """Si a ese rol se le ejecutan las herramientas sin pedirle confirmación.

    Confirmar es preguntarle al dueño si autoriza algo **en su propio sistema**,
    y en un entorno sin consola —la API, la web— esa pregunta no se puede hacer:
    hoy se resuelve denegando, que para el propietario significa no poder usar
    media herramienta de Morgan.

    Lo que NO se levanta con esto, y conviene tener claro por qué:

    - **La validación de comandos y rutas.** No protegen a Morgan de ti; te
      protegen a ti de que el **modelo** proponga `format C:` por su cuenta. Quien
      escribe esos comandos es el LLM, no la persona, así que quitarlos por ser
      dueño sería confundir quién manda con quién teclea.
    - **La auditoría.** Sigue registrándolo todo, y precisamente por eso: si algo
      sale mal, poder reconstruir qué se ejecutó vale más cuanto mayor es el
      privilegio.
    - **Las herramientas que no existen en el entorno.** En la nube, las que tocan
      la máquina no están registradas. Y no es una restricción al dueño: esa
      máquina es un contenedor de Render, no su PC. Dárselas no le daría acceso a
      su ordenador, se lo daría al servidor.
    """
    return rol is Rol.OWNER
