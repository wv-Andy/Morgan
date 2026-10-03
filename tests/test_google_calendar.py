"""
Google Calendar: conectar, renovar, leer y escribir con plan (V2.0.17).

> **APARCADO desde la 2.0.18.** decidí posponer las integraciones con
> Google: el servicio no está en el catálogo y sus herramientas no se registran.
> Estas pruebas siguen ejecutándose contra el código aparcado —las de rutas le
> añaden el servicio al catálogo solo durante la prueba— para que retomarlo no sea
> retomar código sin verificar. Lo que fija que hoy NO se ofrece está en
> `TestEstaAparcado`. Lo descarté el 2026-09-19; el código y estas pruebas se
> conservan, y `TestEstaAparcado` vigila que no se ofrezca.

Lo decidí: primero Calendar, leer **y escribir** —siempre con un plan
aprobado— y la app de Google en **modo de pruebas**, donde la autorización caduca
cada 7 días.

Lo que cambia respecto a GitHub, y por eso se prueba aquí:

1. **El token caduca a la hora.** Hace falta guardar el de renovación, usarlo antes
   de cada llamada y guardar el nuevo **sin tocar la cuenta ni los permisos**.
2. **La autorización caduca a los 7 días** en modo de pruebas. Renovar devuelve
   `invalid_grant`, y Morgan tiene que decir «vuelve a conectar», no un error
   genérico.
3. **La zona horaria es la del calendario**, no la del servidor. «El día 20» es
   en la zona de la persona.
4. **Crear un evento exige plan** (`Tool.exige_plan`, V2.0.16).
5. **El modelo no sabía qué día es hoy**, y sin eso no hay «mañana».

Nunca se llama a Google de verdad: `httpx` se sustituye en cada prueba.
"""

import json
import pathlib
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import reset_settings
from src.identidad import como_usuario
from src.integraciones import google_calendar as gc
from src.integraciones.oauth import AutorizacionCaducada, ErrorDelServicio, token_vigente
from src.integraciones.repositorio import IntegracionesSQLite, IntegracionesSupabase
from src.memory.db import Database
from src.tools.calendario import CrearEventoTool, VerEventosTool

CLAVE = "una-clave-de-cifrado-de-prueba"
REGISTRO = {"username": "ana", "email": "ana@ejemplo.co", "password": "contrasena-larga"}


class Respuesta:
    def __init__(self, cuerpo: dict, status: int = 200):
        self._cuerpo = cuerpo
        self.status_code = status

    def json(self):
        return self._cuerpo


@pytest.fixture
def credenciales(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secreto-de-google")
    monkeypatch.setenv("MORGAN_SECRET_KEY", CLAVE)


@pytest.fixture
def repo(credenciales):
    ruta = pathlib.Path(tempfile.mkdtemp()) / "calendario.db"
    return IntegracionesSQLite(Database(ruta))


# ── El cliente ────────────────────────────────────────────────────────────────


class TestElIntercambio:
    def test_pide_token_de_renovacion_siempre(self, credenciales):
        """Sin `offline` no llega; sin `consent`, al reconectar tampoco."""
        url = gc.url_de_autorizacion("estado-1", ("openid", "email", "cal"), "https://api/cb")
        parametros = parse_qs(urlparse(url).query)

        assert parametros["access_type"] == ["offline"]
        assert parametros["prompt"] == ["consent"]
        assert parametros["state"] == ["estado-1"]
        assert parametros["scope"] == ["openid email cal"]
        assert "secreto-de-google" not in url

    def test_canjear_guarda_renovacion_y_caducidad(self, credenciales, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: Respuesta({
            "access_token": "ya29.acceso", "refresh_token": "1//renovar",
            "expires_in": 3599, "scope": "openid email https://www.googleapis.com/auth/calendar.events",
        }))

        antes = time.time()
        c = gc.canjear("codigo", "https://api/cb")

        assert c.token == "ya29.acceso"
        assert c.refresco == "1//renovar"
        assert antes + 3500 < c.expira_en < antes + 3700
        assert "https://www.googleapis.com/auth/calendar.events" in c.scopes

    def test_sin_token_de_renovacion_falla_ahora_y_no_a_la_hora(self, credenciales, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: Respuesta({
            "access_token": "ya29.acceso", "expires_in": 3599,
        }))

        with pytest.raises(ErrorDelServicio, match="renovación"):
            gc.canjear("codigo", "https://api/cb")

    def test_invalid_grant_es_autorizacion_caducada_con_el_porque(self, credenciales, monkeypatch):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: Respuesta(
            {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, 400,
        ))

        with pytest.raises(AutorizacionCaducada, match="7 días"):
            gc.refrescar("1//renovar")

    def test_revocar_manda_el_token(self, credenciales, monkeypatch):
        enviado = {}

        def post(url, data=None, **k):
            enviado.update(url=url, data=data)
            return Respuesta({}, 200)

        monkeypatch.setattr(httpx, "post", post)

        assert gc.revocar("1//renovar") is True
        assert enviado == {"url": gc.REVOCAR, "data": {"token": "1//renovar"}}


class TestElCalendario:
    def test_listar_traduce_los_eventos_y_quita_los_cancelados(self, monkeypatch):
        visto = {}

        def get(url, params=None, headers=None, **k):
            visto.update(url=url, params=params, headers=headers)
            return Respuesta({"timeZone": "America/Guatemala", "items": [
                {"summary": "Dentista", "start": {"dateTime": "2026-09-20T10:00:00-06:00"},
                 "end": {"dateTime": "2026-09-20T11:00:00-06:00"}, "location": "Centro"},
                {"summary": "Cumple", "start": {"date": "2026-09-21"}, "end": {"date": "2026-09-22"}},
                {"summary": "Anulado", "status": "cancelled", "start": {}, "end": {}},
            ]})

        monkeypatch.setattr(httpx, "get", get)
        desde = datetime(2026, 9, 20, tzinfo=timezone.utc)

        r = gc.listar_eventos("ya29", desde, desde.replace(day=27))

        assert visto["url"] == gc.EVENTOS
        assert visto["headers"] == {"Authorization": "Bearer ya29"}
        assert visto["params"]["singleEvents"] == "true"
        assert r["zona_horaria"] == "America/Guatemala"
        assert [e["titulo"] for e in r["eventos"]] == ["Dentista", "Cumple"]
        assert r["eventos"][1]["todo_el_dia"] is True

    def test_crear_manda_todo_en_el_cuerpo_y_nada_en_la_url(self, monkeypatch):
        """El calendario es siempre `primary`: lo que escriba el modelo no cambia a qué se llama."""
        visto = {}

        def post(url, json=None, headers=None, **k):
            visto.update(url=url, json=json)
            return Respuesta({"summary": json["summary"], "start": json["start"], "end": json["end"]})

        monkeypatch.setattr(httpx, "post", post)

        gc.crear_evento("ya29", "../../otra/ruta", "2026-09-20T10:00", "2026-09-20T11:00", "America/Guatemala")

        assert visto["url"] == gc.EVENTOS
        assert visto["json"]["summary"] == "../../otra/ruta"
        assert visto["json"]["start"] == {"dateTime": "2026-09-20T10:00", "timeZone": "America/Guatemala"}

    def test_un_401_es_volver_a_conectar(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: Respuesta({}, 401))

        with pytest.raises(AutorizacionCaducada):
            gc.zona_horaria("ya29")


# ── La renovación ─────────────────────────────────────────────────────────────


class TestElTokenSeRenueva:
    def _conectar(self, repo, expira_en):
        repo.guardar(
            "google_calendar", "ya29.viejo", cuenta="ana@gmail.com",
            scopes=("openid", "email"), refresco="1//renovar", expira_en=expira_en,
        )

    def test_si_no_ha_caducado_no_se_llama_a_google(self, repo, monkeypatch):
        monkeypatch.setattr(gc, "refrescar", lambda r: pytest.fail("no tenía que renovar"))
        with como_usuario("usr-ana"):
            self._conectar(repo, time.time() + 3000)
            assert token_vigente(repo, "google_calendar") == "ya29.viejo"

    def test_caducado_se_renueva_y_se_guarda_sin_perder_la_cuenta(self, repo, monkeypatch):
        monkeypatch.setattr(gc, "refrescar", lambda r: ("ya29.nuevo", time.time() + 3600))
        with como_usuario("usr-ana"):
            self._conectar(repo, time.time() + 10)  # dentro del margen de un minuto

            assert token_vigente(repo, "google_calendar") == "ya29.nuevo"

            token, refresco, expira = repo.credenciales_de("google_calendar")
            integracion = repo.obtener("google_calendar")
        assert token == "ya29.nuevo"
        assert refresco == "1//renovar"
        assert expira > time.time() + 3000
        assert integracion.cuenta == "ana@gmail.com"
        assert integracion.scopes == ("openid", "email")

    def test_si_la_autorizacion_caduco_se_anota_y_se_dice(self, repo, monkeypatch):
        def caducada(refresco):
            raise AutorizacionCaducada(gc.CADUCADA)

        monkeypatch.setattr(gc, "refrescar", caducada)
        with como_usuario("usr-ana"):
            self._conectar(repo, time.time() - 5)

            with pytest.raises(AutorizacionCaducada):
                token_vigente(repo, "google_calendar")
            assert "7 días" in repo.obtener("google_calendar").error

    def test_sin_conectar_no_hay_token(self, repo):
        with como_usuario("usr-ana"):
            assert token_vigente(repo, "google_calendar") is None

    def test_el_de_renovacion_tampoco_se_guarda_en_claro(self, repo):
        with como_usuario("usr-ana"):
            self._conectar(repo, time.time() + 3000)
        with repo.db.connect() as conn:
            fila = dict(conn.execute("SELECT * FROM integraciones").fetchone())

        assert "1//renovar" not in json.dumps(fila)


class TestLaVersionDeSupabase:
    class Espia:
        def __init__(self):
            self.peticiones = []

        def select(self, tabla, consulta=""):
            self.peticiones.append(consulta)
            return []

        def update(self, tabla, consulta, valores):
            self.peticiones.append(consulta)
            self.valores = valores
            return []

    @pytest.mark.parametrize("metodo, argumentos", [
        ("credenciales_de", ("google_calendar",)),
        ("actualizar_token", ("google_calendar", "ya29", 123.0)),
    ])
    def test_filtra_por_usuario(self, credenciales, metodo, argumentos):
        cliente = self.Espia()
        with como_usuario("usr-ana"):
            getattr(IntegracionesSupabase(cliente), metodo)(*argumentos)

        assert cliente.peticiones and all("user_id=eq.usr-ana" in p for p in cliente.peticiones)

    def test_actualizar_no_manda_el_token_en_claro_ni_toca_la_cuenta(self, credenciales):
        cliente = self.Espia()
        with como_usuario("usr-ana"):
            IntegracionesSupabase(cliente).actualizar_token("google_calendar", "ya29.secreto", 123.0)

        assert "ya29.secreto" not in json.dumps(cliente.valores)
        assert "cuenta" not in cliente.valores and "scopes" not in cliente.valores


# ── Las rutas ─────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch, credenciales):
    """La API **con Calendar reactivado solo durante la prueba**: está aparcado."""
    from src.integraciones import modelos

    monkeypatch.setitem(
        modelos.CATALOGO, modelos.ServicioExterno.GOOGLE_CALENDAR,
        modelos.APARCADOS[modelos.ServicioExterno.GOOGLE_CALENDAR],
    )
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_API_URL", "https://morgan-api.ejemplo.co")
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()
    dependencies.reset_container()


def _estado(usuario: str = "ana") -> tuple[str, str]:
    from src.api.dependencies import get_container
    from src.identidad.repositorio import repositorio_de_cuentas
    from src.integraciones.repositorio import repositorio_de_integraciones

    repos = get_container().repositories
    fila = repositorio_de_cuentas(repos).buscar(usuario)
    with como_usuario(fila["id"]):
        return fila["id"], repositorio_de_integraciones(repos).crear_estado("google_calendar")


class TestLasRutas:
    def test_el_catalogo_lo_ofrece(self, api):
        api.post("/auth/registro", json=REGISTRO)

        servicios = {s["servicio"]: s for s in api.get("/integraciones").json()["servicios"]}

        assert servicios["google_calendar"]["disponible"] is True
        assert any("7 días" in p for p in servicios["google_calendar"]["permite"])

    def test_sin_credenciales_de_google_no_se_ofrece(self, api, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID")
        api.post("/auth/registro", json=REGISTRO)

        google = {s["servicio"]: s for s in api.get("/integraciones").json()["servicios"]}["google_calendar"]

        assert google["disponible"] is False
        assert "GOOGLE_CLIENT_ID" in google["motivo_no_disponible"]

    def test_conectar_devuelve_la_url_de_google(self, api):
        csrf = api.post("/auth/registro", json=REGISTRO).json()["csrf"]

        r = api.post("/integraciones/google_calendar/conectar", headers={"X-Morgan-CSRF": csrf})

        url = r.json()["url"]
        assert url.startswith(gc.AUTORIZAR)
        assert parse_qs(urlparse(url).query)["redirect_uri"] == [
            "https://morgan-api.ejemplo.co/integraciones/google_calendar/callback"
        ]

    def test_la_vuelta_guarda_el_de_renovacion_y_la_caducidad(self, api, monkeypatch):
        from src.integraciones.oauth import Credenciales

        monkeypatch.setattr(gc, "canjear", lambda code, cb: Credenciales(
            token="ya29.acceso", scopes=("openid",), refresco="1//renovar", expira_en=time.time() + 3600,
        ))
        monkeypatch.setattr(gc, "cuenta_de", lambda t: {"login": "ana@gmail.com", "nombre": "Ana", "avatar": None})
        api.post("/auth/registro", json=REGISTRO)
        user_id, estado = _estado()

        r = api.get(f"/integraciones/google_calendar/callback?code=abc&state={estado}", follow_redirects=False)

        assert "resultado=conectado" in r.headers["location"]
        from src.api.dependencies import get_container
        from src.integraciones.repositorio import repositorio_de_integraciones

        with como_usuario(user_id):
            token, refresco, expira = repositorio_de_integraciones(
                get_container().repositories
            ).credenciales_de("google_calendar")
        assert (token, refresco) == ("ya29.acceso", "1//renovar")
        assert expira > time.time()

    def test_desconectar_revoca_con_el_de_renovacion(self, api, monkeypatch):
        """Revocar el de renovación invalida la autorización entera, no solo la hora en curso."""
        revocados = []
        monkeypatch.setattr(gc, "revocar", lambda t: revocados.append(t) or True)
        csrf = api.post("/auth/registro", json=REGISTRO).json()["csrf"]
        user_id, _ = _estado()
        from src.api.dependencies import get_container
        from src.integraciones.repositorio import repositorio_de_integraciones

        with como_usuario(user_id):
            repositorio_de_integraciones(get_container().repositories).guardar(
                "google_calendar", "ya29.acceso", refresco="1//renovar", expira_en=time.time() + 3600,
            )

        r = api.delete("/integraciones/google_calendar", headers={"X-Morgan-CSRF": csrf})

        assert r.json()["desconectado"] is True
        assert revocados == ["1//renovar"]


# ── Las herramientas ─────────────────────────────────────────────────────────


class Repos:
    """Lo mínimo que usan las herramientas: una base local con integraciones."""

    def __init__(self, repo):
        self.db = repo.db


class TestLasHerramientas:
    def test_sin_conectar_dice_donde_se_conecta(self, repo):
        with como_usuario("usr-ana"):
            r = VerEventosTool(Repos(repo)).execute()

        assert r["success"] is False
        assert "Servicios" in r["error"]

    def test_el_rango_va_en_la_zona_del_calendario(self, repo, monkeypatch):
        """«El día 20» empieza a medianoche en Guatemala, no en UTC."""
        visto = {}
        monkeypatch.setattr(gc, "zona_horaria", lambda t: "America/Guatemala")

        def listar(token, desde, hasta, maximo=50):
            visto.update(desde=desde, hasta=hasta)
            return {"zona_horaria": "America/Guatemala", "eventos": []}

        monkeypatch.setattr(gc, "listar_eventos", listar)
        with como_usuario("usr-ana"):
            repo.guardar("google_calendar", "ya29", refresco="1//r", expira_en=time.time() + 3000)
            r = VerEventosTool(Repos(repo)).execute(desde="2026-09-20", dias=1)

        assert r["success"] is True
        assert visto["desde"].isoformat() == "2026-09-20T00:00:00-06:00"
        assert visto["hasta"].isoformat() == "2026-09-21T00:00:00-06:00"
        assert r["data"]["zona_horaria"] == "America/Guatemala"

    def test_la_caducidad_llega_al_modelo_como_que_hacer(self, repo, monkeypatch):
        def caducada(r):
            raise AutorizacionCaducada(gc.CADUCADA)

        monkeypatch.setattr(gc, "refrescar", caducada)
        with como_usuario("usr-ana"):
            repo.guardar("google_calendar", "ya29", refresco="1//r", expira_en=time.time() - 1)
            r = VerEventosTool(Repos(repo)).execute()

        assert "vuelve a conectarlo" in r["error"]

    def test_crear_exige_plan(self, repo):
        herramienta = CrearEventoTool(Repos(repo))

        assert herramienta.exige_plan is True
        assert herramienta.requires_local is False

    @pytest.mark.parametrize("argumentos, motivo", [
        ({"titulo": "", "inicio": "2026-09-20T10:00", "fin": "2026-09-20T11:00"}, "título"),
        ({"titulo": "x", "inicio": "mañana", "fin": "2026-09-20T11:00"}, "AAAA-MM-DD"),
        ({"titulo": "x", "inicio": "2026-09-20T11:00", "fin": "2026-09-20T10:00"}, "después"),
    ])
    def test_crear_valida_antes_de_llamar(self, repo, monkeypatch, argumentos, motivo):
        monkeypatch.setattr(gc, "crear_evento", lambda *a, **k: pytest.fail("no tenía que llamar"))
        with como_usuario("usr-ana"):
            r = CrearEventoTool(Repos(repo)).execute(**argumentos)

        assert r["success"] is False
        assert motivo in r["error"]

    def test_crear_usa_la_zona_del_calendario(self, repo, monkeypatch):
        visto = {}
        monkeypatch.setattr(gc, "zona_horaria", lambda t: "America/Guatemala")
        monkeypatch.setattr(gc, "crear_evento", lambda token, titulo, inicio, fin, zona, **k: visto.update(
            inicio=inicio, zona=zona) or {"titulo": titulo})
        with como_usuario("usr-ana"):
            repo.guardar("google_calendar", "ya29", refresco="1//r", expira_en=time.time() + 3000)
            r = CrearEventoTool(Repos(repo)).execute(titulo="Dentista", inicio="2026-09-20T10:00", fin="2026-09-20T11:00")

        assert r["success"] is True
        assert visto == {"inicio": "2026-09-20T10:00", "zona": "America/Guatemala"}

    def test_no_tocan_la_maquina(self, repo):
        """Cuando se reactive, existirán también en la nube."""
        assert VerEventosTool(Repos(repo)).requires_local is False


class TestEstaAparcado:
    """Decisión mía (2026-09-16): las integraciones con Google, pospuestas."""

    def test_la_web_no_lo_ofrece(self, tmp_path, monkeypatch, credenciales):
        """Aunque el servidor TENGA las credenciales de Google, no sale en Servicios."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        try:
            cliente = TestClient(create_app())
            csrf = cliente.post("/auth/registro", json=REGISTRO).json()["csrf"]

            servicios = {s["servicio"] for s in cliente.get("/integraciones").json()["servicios"]}
            conectar = cliente.post(
                "/integraciones/google_calendar/conectar", headers={"X-Morgan-CSRF": csrf},
            )
        finally:
            reset_settings()
            dependencies.reset_container()

        assert "google_calendar" not in servicios
        assert servicios == {"github"}
        assert conectar.status_code == 404

    @pytest.mark.parametrize("entorno", ["local", "cloud"])
    def test_el_modelo_no_ve_sus_herramientas(self, monkeypatch, tmp_path, entorno):
        monkeypatch.setenv("MORGAN_ENVIRONMENT", entorno)
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_API_TOKEN", "t" * 32)
        reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        try:
            nombres = {t.name for t in dependencies.get_container().tool_registry.list_tools()}
        finally:
            reset_settings()
            dependencies.reset_container()

        assert not {"calendario_ver_eventos", "calendario_crear_evento"} & nombres


# ── La fecha ──────────────────────────────────────────────────────────────────


class TestElModeloSabeQueDiaEs:
    def test_la_linea_dice_el_dia_de_la_semana(self):
        from src.agent.core import _linea_de_fecha

        linea = _linea_de_fecha(datetime(2026, 9, 17, 8, 5, tzinfo=timezone.utc))

        assert "jueves 2026-09-17 08:05" in linea

    def test_va_en_el_prompt_del_turno_y_no_en_el_compartido(self, non_interactive_permissions):
        from src.agent.core import Agent
        from src.agent.sessions import SessionStore
        from src.models.base import LLMProvider, LLMResponse
        from src.tools.registry import ToolRegistry

        class Espia(LLMProvider):
            prompts = []

            @property
            def model_name(self):
                return "espia"

            def generate(self, messages, tools=None, system_prompt=None):
                self.prompts.append(system_prompt)
                return LLMResponse(type="text", content="ok")

        modelo = Espia()
        agente = Agent(model=modelo, tool_registry=ToolRegistry(),
                       permission_manager=non_interactive_permissions, sessions=SessionStore())

        agente.chat("¿qué día es?", session_id="fecha")

        assert "Fecha y hora actuales (UTC)" in modelo.prompts[-1]
        assert "Fecha y hora actuales" not in agente.system_prompt
