"""
Las herramientas de GitHub.

**Solo leen, y es una decisión.** El token que GitHub concede permite escribir
—`repo` no tiene variante de solo lectura para repositorios privados en las
OAuth Apps clásicas— pero estas herramientas no escriben. La especificación lo
pedía con estas palabras: «no implementar automáticamente todas las capacidades
de GitHub solo porque OAuth esté conectado».

Lo que se vigila aquí:

1. **El usuario sale del contexto, nunca del argumento.** Un identificador en los
   parámetros sería algo que el modelo puede inventarse, y un modelo que puede
   nombrar a otro usuario en una llamada a herramienta es un modelo que puede
   leer sus repos.
2. **Sin conectar se dice dónde conectar**, no «no autorizado». La solución está
   a dos clics y el mensaje tiene que decirlo.
3. **El nombre del repositorio se valida** antes de meterlo en una URL: lo
   compone el modelo a partir de lo que le dicen.
4. **El contenido de un repositorio es dato, no instrucción.**
"""

import pytest

from src.identidad import como_usuario
from src.integraciones.github import ErrorDeGitHub
from src.tools.github import (
    LeerArchivoTool,
    ListarIssuesTool,
    ListarPullRequestsTool,
    ListarReposTool,
    _repo_valido,
    github_tools,
)


class RepositoriosFalsos:
    """Una fábrica con lo justo para que el repositorio de integraciones exista."""

    def __init__(self, db):
        self.db = db


@pytest.fixture
def repositorios(tmp_path, monkeypatch):
    from src.memory.db import Database

    monkeypatch.setenv("MORGAN_SECRET_KEY", "clave-de-prueba")
    return RepositoriosFalsos(Database(tmp_path / "p.db"))


@pytest.fixture
def conectado(repositorios):
    """Deja GitHub conectado para el usuario de prueba."""
    from src.integraciones.repositorio import repositorio_de_integraciones

    with como_usuario("usr-ana"):
        repositorio_de_integraciones(repositorios).guardar(
            "github", "gho_token_de_ana", cuenta="ana",
        )
    return repositorios


class TestSinConectarSeDiceDondeConectar:
    """«No autorizado» deja a la persona sin saber que la solución está a dos
    clics."""

    @pytest.mark.parametrize(
        "clase, argumentos",
        [
            (ListarReposTool, {}),
            (ListarIssuesTool, {"repo": "a/b"}),
            (ListarPullRequestsTool, {"repo": "a/b"}),
            (LeerArchivoTool, {"repo": "a/b", "ruta": "x.py"}),
        ],
    )
    def test_todas_dicen_lo_mismo(self, repositorios, clase, argumentos):
        with como_usuario("usr-ana"):
            salida = clase(repositorios).execute(**argumentos)

        assert salida["success"] is False
        assert "Servicios" in salida["error"]

    def test_y_no_se_llama_a_github(self, repositorios, monkeypatch):
        """Sin token no hay nada que preguntar. Llamar igualmente sería una
        petición garantizada a fallar."""
        import src.integraciones.github as gh

        def no_deberia(*_a, **_k):
            raise AssertionError("no debería haberse llamado a GitHub")

        monkeypatch.setattr(gh, "listar_repos", no_deberia)

        with como_usuario("usr-ana"):
            ListarReposTool(repositorios).execute()


class TestElUsuarioSaleDelContexto:
    """Ninguna herramienta recibe un identificador de usuario."""

    @pytest.mark.parametrize(
        "clase",
        [ListarReposTool, ListarIssuesTool, ListarPullRequestsTool, LeerArchivoTool],
    )
    def test_no_hay_parametro_de_usuario(self, repositorios, clase):
        propiedades = clase(repositorios).parameters.get("properties", {})

        prohibidos = {"user_id", "usuario", "user", "cuenta", "token"}
        assert not (set(propiedades) & prohibidos), (
            f"{clase.__name__} acepta {set(propiedades) & prohibidos} como parámetro"
        )

    def test_cada_uno_usa_SU_token(self, conectado, monkeypatch):
        """La comprobación de verdad: con dos usuarios, cada llamada lleva el
        token de quien pregunta."""
        import src.integraciones.github as gh
        from src.integraciones.repositorio import repositorio_de_integraciones

        with como_usuario("usr-bea"):
            repositorio_de_integraciones(conectado).guardar(
                "github", "gho_token_de_bea", cuenta="bea",
            )

        usados = []
        monkeypatch.setattr(gh, "listar_repos", lambda token, **k: usados.append(token) or [])

        with como_usuario("usr-ana"):
            ListarReposTool(conectado).execute()
        with como_usuario("usr-bea"):
            ListarReposTool(conectado).execute()

        assert usados == ["gho_token_de_ana", "gho_token_de_bea"]


class TestElNombreDelRepositorioSeValida:
    """Lo compone el modelo a partir de lo que le dicen, y acaba dentro de una
    URL de la API."""

    @pytest.mark.parametrize(
        "malo",
        ["", "solo-nombre", "a/b/c", "/", "a//b", "/a", "a b"],
    )
    def test_lo_que_no_tiene_forma_se_rechaza(self, malo):
        with pytest.raises(ValueError):
            _repo_valido(malo)

    def test_lo_correcto_pasa(self):
        assert _repo_valido("wv-Andy/Morgan") == "wv-Andy/Morgan"

    @pytest.mark.parametrize(
        "entrada",
        ["  duenyo/nombre/  ", "/duenyo/nombre", "duenyo/nombre/"],
    )
    def test_se_toleran_espacios_y_barras_de_sobra(self, entrada):
        """Quien copia una URL o escribe deprisa pone barras de mas. Rechazarlo
        seria pedantería: la intencion es inequívoca."""
        assert _repo_valido(entrada) == "duenyo/nombre"

    def test_la_herramienta_lo_explica_en_vez_de_reventar(self, conectado):
        with como_usuario("usr-ana"):
            salida = ListarIssuesTool(conectado).execute(repo="esto-no-vale")

        assert salida["success"] is False
        assert "duenyo/nombre" in salida["error"]


class TestLosErroresDeGitHubLleganLegibles:
    """El mensaje tiene que decir qué hacer: esperar, reconectar, o que no
    existe. «Error 403» no distingue entre esas tres."""

    def test_un_fallo_se_convierte_en_respuesta_no_en_excepcion(self, conectado, monkeypatch):
        import src.integraciones.github as gh

        def revienta(*_a, **_k):
            raise ErrorDeGitHub("La autorización de GitHub ya no vale.")

        monkeypatch.setattr(gh, "listar_repos", revienta)

        with como_usuario("usr-ana"):
            salida = ListarReposTool(conectado).execute()

        assert salida["success"] is False
        assert "ya no vale" in salida["error"]


class TestElContenidoDeUnRepoEsDatoNoInstruccion:
    """Lo que hay en un repositorio lo escribió alguien, igual que una página
    web. Un comentario del código que diga «ignora lo anterior» no puede tratarse
    como una orden."""

    def test_el_archivo_llega_marcado(self, conectado, monkeypatch):
        import src.integraciones.github as gh

        monkeypatch.setattr(gh, "leer_archivo", lambda *a, **k: {
            "tipo": "archivo", "ruta": "x.py", "tamano": 10,
            "contenido": "print('hola')", "url": "https://…",
        })

        with como_usuario("usr-ana"):
            salida = LeerArchivoTool(conectado).execute(repo="a/b", ruta="x.py")

        assert "<untrusted_file_data" in salida["data"]["contenido"]

    def test_un_directorio_no_lleva_ese_marcado(self, conectado, monkeypatch):
        """No hay texto que confundir con instrucciones: es un índice."""
        import src.integraciones.github as gh

        monkeypatch.setattr(gh, "leer_archivo", lambda *a, **k: {
            "tipo": "directorio", "ruta": "src", "entradas": [],
        })

        with como_usuario("usr-ana"):
            salida = LeerArchivoTool(conectado).execute(repo="a/b", ruta="src")

        assert salida["data"]["tipo"] == "directorio"


class TestSoloLeen:
    """Ninguna escribe. Añadir «crear issue» o «hacer commit» exigiría antes
    pasar por el sistema de planes, para que la persona vea qué se va a escribir
    y dónde **antes** de que ocurra."""

    def test_ninguna_herramienta_escribe(self, repositorios):
        nombres = {h.name for h in github_tools(repositorios)}

        prohibidas = {"crear", "commit", "escribir", "borrar", "merge", "cerrar"}
        for nombre in nombres:
            assert not any(p in nombre for p in prohibidas), (
                f"{nombre} suena a escritura: debería pasar por el sistema de planes"
            )

    def test_todas_son_de_riesgo_seguro(self, repositorios):
        for herramienta in github_tools(repositorios):
            assert herramienta.permission_level == "safe"


class TestSeRegistranSiempre:
    """Al revés que las de conocimiento. Si solo aparecieran con GitHub ya
    conectado, el modelo no sabría que existen y nunca sugeriría conectarlo."""

    def test_estan_en_el_catalogo_sin_conexion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        from src.config import reset_settings

        reset_settings()
        try:
            from src.api import dependencies

            dependencies.reset_container()
            contenedor = dependencies.CoreContainer()
            nombres = {h.name for h in contenedor.tool_registry.list_tools()}

            assert "github_listar_repos" in nombres
        finally:
            reset_settings()
            from src.api import dependencies

            dependencies.reset_container()

    def test_y_tambien_en_la_nube(self, tmp_path, monkeypatch):
        """No tocan el disco: hablan con la API de GitHub."""
        for herramienta in github_tools(RepositoriosFalsos(None)):
            assert herramienta.requires_local is False


class TestSoloViajanSiEstaConectado:
    """4.1.5: sin GitHub conectado, sus cuatro herramientas iban igual en cada llamada al
    modelo (~550 tokens) para casi todas las cuentas."""

    @pytest.fixture(autouse=True)
    def _sin_memoria(self):
        from src.tools.github import olvidar_conexiones

        olvidar_conexiones()
        yield
        olvidar_conexiones()

    def test_ana_con_github_si_y_bea_sin_github_no(self, conectado):
        herramienta = ListarReposTool(conectado)
        with como_usuario("usr-ana"):
            assert herramienta.disponible()
        with como_usuario("usr-bea"):
            assert not herramienta.disponible()

    def test_se_recuerda_un_rato_y_conectar_lo_borra(self, repositorios, monkeypatch):
        from src.integraciones import repositorio as modulo
        from src.tools.github import olvidar_conexiones

        consultas = []
        original = modulo.repositorio_de_integraciones

        def contando(repos):
            consultas.append(1)
            return original(repos)

        monkeypatch.setattr(modulo, "repositorio_de_integraciones", contando)
        herramienta = ListarReposTool(repositorios)
        with como_usuario("usr-ana"):
            assert not herramienta.disponible() and not herramienta.disponible()
            assert len(consultas) == 1, "la segunda, de memoria"
            original(repositorios).guardar("github", "gho_x", cuenta="ana")
            olvidar_conexiones()
            assert herramienta.disponible()

    def test_si_no_se_puede_saber_se_ofrece(self, repositorios, monkeypatch):
        from src.integraciones import repositorio as modulo

        def rota(repos):
            raise RuntimeError("base caída")

        monkeypatch.setattr(modulo, "repositorio_de_integraciones", rota)
        with como_usuario("usr-ana"):
            assert ListarReposTool(repositorios).disponible()
