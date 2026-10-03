"""
Pruebas del parseo de resultados de búsqueda web.

No usan red: trabajan sobre un fragmento real del HTML que devuelve DuckDuckGo,
para que el parser quede cubierto de forma determinista.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.tools.web import SearchWebTool, resolve_ddg_url, strip_html

# Fragmento reducido pero fiel a la estructura real de html.duckduckgo.com
DDG_HTML = """
<div class="results">
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffastapi.tiangolo.com%2F&amp;rut=abc123">FastAPI - FastAPI</a>
      </h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffastapi.tiangolo.com%2F">
        FastAPI es un framework <b>web</b> moderno &amp; r&aacute;pido.
      </a>
      <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffastapi.tiangolo.com%2F">
        fastapi.tiangolo.com
      </a>
    </div>
  </div>
  <div class="result results_links results_links_deep web-result">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.geeksforgeeks.org%2Ffastapi%2F">FastAPI Tutorial - GeeksforGeeks</a>
      </h2>
      <a class="result__snippet" href="#">Gu&iacute;a de introducci&oacute;n.</a>
    </div>
  </div>
</div>
"""


def _run_search(html: str, **kwargs):
    """Ejecuta search_web contra un HTML fijo, sin tocar la red."""
    response = MagicMock()
    response.read.return_value = html.encode("utf-8")
    response.__enter__ = lambda s: s
    response.__exit__ = lambda *a: False

    with patch("src.tools.web.urllib.request.urlopen", return_value=response):
        return SearchWebTool().execute(query="fastapi", **kwargs)


class TestResolveDdgUrl:
    def test_extrae_la_url_real_del_redirector(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Ffastapi.tiangolo.com%2F&rut=abc"

        assert resolve_ddg_url(href) == "https://fastapi.tiangolo.com/"

    def test_deja_intacta_una_url_directa(self):
        assert resolve_ddg_url("https://ejemplo.org/pagina") == "https://ejemplo.org/pagina"

    def test_cadena_vacia(self):
        assert resolve_ddg_url("") == ""

    def test_entidades_html_en_el_href(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example%2F&amp;rut=x"

        assert resolve_ddg_url(href) == "https://a.example/"


class TestStripHtml:
    def test_quita_etiquetas_y_traduce_entidades(self):
        assert strip_html("<b>Hola</b> &amp; adi&oacute;s") == "Hola & adiós"

    def test_normaliza_espacios(self):
        assert strip_html("  varias\n   lineas  ") == "varias lineas"


class TestSearchWebParsing:
    def test_devuelve_urls_reales_no_redirectores(self):
        """Antes se entregaba //duckduckgo.com/l/?uddg=... al modelo."""
        result = _run_search(DDG_HTML)

        assert result["success"] is True
        urls = [r["url"] for r in result["data"]["results"]]
        assert urls == ["https://fastapi.tiangolo.com/", "https://www.geeksforgeeks.org/fastapi/"]
        assert not any("duckduckgo.com/l/" in u for u in urls)

    def test_el_titulo_es_el_de_la_pagina_no_la_url(self):
        """Antes se usaba result__url, que contiene la dirección mostrada."""
        result = _run_search(DDG_HTML)

        titulos = [r["title"] for r in result["data"]["results"]]
        assert titulos == ["FastAPI - FastAPI", "FastAPI Tutorial - GeeksforGeeks"]

    def test_los_snippets_se_limpian(self):
        result = _run_search(DDG_HTML)

        primero = result["data"]["results"][0]["snippet"]
        assert "<b>" not in primero
        assert "&amp;" not in primero
        assert "rápido" in primero

    def test_respeta_max_results(self):
        result = _run_search(DDG_HTML, max_results=1)

        assert result["data"]["total_results"] == 1

    def test_html_sin_resultados_no_revienta(self):
        result = _run_search("<html><body>sin resultados</body></html>")

        assert result["success"] is True
        assert isinstance(result["data"]["results"], list)

    def test_error_de_red_controlado(self):
        with patch("src.tools.web.urllib.request.urlopen", side_effect=OSError("sin conexión")):
            result = SearchWebTool().execute(query="cualquier cosa")

        assert result["success"] is False
        assert result["error"]

    @pytest.mark.parametrize("campo", ["title", "url", "snippet"])
    def test_estructura_de_cada_resultado(self, campo):
        result = _run_search(DDG_HTML)

        assert all(campo in r for r in result["data"]["results"])


class TestDosBuscadores:
    """4.5, medido en producción (2026-09-30): DuckDuckGo no contesta a los servidores de
    Render. Cada búsqueda agotaba sus 12 s y el modelo buscaba 4 veces por turno hasta el
    tope de vueltas. Con clave, Tavily primero; un buscador que no responde descansa, y si
    no queda ninguno se dice a la primera."""

    import json as _json
    import urllib.error as _uerr

    TAVILY = {"results": [
        {"title": "Salarios mínimos - MTSS", "url": "https://www.mtss.go.cr/salarios",
         "content": "x" * 900, "score": 0.9},
        {"title": "Otro", "url": "https://ejemplo.cr", "content": "corto"},
    ]}

    def _respuesta(self, cuerpo: str):
        r = MagicMock()
        r.read.return_value = cuerpo.encode("utf-8")
        r.__enter__ = lambda s: s
        r.__exit__ = lambda *a: False
        return r

    def _red(self, tavily=None, ddg=None):
        """Un `urlopen` falso que contesta según el destino y apunta a quién se llamó."""
        llamadas = []

        def urlopen(req, timeout=None):
            destino = "tavily" if "tavily" in req.full_url else "duckduckgo"
            llamadas.append((destino, req))
            respuesta = tavily if destino == "tavily" else ddg
            if isinstance(respuesta, BaseException):
                raise respuesta
            return self._respuesta(respuesta)

        return urlopen, llamadas

    def _error_http(self, codigo):
        return self._uerr.HTTPError("https://api.tavily.com/search", codigo, "x", {}, None)

    def test_con_clave_busca_en_tavily_y_no_en_duckduckgo(self):
        urlopen, llamadas = self._red(tavily=self._json.dumps(self.TAVILY))
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = SearchWebTool(tavily_api_key="tvly-prueba").execute(query="salario mínimo", max_results=5)

        assert r["success"] is True
        assert [d for d, _ in llamadas] == ["tavily"]
        req = llamadas[0][1]
        assert req.get_method() == "POST"
        assert req.get_header("Authorization") == "Bearer tvly-prueba"
        assert self._json.loads(req.data)["query"] == "salario mínimo"
        primero = r["data"]["results"][0]
        assert primero["url"] == "https://www.mtss.go.cr/salarios"
        assert len(primero["snippet"]) <= 400

    def test_sin_clave_solo_duckduckgo(self):
        urlopen, llamadas = self._red(ddg=DDG_HTML)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = SearchWebTool().execute(query="fastapi")
        assert r["success"] is True and [d for d, _ in llamadas] == ["duckduckgo"]

    def test_si_tavily_no_responde_busca_en_duckduckgo_y_tavily_descansa(self):
        urlopen, llamadas = self._red(tavily=self._error_http(432), ddg=DDG_HTML)
        herramienta = SearchWebTool(tavily_api_key="tvly-prueba")
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            primera = herramienta.execute(query="fastapi")
            segunda = herramienta.execute(query="fastapi otra")
        assert primera["success"] and segunda["success"]
        assert [d for d, _ in llamadas] == ["tavily", "duckduckgo", "duckduckgo"]

    def test_una_consulta_rechazada_no_hace_descansar_a_tavily(self):
        urlopen, llamadas = self._red(tavily=self._error_http(400), ddg=DDG_HTML)
        herramienta = SearchWebTool(tavily_api_key="tvly-prueba")
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            herramienta.execute(query="a")
            herramienta.execute(query="b")
        assert [d for d, _ in llamadas] == ["tavily", "duckduckgo", "tavily", "duckduckgo"]

    def test_si_ninguno_responde_se_dice_a_la_primera_y_no_se_espera_otra_vez(self):
        from src.tools.web import SIN_BUSQUEDA

        urlopen, llamadas = self._red(tavily=TimeoutError("timed out"),
                                      ddg=self._uerr.URLError("timed out"))
        herramienta = SearchWebTool(tavily_api_key="tvly-prueba")
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            primera = herramienta.execute(query="salario mínimo")
            segunda = herramienta.execute(query="salario mínimo 2026")
        assert primera == {"success": False, "data": None, "error": SIN_BUSQUEDA}
        assert segunda["error"] == SIN_BUSQUEDA
        assert len(llamadas) == 2, "la segunda búsqueda no vuelve a esperar a nadie"

    def test_la_pausa_se_acaba(self, monkeypatch):
        from src.tools import web

        urlopen, llamadas = self._red(ddg=self._uerr.URLError("timed out"))
        herramienta = SearchWebTool()
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            herramienta.execute(query="a")
            ahora = web.time.monotonic()
            monkeypatch.setattr(web.time, "monotonic", lambda: ahora + web.PAUSA_DE_UN_BUSCADOR_S + 1)
            herramienta.execute(query="b")
        assert len(llamadas) == 2

    def test_la_clave_sale_del_entorno(self, monkeypatch):
        from src.config import get_settings, reset_settings

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-de-render")
        reset_settings()
        try:
            assert get_settings().tavily_api_key == "tvly-de-render"
            assert "tvly-de-render" not in str(get_settings().redacted())
        finally:
            monkeypatch.delenv("TAVILY_API_KEY")
            reset_settings()

    def test_la_api_le_pasa_la_clave_al_buscador(self, monkeypatch):
        from src.api import dependencies
        from src.config import reset_settings

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-de-render")
        reset_settings()
        dependencies.reset_container()
        try:
            herramienta = dependencies.get_container().tool_registry.get("search_web")
            assert herramienta.tavily_api_key == "tvly-de-render"
        finally:
            monkeypatch.delenv("TAVILY_API_KEY")
            reset_settings()
            dependencies.reset_container()


class TestVariosMotores:
    """4.8, pedido por mí: «que busque con Google y otros motores». Google (vía Serper)
    primero, luego Tavily, Brave y DuckDuckGo, cada uno solo con su clave."""

    import json as _json
    import urllib.error as _uerr

    SERPER = {"organic": [{"title": "Salario mínimo 2026", "link": "https://www.mtss.go.cr/s", "snippet": "₡ 12 436"}],
              "answerBox": {"answer": "₡ 12 436,41 al día"}}
    BRAVE = {"web": {"results": [{"title": "Brave dice", "url": "https://b.example", "description": "x" * 900}]}}
    TAVILY = {"results": [{"title": "Tavily dice", "url": "https://t.example", "content": "t"}]}

    def _red(self, **respuestas):
        llamadas = []

        def urlopen(req, timeout=None):
            destino = next(n for n in ("serper", "brave", "tavily", "duckduckgo") if n in req.full_url)
            llamadas.append((destino, req))
            r = respuestas.get(destino)
            if isinstance(r, BaseException):
                raise r
            m = MagicMock()
            m.read.return_value = (r if isinstance(r, str) else self._json.dumps(r)).encode("utf-8")
            m.__enter__ = lambda s: s
            m.__exit__ = lambda *a: False
            return m

        return urlopen, llamadas

    def _todos(self, **k):
        return SearchWebTool(tavily_api_key="tv", serper_api_key="sp", brave_api_key="br", **k)

    def test_google_primero_y_dice_quien_contesto(self):
        urlopen, llamadas = self._red(serper=self.SERPER)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = self._todos().execute(query="salario mínimo")
        assert [d for d, _ in llamadas] == ["serper"]
        req = llamadas[0][1]
        assert req.get_header("X-api-key") == "sp" and self._json.loads(req.data)["q"] == "salario mínimo"
        assert r["data"]["motor"] == "google"
        assert r["data"]["results"][0]["url"] == "https://www.mtss.go.cr/s"
        assert r["data"]["respuesta_de_google"] == "₡ 12 436,41 al día"

    def test_si_google_no_responde_sigue_el_siguiente(self):
        urlopen, llamadas = self._red(serper=self._uerr.HTTPError("u", 403, "x", {}, None), tavily=self.TAVILY)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = self._todos().execute(query="q")
        assert [d for d, _ in llamadas] == ["serper", "tavily"] and r["data"]["motor"] == "tavily"

    def test_la_persona_pide_un_motor(self):
        urlopen, llamadas = self._red(brave=self.BRAVE)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = self._todos().execute(query="q", motor="brave")
        assert [d for d, _ in llamadas] == ["brave"] and r["data"]["motor"] == "brave"
        assert llamadas[0][1].get_header("X-subscription-token") == "br"
        assert len(r["data"]["results"][0]["snippet"]) <= 400

    def test_un_motor_sin_clave_se_dice(self):
        urlopen, _ = self._red(tavily=self.TAVILY)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = SearchWebTool(tavily_api_key="tv").execute(query="q", motor="google")
        assert r["data"]["motor"] == "tavily" and "google: no está configurado" in r["data"]["aviso"].lower()

    def test_sin_claves_solo_duckduckgo(self):
        urlopen, llamadas = self._red(duckduckgo=DDG_HTML)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            r = SearchWebTool().execute(query="fastapi", motor="google")
        assert [d for d, _ in llamadas] == ["duckduckgo"] and r["data"]["motor"] == "duckduckgo"

    def test_el_orden_se_cambia(self):
        urlopen, llamadas = self._red(tavily=self.TAVILY)
        with patch("src.tools.web.urllib.request.urlopen", side_effect=urlopen):
            self._todos(orden=("tavily", "google")).execute(query="q")
        assert [d for d, _ in llamadas] == ["tavily"]

    def test_el_esquema_ofrece_elegir_motor(self):
        assert SearchWebTool().parameters["properties"]["motor"]["enum"] == ["google", "tavily", "brave", "duckduckgo"]

    def test_las_claves_salen_del_entorno_y_no_se_ven(self, monkeypatch):
        from src.config import get_settings, reset_settings

        monkeypatch.setenv("SERPER_API_KEY", "sp-secreta")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "br-secreta")
        monkeypatch.setenv("MORGAN_BUSCADORES", "tavily, google")
        reset_settings()
        try:
            s = get_settings()
            assert (s.serper_api_key, s.brave_search_api_key, s.buscadores) == ("sp-secreta", "br-secreta",
                                                                               ("tavily", "google"))
            assert "secreta" not in str(s.redacted())
        finally:
            reset_settings()

    def test_la_api_las_cablea(self, monkeypatch):
        from src.api import dependencies
        from src.config import reset_settings

        monkeypatch.setenv("SERPER_API_KEY", "sp-de-render")
        reset_settings()
        dependencies.reset_container()
        try:
            h = dependencies.get_container().tool_registry.get("search_web")
            assert h.serper_api_key == "sp-de-render" and h.orden[0] == "google"
        finally:
            reset_settings()
            dependencies.reset_container()
