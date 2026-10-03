"""
Roles, propietario y autorización (identidad, V2.0 adelantada).

La regla que gobierna todo esto: **la autorización se decide por permisos, nunca
por quién eres**. Nada de `if user.email == "..."`. Estas pruebas comprueban las
dos mitades de esa promesa —que el permiso manda, y que nadie puede darse uno a
sí mismo— además del arranque del propietario, que solo debe ocurrir una vez.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import reset_settings
from src.identidad.roles import Permiso, Rol, permisos_de, puede, sin_cupo

CSRF = "x-morgan-csrf"
CLAVE = "contrasena-larga"


# --- El modelo, sin HTTP de por medio ----------------------------------------


class TestElModeloDeRoles:
    def test_la_escala_es_acumulativa(self):
        """Lo que puede un admin lo puede un propietario. Sin esto aparecen
        sorpresas del tipo «el dueño no puede hacer algo que sí puede el
        moderador»."""
        assert permisos_de(Rol.ADMIN) <= permisos_de(Rol.OWNER)
        assert permisos_de(Rol.USER) <= permisos_de(Rol.ADMIN)

    def test_un_usuario_normal_no_tiene_permisos_administrativos(self):
        assert permisos_de(Rol.USER) == frozenset()

    @pytest.mark.parametrize("basura", ["", None, "superadmin", "owner-", "root", "1"])
    def test_lo_desconocido_cae_al_rol_de_menos_privilegio(self, basura):
        """Fail-safe. Una fila corrupta, un rol de una versión futura o un valor
        escrito a mano dan `user`. Lo contrario convertiría cualquier error de
        datos en una escalada de privilegios."""
        assert Rol.desde(basura) is Rol.USER

    def test_el_rol_se_reconoce_pese_a_mayusculas_y_espacios(self):
        """Pegar un valor en un panel web arrastra espacios, y un rol es un dato
        de configuración: recortarlos evita un fallo que no enseña nada."""
        assert Rol.desde("OWNER") is Rol.OWNER
        assert Rol.desde(" Admin ") is Rol.ADMIN
        assert Rol.desde("owner ") is Rol.OWNER

    def test_solo_el_propietario_esta_exento_de_cupo(self):
        """Administrar Morgan no es pagarlo. El cupo existe para repartir un
        recurso compartido, y quien lo paga es el dueño, no quien modera."""
        assert sin_cupo(Rol.OWNER)
        assert not sin_cupo(Rol.ADMIN)
        assert not sin_cupo(Rol.USER)

    def test_gestionar_usuarios_es_solo_del_propietario(self):
        assert puede(Rol.OWNER, Permiso.USUARIOS_GESTIONAR)
        assert not puede(Rol.ADMIN, Permiso.USUARIOS_GESTIONAR)
        assert puede(Rol.ADMIN, Permiso.USUARIOS_LEER)


class TestElUsuarioLocalMandaEnSuEquipo:
    """El Morgan de escritorio no tiene cuentas: pedirle permisos a uno mismo en
    su propia máquina no protege de nada."""

    def test_es_propietario_de_hecho(self):
        from src.identidad.modelos import usuario_local

        assert usuario_local().rol_efectivo is Rol.OWNER

    def test_una_cuenta_normal_no_hereda_eso(self):
        from src.identidad.modelos import MorganUser

        assert MorganUser(id="usr-1").rol_efectivo is Rol.USER


# --- Por HTTP ----------------------------------------------------------------


@pytest.fixture
def nube(monkeypatch):
    """Morgan en la nube, con una cuenta configurada como propietaria."""
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_WEB_URL", "https://morgan.ejemplo.co")
    monkeypatch.setenv("MORGAN_OWNER_EMAIL", "andy@ejemplo.co")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()


def _registrar(cliente, nombre, correo=None) -> str:
    cliente.cookies.clear()
    respuesta = cliente.post("/auth/registro", json={
        "username": nombre,
        "email": correo or f"{nombre}@ejemplo.co",
        "password": CLAVE,
    })
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["csrf"]


def _entrar(cliente, nombre) -> str:
    cliente.cookies.clear()
    respuesta = cliente.post(
        "/auth/login", json={"identificador": nombre, "password": CLAVE}
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["csrf"]


def _promocionar_al_propietario(cliente):
    """Rehace el contenedor, que es lo que dispara el bootstrap al arrancar."""
    from src.api import dependencies

    dependencies.reset_container()
    dependencies.get_container()


def _id_de(cliente, nombre) -> str:
    usuarios = cliente.get("/admin/usuarios").json()["usuarios"]
    return next(u["id"] for u in usuarios if u["username"] == nombre)


class TestElArranqueDelPropietario:
    def test_registrarse_no_convierte_a_nadie_en_propietario(self, nube):
        _registrar(nube, "andy")

        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "user"

    def test_el_arranque_promociona_la_cuenta_configurada(self, nube):
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)

        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "owner"

    def test_no_promociona_a_quien_no_toca(self, nube):
        _registrar(nube, "bruno")
        _promocionar_al_propietario(nube)

        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "user"

    def test_no_crea_un_segundo_propietario(self, nube, monkeypatch):
        """Reiniciar el servidor no puede ser una forma de traspasar la
        propiedad de la instalación."""
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)

        # La variable pasa a señalar a otra persona, que ademas existe.
        _registrar(nube, "bruno")
        monkeypatch.setenv("MORGAN_OWNER_EMAIL", "bruno@ejemplo.co")
        reset_settings()
        _promocionar_al_propietario(nube)

        _entrar(nube, "andy")
        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "owner"

        _entrar(nube, "bruno")
        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "user"

    def test_repetirlo_no_cambia_nada(self, nube):
        """Se ejecuta en cada reinicio, así que tiene que ser idempotente."""
        _registrar(nube, "andy")
        for _ in range(3):
            _promocionar_al_propietario(nube)

        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "owner"

    def test_sin_cuenta_todavia_no_falla(self, nube):
        """La variable puede estar puesta antes de que nadie se registre. Eso no
        es un error: se espera."""
        _promocionar_al_propietario(nube)
        _registrar(nube, "bruno")

        assert nube.get("/admin/usuarios").status_code == 403


class TestNadieSeElevaASiMismo:
    """La parte que de verdad importa: que el rol lo decida siempre el servidor."""

    def test_un_usuario_normal_no_entra_en_administracion(self, nube):
        _registrar(nube, "bruno")

        respuesta = nube.get("/admin/usuarios")

        assert respuesta.status_code == 403
        assert respuesta.json()["error"]["code"] == "PERMISO_DENEGADO"

    def test_pedir_un_rol_en_el_registro_no_sirve_de_nada(self, nube):
        """El campo ni se mira; se comprueba que tampoco cuela por accidente."""
        nube.cookies.clear()
        nube.post("/auth/registro", json={
            "username": "listo", "email": "listo@ejemplo.co",
            "password": CLAVE, "role": "owner", "rol": "owner", "is_admin": True,
        })

        assert nube.get("/auth/yo").json()["usuario"]["rol"] == "user"

    def test_no_puede_cambiar_roles_ajenos(self, nube):
        csrf = _registrar(nube, "bruno")

        respuesta = nube.post(
            "/admin/usuarios/cualquiera/rol", json={"rol": "admin"},
            headers={CSRF: csrf},
        )

        assert respuesta.status_code == 403

    def test_el_propietario_no_puede_cambiarse_el_suyo(self, nube):
        """Ni para subir ni para bajar. Es la vía por la que una cuenta
        comprometida se consolidaría."""
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)
        csrf = _entrar(nube, "andy")
        yo = _id_de(nube, "andy")

        respuesta = nube.post(
            f"/admin/usuarios/{yo}/rol", json={"rol": "user"}, headers={CSRF: csrf}
        )

        assert respuesta.status_code == 400
        assert respuesta.json()["error"]["code"] == "ROL_PROPIO"


class TestLoQueElPropietarioSiPuede:
    @pytest.fixture
    def propietario(self, nube):
        _registrar(nube, "andy")
        _registrar(nube, "bruno")
        _promocionar_al_propietario(nube)
        return _entrar(nube, "andy")

    def test_ve_las_cuentas(self, nube, propietario):
        usuarios = nube.get("/admin/usuarios").json()["usuarios"]

        assert {u["username"] for u in usuarios} == {"andy", "bruno"}

    def test_la_lista_no_publica_hashes_ni_tokens(self, nube, propietario):
        """Se construye por lista blanca, no quitando campos: así una columna
        nueva en la tabla no se publica sola."""
        cuerpo = nube.get("/admin/usuarios").text

        for prohibido in ("password", "scrypt", "token", "hash"):
            assert prohibido not in cuerpo.lower()

    def test_promociona_a_otro_a_administrador(self, nube, propietario):
        bruno = _id_de(nube, "bruno")

        respuesta = nube.post(
            f"/admin/usuarios/{bruno}/rol", json={"rol": "admin"},
            headers={CSRF: propietario},
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["usuario"]["rol"] == "admin"

    def test_no_puede_crear_un_segundo_propietario(self, nube, propietario):
        bruno = _id_de(nube, "bruno")

        respuesta = nube.post(
            f"/admin/usuarios/{bruno}/rol", json={"rol": "owner"},
            headers={CSRF: propietario},
        )

        assert respuesta.status_code == 400
        assert respuesta.json()["error"]["code"] == "OWNER_UNICO"

    @pytest.mark.parametrize("rol", ["superadmin", "root", "", "OWNER"])
    def test_un_rol_inventado_se_rechaza_en_vez_de_degradar(self, nube, propietario, rol):
        """`Rol.desde` convierte lo desconocido en `user`, que está bien al leer
        de la base pero no al recibir una orden: aquí hay que decir que el valor
        no vale, no degradar a alguien por una errata."""
        bruno = _id_de(nube, "bruno")

        respuesta = nube.post(
            f"/admin/usuarios/{bruno}/rol", json={"rol": rol},
            headers={CSRF: propietario},
        )

        assert respuesta.status_code == 400

    def test_una_cuenta_inexistente_da_404(self, nube, propietario):
        respuesta = nube.post(
            "/admin/usuarios/no-existe/rol", json={"rol": "admin"},
            headers={CSRF: propietario},
        )

        assert respuesta.status_code == 404


class TestElAdministradorLeePeroNoGestiona:
    @pytest.fixture
    def admin(self, nube):
        _registrar(nube, "andy")
        _registrar(nube, "bruno")
        _promocionar_al_propietario(nube)
        csrf_owner = _entrar(nube, "andy")
        bruno = _id_de(nube, "bruno")
        nube.post(f"/admin/usuarios/{bruno}/rol", json={"rol": "admin"},
                  headers={CSRF: csrf_owner})
        return _entrar(nube, "bruno")

    def test_puede_ver_las_cuentas(self, nube, admin):
        assert nube.get("/admin/usuarios").status_code == 200

    def test_no_puede_cambiar_roles(self, nube, admin):
        andy = _id_de(nube, "andy")

        respuesta = nube.post(
            f"/admin/usuarios/{andy}/rol", json={"rol": "user"}, headers={CSRF: admin}
        )

        assert respuesta.status_code == 403

    def test_no_puede_tocar_al_propietario(self, nube, admin):
        andy = _id_de(nube, "andy")

        respuesta = nube.post(
            f"/admin/usuarios/{andy}/estado", json={"status": "suspendido"},
            headers={CSRF: admin},
        )

        assert respuesta.status_code == 403


class TestSuspenderEchaDeVerdad:
    def test_la_sesion_abierta_cae_en_el_momento(self, nube):
        """Suspender a alguien que sigue dentro no es suspender a nadie. Sin
        esto, la sesión viviría treinta días más."""
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)
        _registrar(nube, "carla")
        galletas = dict(nube.cookies)
        assert nube.get("/auth/yo").json()["autenticado"] is True

        csrf = _entrar(nube, "andy")
        carla = _id_de(nube, "carla")
        nube.post(f"/admin/usuarios/{carla}/estado", json={"status": "suspendido"},
                  headers={CSRF: csrf})

        nube.cookies.clear()
        for nombre, valor in galletas.items():
            nube.cookies.set(nombre, valor)

        assert nube.get("/auth/yo").json()["autenticado"] is False

    def test_al_propietario_no_se_le_puede_suspender(self, nube):
        """Dejaría la instalación sin nadie que pueda administrarla."""
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)
        csrf = _entrar(nube, "andy")
        andy = _id_de(nube, "andy")

        respuesta = nube.post(
            f"/admin/usuarios/{andy}/estado", json={"status": "suspendido"},
            headers={CSRF: csrf},
        )

        assert respuesta.status_code == 400

    @pytest.mark.parametrize("estado", ["borrado", "", "eliminado"])
    def test_un_estado_inventado_se_rechaza(self, nube, estado):
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)
        _registrar(nube, "carla")
        csrf = _entrar(nube, "andy")
        carla = _id_de(nube, "carla")

        respuesta = nube.post(
            f"/admin/usuarios/{carla}/estado", json={"status": estado},
            headers={CSRF: csrf},
        )

        assert respuesta.status_code == 400


class TestLaAuditoriaRegistraLoAdministrativo:
    def test_queda_quien_hizo_que_y_sobre_quien(self, nube):
        _registrar(nube, "andy")
        _registrar(nube, "bruno")
        _promocionar_al_propietario(nube)
        csrf = _entrar(nube, "andy")
        bruno = _id_de(nube, "bruno")
        andy = _id_de(nube, "andy")

        nube.post(f"/admin/usuarios/{bruno}/rol", json={"rol": "admin"},
                  headers={CSRF: csrf})

        from src.api import dependencies

        registro = dependencies.get_container().audit_logger
        eventos = [
            e for e in registro.get_recent(50)
            if e.get("accion") == "user.update_role"
        ]

        assert eventos, "el cambio de rol no quedó registrado"
        assert eventos[0]["actor"] == andy
        assert eventos[0]["objetivo"] == bruno
        assert "user -> admin" in eventos[0]["detalle"]

    def test_la_promocion_inicial_tambien(self, nube):
        """Es la operación más privilegiada de toda la vida de una instalación."""
        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)

        from src.api import dependencies

        acciones = [e.get("accion") for e in dependencies.get_container().audit_logger.get_recent(50)]

        assert "owner.bootstrap" in acciones


class TestElAislamientoSigueIntacto:
    """Añadir roles no puede abrir una vía para ver los datos de otro. Ser
    propietario da permisos administrativos **explícitos**, no acceso universal a
    las conversaciones de nadie."""

    def test_el_propietario_no_ve_las_conversaciones_ajenas(self, nube):
        csrf_bruno = _registrar(nube, "bruno")
        nube.post("/sessions", json={"title": "Privado de Bruno"},
                  headers={CSRF: csrf_bruno})

        _registrar(nube, "andy")
        _promocionar_al_propietario(nube)
        _entrar(nube, "andy")

        titulos = [s["title"] for s in nube.get("/sessions").json()["sessions"]]

        assert "Privado de Bruno" not in titulos


class TestLaBaseGarantizaUnSoloPropietario:
    """La comprobación del código puede olvidarse en una vía nueva. La de la base
    no: es un índice único parcial sobre `role = 'owner'`."""

    def test_no_se_pueden_insertar_dos(self, tmp_path):
        import time

        from src.memory.db import Database, MemoryStorageError

        db = Database(tmp_path / "dos.db")

        def meter(identificador, rol):
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO morgan_users (id, username, role, creado_en) "
                    "VALUES (?, ?, ?, ?)",
                    (identificador, identificador, rol, time.time()),
                )

        meter("primero", "owner")

        with pytest.raises(MemoryStorageError):
            meter("segundo", "owner")

    def test_varios_administradores_si(self, tmp_path):
        """La restricción es solo para el propietario: administradores puede
        haber los que hagan falta."""
        import time

        from src.memory.db import Database

        db = Database(tmp_path / "admins.db")
        with db.connect() as conn:
            for nombre in ("uno", "dos", "tres"):
                conn.execute(
                    "INSERT INTO morgan_users (id, username, role, creado_en) "
                    "VALUES (?, ?, 'admin', ?)",
                    (nombre, nombre, time.time()),
                )

        with db.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM morgan_users WHERE role = 'admin'"
            ).fetchone()[0]

        assert n == 3
