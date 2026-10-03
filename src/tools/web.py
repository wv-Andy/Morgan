"""
Herramientas de navegación e inspección web segura para Morgan (V0.6).

Incluye búsqueda web, descarga controlada y extracción limpia de texto con
aislamiento de contenido no confiable para prevenir Prompt Injections.
"""

import html as html_module
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser
from typing import Any

from src.tools.base import Tool, ToolCategory

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MAX_PAGE_SIZE_BYTES = 250 * 1024  # 250 KB límite de descarga
MAX_TEXT_OUTPUT_CHARS = 12000     # 12,000 caracteres límite de texto

#: Por encima de esta proporción de caracteres de reemplazo (U+FFFD), lo decodificado
#: no es texto: son bytes que no lo eran. La página de python.org sin descomprimir
#: dio un 44% (5.294 de 12.140). Una página real con alguna secuencia rota se queda
#: muy por debajo.
MAX_PROPORCION_ILEGIBLE = 0.05


class PaginaIlegible(Exception):
    """La respuesta no se puede convertir en texto. El mensaje se le da al modelo."""


def _cabecera(respuesta: Any, nombre: str) -> str:
    """Una cabecera como texto, o cadena vacía.

    `isinstance` porque las pruebas simulan la respuesta con `MagicMock`, cuyas
    cabeceras devuelven otro `MagicMock` y no `None`.
    """
    valor = respuesta.headers.get(nombre) if getattr(respuesta, "headers", None) is not None else None
    return valor.strip().lower() if isinstance(valor, str) else ""


def leer_texto(respuesta: Any, limite: int = MAX_PAGE_SIZE_BYTES) -> str:
    """El cuerpo de una respuesta de urllib como texto: descomprimido, decodificado y acotado.

    **El defecto que lo trajo (V2.0.15).** python.org responde con
    `Content-Encoding: gzip` aunque no se le pida, y las dos herramientas web
    decodificaban los bytes comprimidos como si fueran UTF-8. El resultado eran
    12.140 caracteres de basura que viajaban al modelo como 25.789 tokens: Groq no
    podía atenderlos nunca y, en producción, acababa contestando OpenAI, que cobra.
    Y Morgan, además, no leía la página.

    Tres protecciones:

    - **gzip y deflate se descomprimen**, con la biblioteca estándar. Brotli o zstd
      exigirían una dependencia: se dice que no se sabe leer, en vez de pasar basura.
    - **La descompresión tiene el mismo tope que la descarga.** 250 KB comprimidos
      pueden ser gigas de ceros; se para al llegar al límite.
    - **Si lo decodificado no parece texto**, se rechaza. Es la red para lo que no
      avise de su compresión o mienta sobre su codificación.
    """
    codificacion = _cabecera(respuesta, "Content-Encoding")

    if codificacion in ("", "identity"):
        datos = respuesta.read(limite)
    elif codificacion in ("gzip", "x-gzip", "deflate"):
        comprimido = respuesta.read(limite)
        # 16 + MAX_WBITS acepta la cabecera gzip; MAX_WBITS, el deflate con cabecera
        # zlib, que es lo que manda casi todo el mundo bajo «deflate».
        ventana = 16 + zlib.MAX_WBITS if "gzip" in codificacion else zlib.MAX_WBITS
        try:
            datos = zlib.decompressobj(ventana).decompress(comprimido, limite)
        except zlib.error:
            try:
                # Algunos servidores mandan deflate crudo, sin la cabecera zlib.
                datos = zlib.decompressobj(-zlib.MAX_WBITS).decompress(comprimido, limite)
            except zlib.error as exc:
                raise PaginaIlegible(
                    f"La página dice venir comprimida ({codificacion}) y no se pudo descomprimir."
                ) from exc
    else:
        raise PaginaIlegible(
            f"La página viene comprimida en un formato que Morgan no sabe leer ({codificacion})."
        )

    juego = "utf-8"
    tipo = getattr(respuesta, "headers", None)
    if tipo is not None and hasattr(tipo, "get_content_charset"):
        pedido = tipo.get_content_charset()
        if isinstance(pedido, str) and pedido:
            juego = pedido
    try:
        texto = datos.decode(juego, errors="replace")
    except LookupError:
        texto = datos.decode("utf-8", errors="replace")

    if texto and texto.count("�") / len(texto) > MAX_PROPORCION_ILEGIBLE:
        raise PaginaIlegible(
            "La respuesta no es texto legible: parece contenido binario o comprimido "
            "sin avisar. No se ha leído."
        )
    return texto


# --- Parseo de los resultados de DuckDuckGo -------------------------------------
#
# DuckDuckGo envuelve cada enlace en un redirector propio
# (//duckduckgo.com/l/?uddg=<url-codificada>). Si se entrega esa URL tal cual, el
# modelo recibe una direccion opaca en lugar del sitio real, y read_webpage acaba
# descargando la pagina de redireccion.

_RESULT_LINK_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_RESULT_SNIPPET_RE = re.compile(
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(fragment: str) -> str:
    """Convierte un fragmento HTML en texto legible."""
    text = _TAG_RE.sub("", fragment)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_ddg_url(href: str) -> str:
    """Devuelve la URL real de destino a partir del redirector de DuckDuckGo."""
    if not href:
        return ""

    href = html_module.unescape(href.strip())
    if href.startswith("//"):
        href = "https:" + href

    try:
        parsed = urllib.parse.urlparse(href)
    except ValueError:
        return href

    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target

    return href


def check_url_is_public(url: str) -> tuple[bool, str | None]:
    """Rechaza URLs que apunten a la red interna (protección SSRF).

    `read_webpage` es una herramienta de nivel 'safe' y se ejecuta sin confirmación,
    así que no debe poder alcanzar localhost, la red local ni los endpoints de
    metadatos de la nube a partir de una URL sugerida por contenido no confiable.
    """
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False, f"Esquema no permitido: '{parsed.scheme}'. Solo se admite http y https."

    host = parsed.hostname
    if not host:
        return False, "La URL no contiene un host válido."

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"Error de resolución DNS: no se pudo resolver el host '{host}' ({exc})."

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            return False, (
                f"Acceso denegado: '{host}' resuelve a una dirección interna ({address}). "
                "Morgan solo navega por direcciones públicas."
            )

    return True, None


class _HTMLTextExtractor(HTMLParser):
    """Parser HTML para extraer texto legible ignorando scripts, estilos y etiquetas irrelevantes."""

    def __init__(self):
        super().__init__()
        self.result = []
        self.ignore_tags = {"script", "style", "nav", "footer", "head", "noscript", "svg"}
        self.current_ignore = False
        self.ignore_stack = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.ignore_tags:
            self.ignore_stack.append(tag)
            self.current_ignore = True
        elif tag.lower() in {"p", "h1", "h2", "h3", "h4", "li", "tr"}:
            self.result.append("\n")

    def handle_endtag(self, tag):
        if self.ignore_stack and tag.lower() == self.ignore_stack[-1]:
            self.ignore_stack.pop()
            self.current_ignore = len(self.ignore_stack) > 0
        elif tag.lower() in {"p", "h1", "h2", "h3", "h4", "div"}:
            self.result.append("\n")

    def handle_data(self, data):
        if not self.current_ignore:
            text = data.strip()
            if text:
                self.result.append(f"{text} ")

    def get_text(self) -> str:
        raw = "".join(self.result)
        # Normalizar múltiples saltos de línea y espacios
        cleaned = re.sub(r"\n\s*\n", "\n\n", raw)
        return cleaned.strip()


def sanitize_web_content(content: str, url: str) -> str:
    """Envuelve el contenido web en etiquetas de delimitación segura contra prompt injection."""
    truncated = content[:MAX_TEXT_OUTPUT_CHARS]
    trunc_note = "\n[... Contenido truncado por límite de tamaño ...]" if len(content) > MAX_TEXT_OUTPUT_CHARS else ""
    return (
        f'<untrusted_web_data source_url="{url}">\n'
        f"{truncated}{trunc_note}\n"
        f"</untrusted_web_data>"
    )


#: Lo que se le dice al modelo si ningún buscador responde (4.5). Medido en producción
#: (2026-09-30): con DuckDuckGo sin contestar a Render, el modelo buscó 4 veces por turno
#: con frases distintas, 12 s cada una, hasta el tope de vueltas, y acabó dando cifras de
#: memoria como si fueran de internet.
SIN_BUSQUEDA = (
    "La búsqueda en internet no responde ahora. No la intentes otra vez en este turno: "
    "contesta con lo que sepas y di claramente que no pudiste comprobarlo en internet."
)

#: Cuánto descansa un buscador que no respondió antes de volver a probarlo (4.5). Con
#: DuckDuckGo bloqueando a Render, sin esto cada búsqueda seguía esperando 12 s en balde.
PAUSA_DE_UN_BUSCADOR_S = 600.0

TAVILY_URL = "https://api.tavily.com/search"
#: Resultados de Google (4.8, pedido por mí: «que busque con Google y otros motores»). La
#: API propia de Google (Custom Search) está cerrada a clientes nuevos y se apaga en 2027;
#: Serper da los de Google, 2.500 búsquedas gratis y sin tarjeta.
SERPER_URL = "https://google.serper.dev/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
#: El orden de siempre. Cada uno, solo si tiene su clave (DuckDuckGo no la necesita).
ORDEN_DE_BUSCADORES = ("google", "tavily", "brave", "duckduckgo")
#: Largo máximo del fragmento de cada resultado de Tavily: lo que devuelve por defecto
#: puede pasar de mil caracteres por resultado, y el modelo solo necesita saber si le
#: interesa leer la página.
MAX_FRAGMENTO = 400


class _BuscadorCaido(Exception):
    """El buscador no respondió: tiempo, red, clave o cupo. Se le deja descansar."""


class SearchWebTool(Tool):
    """Busca información en la web.

    **Varios motores (4.8, pedido por mí).** Google (con `SERPER_API_KEY`), Tavily,
    Brave (con `BRAVE_SEARCH_API_KEY`) y DuckDuckGo, en `ORDEN_DE_BUSCADORES` (se cambia con
    `MORGAN_BUSCADORES`), cada uno solo si tiene su clave. El modelo puede pedir uno
    (`motor`), si la persona lo nombra; si no está, se usa el siguiente y se dice.

    **Dos buscadores (4.5, decisión mía).** DuckDuckGo no contesta a los servidores
    de Render (medido en producción: cada búsqueda agotaba sus 12 s; desde un PC tardaba
    1,1 s). Con `TAVILY_API_KEY` se busca primero en Tavily (1.000 búsquedas al mes
    gratis, sin tarjeta) y DuckDuckGo queda de respaldo. Sin clave, solo DuckDuckGo.

    Un buscador que no responde descansa `PAUSA_DE_UN_BUSCADOR_S`; si no queda ninguno,
    se dice a la primera (`SIN_BUSQUEDA`). Las pausas son de esta instancia: en la API
    hay una sola, compartida.
    """

    def __init__(self, tavily_api_key: str | None = None, serper_api_key: str | None = None,
                 brave_api_key: str | None = None, orden: tuple[str, ...] | None = None):
        self.tavily_api_key = tavily_api_key
        self.serper_api_key = serper_api_key
        self.brave_api_key = brave_api_key
        self.orden = tuple(o for o in (orden or ORDEN_DE_BUSCADORES) if o in ORDEN_DE_BUSCADORES)
        self._pausado_hasta: dict[str, float] = {}
        self._cerrojo = threading.Lock()

    def _motores(self) -> dict:
        """Los que se pueden usar, en su orden."""
        todos = {"google": (self.serper_api_key, self._google), "tavily": (self.tavily_api_key, self._tavily),
                 "brave": (self.brave_api_key, self._brave), "duckduckgo": (True, self._duckduckgo)}
        return {n: todos[n][1] for n in self.orden if todos[n][0]}

    @property
    def name(self) -> str:
        return "search_web"

    @property
    def description(self) -> str:
        return (
            "Busca información actualizada en Internet. "
            "Devuelve títulos, fragmentos de texto y enlaces relevantes."
        )

    @property
    def category(self) -> str:
        return ToolCategory.WEB.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def requires_local(self) -> bool:
        # No toca la máquina del usuario: opera sobre la base de datos o la red.
        return False

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término o consulta de búsqueda en la web.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Número máximo de resultados a devolver (1 a 10). Por defecto 5.",
                },
                "motor": {
                    "type": "string", "enum": list(ORDEN_DE_BUSCADORES),
                    "description": "Solo si la persona pide un buscador concreto.",
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str, max_results: int = 5, motor: str | None = None, **kwargs: Any) -> dict:
        try:
            max_results = max(1, min(int(max_results), 10))
        except (TypeError, ValueError):
            max_results = 5

        motores = self._motores()
        buscadores = list(motores.items())
        pedido = str(motor or "").lower().strip() or None
        if pedido in motores:
            buscadores.sort(key=lambda b: b[0] != pedido)

        fallos = []
        for nombre, buscar in buscadores:
            if self._en_pausa(nombre):
                fallos.append(f"{nombre}: en pausa")
                continue
            try:
                datos = {**buscar(query, max_results), "motor": nombre}
                if pedido and pedido != nombre:
                    motivo = "no está configurado" if pedido not in motores else "no respondió"
                    datos["aviso"] = f"No se buscó con {pedido}: {motivo}. Se buscó con {nombre}."
                return {"success": True, "data": datos, "error": None}
            except _BuscadorCaido as exc:
                self._pausar(nombre)
                fallos.append(f"{nombre}: {exc}")
            except Exception as exc:  # noqa: BLE001 - una respuesta rara no pausa al buscador
                fallos.append(f"{nombre}: {exc}")

        logger.warning("Ningún buscador respondió (%s)", "; ".join(fallos))
        return {"success": False, "data": None, "error": SIN_BUSQUEDA}

    def _en_pausa(self, nombre: str) -> bool:
        with self._cerrojo:
            return time.monotonic() < self._pausado_hasta.get(nombre, 0.0)

    def _pausar(self, nombre: str) -> None:
        with self._cerrojo:
            self._pausado_hasta[nombre] = time.monotonic() + PAUSA_DE_UN_BUSCADOR_S

    def _pedir(self, nombre: str, req) -> dict:
        """El JSON de un buscador. 400 es la consulta (no pausa); lo demás, el buscador."""
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(leer_texto(response))
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                raise ValueError(f"{nombre} rechazó la consulta ({exc.code})") from exc
            raise _BuscadorCaido(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise _BuscadorCaido(str(exc)) from exc

    @staticmethod
    def _resultados(filas, max_results: int) -> list[dict]:
        return [
            {"title": strip_html(t or "") or f"Resultado {i + 1}", "url": u,
             "snippet": strip_html(f or "")[:MAX_FRAGMENTO]}
            for i, (t, u, f) in enumerate(filas) if u
        ][:max_results]

    def _google(self, query: str, max_results: int) -> dict:
        req = urllib.request.Request(
            SERPER_URL, method="POST",
            data=json.dumps({"q": query, "num": max_results}).encode("utf-8"),
            headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json",
                     "User-Agent": USER_AGENT},
        )
        datos = self._pedir("Google", req)
        resultados = self._resultados(
            ((r.get("title"), r.get("link"), r.get("snippet")) for r in datos.get("organic") or []),
            max_results)
        salida = {"query": query, "total_results": len(resultados), "results": resultados}
        caja = datos.get("answerBox") or {}
        directa = caja.get("answer") or caja.get("snippet")
        if directa:
            # La respuesta que Google pone arriba, recortada.
            salida["respuesta_de_google"] = strip_html(str(directa))[:MAX_FRAGMENTO]
        return salida

    def _brave(self, query: str, max_results: int) -> dict:
        consulta = urllib.parse.urlencode({"q": query, "count": max_results})
        req = urllib.request.Request(f"{BRAVE_URL}?{consulta}", headers={
            "X-Subscription-Token": self.brave_api_key, "Accept": "application/json",
            "User-Agent": USER_AGENT})
        datos = self._pedir("Brave", req)
        resultados = self._resultados(
            ((r.get("title"), r.get("url"), r.get("description"))
             for r in (datos.get("web") or {}).get("results") or []), max_results)
        return {"query": query, "total_results": len(resultados), "results": resultados}

    def _tavily(self, query: str, max_results: int) -> dict:
        cuerpo = json.dumps({"query": query, "max_results": max_results,
                             "search_depth": "basic"}).encode("utf-8")
        req = urllib.request.Request(
            TAVILY_URL, data=cuerpo, method="POST",
            headers={"Authorization": f"Bearer {self.tavily_api_key}",
                     "Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                datos = json.loads(leer_texto(response))
        except urllib.error.HTTPError as exc:
            # 400 es la consulta: no es culpa del buscador. Lo demás (401 clave, 429 o
            # 432 cupo, 5xx) sí: descansa.
            if exc.code == 400:
                raise ValueError(f"Tavily rechazó la consulta ({exc.code})") from exc
            raise _BuscadorCaido(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise _BuscadorCaido(str(exc)) from exc

        resultados = [
            {"title": strip_html(r.get("title") or "") or f"Resultado {i + 1}",
             "url": r.get("url") or "",
             "snippet": strip_html(r.get("content") or "")[:MAX_FRAGMENTO]}
            for i, r in enumerate(datos.get("results") or [])
            if r.get("url")
        ][:max_results]
        return {"query": query, "total_results": len(resultados), "results": resultados}

    def _duckduckgo(self, query: str, max_results: int) -> dict:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                html = leer_texto(response)
        except (urllib.error.URLError, OSError) as exc:
            raise _BuscadorCaido(str(exc)) from exc
        # El titulo real esta en el texto de result__a; result__url solo contiene
        # la direccion recortada que DuckDuckGo muestra en pantalla.
        links = _RESULT_LINK_RE.findall(html)
        snippets = _RESULT_SNIPPET_RE.findall(html)

        results = []
        for i, (href, raw_title) in enumerate(links):
            if len(results) >= max_results:
                break

            target = resolve_ddg_url(href)
            if not target:
                continue

            results.append({
                "title": strip_html(raw_title) or f"Resultado {i + 1}",
                "url": target,
                "snippet": strip_html(snippets[i]) if i < len(snippets) else "",
            })

        # Si el parser específico no capturó suficiente pero hay respuesta, extraer texto estructurado
        if not results:
            extractor = _HTMLTextExtractor()
            extractor.feed(html)
            summary_text = extractor.get_text()[:1500]
            return {
                "query": query,
                "total_results": 1,
                "results": [{
                    "title": f"Búsqueda: {query}",
                    "url": url,
                    "snippet": summary_text or "No se pudieron extraer snippets específicos.",
                }],
            }

        return {"query": query, "total_results": len(results), "results": results}


class ReadWebpageTool(Tool):
    """Descarga y extrae el texto legible de una página web de forma segura."""

    @property
    def name(self) -> str:
        return "read_webpage"

    @property
    def description(self) -> str:
        return (
            "Descarga el contenido de una URL y extrae el texto legible de la página "
            "(limpiando código HTML, scripts y anuncios) con aislamiento de seguridad."
        )

    @property
    def category(self) -> str:
        return ToolCategory.WEB.value

    @property
    def permission_level(self) -> str:
        return "safe"

    @property
    def requires_local(self) -> bool:
        # No toca la máquina del usuario: opera sobre la base de datos o la red.
        return False

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Dirección web completa (ej. 'https://es.wikipedia.org/wiki/...').",
                },
            },
            "required": ["url"],
        }

    def execute(self, url: str, **kwargs: Any) -> dict:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        is_public, block_reason = check_url_is_public(url)
        if not is_public:
            return {"success": False, "data": None, "error": block_reason}

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT},
            )

            with urllib.request.urlopen(req, timeout=12) as response:
                html_text = leer_texto(response)

            extractor = _HTMLTextExtractor()
            extractor.feed(html_text)
            extracted_text = extractor.get_text()

            if not extracted_text:
                return {
                    "success": True,
                    "data": {
                        "url": url,
                        "text": "La página no contiene texto legible accesible.",
                        "is_empty": True,
                    },
                    "error": None,
                }

            safe_content = sanitize_web_content(extracted_text, url)

            return {
                "success": True,
                "data": {
                    "url": url,
                    "characters": len(extracted_text),
                    "content": safe_content,
                },
                "error": None,
            }

        except PaginaIlegible as e:
            return {"success": False, "data": None, "error": str(e)}
        except urllib.error.HTTPError as e:
            return {"success": False, "data": None, "error": f"Error HTTP {e.code}: {e.reason}"}
        except urllib.error.URLError as e:
            return {"success": False, "data": None, "error": f"Error de conexión a la URL: {e.reason}"}
        except Exception as e:
            return {"success": False, "data": None, "error": f"Error al leer página web: {e}"}
