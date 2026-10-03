"""
Qué es una integración y qué servicios se conocen (V1.9).

El catálogo vive aquí y no en la base de datos porque no es un dato: es qué sabe
hacer Morgan. Añadir un servicio significa escribir el código que habla con él,
no insertar una fila.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ServicioExterno(str, Enum):
    """Los servicios que Morgan sabe conectar.

    El valor es el identificador que viaja en la URL y se guarda en la base, así
    que **no se renombra**: cambiarlo dejaría huérfanas las integraciones ya
    conectadas.
    """

    GITHUB = "github"
    GOOGLE_CALENDAR = "google_calendar"


@dataclass(frozen=True)
class Definicion:
    """Lo que hay que saber de un servicio para ofrecerlo."""

    servicio: ServicioExterno
    nombre: str
    descripcion: str
    #: Los permisos que se piden. **Mínimos y explícitos**: se pide leer, no
    #: administrar, y ampliarlos exige volver a autorizar. Un `repo` completo
    #: —que incluye borrar— por si acaso es exactamente lo que la
    #: especificación prohíbe.
    scopes: tuple[str, ...]
    #: Qué habilita, en lenguaje llano, para poder enseñarlo antes de conectar.
    permite: tuple[str, ...]
    #: Las variables de entorno sin las cuales no se puede ofrecer.
    variables: tuple[str, ...]


CATALOGO: dict[ServicioExterno, Definicion] = {
    ServicioExterno.GITHUB: Definicion(
        servicio=ServicioExterno.GITHUB,
        nombre="GitHub",
        descripcion="Consultar tus repositorios, issues y pull requests.",
        # 'read:user' para saber de quién es la cuenta conectada, y 'repo' para
        # los privados. GitHub no ofrece un permiso de solo lectura sobre
        # repositorios privados en las OAuth Apps clásicas: 'repo' es el mínimo
        # que permite verlos, y por eso la interfaz dice exactamente qué
        # habilita en lugar de dar el permiso por supuesto.
        scopes=("read:user", "repo"),
        permite=(
            "Ver tus repositorios, incluidos los privados",
            "Leer código, issues y pull requests",
            "Crear issues y comentarios cuando se lo pidas",
        ),
        variables=("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"),
    ),
}


#: Servicios con el código hecho y probado, pero **que no se ofrecen** (V2.0.18).
#:
#: decidí el 2026-09-16 posponer las integraciones con Google. No están en
#: `CATALOGO`, así que la web no enseña el botón de conectar y las rutas responden
#: 404; y sus herramientas no se registran, así que el modelo no las ve. El código
#: y sus pruebas siguen en el repositorio para retomarlo sin empezar de cero.
#: Aparcado en la 2.0.18 y descartado por mí el 2026-09-19: el código se conserva,
#: sin registrar sus herramientas.
APARCADOS: dict[ServicioExterno, Definicion] = {
    ServicioExterno.GOOGLE_CALENDAR: Definicion(
        servicio=ServicioExterno.GOOGLE_CALENDAR,
        nombre="Google Calendar",
        descripcion="Consultar tu agenda y apuntar eventos cuando apruebes el plan.",
        # `calendar.events` y no `calendar`: ver y editar EVENTOS, no crear,
        # compartir ni borrar calendarios enteros. Es el mínimo que permite
        # escribir, que es lo que decidí. `openid` y `email`, para saber de
        # quién es la cuenta conectada y enseñarlo.
        scopes=(
            "openid",
            "email",
            "https://www.googleapis.com/auth/calendar.events",
        ),
        permite=(
            "Ver los eventos de tus calendarios",
            "Crear eventos, solo cuando apruebes el plan que te proponga",
            "En modo de pruebas de Google: habrá que volver a conectar cada 7 días",
        ),
        variables=("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    ),
}


@dataclass
class Integracion:
    """Un servicio conectado por una persona concreta."""

    user_id: str
    servicio: str
    #: Cómo se llama la cuenta conectada, para poder enseñarla. No es secreto.
    cuenta: str | None = None
    scopes: tuple[str, ...] = ()
    creado_en: float = 0.0
    actualizado_en: float = 0.0
    #: El último error al hablar con el servicio, si lo hubo. Sirve para
    #: distinguir «no conectado» de «conectado y fallando», que para quien mira
    #: la pantalla son situaciones muy distintas.
    error: str | None = None
    metadatos: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Lo que se le puede contar al frontend.

        **El token no está aquí, y no es un olvido.** La interfaz nunca lo
        necesita: quien habla con GitHub es el backend. Devolverlo lo pondría al
        alcance de cualquier script inyectado en la página, y el daño no sería
        de Morgan sino de los repositorios de esa persona.
        """
        return {
            "servicio": self.servicio,
            "conectado": True,
            "cuenta": self.cuenta,
            "scopes": list(self.scopes),
            "creado_en": self.creado_en,
            "actualizado_en": self.actualizado_en,
            "error": self.error,
        }
