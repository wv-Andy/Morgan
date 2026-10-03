"""
Extracción de texto de archivos subidos (V1.4).

El texto que sale de aquí **lo escribió un tercero**, igual que el de una página
web: puede contener instrucciones dirigidas al modelo. Por eso se devuelve
envuelto en `<untrusted_file_data>`, el mismo mecanismo que ya usa `read_webpage`
y que el system prompt enseña a tratar como datos pasivos.

Extraer no es gratis. Un PDF de mil páginas puede tardar y ocupar más contexto del
que cabe, así que hay límites de páginas y de caracteres, y se dice cuándo se ha
recortado en lugar de devolver un texto truncado en silencio.
"""

import io
import logging
import time

logger = logging.getLogger(__name__)

# Un turno del modelo no puede tragarse un libro entero: pasado esto, el texto
# desplaza al resto del contexto y la respuesta empeora.
MAX_CARACTERES = 30_000
MAX_PAGINAS = 100

# Un archivo puede ser costoso de analizar sin ser grande: un PDF con miles de
# objetos anidados, un XML con expansion de entidades. Extraer no puede quedarse
# colgado indefinidamente ocupando un hilo del servidor.
SEGUNDOS_MAXIMOS = 30

ENVOLTORIO_INICIO = '<untrusted_file_data source="{origen}">'
ENVOLTORIO_FIN = "</untrusted_file_data>"


class ExtraccionFallida(RuntimeError):
    """No se pudo leer el contenido. El mensaje está pensado para enseñarse."""


def _acotar(texto: str) -> tuple[str, bool]:
    if len(texto) <= MAX_CARACTERES:
        return texto, False
    return texto[:MAX_CARACTERES], True


def envolver(texto: str, origen: str) -> str:
    """Aísla el contenido como dato no confiable."""
    return f"{ENVOLTORIO_INICIO.format(origen=origen)}\n{texto}\n{ENVOLTORIO_FIN}"


def extraer_texto_plano(contenido: bytes) -> str:
    """Decodifica un archivo de texto tolerando codificaciones distintas.

    `errors='replace'` en lugar de fallar: un byte suelto mal codificado no debe
    impedir leer un fichero de diez mil líneas.
    """
    for codificacion in ("utf-8", "utf-16", "latin-1"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return contenido.decode("utf-8", errors="replace")


def extraer_texto_pdf(contenido: bytes) -> str:
    """Extrae el texto de un PDF.

    Un PDF escaneado no tiene texto que extraer, solo imágenes. Se detecta y se
    dice, en vez de devolver una cadena vacía que parecería un fallo.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependencia declarada
        raise ExtraccionFallida(
            "Falta la librería para leer PDF. Instala las dependencias del proyecto."
        ) from exc

    inicio = time.monotonic()
    try:
        lector = PdfReader(io.BytesIO(contenido))
        paginas = lector.pages[:MAX_PAGINAS]

        textos = []
        for numero, pagina in enumerate(paginas, start=1):
            # Se comprueba entre paginas y no con un temporizador: pypdf es
            # sincrono y no se puede interrumpir a mitad de una pagina, pero
            # cortar entre ellas evita que un documento hostil ocupe un hilo
            # indefinidamente.
            if time.monotonic() - inicio > SEGUNDOS_MAXIMOS:
                textos.append(
                    f"[Se agotó el tiempo de extracción tras {numero - 1} páginas]"
                )
                break
            textos.append((pagina.extract_text() or "").strip())
    except Exception as exc:
        logger.warning("No se pudo leer el PDF: %s", exc)
        raise ExtraccionFallida(
            "No he podido leer el PDF. Puede estar dañado o protegido con contraseña."
        ) from exc

    texto = "\n\n".join(t for t in textos if t)
    if not texto:
        raise ExtraccionFallida(
            "El PDF no contiene texto seleccionable. Parece escaneado: haría falta "
            "reconocimiento óptico de caracteres, que Morgan todavía no tiene."
        )

    if len(lector.pages) > MAX_PAGINAS:
        texto += f"\n\n[Se leyeron {MAX_PAGINAS} de {len(lector.pages)} páginas]"

    return texto


def extraer(contenido: bytes, mime: str, origen: str) -> str:
    """Devuelve el texto del archivo, ya aislado como dato no confiable.

    Qué es «texto» lo decide `TIPOS_ADMITIDOS`, no el prefijo del MIME. Fiarse de
    que empiece por `text/` dejaba fuera YAML (`application/x-yaml`), TOML y SVG
    (`image/svg+xml`), que sí se leen decodificando bytes.
    """
    from src.uploads.store import TIPOS_ADMITIDOS

    if mime == "application/pdf":
        texto = extraer_texto_pdf(contenido)
    elif mime in TIPOS_ADMITIDOS["texto"]:
        texto = extraer_texto_plano(contenido)
    else:
        raise ExtraccionFallida(
            f"No sé extraer texto de un archivo '{mime}'. "
            "Puedo con PDF, texto plano, Markdown, CSV, JSON, YAML, TOML, "
            "configuración, registros y código."
        )

    texto, recortado = _acotar(texto)
    if recortado:
        texto += f"\n\n[Texto recortado a {MAX_CARACTERES} caracteres]"

    return envolver(texto, origen)
