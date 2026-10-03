"""
Herramientas de GitHub (V1.9).

Morgan puede consultar los repositorios de **la persona que le está hablando**,
usando la autorización que esa persona concedió desde Servicios.

## Solo lectura, y es una decisión

El token que GitHub concede permite escribir —`repo` no tiene variante de solo
lectura para repositorios privados en las OAuth Apps clásicas—, pero **estas
herramientas solo leen**. La especificación lo pedía con estas palabras: «no
implementar automáticamente todas las capacidades de GitHub solo porque OAuth
esté conectado».

Añadir «crear issue» o «hacer commit» es fácil desde aquí, y por eso conviene
decir qué haría falta antes: pasar por el sistema de planes, para que la persona
vea qué se va a escribir y dónde **antes** de que ocurra. Escribir en el
repositorio de alguien sin ese paso es exactamente lo que el sistema de permisos
de Morgan existe para impedir.

## El usuario sale del contexto, nunca del argumento

Ninguna herramienta recibe un identificador de usuario. El token se busca en el
repositorio de integraciones, que filtra por el usuario de la petición. Si fuera
un parámetro, el modelo podría inventárselo — y un modelo que puede nombrar a
otro usuario en una llamada a herramienta es un modelo que puede leer sus repos.
"""

import logging
import time
from typing import Any

from src.integraciones import github as gh
from src.tools.base import RiskLevel, Tool, ToolCategory

logger = logging.getLogger(__name__)


#: Si cada persona tiene GitHub conectado, recordado un rato (4.1.5): `disponible()` se
#: pregunta en cada llamada al modelo, y cuatro herramientas por llamada serían cuatro
#: consultas a la base. Conectar o desconectar lo borra (`olvidar_conexiones`).
RECUERDA_CONEXION = 30.0
_conectados: dict[str, tuple[float, bool]] = {}


def olvidar_conexiones() -> None:
    """Tras conectar o desconectar un servicio: la próxima vez se pregunta a la base."""
    _conectados.clear()


class _HerramientaDeGitHub(Tool):
    """Lo común: encontrar el token de quien pregunta, y fallar con sentido."""

    def __init__(self, repositorios):
        self.repositorios = repositorios

    @property
    def category(self) -> str:
        return ToolCategory.GIT.value

    @property
    def requires_local(self) -> bool:
        # Habla con la API de GitHub, no con el disco. Funciona igual en la nube.
        return False

    @property
    def permission_level(self) -> str:
        # Solo lectura sobre datos que la persona ya autorizó a leer.
        return RiskLevel.SAFE.value

    motivo_no_disponible = ("GitHub no está conectado a esta cuenta; se conecta desde la sección "
                            "Servicios de Morgan.")

    def disponible(self) -> bool:
        """Solo si quien pregunta tiene GitHub conectado (4.1.5).

        Sin conectar, estas cuatro herramientas solo saben contestar «conéctalo», y
        viajaban igual en **cada** llamada al modelo: unos 2.200 caracteres (~550
        tokens) para casi todas las cuentas, con el cupo de Groq por minuto ya al
        límite. Sin ellas, el prompt dice cómo conectarlo. Si no se puede saber, se
        ofrecen: la herramienta ya dice qué pasa."""
        from src.identidad import usuario_actual
        from src.integraciones.repositorio import repositorio_de_integraciones

        usuario = usuario_actual()
        ahora = time.monotonic()
        guardado = _conectados.get(usuario)
        if guardado is not None and ahora - guardado[0] < RECUERDA_CONEXION:
            return guardado[1]
        try:
            conectado = repositorio_de_integraciones(self.repositorios).obtener("github") is not None
        except Exception:
            logger.debug("No se pudo mirar si GitHub está conectado", exc_info=True)
            return True
        _conectados[usuario] = (ahora, conectado)
        return conectado

    def _token(self) -> str | None:
        """El token de quien hace esta petición, o None si no ha conectado.

        Se busca aquí y no se recibe por parámetro: el usuario sale del contexto
        de la petición. Un identificador en los argumentos sería algo que el
        modelo puede inventarse.
        """
        from src.integraciones.repositorio import repositorio_de_integraciones

        return repositorio_de_integraciones(self.repositorios).token_de("github")

    @staticmethod
    def _ok(datos: Any) -> dict:
        return {"success": True, "data": datos, "error": None}

    @staticmethod
    def _error(mensaje: str) -> dict:
        return {"success": False, "data": None, "error": mensaje}

    def _sin_conectar(self) -> dict:
        # Se dice QUÉ hacer, no solo qué falta. «No autorizado» deja a la
        # persona sin saber que la solución está a dos clics.
        return self._error(
            "GitHub no está conectado a esta cuenta. Se conecta desde la "
            "sección Servicios de Morgan, y ahí se ve también qué permisos se "
            "conceden."
        )

    def _ejecutar(self, token: str, **kwargs) -> dict:
        raise NotImplementedError

    def execute(self, **kwargs: Any) -> dict:
        token = self._token()
        if not token:
            return self._sin_conectar()

        try:
            return self._ejecutar(token, **kwargs)
        except gh.ErrorDeGitHub as exc:
            # El mensaje ya viene escrito para leerse: dice si hay que esperar,
            # reconectar o si simplemente no existe.
            return self._error(str(exc))
        except ValueError as exc:
            return self._error(str(exc))


def _repo_valido(repo: str) -> str:
    """Comprueba la forma `duenyo/nombre` antes de meterla en una URL.

    No es paranoia: el valor lo compone el modelo a partir de lo que le dice la
    persona, y lo que le dicen puede venir de un archivo que acaba de leer.

    **Comprobar la forma no bastaba.** `'../..'` tiene dos trozos y ninguna
    barra de más, así que pasaba — y `/repos/../../issues` se normaliza a
    `/issues`. Los trozos que son `.` o `..` dejan de ser nombres y pasan a ser
    instrucciones para la URL, así que se rechazan por su nombre.
    """
    limpio = (repo or "").strip().strip("/")
    partes = limpio.split("/")

    if len(partes) != 2 or not all(partes):
        raise ValueError(
            f"'{repo}' no parece un repositorio. Se escribe como "
            "'duenyo/nombre', por ejemplo 'wv-Andy/Morgan'."
        )
    if any(p in (".", "..") for p in partes):
        raise ValueError(
            f"'{repo}' no es un repositorio: '.' y '..' no son nombres."
        )
    return limpio


class ListarReposTool(_HerramientaDeGitHub):
    """Qué repositorios tiene la persona. Suele ser el primer paso de todo."""

    @property
    def name(self) -> str:
        return "github_listar_repos"

    @property
    def description(self) -> str:
        return (
            "Lista los repositorios de GitHub del usuario, del más recientemente "
            "actualizado al más antiguo. Incluye los privados y los de sus "
            "organizaciones. Úsalo cuando pregunte por sus repos o cuando "
            "necesites el nombre exacto de uno antes de consultarlo."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limite": {
                    "type": "integer",
                    "description": "Cuántos devolver (por defecto 30, máximo 100)",
                },
            },
        }

    def _ejecutar(self, token: str, **kwargs) -> dict:
        limite = int(kwargs.get("limite") or 30)
        repos = gh.listar_repos(token, limite=limite)

        if not repos:
            return self._ok({
                "total": 0,
                "repositorios": [],
                "nota": (
                    "La cuenta conectada no tiene repositorios visibles con los "
                    "permisos concedidos."
                ),
            })

        return self._ok({"total": len(repos), "repositorios": repos})


class ListarIssuesTool(_HerramientaDeGitHub):
    @property
    def name(self) -> str:
        return "github_listar_issues"

    @property
    def description(self) -> str:
        return (
            "Lista las issues de un repositorio de GitHub. No incluye pull "
            "requests, aunque la API de GitHub los mezcle. Úsalo para saber qué "
            "hay pendiente o para buscar si algo ya se reportó."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "El repositorio, como 'duenyo/nombre'",
                },
                "estado": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": "Abiertas (por defecto), cerradas o todas",
                },
                "limite": {"type": "integer", "description": "Cuántas devolver"},
            },
            "required": ["repo"],
        }

    def _ejecutar(self, token: str, **kwargs) -> dict:
        repo = _repo_valido(kwargs.get("repo", ""))
        issues = gh.listar_issues(
            token, repo,
            estado=kwargs.get("estado") or "open",
            limite=int(kwargs.get("limite") or 20),
        )
        return self._ok({"repositorio": repo, "total": len(issues), "issues": issues})


class ListarPullRequestsTool(_HerramientaDeGitHub):
    @property
    def name(self) -> str:
        return "github_listar_prs"

    @property
    def description(self) -> str:
        return (
            "Lista los pull requests de un repositorio de GitHub, con su rama de "
            "origen y destino. Úsalo para ver qué está en revisión."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "El repositorio, como 'duenyo/nombre'",
                },
                "estado": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": "Abiertos (por defecto), cerrados o todos",
                },
                "limite": {"type": "integer", "description": "Cuántos devolver"},
            },
            "required": ["repo"],
        }

    def _ejecutar(self, token: str, **kwargs) -> dict:
        repo = _repo_valido(kwargs.get("repo", ""))
        prs = gh.listar_prs(
            token, repo,
            estado=kwargs.get("estado") or "open",
            limite=int(kwargs.get("limite") or 20),
        )
        return self._ok({"repositorio": repo, "total": len(prs), "pull_requests": prs})


class LeerArchivoTool(_HerramientaDeGitHub):
    """Leer código de un repositorio, que es para lo que más se pide GitHub."""

    @property
    def name(self) -> str:
        return "github_leer_archivo"

    @property
    def description(self) -> str:
        return (
            "Lee un archivo de un repositorio de GitHub. Si la ruta es un "
            "directorio, devuelve su contenido. Úsalo para mirar código, "
            "configuración o documentación de sus repos antes de opinar sobre "
            "ellos."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "El repositorio, como 'duenyo/nombre'",
                },
                "ruta": {
                    "type": "string",
                    "description": "Ruta dentro del repositorio, como 'src/main.py'",
                },
                "rama": {
                    "type": "string",
                    "description": "Rama o commit. Por defecto, la rama principal",
                },
            },
            "required": ["repo", "ruta"],
        }

    def _ejecutar(self, token: str, **kwargs) -> dict:
        repo = _repo_valido(kwargs.get("repo", ""))
        # La validacion de verdad esta en `gh.leer_archivo`, que es por donde
        # pasa venga de donde venga. Aqui solo se limpia lo cosmetico.
        ruta = (kwargs.get("ruta") or "").strip().lstrip("/")

        datos = gh.leer_archivo(token, repo, ruta, rama=kwargs.get("rama"))

        if datos.get("tipo") == "archivo":
            # Lo que hay en un repositorio es contenido de terceros, igual que
            # una pagina web: dato, no instruccion. Se marca para que el modelo
            # no trate un comentario del codigo como una orden.
            datos["contenido"] = (
                f'<untrusted_file_data source="{repo}/{ruta}">\n'
                f"{datos['contenido']}\n</untrusted_file_data>"
            )

        return self._ok(datos)


def github_tools(repositorios) -> list[Tool]:
    """Las herramientas de GitHub, todas de solo lectura.

    Se registran **siempre**, conectado o no: si solo aparecieran con GitHub ya
    conectado, el modelo no sabría que existen y nunca sugeriría conectarlo.
    Sin conexión responden diciendo dónde se conecta, que es más útil que no
    estar.
    """
    return [
        ListarReposTool(repositorios),
        ListarIssuesTool(repositorios),
        ListarPullRequestsTool(repositorios),
        LeerArchivoTool(repositorios),
    ]
