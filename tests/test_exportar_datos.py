"""
Descargar todo lo que Morgan guarda de ti.

**Es el complemento de poder borrar la cuenta**, y las dos juntas son lo que
convierte «tus datos» en algo real: si solo se puede borrar, la única forma de
conservar una conversación es copiarla a mano; si solo se puede exportar, irse
significa dejarlo todo ahí.

Lo que estas pruebas vigilan, por orden de lo que costaría equivocarse:

1. **Lo que NO sale.** Un fichero de exportación acaba en la carpeta de
   descargas, se comparte por correo y se sube a sitios. Meter ahí una
   credencial es regalar material para atacarla sin prisa.
2. **Solo lo propio.** La ruta no acepta identificador: si lo aceptara, sería
   una forma de leer los datos de otro.
3. **Que un fallo parcial se diga.** Una exportación incompleta que parece
   completa es peor que ninguna.
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
    monkeypatch.setenv("MORGAN_SECRET_KEY", "clave-de-prueba")
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()
    dependencies.reset_container()


def _entrar(cliente) -> str:
    return cliente.post("/auth/registro", json=REGISTRO).json()["csrf"]


class TestHaceFaltaCuenta:
    def test_sin_sesion_no_se_pasa(self, cliente):
        assert cliente.get("/auth/datos").status_code == 401


class TestSaleLoQueEsTuyo:
    def test_la_cuenta_y_los_bloques_esperados(self, cliente):
        _entrar(cliente)

        datos = cliente.get("/auth/datos").json()["datos"]

        assert datos["cuenta"]["email"] == REGISTRO["email"]
        for bloque in ("conversaciones", "recuerdos", "archivos", "tareas", "planes"):
            assert bloque in datos, f"falta el bloque {bloque}"

    def test_las_conversaciones_llevan_sus_mensajes(self, cliente):
        """Una conversación sin sus mensajes no es una conversación exportada,
        es una lista de títulos."""
        csrf = _entrar(cliente)
        cliente.post(
            "/sessions", json={"session_id": "s1", "title": "Una charla"},
            headers={CABECERA_CSRF: csrf},
        )

        datos = cliente.get("/auth/datos").json()["datos"]
        conversacion = next(c for c in datos["conversaciones"] if c["id"] == "s1")

        assert "mensajes" in conversacion

    def test_los_recuerdos_van_enteros(self, cliente):
        csrf = _entrar(cliente)
        cliente.post(
            "/memory", json={"key": "color", "value": "verde"},
            headers={CABECERA_CSRF: csrf},
        )

        datos = cliente.get("/auth/datos").json()["datos"]

        assert any(
            r["key"] == "color" and r["value"] == "verde" for r in datos["recuerdos"]
        )

    def test_tambien_las_preferencias(self, cliente):
        """Son lo que la persona escribió sobre sí misma: de lo más suyo que
        hay en Morgan."""
        _entrar(cliente)

        datos = cliente.get("/auth/datos").json()["datos"]

        assert "ajustes" in datos


class TestLoQueNoSaleNunca:
    """Un fichero de exportación acaba en la carpeta de descargas, se comparte
    por correo y se sube a sitios. Una credencial ahí dentro es material para
    atacarla sin prisa, y sin que nadie se entere."""

    def test_no_va_el_hash_de_la_contrasena(self, cliente):
        _entrar(cliente)

        cuerpo = cliente.get("/auth/datos").text

        assert "password_hash" not in cuerpo
        assert "$argon2" not in cuerpo and "pbkdf2" not in cuerpo.lower()

    def test_no_van_los_identificadores_de_sesion(self, cliente):
        """Cada uno es una llave viva: con él se entra sin contraseña."""
        _entrar(cliente)

        cuerpo = cliente.get("/auth/datos").text

        assert "token_hash" not in cuerpo
        assert "auth_sessions" not in cuerpo

    def test_no_van_los_tokens_de_servicios_conectados(self, cliente):
        """Peor que el propio: abre una cuenta ajena a Morgan."""
        from src.api.dependencies import get_container
        from src.identidad import como_usuario
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.integraciones.repositorio import repositorio_de_integraciones

        _entrar(cliente)
        contenedor = get_container()
        fila = repositorio_de_cuentas(contenedor.repositories).buscar(REGISTRO["username"])
        repo = repositorio_de_integraciones(contenedor.repositories)
        with como_usuario(fila["id"]):
            repo.guardar("github", "gho_secretodeverdad", cuenta="ana")

        cuerpo = cliente.get("/auth/datos").text

        assert "gho_secretodeverdad" not in cuerpo
        assert "token_cifrado" not in cuerpo

    def test_pero_si_se_dice_QUE_tienes_conectado(self, cliente):
        """Saber que tu Morgan tiene acceso a tu GitHub es un dato tuyo, y de
        los que importan. Lo que no pinta ahí es la llave."""
        from src.api.dependencies import get_container
        from src.identidad import como_usuario
        from src.identidad.repositorio import repositorio_de_cuentas
        from src.integraciones.repositorio import repositorio_de_integraciones

        _entrar(cliente)
        contenedor = get_container()
        fila = repositorio_de_cuentas(contenedor.repositories).buscar(REGISTRO["username"])
        repo = repositorio_de_integraciones(contenedor.repositories)
        with como_usuario(fila["id"]):
            repo.guardar("github", "gho_algo", cuenta="ana-en-github")

        datos = cliente.get("/auth/datos").json()["datos"]

        assert any(
            s["servicio"] == "github" and s["cuenta"] == "ana-en-github"
            for s in datos["servicios_conectados"]
        )


class TestSoloLoPropio:
    def test_no_se_exporta_lo_de_otra_persona(self, cliente):
        csrf_bea = cliente.post("/auth/registro", json={
            "username": "bea", "email": "bea@ejemplo.co", "password": "otra-contrasena",
        }).json()["csrf"]
        cliente.post(
            "/sessions", json={"session_id": "de-bea", "title": "Privado"},
            headers={CABECERA_CSRF: csrf_bea},
        )
        cliente.post("/auth/logout", headers={CABECERA_CSRF: csrf_bea})

        _entrar(cliente)
        datos = cliente.get("/auth/datos").json()["datos"]

        assert all(c["id"] != "de-bea" for c in datos["conversaciones"])

    def test_la_ruta_no_acepta_identificador(self, cliente):
        """Si lo aceptara, sería una forma de leer los datos de otro. Se
        comprueba colando uno: debe ignorarse."""
        _entrar(cliente)

        datos = cliente.get("/auth/datos?user_id=bea").json()["datos"]

        assert datos["cuenta"]["email"] == REGISTRO["email"]


class TestUnFalloParcialSeDice:
    """Una exportación incompleta que parece completa es peor que ninguna: quien
    la guarda cree tener sus conversaciones y no las tiene."""

    def test_se_marca_lo_que_no_se_pudo_leer(self, cliente, monkeypatch):
        from src.api.dependencies import get_container

        _entrar(cliente)
        contenedor = get_container()

        def revienta(*_a, **_k):
            raise RuntimeError("el almacén no responde")

        monkeypatch.setattr(contenedor.repositories.memories, "search", revienta)

        datos = cliente.get("/auth/datos").json()["datos"]

        assert "recuerdos" in datos.get("incompleto", [])

    def test_y_el_resto_se_exporta_igual(self, cliente, monkeypatch):
        """Un fallo al leer una cosa no puede dejar sin exportar el resto."""
        from src.api.dependencies import get_container

        _entrar(cliente)
        contenedor = get_container()
        monkeypatch.setattr(
            contenedor.repositories.memories, "search",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
        )

        datos = cliente.get("/auth/datos").json()["datos"]

        assert "conversaciones" in datos
        assert datos["cuenta"]["email"] == REGISTRO["email"]


class TestQuedaRegistrado:
    def test_la_auditoria_lo_apunta(self, cliente):
        """Sacar todos los datos de una cuenta es una operación que conviene
        poder investigar después."""
        from src.api.dependencies import get_container

        _entrar(cliente)
        cliente.get("/auth/datos")

        eventos = get_container().audit_logger.get_recent(limit=20)

        assert any(e["tool"] == "export_account_data" for e in eventos)
