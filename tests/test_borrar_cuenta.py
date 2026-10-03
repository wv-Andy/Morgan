"""
Eliminar la cuenta y sus datos.

Es la operación más destructiva que ofrece Morgan y **no tiene deshacer**, así
que lo que se comprueba aquí no es que funcione: es que no se pase de largo.

Cuatro cosas que salieron de pensar cómo puede acabar mal:

1. **La contraseña se comprueba antes de borrar nada.** Al revés, equivocarse al
   escribirla dejaría a alguien sin sus conversaciones y con la cuenta intacta.
2. **Primero los datos, después la cuenta.** Al revés, un fallo a medias deja
   filas cuyo `user_id` no corresponde a nadie; con este orden deja una cuenta
   viva y vacía.
3. **Solo se borra lo propio.** No se acepta ningún identificador: si lo
   aceptara, sería una ruta para borrar la cuenta de otro.
4. **El propietario no puede borrarse.** Dejaría la instalación sin nadie que
   la administre, y el propietario se establece con una variable de entorno al
   arrancar: no habría forma de nombrar otro desde dentro.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.sesion_web import CABECERA_CSRF
from src.config import reset_settings

REGISTRO = {
    "username": "ana",
    "email": "ana@ejemplo.co",
    "password": "contrasena-larga",
}


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.delenv("MORGAN_OWNER_EMAIL", raising=False)
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()
    dependencies.reset_container()


def _entrar(cliente, datos=None) -> str:
    respuesta = cliente.post("/auth/registro", json=datos or REGISTRO)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["csrf"]


def _borrar(cliente, csrf: str, password: str):
    return cliente.request(
        "DELETE", "/auth/cuenta",
        json={"password": password},
        headers={CABECERA_CSRF: csrf},
    )


class TestHaceFaltaConfirmarConLaContrasena:
    """Una sesión abierta en un equipo prestado basta para que otra persona lo
    borre todo de un clic. Por eso se pide igualmente."""

    def test_sin_sesion_no_se_pasa(self, cliente):
        assert cliente.request("DELETE", "/auth/cuenta", json={"password": "x"}).status_code == 401

    def test_con_la_contrasena_equivocada_se_rechaza(self, cliente):
        csrf = _entrar(cliente)

        respuesta = _borrar(cliente, csrf, "no-es-esta")

        assert respuesta.status_code == 401

    def test_y_la_cuenta_sigue_ahi(self, cliente):
        """Lo importante del caso anterior: que no se haya borrado nada."""
        csrf = _entrar(cliente)
        _borrar(cliente, csrf, "no-es-esta")

        assert cliente.get("/auth/yo").json()["autenticado"] is True

    def test_con_la_correcta_se_borra(self, cliente):
        csrf = _entrar(cliente)

        respuesta = _borrar(cliente, csrf, REGISTRO["password"])

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["success"] is True


class TestDespuesDeBorrarNoSePuedeVolver:
    def test_la_sesion_deja_de_valer(self, cliente):
        csrf = _entrar(cliente)
        _borrar(cliente, csrf, REGISTRO["password"])

        assert cliente.get("/sessions").status_code == 401

    def test_no_se_puede_iniciar_sesion_de_nuevo(self, cliente):
        csrf = _entrar(cliente)
        _borrar(cliente, csrf, REGISTRO["password"])

        respuesta = cliente.post(
            "/auth/login",
            json={"identificador": REGISTRO["username"], "password": REGISTRO["password"]},
        )

        assert respuesta.status_code == 401

    def test_el_nombre_queda_libre(self, cliente):
        """Si la fila no se borrara de verdad, registrarse otra vez daría
        «ya existe» — y sería la señal de que quedó algo."""
        csrf = _entrar(cliente)
        _borrar(cliente, csrf, REGISTRO["password"])

        assert cliente.post("/auth/registro", json=REGISTRO).status_code == 200


class TestLosDatosSeVanConLaCuenta:
    """Dejar conversaciones y recuerdos de una cuenta que ya no existe no es
    solo desorden: un identificador reutilizado los haría reaparecer en la
    cuenta de otra persona."""

    def test_las_conversaciones_se_borran(self, cliente):
        csrf = _entrar(cliente)
        cliente.post(
            "/sessions",
            json={"session_id": "mia", "title": "Mía"},
            headers={CABECERA_CSRF: csrf},
        )

        respuesta = _borrar(cliente, csrf, REGISTRO["password"])

        assert respuesta.json()["borrado"]["conversaciones"] >= 1

    def test_los_recuerdos_tambien(self, cliente):
        csrf = _entrar(cliente)
        cliente.post(
            "/memory",
            json={"key": "algo", "value": "mío"},
            headers={CABECERA_CSRF: csrf},
        )

        respuesta = _borrar(cliente, csrf, REGISTRO["password"])

        assert respuesta.json()["borrado"]["recuerdos"] >= 1

    def test_quien_entra_despues_con_el_mismo_nombre_no_los_hereda(self, cliente):
        """La comprobación que de verdad importa: que no reaparezcan."""
        csrf = _entrar(cliente)
        cliente.post(
            "/sessions",
            json={"session_id": "mia", "title": "Mía"},
            headers={CABECERA_CSRF: csrf},
        )
        _borrar(cliente, csrf, REGISTRO["password"])

        nuevo_csrf = _entrar(cliente)
        sesiones = cliente.get("/sessions").json()["sessions"]
        recuerdos = cliente.get("/memory").json()["memories"]

        assert nuevo_csrf
        assert sesiones == []
        assert recuerdos == []


class TestNoSePuedeBorrarLaCuentaDeOtro:
    def test_un_identificador_en_el_cuerpo_se_ignora(self, cliente):
        """La ruta borra la cuenta de quien pide, y no lee ningún id del cuerpo.

        Se comprueba colando uno ajeno: si algún día alguien añadiera ese
        parámetro «por comodidad», esta prueba lo cazaría — sería una ruta para
        borrar la cuenta de otra persona.
        """
        csrf_bea = _entrar(cliente, {
            "username": "bea", "email": "bea@ejemplo.co", "password": "otra-contrasena",
        })
        cliente.post("/auth/logout", headers={CABECERA_CSRF: csrf_bea})

        csrf_ana = _entrar(cliente)
        cliente.request(
            "DELETE", "/auth/cuenta",
            json={"password": REGISTRO["password"], "user_id": "bea"},
            headers={CABECERA_CSRF: csrf_ana},
        )

        # Bea sigue entrando: su cuenta no se ha tocado.
        entrada = cliente.post(
            "/auth/login",
            json={"identificador": "bea", "password": "otra-contrasena"},
        )

        assert entrada.status_code == 200


class TestElPropietarioNoSePuedeBorrar:
    """Dejaría este Morgan sin nadie que administre roles ni cuentas, y el
    propietario se establece con una variable de entorno al arrancar: no habría
    forma de nombrar otro desde dentro."""

    @staticmethod
    def _hacer_propietario(cliente) -> str:
        """Registra y promueve, que es el estado real de una cuenta dueña.

        La promoción por  ocurre **al arrancar**, así que
        una cuenta creada después no la recibe hasta el siguiente reinicio. Aquí
        se hace a mano para reproducir el estado, no el camino.
        """
        from src.api.dependencies import get_container
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.identidad.roles import Rol

        csrf = _entrar(cliente)
        repo = repositorio_de_cuentas(get_container().repositories)
        fila = repo.buscar(REGISTRO["username"])
        repo.actualizar(fila["id"], {"role": Rol.OWNER.value})
        return csrf

    def test_se_rechaza_con_409(self, cliente):
        csrf = self._hacer_propietario(cliente)

        respuesta = _borrar(cliente, csrf, REGISTRO["password"])

        assert respuesta.status_code == 409
        assert respuesta.json()["error"]["code"] == "CUENTA_PROTEGIDA"

    def test_y_sus_datos_siguen_intactos(self, cliente):
        """El rechazo tiene que ocurrir ANTES de tocar nada."""
        csrf = self._hacer_propietario(cliente)
        cliente.post(
            "/sessions",
            json={"session_id": "mia", "title": "Mía"},
            headers={CABECERA_CSRF: csrf},
        )

        _borrar(cliente, csrf, REGISTRO["password"])

        assert len(cliente.get("/sessions").json()["sessions"]) == 1


class TestQuedaRegistrado:
    def test_la_auditoria_lo_apunta(self, cliente):
        """Borrar una cuenta es crítico. Que no quede rastro de que ocurrió
        sería peor que el propio borrado."""
        from src.api.dependencies import get_container

        csrf = _entrar(cliente)
        _borrar(cliente, csrf, REGISTRO["password"])

        eventos = get_container().audit_logger.get_recent(limit=20)

        assert any(e["tool"] == "delete_account" for e in eventos)
