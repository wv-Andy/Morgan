"""
Identidad y emparejamiento de los agentes locales (3.0-C).

Lo que fija, del contrato (docs/agente-local.md §4-§6 y amenazas C, D, H, I):

- **El código**: diez minutos, un solo uso, solo el último vale, y consultarlo no lo
  gasta. Adivinarlo está frenado por origen y en total.
- **La confirmación en el PC** (amenaza H): sin un «s» explícito no se empareja, y el
  código sigue sin usar.
- **La credencial**: solo su hash en la nube, cifrada con DPAPI en el PC, no vale en
  la API REST, y un token personal no vale donde vale ella.
- **Revocar** corta al momento; cambiar o restablecer la contraseña revoca todos.
"""

import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from src.agente import estado as almacen
from src.agente.emparejar import EmparejamientoFallido, desemparejar, emparejar
from src.agente.protocolo import ALFABETO_CODIGO, PROTOCOLO_ACTUAL, normalizar_codigo
from src.api.app import create_app
from src.config import reset_settings
from src.identidad.agentes import (
    MAX_AGENTES,
    MAX_FALLOS_POR_ORIGEN,
    AgenteNoValido,
    CodigoNoValido,
    DemasiadosFallos,
    ServicioDeAgentes,
    correo_parcial,
)
from src.identidad.repositorio import CuentasSQLite, CuentasSupabase
from src.memory.db import Database

CLAVE = "contrasena-larga"


# --- El servicio -------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    db = Database(tmp_path / "agentes.db")
    db.migrate()
    with db.connect() as conn:
        for uid in ("usr-ana", "usr-bruno"):
            conn.execute(
                "INSERT INTO morgan_users (id, username, email, status, role, creado_en, display_name) "
                "VALUES (?, ?, ?, 'activo', 'user', 1.0, ?)",
                (uid, uid[4:], f"{uid[4:]}@ejemplo.co", uid[4:].title()),
            )
    return db


@pytest.fixture
def agentes(base):
    return ServicioDeAgentes(CuentasSQLite(base))


def _emparejar(agentes, user_id="usr-ana", origen="1.1.1.1"):
    codigo = agentes.crear_codigo(user_id)["codigo"]
    return agentes.confirmar(codigo, origen, "Portátil", "Windows 11", "3.0.0", PROTOCOLO_ACTUAL)


class TestElCodigo:
    def test_forma_y_solo_su_hash(self, agentes, base):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]

        assert len(codigo) == 9 and codigo[4] == "-"
        assert all(c in ALFABETO_CODIGO for c in normalizar_codigo(codigo))
        with base.connect() as conn:
            fila = dict(conn.execute("SELECT * FROM agente_codigos").fetchone())
        assert normalizar_codigo(codigo) not in json.dumps(fila)

    def test_dura_diez_minutos(self, agentes):
        creado = agentes.crear_codigo("usr-ana")
        assert round((creado["caduca_en"] - time.time()) / 60) == 10

    def test_consultar_no_lo_gasta_y_confirmar_si(self, agentes):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]

        cuenta = agentes.consultar(codigo, "1.1.1.1")
        assert cuenta["nombre"] == "Ana" and cuenta["correo"] == "an•••@ejemplo.co"
        agentes.consultar(codigo.lower().replace("-", " "), "1.1.1.1")  # se teclea como sea

        agentes.confirmar(codigo, "1.1.1.1", "Portátil", None, "3.0", PROTOCOLO_ACTUAL)
        with pytest.raises(CodigoNoValido):
            agentes.confirmar(codigo, "1.1.1.1", "Otro", None, "3.0", PROTOCOLO_ACTUAL)

    def test_solo_vale_el_ultimo(self, agentes):
        viejo = agentes.crear_codigo("usr-ana")["codigo"]
        nuevo = agentes.crear_codigo("usr-ana")["codigo"]

        with pytest.raises(CodigoNoValido):
            agentes.consultar(viejo, "1.1.1.1")
        agentes.consultar(nuevo, "1.1.1.1")

    def test_caducado_no_vale(self, agentes, base):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]
        with base.connect() as conn:
            conn.execute("UPDATE agente_codigos SET caduca_en = 1.0")

        with pytest.raises(CodigoNoValido):
            agentes.consultar(codigo, "1.1.1.1")

    def test_el_de_una_cuenta_suspendida_no_vale(self, agentes, base):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]
        with base.connect() as conn:
            conn.execute("UPDATE morgan_users SET status = 'suspendido' WHERE id = 'usr-ana'")

        with pytest.raises(CodigoNoValido):
            agentes.consultar(codigo, "1.1.1.1")


class TestAdivinarEstaFrenado:
    def test_por_origen(self, agentes):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]
        for _ in range(MAX_FALLOS_POR_ORIGEN):
            with pytest.raises(CodigoNoValido):
                agentes.consultar("ZZZZ-ZZZZ", "6.6.6.6")

        # Frenado aunque ahora acierte: si no, el freno solo avisaría.
        with pytest.raises(DemasiadosFallos):
            agentes.consultar(codigo, "6.6.6.6")
        # Otro origen no paga los fallos del primero.
        agentes.consultar(codigo, "1.1.1.1")

    def test_en_total_aunque_cambie_de_origen(self, agentes, monkeypatch):
        from src.identidad import agentes as modulo

        monkeypatch.setattr(modulo, "MAX_FALLOS_EN_TOTAL", 5)
        for n in range(5):
            with pytest.raises(CodigoNoValido):
                agentes.consultar("ZZZZ-ZZZZ", f"6.6.6.{n}")

        with pytest.raises(DemasiadosFallos):
            agentes.consultar("ZZZZ-ZZZZ", "7.7.7.7")


class TestLaIdentidadDelAgente:
    def test_la_credencial_solo_se_guarda_como_hash(self, agentes, base):
        emparejado = _emparejar(agentes)

        assert emparejado["credencial"].startswith("mga_")
        assert emparejado["agent_id"].startswith("agt-")
        with base.connect() as conn:
            fila = dict(conn.execute("SELECT * FROM agentes").fetchone())
        assert emparejado["credencial"] not in json.dumps(fila)
        assert "credencial_hash" not in json.dumps(agentes.listar("usr-ana"))

    def test_resolver_y_revocar(self, agentes):
        emparejado = _emparejar(agentes)

        resuelto = agentes.resolver(emparejado["credencial"])
        assert resuelto["agente"]["id"] == emparejado["agent_id"]
        assert resuelto["usuario"]["id"] == "usr-ana"

        assert agentes.revocar("usr-bruno", emparejado["agent_id"]) is False
        assert agentes.resolver(emparejado["credencial"]) is not None
        assert agentes.revocar("usr-ana", emparejado["agent_id"]) is True
        assert agentes.resolver(emparejado["credencial"]) is None

    def test_un_token_personal_no_es_una_credencial_de_agente(self, agentes):
        assert agentes.resolver("mgn_loquesea") is None
        assert agentes.resolver(None) is None

    def test_protocolo_fuera_de_rango(self, agentes):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]
        for version in (0, PROTOCOLO_ACTUAL + 1):
            with pytest.raises(AgenteNoValido):
                agentes.confirmar(codigo, "1.1.1.1", "PC", None, "x", version)
        # Y el código no se gastó por intentarlo con un agente que no vale.
        agentes.consultar(codigo, "1.1.1.1")

    def test_dos_agentes_con_el_mismo_codigo_a_la_vez(self, agentes, base):
        """Los dos pasan la comprobación del código vivo; solo uno llega a gastarlo.
        Se simula que otro lo gastó justo entre medias: este no puede emparejar."""
        codigo = agentes.crear_codigo("usr-ana")["codigo"]
        agentes.repo.consumir_codigo_agente = lambda *a: False

        with pytest.raises(CodigoNoValido):
            agentes.confirmar(codigo, "1.1.1.1", "PC", None, "3.0", PROTOCOLO_ACTUAL)
        with base.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM agentes").fetchone()[0] == 0

    @pytest.mark.parametrize("nombre", ["", "   ", "x" * 61])
    def test_el_nombre_del_equipo_se_valida(self, agentes, nombre):
        codigo = agentes.crear_codigo("usr-ana")["codigo"]

        with pytest.raises(AgenteNoValido):
            agentes.confirmar(codigo, "1.1.1.1", nombre, None, "3.0", PROTOCOLO_ACTUAL)

    def test_hay_un_tope_de_equipos(self, agentes):
        for _ in range(MAX_AGENTES):
            _emparejar(agentes)
        codigo = agentes.crear_codigo("usr-ana")["codigo"]

        with pytest.raises(AgenteNoValido):
            agentes.confirmar(codigo, "1.1.1.1", "Uno más", None, "3.0", PROTOCOLO_ACTUAL)

    def test_cambiar_la_contrasena_los_revoca(self, agentes, base):
        from src.identidad.cuentas import ServicioDeCuentas
        from src.identidad.password import hashear

        with base.connect() as conn:
            conn.execute("UPDATE morgan_users SET password_hash = ? WHERE id = 'usr-ana'", (hashear(CLAVE),))
        emparejado = _emparejar(agentes)
        pendiente = agentes.crear_codigo("usr-ana")["codigo"]

        ServicioDeCuentas(CuentasSQLite(base)).cambiar_password("usr-ana", CLAVE, "otra-clave-larga")

        assert agentes.resolver(emparejado["credencial"]) is None
        with pytest.raises(CodigoNoValido):
            agentes.consultar(pendiente, "1.1.1.1")

    def test_restablecerla_tambien(self, agentes, base):
        from src.identidad.cuentas import ServicioDeCuentas

        emparejado = _emparejar(agentes)
        cuentas = ServicioDeCuentas(CuentasSQLite(base))
        cuentas.restablecer_password(cuentas.solicitar_recuperacion("ana@ejemplo.co"), "otra-clave-larga")

        assert agentes.resolver(emparejado["credencial"]) is None

    def test_borrar_la_cuenta_se_lleva_sus_agentes(self, agentes, base):
        _emparejar(agentes)
        agentes.crear_codigo("usr-ana")

        CuentasSQLite(base).eliminar("usr-ana")

        with base.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM agentes").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM agente_codigos").fetchone()[0] == 0

    def test_la_limpieza_barre_codigos_usados_y_agentes_revocados(self, agentes, base):
        vivo = _emparejar(agentes)
        revocado = _emparejar(agentes)
        agentes.revocar("usr-ana", revocado["agent_id"])

        CuentasSQLite(base).limpiar(time.time())

        with base.connect() as conn:
            assert [f[0] for f in conn.execute("SELECT id FROM agentes")] == [vivo["agent_id"]]
            assert conn.execute("SELECT COUNT(*) FROM agente_codigos").fetchone()[0] == 0


def test_correo_parcial():
    assert correo_parcial("ana.garcia@ejemplo.co") == "an•••@ejemplo.co"
    assert correo_parcial(None) is None


# --- Por HTTP, con el agente de verdad ----------------------------------------


@pytest.fixture
def nube(monkeypatch):
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield create_app()
    reset_settings()


def _web(app, nombre="ana"):
    web = TestClient(app)
    r = web.post("/auth/registro", json={
        "username": nombre, "email": f"{nombre}@ejemplo.co", "password": CLAVE,
    }, headers={"X-Forwarded-For": f"10.2.0.{len(nombre)}"})
    assert r.status_code == 200, r.text
    return web, r.json()["csrf"]


def _codigo(web, csrf) -> str:
    r = web.post("/auth/agentes/codigo", headers={"x-morgan-csrf": csrf})
    assert r.status_code == 200, r.text
    return r.json()["codigo"]


def _codigo_http(respuesta) -> str:
    return (respuesta.json().get("error") or {}).get("code", "")


class TestEmparejarDeVerdad:
    def test_el_flujo_entero(self, nube):
        web, csrf = _web(nube)
        dicho: list[str] = []

        emparejamiento = emparejar(
            "http://testserver", _codigo(web, csrf), "Portátil",
            preguntar=lambda _: "s", decir=dicho.append, http=TestClient(nube),
        )

        assert emparejamiento is not None
        assert almacen.estado() is almacen.EstadoAgente.PAIRED
        assert "ana" in dicho[0]  # enseñó la cuenta antes de preguntar
        lista = web.get("/auth/agentes").json()["agentes"]
        assert [a["id"] for a in lista] == [emparejamiento.agent_id]
        assert lista[0]["sistema"] and lista[0]["protocol_version"] == PROTOCOLO_ACTUAL

    @pytest.mark.skipif(sys.platform != "win32", reason="DPAPI es de Windows")
    def test_la_credencial_se_guarda_cifrada(self, nube):
        web, csrf = _web(nube)
        emparejar("http://testserver", _codigo(web, csrf), "PC",
                  preguntar=lambda _: "s", decir=lambda _: None, http=TestClient(nube))

        credencial = almacen.credencial()
        cifrada = (almacen.carpeta() / "credencial.bin").read_bytes()
        assert credencial.startswith("mga_")
        assert credencial.encode() not in cifrada
        assert credencial not in (almacen.carpeta() / "agente.json").read_text(encoding="utf-8")

    def test_sin_confirmar_en_el_pc_no_se_empareja(self, nube):
        """Amenaza H: el código de otro, tecleado por engaño."""
        web, csrf = _web(nube)
        codigo = _codigo(web, csrf)

        resultado = emparejar("http://testserver", codigo, "PC",
                              preguntar=lambda _: "", decir=lambda _: None, http=TestClient(nube))

        assert resultado is None
        assert almacen.estado() is almacen.EstadoAgente.UNPAIRED
        assert web.get("/auth/agentes").json()["agentes"] == []
        # Y el código sigue sin gastar.
        r = TestClient(nube).post("/agente/emparejar/consultar", json={"codigo": codigo})
        assert r.status_code == 200

    def test_un_pc_emparejado_no_se_empareja_otra_vez(self, nube):
        web, csrf = _web(nube)
        cliente = TestClient(nube)
        emparejar("http://testserver", _codigo(web, csrf), "PC",
                  preguntar=lambda _: "s", decir=lambda _: None, http=cliente)

        with pytest.raises(EmparejamientoFallido):
            emparejar("http://testserver", _codigo(web, csrf), "PC",
                      preguntar=lambda _: "s", decir=lambda _: None, http=cliente)

    def test_desemparejar_revoca_en_la_nube_y_olvida_en_el_pc(self, nube):
        web, csrf = _web(nube)
        cliente = TestClient(nube)
        emparejar("http://testserver", _codigo(web, csrf), "PC",
                  preguntar=lambda _: "s", decir=lambda _: None, http=cliente)
        credencial = almacen.credencial()

        assert desemparejar(decir=lambda _: None, http=cliente) is True

        assert almacen.estado() is almacen.EstadoAgente.UNPAIRED
        assert web.get("/auth/agentes").json()["agentes"] == []
        r = cliente.post("/agente/desemparejar", headers={"Authorization": f"Bearer {credencial}"})
        assert r.status_code == 401

    def test_sin_nube_olvida_igual(self, nube):
        web, csrf = _web(nube)
        emparejar("http://testserver", _codigo(web, csrf), "PC",
                  preguntar=lambda _: "s", decir=lambda _: None, http=TestClient(nube))

        class SinRed:
            def post(self, *a, **k):
                raise ConnectionError("sin red")

        dicho: list[str] = []
        assert desemparejar(decir=dicho.append, http=SinRed()) is False
        assert almacen.estado() is almacen.EstadoAgente.UNPAIRED
        assert "revócalo también" in dicho[0]


class TestCadaCredencialEnSuSitio:
    def test_la_del_agente_no_vale_en_la_api(self, nube):
        web, csrf = _web(nube)
        emparejar("http://testserver", _codigo(web, csrf), "PC",
                  preguntar=lambda _: "s", decir=lambda _: None, http=TestClient(nube))
        agente = TestClient(nube, headers={"Authorization": f"Bearer {almacen.credencial()}"})

        assert agente.get("/sessions").status_code == 401
        assert agente.get("/auth/agentes").status_code == 401

    def test_un_token_personal_no_desempareja(self, nube):
        web, csrf = _web(nube)
        r = web.post("/auth/tokens", json={"nombre": "x", "alcances": ["chat", "lectura", "escritura"]},
                     headers={"x-morgan-csrf": csrf})
        token = r.json()["valor"]

        r = TestClient(nube).post("/agente/desemparejar", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_con_un_token_personal_no_se_pide_codigo(self, nube):
        web, csrf = _web(nube)
        r = web.post("/auth/tokens", json={"nombre": "x", "alcances": ["chat", "lectura", "escritura"]},
                     headers={"x-morgan-csrf": csrf})
        script = TestClient(nube, headers={"Authorization": f"Bearer {r.json()['valor']}"})

        r = script.post("/auth/agentes/codigo")
        assert r.status_code == 403 and _codigo_http(r) == "TOKEN_NO_PERMITIDO"

    def test_sin_sesion_no_hay_codigo(self, nube):
        assert TestClient(nube).post("/auth/agentes/codigo").status_code == 401

    def test_revocar_el_de_otro_da_404(self, nube):
        web_a, csrf_a = _web(nube, "ana")
        emparejar("http://testserver", _codigo(web_a, csrf_a), "PC",
                  preguntar=lambda _: "s", decir=lambda _: None, http=TestClient(nube))
        agent_id = almacen.cargar().agent_id
        web_b, csrf_b = _web(nube, "bruno")

        assert web_b.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf_b}).status_code == 404
        assert web_a.delete(f"/auth/agentes/{agent_id}", headers={"x-morgan-csrf": csrf_a}).status_code == 200

    def test_el_codigo_malo_dice_por_que(self, nube):
        r = TestClient(nube).post("/agente/emparejar/consultar", json={"codigo": "ZZZZ-ZZZZ"})
        assert r.status_code == 400 and _codigo_http(r) == "CODIGO_NO_VALIDO"


# --- DPAPI ---------------------------------------------------------------------


class TestElAlmacenSeguro:
    @pytest.mark.skipif(sys.platform != "win32", reason="DPAPI es de Windows")
    def test_ida_y_vuelta(self):
        from src.agente.credencial import cifrar, descifrar

        cifrado = cifrar("mga_secreto")
        assert b"mga_secreto" not in cifrado
        assert descifrar(cifrado) == "mga_secreto"

    def test_fuera_de_windows_no_guarda_en_claro(self, monkeypatch):
        from src.agente import credencial

        monkeypatch.setattr(credencial.sys, "platform", "linux")
        with pytest.raises(credencial.SinAlmacenSeguro):
            credencial.cifrar("mga_secreto")


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
    def test_el_codigo_filtra_usado_y_caducidad(self):
        cliente = ClienteFalso([{"caduca_en": 9e9, "morgan_users": {"id": "usr-ana", "status": "activo"}}])

        encontrado = CuentasSupabase(cliente).codigo_agente("abc", 100.0)

        consulta = cliente.peticiones[-1][2]
        assert "codigo_hash=eq.abc" in consulta and "usado_en=is.null" in consulta
        assert "caduca_en=gt.100.0" in consulta
        assert encontrado["usuario"]["id"] == "usr-ana"

    def test_consumir_solo_si_seguia_vivo(self):
        cliente = ClienteFalso()
        assert CuentasSupabase(cliente).consumir_codigo_agente("abc", 100.0) is False

        consulta = cliente.peticiones[-1][2]
        assert "usado_en=is.null" in consulta and "caduca_en=gt.100.0" in consulta

    def test_la_credencial_solo_de_agentes_activos_de_cuentas_activas(self):
        cliente = ClienteFalso([{"id": "agt-1", "morgan_users": {"id": "u", "status": "suspendido"}}])

        assert CuentasSupabase(cliente).agente_por_credencial("h") is None
        assert "estado=eq.activo" in cliente.peticiones[-1][2]

    def test_revocar_filtra_por_su_dueño(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).revocar_agente("usr-ana", "agt-1")

        consulta = cliente.peticiones[-1][2]
        assert "id=eq.agt-1" in consulta and "user_id=eq.usr-ana" in consulta

    def test_listar_no_pide_el_hash(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).listar_agentes("usr-ana")
        assert "credencial_hash" not in cliente.peticiones[-1][2]

    def test_borrar_la_cuenta_borra_sus_agentes(self):
        cliente = ClienteFalso()
        CuentasSupabase(cliente).eliminar("usr-ana")

        assert ("DELETE", "agentes", "user_id=eq.usr-ana") in cliente.peticiones
        assert ("DELETE", "agente_codigos", "user_id=eq.usr-ana") in cliente.peticiones


class TestLaCredencialDelAgenteNoAbreLaApi:
    """3.1.5, atacando la nube real: la credencial `mga_` de un PC no puede servir para
    entrar en la API REST. `/auth/yo` es pública a propósito (contesta «no autenticado»
    al cargar la web), así que lo que se comprueba es que **no autentica a nadie**."""

    @pytest.fixture
    def nube(self, monkeypatch):
        from src.api import dependencies
        from src.api.app import create_app
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        yield create_app()
        reset_settings()

    def test_no_autentica_en_ninguna_ruta(self, nube):
        from fastapi.testclient import TestClient

        app = nube
        web = TestClient(app)
        r = web.post("/auth/registro", json={"username": "ana", "email": "ana@ejemplo.co",
                                             "password": "contrasena-larga"},
                     headers={"X-Forwarded-For": "10.7.0.1"})
        assert r.status_code == 200, r.text
        csrf = r.json()["csrf"]
        codigo = web.post("/auth/agentes/codigo", headers={"x-morgan-csrf": csrf}).json()["codigo"]
        credencial = TestClient(app).post("/agente/emparejar/confirmar", json={
            "codigo": codigo, "nombre": "PC", "protocol_version": PROTOCOLO_ACTUAL,
        }).json()["credencial"]

        anonimo = TestClient(app)
        con_agente = TestClient(app, headers={"Authorization": f"Bearer {credencial}"})

        # `/auth/yo`: misma respuesta que sin credencial ninguna.
        assert con_agente.get("/auth/yo").json() == anonimo.get("/auth/yo").json()
        assert con_agente.get("/auth/yo").json()["usuario"] is None

        # Y en lo que exige cuenta, no entra.
        for ruta in ("/auth/agentes", "/auth/tokens", "/sessions", "/uploads"):
            assert con_agente.get(ruta).status_code in (401, 403, 404), ruta
