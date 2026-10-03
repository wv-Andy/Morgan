"""
La implementación de Supabase de las cuentas.

Existía desde la V1.6 y **nunca se había ejercitado**. Las pruebas del sistema de
cuentas usan SQLite, así que la otra mitad —la que corre en producción— no tenía
ninguna.

Lo que costó: un `_cita()` que envolvía los valores entre comillas dobles creyendo
que así escapaba comas y paréntesis. PostgREST toma esas comillas como **parte del
valor**, de modo que `eq."abc"` busca literalmente `"abc"` y no encuentra nada.

El resultado fue que **el sistema de cuentas entero dejó de funcionar en la nube**:
login, registro, sesiones, recuperación de contraseña y hasta la promoción del
propietario. Todos respondían «no existe» sobre filas que estaban ahí.

Estas pruebas no necesitan red: capturan las peticiones que se construyen y
comprueban su forma. Es donde estaba el fallo, y es lo que se puede comprobar en
cada ejecución de la suite.
"""

import time
from urllib.parse import unquote

import pytest

from src.identidad.repositorio import CuentasSupabase


class ClienteFalso:
    """Un PostgREST de mentira que apunta lo que le piden."""

    def __init__(self, respuesta=None):
        self.peticiones: list[tuple[str, str, str]] = []
        self.respuesta = respuesta if respuesta is not None else []

    def select(self, tabla, consulta=""):
        self.peticiones.append(("GET", tabla, consulta))
        return self.respuesta

    def update(self, tabla, consulta, valores):
        self.peticiones.append(("PATCH", tabla, consulta))
        return self.respuesta

    def insert(self, tabla, filas):
        self.peticiones.append(("POST", tabla, ""))
        return self.respuesta

    def delete(self, tabla, consulta):
        self.peticiones.append(("DELETE", tabla, consulta))
        return self.respuesta

    @property
    def ultima_consulta(self) -> str:
        return self.peticiones[-1][2]


@pytest.fixture
def cliente():
    return ClienteFalso()


@pytest.fixture
def repo(cliente):
    return CuentasSupabase(cliente)


class TestLosValoresNoLlevanComillas:
    """El fallo exacto. PostgREST solo usa comillas dobles dentro de listas
    —`in.("a","b")`—, nunca en una comparación simple."""

    def test_cita_no_anade_comillas(self):
        assert '"' not in CuentasSupabase._cita("abc123")

    def test_un_hash_pasa_tal_cual(self):
        """Un sha256 en hexadecimal no necesita ninguna codificación, y añadirle
        algo es lo que lo rompía."""
        hash_real = "b44db9325019f2b82de60e75051aee2e1bc6add3c53daf3dba9d8db403083e73"

        assert CuentasSupabase._cita(hash_real) == hash_real

    @pytest.mark.parametrize(
        "metodo, argumentos",
        [
            ("obtener", ("usr-abc",)),
            ("buscar", ("andy@ejemplo.co",)),
            ("duplicado", ("andy", "andy@ejemplo.co")),
            ("reset_valido", ("unhash", 1.0)),
            ("marcar_reset_usado", ("unhash", 1.0)),
            ("invalidar_resets", ("usr-abc", 1.0)),
            ("listar_sesiones", ("usr-abc", 1.0)),
            ("revocar_sesion", ("sesion-abc",)),
            ("tocar_sesion", ("sesion-abc", 1.0)),
            ("olvidar_intentos", ("andy", "1.2.3.4")),
            ("contar_intentos", ("andy", "1.2.3.4", 1.0)),
        ],
    )
    def test_ninguna_consulta_lleva_comillas(self, repo, cliente, metodo, argumentos):
        """La comprobación que habría evitado todo esto."""
        getattr(repo, metodo)(*argumentos)

        for _, _, consulta in cliente.peticiones:
            assert '"' not in consulta, f"{metodo} manda comillas: {consulta}"
            assert "%22" not in consulta, f"{metodo} manda comillas codificadas"


class TestLosValoresLleganEnteros:
    """Codificar de más también rompe: el valor tiene que llegar como es."""

    def test_un_correo_se_recupera_igual(self, repo, cliente):
        repo.buscar("andy+etiqueta@ejemplo.co")

        assert "andy+etiqueta@ejemplo.co" in unquote(cliente.ultima_consulta)

    def test_un_identificador_con_guiones_tambien(self, repo, cliente):
        """Los identificadores salen de `token_urlsafe`, que produce guiones y
        guiones bajos. El de producción empieza por `usr--`."""
        repo.obtener("usr--IZu0GOGWxGolrgc")

        assert "usr--IZu0GOGWxGolrgc" in unquote(cliente.ultima_consulta)

    @pytest.mark.parametrize(
        "peligroso",
        [
            "usuario,otro",           # una coma separa argumentos en PostgREST
            "usuario)or(1=1",         # paréntesis
            "usuario&role=eq.owner",  # intentar colar otro filtro
            "usuario*",
        ],
    )
    def test_lo_que_podria_colarse_como_otro_filtro_se_codifica(self, repo, cliente, peligroso):
        """Que era lo que las comillas pretendían evitar, y que la codificación
        de URL resuelve de verdad."""
        repo.buscar(peligroso)

        consulta = cliente.ultima_consulta
        # El valor viaja codificado, asi que sus caracteres especiales no actuan
        # como sintaxis: no aparecen crudos fuera de la parte que Morgan compone.
        assert "&role=eq.owner" not in consulta
        assert peligroso in unquote(consulta)


class TestLaFormaDeLasConsultas:
    def test_buscar_mira_usuario_y_correo(self, repo, cliente):
        repo.buscar("andy")

        consulta = unquote(cliente.ultima_consulta)
        assert "username.eq." in consulta
        assert "email.eq." in consulta

    def test_el_propietario_se_busca_por_rol(self, repo, cliente):
        repo.propietario()

        assert "role=eq.owner" in cliente.ultima_consulta

    def test_el_usuario_local_no_sale_en_la_lista(self, repo, cliente):
        """Es el dueño implícito de lo que había antes de las cuentas, no una
        persona."""
        repo.listar_usuarios()

        assert "id=neq.local" in cliente.ultima_consulta

    def test_un_reset_usado_no_vale(self, repo, cliente):
        repo.reset_valido("h", 100.0)

        consulta = cliente.ultima_consulta
        assert "usado_en=is.null" in consulta
        assert "expira_en=gt.100.0" in consulta

    def test_una_sesion_revocada_no_identifica(self, repo, cliente):
        repo.usuario_de_sesion("s", 100.0)

        consulta = cliente.ultima_consulta
        assert "revocada=is.false" in consulta
        assert "expira_en=gt.100.0" in consulta

    def test_solo_se_revocan_las_vivas(self, repo, cliente):
        repo.revocar_sesiones("usr-1", None)

        assert "revocada=is.false" in cliente.ultima_consulta

    def test_revocar_todas_menos_una(self, repo, cliente):
        repo.revocar_sesiones("usr-1", "sesion-actual")

        assert "id=neq.sesion-actual" in unquote(cliente.ultima_consulta)


class TestLaSesionDevuelveElUsuario:
    def test_se_extrae_del_embebido(self):
        """Viaja embebido para no pagar dos viajes de red por petición."""
        cliente = ClienteFalso([
            {"morgan_users": {"id": "usr-1", "status": "activo", "username": "andy"}}
        ])

        assert CuentasSupabase(cliente).usuario_de_sesion("s", 1.0)["username"] == "andy"

    def test_una_cuenta_suspendida_no_identifica(self):
        """El JOIN de SQLite filtra por estado; aquí se comprueba a mano, y las
        dos implementaciones tienen que comportarse igual."""
        cliente = ClienteFalso([
            {"morgan_users": {"id": "usr-1", "status": "suspendido"}}
        ])

        assert CuentasSupabase(cliente).usuario_de_sesion("s", 1.0) is None

    def test_sin_sesion_devuelve_none(self):
        assert CuentasSupabase(ClienteFalso([])).usuario_de_sesion("s", 1.0) is None


class TestLasDosImplementacionesOfrecenLoMismo:
    """Si una gana un método y la otra no, el fallo aparece solo en el entorno
    que no lo tiene — y ese suele ser producción."""

    def test_supabase_implementa_toda_la_interfaz(self):
        from src.identidad.repositorio import CuentasSQLite, RepositorioDeCuentas

        de_la_interfaz = {
            nombre for nombre in dir(RepositorioDeCuentas)
            if not nombre.startswith("_")
        }

        for clase in (CuentasSQLite, CuentasSupabase):
            faltan = [n for n in de_la_interfaz if not hasattr(clase, n)]
            assert not faltan, f"{clase.__name__} no implementa: {faltan}"

    def test_ninguna_es_abstracta_por_accidente(self):
        """Una abstracta sin implementar revienta al instanciar, y eso pasa en el
        arranque del contenedor: en la nube, con todo desplegado."""
        from src.identidad.repositorio import CuentasSQLite

        CuentasSupabase(ClienteFalso())
        CuentasSQLite(object())
