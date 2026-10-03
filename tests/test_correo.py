"""
Envío del correo de recuperación (identidad, V2.0 adelantada).

La pieza tiene dos transportes por un motivo concreto, descubierto en producción:
**Render bloquea la salida SMTP**. Las credenciales de Gmail eran correctas y el
envío moría con `Network is unreachable`. Estas pruebas fijan que se prefiere el
transporte que sí funciona allí, y que un fallo se pueda diagnosticar sin entrar
en el panel del servidor.
"""

import pytest

from src.identidad import correo


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    """Sin configuración heredada del `.env` del desarrollador, y sin lo anotado
    por otra prueba: las dos cosas son estado global."""
    for var in (
        "MORGAN_EMAIL_API", "MORGAN_EMAIL_API_KEY", "MORGAN_EMAIL_FROM",
        "MORGAN_SMTP_HOST", "MORGAN_SMTP_PORT", "MORGAN_SMTP_USER",
        "MORGAN_SMTP_PASSWORD", "MORGAN_SMTP_FROM",
    ):
        monkeypatch.delenv(var, raising=False)
    correo.olvidar_ultimo_intento()


class RespuestaFalsa:
    def __init__(self, status_code: int, text: str = "{}"):
        self.status_code = status_code
        self.text = text


def _api(monkeypatch, proveedor="brevo"):
    monkeypatch.setenv("MORGAN_EMAIL_API", proveedor)
    monkeypatch.setenv("MORGAN_EMAIL_API_KEY", "clave-secreta-de-prueba")
    monkeypatch.setenv("MORGAN_EMAIL_FROM", "morgan@ejemplo.co")


def _smtp(monkeypatch):
    monkeypatch.setenv("MORGAN_SMTP_HOST", "smtp.ejemplo.co")
    monkeypatch.setenv("MORGAN_SMTP_USER", "morgan@ejemplo.co")
    monkeypatch.setenv("MORGAN_SMTP_PASSWORD", "contrasena-secreta")


class TestQueTransporteSeUsa:
    def test_sin_nada_configurado_no_hay_transporte(self):
        assert correo.transporte() is None

    def test_con_smtp_usa_smtp(self, monkeypatch):
        _smtp(monkeypatch)

        assert correo.transporte() == "smtp"

    def test_con_api_usa_la_api(self, monkeypatch):
        _api(monkeypatch)

        assert correo.transporte() == "api:brevo"

    def test_la_api_manda_sobre_smtp(self, monkeypatch):
        """Es la que funciona en todas partes. Y tener las dos configuradas casi
        siempre significa que se dejó la vieja puesta al migrar."""
        _smtp(monkeypatch)
        _api(monkeypatch)

        assert correo.transporte() == "api:brevo"

    def test_un_proveedor_que_no_existe_no_cuenta(self, monkeypatch):
        """Mejor no tener transporte que creer que se tiene uno: así el estado lo
        dice, en vez de fallar en cada envío."""
        monkeypatch.setenv("MORGAN_EMAIL_API", "el-que-me-invente")
        monkeypatch.setenv("MORGAN_EMAIL_API_KEY", "loquesea")
        monkeypatch.setenv("MORGAN_EMAIL_FROM", "morgan@ejemplo.co")

        assert correo.transporte() is None

    def test_una_api_sin_remitente_no_cuenta(self, monkeypatch):
        monkeypatch.setenv("MORGAN_EMAIL_API", "brevo")
        monkeypatch.setenv("MORGAN_EMAIL_API_KEY", "loquesea")

        assert correo.transporte() is None


class TestElEnvioPorApi:
    def test_brevo_recibe_lo_que_espera(self, monkeypatch):
        capturado = {}

        def falso_post(url, headers=None, json=None, timeout=None):
            capturado.update(url=url, headers=headers, json=json)
            return RespuestaFalsa(201)

        import httpx

        monkeypatch.setattr(httpx, "post", falso_post)
        _api(monkeypatch, "brevo")

        assert correo.enviar_recuperacion("alguien@ejemplo.co", "el-token") is True
        assert "brevo.com" in capturado["url"]
        assert capturado["headers"]["api-key"] == "clave-secreta-de-prueba"
        assert capturado["json"]["to"] == [{"email": "alguien@ejemplo.co"}]
        assert "el-token" in capturado["json"]["textContent"]

    def test_resend_recibe_lo_que_espera(self, monkeypatch):
        capturado = {}

        def falso_post(url, headers=None, json=None, timeout=None):
            capturado.update(url=url, headers=headers, json=json)
            return RespuestaFalsa(200)

        import httpx

        monkeypatch.setattr(httpx, "post", falso_post)
        _api(monkeypatch, "resend")

        assert correo.enviar_recuperacion("alguien@ejemplo.co", "el-token") is True
        assert "resend.com" in capturado["url"]
        assert capturado["headers"]["authorization"].startswith("Bearer ")

    def test_el_enlace_lleva_el_token_y_apunta_a_la_web(self, monkeypatch):
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")

        enlace = correo.enlace_de_recuperacion("abc123")

        assert enlace == "https://morgan.ejemplo.co/restablecer?token=abc123"

    def test_un_rechazo_del_proveedor_se_anota_con_su_motivo(self, monkeypatch):
        """El cuerpo del error trae lo único que permite arreglarlo: remitente sin
        verificar, clave inválida, cuota agotada."""
        import httpx

        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **k: RespuestaFalsa(400, '{"message":"Sender not valid"}'),
        )
        _api(monkeypatch)

        with pytest.raises(RuntimeError):
            correo.enviar_recuperacion("alguien@ejemplo.co", "el-token")

        anotado = correo.ultimo_intento()
        assert anotado["ok"] is False
        assert "Sender not valid" in anotado["detalle"]


class TestElDiagnostico:
    def test_el_bloqueo_de_smtp_se_explica(self, monkeypatch):
        """El caso real: en Render las credenciales eran correctas y el envío
        moría con `Network is unreachable`. Sin esta pista, lo lógico es revisar
        la contraseña una y otra vez, que es justo lo que no falla."""
        _smtp(monkeypatch)

        def bloqueado(*a, **k):
            raise OSError("[Errno 101] Network is unreachable")

        monkeypatch.setattr(correo, "_enviar_por_smtp", bloqueado)

        with pytest.raises(OSError):
            correo.enviar_recuperacion("alguien@ejemplo.co", "el-token")

        detalle = correo.ultimo_intento()["detalle"]
        assert "bloquea la salida SMTP" in detalle
        assert "MORGAN_EMAIL_API" in detalle

    def test_lo_anotado_no_filtra_la_clave(self, monkeypatch):
        """`/status` es público, y los errores traen el texto del proveedor."""
        _api(monkeypatch)
        correo._anotar(
            False, "rechazado: clave-secreta-de-prueba no vale para morgan@ejemplo.co"
        )

        detalle = correo.ultimo_intento()["detalle"]

        assert "clave-secreta-de-prueba" not in detalle
        assert "morgan@ejemplo.co" not in detalle

    def test_un_envio_correcto_se_anota(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: RespuestaFalsa(201))
        _api(monkeypatch)
        correo.enviar_recuperacion("alguien@ejemplo.co", "el-token")

        assert correo.ultimo_intento()["ok"] is True


class TestSinConfigurar:
    def test_el_enlace_no_queda_en_el_log_ni_en_local(self, monkeypatch, caplog):
        """Se intentó dejarlo escrito «para poder probar en desarrollo» y no
        funcionaba: el filtro de secretos del log enmascara todo lo que parece
        `token=`, así que lo que quedaba era `?token=***`, inservible.

        La prueba fija que **no se intente burlar ese filtro**. Para probar el
        flujo sin proveedor, el token se saca de `solicitar_recuperacion()`.
        """
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        reset_settings()

        with caplog.at_level("DEBUG"):
            assert correo.enviar_recuperacion("alguien@ejemplo.co", "el-token") is False

        assert "el-token" not in caplog.text
        # Pero sí dice qué falta, que es lo accionable.
        assert "MORGAN_EMAIL_API" in caplog.text
        reset_settings()

    def test_en_la_nube_NO_deja_el_enlace_en_el_log(self, monkeypatch, caplog):
        """Allí el log lo lee quien tenga acceso al panel, y un enlace de
        recuperación en el log es un enlace de recuperación regalado."""
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_API_TOKEN", "x")
        reset_settings()

        with caplog.at_level("DEBUG"):
            assert correo.enviar_recuperacion("alguien@ejemplo.co", "el-token") is False

        assert "el-token" not in caplog.text
        reset_settings()


class TestElEnlaceApuntaADondeDebe:
    """El fallo que costó una tarde: el correo salía perfectamente y el enlace
    llevaba a `localhost:5173`, donde no hay nada escuchando. Todo parecía
    correcto, incluido el estado del correo."""

    def test_sin_MORGAN_WEB_URL_cae_a_desarrollo(self, monkeypatch):
        monkeypatch.delenv("MORGAN_WEB_URL", raising=False)

        assert "localhost" in correo.enlace_de_recuperacion("t")

    def test_el_estado_lo_denuncia_en_un_despliegue_con_cuentas(self, monkeypatch):
        """Que el correo salga no basta: hay que comprobar adónde lleva."""
        from src.api.health_checks import check_correo
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_API_TOKEN", "x")
        monkeypatch.delenv("MORGAN_WEB_URL", raising=False)
        _api(monkeypatch)
        reset_settings()

        estado, detalle = check_correo()

        assert estado.value == "unavailable"
        assert "MORGAN_WEB_URL" in detalle
        reset_settings()

    def test_con_la_direccion_publica_esta_bien(self, monkeypatch):
        from src.api.health_checks import check_correo
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_API_TOKEN", "x")
        monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan-ia.vercel.app")
        _api(monkeypatch)
        reset_settings()

        estado, _ = check_correo()

        assert estado.value == "available"
        reset_settings()

    def test_en_local_no_molesta(self, monkeypatch):
        """Ahí localhost es exactamente lo que se quiere."""
        from src.api.health_checks import check_correo
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "local")
        monkeypatch.delenv("MORGAN_REQUIRE_AUTH", raising=False)
        monkeypatch.delenv("MORGAN_WEB_URL", raising=False)
        reset_settings()

        estado, _ = check_correo()

        assert estado.value == "available"
        reset_settings()
