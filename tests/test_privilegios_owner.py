"""
Qué se le levanta al propietario, y qué no (identidad, V2.0 adelantada).

El dueño de Morgan no debe encontrarse con los topes que existen para repartir un
recurso que **él paga**. Pero «más privilegio» no es «menos seguridad», y la
diferencia entre las dos cosas es lo que fijan estas pruebas.

Se levantan:

- El cupo diario de llamadas al modelo.
- Los límites de archivos subidos: número, tamaño y espacio.
- Las confirmaciones de herramientas, que en un entorno sin consola no se pueden
  ni formular y hoy acaban denegando.

**No** se levantan, y cada una por su motivo:

- La validación de comandos y rutas. No protege a Morgan del dueño; protege al
  dueño de que el **modelo** proponga una barbaridad por su cuenta. Quien escribe
  esos comandos es el LLM, no la persona.
- La auditoría. Cuanto mayor es el privilegio, más vale poder reconstruir qué pasó.
- Las herramientas que no existen en el entorno. En la nube no están registradas,
  y eso no es una restricción al dueño: esa máquina es un contenedor, no su PC.
"""

import pytest

from src.config import reset_settings
from src.identidad import como_usuario
from src.identidad.roles import Rol


class TestSinLimitesDeConsumo:
    def test_el_cupo_de_mensajes_no_le_aplica(self, tmp_path):
        from src.identidad.cuotas import ControlDeUso, Cuotas
        from src.memory.db import Database

        control = ControlDeUso(Database(tmp_path / "u.db"), Cuotas(mensajes=1))

        for _ in range(50):
            control.apuntar("duenyo", "mensajes", Rol.OWNER)

        assert control.consumo("duenyo")["mensajes"] == 0

    def test_a_un_administrador_si_le_aplica(self):
        """Administrar Morgan no es pagarlo, y no conviene que ayudar a moderar
        traiga barra libre de un recurso ajeno."""
        from src.identidad.roles import sin_cupo

        assert sin_cupo(Rol.OWNER)
        assert not sin_cupo(Rol.ADMIN)

    def _store(self, tmp_path):
        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.uploads import UploadStore
        from src.uploads.almacenamiento import AlmacenEnDisco

        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "up.db"))
        store = UploadStore(
            repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "b")
        )
        store.max_archivos = 2
        return store

    def test_sube_mas_archivos_que_el_limite(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        reset_settings()
        store = self._store(tmp_path)

        with como_usuario("duenyo", Rol.OWNER):
            for i in range(5):
                store.guardar(f"a{i}.txt", b"contenido")

            assert len(store.listar()) == 5

        reset_settings()

    def test_un_usuario_normal_si_encuentra_el_limite(self, tmp_path, monkeypatch):
        """La exención es del propietario, no de cualquiera."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        reset_settings()
        store = self._store(tmp_path)

        from src.uploads.store import ArchivoRechazado

        with como_usuario("ana", Rol.USER):
            store.guardar("a1.txt", b"contenido")
            store.guardar("a2.txt", b"contenido")
            with pytest.raises(ArchivoRechazado):
                store.guardar("a3.txt", b"contenido")

        reset_settings()


class TestSinConfirmaciones:
    """En la API no hay consola, así que «¿autorizas esto?» no se puede preguntar
    y se resuelve denegando. Para el dueño eso significaba no poder usar media
    herramienta de su propio Morgan."""

    @pytest.fixture
    def permisos(self, tmp_path):
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager

        return PermissionManager(
            audit_logger=AuditLogger(tmp_path / "audit.log"), interactive=False
        )

    @staticmethod
    def _herramienta(riesgo):
        from src.tools.base import Tool, ToolCategory

        class Falsa(Tool):
            @property
            def name(self):
                return "prueba"

            @property
            def description(self):
                return "una herramienta de mentira"

            @property
            def parameters(self):
                return {"type": "object", "properties": {}}

            @property
            def category(self):
                return ToolCategory.SYSTEM

            @property
            def risk_level(self):
                return riesgo

            def execute(self, **kwargs):
                return {"success": True}

        return Falsa()

    @pytest.mark.parametrize("riesgo", ["moderate", "high_risk", "critical"])
    def test_al_propietario_se_le_ejecuta(self, permisos, riesgo):
        with como_usuario("duenyo", Rol.OWNER):
            assert permisos.check_permission(self._herramienta(riesgo)) is True

    @pytest.mark.parametrize("riesgo", ["moderate", "high_risk", "critical"])
    def test_a_un_usuario_normal_no(self, permisos, riesgo):
        with como_usuario("ana", Rol.USER):
            assert permisos.check_permission(self._herramienta(riesgo)) is False

    def test_lo_seguro_sigue_siendo_seguro_para_todos(self, permisos):
        with como_usuario("ana", Rol.USER):
            assert permisos.check_permission(self._herramienta("safe")) is True

    def test_queda_auditado(self, permisos, tmp_path):
        """Cuanto mayor es el privilegio, más vale poder reconstruir qué pasó."""
        with como_usuario("duenyo", Rol.OWNER):
            permisos.check_permission(self._herramienta("critical"))

        assert "prueba" in (tmp_path / "audit.log").read_text(encoding="utf-8")


class TestLoQueNoSeLevantaNiSiendoPropietario:
    @pytest.fixture
    def permisos(self, tmp_path):
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager

        return PermissionManager(
            audit_logger=AuditLogger(tmp_path / "audit.log"), interactive=False
        )

    def test_un_comando_destructivo_se_rechaza_igual(self, permisos):
        """Lo que impide el validador no es que el dueño mande: es que el
        **modelo** proponga borrar un disco por su cuenta. Quien escribe el
        comando es el LLM, no la persona, así que quitarlo por ser dueño sería
        confundir quién manda con quién teclea.
        """
        from src.tools.terminal import ExecuteCommandTool

        with como_usuario("duenyo", Rol.OWNER):
            permitido = permisos.check_permission(
                ExecuteCommandTool(), {"command": "diskpart /s borrar.txt"}
            )

        assert permitido is False

    def test_una_ruta_protegida_se_rechaza_igual(self, permisos):
        from src.tools.filesystem import DeleteFileTool

        with como_usuario("duenyo", Rol.OWNER):
            permitido = permisos.check_permission(
                DeleteFileTool(), {"path": "C:\\Windows"}
            )

        assert permitido is False

    def test_una_herramienta_bloqueada_sigue_bloqueada(self, permisos, tmp_path):
        """Bloquear algo a mano es una decisión explícita, y el privilegio no
        debe deshacerla por su cuenta."""
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager

        gestor = PermissionManager(
            audit_logger=AuditLogger(tmp_path / "a.log"), interactive=False
        )
        gestor.block_tool("prueba")

        with como_usuario("duenyo", Rol.OWNER):
            assert gestor.check_permission(TestSinConfirmaciones._herramienta("safe")) is False

    def test_en_la_nube_las_herramientas_locales_siguen_sin_existir(self, monkeypatch):
        """No es una restricción al dueño: esa máquina es un contenedor, no su
        PC. Dárselas no le daría acceso a su ordenador, se lo daría al servidor.
        """
        monkeypatch.setenv("MORGAN_ENVIRONMENT", "cloud")
        monkeypatch.setenv("MORGAN_API_TOKEN", "x")
        reset_settings()

        from src.api import dependencies

        dependencies.reset_container()
        registro = dependencies.get_container().tool_registry

        with como_usuario("duenyo", Rol.OWNER):
            locales = [t.name for t in registro.list_tools() if t.requires_local]

        assert locales == []

        reset_settings()
        dependencies.reset_container()


class TestElContextoLlevaElRol:
    """Se guarda junto al usuario en lugar de consultarlo: hace falta una vez por
    herramienta ejecutada, y un viaje a la base por comprobación sería absurdo."""

    def test_el_usuario_local_manda_en_su_equipo(self):
        from src.identidad import rol_actual

        assert rol_actual() is Rol.OWNER

    def test_sin_rol_explicito_se_asume_el_menor(self):
        """Quien no dice qué rol tiene, no tiene ninguno especial."""
        from src.identidad import rol_actual

        with como_usuario("ana"):
            assert rol_actual() is Rol.USER

    def test_se_restaura_al_salir(self):
        from src.identidad import rol_actual

        with como_usuario("ana", Rol.USER):
            pass

        assert rol_actual() is Rol.OWNER

    def test_sin_identidad_disponible_se_asume_lo_restrictivo(self, monkeypatch):
        """Si la capa de identidad falla, la respuesta es que **sí** hacen falta
        confirmaciones. Es el lado seguro del error."""
        import src.identidad.roles as roles
        from src.security.permissions import PermissionManager

        def romper(*a, **k):
            raise RuntimeError("identidad no disponible")

        monkeypatch.setattr(roles, "sin_confirmaciones", romper)

        assert PermissionManager._sin_confirmaciones() is False


class TestElModoSinCuentasNoEsUnPropietarioConPrivilegios:
    """La distinción que costó diez pruebas descubrir.

    El usuario implícito tiene rol de propietario porque en tu equipo mandas tú,
    pero eso es el **modo sin cuentas**, no una identidad con privilegios: en la
    CLI hay una consola con la que preguntar, y las confirmaciones ahí no
    estorban, protegen de que el modelo se equivoque.

    Sin esta distinción, la exención se aplicaba también al Morgan de escritorio
    y a la CLI, que es justo donde no debe.
    """

    def test_el_usuario_implicito_no_se_salta_las_confirmaciones(self, tmp_path):
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager

        gestor = PermissionManager(
            audit_logger=AuditLogger(tmp_path / "a.log"), interactive=False
        )

        # Sin `como_usuario`: es el contexto por defecto, el del Morgan local.
        assert gestor.check_permission(TestSinConfirmaciones._herramienta("critical")) is False

    def test_el_propietario_con_cuenta_si(self, tmp_path):
        from src.security.audit import AuditLogger
        from src.security.permissions import PermissionManager

        gestor = PermissionManager(
            audit_logger=AuditLogger(tmp_path / "a.log"), interactive=False
        )

        with como_usuario("usr-real", Rol.OWNER):
            assert gestor.check_permission(TestSinConfirmaciones._herramienta("critical")) is True

    def test_los_limites_de_archivos_siguen_en_el_morgan_de_escritorio(
        self, tmp_path, monkeypatch
    ):
        """Protegen tu disco de que Morgan lo llene, y eso vale igual en tu
        equipo."""
        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        reset_settings()

        from src.memory.db import Database
        from src.memory.sqlite_repositories import SQLiteRepositoryFactory
        from src.uploads import UploadStore
        from src.uploads.almacenamiento import AlmacenEnDisco
        from src.uploads.store import ArchivoRechazado

        fabrica = SQLiteRepositoryFactory(Database(tmp_path / "up.db"))
        store = UploadStore(
            repositorio=fabrica.uploads, almacen=AlmacenEnDisco(tmp_path / "b")
        )
        store.max_archivos = 2

        store.guardar("a1.txt", b"contenido")
        store.guardar("a2.txt", b"contenido")
        with pytest.raises(ArchivoRechazado):
            store.guardar("a3.txt", b"contenido")

        reset_settings()

    def test_la_distincion_esta_en_una_sola_funcion(self):
        """Para que no se reparta por el código, que es como se olvida."""
        from src.identidad.modelos import USUARIO_LOCAL
        from src.identidad.roles import propietario_con_cuenta

        assert propietario_con_cuenta("usr-real", Rol.OWNER) is True
        assert propietario_con_cuenta(USUARIO_LOCAL, Rol.OWNER) is False
        assert propietario_con_cuenta("usr-real", Rol.ADMIN) is False
