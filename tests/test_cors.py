"""
Pruebas de CORS.

Defecto que las origina: con el backend desplegado en Render y la interfaz en
Vercel, la web decía «API desconectada». El backend estaba perfectamente —`/health`
respondía 200 desde la línea de comandos— pero `MORGAN_CORS_ORIGINS` no incluía el
dominio de Vercel, así que el navegador bloqueaba cada llamada.

Un fallo de CORS no produce un error legible: la petición ni siquiera llega a
completarse desde el punto de vista del cliente. Por eso conviene que esté cubierto.
"""

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.api.app import create_app

VERCEL = "https://morgan-ia.vercel.app"
PREVIA = "https://morgan-bx9vhsy96-wv-andys-projects.vercel.app"
AJENO = "https://sitio-de-un-atacante.vercel.app"


@pytest.fixture
def cliente(monkeypatch):
    """Construye la app con los orígenes que se le indiquen."""

    def _construir(origenes: str | None = None, patron: str | None = None):
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_CORS_ORIGINS", origenes or "")
        monkeypatch.setenv("MORGAN_CORS_ORIGIN_REGEX", patron or "")
        config.reset_settings()
        return TestClient(create_app())

    yield _construir
    config.reset_settings()


def _preflight(cli: TestClient, origen: str):
    return cli.options(
        "/chat",
        headers={
            "Origin": origen,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


class TestOrigenesEnumerados:
    def test_un_origen_permitido_recibe_la_cabecera(self, cliente):
        respuesta = _preflight(cliente(origenes=VERCEL), VERCEL)

        assert respuesta.headers.get("access-control-allow-origin") == VERCEL

    def test_un_origen_no_listado_no_la_recibe(self, cliente):
        """Este era el caso real: el dominio de Vercel no estaba en la lista."""
        respuesta = _preflight(cliente(origenes=VERCEL), AJENO)

        assert "access-control-allow-origin" not in respuesta.headers

    def test_se_aceptan_varios_separados_por_comas(self, cliente):
        cli = cliente(origenes=f"{VERCEL},{PREVIA}")

        for origen in (VERCEL, PREVIA):
            assert _preflight(cli, origen).headers.get("access-control-allow-origin") == origen

    def test_los_espacios_sobrantes_no_estropean_la_lista(self, cliente):
        """Pegar el valor en un panel web suele arrastrar espacios."""
        cli = cliente(origenes=f"  {VERCEL} ,  {PREVIA}  ")

        assert _preflight(cli, VERCEL).headers.get("access-control-allow-origin") == VERCEL


class TestPatronParaLasVistasPrevias:
    """Los despliegues de vista previa de Vercel estrenan dominio cada vez;
    enumerarlos uno a uno no escala."""

    PATRON = r"https://morgan-.*-wv-andys-projects\.vercel\.app"

    def test_el_patron_admite_una_vista_previa_nueva(self, cliente):
        cli = cliente(origenes=VERCEL, patron=self.PATRON)
        nueva = "https://morgan-jamas-vista-wv-andys-projects.vercel.app"

        assert _preflight(cli, nueva).headers.get("access-control-allow-origin") == nueva

    def test_el_patron_no_abre_la_puerta_a_cualquiera(self, cliente):
        """Un patrón demasiado amplio sería peor que no tenerlo."""
        cli = cliente(origenes=VERCEL, patron=self.PATRON)

        assert "access-control-allow-origin" not in _preflight(cli, AJENO).headers

    def test_un_patron_invalido_se_ignora_en_vez_de_tumbar_el_arranque(self, cliente):
        cli = cliente(origenes=VERCEL, patron="((((sin cerrar")

        # La lista enumerada sigue funcionando.
        assert _preflight(cli, VERCEL).headers.get("access-control-allow-origin") == VERCEL


class TestElDiagnosticoQuedaRegistrado:
    def test_en_cloud_sin_configurar_se_avisa(self, monkeypatch, caplog):
        """Sin este aviso, el sintoma («API desconectada») no apunta a la causa."""
        import logging

        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        # Vacias, no borradas: load_dotenv() volveria a leerlas del .env del
        # desarrollador, porque solo respeta las que YA existen en el entorno.
        monkeypatch.setenv("MORGAN_CORS_ORIGINS", "")
        monkeypatch.setenv("MORGAN_CORS_ORIGIN_REGEX", "")
        config.reset_settings()

        with caplog.at_level(logging.WARNING, logger="src.api.app"):
            create_app()

        assert "MORGAN_CORS_ORIGINS" in caplog.text
        config.reset_settings()

    def test_el_patron_no_es_un_secreto_y_se_publica(self, monkeypatch):
        """Ayuda a diagnosticar y no expone nada."""
        monkeypatch.setenv("MORGAN_CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app")
        config.reset_settings()
        try:
            assert "cors_origin_regex" in config.get_settings().redacted()
        finally:
            config.reset_settings()


class TestOrigenesPegadosDesdeUnPanelWeb:
    """Un origen CORS debe casar exactamente con lo que envia el navegador.
    Tres descuidos al pegarlo en el panel de un hosting lo rompen, y ninguno
    produce error legible: solo un «API desconectada» sin pistas."""

    @pytest.mark.parametrize(
        "pegado",
        [
            '"https://morgan-ia.vercel.app"',   # comillas dobles arrastradas
            "'https://morgan-ia.vercel.app'",   # comillas simples
            "https://morgan-ia.vercel.app/",    # barra final
            "  https://morgan-ia.vercel.app ",  # espacios
            "morgan-ia.vercel.app",             # sin esquema
        ],
    )
    def test_se_normaliza_y_acaba_funcionando(self, cliente, pegado):
        respuesta = _preflight(cliente(origenes=pegado), VERCEL)

        assert respuesta.headers.get("access-control-allow-origin") == VERCEL

    def test_normalizar_no_convierte_un_dominio_ajeno_en_valido(self, cliente):
        """Tolerar descuidos no puede significar aceptar a cualquiera."""
        cli = cliente(origenes='"https://morgan-ia.vercel.app/"')

        assert "access-control-allow-origin" not in _preflight(cli, AJENO).headers

    def test_un_valor_inservible_cae_a_los_de_desarrollo(self, cliente):
        """Mejor los de desarrollo que ninguno: deja el fallo a la vista."""
        cli = cliente(origenes="  ,  , ")

        assert _preflight(cli, "http://localhost:5173").headers.get(
            "access-control-allow-origin"
        ) == "http://localhost:5173"


class TestLaRaizDiceQueOrigenesAcepta:
    """Convierte un sintoma mudo en algo que se mira y se ve."""

    def test_publica_los_origenes_configurados(self, cliente):
        cuerpo = cliente(origenes=VERCEL).get("/").json()

        assert cuerpo["cors"]["allowed_origins"] == [VERCEL]

    def test_publica_el_patron_si_lo_hay(self, cliente):
        patron = r"https://morgan-.*\.vercel\.app"
        cuerpo = cliente(origenes=VERCEL, patron=patron).get("/").json()

        assert cuerpo["cors"]["origin_regex"] == patron

    def test_no_publica_ningun_secreto(self, cliente):
        """La raiz no pide token: su contenido es publico."""
        texto = cliente(origenes=VERCEL).get("/").text.lower()

        for prohibido in ("api_key", "secret", "password", "supabase", "bearer"):
            assert prohibido not in texto


class TestLasRespuestasDeErrorTambienLlevanCORS:
    """El fallo mas dificil de ver de toda esta serie.

    El middleware de autenticacion se registraba DESPUES del de CORS, y Starlette
    aplica el ultimo registrado por fuera. Asi, un 401 salia sin cabeceras CORS:
    el navegador bloqueaba la respuesta y el codigo del cliente nunca llegaba a
    leer el 401. En vez de pedir el token, la web mostraba "API desconectada", que
    apunta a una causa equivocada.

    Un error sin cabeceras CORS es invisible para quien lo provoca.
    """

    @pytest.fixture
    def cliente_con_token(self, monkeypatch):
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_CORS_ORIGINS", VERCEL)
        monkeypatch.setenv("MORGAN_CORS_ORIGIN_REGEX", "")
        monkeypatch.setenv("MORGAN_API_TOKEN", "token-de-prueba")
        config.reset_settings()
        yield TestClient(create_app())
        config.reset_settings()

    def test_el_401_llega_con_cabecera_de_origen(self, cliente_con_token):
        """Sin esto el navegador oculta el 401 y la causa real se pierde."""
        respuesta = cliente_con_token.get("/status", headers={"Origin": VERCEL})

        assert respuesta.status_code == 401
        assert respuesta.headers.get("access-control-allow-origin") == VERCEL

    def test_con_token_valido_tambien(self, cliente_con_token):
        respuesta = cliente_con_token.get(
            "/status",
            headers={"Origin": VERCEL, "Authorization": "Bearer token-de-prueba"},
        )

        assert respuesta.status_code == 200
        assert respuesta.headers.get("access-control-allow-origin") == VERCEL

    def test_una_ruta_inexistente_tambien(self, cliente_con_token):
        """Un 404 mudo confunde igual que un 401 mudo."""
        respuesta = cliente_con_token.get("/no-existe-esta-ruta", headers={"Origin": VERCEL})

        assert respuesta.headers.get("access-control-allow-origin") == VERCEL

    def test_un_origen_ajeno_no_recibe_cabecera_ni_en_el_error(self, cliente_con_token):
        """Etiquetar los errores no puede convertirse en abrir la puerta."""
        respuesta = cliente_con_token.get("/status", headers={"Origin": AJENO})

        assert "access-control-allow-origin" not in respuesta.headers

    def test_el_cors_es_el_middleware_mas_externo(self, cliente_con_token):
        """Comprueba la causa, no solo el sintoma: el orden de registro."""
        from fastapi.middleware.cors import CORSMiddleware

        app = create_app()
        # Starlette aplica el primero de la lista por fuera.
        assert app.user_middleware[0].cls is CORSMiddleware, (
            "CORS debe quedar por fuera de todo, o los errores saldran sin sus "
            "cabeceras y el navegador los ocultara"
        )
