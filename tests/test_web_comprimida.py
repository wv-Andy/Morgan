"""
Leer una página que llega comprimida, y no mandarle basura al modelo (V2.0.15).

**Por qué existe.** Midiendo el streaming, un turno que leía
`https://www.python.org/downloads/` hizo una llamada al modelo de **25.789
tokens**. Groq la rechazó —su límite son 8.000 por minuto, así que no cabe nunca—,
Gemini agotó su plazo, y en producción el siguiente eslabón es OpenAI, que cobra.

La causa, medida:

    Content-Encoding: gzip        primeros bytes: 1f 8b 08 00
    content: 12.140 caracteres, 5.294 de ellos U+FFFD (carácter de reemplazo)
    json.dumps -> 47.751 caracteres, con 7.032 escapes \\uXXXX

python.org responde comprimido aunque no se le pida, y `read_webpage`
decodificaba los bytes comprimidos como si fueran UTF-8. Morgan no podía leer
esa página, y además lo pagaba: cada carácter basura viajaba escapado en seis.
"""

import gzip
import json
import zlib
from email.message import Message
from unittest.mock import patch

from src.models.base import ChatMessage
from src.models.openai_format import to_openai_messages
from src.tools.web import MAX_PAGE_SIZE_BYTES, ReadWebpageTool, SearchWebTool

PAGINA = (
    "<html><head><title>Descargas</title></head><body>"
    "<h1>Download the latest version of Python</h1>"
    "<p>Python 3.14.6 — la versión estable, con canción y acentos: áéíóú ñ.</p>"
    "</body></html>"
).encode("utf-8")


class Respuesta:
    """Una respuesta de urllib con cabeceras de verdad, no un MagicMock."""

    def __init__(self, cuerpo: bytes, cabeceras: dict[str, str]):
        self._cuerpo = cuerpo
        self.headers = Message()
        for clave, valor in cabeceras.items():
            self.headers[clave] = valor

    def read(self, limite: int = -1) -> bytes:
        return self._cuerpo if limite is None or limite < 0 else self._cuerpo[:limite]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _leer(cuerpo: bytes, **cabeceras: str) -> dict:
    cabeceras = {k.replace("_", "-"): v for k, v in cabeceras.items()}
    with patch("src.tools.web.check_url_is_public", return_value=(True, None)), \
         patch("src.tools.web.urllib.request.urlopen", return_value=Respuesta(cuerpo, cabeceras)):
        return ReadWebpageTool().execute(url="https://ejemplo.org/")


class TestLaPaginaComprimidaSeLee:
    def test_gzip(self):
        r = _leer(gzip.compress(PAGINA), Content_Type="text/html; charset=utf-8", Content_Encoding="gzip")

        assert r["success"] is True
        assert "Python 3.14.6" in r["data"]["content"]
        assert "�" not in r["data"]["content"]

    def test_deflate(self):
        r = _leer(zlib.compress(PAGINA), Content_Type="text/html; charset=utf-8", Content_Encoding="deflate")

        assert "Python 3.14.6" in r["data"]["content"]

    def test_sin_comprimir_sigue_igual(self):
        r = _leer(PAGINA, Content_Type="text/html; charset=utf-8")

        assert "canción" in r["data"]["content"]

    def test_una_compresion_que_no_sabe_leer_se_dice(self):
        """Brotli exigiría otra dependencia. Mejor decirlo que pasar basura."""
        r = _leer(b"\x8b\x0b\x80bytes-brotli", Content_Type="text/html", Content_Encoding="br")

        assert r["success"] is False
        assert "br" in r["error"]
        assert r["data"] is None

    def test_la_descompresion_tiene_el_mismo_tope_que_la_descarga(self):
        """Unos kilobytes comprimidos pueden ser gigas: se corta al tope."""
        bomba = gzip.compress(b"<p>" + b"a" * (20 * MAX_PAGE_SIZE_BYTES) + b"</p>")
        assert len(bomba) < MAX_PAGE_SIZE_BYTES

        r = _leer(bomba, Content_Type="text/html", Content_Encoding="gzip")

        assert r["success"] is True
        assert r["data"]["characters"] <= MAX_PAGE_SIZE_BYTES


class TestLaBasuraNoLlegaAlModelo:
    def test_bytes_que_no_son_texto_se_rechazan(self):
        """Lo que pasaba con python.org: bytes comprimidos sin descomprimir."""
        basura = gzip.compress(PAGINA * 40)  # comprimido, pero sin decirlo

        r = _leer(basura, Content_Type="text/html; charset=utf-8")

        assert r["success"] is False
        assert r["data"] is None
        assert "�" not in (r["error"] or "")


class TestLaBusquedaTambien:
    def test_search_web_con_la_respuesta_comprimida(self):
        html = (
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2F">'
            "Python</a><a class=\"result__snippet\">La web oficial</a>"
        ).encode("utf-8")
        respuesta = Respuesta(gzip.compress(html), {"Content-Type": "text/html", "Content-Encoding": "gzip"})

        with patch("src.tools.web.urllib.request.urlopen", return_value=respuesta):
            r = SearchWebTool().execute(query="python")

        assert r["success"] is True
        assert r["data"]["results"][0]["url"] == "https://python.org/"


class TestLoQueViajaAlModelo:
    def test_los_acentos_viajan_tal_cual(self):
        """Cada `á` escapada ocupa seis caracteres. Gemini ya los mandaba tal cual."""
        mensajes = to_openai_messages([
            ChatMessage(role="tool", tool_name="read_webpage", tool_call_id="c1",
                        tool_result={"success": True, "data": {"content": "canción áéíóú"}}),
        ])

        contenido = mensajes[-1]["content"]
        assert "canción áéíóú" in contenido
        assert "\\u00" not in contenido
        assert json.loads(contenido)["data"]["content"] == "canción áéíóú"
