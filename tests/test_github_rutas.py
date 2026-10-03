"""
Lo que el modelo escribe no puede cambiar a qué endpoint se llama.

**El fallo.** `github_leer_archivo` interpolaba la ruta del archivo en la URL de
la API sin comprobarla. `httpx` normaliza los `..` al construir la URL, así que
`/repos/duenyo/nombre/contents/../../../../user/emails` se convertía en
**`/user/emails`**: las direcciones privadas de la persona, desde una
herramienta que dice leer un archivo de un repositorio. Igual se alcanzaban
`/notifications`, `/user/keys` y `/gists`.

Y `_repo_valido` no lo tapaba, porque comprobaba la *forma*: `'../..'` tiene dos
trozos y ninguna barra de más, así que pasaba.

**Por qué importa aquí más que en otro sitio.** El valor lo compone el modelo a
partir de lo que le dicen, y lo que le dicen puede venir de un archivo que acaba
de leer. Este proyecto ya lo da por supuesto: el contenido de un repositorio
llega marcado como `untrusted_file_data` justo porque puede llevar
instrucciones. Un README con la ruta adecuada bastaba.

**Codificar no arreglaba nada.** `quote()` no toca los puntos, así que `..`
sobrevive intacto. Hay que rechazarlo por su nombre.
"""

import httpx
import pytest

from src.integraciones.github import (
    API,
    ErrorDeGitHub,
    _pedir,
    _repo_en_url,
    _trozo_de_url,
)
from src.tools.github import _repo_valido

#: Lo que se alcanzaba. Cada una devuelve algo que la herramienta no promete.
FUGAS = [
    "../../../../user/emails",
    "../../../../user/keys",
    "../../../../notifications",
    "../../../../gists",
]


class TestLaRutaNoPuedeSalirDelRepositorio:
    @pytest.mark.parametrize("ruta", FUGAS)
    def test_se_rechaza_salir_hacia_arriba(self, ruta):
        with pytest.raises(ErrorDeGitHub):
            _trozo_de_url(ruta, con_barras=True)

    @pytest.mark.parametrize("ruta", FUGAS)
    def test_y_sin_el_arreglo_la_url_acababa_en_otro_sitio(self, ruta):
        """Deja constancia de lo que pasaba, para que se entienda la anterior.

        Si algún día `httpx` dejara de normalizar, esta prueba lo diría — y el
        arreglo seguiría siendo correcto de todos modos.
        """
        cruda = httpx.URL(f"{API}/repos/duenyo/nombre/contents/{ruta}")
        assert not cruda.path.startswith("/repos/duenyo/nombre/"), (
            "La ruta ya no escapa sola; revisar si sigue haciendo falta el "
            "rechazo (probablemente sí: no depende de httpx)"
        )

    def test_un_nombre_de_archivo_normal_pasa(self):
        assert _trozo_de_url("README.md", con_barras=True) == "README.md"
        assert _trozo_de_url("src/api/app.py", con_barras=True) == "src/api/app.py"

    def test_lo_que_partiria_la_url_se_codifica(self):
        """`?` y `#` sí se pueden codificar, y entonces son nombres normales."""
        assert _trozo_de_url("raro?x=1", con_barras=True) == "raro%3Fx%3D1"
        assert _trozo_de_url("con espacio.md", con_barras=True) == "con%20espacio.md"


class TestElRepositorioTampoco:
    @pytest.mark.parametrize("repo", ["../..", "a/..", "../nombre", "./."])
    def test_los_puntos_no_son_nombres(self, repo):
        with pytest.raises(ErrorDeGitHub):
            _repo_en_url(repo)

        with pytest.raises(ValueError):
            _repo_valido(repo)

    def test_uno_de_verdad_pasa_por_los_dos(self):
        assert _repo_en_url("wv-Andy/Morgan") == "wv-Andy/Morgan"
        assert _repo_valido("wv-Andy/Morgan") == "wv-Andy/Morgan"


class TestLaRedDeSeguridad:
    """`_pedir` comprueba que la URL que sale es la que se pidió.

    No es la protección principal — esa es rechazar los puntos — sino la que no
    depende de que una función nueva se acuerde de validar, que es exactamente
    como apareció el fallo.
    """

    def test_una_ruta_que_se_normaliza_no_llega_a_salir(self, monkeypatch):
        def no_deberia_llamarse(*args, **kwargs):
            raise AssertionError("La petición salió pese a alterarse la ruta")

        monkeypatch.setattr(httpx, "get", no_deberia_llamarse)

        with pytest.raises(ErrorDeGitHub, match="no era válida"):
            _pedir("un-token", "/repos/duenyo/nombre/contents/../../../../user")

    def test_una_ruta_intacta_sigue_saliendo(self, monkeypatch):
        vistas = []

        class Respuesta:
            status_code = 200
            headers: dict = {}

            @staticmethod
            def json():
                return {"ok": True}

        def falsa(url, **kwargs):
            vistas.append(str(url))
            return Respuesta()

        monkeypatch.setattr(httpx, "get", falsa)

        assert _pedir("un-token", "/repos/duenyo/nombre/contents/README.md") == {"ok": True}
        assert vistas == [f"{API}/repos/duenyo/nombre/contents/README.md"]


class TestDesdeLaHerramientaEntera:
    """El camino completo: lo que llamaría el modelo, hasta la URL.

    Las de arriba prueban las piezas. Esta comprueba que nada entre la llamada
    a la herramienta y la petición HTTP deshace el arreglo — que es donde vivía
    el fallo, en la costura entre dos capas que cada una daba por hecho que
    validaba la otra.

    No se puede comprobar contra producción sin conectar una cuenta de GitHub
    de verdad, así que el token se simula y se intercepta la salida a la red.
    """

    @pytest.fixture
    def herramientas(self, monkeypatch):
        """Las cuatro herramientas, con token y con la red cortada."""
        from src.tools import github as tg

        monkeypatch.setattr(
            tg._HerramientaDeGitHub, "_token", lambda self: "un-token-de-mentira"
        )

        salidas = []

        def no_sale(*args, **kwargs):
            salidas.append(str(args[0]) if args else kwargs.get("url"))
            raise AssertionError(f"Salió una petición a {salidas[-1]}")

        monkeypatch.setattr(httpx, "get", no_sale)

        return {h.name: h for h in tg.github_tools(object())}, salidas

    @pytest.mark.parametrize("ruta", FUGAS)
    def test_leer_archivo_no_llega_a_pedir_nada(self, herramientas, ruta):
        tools, salidas = herramientas

        resultado = tools["github_leer_archivo"].execute(
            repo="wv-Andy/Morgan", ruta=ruta
        )

        assert resultado["success"] is False
        assert not salidas, f"La petición salió igual: {salidas}"
        assert "válido" in resultado["error"] or "valido" in resultado["error"]

    @pytest.mark.parametrize("repo", ["../..", "a/.."])
    def test_las_listas_tampoco(self, herramientas, repo):
        tools, salidas = herramientas

        for nombre in ("github_listar_issues", "github_listar_prs"):
            resultado = tools[nombre].execute(repo=repo)

            assert resultado["success"] is False, nombre
            assert not salidas, f"{nombre} dejó salir la petición a {salidas}"
