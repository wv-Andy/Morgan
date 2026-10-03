"""
Conectar y desconectar una cuenta de GitHub: el intercambio OAuth.

**Por qué existe este archivo.** Era la mitad sin ejecutar de
`src/integraciones/github.py`, y es la mitad donde viaja el secreto de la
aplicación y donde se decide si un token deja de valer de verdad. Las consultas
—repos, issues— están en `test_github_consultas.py`; las rutas, en
`test_github_rutas.py`.

Lo de siempre: GitHub se simula. Aquí se comprueba qué se le manda y cómo se
interpreta lo que contesta, no que GitHub esté en pie.
"""

import httpx
import pytest

from src.integraciones import github as gh


@pytest.fixture
def app_registrada(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.cliente")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secreto-de-la-app")


def respuesta(cuerpo, status=200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=cuerpo,
        request=httpx.Request("POST", "https://github.com/login/oauth/access_token"),
    )


class TestSiEsteDespliegueSiquieraPuedeConectar:
    """Sin las dos variables no hay nada que ofrecer, y la interfaz enseña qué
    falta en lugar de un botón que daría un error de GitHub.
    """

    def test_con_las_dos_esta_configurado(self, app_registrada):
        assert gh.configurado() is True

    @pytest.mark.parametrize("falta", ["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"])
    def test_con_una_sola_no_basta(self, app_registrada, monkeypatch, falta):
        monkeypatch.setenv(falta, "")

        assert gh.configurado() is False

    def test_solo_espacios_cuenta_como_que_falta(self, app_registrada, monkeypatch):
        """Un panel de despliegue con la variable creada y vacía es un caso
        real, y `bool(" ")` es verdadero.
        """
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "   ")

        with pytest.raises(gh.GitHubNoConfigurado):
            gh._credenciales()

    def test_el_aviso_dice_qué_variables_son(self, monkeypatch):
        """Quien administra el despliegue necesita los nombres exactos."""
        monkeypatch.setenv("GITHUB_CLIENT_ID", "")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "")

        with pytest.raises(gh.GitHubNoConfigurado) as fallo:
            gh._credenciales()

        assert "GITHUB_CLIENT_ID" in str(fallo.value)
        assert "GITHUB_CLIENT_SECRET" in str(fallo.value)


class TestADóndeSeMandaElNavegador:
    CALLBACK = "https://morgan.onrender.com/integraciones/github/callback"

    def url(self, estado="est-123", scopes=("repo", "read:user")):
        from urllib.parse import parse_qs, urlparse

        cruda = gh.url_de_autorizacion(estado, scopes, self.CALLBACK)
        partes = urlparse(cruda)
        return cruda, {k: v[0] for k, v in parse_qs(partes.query).items()}

    def test_va_a_github_y_no_a_ningún_otro_sitio(self, app_registrada):
        cruda, _ = self.url()

        assert cruda.startswith("https://github.com/login/oauth/authorize?")

    def test_lleva_el_estado_que_se_emitió(self, app_registrada):
        """Es lo único que ata la vuelta a la persona que pidió conectar. Sin
        él, otra web podría completar el flujo contra la sesión de alguien.
        """
        _, params = self.url(estado="est-unico")

        assert params["state"] == "est-unico"

    def test_los_permisos_van_separados_por_espacios(self, app_registrada):
        """Es el formato de GitHub. Con comas pide permisos con nombres raros
        que no existen, y concede menos de lo necesario sin decir nada.
        """
        _, params = self.url(scopes=("repo", "read:user", "read:org"))

        assert params["scope"] == "repo read:user read:org"

    def test_el_secreto_no_viaja_en_esta_url(self, app_registrada):
        """Esta URL la abre el navegador: es pública. El secreto solo sale en el
        canje, que ocurre en el backend.
        """
        cruda, _ = self.url()

        assert "secreto-de-la-app" not in cruda

    def test_no_se_ofrece_crear_una_cuenta_de_github(self, app_registrada):
        """Quien llega aquí viene a conectar una que ya tiene, y un desvío a un
        registro en mitad del flujo se lee como que algo ha salido mal.
        """
        _, params = self.url()

        assert params["allow_signup"] == "false"

    def test_sin_la_app_registrada_no_se_construye(self, monkeypatch):
        monkeypatch.setenv("GITHUB_CLIENT_ID", "")

        with pytest.raises(gh.GitHubNoConfigurado):
            gh.url_de_autorizacion("est", ("repo",), self.CALLBACK)


class TestElCanjeDelCódigo:
    CALLBACK = "https://morgan.onrender.com/integraciones/github/callback"

    def test_devuelve_el_token_y_los_permisos_concedidos(self, app_registrada, monkeypatch):
        """Los concedidos, no los pedidos: en GitHub se pueden recortar, y
        guardar los pedidos haría que Morgan intentara cosas que no puede.
        """
        monkeypatch.setattr(httpx, "post", lambda *a, **k: respuesta({
            "access_token": "gho_token", "scope": "repo,read:user",
            "token_type": "bearer",
        }))

        token, scopes = gh.canjear_codigo("codigo-de-un-uso", self.CALLBACK)

        assert token == "gho_token"
        assert scopes == ("repo", "read:user")

    def test_el_secreto_va_en_el_cuerpo_y_no_en_la_url(self, app_registrada, monkeypatch):
        """En la URL acabaría en los registros de acceso de cualquier
        intermediario. En el cuerpo de un POST sobre TLS, no.
        """
        visto = {}

        def espia(url, **kwargs):
            visto["url"] = url
            visto["data"] = kwargs.get("data") or {}
            return respuesta({"access_token": "gho_x", "scope": "repo"})

        monkeypatch.setattr(httpx, "post", espia)
        gh.canjear_codigo("codigo", self.CALLBACK)

        assert "secreto-de-la-app" not in visto["url"]
        assert visto["data"]["client_secret"] == "secreto-de-la-app"
        assert visto["data"]["code"] == "codigo"

    def test_se_pide_json_y_no_el_formato_por_defecto(self, app_registrada, monkeypatch):
        """Sin esa cabecera GitHub contesta `application/x-www-form-urlencoded`
        y `respuesta.json()` revienta.
        """
        visto = {}

        def espia(url, **kwargs):
            visto.update(kwargs.get("headers") or {})
            return respuesta({"access_token": "gho_x", "scope": ""})

        monkeypatch.setattr(httpx, "post", espia)
        gh.canjear_codigo("codigo", self.CALLBACK)

        assert visto["Accept"] == "application/json"

    def test_sin_permisos_devuelve_una_tupla_vacía_y_no_una_con_nada_dentro(
        self, app_registrada, monkeypatch
    ):
        """`"".split(",")` da `['']`, y un permiso llamado cadena vacía se
        guardaría como si existiera.
        """
        monkeypatch.setattr(httpx, "post", lambda *a, **k: respuesta({
            "access_token": "gho_x", "scope": "",
        }))

        _, scopes = gh.canjear_codigo("codigo", self.CALLBACK)

        assert scopes == ()

    def test_un_código_caducado_se_explica_con_las_palabras_de_github(
        self, app_registrada, monkeypatch
    ):
        """GitHub contesta **200** con un error dentro. Mirar solo el código de
        estado daría por bueno el canje y guardaría `None` como token.

        Y su mensaje es útil: 'bad_verification_code' dice que el código caducó
        o ya se usó, que es lo que suele pasar al recargar la pestaña de vuelta.
        """
        monkeypatch.setattr(httpx, "post", lambda *a, **k: respuesta({
            "error": "bad_verification_code",
            "error_description": "The code passed is incorrect or expired.",
        }))

        with pytest.raises(gh.ErrorDeGitHub, match="incorrect or expired"):
            gh.canjear_codigo("codigo-ya-usado", self.CALLBACK)

    def test_un_error_sin_descripción_deja_al_menos_el_código(
        self, app_registrada, monkeypatch
    ):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: respuesta({
            "error": "redirect_uri_mismatch",
        }))

        with pytest.raises(gh.ErrorDeGitHub, match="redirect_uri_mismatch"):
            gh.canjear_codigo("codigo", self.CALLBACK)

    def test_una_respuesta_sin_token_no_pasa_por_buena(
        self, app_registrada, monkeypatch
    ):
        """Sin esta comprobación se guardaría `None` cifrado y el fallo
        aparecería mucho después, en la primera consulta.
        """
        monkeypatch.setattr(httpx, "post", lambda *a, **k: respuesta({"scope": "repo"}))

        with pytest.raises(gh.ErrorDeGitHub, match="ningún token"):
            gh.canjear_codigo("codigo", self.CALLBACK)

    def test_un_fallo_de_red_se_distingue_de_un_rechazo(self, app_registrada, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(
            httpx.ConnectError("sin red")))

        with pytest.raises(gh.ErrorDeGitHub, match="No se pudo contactar"):
            gh.canjear_codigo("codigo", self.CALLBACK)

    def test_un_500_de_github_no_se_confunde_con_un_código_malo(
        self, app_registrada, monkeypatch
    ):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: respuesta({}, status=500))

        with pytest.raises(gh.ErrorDeGitHub, match="500"):
            gh.canjear_codigo("codigo", self.CALLBACK)


class TestRevocarElToken:
    """Desconectar borra la fila local pase lo que pase, pero **avisar a GitHub
    es lo que hace que el token deje de valer de verdad**. Si solo se borrara
    aquí, una copia filtrada seguiría funcionando y la persona creería haber
    revocado el acceso.
    """

    @pytest.fixture
    def espia(self, monkeypatch):
        visto = {}

        def peticion(metodo, url, **kwargs):
            visto["metodo"] = metodo
            visto["url"] = url
            visto["auth"] = kwargs.get("auth")
            visto["json"] = kwargs.get("json")
            return httpx.Response(
                status_code=visto.get("status", 204),
                request=httpx.Request(metodo, url),
            )

        monkeypatch.setattr(httpx, "request", peticion)
        return visto

    def test_se_le_pide_a_github_que_lo_invalide(self, app_registrada, espia):
        assert gh.revocar("gho_token") is True
        assert espia["metodo"] == "DELETE"
        assert espia["json"] == {"access_token": "gho_token"}

    def test_la_petición_se_autentica_como_la_aplicación(self, app_registrada, espia):
        """No con el token que se quiere revocar: GitHub exige las credenciales
        de la aplicación para este endpoint, y con el token da 401.
        """
        gh.revocar("gho_token")

        assert espia["auth"] == ("Iv1.cliente", "secreto-de-la-app")
        assert "Iv1.cliente" in espia["url"]

    def test_que_ya_no_existiera_cuenta_como_revocado(self, app_registrada, espia):
        """404 significa que el token ya no valía. Para el caso es lo mismo, y
        tratarlo como fallo haría que la interfaz avisara de algo que está bien.
        """
        espia["status"] = 404

        assert gh.revocar("gho_token") is True

    def test_un_rechazo_de_verdad_se_devuelve_como_fallo(self, app_registrada, espia):
        espia["status"] = 401

        assert gh.revocar("gho_token") is False

    def test_sin_red_no_revienta_la_desconexión(self, app_registrada, monkeypatch):
        """Que la revocación remota falle NO debe impedir desconectar:
        quedarse conectado porque GitHub no respondió es el peor resultado.
        """
        monkeypatch.setattr(httpx, "request", lambda *a, **k: (_ for _ in ()).throw(
            httpx.ConnectError("sin red")))

        assert gh.revocar("gho_token") is False

    def test_sin_la_app_registrada_devuelve_falso_sin_lanzar(self, monkeypatch):
        """Pasa al quitar las variables con integraciones ya guardadas. Lanzar
        aquí dejaría a la persona sin poder desconectar.
        """
        monkeypatch.setenv("GITHUB_CLIENT_ID", "")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "")

        assert gh.revocar("gho_token") is False
