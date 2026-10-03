"""
Que lo caducado se barra de verdad, y no solo pueda barrerse.

**Por qué existe este archivo.** `limpiar_caducado()` estaba escrito,
implementado en los dos almacenes y probado. Y **nadie lo llamaba**.

Lo encontró la auditoría de la V2.0.2 mirando producción: 26 sesiones y 13
tokens de recuperación acumulados para **tres** usuarios. De esas 39 filas, 33
se podían borrar —20 sesiones revocadas y los 13 tokens, todos caducados—. No
faltaba el barrido: faltaba enchufarlo.

Es un tipo de defecto que las pruebas de antes no podían ver, porque probaban
que el método funciona. Nadie probaba que se usara. La diferencia entre esas dos
cosas es este archivo.
"""

import time

import pytest

from src.identidad.cuentas import (
    FRENOS,
    HORAS_ENTRE_BARRIDOS,
    MINUTOS_ENTRE_TOQUES,
    ServicioDeCuentas,
)
from src.memory.db import Database


@pytest.fixture(autouse=True)
def frenos_sin_echar():
    """Los frenos son estado del PROCESO, no de la instancia, y eso obliga a
    limpiarlos entre pruebas. Es el precio de que frenen de verdad.
    """
    FRENOS.reiniciar()
    yield
    FRENOS.reiniciar()


@pytest.fixture
def servicio(tmp_path):
    # `ServicioDeCuentas` acepta una `Database` por comodidad y monta el
    # repositorio de SQLite por su cuenta. Es como lo hacen las demas pruebas.
    return ServicioDeCuentas(Database(tmp_path / "cuentas.db"))


CONTRASENA = "una-contrasena-larga"


def _registrar(servicio, nombre="ana") -> str:
    """Crea la cuenta y abre sesión. Devuelve el token.

    Son **dos** pasos, y eso importa aquí: `registrar` no crea sesión, solo la
    cuenta. La sesión la abre `iniciar_sesion`, y es la ruta `/auth/registro`
    quien encadena los dos. La primera versión de estas pruebas daba por hecho
    que registrarse abría sesión, y una falló por eso.
    """
    servicio.registrar(
        username=nombre,
        email=f"{nombre}@ejemplo.co",
        password=CONTRASENA,
    )
    _usuario, token = servicio.iniciar_sesion(nombre, CONTRASENA)
    return token


class TestElBarridoSeEjecutaAlEntrar:
    """La parte que faltaba. Lo demás ya funcionaba."""

    def test_abrir_sesion_barre(self, servicio, monkeypatch):
        """El gancho está en `_crear_sesion`, que es el único sitio por el que
        pasa lo que hace crecer estas tablas.

        **No está en `registrar`, y es correcto que no lo esté**: registrar no
        abre sesión, solo crea la cuenta. Quien encadena los dos pasos es la
        ruta, y por eso hay una prueba de la ruta más abajo.
        """
        servicio.registrar(username="ana", email="ana@ejemplo.co",
                           password=CONTRASENA)

        llamadas = []
        monkeypatch.setattr(
            servicio.repo, "limpiar",
            lambda ahora: llamadas.append(ahora) or 0,
        )

        servicio.iniciar_sesion("ana", CONTRASENA)

        assert llamadas, "Abrir sesión no barre"

    def test_registrarse_por_la_ruta_tambien_barre(self, tmp_path, monkeypatch):
        """El camino de verdad: `/auth/registro` crea la cuenta **y** abre
        sesión, así que pasa por el gancho.

        Se prueba por la ruta y no por el servicio porque es ahí donde los dos
        pasos se encadenan, y probar el servicio solo habría dado la impresión
        de que registrarse no barre.
        """
        from fastapi.testclient import TestClient

        from src.api.app import create_app
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        from src.api import dependencies

        dependencies.reset_container()
        try:
            cliente = TestClient(create_app())

            # Se espía la CLASE y no una instancia, porque el repositorio
            # también se construye en cada llamada: `repositorio_de_cuentas()`
            # devuelve un objeto nuevo cada vez. Es la misma razón por la que
            # los frenos tuvieron que salir de `self`.
            from src.identidad.repositorio import CuentasSQLite

            llamadas = []
            monkeypatch.setattr(
                CuentasSQLite, "limpiar",
                lambda self, ahora: llamadas.append(ahora) or 0,
            )

            respuesta = cliente.post("/auth/registro", json={
                "username": "ana", "email": "ana@ejemplo.co",
                "password": CONTRASENA,
            })
            assert respuesta.status_code == 200, respuesta.text

            assert llamadas, "Registrarse por la ruta no barre"
        finally:
            reset_settings()
            dependencies.reset_container()


class TestElFrenoFrena:
    """Dos DELETE contra una base en otro continente son ~600 ms. Pagarlos en
    cada entrada sería cobrarle a alguien por una limpieza que no le corre prisa
    a nadie.
    """

    def test_dos_entradas_seguidas_barren_una_sola_vez(self, servicio, monkeypatch):
        _registrar(servicio)

        llamadas = []
        monkeypatch.setattr(
            servicio.repo, "limpiar",
            lambda ahora: llamadas.append(ahora) or 0,
        )

        servicio.iniciar_sesion("ana", CONTRASENA)
        servicio.iniciar_sesion("ana", CONTRASENA)
        servicio.iniciar_sesion("ana", CONTRASENA)

        assert len(llamadas) <= 1, (
            f"Se ha barrido {len(llamadas)} veces en tres entradas seguidas"
        )

    def test_pasado_el_plazo_vuelve_a_barrer(self, servicio, monkeypatch):
        """Sin esto, una instancia que no se reinicie no barre nunca más."""
        _registrar(servicio)
        # `_registrar` entra, y entrar ya barre: hay que dejar el freno sin
        # echar antes de contar, o la primera llamada sale throttled y la
        # prueba mide una vuelta menos.
        FRENOS.reiniciar()

        llamadas = []
        monkeypatch.setattr(
            servicio.repo, "limpiar",
            lambda ahora: llamadas.append(ahora) or 0,
        )

        servicio.barrer_si_toca()
        FRENOS.ultimo_barrido = time.time() - (HORAS_ENTRE_BARRIDOS * 3600 + 10)
        servicio.barrer_si_toca()

        assert len(llamadas) == 2

    def test_el_plazo_es_de_horas_y_no_de_dias(self):
        """Un plazo demasiado largo hace que el barrido no llegue a pasar nunca
        en un despliegue que se reinicia cada pocas horas, que es el caso.
        """
        assert 1 <= HORAS_ENTRE_BARRIDOS <= 24


class TestUnBarridoRotoNoImpideEntrar:
    """El peor resultado de un barrido que falla es una tabla grande. El peor de
    un barrido que rompe la entrada es que nadie pueda usar Morgan.
    """

    def test_si_el_barrido_revienta_se_entra_igual(self, servicio, monkeypatch):
        _registrar(servicio)
        FRENOS.ultimo_barrido = 0.0

        def revienta(ahora):
            raise RuntimeError("la base dice que no")

        monkeypatch.setattr(servicio.repo, "limpiar", revienta)

        usuario, token = servicio.iniciar_sesion("ana", CONTRASENA)

        assert token
        assert usuario is not None

    def test_y_queda_en_el_registro(self, servicio, monkeypatch, caplog):
        monkeypatch.setattr(
            servicio.repo, "limpiar",
            lambda ahora: (_ for _ in ()).throw(RuntimeError("no")),
        )

        with caplog.at_level("WARNING"):
            servicio.barrer_si_toca()

        assert "barrer" in caplog.text.lower()

    def test_un_barrido_que_falla_no_se_reintenta_en_cada_entrada(
        self, servicio, monkeypatch
    ):
        """El instante se apunta ANTES de barrer, a propósito. Si se apuntara
        después, un fallo dejaría el freno sin echar y cada entrada volvería a
        intentarlo: un error que se repite en cada petición es peor que la
        basura que iba a limpiar.
        """
        intentos = []

        def revienta(ahora):
            intentos.append(ahora)
            raise RuntimeError("no")

        monkeypatch.setattr(servicio.repo, "limpiar", revienta)

        servicio.barrer_si_toca()
        servicio.barrer_si_toca()
        servicio.barrer_si_toca()

        assert len(intentos) == 1, (
            f"Un barrido que falla se ha reintentado {len(intentos)} veces"
        )


class TestQueSeBarreYQueNo:
    """Lo que importa: que no se lleve por delante nada que sirva."""

    def test_una_sesion_viva_no_se_borra(self, servicio):
        token = _registrar(servicio)

        servicio.limpiar_caducado()

        assert servicio.usuario_de_sesion(token) is not None, (
            "El barrido se ha llevado una sesión que valía"
        )

    def test_una_sesion_revocada_si(self, servicio):
        token = _registrar(servicio)
        servicio.cerrar_sesion(token)

        borradas = servicio.limpiar_caducado()

        assert borradas >= 1
        assert servicio.usuario_de_sesion(token) is None

    def test_un_token_de_recuperacion_sin_usar_y_vigente_no_se_borra(self, servicio):
        """Es el caso que más costaría equivocarse: barrer el enlace que alguien
        acaba de pedir y está a punto de pulsar.
        """
        _registrar(servicio)
        token = servicio.solicitar_recuperacion("ana@ejemplo.co")
        assert token

        servicio.limpiar_caducado()

        # Sigue sirviendo: se puede restablecer con él.
        servicio.restablecer_password(token, "otra-contrasena-larga")
        usuario, _ = servicio.iniciar_sesion("ana", "otra-contrasena-larga")
        assert usuario is not None

    def test_lo_que_no_ha_caducado_deja_la_cuenta_intacta(self, servicio):
        token = _registrar(servicio)
        usuario = servicio.usuario_de_sesion(token)
        assert usuario is not None

        servicio.limpiar_caducado()

        assert servicio.obtener(usuario.id) is not None


class TestLaImplementacionDeLaNubeBarreLoMismo:
    """Se escribe y se prueba a la vez que la de SQLite, que es justo lo que no
    se hizo con las conversaciones y costó ocho defectos en producción.
    """

    class ClienteQueApunta:
        def __init__(self):
            self.borrados: list[tuple[str, str]] = []

        def delete(self, tabla, consulta):
            self.borrados.append((tabla, consulta))
            return [{"id": "x"}]

        def select(self, *a, **k):
            return []

        def insert(self, *a, **k):
            return []

        def update(self, *a, **k):
            return []

        def upsert(self, *a, **k):
            return []

    def test_barre_las_dos_tablas(self):
        from src.identidad.repositorio import CuentasSupabase

        cliente = self.ClienteQueApunta()
        CuentasSupabase(cliente).limpiar(1000.0)

        tablas = [t for t, _ in cliente.borrados]
        assert "auth_sessions" in tablas
        assert "password_reset_tokens" in tablas

    def test_las_sesiones_se_barren_por_caducadas_O_revocadas(self):
        """Las 20 que sobraban en producción estaban revocadas, no caducadas.
        Filtrando solo por caducidad no se habría limpiado ninguna.
        """
        from src.identidad.repositorio import CuentasSupabase

        cliente = self.ClienteQueApunta()
        CuentasSupabase(cliente).limpiar(1000.0)

        consulta = next(c for t, c in cliente.borrados if t == "auth_sessions")
        assert "revocada" in consulta, (
            "Solo se barren las caducadas, y en producción las que sobraban "
            "estaban revocadas"
        )
        assert "expira_en" in consulta


class TestElFrenoSobreviveALaInstancia:
    """La parte que hacía inútiles los dos frenos, y que solo se ve mirando
    **entre** peticiones.

    `ServicioDeCuentas` se construye en cada petición: `get_servicio` es una
    dependencia de FastAPI. Comprobado:

        get_servicio(c) is get_servicio(c)   ->   False

    Con el contador en `self`, cada petición empezaba a cero. El del «último
    uso» llevaba así desde que se escribió —se añadió para ahorrar un viaje de
    red por petición autenticada, y no ahorraba ninguno— y el del barrido lo
    añadí igual de roto en la misma tanda.

    Estas pruebas fallan con el estado en `self` y pasan con él fuera. Es la
    diferencia entre probar que el freno existe y probar que frena.
    """

    def test_dos_servicios_distintos_comparten_el_freno_del_barrido(self, tmp_path):
        db = Database(tmp_path / "compartido.db")
        uno = ServicioDeCuentas(db)
        otro = ServicioDeCuentas(db)

        assert uno is not otro, "si fueran el mismo, esta prueba no diría nada"

        llamadas = []
        for s in (uno, otro):
            s.repo.limpiar = lambda ahora: llamadas.append(ahora) or 0

        uno.barrer_si_toca()
        otro.barrer_si_toca()

        assert len(llamadas) == 1, (
            f"Se ha barrido {len(llamadas)} veces con dos instancias. El freno "
            "está en `self` y cada petición crea una nueva."
        )

    def test_y_el_del_ultimo_uso_tambien(self, tmp_path):
        """El que llevaba roto desde que se escribió."""
        db = Database(tmp_path / "toques.db")
        uno = ServicioDeCuentas(db)
        otro = ServicioDeCuentas(db)

        ahora = time.time()
        assert uno._toca_ahora("ses-1", ahora) is True, "el primero sí toca"
        assert otro._toca_ahora("ses-1", ahora + 1) is False, (
            "Una instancia nueva vuelve a tocar la misma sesión un segundo "
            "después. El freno no frena, y son ~300 ms de red por petición."
        )

    def test_pasado_el_plazo_del_toque_se_vuelve_a_anotar(self, tmp_path):
        db = Database(tmp_path / "toques2.db")
        servicio = ServicioDeCuentas(db)
        ahora = time.time()

        assert servicio._toca_ahora("ses-1", ahora) is True
        despues = ahora + MINUTOS_ENTRE_TOQUES * 60 + 1
        assert ServicioDeCuentas(db)._toca_ahora("ses-1", despues) is True

    def test_el_registro_de_toques_se_recorta(self, tmp_path, monkeypatch):
        """Es estado de proceso: sin tope, una instancia de larga vida con
        muchas sesiones se lo come todo.

        El tope se baja a 10 **a propósito**. La primera versión de esta prueba
        leía la constante para construir el caso, así que subirla a diez
        millones la hacía pasar igual: se adaptaba a lo que debía vigilar.
        Descubierto mutando.
        """
        import src.identidad.cuentas as cuentas

        monkeypatch.setattr(cuentas, "MAXIMOS_TOQUES_RECORDADOS", 10)

        servicio = ServicioDeCuentas(Database(tmp_path / "muchos.db"))
        ahora = time.time()
        for i in range(60):
            servicio._toca_ahora(f"ses-{i}", ahora)

        assert len(FRENOS.ultimos_toques) <= 10, (
            f"El registro tiene {len(FRENOS.ultimos_toques)} entradas con el "
            "tope en 10"
        )

    def test_y_el_tope_configurado_es_razonable(self):
        """La otra mitad: que el número de verdad no sea absurdo. Esta es la que
        falla si alguien lo sube a diez millones.
        """
        from src.identidad.cuentas import MAXIMOS_TOQUES_RECORDADOS

        assert 100 <= MAXIMOS_TOQUES_RECORDADOS <= 100_000
