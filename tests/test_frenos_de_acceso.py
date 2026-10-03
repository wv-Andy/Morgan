"""
Los frenos del acceso con el registro abierto (V2.0.22, auditoría de la 2.3).

decidí que el registro se queda abierto a cualquiera con el enlace. La
auditoría midió qué podía hacer alguien de fuera con eso, y encontró dos huecos:

1. **Contraseñas sin límite real.** El freno contaba por cuenta *y origen*, y el
   origen sale de `X-Forwarded-For`, que escribe quien llama. Cambiando esa
   cabecera en cada intento, 20 contraseñas equivocadas no frenaron nada y la
   correcta entró después.
2. **Cuentas sin límite.** Un script creó 30 cuentas en 4 segundos desde el mismo
   origen, cada una con su cupo diario de mensajes.

Estas pruebas reproducen los dos ataques tal como se midieron.
"""

import time

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import reset_settings
from src.identidad import cuentas as modulo
from src.identidad.cuentas import (
    CLAVE_REGISTRO,
    MAX_INTENTOS,
    MAX_INTENTOS_POR_CUENTA,
    MAX_REGISTROS_EN_TOTAL,
    MAX_REGISTROS_POR_ORIGEN,
    DemasiadosIntentos,
    DemasiadosRegistros,
    ErrorDeAutenticacion,
    ServicioDeCuentas,
)
from src.memory.db import Database

BUENA = "la-de-verdad-123"


@pytest.fixture
def servicio(tmp_path):
    return ServicioDeCuentas(Database(tmp_path / "frenos.db"))


@pytest.fixture
def victima(servicio):
    return servicio.registrar("victima", "victima@ejemplo.co", BUENA)


def _falla(servicio, identificador, origen):
    with pytest.raises((ErrorDeAutenticacion, DemasiadosIntentos)) as exc:
        servicio.iniciar_sesion(identificador, "mala", origen=origen)
    return exc.type


# ── 1. Contraseñas ─────────────────────────────────────────────────────────────


class TestLasContrasenasTienenTopePorCuenta:
    def test_cambiar_de_origen_en_cada_intento_ya_no_lo_esquiva(self, servicio, victima):
        """El ataque medido: un origen distinto por intento."""
        for i in range(MAX_INTENTOS_POR_CUENTA):
            assert _falla(servicio, "victima", f"10.0.{i}.1") is ErrorDeAutenticacion

        with pytest.raises(DemasiadosIntentos):
            servicio.iniciar_sesion("victima", BUENA, origen="10.9.9.9")

    def test_el_de_cada_origen_sigue_en_cinco(self, servicio, victima):
        for _ in range(MAX_INTENTOS):
            _falla(servicio, "victima", "1.1.1.1")

        assert _falla(servicio, "victima", "1.1.1.1") is DemasiadosIntentos
        # Desde otro origen aún se puede: el tope de la cuenta está en 20.
        usuario, _ = servicio.iniciar_sesion("victima", BUENA, origen="2.2.2.2")
        assert usuario.id == victima.id

    def test_alternar_usuario_y_correo_no_da_el_doble(self, servicio, victima):
        mitad = MAX_INTENTOS_POR_CUENTA // 2
        for i in range(mitad):
            _falla(servicio, "victima", f"10.1.{i}.1")
            _falla(servicio, "victima@ejemplo.co", f"10.2.{i}.1")

        with pytest.raises(DemasiadosIntentos):
            servicio.iniciar_sesion("victima@ejemplo.co", BUENA, origen="10.9.9.9")

    def test_restablecer_la_contrasena_levanta_el_bloqueo(self, servicio, victima):
        """El precio del tope por cuenta es que un atacante puede bloquearla. Su
        dueño tiene que poder salir de ahí sin esperar."""
        for i in range(MAX_INTENTOS_POR_CUENTA):
            _falla(servicio, "victima", f"10.0.{i}.1")

        token = servicio.solicitar_recuperacion("victima@ejemplo.co")
        servicio.restablecer_password(token, "otra-contrasena-larga")

        usuario, _ = servicio.iniciar_sesion("victima", "otra-contrasena-larga", origen="10.9.9.9")
        assert usuario.id == victima.id

    def test_una_cuenta_inexistente_tambien_frena(self, servicio):
        for i in range(MAX_INTENTOS_POR_CUENTA):
            _falla(servicio, "nadie", f"10.0.{i}.1")

        assert _falla(servicio, "nadie", "10.9.9.9") is DemasiadosIntentos


# ── 2. Altas ───────────────────────────────────────────────────────────────────


def _alta(servicio, n, origen):
    return servicio.registrar(f"persona{n}", f"persona{n}@ejemplo.co", BUENA, origen=origen)


class TestLasAltasTienenTope:
    def test_tres_por_origen(self, servicio):
        for n in range(MAX_REGISTROS_POR_ORIGEN):
            _alta(servicio, n, "1.1.1.1")

        with pytest.raises(DemasiadosRegistros):
            _alta(servicio, 99, "1.1.1.1")
        # Otra persona, desde otro sitio, sí.
        assert _alta(servicio, 100, "2.2.2.2")

    def test_y_veinte_en_total_aunque_cambie_el_origen(self, servicio):
        """El tope que no depende de ninguna cabecera: el que acota a un script."""
        for n in range(MAX_REGISTROS_EN_TOTAL):
            _alta(servicio, n, f"10.0.{n}.1")

        with pytest.raises(DemasiadosRegistros):
            _alta(servicio, 99, "10.9.9.9")

    def test_un_alta_fallida_no_gasta_cupo(self, servicio):
        _alta(servicio, 0, "1.1.1.1")
        for _ in range(10):
            with pytest.raises(Exception):
                servicio.registrar("persona0", "otra@ejemplo.co", BUENA, origen="1.1.1.1")

        _alta(servicio, 1, "1.1.1.1")
        _alta(servicio, 2, "1.1.1.1")

    def test_pasada_la_ventana_se_puede_otra_vez(self, servicio, monkeypatch):
        for n in range(MAX_REGISTROS_POR_ORIGEN):
            _alta(servicio, n, "1.1.1.1")

        despues = time.time() + modulo.VENTANA_INTENTOS_MINUTOS * 60 + 1
        monkeypatch.setattr(modulo.time, "time", lambda: despues)

        assert _alta(servicio, 50, "1.1.1.1")

    def test_sin_origen_no_se_frena(self, servicio):
        """El propietario al arrancar y las pruebas de servicio: no hay nadie de fuera."""
        for n in range(MAX_REGISTROS_EN_TOTAL + 5):
            servicio.registrar(f"interna{n}", f"interna{n}@ejemplo.co", BUENA)

    def test_teclear_la_clave_de_altas_al_entrar_no_cierra_el_registro(self, servicio):
        """Comparten tabla. Sin la guarda, veinte fallos con `#registro` en el
        campo de usuario cerrarían el registro a todo el mundo."""
        for i in range(MAX_REGISTROS_EN_TOTAL + 5):
            with pytest.raises(ErrorDeAutenticacion):
                servicio.iniciar_sesion(CLAVE_REGISTRO, "x", origen=f"10.0.{i}.1")

        assert _alta(servicio, 0, "1.1.1.1")

    def test_la_clave_de_altas_no_puede_ser_un_nombre_de_usuario(self, servicio):
        with pytest.raises(Exception):
            servicio.registrar(CLAVE_REGISTRO, "clave@ejemplo.co", BUENA)


# ── 3. Por HTTP, como se midió ─────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield create_app()
    reset_settings()
    dependencies.reset_container()


class TestPorHttp:
    def test_el_script_de_la_auditoria_ya_no_crea_treinta_cuentas(self, api):
        codigos = []
        for i in range(30):
            c = TestClient(api, base_url="https://testserver")
            r = c.post("/auth/registro", json={
                "username": f"bot{i}", "email": f"bot{i}@ejemplo.co", "password": BUENA,
            })
            codigos.append(r.status_code)

        assert codigos.count(200) == MAX_REGISTROS_POR_ORIGEN
        assert codigos[MAX_REGISTROS_POR_ORIGEN] == 429

    def test_el_429_se_explica(self, api):
        for i in range(MAX_REGISTROS_POR_ORIGEN + 1):
            r = TestClient(api, base_url="https://testserver").post("/auth/registro", json={
                "username": f"bot{i}", "email": f"bot{i}@ejemplo.co", "password": BUENA,
            })

        assert r.status_code == 429
        assert r.json()["error"]["code"] == "DEMASIADOS_REGISTROS"
        assert "minutos" in r.json()["error"]["message"]

    def test_falsear_x_forwarded_for_ya_no_da_contrasenas_infinitas(self, api):
        c = TestClient(api, base_url="https://testserver")
        c.post("/auth/registro", json={"username": "victima", "email": "v@ejemplo.co", "password": BUENA})

        codigos = [
            c.post("/auth/login", json={"identificador": "victima", "password": f"mala-{i}"},
                   headers={"X-Forwarded-For": f"10.0.{i}.1"}).status_code
            for i in range(MAX_INTENTOS_POR_CUENTA + 1)
        ]
        correcta = c.post("/auth/login", json={"identificador": "victima", "password": BUENA},
                          headers={"X-Forwarded-For": "10.9.9.9"}).status_code

        assert 429 in codigos
        assert correcta == 429


class TestLaSondaDelOrigen:
    """`/diagnostico/origen` (auditoría 2.3): mide qué origen cuentan los frenos
    en producción, detrás de Vercel y Render. Tiene que responder **sin sesión**
    —es el caso del registro y del inicio de sesión— y decir el mismo origen que
    usan los frenos, no otro calculado aparte."""

    def test_responde_sin_sesion_con_el_origen_que_cuentan_los_frenos(self, api):
        web = TestClient(api, base_url="https://testserver")

        r = web.get("/diagnostico/origen", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["origen_contado"] == "203.0.113.7"
        assert cuerpo["cabeceras"]["x-forwarded-for"] == "203.0.113.7, 10.0.0.1"
        assert set(cuerpo) == {"origen_contado", "cliente_directo", "cabeceras"}

    def test_por_la_web_cuenta_la_ip_que_pone_vercel(self, api):
        """Medido en producción: Vercel pone la IP real en x-vercel-forwarded-for,
        y cf-connecting-ip es su IP de salida, compartida por mucha gente."""
        web = TestClient(api, base_url="https://testserver")

        r = web.get("/diagnostico/origen", headers={
            "X-Forwarded-For": "186.151.100.196,54.226.252.221",
            "X-Vercel-Forwarded-For": "186.151.100.196",
            "CF-Connecting-IP": "54.226.252.221",
        })

        assert r.json()["origen_contado"] == "186.151.100.196"

    def test_directo_no_cuenta_un_x_forwarded_for_inventado(self, api):
        """Medido en producción: directo contra Render, un X-Forwarded-For
        inventado llegaba el primero y era lo que contaban los frenos. Cloudflare
        pone la IP real en cf-connecting-ip y el cliente no puede imponerla."""
        web = TestClient(api, base_url="https://testserver")

        r = web.get("/diagnostico/origen", headers={
            "X-Forwarded-For": "6.6.6.6,186.151.100.196",
            "CF-Connecting-IP": "186.151.100.196",
        })

        assert r.json()["origen_contado"] == "186.151.100.196"


class TestUnAltaNoRepiteElInicioDeSesion:
    """Medido en un despliegue como el de producción (2.3-C, V2.0.36): un alta hacía
    14 consultas a Supabase y **dos** hashes de la contraseña en 0,15 de CPU, porque
    la ruta hacía un inicio de sesión completo sobre la cuenta recién creada. Con 25
    altas a la vez el servidor se reinició. La sesión se abre ahora sin volver a
    comprobar lo que se acaba de guardar (V2.0.37)."""

    def test_un_solo_hash_y_sin_volver_a_buscar_la_cuenta(self, api, monkeypatch):
        from src.identidad import password as pw
        from src.identidad.repositorio import CuentasSQLite

        hashes = []
        scrypt_real = pw.hashlib.scrypt
        monkeypatch.setattr(pw.hashlib, "scrypt", lambda *a, **k: hashes.append(1) or scrypt_real(*a, **k))
        busquedas = []
        buscar_real = CuentasSQLite.buscar
        monkeypatch.setattr(CuentasSQLite, "buscar", lambda self, *a, **k: busquedas.append(a) or buscar_real(self, *a, **k))

        web = TestClient(api, base_url="https://testserver")
        r = web.post("/auth/registro", headers={"User-Agent": "Navegador de prueba"}, json={
            "username": "nueva", "email": "nueva@ejemplo.co", "password": "contrasena-larga",
        })

        assert r.status_code == 200, r.text
        assert len(hashes) == 1, f"el alta calculó {len(hashes)} hashes de la contraseña"
        assert busquedas == [], "el alta volvió a buscar la cuenta que acababa de crear"

        # Y la sesión que abre funciona, y ahora sí sabe desde qué navegador.
        web.headers["X-Morgan-CSRF"] = r.json()["csrf"]
        assert web.get("/auth/yo").json()["autenticado"] is True
        sesiones = web.get("/auth/sesiones").json()["sesiones"]
        assert sesiones and sesiones[0]["user_agent"] == "Navegador de prueba"
