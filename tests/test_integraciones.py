"""
Servicios externos: conectar, consultar y desconectar (V1.9).

**Esto no es el login de Morgan**, y la distinción es la que gobierna todo el
diseño: una integración es *tú* autorizando a Morgan a usar tu cuenta de otro
servicio, y por tanto cuelga siempre de una cuenta de Morgan ya autenticada.

Lo que se comprueba aquí, por orden de lo que costaría equivocarse:

1. **El `state` decide de quién es la autorización, no la cookie.** Sin esa
   comprobación, una web ajena podría dejar SU cuenta de GitHub conectada a la
   sesión de otra persona, y Morgan actuaría después sobre repositorios ajenos
   creyendo que son los tuyos.
2. **El token nunca sale del backend.** Ni en la lista, ni en el detalle, ni en
   la auditoría.
3. **Se guarda cifrado.** Un token de GitHub abre repositorios privados; en
   claro, una copia de la base es una copia de las llaves.
4. **Aislamiento entre usuarios**, en las dos implementaciones. La de Supabase
   se escribe y se prueba a la vez que la de SQLite, que es justo lo que no se
   hizo con las conversaciones.
5. **Si no puede funcionar, se dice.** Sin credenciales del servidor no hay
   botón de conectar.
"""

import json
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.sesion_web import CABECERA_CSRF
from src.config import reset_settings
from src.identidad import como_usuario
from src.integraciones.repositorio import (
    IntegracionesSQLite,
    IntegracionesSupabase,
)
from src.memory.db import Database

CLAVE = "una-clave-de-cifrado-de-prueba"
REGISTRO = {"username": "ana", "email": "ana@ejemplo.co", "password": "contrasena-larga"}


@pytest.fixture
def repo_sqlite(monkeypatch):
    monkeypatch.setenv("MORGAN_SECRET_KEY", CLAVE)
    ruta = pathlib.Path(tempfile.mkdtemp()) / "integraciones.db"
    return IntegracionesSQLite(Database(ruta))


class ClienteEspia:
    """Un PostgREST de mentira que apunta lo que le piden."""

    def __init__(self, respuesta=None):
        self.peticiones: list[dict] = []
        self.respuesta = respuesta if respuesta is not None else []

    def _apuntar(self, metodo, tabla, consulta="", filas=None, on_conflict=None):
        self.peticiones.append({
            "metodo": metodo, "tabla": tabla, "consulta": consulta,
            "filas": filas or [], "on_conflict": on_conflict,
        })
        return self.respuesta

    def select(self, tabla, consulta=""):
        return self._apuntar("GET", tabla, consulta)

    def update(self, tabla, consulta, valores):
        return self._apuntar("PATCH", tabla, consulta, [valores])

    def insert(self, tabla, filas):
        return self._apuntar("POST", tabla, filas=filas)

    def upsert(self, tabla, filas, on_conflict):
        return self._apuntar("POST", tabla, filas=filas, on_conflict=on_conflict)

    def delete(self, tabla, consulta):
        return self._apuntar("DELETE", tabla, consulta)


# ── El cifrado ──────────────────────────────────────────────────────────────


class TestElTokenSeGuardaCifrado:
    """Con un token de GitHub se puede leer código privado y escribir. En claro,
    una copia de seguridad extraviada deja de ser un problema de privacidad y
    pasa a ser uno de acceso."""

    def test_en_la_base_no_aparece_el_token(self, repo_sqlite):
        with como_usuario("usr-ana"):
            repo_sqlite.guardar("github", "gho_secretodeverdad", cuenta="ana")

        with repo_sqlite.db.connect() as conn:
            fila = conn.execute("SELECT token_cifrado FROM integraciones").fetchone()

        assert "gho_secretodeverdad" not in fila["token_cifrado"]

    def test_pero_se_recupera_entero(self, repo_sqlite):
        with como_usuario("usr-ana"):
            repo_sqlite.guardar("github", "gho_secretodeverdad")

            assert repo_sqlite.token_de("github") == "gho_secretodeverdad"

    def test_sin_clave_no_se_guarda_nada(self, monkeypatch):
        """No se guarda en claro «mientras tanto»: un token sin cifrar no se
        distingue de uno cifrado mirando la tabla, así que el día que alguien lo
        note llevará meses ahí."""
        from src.integraciones.secretos import SinClaveDeCifrado

        monkeypatch.delenv("MORGAN_SECRET_KEY", raising=False)
        repo = IntegracionesSQLite(Database(pathlib.Path(tempfile.mkdtemp()) / "x.db"))

        with pytest.raises(SinClaveDeCifrado):
            with como_usuario("usr-ana"):
                repo.guardar("github", "gho_algo")

    def test_si_la_clave_cambia_se_dice_en_vez_de_devolver_vacio(self, repo_sqlite, monkeypatch):
        """Un token vacío se usaría como válido y daría un 401 de GitHub, que es
        un síntoma que no apunta a su causa."""
        with como_usuario("usr-ana"):
            repo_sqlite.guardar("github", "gho_algo")

        monkeypatch.setenv("MORGAN_SECRET_KEY", "otra-clave-distinta")

        with pytest.raises(ValueError, match="MORGAN_SECRET_KEY"):
            with como_usuario("usr-ana"):
                repo_sqlite.token_de("github")


# ── Aislamiento ─────────────────────────────────────────────────────────────


class TestCadaUnoConLoSuyo:
    def test_lo_de_ana_no_lo_ve_bea(self, repo_sqlite):
        with como_usuario("usr-ana"):
            repo_sqlite.guardar("github", "gho_de_ana", cuenta="ana")

        with como_usuario("usr-bea"):
            assert repo_sqlite.listar() == []
            assert repo_sqlite.obtener("github") is None
            assert repo_sqlite.token_de("github") is None

    def test_desconectar_no_desconecta_al_otro(self, repo_sqlite):
        with como_usuario("usr-ana"):
            repo_sqlite.guardar("github", "gho_de_ana")
        with como_usuario("usr-bea"):
            repo_sqlite.guardar("github", "gho_de_bea")
            repo_sqlite.eliminar("github")

        with como_usuario("usr-ana"):
            assert repo_sqlite.token_de("github") == "gho_de_ana"

    def test_dos_personas_pueden_conectar_el_mismo_servicio(self, repo_sqlite):
        """La clave primaria es (user_id, servicio). Si fuera solo el servicio,
        conectar el segundo pisaría al primero."""
        with como_usuario("usr-ana"):
            repo_sqlite.guardar("github", "gho_de_ana", cuenta="ana")
        with como_usuario("usr-bea"):
            repo_sqlite.guardar("github", "gho_de_bea", cuenta="bea")

        with como_usuario("usr-ana"):
            assert repo_sqlite.obtener("github").cuenta == "ana"
        with como_usuario("usr-bea"):
            assert repo_sqlite.obtener("github").cuenta == "bea"


class TestLaVersionDeSupabaseFiltraIgual:
    """Se escribe y se prueba a la vez que la de SQLite. La alternativa —dejarla
    para cuando haga falta— es como se llegó a tener media capa de datos sin
    filtrar por usuario en producción."""

    @pytest.fixture
    def cliente(self, monkeypatch):
        monkeypatch.setenv("MORGAN_SECRET_KEY", CLAVE)
        return ClienteEspia()

    @pytest.mark.parametrize(
        "metodo, argumentos",
        [
            ("obtener", ("github",)),
            ("token_de", ("github",)),
            ("listar", ()),
            ("eliminar", ("github",)),
            ("anotar_error", ("github", "algo falló")),
        ],
    )
    def test_toda_operacion_filtra_por_usuario(self, cliente, metodo, argumentos):
        repo = IntegracionesSupabase(cliente)
        with como_usuario("usr-ana"):
            getattr(repo, metodo)(*argumentos)

        for peticion in cliente.peticiones:
            assert "user_id=eq.usr-ana" in peticion["consulta"], (
                f"{metodo} no filtra: {peticion['consulta']}"
            )

    def test_guardar_deja_el_usuario_puesto(self, cliente):
        repo = IntegracionesSupabase(cliente)
        with como_usuario("usr-ana"):
            repo.guardar("github", "gho_algo")

        fila = cliente.peticiones[0]["filas"][0]
        assert fila["user_id"] == "usr-ana"

    def test_el_on_conflict_nombra_la_clave_real(self, cliente):
        """La clave primaria es (user_id, servicio). Nombrar solo una columna da
        «no unique or exclusion constraint matching», que llega al navegador
        como un 500 sin explicación. Ya pasó con `sessions`."""
        repo = IntegracionesSupabase(cliente)
        with como_usuario("usr-ana"):
            repo.guardar("github", "gho_algo")

        assert cliente.peticiones[0]["on_conflict"] == "user_id,servicio"

    def test_el_token_tampoco_viaja_en_claro(self, cliente):
        repo = IntegracionesSupabase(cliente)
        with como_usuario("usr-ana"):
            repo.guardar("github", "gho_secretodeverdad")

        enviado = json.dumps(cliente.peticiones[0]["filas"][0])
        assert "gho_secretodeverdad" not in enviado


# ── El estado de OAuth ──────────────────────────────────────────────────────


class TestElEstadoProtegeLaAutorizacion:
    """Sin comprobar que el `state` que vuelve es uno que se emitió, una web
    ajena podría completar el flujo y dejar su cuenta conectada a la sesión de
    otra persona."""

    def test_se_puede_consumir_una_vez(self, repo_sqlite):
        with como_usuario("usr-ana"):
            estado = repo_sqlite.crear_estado("github")

        assert repo_sqlite.consumir_estado(estado, "github") == "usr-ana"

    def test_pero_no_dos(self, repo_sqlite):
        """Un estado reutilizable deja de proteger de nada."""
        with como_usuario("usr-ana"):
            estado = repo_sqlite.crear_estado("github")

        repo_sqlite.consumir_estado(estado, "github")

        assert repo_sqlite.consumir_estado(estado, "github") is None

    def test_uno_inventado_no_vale(self, repo_sqlite):
        assert repo_sqlite.consumir_estado("me-lo-invento", "github") is None

    def test_no_sirve_para_otro_servicio(self, repo_sqlite):
        with como_usuario("usr-ana"):
            estado = repo_sqlite.crear_estado("github")

        assert repo_sqlite.consumir_estado(estado, "otro") is None

    def test_uno_caducado_no_vale(self, repo_sqlite, monkeypatch):
        with como_usuario("usr-ana"):
            estado = repo_sqlite.crear_estado("github")

        import src.integraciones.repositorio as modulo

        monkeypatch.setattr(modulo, "_ahora", lambda: 9_999_999_999.0)

        assert repo_sqlite.consumir_estado(estado, "github") is None

    def test_dice_de_quien_era_aunque_pregunte_otro(self, repo_sqlite):
        """Es el punto entero: la vuelta de OAuth puede llegar sin la cookie de
        quien empezó, y el estado es lo que dice a nombre de quién guardar."""
        with como_usuario("usr-ana"):
            estado = repo_sqlite.crear_estado("github")

        with como_usuario("usr-bea"):
            assert repo_sqlite.consumir_estado(estado, "github") == "usr-ana"


# ── La API ──────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_SECRET_KEY", CLAVE)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()
    dependencies.reset_container()


def _entrar(api) -> str:
    respuesta = api.post("/auth/registro", json=REGISTRO)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["csrf"]


class TestHaceFaltaCuenta:
    """Una integración se guarda a nombre de alguien. Sin sesión no hay a quién
    atribuirla, y en un Morgan compartido la conectaría uno y la usarían todos."""

    def test_listar_sin_sesion_da_401(self, api):
        assert api.get("/integraciones").status_code == 401

    def test_conectar_sin_sesion_da_401(self, api):
        assert api.post("/integraciones/github/conectar").status_code == 401


class TestSiNoPuedeFuncionarSeDice:
    """Un botón que siempre da error hace perder el tiempo y parece una avería
    de Morgan cuando es configuración que falta."""

    def test_sin_credenciales_el_servicio_sale_no_disponible(self, api):
        _entrar(api)

        github = api.get("/integraciones").json()["servicios"][0]

        assert github["disponible"] is False
        assert "GITHUB_CLIENT_ID" in github["motivo_no_disponible"]

    def test_y_conectar_responde_503_con_el_motivo(self, api):
        csrf = _entrar(api)

        respuesta = api.post(
            "/integraciones/github/conectar", headers={CABECERA_CSRF: csrf},
        )

        assert respuesta.status_code == 503
        assert respuesta.json()["error"]["code"] == "NO_CONFIGURADO"

    def test_sin_clave_de_cifrado_tampoco(self, api, monkeypatch):
        monkeypatch.delenv("MORGAN_SECRET_KEY", raising=False)
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id-de-prueba")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-prueba")
        _entrar(api)

        github = api.get("/integraciones").json()["servicios"][0]

        assert github["disponible"] is False
        assert "MORGAN_SECRET_KEY" in github["motivo_no_disponible"]

    def test_se_nombran_TODAS_las_que_faltan_no_la_primera(self, api, monkeypatch):
        """Informar de una en una obliga a arreglar, redesplegar, volver a mirar
        y descubrir que falta otra. Con tres variables son tres vueltas de un
        ciclo que en Render tarda minutos.

        Se encontró en producción: la respuesta solo nombraba MORGAN_SECRET_KEY
        aunque hubiera más pendientes.
        """
        monkeypatch.delenv("MORGAN_SECRET_KEY", raising=False)
        _entrar(api)

        motivo = api.get("/integraciones").json()["servicios"][0]["motivo_no_disponible"]

        assert "GITHUB_CLIENT_ID" in motivo
        assert "GITHUB_CLIENT_SECRET" in motivo
        assert "MORGAN_SECRET_KEY" in motivo

    def test_si_solo_falta_una_solo_se_nombra_esa(self, api, monkeypatch):
        """Lo contrario también importa: una lista con todo siempre sería tan
        inútil como una con solo lo primero."""
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto")
        monkeypatch.delenv("MORGAN_SECRET_KEY", raising=False)
        _entrar(api)

        motivo = api.get("/integraciones").json()["servicios"][0]["motivo_no_disponible"]

        assert "MORGAN_SECRET_KEY" in motivo
        assert "GITHUB_CLIENT_ID" not in motivo

    def test_un_servicio_desconocido_da_404(self, api):
        csrf = _entrar(api)

        respuesta = api.post(
            "/integraciones/inventado/conectar", headers={CABECERA_CSRF: csrf},
        )

        assert respuesta.status_code == 404


class TestConCredencialesSeOfrece:
    @pytest.fixture
    def api_con_github(self, api, monkeypatch):
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id-de-prueba")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-prueba")
        monkeypatch.setenv("MORGAN_API_URL", "https://morgan-api.ejemplo.co")
        return api

    def test_sale_disponible(self, api_con_github):
        _entrar(api_con_github)

        github = api_con_github.get("/integraciones").json()["servicios"][0]

        assert github["disponible"] is True
        assert github["conectado"] is False

    def test_conectar_devuelve_la_url_de_github(self, api_con_github):
        csrf = _entrar(api_con_github)

        respuesta = api_con_github.post(
            "/integraciones/github/conectar", headers={CABECERA_CSRF: csrf},
        )

        url = respuesta.json()["url"]
        assert url.startswith("https://github.com/login/oauth/authorize")
        assert "state=" in url
        assert "client_id=id-de-prueba" in url

    def test_la_url_de_retorno_apunta_al_backend(self, api_con_github):
        """A Render, no a Vercel: el secreto vive en el backend y es allí donde
        se canjea el código. Poner la de la web da `redirect_uri_mismatch`."""
        from urllib.parse import parse_qs, urlparse

        csrf = _entrar(api_con_github)
        url = api_con_github.post(
            "/integraciones/github/conectar", headers={CABECERA_CSRF: csrf},
        ).json()["url"]

        retorno = parse_qs(urlparse(url).query)["redirect_uri"][0]

        assert retorno == "https://morgan-api.ejemplo.co/integraciones/github/callback"

    def test_los_permisos_que_se_piden_son_los_del_catalogo(self, api_con_github):
        """Mínimos y explícitos. Si alguien los amplía, esto lo hace visible."""
        from urllib.parse import parse_qs, urlparse

        csrf = _entrar(api_con_github)
        url = api_con_github.post(
            "/integraciones/github/conectar", headers={CABECERA_CSRF: csrf},
        ).json()["url"]

        scopes = parse_qs(urlparse(url).query)["scope"][0].split()

        assert scopes == ["read:user", "repo"]


class TestElCallbackNoExigeSesion:
    """Llega como navegación del navegador desde GitHub. Exigir sesión rompía el
    flujo justo al volver, después de autorizar: el peor momento para fallar."""

    def test_la_ruta_esta_abierta(self, api):
        from src.api.identidad_middleware import _es_publica

        assert _es_publica("/integraciones/github/callback")

    def test_pero_solo_esa(self, api):
        from src.api.identidad_middleware import _es_publica

        assert not _es_publica("/integraciones")
        assert not _es_publica("/integraciones/github/conectar")
        assert not _es_publica("/sessions/callback")

    def test_un_estado_invalido_devuelve_a_la_web_sin_conectar(self, api, monkeypatch):
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")

        respuesta = api.get(
            "/integraciones/github/callback?code=abc&state=me-lo-invento",
            follow_redirects=False,
        )

        assert respuesta.status_code == 302
        assert "resultado=estado_invalido" in respuesta.headers["location"]

    def test_cancelar_en_github_no_es_un_error(self, api, monkeypatch):
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")

        respuesta = api.get(
            "/integraciones/github/callback?error=access_denied",
            follow_redirects=False,
        )

        assert "resultado=cancelado" in respuesta.headers["location"]


class TestElTokenNuncaSaleDelBackend:
    """La interfaz no lo necesita: quien habla con GitHub es el backend.
    Devolverlo lo pondría al alcance de cualquier script inyectado en la página,
    y el daño no sería de Morgan sino de los repositorios de esa persona."""

    def test_no_aparece_en_la_lista(self, api, monkeypatch):
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto")
        _entrar(api)

        from src.api.dependencies import get_container
        from src.integraciones.repositorio import repositorio_de_integraciones

        repo = repositorio_de_integraciones(get_container().repositories)
        from src.identidad.repositorio import repositorio_de_cuentas

        fila = repositorio_de_cuentas(get_container().repositories).buscar("ana")
        with como_usuario(fila["id"]):
            repo.guardar("github", "gho_secretodeverdad", cuenta="ana")

        cuerpo = api.get("/integraciones").text

        assert "gho_secretodeverdad" not in cuerpo
        assert "token" not in cuerpo.lower() or "token_cifrado" not in cuerpo

    def test_la_integracion_publicable_no_lo_lleva(self):
        from src.integraciones.modelos import Integracion

        publicable = Integracion(
            user_id="usr-ana", servicio="github", cuenta="ana",
        ).to_dict()

        assert "token" not in publicable
        assert set(publicable) == {
            "servicio", "conectado", "cuenta", "scopes",
            "creado_en", "actualizado_en", "error",
        }


# ── La vuelta de GitHub, de principio a fin ─────────────────────────────────
#
# Lo de arriba comprueba que un `state` invalido no conecta. Lo que faltaba es
# el camino que SI conecta, que es donde esta la propiedad que importa: la
# integracion queda a nombre de quien pidio conectar, y ese dato sale del
# `state`, no de la cookie de esta peticion.


class TestElCallbackQueSiConecta:
    @pytest.fixture
    def api_lista(self, api, monkeypatch):
        """Con la aplicación registrada, y GitHub simulado."""
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id-de-prueba")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-prueba")
        monkeypatch.setenv("MORGAN_API_URL", "https://morgan-api.ejemplo.co")
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")

        from src.integraciones import github as gh

        # Se concede MENOS de lo que se pide, a proposito: el catalogo pide
        # ('read:user', 'repo') y GitHub deja recortarlo en la propia pantalla
        # de autorizacion. Con un doble que devuelve los mismos permisos que se
        # piden, guardar los pedidos en vez de los concedidos es indetectable,
        # y la prueba pasaria con el codigo roto.
        monkeypatch.setattr(
            gh, "canjear_codigo",
            lambda codigo, callback: ("gho_token_de_ana", ("read:user",)),
        )
        monkeypatch.setattr(
            gh, "cuenta_de",
            lambda token: {"login": "wv-Andy", "nombre": "Andy", "avatar": "https://a/1"},
        )
        return api

    @staticmethod
    def _estado_de(api, usuario: str = "ana") -> str:
        """Emite un `state` real pidiendo conectar, como haría la web."""
        from src.api.dependencies import get_container
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.integraciones.repositorio import repositorio_de_integraciones

        repos = get_container().repositories
        fila = repositorio_de_cuentas(repos).buscar(usuario)
        with como_usuario(fila["id"]):
            return repositorio_de_integraciones(repos).crear_estado("github")

    def test_se_conecta_y_se_vuelve_a_la_web(self, api_lista):
        _entrar(api_lista)
        estado = self._estado_de(api_lista)

        respuesta = api_lista.get(
            f"/integraciones/github/callback?code=abc&state={estado}",
            follow_redirects=False,
        )

        assert respuesta.status_code == 302
        assert "resultado=conectado" in respuesta.headers["location"]
        assert respuesta.headers["location"].startswith("https://morgan.ejemplo.co/")

    def test_queda_conectado_con_el_nombre_de_la_cuenta(self, api_lista):
        """Sin el nombre, la interfaz diría «GitHub conectado» sin decir a cuál,
        y quien tenga dos no sabría sobre qué va a actuar Morgan.
        """
        _entrar(api_lista)
        estado = self._estado_de(api_lista)
        api_lista.get(f"/integraciones/github/callback?code=abc&state={estado}",
                      follow_redirects=False)

        github = api_lista.get("/integraciones").json()["servicios"][0]

        assert github["conectado"] is True
        assert github["cuenta"] == "wv-Andy"

    def test_se_guardan_los_permisos_concedidos_y_no_los_pedidos(self, api_lista):
        """En GitHub se pueden recortar en la propia pantalla de autorizacion.

        Guardar los pedidos haria que Morgan creyera tener acceso de escritura
        a los repositorios y lo intentara: el fallo llegaria mucho despues, en
        forma de 403, y en un sitio que no apunta a esta causa.
        """
        _entrar(api_lista)
        estado = self._estado_de(api_lista)
        api_lista.get(f"/integraciones/github/callback?code=abc&state={estado}",
                      follow_redirects=False)

        github = api_lista.get("/integraciones").json()["servicios"][0]

        assert set(github["scopes"]) == {"read:user"}, (
            "Se han guardado los permisos pedidos en lugar de los concedidos"
        )

    def test_si_github_no_dice_nada_se_asume_lo_pedido(self, api_lista, monkeypatch):
        """Es lo unico que se puede suponer, y es el lado utilizable del error:
        con la tupla vacia la interfaz diria que no se concedio ningun permiso
        cuando en realidad se concedieron todos.
        """
        from src.integraciones import github as gh

        monkeypatch.setattr(gh, "canjear_codigo", lambda c, cb: ("gho_x", ()))

        _entrar(api_lista)
        estado = self._estado_de(api_lista)
        api_lista.get(f"/integraciones/github/callback?code=abc&state={estado}",
                      follow_redirects=False)

        github = api_lista.get("/integraciones").json()["servicios"][0]

        assert set(github["scopes"]) == {"read:user", "repo"}

    def test_el_estado_ya_no_sirve_una_segunda_vez(self, api_lista):
        """Recargar la pestaña de vuelta es lo más normal del mundo, y no debe
        volver a canjear un código que GitHub ya invalidó.
        """
        _entrar(api_lista)
        estado = self._estado_de(api_lista)
        api_lista.get(f"/integraciones/github/callback?code=abc&state={estado}",
                      follow_redirects=False)

        segunda = api_lista.get(
            f"/integraciones/github/callback?code=abc&state={estado}",
            follow_redirects=False,
        )

        assert "resultado=estado_invalido" in segunda.headers["location"]

    def test_si_github_falla_al_canjear_se_vuelve_diciendolo(self, api_lista, monkeypatch):
        """Y se vuelve a la web igualmente. Dejar a la persona mirando un JSON
        en el dominio de la API es abandonarla a mitad de camino.
        """
        from src.integraciones import github as gh

        def revienta(codigo, callback):
            raise gh.ErrorDeGitHub("The code passed is incorrect or expired.")

        monkeypatch.setattr(gh, "canjear_codigo", revienta)

        _entrar(api_lista)
        estado = self._estado_de(api_lista)

        respuesta = api_lista.get(
            f"/integraciones/github/callback?code=caducado&state={estado}",
            follow_redirects=False,
        )

        assert "resultado=fallo" in respuesta.headers["location"]
        assert api_lista.get("/integraciones").json()["servicios"][0]["conectado"] is False

    def test_sin_code_no_se_intenta_nada(self, api_lista):
        respuesta = api_lista.get(
            "/integraciones/github/callback?state=algo", follow_redirects=False
        )

        assert "resultado=invalido" in respuesta.headers["location"]

    def test_el_token_no_llega_a_la_auditoria(self, api_lista):
        """Un registro de auditoría se comparte para investigar. Ahí no pinta
        nada una credencial, ni enmascarada.
        """
        _entrar(api_lista)
        estado = self._estado_de(api_lista)
        api_lista.get(f"/integraciones/github/callback?code=abc&state={estado}",
                      follow_redirects=False)

        from src.api.dependencies import get_container

        registro = get_container().audit_logger
        texto = json.dumps(
            [e if isinstance(e, dict) else str(e)
             for e in registro.get_recent_logs(20)],
            default=str,
        )

        assert "gho_token_de_ana" not in texto
        assert "conectar_integracion" in texto


class TestDeQuienEsLaAutorizacionLoDiceElEstado:
    """La propiedad que sostiene todo el flujo.

    La vuelta de OAuth es una navegación del navegador. Fiarse de la cookie
    sería preguntarle al mismo canal que se intenta verificar: una web ajena
    podría completar el flujo con SU código y dejar su cuenta de GitHub colgada
    de la sesión de otra persona. Morgan actuaría después sobre repositorios
    ajenos creyendo que son los tuyos.

    El `state` se emitió con una sesión válida y se guardó con su `user_id`. Es
    lo único que ata la vuelta a una persona.
    """

    @pytest.fixture
    def api_dos_personas(self, api, monkeypatch):
        """Bea se registra primero y Ana después, así que **la cookie viva es la
        de Ana**.

        El orden no es un detalle. La primera versión de esta prueba registraba
        a Ana primero y luego intentaba `/auth/entrar` como Ana; esa entrada
        falla —la cuenta aún no está verificada— y la cookie se quedaba siendo
        la de Bea. Con la cookie y el `state` apuntando a la misma persona, la
        prueba pasaba también con el callback fiándose de la cookie: no
        comprobaba nada. Se descubrió rompiendo el código a propósito, que es la
        única forma de descubrirlo.

        Registrarse deja la sesión abierta, así que basta con el orden y no hace
        falta ninguna entrada.
        """
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id-de-prueba")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-prueba")
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")

        from src.integraciones import github as gh

        monkeypatch.setattr(gh, "canjear_codigo",
                            lambda c, cb: ("gho_de_bea", ("repo",)))
        monkeypatch.setattr(gh, "cuenta_de",
                            lambda t: {"login": "bea-en-github", "nombre": "Bea",
                                       "avatar": None})

        respuesta = api.post("/auth/registro", json={
            "username": "bea", "email": "bea@ejemplo.co",
            "password": "otra-contrasena-larga",
        })
        assert respuesta.status_code == 200, respuesta.text
        api.post("/auth/salir", headers={CABECERA_CSRF: respuesta.json()["csrf"]})

        _entrar(api)  # Ana, y su sesión es la que queda en la cookie
        return api

    def test_se_conecta_a_quien_emitio_el_estado_y_no_a_quien_vuelve(
        self, api_dos_personas
    ):
        """Bea pide conectar. La vuelta llega con la cookie de Ana —porque otra
        pestaña se dejó su sesión abierta, o porque alguien la provocó—. La
        integración tiene que quedar a nombre de **Bea**.
        """
        from src.api.dependencies import get_container
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.integraciones.repositorio import repositorio_de_integraciones

        repos = get_container().repositories
        cuentas = repositorio_de_cuentas(repos)
        bea = cuentas.buscar("bea")["id"]
        ana = cuentas.buscar("ana")["id"]
        assert bea != ana

        # Sin esto la prueba puede volverse hueca sin que nadie lo note: si la
        # cookie y el `state` apuntan a la misma persona, no hay nada que
        # distinguir y pasa igual con el código roto. Ya ocurrió una vez.
        quien_vuelve = api_dos_personas.get("/auth/yo").json()
        assert quien_vuelve["usuario"] and quien_vuelve["usuario"]["id"] == ana, (
            "La vuelta no llega con la sesión de Ana, así que esta prueba no "
            "está comprobando lo que dice comprobar"
        )

        integraciones = repositorio_de_integraciones(repos)
        with como_usuario(bea):
            estado = integraciones.crear_estado("github")

        api_dos_personas.get(
            f"/integraciones/github/callback?code=abc&state={estado}",
            follow_redirects=False,
        )

        with como_usuario(bea):
            assert [i.servicio for i in integraciones.listar()] == ["github"]
        with como_usuario(ana):
            assert integraciones.listar() == [], (
                "La cuenta de GitHub de Bea ha quedado conectada a la sesión de Ana"
            )

    def test_un_estado_inventado_no_vale_aunque_haya_sesion(self, api_dos_personas):
        respuesta = api_dos_personas.get(
            "/integraciones/github/callback?code=abc&state=me-lo-invento",
            follow_redirects=False,
        )

        assert "resultado=estado_invalido" in respuesta.headers["location"]


class TestDesconectar:
    @pytest.fixture
    def api_conectada(self, api, monkeypatch):
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id-de-prueba")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-prueba")

        from src.integraciones import github as gh

        self.revocados: list[str] = []

        def revocar(token):
            self.revocados.append(token)
            return True

        monkeypatch.setattr(gh, "revocar", revocar)

        csrf = _entrar(api)

        from src.api.dependencies import get_container
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.integraciones.repositorio import repositorio_de_integraciones

        repos = get_container().repositories
        fila = repositorio_de_cuentas(repos).buscar("ana")
        with como_usuario(fila["id"]):
            repositorio_de_integraciones(repos).guardar(
                "github", "gho_token_guardado", cuenta="wv-Andy",
            )
        return api, csrf

    def test_se_desconecta(self, api_conectada):
        api, csrf = api_conectada

        respuesta = api.delete("/integraciones/github", headers={CABECERA_CSRF: csrf})

        assert respuesta.status_code == 200
        assert api.get("/integraciones").json()["servicios"][0]["conectado"] is False

    def test_se_le_pide_a_github_que_invalide_el_token(self, api_conectada):
        """Borrar la fila local no basta: sin avisar a GitHub, una copia
        filtrada del token seguiría abriendo los repositorios, y la persona
        creería haber revocado el acceso.
        """
        api, csrf = api_conectada

        api.delete("/integraciones/github", headers={CABECERA_CSRF: csrf})

        assert self.revocados == ["gho_token_guardado"]

    def test_si_github_no_responde_se_desconecta_igual(self, api_conectada, monkeypatch):
        """Quedarse conectado porque la revocación remota falló es el peor
        resultado: la persona ha dicho que no quiere seguir conectada.
        """
        from src.integraciones import github as gh

        monkeypatch.setattr(gh, "revocar", lambda t: False)
        api, csrf = api_conectada

        respuesta = api.delete("/integraciones/github", headers={CABECERA_CSRF: csrf})

        assert respuesta.status_code == 200
        assert api.get("/integraciones").json()["servicios"][0]["conectado"] is False

    def test_desconectar_sin_sesion_da_401(self, api):
        assert api.delete("/integraciones/github").status_code == 401

    def test_sin_csrf_no_se_desconecta(self, api_conectada):
        """Es una operación que cambia estado por un método que el navegador
        puede provocar desde otro sitio.
        """
        api, _ = api_conectada

        assert api.delete("/integraciones/github").status_code in (401, 403)


class TestElMorganLocalNoSeQuedaEnRojo:
    """Encontrado rompiendo el código a propósito, no leyéndolo.

    `_exigir_cuenta()` en `desconectar` parecía redundante: quitarlo no rompía
    ninguna prueba, porque con cuentas exigidas el middleware ya devuelve 401
    antes de llegar. Al buscar dónde SÍ importa apareció el caso real: el Morgan
    local, `MORGAN_REQUIRE_AUTH=false`, donde el middleware deja pasar con el
    usuario implícito y esa línea es la única defensa.

    Y mirando ahí apareció otra cosa: `listar` también lo exigía, así que en el
    Morgan local el panel de servicios **devolvía 401 y se pintaba entero como
    un error en rojo**. Contradecía la norma que esa misma vista tiene escrita:
    lo que no puede funcionar se enseña apagado y con su motivo, porque un error
    parece una avería de Morgan cuando es otra cosa.

    Conectar y desconectar siguen exigiendo cuenta. Lo que cambió es que
    preguntar qué hay ya no lo exige.
    """

    @pytest.fixture
    def api_local(self, tmp_path, monkeypatch):
        """Morgan local: sin cuentas, con las credenciales de GitHub puestas."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "false")
        monkeypatch.setenv("MORGAN_SECRET_KEY", CLAVE)
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id-de-prueba")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-prueba")
        reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        yield TestClient(create_app())
        reset_settings()
        dependencies.reset_container()

    def test_el_panel_se_puede_listar(self, api_local):
        """Sin esto la vista no tiene con qué explicarse."""
        assert api_local.get("/integraciones").status_code == 200

    def test_github_sale_no_disponible(self, api_local):
        github = api_local.get("/integraciones").json()["servicios"][0]

        assert github["disponible"] is False
        assert github["conectado"] is False

    def test_y_se_dice_que_es_por_la_cuenta_y_no_por_las_variables(self, api_local):
        """Las credenciales del servidor están puestas. Decir que faltan
        mandaría a quien administra a arreglar lo que no toca.
        """
        github = api_local.get("/integraciones").json()["servicios"][0]

        assert "cuenta de Morgan" in github["motivo_no_disponible"]
        assert "GITHUB_CLIENT_ID" not in github["motivo_no_disponible"]

    def test_conectar_sigue_exigiendo_cuenta(self, api_local):
        """Lo que se levantó es la pregunta, no el permiso. Una integración
        cuelga de una cuenta, y con el usuario implícito la conectaría una
        persona y la usarían todas.
        """
        respuesta = api_local.post("/integraciones/github/conectar")

        assert respuesta.status_code == 401
        assert respuesta.json()["error"]["code"] == "SIN_SESION"

    def test_desconectar_tambien(self, api_local):
        """Aquí es donde `_exigir_cuenta` es la única defensa: el middleware ya
        ha dejado pasar la petición con el usuario implícito.
        """
        assert api_local.delete("/integraciones/github").status_code == 401

    def test_una_fila_del_usuario_implicito_no_se_ensena_como_conectada(
        self, api_local
    ):
        """Aunque exista. Es lo que deja la fila de una versión anterior, o un
        traspaso de datos hecho a mano.

        La razón no es el aislamiento —esa fila es del usuario implícito, que es
        quien pregunta— sino que **desconectar sí exige cuenta**. Enseñarla como
        conectada sería ofrecer un estado del que no se puede salir: un botón de
        desconectar que devuelve 401, o ninguno, y un servicio que dice estar
        conectado para siempre.

        Este camino está cerrado hoy —`conectar` no deja llegar hasta aquí— así
        que es una segunda línea, no la primera. Se comprueba porque una segunda
        línea sin comprobar no es una línea.
        """
        from src.api.dependencies import get_container
        from src.identidad import USUARIO_LOCAL
        from src.integraciones.repositorio import repositorio_de_integraciones

        repo = repositorio_de_integraciones(get_container().repositories)
        with como_usuario(USUARIO_LOCAL):
            repo.guardar("github", "gho_de_una_version_vieja", cuenta="alguien")

        github = api_local.get("/integraciones").json()["servicios"][0]

        assert github["conectado"] is False, (
            "Se enseña como conectado un servicio que este Morgan no permite "
            "desconectar"
        )
        assert "gho_de_una_version_vieja" not in api_local.get("/integraciones").text
