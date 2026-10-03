"""
Verificación del correo al registrarse.

**Verificar no es una puerta**, y esa es la decisión que gobierna todo lo demás:
la cuenta funciona desde el primer momento sin confirmar nada. Obligar a abrir
el correo antes de dejar probar Morgan es la forma más rápida de perder a
alguien, y la consecuencia real de no verificar es concreta y acotada: **no se
puede recuperar la contraseña**, porque el enlace va justo a esa dirección.

Lo que se comprueba aquí, por orden de lo que costaría equivocarse:

1. **Un token de verificación no sirve para restablecer la contraseña.** Los dos
   viajan por el mismo canal —un enlace en un correo— y valen lo mismo para
   quien los intercepte, pero conceden cosas muy distintas.
2. **Confirmar es público; reenviar exige sesión.** Una ruta abierta que dispara
   correos a partir de una dirección es una herramienta para molestar a
   terceros.
3. **Un fallo del correo no impide registrarse.** El envío es lo accesorio.
4. **Cambiar la contraseña no invalida la confirmación pendiente**, ni al revés.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.sesion_web import CABECERA_CSRF
from src.config import reset_settings
from src.identidad.cuentas import (
    TIPO_RESET,
    TIPO_VERIFICACION,
    ServicioDeCuentas,
    _hash_token,
)
from src.memory.db import Database

REGISTRO = {
    "username": "ana",
    "email": "ana@ejemplo.co",
    "password": "contrasena-larga",
}


@pytest.fixture
def servicio(tmp_path):
    return ServicioDeCuentas(Database(tmp_path / "cuentas.db"))


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()
    dependencies.reset_container()


class TestElFlujoBasico:
    def test_una_cuenta_nace_sin_verificar(self, servicio):
        usuario = servicio.registrar(**REGISTRO)

        assert usuario.email_verificado is False

    def test_el_enlace_la_verifica(self, servicio):
        usuario = servicio.registrar(**REGISTRO)
        token, correo = servicio.solicitar_verificacion(usuario.id)

        assert correo == REGISTRO["email"]
        assert servicio.verificar_email(token) is True
        assert servicio.obtener(usuario.id).email_verificado is True

    def test_el_enlace_solo_sirve_una_vez(self, servicio):
        usuario = servicio.registrar(**REGISTRO)
        token, _ = servicio.solicitar_verificacion(usuario.id)
        servicio.verificar_email(token)

        assert servicio.verificar_email(token) is False

    def test_un_token_inventado_no_vale(self, servicio):
        servicio.registrar(**REGISTRO)

        assert servicio.verificar_email("me-lo-invento") is False

    def test_uno_caducado_tampoco(self, servicio, monkeypatch):
        usuario = servicio.registrar(**REGISTRO)
        token, _ = servicio.solicitar_verificacion(usuario.id)

        import src.identidad.cuentas as modulo

        monkeypatch.setattr(modulo.time, "time", lambda: 9_999_999_999.0)

        assert servicio.verificar_email(token) is False

    def test_a_una_cuenta_ya_verificada_no_se_le_reemite(self, servicio):
        """Mandar otro enlace a quien ya lo usó solo sirve para confundir."""
        usuario = servicio.registrar(**REGISTRO)
        token, _ = servicio.solicitar_verificacion(usuario.id)
        servicio.verificar_email(token)

        assert servicio.solicitar_verificacion(usuario.id) is None

    def test_pedir_uno_nuevo_invalida_el_anterior(self, servicio):
        """El viejo puede estar en un correo de hace una semana."""
        usuario = servicio.registrar(**REGISTRO)
        viejo, _ = servicio.solicitar_verificacion(usuario.id)
        nuevo, _ = servicio.solicitar_verificacion(usuario.id)

        assert servicio.verificar_email(viejo) is False
        assert servicio.verificar_email(nuevo) is True


class TestLosDosTiposDeTokenNoSeMezclan:
    """Comparten tabla porque son la misma cosa —un secreto de un solo uso atado
    a un usuario— pero **no son intercambiables**: uno abre la cuenta y el otro
    solo confirma una dirección.

    Si el tipo no se filtrara, un enlace de «confirma tu correo» valdría para
    entrar en la cuenta. Y ese enlace se manda a una dirección que puede no ser
    de quien se registró — que es precisamente lo que la verificación existe
    para averiguar.
    """

    def test_uno_de_verificacion_no_restablece_la_contrasena(self, servicio):
        usuario = servicio.registrar(**REGISTRO)
        token, _ = servicio.solicitar_verificacion(usuario.id)

        assert servicio.repo.reset_valido(
            _hash_token(token),
            0.0,
            tipo=TIPO_RESET,
        ) is None

    def test_uno_de_recuperacion_no_verifica_el_correo(self, servicio):
        servicio.registrar(**REGISTRO)
        token = servicio.solicitar_recuperacion(REGISTRO["email"])

        assert servicio.verificar_email(token) is False

    def test_cambiar_la_contrasena_no_invalida_la_verificacion(self, servicio):
        """Son cosas distintas. Anular la confirmación pendiente al cambiar de
        contraseña obligaría a pedirla otra vez sin ningún motivo."""
        usuario = servicio.registrar(**REGISTRO)
        verificacion, _ = servicio.solicitar_verificacion(usuario.id)

        servicio.cambiar_password(usuario.id, REGISTRO["password"], "otra-contrasena-larga")

        assert servicio.verificar_email(verificacion) is True

    def test_ni_al_reves(self, servicio):
        usuario = servicio.registrar(**REGISTRO)
        recuperacion = servicio.solicitar_recuperacion(REGISTRO["email"])
        servicio.solicitar_verificacion(usuario.id)

        # Pedir la verificación no puede anular un enlace de recuperación vivo.
        assert servicio.repo.reset_valido(
            _hash_token(recuperacion),
            0.0,
            tipo=TIPO_RESET,
        ) is not None


class TestPorHttp:
    def test_confirmar_es_publico(self, cliente):
        """Quien abre el enlace puede estar en otro navegador, o sin sesión.
        Exigirla convertiría un clic en un correo en «primero inicia sesión»."""
        from src.api.identidad_middleware import _es_publica

        assert _es_publica("/auth/verificar")

    def test_pero_reenviar_no(self, cliente):
        """Una ruta abierta que dispara correos a partir de una dirección es una
        herramienta para molestar a terceros."""
        from src.api.identidad_middleware import _es_publica

        assert not _es_publica("/auth/verificar/reenviar")
        assert cliente.post("/auth/verificar/reenviar").status_code == 401

    def test_un_token_invalido_da_400_sin_decir_de_quien_era(self, cliente):
        respuesta = cliente.post("/auth/verificar", json={"token": "me-lo-invento"})

        assert respuesta.status_code == 400
        assert respuesta.json()["error"]["code"] == "TOKEN_INVALIDO"
        # No se filtra si el token existía, de quién era, ni si caducó.
        assert "ana" not in respuesta.text

    def test_yo_dice_si_el_correo_esta_confirmado(self, cliente):
        """La interfaz lo necesita para poder avisar."""
        cliente.post("/auth/registro", json=REGISTRO)

        usuario = cliente.get("/auth/yo").json()["usuario"]

        assert usuario["email_verificado"] is False

    def test_el_recorrido_completo(self, cliente):
        csrf = cliente.post("/auth/registro", json=REGISTRO).json()["csrf"]

        from src.api import dependencies
        from src.identidad.cuentas import ServicioDeCuentas as Servicio
        from src.identidad.repositorio import repositorio_de_cuentas

        repo = repositorio_de_cuentas(dependencies.get_container().repositories)
        servicio = Servicio(repo)
        fila = repo.buscar(REGISTRO["username"])

        # El del registro ya se emitió, así que se pide de nuevo el vigente.
        token, _ = servicio.solicitar_verificacion(fila["id"])
        respuesta = cliente.post("/auth/verificar", json={"token": token})

        assert respuesta.status_code == 200
        assert cliente.get("/auth/yo").json()["usuario"]["email_verificado"] is True
        assert csrf

    def test_reenviar_a_una_cuenta_ya_verificada_no_es_un_error(self, cliente):
        """Responde que no hacía falta, en lugar de fallar: el usuario no ha
        hecho nada mal."""
        csrf = cliente.post("/auth/registro", json=REGISTRO).json()["csrf"]

        from src.api import dependencies
        from src.identidad.cuentas import ServicioDeCuentas as Servicio
        from src.identidad.repositorio import repositorio_de_cuentas

        repo = repositorio_de_cuentas(dependencies.get_container().repositories)
        servicio = Servicio(repo)
        token, _ = servicio.solicitar_verificacion(repo.buscar(REGISTRO["username"])["id"])
        cliente.post("/auth/verificar", json={"token": token})

        respuesta = cliente.post(
            "/auth/verificar/reenviar", headers={CABECERA_CSRF: csrf}
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["enviado"] is False


class TestElCorreoNoBloqueaElRegistro:
    """El envío es lo accesorio. Un proveedor caído no puede impedir que alguien
    se cree una cuenta."""

    def test_registrarse_funciona_aunque_el_correo_falle(self, cliente, monkeypatch):
        import src.identidad.correo as correo

        def revienta(*_a, **_k):
            raise RuntimeError("el proveedor no responde")

        monkeypatch.setattr(correo, "enviar_verificacion", revienta)

        respuesta = cliente.post("/auth/registro", json=REGISTRO)

        assert respuesta.status_code == 200
        assert cliente.get("/auth/yo").json()["autenticado"] is True

    def test_y_el_token_queda_guardado_para_reintentarlo(self, cliente, monkeypatch):
        """Si no se guardara, un fallo pasajero dejaría a la persona sin poder
        confirmar hasta pedirlo otra vez a mano."""
        import src.identidad.correo as correo

        monkeypatch.setattr(correo, "enviar_verificacion", lambda *a, **k: False)
        cliente.post("/auth/registro", json=REGISTRO)

        from src.api import dependencies

        db = dependencies.get_container().repositories.db
        with db.connect() as conn:
            vivos = conn.execute(
                "SELECT COUNT(*) AS n FROM password_reset_tokens "
                "WHERE tipo = ? AND usado_en IS NULL",
                (TIPO_VERIFICACION,),
            ).fetchone()["n"]

        assert vivos == 1
