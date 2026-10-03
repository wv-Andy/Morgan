"""
Lo que Morgan le pregunta a GitHub, y cómo traduce lo que le contestan.

**Por qué ahora.** `src/integraciones/github.py` estaba al 48%, y lo que no se
ejecutaba era justo esto: las traducciones de error y el mapeo de los campos de
GitHub a la forma de Morgan. Es el módulo donde ya apareció una fuga —la ruta
del archivo se salía del repositorio, ver `test_github_rutas.py`— y desde la
V1.9 está **en uso de verdad**: hay una cuenta conectada.

Un nombre de campo mal puesto aquí no falla: devuelve `None` y Morgan dice que
tus repositorios no tienen nombre. Es la clase de error que solo se ve usándolo,
y solo si alguien mira con atención.

Las respuestas de GitHub se simulan. Lo que se comprueba es la traducción y el
manejo de errores, no que GitHub esté en pie, y una prueba que dependa de la red
no es reproducible ni puede correr sin credenciales.
"""

import base64
import json

import httpx
import pytest

from src.integraciones import github as gh

TOKEN = "gho_de_mentira"


def respuesta(cuerpo, status=200, cabeceras=None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=cuerpo,
        headers=cabeceras or {},
        request=httpx.Request("GET", "https://api.github.com/x"),
    )


@pytest.fixture
def responde(monkeypatch):
    """Deja que GitHub «conteste» lo que diga la prueba."""

    def montar(cuerpo, status=200, cabeceras=None):
        monkeypatch.setattr(
            httpx, "get", lambda *a, **k: respuesta(cuerpo, status, cabeceras)
        )

    return montar


class TestLosErroresSeTraducenAAlgoQueSePuedeHacer:
    """Un «error 403» no le dice nada a nadie. Lo que hace falta saber es si hay
    que esperar, reconectar o si simplemente no existe.
    """

    def test_401_es_volver_a_conectar(self, responde):
        responde({"message": "Bad credentials"}, status=401)

        with pytest.raises(gh.ErrorDeGitHub) as fallo:
            gh.listar_repos(TOKEN)

        mensaje = str(fallo.value)
        assert "Servicios" in mensaje, "No dice DÓNDE se vuelve a conectar"
        assert "401" not in mensaje

    def test_403_con_el_cupo_agotado_es_esperar(self, responde):
        responde({}, status=403, cabeceras={"x-ratelimit-remaining": "0"})

        with pytest.raises(gh.ErrorDeGitHub, match="Espera"):
            gh.listar_repos(TOKEN)

    def test_403_sin_cupo_agotado_es_un_permiso_que_falta(self, responde):
        """Los dos casos son 403 y la salida es distinta: esperar no arregla un
        permiso que no se concedió, y reconectar no arregla un límite.
        """
        responde({}, status=403, cabeceras={"x-ratelimit-remaining": "4999"})

        with pytest.raises(gh.ErrorDeGitHub) as fallo:
            gh.listar_repos(TOKEN)

        assert "permisos" in str(fallo.value)
        assert "Espera" not in str(fallo.value)

    def test_404_no_distingue_entre_no_existe_y_no_tengo_acceso(self, responde):
        """Y lo dice, porque desde fuera de un repositorio privado las dos cosas
        se ven igual. Callarlo llevaría a jurar que el repositorio no existe.
        """
        responde({}, status=404)

        with pytest.raises(gh.ErrorDeGitHub) as fallo:
            gh.listar_issues(TOKEN, "duenyo/nombre")

        assert "privado" in str(fallo.value)

    def test_un_fallo_de_red_no_se_confunde_con_un_rechazo(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(
            httpx.ConnectError("sin red")))

        with pytest.raises(gh.ErrorDeGitHub, match="No se pudo contactar"):
            gh.listar_repos(TOKEN)


class TestLosRepositorios:
    FILA = {
        "full_name": "ana/agenda",
        "name": "agenda",
        "private": True,
        "description": "Un agente personal",
        "language": "Python",
        "updated_at": "2026-09-10T10:00:00Z",
        "html_url": "https://github.com/ana/agenda",
    }

    def test_el_nombre_es_el_completo_y_no_el_corto(self, responde):
        """`duenyo/nombre` y no `nombre`: es lo que hace falta para poder pedir
        sus issues después, y lo que distingue dos repos con el mismo nombre en
        cuentas distintas.
        """
        responde([self.FILA])

        repos = gh.listar_repos(TOKEN)

        assert repos[0]["nombre"] == "ana/agenda"

    def test_se_dice_si_es_privado(self, responde):
        """Importa para lo que Morgan diga después: contar el contenido de un
        repositorio privado en un sitio público sería un problema.
        """
        responde([self.FILA])

        assert gh.listar_repos(TOKEN)[0]["privado"] is True

    def test_llegan_los_demas_campos(self, responde):
        responde([self.FILA])

        repo = gh.listar_repos(TOKEN)[0]

        assert repo["descripcion"] == "Un agente personal"
        assert repo["lenguaje"] == "Python"
        assert repo["url"].endswith("ana/agenda")
        assert repo["actualizado"].startswith("2026-09-10")

    def test_un_repositorio_sin_descripcion_no_revienta(self, responde):
        """GitHub manda `null` en casi todo lo opcional."""
        responde([{"full_name": "a/b", "private": False}])

        repo = gh.listar_repos(TOKEN)[0]

        assert repo["nombre"] == "a/b"
        assert repo["descripcion"] is None

    def test_sin_repositorios_devuelve_una_lista_vacia(self, responde):
        responde([])

        assert gh.listar_repos(TOKEN) == []


class TestLasIssuesNoTraenPullRequests:
    """GitHub los devuelve **mezclados**: tienen el mismo endpoint, y un PR
    aparece en la lista de issues con un campo `pull_request` de más.

    Quien pide «las issues abiertas» no espera ver PRs entre ellas, y Morgan
    contaría mal si los dejara pasar.
    """

    def test_se_filtran(self, responde):
        responde([
            {"number": 1, "title": "Una issue de verdad", "state": "open"},
            {"number": 2, "title": "Un PR colado", "state": "open",
             "pull_request": {"url": "https://api.github.com/..."}},
            {"number": 3, "title": "Otra issue", "state": "open"},
        ])

        issues = gh.listar_issues(TOKEN, "duenyo/nombre")

        assert [i["numero"] for i in issues] == [1, 3], (
            "Un pull request se ha colado en la lista de issues"
        )

    def test_llegan_los_campos_que_importan(self, responde):
        responde([{
            "number": 7,
            "title": "Algo que arreglar",
            "state": "open",
            "user": {"login": "wv-Andy"},
            "labels": [{"name": "bug"}, {"name": "urgente"}],
            "comments": 3,
            "updated_at": "2026-09-10T10:00:00Z",
            "html_url": "https://github.com/x/y/issues/7",
        }])

        issue = gh.listar_issues(TOKEN, "duenyo/nombre")[0]

        assert issue["autor"] == "wv-Andy"
        assert issue["etiquetas"] == ["bug", "urgente"]
        assert issue["comentarios"] == 3

    def test_una_issue_sin_autor_no_revienta(self, responde):
        """Las de una cuenta borrada vienen con `user: null`."""
        responde([{"number": 1, "title": "x", "state": "open", "user": None}])

        assert gh.listar_issues(TOKEN, "a/b")[0]["autor"] is None

    @pytest.mark.parametrize("pedido,esperado", [
        ("open", "open"), ("closed", "closed"), ("all", "all"),
        ("inventado", "open"), ("", "open"),
    ])
    def test_un_estado_que_no_existe_se_convierte_en_abierto(
        self, monkeypatch, pedido, esperado
    ):
        """El estado lo compone el modelo. Mandarle a GitHub uno inventado da un
        422 que no explica nada; caer en «open» es lo que la persona esperaba.
        """
        vistos = {}

        def espia(url, **kwargs):
            vistos.update(kwargs.get("params") or {})
            return respuesta([])

        monkeypatch.setattr(httpx, "get", espia)
        gh.listar_issues(TOKEN, "a/b", estado=pedido)

        assert vistos["state"] == esperado


class TestLosPullRequests:
    def test_traen_las_dos_ramas(self, responde):
        """De dónde sale y a dónde va. Sin eso, «revisa este PR» no dice qué
        comparar con qué.
        """
        responde([{
            "number": 12, "title": "Arregla el móvil", "state": "open",
            "user": {"login": "wv-Andy"},
            "head": {"ref": "arreglo-movil"},
            "base": {"ref": "main"},
            "draft": False,
            "updated_at": "2026-09-10T10:00:00Z",
            "html_url": "https://github.com/x/y/pull/12",
        }])

        pr = gh.listar_prs(TOKEN, "duenyo/nombre")[0]

        assert pr["rama"] == "arreglo-movil"
        assert pr["hacia"] == "main"
        assert pr["borrador"] is False

    def test_uno_sin_ramas_no_revienta(self, responde):
        responde([{"number": 1, "title": "x", "state": "open"}])

        pr = gh.listar_prs(TOKEN, "a/b")[0]

        assert pr["rama"] is None and pr["hacia"] is None


class TestLeerUnArchivo:
    @staticmethod
    def contenido(texto: str) -> dict:
        return {
            "path": "README.md",
            "size": len(texto),
            "encoding": "base64",
            "content": base64.b64encode(texto.encode("utf-8")).decode(),
            "html_url": "https://github.com/x/y/blob/main/README.md",
        }

    def test_el_texto_llega_decodificado(self, responde):
        responde(self.contenido("# Morgan\n\nUn agente."))

        archivo = gh.leer_archivo(TOKEN, "duenyo/nombre", "README.md")

        assert archivo["tipo"] == "archivo"
        assert "Un agente." in archivo["contenido"]

    def test_los_acentos_sobreviven(self, responde):
        """GitHub manda base64 y hay que decodificar en UTF-8. Con la
        codificación equivocada, un archivo en español llega ilegible.
        """
        responde(self.contenido("Configuración y ñandú"))

        assert "Configuración y ñandú" in gh.leer_archivo(TOKEN, "a/b", "x.md")["contenido"]

    def test_una_ruta_de_directorio_devuelve_su_indice(self, responde):
        """Es lo que suele buscarse al pasar una ruta sin nombre de archivo, y
        GitHub responde una lista en lugar de un objeto.
        """
        responde([
            {"name": "api", "type": "dir", "size": 0},
            {"name": "main.py", "type": "file", "size": 4096},
        ])

        salida = gh.leer_archivo(TOKEN, "duenyo/nombre", "src")

        assert salida["tipo"] == "directorio"
        assert [e["nombre"] for e in salida["entradas"]] == ["api", "main.py"]

    def test_un_binario_se_dice_en_lugar_de_devolver_ruido(self, responde):
        """Entregar bytes al modelo llena el contexto de algo que no puede leer."""
        responde({"path": "foto.png", "encoding": "none", "content": ""})

        with pytest.raises(gh.ErrorDeGitHub) as fallo:
            gh.leer_archivo(TOKEN, "a/b", "foto.png")

        assert "binario" in str(fallo.value)

    def test_un_archivo_que_no_es_utf8_se_dice(self, responde):
        responde({
            "path": "raro.bin",
            "encoding": "base64",
            "content": base64.b64encode(bytes([0xFF, 0xFE, 0x00])).decode(),
        })

        with pytest.raises(gh.ErrorDeGitHub, match="no es texto"):
            gh.leer_archivo(TOKEN, "a/b", "raro.bin")

    def test_se_puede_pedir_una_rama(self, monkeypatch):
        vistos = {}

        def espia(url, **kwargs):
            vistos.update(kwargs.get("params") or {})
            return respuesta(TestLeerUnArchivo.contenido("hola"))

        monkeypatch.setattr(httpx, "get", espia)
        gh.leer_archivo(TOKEN, "a/b", "x.md", rama="desarrollo")

        assert vistos["ref"] == "desarrollo"


class TestElLimiteDeResultados:
    @pytest.mark.parametrize("pedido,esperado", [
        (30, 30), (1, 1), (100, 100),
        (500, 100),   # GitHub no acepta más de 100 por página
        (0, 1), (-5, 1),
    ])
    def test_se_acota_a_lo_que_github_acepta(self, monkeypatch, pedido, esperado):
        """Un límite fuera de rango da un 422 que no explica nada, y el límite
        lo compone el modelo.
        """
        vistos = {}

        def espia(url, **kwargs):
            vistos.update(kwargs.get("params") or {})
            return respuesta([])

        monkeypatch.setattr(httpx, "get", espia)
        gh.listar_repos(TOKEN, limite=pedido)

        assert vistos["per_page"] == esperado


class TestLaCuentaConectada:
    def test_se_devuelve_quien_es(self, monkeypatch):
        """Sin esto la interfaz diría «GitHub conectado» sin decir a qué cuenta,
        y quien tenga dos —la personal y la del trabajo— no sabría sobre cuál va
        a actuar Morgan.
        """
        monkeypatch.setattr(httpx, "get", lambda *a, **k: respuesta({
            "login": "wv-Andy", "name": "Andy",
            "avatar_url": "https://avatars.githubusercontent.com/u/1",
        }))

        cuenta = gh.cuenta_de(TOKEN)

        assert cuenta["login"] == "wv-Andy"
        assert cuenta["nombre"] == "Andy"
        assert cuenta["avatar"].startswith("https://")

    def test_un_token_caducado_se_dice_con_palabras(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: respuesta({}, status=401))

        with pytest.raises(gh.ErrorDeGitHub, match="Vuelve a conectar"):
            gh.cuenta_de(TOKEN)


class TestLosÚltimosCaminosDeError:
    """Ramas que no se recorrían con nada. Son las que se ejecutan justo cuando
    algo va mal, que es cuando peor viene un `AttributeError` encima.
    """

    def test_sin_red_al_pedir_la_cuenta(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(
            httpx.ConnectError("sin red")))

        with pytest.raises(gh.ErrorDeGitHub, match="No se pudo consultar la cuenta"):
            gh.cuenta_de(TOKEN)

    def test_un_500_al_pedir_la_cuenta(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: respuesta({}, status=500))

        with pytest.raises(gh.ErrorDeGitHub, match="500"):
            gh.cuenta_de(TOKEN)

    def test_un_500_en_una_consulta_cualquiera(self, responde):
        """Ni 401 ni 403 ni 404: hay que decir algo igualmente, y el número es
        lo único que se sabe.
        """
        responde({}, status=500)

        with pytest.raises(gh.ErrorDeGitHub, match="500"):
            gh.listar_repos(TOKEN)

    @pytest.mark.parametrize("vacio", ["", "   ", None])
    def test_un_nombre_vacío_se_rechaza_antes_de_salir_a_la_red(self, vacio):
        """El modelo puede componer una ruta vacía. Interpolada sin más, la URL
        apunta al directorio de arriba.
        """
        with pytest.raises(gh.ErrorDeGitHub, match="Falta el nombre"):
            gh._trozo_de_url(vacio)

    @pytest.mark.parametrize("malo", ["solo-el-nombre", "a/b/c", "", "/", "a//b"])
    def test_un_repositorio_mal_escrito_se_explica(self, malo):
        """Y se dice cómo se escribe, porque el modelo puede reintentarlo bien
        si el error se lo explica.
        """
        with pytest.raises(gh.ErrorDeGitHub, match="duenyo/nombre"):
            gh._repo_en_url(malo)

    @pytest.mark.parametrize("escrito", ["a/b/", "/a/b", " a/b ", "/a/b/"])
    def test_las_barras_y_espacios_de_sobra_se_perdonan(self, escrito):
        """No es lo mismo que perdonar un `..`. Aquí sobra puntuación y la
        intención está clara; allí la ruta cambia de destino.

        Importa porque el repositorio lo compone el modelo copiando de donde
        sea, y suele venir con una barra final pegada de una URL.
        """
        assert gh._repo_en_url(escrito) == "a/b"
