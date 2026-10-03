"""
Tokens personales de API (plan de la API, fase 1).

Lo que usa un cliente que no es el navegador para hablar con Morgan. La prueba de
aceptación del plan ([plan-api-plataforma.md](../docs/plan-api-plataforma.md)):

- Un cliente con `Bearer mgn_...` **chatea y lista**, y **no puede** borrar la
  cuenta ni crear tokens.
- **El token de A no ve nada de B.**
- **Revocar corta en la petición siguiente.**

Y lo que el diseño promete además: solo se guarda el hash, caduca, los alcances se
respetan, cambiar la contraseña los revoca todos, y eximir del CSRF a los tokens no
abre nada a la web (una petición con cookie sigue exigiendo CSRF).
"""

import json

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import reset_settings
from src.identidad.repositorio import CuentasSQLite, CuentasSupabase
from src.identidad.tokens import (
    CHAT,
    DIAS_MAXIMOS,
    ESCRITURA,
    LECTURA,
    MAX_TOKENS_VIVOS,
    DemasiadosTokens,
    ServicioDeTokens,
    TokenNoValido,
    alcance_necesario,
    fijar_token,
    token_de_la_cabecera,
)
from src.memory.db import Database

CLAVE = "contrasena-larga"
TODOS = [CHAT, LECTURA, ESCRITURA]


# --- El servicio, sin HTTP ----------------------------------------------------


@pytest.fixture
def base(tmp_path):
    db = Database(tmp_path / "tokens.db")
    db.migrate()
    with db.connect() as conn:
        for uid, estado in (("usr-ana", "activo"), ("usr-bruno", "activo")):
            conn.execute(
                "INSERT INTO morgan_users (id, username, email, status, role, creado_en) "
                "VALUES (?, ?, ?, ?, 'user', 1.0)",
                (uid, uid[4:], f"{uid[4:]}@ejemplo.co", estado),
            )
    return db


@pytest.fixture
def tokens(base):
    return ServicioDeTokens(CuentasSQLite(base))


class TestCrear:
    def test_el_valor_lleva_el_prefijo_y_solo_se_guarda_su_hash(self, tokens, base):
        valor, datos = tokens.crear("usr-ana", "script", [CHAT])

        assert valor.startswith("mgn_") and len(valor) > 40
        with base.connect() as conn:
            fila = dict(conn.execute("SELECT * FROM api_tokens").fetchone())
        assert valor not in json.dumps(fila)
        assert fila["token_hash"] != valor and len(fila["token_hash"]) == 64

    def test_lo_que_se_enseña_no_lleva_ni_hash_ni_valor(self, tokens):
        valor, datos = tokens.crear("usr-ana", "script", [CHAT])
        listados = tokens.listar("usr-ana")

        for cosa in (datos, *listados):
            assert "token_hash" not in cosa
            assert valor not in json.dumps(cosa)
        assert listados[0]["alcances"] == [CHAT]

    def test_caduca_a_los_90_dias_si_no_se_dice_otra_cosa(self, tokens):
        _, datos = tokens.crear("usr-ana", "script", [CHAT])

        assert round((datos["caduca_en"] - datos["creado_en"]) / 86400) == 90

    @pytest.mark.parametrize("nombre, alcances, dias", [
        ("", [CHAT], 90),
        ("x" * 61, [CHAT], 90),
        ("script", [], 90),
        ("script", ["admin"], 90),
        ("script", [CHAT, "todo"], 90),
        ("script", [CHAT], 0),
        ("script", [CHAT], DIAS_MAXIMOS + 1),
    ])
    def test_rechaza_lo_que_no_vale(self, tokens, nombre, alcances, dias):
        with pytest.raises(TokenNoValido):
            tokens.crear("usr-ana", nombre, alcances, dias)

    def test_hay_un_tope_de_tokens_vivos(self, tokens):
        for n in range(MAX_TOKENS_VIVOS):
            tokens.crear("usr-ana", f"t{n}", [CHAT])

        with pytest.raises(DemasiadosTokens):
            tokens.crear("usr-ana", "uno más", [CHAT])
        # El de otra persona no cuenta.
        tokens.crear("usr-bruno", "suyo", [CHAT])


class TestResolver:
    def test_un_token_vivo_da_su_usuario_y_sus_alcances(self, tokens):
        valor, _ = tokens.crear("usr-ana", "script", [CHAT, LECTURA])

        resuelto = tokens.resolver(valor)

        assert resuelto["usuario"]["id"] == "usr-ana"
        assert resuelto["token"]["alcances"] == {CHAT, LECTURA}

    def test_uno_inventado_no(self, tokens):
        tokens.crear("usr-ana", "script", [CHAT])

        assert tokens.resolver("mgn_inventado") is None
        assert tokens.resolver("") is None

    def test_uno_caducado_no(self, tokens, base):
        valor, datos = tokens.crear("usr-ana", "script", [CHAT])
        with base.connect() as conn:
            conn.execute("UPDATE api_tokens SET caduca_en = 1.0 WHERE id = ?", (datos["id"],))

        assert tokens.resolver(valor) is None
        assert tokens.listar("usr-ana") == []

    def test_uno_revocado_no(self, tokens):
        valor, datos = tokens.crear("usr-ana", "script", [CHAT])

        assert tokens.revocar("usr-ana", datos["id"]) is True
        assert tokens.resolver(valor) is None

    def test_el_de_una_cuenta_suspendida_no(self, tokens, base):
        valor, _ = tokens.crear("usr-ana", "script", [CHAT])
        with base.connect() as conn:
            conn.execute("UPDATE morgan_users SET status = 'suspendido' WHERE id = 'usr-ana'")

        assert tokens.resolver(valor) is None

    def test_restablecer_la_contrasena_los_revoca_todos(self, tokens, base):
        """Quien la restablece puede estar haciéndolo porque se la robaron."""
        from src.identidad.cuentas import ServicioDeCuentas

        cuentas = ServicioDeCuentas(CuentasSQLite(base))
        valor, _ = tokens.crear("usr-ana", "script", [CHAT])
        enlace = cuentas.solicitar_recuperacion("ana@ejemplo.co")

        cuentas.restablecer_password(enlace, "otra-clave-larga")

        assert tokens.resolver(valor) is None

    def test_nadie_revoca_el_token_de_otro(self, tokens):
        valor, datos = tokens.crear("usr-ana", "script", [CHAT])

        assert tokens.revocar("usr-bruno", datos["id"]) is False
        assert tokens.resolver(valor) is not None

    def test_borrar_la_cuenta_se_lleva_sus_tokens(self, tokens, base):
        tokens.crear("usr-ana", "script", [CHAT])

        CuentasSQLite(base).eliminar("usr-ana")

        with base.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0

    def test_la_limpieza_barre_revocados_y_caducados(self, tokens, base):
        _, vivo = tokens.crear("usr-ana", "vivo", [CHAT])
        _, revocado = tokens.crear("usr-ana", "revocado", [CHAT])
        tokens.revocar("usr-ana", revocado["id"])

        CuentasSQLite(base).limpiar(__import__("time").time())

        with base.connect() as conn:
            ids = [f[0] for f in conn.execute("SELECT id FROM api_tokens")]
        assert ids == [vivo["id"]]


class TestQueAlcanceHaceFalta:
    @pytest.mark.parametrize("metodo, ruta, alcance", [
        ("POST", "/chat", CHAT),
        ("POST", "/chat/stream", CHAT),
        ("GET", "/sessions", LECTURA),
        ("GET", "/sessions/abc", LECTURA),
        ("GET", "/memory", LECTURA),
        ("HEAD", "/health", LECTURA),
        ("GET", "/auth/yo", LECTURA),
        ("POST", "/sessions", ESCRITURA),
        ("DELETE", "/memory/x", ESCRITURA),
        ("PATCH", "/espacios/e", ESCRITURA),
        ("PUT", "/settings", ESCRITURA),
    ])
    def test_cada_ruta_pide_el_suyo(self, metodo, ruta, alcance):
        assert alcance_necesario(metodo, ruta) == alcance

    @pytest.mark.parametrize("metodo, ruta", [
        ("POST", "/auth/tokens"),
        ("GET", "/auth/tokens"),
        ("DELETE", "/auth/tokens/tok-1"),
        ("POST", "/auth/tokens/revocar-todos"),
        ("DELETE", "/auth/cuenta"),
        ("POST", "/auth/password"),
        ("GET", "/auth/datos"),
        ("GET", "/auth/sesiones"),
        ("POST", "/auth/logout"),
        ("POST", "/auth/yo"),
        ("GET", "/admin/usuarios"),
        ("POST", "/admin/usuarios/u/rol"),
        ("POST", "/integraciones/github/conectar"),
        ("GET", "/diagnostico/goteo"),
        ("GET", "/auth"),
        ("GET", "/auth/"),
    ])
    def test_lo_que_nunca_puede_un_token(self, metodo, ruta):
        assert alcance_necesario(metodo, ruta) is None

    def test_un_prefijo_parecido_no_es_el_prohibido(self):
        """«/authors» no es «/auth»: la comparación es por segmento."""
        assert alcance_necesario("GET", "/authors") == LECTURA
        assert alcance_necesario("GET", "/administracion") == LECTURA


class TestLaCabecera:
    def test_solo_bearer_con_el_prefijo(self):
        assert token_de_la_cabecera("Bearer mgn_abc") == "mgn_abc"
        assert token_de_la_cabecera("bearer   mgn_abc ") == "mgn_abc"
        assert token_de_la_cabecera("Bearer secreto-compartido") is None
        assert token_de_la_cabecera("Basic mgn_abc") is None
        assert token_de_la_cabecera("mgn_abc") is None
        assert token_de_la_cabecera(None) is None


class TestLaAuditoriaAnotaElToken:
    def test_con_token_se_anota_su_id(self, temp_audit):
        testigo = fijar_token("tok-123")
        try:
            temp_audit.log("read_file", "low", True, {"path": "x"})
            temp_audit.registrar_evento("algo", actor="usr-ana")
        finally:
            fijar_token(None)
            del testigo

        entradas = temp_audit.get_recent(5)
        assert [e.get("token") for e in entradas] == ["tok-123", "tok-123"]

    def test_sin_token_las_entradas_son_las_de_siempre(self, temp_audit):
        temp_audit.log("read_file", "low", True, {"path": "x"})

        assert "token" not in temp_audit.get_recent(1)[0]


# --- Por HTTP -----------------------------------------------------------------


@pytest.fixture
def nube(monkeypatch):
    """Morgan en la nube: exige cuenta para todo lo que no sea entrar."""
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    app = create_app()
    yield app
    reset_settings()


def _cuenta(app, nombre: str):
    """Un navegador con sesión abierta. Devuelve (cliente, csrf)."""
    web = TestClient(app)
    r = web.post("/auth/registro", json={
        "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
    })
    assert r.status_code == 200, r.text
    return web, r.json()["csrf"]


def _crear_token(web, csrf, alcances=TODOS, nombre="script") -> tuple[str, str]:
    r = web.post(
        "/auth/tokens", json={"nombre": nombre, "alcances": alcances},
        headers={"x-morgan-csrf": csrf},
    )
    assert r.status_code == 200, r.text
    return r.json()["valor"], r.json()["token"]["id"]


def _script(app, valor: str) -> TestClient:
    """Un cliente que NO es un navegador: sin cookies, solo la cabecera."""
    return TestClient(app, headers={"Authorization": f"Bearer {valor}"})


def _codigo(respuesta) -> str:
    return (respuesta.json().get("error") or {}).get("code", "")


@pytest.fixture
def agente(nube):
    """Un agente con un modelo que apunta con qué usuario y token se le llama."""
    from src.agent.core import Agent
    from src.api.dependencies import get_container
    from src.identidad import usuario_actual
    from src.identidad.tokens import token_actual
    from src.models.base import LLMProvider, LLMResponse
    from src.security.permissions import PermissionManager
    from src.tools.registry import ToolRegistry

    vistos: list[tuple[str, str | None]] = []

    class Apuntador(LLMProvider):
        @property
        def model_name(self):
            return "apuntador"

        def generate(self, messages, tools=None, system_prompt=None, **kwargs):
            vistos.append((usuario_actual(), token_actual()))
            return LLMResponse(type="text", content="hola desde el script")

    contenedor = get_container()
    contenedor.agent = Agent(
        model=Apuntador(),
        tool_registry=ToolRegistry(),
        permission_manager=PermissionManager(interactive=False),
        # Con el historial del contenedor, para que la conversación se guarde.
        conversation_history=contenedor.conversation_history,
    )
    return vistos


class TestLaAceptacionDelPlan:
    def test_un_script_con_token_chatea_y_lista(self, nube, agente):
        web, csrf = _cuenta(nube, "ana")
        valor, token_id = _crear_token(web, csrf)
        script = _script(nube, valor)

        yo = script.get("/auth/yo").json()
        assert yo["autenticado"] is True and yo["usuario"]["email"] == "ana@ejemplo.co"
        assert yo["csrf"] == ""

        r = script.post("/chat", json={"message": "hola", "session_id": "s-script"})
        assert r.status_code == 200, r.text
        assert "hola desde el script" in r.text

        ids = [s["id"] for s in script.get("/sessions").json()["sessions"]]
        assert "s-script" in ids

        # El turno corrió como la dueña del token, y con el token anotado.
        yo_id = yo["usuario"]["id"]
        assert agente[-1] == (yo_id, token_id)

    def test_el_streaming_tambien(self, nube, agente):
        web, csrf = _cuenta(nube, "ana")
        valor, _ = _crear_token(web, csrf, [CHAT])

        r = _script(nube, valor).post("/chat/stream", json={"message": "hola"})

        assert r.status_code == 200, r.text
        assert "hola desde el script" in r.text

    def test_no_puede_borrar_la_cuenta_ni_crear_tokens(self, nube):
        web, csrf = _cuenta(nube, "ana")
        valor, token_id = _crear_token(web, csrf)
        script = _script(nube, valor)

        intentos = [
            script.request("DELETE", "/auth/cuenta", json={"password": CLAVE}),
            script.post("/auth/tokens", json={"nombre": "otro", "alcances": TODOS}),
            script.delete(f"/auth/tokens/{token_id}"),
            script.post("/auth/tokens/revocar-todos"),
            script.post("/auth/password", json={"actual": CLAVE, "nueva": "otra-clave-larga"}),
            script.get("/auth/datos"),
            script.get("/admin/usuarios"),
        ]

        for r in intentos:
            assert r.status_code == 403, (r.request.url, r.text)
            assert _codigo(r) == "TOKEN_NO_PERMITIDO"
        # Y la cuenta sigue ahí, con su token.
        assert web.get("/auth/tokens").json()["tokens"][0]["id"] == token_id

    def test_el_token_de_a_no_ve_nada_de_b(self, nube, agente):
        web_b, csrf_b = _cuenta(nube, "bruno")
        r = web_b.post(
            "/chat", json={"message": "secreto de bruno", "session_id": "s-bruno"},
            headers={"x-morgan-csrf": csrf_b},
        )
        assert r.status_code == 200, r.text

        web_a, csrf_a = _cuenta(nube, "ana")
        script_a = _script(nube, _crear_token(web_a, csrf_a)[0])

        assert script_a.get("/sessions").json()["sessions"] == []
        assert script_a.get("/sessions/s-bruno").status_code == 404
        assert "secreto de bruno" not in script_a.get("/sessions/s-bruno/messages").text

    def test_revocar_corta_en_la_peticion_siguiente(self, nube):
        web, csrf = _cuenta(nube, "ana")
        valor, token_id = _crear_token(web, csrf)
        script = _script(nube, valor)
        assert script.get("/sessions").status_code == 200

        r = web.delete(f"/auth/tokens/{token_id}", headers={"x-morgan-csrf": csrf})
        assert r.status_code == 200

        r = script.get("/sessions")
        assert r.status_code == 401 and _codigo(r) == "TOKEN_INVALIDO"
        assert r.headers.get("www-authenticate") == "Bearer"


class TestLosAlcances:
    def test_sin_escritura_no_crea_nada(self, nube):
        web, csrf = _cuenta(nube, "ana")
        script = _script(nube, _crear_token(web, csrf, [CHAT, LECTURA])[0])

        r = script.post("/sessions", json={"title": "x"})

        assert r.status_code == 403 and _codigo(r) == "ALCANCE_INSUFICIENTE"

    def test_con_escritura_si_y_sin_csrf(self, nube):
        """El CSRF protege lo que el navegador manda solo; un token no viaja solo."""
        web, csrf = _cuenta(nube, "ana")
        script = _script(nube, _crear_token(web, csrf, [ESCRITURA])[0])

        assert script.post("/sessions", json={"title": "x"}).status_code == 200

    def test_sin_chat_no_chatea(self, nube, agente):
        web, csrf = _cuenta(nube, "ana")
        script = _script(nube, _crear_token(web, csrf, [LECTURA, ESCRITURA])[0])

        r = script.post("/chat", json={"message": "hola"})

        assert r.status_code == 403 and _codigo(r) == "ALCANCE_INSUFICIENTE"
        assert agente == []

    def test_sin_lectura_no_lista(self, nube):
        web, csrf = _cuenta(nube, "ana")
        script = _script(nube, _crear_token(web, csrf, [CHAT])[0])

        assert _codigo(script.get("/sessions")) == "ALCANCE_INSUFICIENTE"


class TestLaWebSigueIgual:
    def test_con_cookie_el_csrf_se_exige_aunque_traiga_token(self, nube):
        """Si la cabecera bastara para saltarse el CSRF, una web ajena podría
        mandar la cookie de la víctima con un `Bearer mgn_` cualquiera."""
        web, csrf = _cuenta(nube, "ana")
        valor, _ = _crear_token(web, csrf)

        r = web.post(
            "/sessions", json={"title": "x"}, headers={"Authorization": f"Bearer {valor}"}
        )

        assert r.status_code == 403 and _codigo(r) == "CSRF"

    def test_los_tokens_se_gestionan_con_sesion(self, nube):
        web, csrf = _cuenta(nube, "ana")
        _crear_token(web, csrf, nombre="uno")
        _, dos = _crear_token(web, csrf, nombre="dos")

        assert [t["nombre"] for t in web.get("/auth/tokens").json()["tokens"]] == ["dos", "uno"]

        r = web.post("/auth/tokens/revocar-todos", headers={"x-morgan-csrf": csrf})
        assert r.json()["revocados"] == 2
        assert web.get("/auth/tokens").json()["tokens"] == []

    def test_revocar_uno_ajeno_da_404(self, nube):
        web_a, csrf_a = _cuenta(nube, "ana")
        valor, token_id = _crear_token(web_a, csrf_a)
        web_b, csrf_b = _cuenta(nube, "bruno")

        r = web_b.delete(f"/auth/tokens/{token_id}", headers={"x-morgan-csrf": csrf_b})

        assert r.status_code == 404
        assert _script(nube, valor).get("/sessions").status_code == 200

    def test_crear_con_datos_malos_da_400(self, nube):
        web, csrf = _cuenta(nube, "ana")

        r = web.post(
            "/auth/tokens", json={"nombre": "x", "alcances": ["admin"]},
            headers={"x-morgan-csrf": csrf},
        )

        assert r.status_code == 400

    def test_cambiar_la_contrasena_los_revoca_todos(self, nube):
        web, csrf = _cuenta(nube, "ana")
        script = _script(nube, _crear_token(web, csrf)[0])

        r = web.post(
            "/auth/password", json={"actual": CLAVE, "nueva": "otra-clave-larga"},
            headers={"x-morgan-csrf": csrf},
        )
        assert r.status_code == 200, r.text

        assert _codigo(script.get("/sessions")) == "TOKEN_INVALIDO"


class TestLasPuertasQueNoSeAbren:
    def test_un_token_inventado_da_401(self, nube):
        r = _script(nube, "mgn_inventado").get("/sessions")

        assert r.status_code == 401 and _codigo(r) == "TOKEN_INVALIDO"

    def test_sin_cuentas_exigidas_un_token_malo_no_entra_como_el_usuario_local(self):
        """En el Morgan de tu equipo, sin sesión se es el usuario local, que es el
        propietario. Un `mgn_` que no vale no puede heredar eso."""
        r = _script(create_app(), "mgn_inventado").get("/sessions")

        assert r.status_code == 401 and _codigo(r) == "TOKEN_INVALIDO"

    def test_el_interruptor_apaga_los_que_existen(self, nube, monkeypatch):
        web, csrf = _cuenta(nube, "ana")
        script = _script(nube, _crear_token(web, csrf)[0])

        monkeypatch.setenv("MORGAN_TOKENS_API", "false")
        reset_settings()

        r = script.get("/sessions")
        assert r.status_code == 401 and _codigo(r) == "TOKENS_DESACTIVADOS"
        r = web.post(
            "/auth/tokens", json={"nombre": "x", "alcances": [CHAT]},
            headers={"x-morgan-csrf": csrf},
        )
        assert r.status_code == 403 and _codigo(r) == "TOKENS_DESACTIVADOS"

    def test_con_token_compartido_el_personal_sigue_valiendo(self, nube, monkeypatch):
        """El token compartido protege el despliegue; uno personal lo comprueba la
        identidad, que va por dentro. El malo se rechaza igual."""
        web, csrf = _cuenta(nube, "ana")
        valor, _ = _crear_token(web, csrf)

        monkeypatch.setenv("MORGAN_API_TOKEN", "secreto-compartido")
        reset_settings()

        assert _script(nube, valor).get("/sessions").status_code == 200
        assert _script(nube, "mgn_inventado").get("/sessions").status_code == 401
        # Y lo que no es un token personal sigue necesitando el compartido.
        assert _script(nube, "otro").get("/sessions").status_code == 401


# --- La implementación de Supabase ---------------------------------------------


class ClienteFalso:
    def __init__(self, respuesta=None):
        self.peticiones: list[tuple[str, str, str]] = []
        self.respuesta = respuesta if respuesta is not None else []

    def select(self, tabla, consulta=""):
        self.peticiones.append(("GET", tabla, consulta))
        return [dict(f) for f in self.respuesta]

    def update(self, tabla, consulta, valores):
        self.peticiones.append(("PATCH", tabla, consulta))
        return self.respuesta

    def insert(self, tabla, filas):
        self.peticiones.append(("POST", tabla, json.dumps(filas)))
        return self.respuesta

    def delete(self, tabla, consulta):
        self.peticiones.append(("DELETE", tabla, consulta))
        return self.respuesta


class TestEnSupabase:
    def test_resolver_filtra_revocado_y_caducidad_y_trae_el_usuario(self):
        cliente = ClienteFalso([{
            "id": "tok-1", "nombre": "s", "alcances": "chat", "caduca_en": 9e9,
            "morgan_users": {"id": "usr-ana", "status": "activo"},
        }])

        encontrado = CuentasSupabase(cliente).token_por_hash("abc", 100.0)

        consulta = cliente.peticiones[-1][2]
        assert cliente.peticiones[-1][1] == "api_tokens"
        assert "token_hash=eq.abc" in consulta
        assert "revocado=is.false" in consulta and "caduca_en=gt.100.0" in consulta
        assert "morgan_users(*)" in consulta
        assert encontrado["usuario"]["id"] == "usr-ana"
        assert "morgan_users" not in encontrado["token"]

    def test_una_cuenta_suspendida_no_resuelve(self):
        cliente = ClienteFalso([{
            "id": "tok-1", "morgan_users": {"id": "usr-ana", "status": "suspendido"},
        }])

        assert CuentasSupabase(cliente).token_por_hash("abc", 1.0) is None

    def test_listar_no_pide_el_hash(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).listar_tokens("usr-ana", 1.0)

        consulta = cliente.peticiones[-1][2]
        assert "token_hash" not in consulta and "select=" in consulta

    def test_revocar_uno_filtra_por_su_dueño(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).revocar_token("usr-ana", "tok-1")

        consulta = cliente.peticiones[-1][2]
        assert "id=eq.tok-1" in consulta and "user_id=eq.usr-ana" in consulta

    def test_revocado_se_guarda_como_booleano(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).crear_token({"id": "tok-1", "revocado": 0})

        assert '"revocado": false' in cliente.peticiones[-1][2]

    def test_borrar_la_cuenta_borra_sus_tokens(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).eliminar("usr-ana")

        assert ("DELETE", "api_tokens", "user_id=eq.usr-ana") in cliente.peticiones
