"""
La integración con GitHub (V1.9).

**Esto autoriza a Morgan a usar TU cuenta de GitHub. No es iniciar sesión.**
Ver la nota del paquete: son dos sistemas distintos y confundirlos es el error
más caro que se puede cometer aquí.

## El intercambio, y dónde está cada protección

```
1. La web pide conectar        ──►  Morgan emite un `state` y lo guarda
2. El navegador va a GitHub    ──►  la persona autoriza (o no)
3. GitHub devuelve al backend  ──►  con `code` y el mismo `state`
4. Morgan valida el `state`    ──►  y solo entonces canjea el `code`
5. El token se guarda cifrado  ──►  y el navegador vuelve a la web
```

**El paso 4 es el que importa.** Sin comprobar que el `state` que vuelve es uno
que se emitió, una web ajena podría completar el flujo y dejar *su* cuenta de
GitHub conectada a la sesión de otra persona. Morgan actuaría después sobre los
repositorios del atacante creyendo que son los tuyos, y todo lo que esa persona
le pidiera «sobre mi repo» pasaría por un repositorio ajeno.

**De quién es la autorización lo dice el `state`, no la cookie.** La vuelta de
GitHub es una navegación del navegador; usar la cookie de sesión sería
preguntarle al mismo canal que se está intentando verificar.

## Los permisos

Mínimos y explícitos, que es lo que pide la especificación. `read:user` para
saber de quién es la cuenta y `repo` para poder ver los privados —GitHub no
ofrece un permiso de solo lectura sobre repositorios privados en las OAuth Apps
clásicas—. Por eso la interfaz dice **exactamente qué habilita** en lugar de dar
el permiso por supuesto.

Que el token permita escribir no significa que Morgan escriba: las acciones
siguen pasando por el sistema de permisos y de confirmación de siempre. Conectar
GitHub no es una autorización en blanco.
"""

import logging
import os
from typing import Any

import httpx

from src.integraciones.oauth import Credenciales, ErrorDelServicio, ServicioNoConfigurado

logger = logging.getLogger(__name__)

AUTORIZAR = "https://github.com/login/oauth/authorize"
CANJEAR = "https://github.com/login/oauth/access_token"
API = "https://api.github.com"

TIMEOUT = 20.0


class GitHubNoConfigurado(ServicioNoConfigurado):
    """El servidor no tiene credenciales de la aplicación OAuth.

    No es un fallo del usuario ni algo que pueda arreglar: hace falta que quien
    administra el despliegue cree la aplicación en GitHub y ponga sus dos
    variables. Por eso la interfaz, en ese caso, **no enseña un botón de
    conectar** — enseña qué falta.
    """


class ErrorDeGitHub(ErrorDelServicio):
    """GitHub rechazó la operación o no respondió."""


def configurado() -> bool:
    return bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))


def _credenciales() -> tuple[str, str]:
    cliente = os.getenv("GITHUB_CLIENT_ID", "").strip()
    secreto = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    if not cliente or not secreto:
        raise GitHubNoConfigurado(
            "Faltan GITHUB_CLIENT_ID y GITHUB_CLIENT_SECRET."
        )
    return cliente, secreto


def url_de_autorizacion(estado: str, scopes: tuple[str, ...], callback: str) -> str:
    """A dónde mandar al navegador para que la persona autorice."""
    from urllib.parse import urlencode

    cliente, _ = _credenciales()
    parametros = {
        "client_id": cliente,
        "redirect_uri": callback,
        "scope": " ".join(scopes),
        "state": estado,
        # No se le ofrece crear una cuenta de GitHub a quien no la tenga: quien
        # llega aqui viene a conectar una cuenta que ya tiene, y el desvio a un
        # registro en mitad del flujo se lee como que algo ha salido mal.
        #
        # OJO: esto NO fuerza la pantalla de autorizacion. Las OAuth Apps
        # clasicas de GitHub no tienen ningun parametro que lo haga —`prompt`
        # es de las GitHub Apps— asi que una segunda conexion con los mismos
        # permisos vuelve sin preguntar. Estuvo escrito lo contrario aqui, y una
        # nota que promete una proteccion que no existe es peor que no tenerla.
        "allow_signup": "false",
    }
    return f"{AUTORIZAR}?{urlencode(parametros)}"


def canjear_codigo(codigo: str, callback: str) -> tuple[str, tuple[str, ...]]:
    """Cambia el código de un solo uso por un token. Devuelve (token, scopes).

    El secreto de la aplicación viaja en esta petición, que sale del **backend**.
    Es la razón de que la URL de retorno apunte a Render y no a Vercel: el
    frontend nunca ve el secreto ni el token.
    """
    cliente, secreto = _credenciales()

    try:
        respuesta = httpx.post(
            CANJEAR,
            data={
                "client_id": cliente,
                "client_secret": secreto,
                "code": codigo,
                "redirect_uri": callback,
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ErrorDeGitHub(f"No se pudo contactar con GitHub: {exc}") from exc

    if respuesta.status_code >= 400:
        raise ErrorDeGitHub(f"GitHub respondió {respuesta.status_code}.")

    datos = respuesta.json()
    if datos.get("error"):
        # El mensaje de GitHub es util —'bad_verification_code' dice que el
        # codigo caduco o ya se uso— y no contiene secretos.
        raise ErrorDeGitHub(
            datos.get("error_description") or str(datos["error"])
        )

    token = datos.get("access_token")
    if not token:
        raise ErrorDeGitHub("GitHub no devolvió ningún token.")

    scopes = tuple(s for s in (datos.get("scope") or "").split(",") if s)
    return token, scopes


def canjear(codigo: str, callback: str) -> Credenciales:
    """`canjear_codigo` con la forma común a todos los servicios (V2.0.17).

    Los tokens de GitHub no caducan: sin renovación ni caducidad.
    """
    token, scopes = canjear_codigo(codigo, callback)
    return Credenciales(token=token, scopes=scopes)


def cuenta_de(token: str) -> dict:
    """Quién es el dueño del token, para poder enseñarlo.

    Importa más de lo que parece: sin esto, la interfaz diría «GitHub conectado»
    sin decir *a qué cuenta*, y quien tenga dos —la personal y la del trabajo—
    no tendría forma de saber sobre cuál va a actuar Morgan.
    """
    try:
        respuesta = httpx.get(
            f"{API}/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ErrorDeGitHub(f"No se pudo consultar la cuenta: {exc}") from exc

    if respuesta.status_code == 401:
        raise ErrorDeGitHub("El token ya no vale. Vuelve a conectar GitHub.")
    if respuesta.status_code >= 400:
        raise ErrorDeGitHub(f"GitHub respondió {respuesta.status_code}.")

    datos = respuesta.json()
    return {
        "login": datos.get("login"),
        "nombre": datos.get("name"),
        "avatar": datos.get("avatar_url"),
    }


def revocar(token: str) -> bool:
    """Le pide a GitHub que invalide el token.

    **Desconectar borra la fila local pase lo que pase**, pero avisar a GitHub
    es lo que hace que el token deje de valer de verdad. Si solo se borrara aquí,
    una copia filtrada seguiría funcionando y la persona creería haber revocado
    el acceso.

    Devuelve si GitHub lo confirmó. Que falle no impide desconectar: quedarse
    conectado porque la revocación remota no respondió sería el peor resultado.
    """
    try:
        cliente, secreto = _credenciales()
    except GitHubNoConfigurado:
        return False

    try:
        respuesta = httpx.request(
            "DELETE",
            f"{API}/applications/{cliente}/token",
            auth=(cliente, secreto),
            json={"access_token": token},
            headers={"Accept": "application/vnd.github+json"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("No se pudo revocar el token en GitHub: %s", exc)
        return False

    # 204 es el exito; 404 significa que ya no existia, que para el caso es lo
    # mismo y no merece tratarse como fallo.
    return respuesta.status_code in (204, 404)


# --- Consultas sobre la cuenta conectada --------------------------------------
#
# Cada una recibe el token y NO lo busca por su cuenta: quien decide de quien es
# la sesion es el contexto de la peticion, y una funcion que fuera a buscarlo
# sola acabaria mirando el equivocado en el primer trabajo de fondo.


def _trozo_de_url(valor: str, *, con_barras: bool = False) -> str:
    """Deja un valor del modelo listo para ir en una URL, o lo rechaza.

    **Por qué existe.** `repo` y `ruta` los compone el modelo a partir de lo que
    le dicen, y lo que le dicen puede venir de un archivo que acaba de leer — por
    eso el contenido de un repositorio llega marcado como `untrusted_file_data`.
    Interpolados sin más, un `..` deja de ser un nombre y pasa a ser una
    instrucción para la URL.

    No es teórico, está medido. Con `ruta='../../../../user/emails'`, la URL
    `/repos/duenyo/nombre/contents/../../../../user/emails` se normaliza a
    **`/user/emails`**: las direcciones privadas de la persona, dentro de una
    herramienta que dice leer un archivo de un repositorio. Igual se alcanzaban
    `/notifications`, `/user/keys` y `/gists`.

    Codificar no basta: `quote()` no toca los puntos, así que `..` sobrevive
    intacto. Hay que **rechazarlo**. Y se codifica además, para que `?` y `#` no
    puedan partir la URL en otra cosa.
    """
    valor = (valor or "").strip()
    if not valor:
        raise ErrorDeGitHub("Falta el nombre.")

    trozos = valor.split("/") if con_barras else [valor]
    for trozo in trozos:
        if trozo in ("", ".", ".."):
            raise ErrorDeGitHub(
                f"'{valor}' no es un nombre válido: no puede llevar '.', '..' "
                "ni barras de más."
            )

    from urllib.parse import quote

    return "/".join(quote(t, safe="") for t in trozos)


def _repo_en_url(repo: str) -> str:
    """`duenyo/nombre`, comprobado y codificado."""
    partes = (repo or "").strip().strip("/").split("/")
    if len(partes) != 2:
        raise ErrorDeGitHub(
            f"'{repo}' no parece un repositorio. Se escribe como "
            "'duenyo/nombre'."
        )
    return f"{_trozo_de_url(partes[0])}/{_trozo_de_url(partes[1])}"


def _pedir(token: str, ruta: str, parametros: dict | None = None) -> Any:
    """Una consulta de solo lectura a la API de GitHub.

    Traduce los errores a algo accionable. `401` no es «error 401»: es que la
    autorizacion dejo de valer y hay que volver a conectar, que es lo unico que
    la persona puede hacer al respecto.
    """
    # La URL que sale tiene que ser la que se pidio, letra por letra.
    #
    # Es una red de seguridad, no la proteccion principal: esa es
    # `_trozo_de_url`. Esta esta aqui porque depender de que cada funcion nueva
    # se acuerde de validar es exactamente como aparecio el fallo. httpx
    # normaliza los `..` al construir la URL, asi que si algo se colo, el camino
    # cambia y aqui se ve.
    destino = httpx.URL(f"{API}{ruta}")
    if destino.path != ruta:
        logger.error("Ruta de GitHub alterada al construir la URL: %r -> %r",
                     ruta, destino.path)
        raise ErrorDeGitHub(
            "La consulta a GitHub no era válida y se ha detenido."
        )

    try:
        respuesta = httpx.get(
            destino,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=parametros or {},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ErrorDeGitHub(f"No se pudo contactar con GitHub: {exc}") from exc

    if respuesta.status_code == 401:
        raise ErrorDeGitHub(
            "La autorización de GitHub ya no vale. Vuelve a conectarlo desde "
            "Servicios."
        )
    if respuesta.status_code == 403:
        # Puede ser el limite de peticiones o un permiso que no se concedio. Se
        # distinguen porque la salida es distinta: esperar, o reconectar.
        restante = respuesta.headers.get("x-ratelimit-remaining")
        if restante == "0":
            raise ErrorDeGitHub(
                "GitHub ha limitado las peticiones por ahora. Espera unos "
                "minutos y vuelve a intentarlo."
            )
        raise ErrorDeGitHub(
            "GitHub no permite esta operación con los permisos concedidos."
        )
    if respuesta.status_code == 404:
        raise ErrorDeGitHub(
            "No existe, o la cuenta conectada no tiene acceso. En un "
            "repositorio privado las dos cosas se ven igual desde fuera."
        )
    if respuesta.status_code >= 400:
        raise ErrorDeGitHub(f"GitHub respondió {respuesta.status_code}.")

    return respuesta.json()


def listar_repos(token: str, limite: int = 30) -> list[dict]:
    """Los repositorios de la cuenta conectada, del más reciente al más viejo.

    Se ordenan por actualización y no por nombre: quien pregunta «¿qué repos
    tengo?» casi siempre busca en lo que ha tocado últimamente.
    """
    datos = _pedir(token, "/user/repos", {
        "sort": "updated",
        "per_page": max(1, min(limite, 100)),
        # Los que le pertenecen y aquellos en los que colabora. Sin esto, los de
        # una organizacion no aparecen y parece que se han perdido.
        "affiliation": "owner,collaborator,organization_member",
    })

    return [
        {
            "nombre": r.get("full_name"),
            "privado": r.get("private"),
            "descripcion": r.get("description"),
            "lenguaje": r.get("language"),
            "actualizado": r.get("updated_at"),
            "url": r.get("html_url"),
        }
        for r in datos
    ]


def listar_issues(token: str, repo: str, estado: str = "open", limite: int = 20) -> list[dict]:
    """Las issues de un repositorio.

    **Se filtran los pull requests.** La API de GitHub los devuelve mezclados
    con las issues —tienen el mismo endpoint— y quien pide «las issues abiertas»
    no espera ver PRs entre ellas.
    """
    datos = _pedir(token, f"/repos/{_repo_en_url(repo)}/issues", {
        "state": estado if estado in ("open", "closed", "all") else "open",
        "per_page": max(1, min(limite, 100)),
    })

    return [
        {
            "numero": i.get("number"),
            "titulo": i.get("title"),
            "estado": i.get("state"),
            "autor": (i.get("user") or {}).get("login"),
            "etiquetas": [e.get("name") for e in i.get("labels") or []],
            "comentarios": i.get("comments"),
            "actualizado": i.get("updated_at"),
            "url": i.get("html_url"),
        }
        for i in datos
        if "pull_request" not in i
    ]


def listar_prs(token: str, repo: str, estado: str = "open", limite: int = 20) -> list[dict]:
    datos = _pedir(token, f"/repos/{_repo_en_url(repo)}/pulls", {
        "state": estado if estado in ("open", "closed", "all") else "open",
        "per_page": max(1, min(limite, 100)),
    })

    return [
        {
            "numero": p.get("number"),
            "titulo": p.get("title"),
            "estado": p.get("state"),
            "autor": (p.get("user") or {}).get("login"),
            "rama": (p.get("head") or {}).get("ref"),
            "hacia": (p.get("base") or {}).get("ref"),
            "borrador": p.get("draft"),
            "actualizado": p.get("updated_at"),
            "url": p.get("html_url"),
        }
        for p in datos
    ]


def leer_archivo(token: str, repo: str, ruta: str, rama: str | None = None) -> dict:
    """El contenido de un archivo del repositorio.

    Devuelve texto decodificado. Si el archivo es binario o demasiado grande, se
    dice: entregar bytes en base64 al modelo llena el contexto de ruido que no
    puede leer.
    """
    import base64

    parametros = {"ref": rama} if rama else {}
    datos = _pedir(
        token,
        f"/repos/{_repo_en_url(repo)}/contents/{_trozo_de_url(ruta, con_barras=True)}",
        parametros,
    )

    if isinstance(datos, list):
        # Es un directorio. Se devuelve su indice, que suele ser lo que se
        # buscaba cuando se pasa una ruta sin nombre de archivo.
        return {
            "tipo": "directorio",
            "ruta": ruta,
            "entradas": [
                {"nombre": e.get("name"), "tipo": e.get("type"), "tamano": e.get("size")}
                for e in datos
            ],
        }

    if datos.get("encoding") != "base64" or not datos.get("content"):
        raise ErrorDeGitHub(
            f"'{ruta}' no se puede leer como texto: puede ser binario o "
            "demasiado grande."
        )

    try:
        contenido = base64.b64decode(datos["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ErrorDeGitHub(
            f"'{ruta}' no es texto legible: {exc}"
        ) from exc

    return {
        "tipo": "archivo",
        "ruta": datos.get("path"),
        "tamano": datos.get("size"),
        "contenido": contenido,
        "url": datos.get("html_url"),
    }
