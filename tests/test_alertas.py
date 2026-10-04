"""
Los errores del servidor, a la bandeja del propietario (4.20): `src/alertas.py`.

Lo que haría daño si fallase: que un error no llegara (el propietario no se entera), que una
tormenta de errores llenara la bandeja, que avisar bloqueara la petición que falló, o que un
fallo al avisar provocara otro aviso (un bucle).
"""

import logging
import threading
import time

import pytest

from src import alertas


class Reloj:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def _esperar(condicion, segundos=3.0):
    limite = time.monotonic() + segundos
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.01)
    return condicion()


@pytest.fixture
def montado():
    """Un manejador en un registro propio, con un `avisar` que apunta lo que recibe."""
    recibidos: list[tuple[str, str]] = []
    reloj = Reloj()
    manejador = alertas.AvisosDeErrores(lambda t, x: recibidos.append((t, x)), reloj=reloj)
    registro = logging.getLogger("prueba.alertas")
    registro.addHandler(manejador)
    registro.propagate = False
    yield registro, recibidos, reloj, manejador
    registro.removeHandler(manejador)


class TestLlegan:
    def test_un_error_llega_a_la_bandeja(self, montado):
        registro, recibidos, _, _ = montado
        registro.error("No se pudo guardar el turno %s", "s-42")
        assert _esperar(lambda: len(recibidos) == 1)
        titulo, texto = recibidos[0]
        assert titulo == "Error en el servidor" and "No se pudo guardar el turno s-42" in texto

    def test_con_la_excepcion(self, montado):
        registro, recibidos, _, _ = montado
        try:
            raise ValueError("clave rota")
        except ValueError:
            registro.exception("Falló algo")
        assert _esperar(lambda: recibidos)
        assert "ValueError: clave rota" in recibidos[0][1]

    def test_un_aviso_no_es_un_error(self, montado):
        registro, recibidos, _, _ = montado
        registro.warning("Turno fuera de objetivo")
        time.sleep(0.1)
        assert recibidos == []


class TestNoInunda:
    def test_el_mismo_error_una_vez_por_hora_y_se_cuenta(self, montado):
        registro, recibidos, reloj, _ = montado
        for n in range(5):
            registro.error("Supabase respondió 503 en la petición %d", n)
        assert _esperar(lambda: len(recibidos) == 1)
        time.sleep(0.1)
        assert len(recibidos) == 1, "los números no lo hacen otro error"
        reloj.t += alertas.VENTANA + 1
        registro.error("Supabase respondió 503 en la petición 99")
        assert _esperar(lambda: len(recibidos) == 2)
        assert "se repitió 4 vez/veces" in recibidos[1][1]

    def test_dos_errores_distintos_llegan_los_dos(self, montado):
        registro, recibidos, _, _ = montado
        registro.error("No se pudo guardar el turno")
        registro.error("El reloj falló en un tic")
        assert _esperar(lambda: len(recibidos) == 2)

    def test_tope_al_dia(self, montado):
        registro, recibidos, reloj, _ = montado
        for n in range(alertas.MAX_AL_DIA + 10):
            registro.error(f"error distinto {chr(65 + n % 26)}{chr(65 + n // 26)}")
        assert _esperar(lambda: len(recibidos) == alertas.MAX_AL_DIA)
        time.sleep(0.1)
        assert len(recibidos) == alertas.MAX_AL_DIA
        reloj.t += 86400
        registro.error("al día siguiente, sí")
        assert _esperar(lambda: len(recibidos) == alertas.MAX_AL_DIA + 1)


class TestFueraDelCamino:
    def test_avisar_no_bloquea_al_que_registra(self):
        soltar = threading.Event()
        manejador = alertas.AvisosDeErrores(lambda t, x: soltar.wait(5))
        registro = logging.getLogger("prueba.alertas.lenta")
        registro.addHandler(manejador)
        registro.propagate = False
        try:
            inicio = time.monotonic()
            registro.error("algo")
            assert time.monotonic() - inicio < 0.5
        finally:
            soltar.set()
            registro.removeHandler(manejador)

    def test_si_avisar_falla_no_hay_bucle(self, caplog):
        llamadas = []

        def falla(titulo, texto):
            llamadas.append(texto)
            logging.getLogger("src.memory.db").error("la base no contesta al avisar")
            raise RuntimeError("no hay base")

        manejador = alertas.AvisosDeErrores(falla)
        raiz = logging.getLogger()
        raiz.addHandler(manejador)
        try:
            # Fuera de "prueba.alertas", que la fixture deja sin propagar.
            logging.getLogger("prueba_bucle").error("el primero")
            assert _esperar(lambda: llamadas)
            time.sleep(0.2)
            assert len(llamadas) == 1, "el error de dentro del aviso no avisa otra vez"
        finally:
            raiz.removeHandler(manejador)


class TestInstalar:
    def test_una_sola_vez(self):
        raiz = logging.getLogger()
        antes = [h for h in raiz.handlers if isinstance(h, alertas.AvisosDeErrores)]
        try:
            primero = alertas.instalar()
            assert (primero is not None) == (not antes)
            assert alertas.instalar() is None
        finally:
            for h in [h for h in raiz.handlers if isinstance(h, alertas.AvisosDeErrores)]:
                if h not in antes:
                    raiz.removeHandler(h)

    def test_llega_a_la_bandeja_del_propietario(self, tmp_path, monkeypatch):
        """De punta a punta con la base de verdad (SQLite): el aviso queda en la bandeja del
        propietario, y de nadie más."""
        from fastapi.testclient import TestClient

        from src.api import dependencies
        from src.api.app import create_app
        from src.automatizacion.repositorio import repositorio_de_automatizaciones
        from src.config import reset_settings

        monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
        reset_settings()
        dependencies.reset_container()
        app = create_app()
        ids = []
        for nombre in ("ana", "bea"):
            r = TestClient(app).post("/auth/registro", json={
                "username": nombre, "email": f"{nombre}@ejemplo.co", "password": "contrasena-larga"})
            ids.append(r.json()["usuario"]["id"])
        from src.identidad.propietario import asegurar_propietario
        from src.identidad.repositorio import repositorio_de_cuentas

        repos = dependencies.get_container().repositories
        monkeypatch.setenv("MORGAN_OWNER_EMAIL", "ana@ejemplo.co")      # como en producción
        assert asegurar_propietario(repositorio_de_cuentas(repos)) == ids[0]
        repo = repositorio_de_automatizaciones(repos)
        try:
            alertas._avisar_al_propietario("Error en el servidor", "algo se rompió")
            avisos = {u: repo.avisos(u) for u in ids}
            assert sum(len(a) for a in avisos.values()) == 1, avisos
            assert avisos[ids[0]] and not avisos[ids[1]], "solo a la propietaria"
            assert avisos[ids[0]][0]["estado"] == "fallo" and avisos[ids[0]][0]["automatizacion_id"] is None
        finally:
            dependencies.reset_container()
            reset_settings()
