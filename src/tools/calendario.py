"""
Herramientas de Google Calendar (V2.0.17).

Lo decidí: Morgan **lee** tu agenda y **escribe** en ella, pero toda
escritura pasa por un plan que apruebas antes (`Tool.exige_plan`, V2.0.16).

| Herramienta | Qué hace | Cómo se ejecuta |
|---|---|---|
| `calendario_ver_eventos` | Los eventos de un rango de días | Sin preguntar: solo lee |
| `calendario_crear_evento` | Crea un evento | **Solo** con un paso de plan aprobado, esos argumentos y una vez |

## La zona horaria es la del calendario

«Mañana a las 10» es en la zona de la persona, no en la del servidor, que en
Render es UTC. La zona sale del propio calendario de Google, y las horas que se
le dan y se le piden al modelo van en ella.

## El usuario sale del contexto, nunca del argumento

Igual que GitHub: ninguna recibe un identificador de usuario. El token se busca
en el repositorio de integraciones, que filtra por quien hace la petición.

## Se registran siempre, conectado o no

Si solo aparecieran con el calendario conectado, el modelo no sabría que existen
y nunca sugeriría conectarlo. Sin conexión responden diciendo dónde se conecta.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.integraciones import google_calendar as gc
from src.integraciones.oauth import (
    AutorizacionCaducada,
    ErrorDelServicio,
    ServicioNoConfigurado,
    token_vigente,
)
from src.tools.base import RiskLevel, Tool, ToolCategory

logger = logging.getLogger(__name__)

SERVICIO = "google_calendar"
MAX_DIAS = 31


class _HerramientaDeCalendario(Tool):
    """Lo común: el token vigente de quien pregunta, y fallar diciendo qué hacer."""

    def __init__(self, repositorios):
        self.repositorios = repositorios

    @property
    def category(self) -> str:
        return ToolCategory.GENERAL.value

    @property
    def requires_local(self) -> bool:
        # Habla con la API de Google, no con el disco. Funciona igual en la nube.
        return False

    def _repo(self):
        from src.integraciones.repositorio import repositorio_de_integraciones

        return repositorio_de_integraciones(self.repositorios)

    @staticmethod
    def _ok(datos: Any) -> dict:
        return {"success": True, "data": datos, "error": None}

    @staticmethod
    def _error(mensaje: str) -> dict:
        return {"success": False, "data": None, "error": mensaje}

    def _ejecutar(self, trabajo) -> dict:
        """Busca el token y ejecuta `trabajo(token)`, traduciendo cada fallo a qué hacer."""
        try:
            token = token_vigente(self._repo(), SERVICIO)
            if token is None:
                return self._error(
                    "Google Calendar no está conectado. La persona puede conectarlo en "
                    "Servicios; hasta entonces no hay agenda que consultar."
                )
            return trabajo(token)
        except AutorizacionCaducada as exc:
            return self._error(str(exc))
        except ServicioNoConfigurado:
            return self._error(
                "Este Morgan no tiene configurada la conexión con Google Calendar. "
                "Quien lo administra tiene que añadir las credenciales de Google."
            )
        except ErrorDelServicio as exc:
            try:
                self._repo().anotar_error(SERVICIO, str(exc))
            except Exception:
                logger.debug("No se pudo anotar el error de Calendar", exc_info=True)
            return self._error(str(exc))


def _zona(nombre: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(nombre or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


class VerEventosTool(_HerramientaDeCalendario):
    @property
    def name(self) -> str:
        return "calendario_ver_eventos"

    @property
    def description(self) -> str:
        return (
            "Consulta los eventos del Google Calendar de la persona en un rango de días. "
            "Devuelve también la zona horaria del calendario y la hora actual en ella: "
            "úsalas para entender «hoy», «mañana» o «a las 10»."
        )

    @property
    def permission_level(self) -> str:
        # Solo lee datos que la persona autorizó a leer.
        return RiskLevel.SAFE.value

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "desde": {
                    "type": "string",
                    "description": "Primer día, en formato AAAA-MM-DD. Por defecto, hoy.",
                },
                "dias": {
                    "type": "integer",
                    "description": f"Cuántos días mirar, de 1 a {MAX_DIAS}. Por defecto 7.",
                },
            },
            "required": [],
        }

    def execute(self, desde: str | None = None, dias: int = 7, **kwargs: Any) -> dict:
        try:
            dias = max(1, min(int(dias), MAX_DIAS))
            dia = date.fromisoformat(desde) if desde else None
        except (TypeError, ValueError):
            return self._error("«desde» tiene que ser una fecha AAAA-MM-DD, y «dias» un número.")

        def trabajo(token: str) -> dict:
            zona = _zona(gc.zona_horaria(token))
            ahora = datetime.now(zona)
            inicio = datetime.combine(dia, time.min, zona) if dia else ahora
            fin = datetime.combine((dia or ahora.date()) + timedelta(days=dias), time.min, zona)
            resultado = gc.listar_eventos(token, inicio, fin)
            return self._ok({
                "zona_horaria": str(zona),
                "ahora": ahora.isoformat(timespec="minutes"),
                "desde": inicio.isoformat(timespec="minutes"),
                "hasta": fin.isoformat(timespec="minutes"),
                "total": len(resultado["eventos"]),
                "eventos": resultado["eventos"],
            })

        return self._ejecutar(trabajo)


class CrearEventoTool(_HerramientaDeCalendario):
    @property
    def name(self) -> str:
        return "calendario_crear_evento"

    @property
    def description(self) -> str:
        return (
            "Crea un evento en el Google Calendar de la persona. SOLO se ejecuta con un "
            "plan aprobado: propón primero create_plan con este paso y sus argumentos, y "
            "llámala con esos mismos argumentos cuando la persona lo apruebe. Las horas "
            "van en la zona del calendario, que da calendario_ver_eventos."
        )

    @property
    def permission_level(self) -> str:
        return RiskLevel.MODERATE.value

    @property
    def exige_plan(self) -> bool:
        # Escribe en el calendario de la persona, fuera de Morgan. Lo
        # decidí: siempre con plan aprobado, también el propietario.
        return True

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "titulo": {"type": "string", "description": "Título del evento."},
                "inicio": {
                    "type": "string",
                    "description": "Cuándo empieza, AAAA-MM-DDTHH:MM, en la zona del calendario.",
                },
                "fin": {
                    "type": "string",
                    "description": "Cuándo acaba, AAAA-MM-DDTHH:MM, en la zona del calendario.",
                },
                "descripcion": {"type": "string", "description": "Notas del evento (opcional)."},
                "lugar": {"type": "string", "description": "Dónde es (opcional)."},
            },
            "required": ["titulo", "inicio", "fin"],
        }

    def execute(
        self, titulo: str, inicio: str, fin: str,
        descripcion: str | None = None, lugar: str | None = None, **kwargs: Any,
    ) -> dict:
        titulo = (titulo or "").strip()
        if not titulo or len(titulo) > 200:
            return self._error("El título es obligatorio y tiene como mucho 200 caracteres.")
        try:
            empieza = datetime.fromisoformat(inicio)
            acaba = datetime.fromisoformat(fin)
        except (TypeError, ValueError):
            return self._error("«inicio» y «fin» tienen que ser AAAA-MM-DDTHH:MM.")
        if (empieza.tzinfo is None) != (acaba.tzinfo is None):
            return self._error("«inicio» y «fin» tienen que venir los dos con zona o los dos sin ella.")
        if acaba <= empieza:
            return self._error("El evento tiene que acabar después de empezar.")

        def trabajo(token: str) -> dict:
            zona = gc.zona_horaria(token) or "UTC"
            evento = gc.crear_evento(
                token, titulo,
                # Sin la zona en el texto: va aparte, en `timeZone`, que es como la
                # interpreta Google. Con las dos, manda el desfase del texto.
                empieza.replace(tzinfo=None).isoformat(timespec="minutes") if empieza.tzinfo is None else empieza.isoformat(),
                acaba.replace(tzinfo=None).isoformat(timespec="minutes") if acaba.tzinfo is None else acaba.isoformat(),
                zona, descripcion=descripcion, lugar=lugar,
            )
            return self._ok({"creado": evento, "zona_horaria": zona})

        return self._ejecutar(trabajo)


def calendario_tools(repositorios) -> list[Tool]:
    return [VerEventosTool(repositorios), CrearEventoTool(repositorios)]


