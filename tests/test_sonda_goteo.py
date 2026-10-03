"""
La sonda que mide dónde corta el proxy (2.0-D).

**Qué pregunta contesta.** El proxy de Vercel corta a los 120 segundos, y no se
sabe si cuenta la duración total de la respuesta o el tiempo sin recibir nada. Si
es lo segundo, el streaming levanta el techo de los turnos largos y no hay que
tocar la infraestructura. Si es lo primero, no arregla nada.

No se podía medir porque **no existía ni una respuesta en streaming** en todo el
backend. Esta sonda es lo mínimo para poder medirlo: una respuesta que tarda a
propósito emitiendo señales de vida.

Lo que se comprueba aquí es que la sonda **sea un instrumento fiable**, porque
una sonda que miente es peor que no tenerla: llevaría a una decisión de
arquitectura tomada sobre un dato falso. En concreto, que la primera línea salga
antes de la primera espera, que exija permiso y que no se pueda pedir una hora.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import reset_settings

PROPIETARIO = {
    "username": "andy",
    "email": "andy@ejemplo.co",
    "password": "contrasena-larga-de-andy",
}
OTRA = {
    "username": "bea",
    "email": "bea@ejemplo.co",
    "password": "otra-contrasena-larga",
}


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Morgan con cuentas, y yo como propietario de la instalación."""
    monkeypatch.setenv("MORGAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MORGAN_SERVE_WEB", "false")
    monkeypatch.setenv("MORGAN_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MORGAN_OWNER_EMAIL", PROPIETARIO["email"])
    reset_settings()
    from src.api import dependencies

    dependencies.reset_container()
    yield TestClient(create_app())
    reset_settings()
    dependencies.reset_container()


def _registrar(api, datos) -> str:
    respuesta = api.post("/auth/registro", json=datos)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["csrf"]


@pytest.fixture
def como_propietario(api):
    """Yo, registrado Y promovido.

    La promoción desde `MORGAN_OWNER_EMAIL` ocurre al construir el contenedor, y
    en ese momento la cuenta todavía no existe. Así que hay que rehacerlo
    después de registrarse, que es justo lo que pasa en producción: la variable
    se pone, se reinicia el servicio y entonces promueve.
    """
    _registrar(api, PROPIETARIO)

    from src.api import dependencies

    dependencies.reset_container()
    return api


def lineas_de(texto: str) -> list[str]:
    return [l for l in texto.splitlines() if l.strip()]


class TestSoloQuienAdministra:
    """Una petición que retiene una conexión tres minutos es, abierta al
    público, una forma barata de agotar las conexiones del servidor. Y en el
    plan gratuito de Render hay pocas.

    No es una ruta de uso: es un instrumento.
    """

    def test_sin_sesion_no_se_puede(self, api):
        assert api.get("/diagnostico/goteo?segundos=1").status_code == 401

    def test_una_cuenta_normal_tampoco(self, api):
        """Bea tiene sesión válida y no es quien administra."""
        _registrar(api, OTRA)

        respuesta = api.get("/diagnostico/goteo?segundos=1")

        assert respuesta.status_code == 403

    def test_quien_administra_si(self, como_propietario):
        assert como_propietario.get("/diagnostico/goteo?segundos=1").status_code == 200


class TestLaPrimeraLineaSaleAntesDeEsperar:
    """Es la parte que hace que la medición signifique algo.

    Si algo entre Morgan y el navegador acumula la respuesta y la entrega junta,
    se vería un corte a los 120 segundos **aunque el reloj del proxy fuera por
    inactividad**, porque el primer byte no habría salido nunca. La conclusión
    sería la contraria de la verdadera, y encima parecería bien medida.

    Por eso la primera línea va antes de la primera espera: es el canario. Si al
    medir de verdad no aparece en el primer segundo, hay un búfer en medio y lo
    que venga después no se puede interpretar.
    """

    def test_la_primera_linea_es_el_instante_cero(self, como_propietario):
        cuerpo = como_propietario.get("/diagnostico/goteo?segundos=1&cada=1").text

        primera = lineas_de(cuerpo)[0]

        assert primera.startswith("linea=0"), (
            "La primera línea no es la inmediata, así que no hay con qué "
            "detectar un búfer por el camino"
        )
        assert "t=0.000" in primera

    @pytest.mark.parametrize("cabecera,valor", [
        ("cache-control", "no-store"),
        ("x-accel-buffering", "no"),
    ])
    def test_se_pide_que_nadie_acumule_la_respuesta(self, como_propietario,
                                                    cabecera, valor):
        """`X-Accel-Buffering` lo entienden nginx y varios proxies. Sin ella, el
        intermediario puede juntar la respuesta y la sonda mide el búfer.
        """
        respuesta = como_propietario.get("/diagnostico/goteo?segundos=1")

        assert valor in respuesta.headers.get(cabecera, "").lower()

    def test_es_texto_plano_y_no_json(self, como_propietario):
        """Se lee con `curl` mirando cómo van cayendo las líneas. Un JSON habría
        que esperar a que cerrara para poder interpretarlo, que es precisamente
        lo que no se puede hacer aquí.
        """
        respuesta = como_propietario.get("/diagnostico/goteo?segundos=1")

        assert respuesta.headers["content-type"].startswith("text/plain")


class TestElGoteo:
    def test_una_linea_por_intervalo(self, como_propietario):
        cuerpo = como_propietario.get("/diagnostico/goteo?segundos=2&cada=0.5").text

        # La inmediata, cuatro del goteo y la de cierre.
        assert len(lineas_de(cuerpo)) == 6

    def test_cada_linea_dice_en_que_segundo_salio(self, como_propietario):
        """Es el dato de la medición: el corte se lee en la última línea que
        llegó. Sin el segundo, «se cortó» no dice dónde.
        """
        lineas = lineas_de(
            como_propietario.get("/diagnostico/goteo?segundos=1&cada=0.25").text
        )

        tiempos = [
            float(l.split("t=")[1].split()[0])
            for l in lineas if "t=" in l
        ]

        assert tiempos == sorted(tiempos), "Los tiempos no van hacia adelante"
        assert tiempos[0] == 0.0
        assert tiempos[-1] >= 1.0

    def test_el_cierre_dice_cuantas_fueron(self, como_propietario):
        """Para poder distinguir «terminó» de «lo cortaron»: si no está la línea
        de cierre, la respuesta no acabó por su cuenta.
        """
        lineas = lineas_de(
            como_propietario.get("/diagnostico/goteo?segundos=1&cada=0.5").text
        )

        assert lineas[-1].startswith("fin ")
        assert "lineas=2" in lineas[-1]

class TestCadaLineaSaleEnSuSegundo:
    """`linea=60` tiene que salir en `t=60`, y esto costó dos intentos acertarlo.

    **Lo que NO está en juego: la validez de la medición.** Cada línea lleva su
    propio instante real, así que aunque el goteo se desviara, `t=120.4` seguiría
    siendo verdad y el corte del proxy se leería igual de bien. Conviene decirlo
    porque es fácil vender esto como más importante de lo que es.

    **Lo que sí está en juego: que la salida se pueda leer.** Con la corrección,
    el número de línea y el segundo coinciden y el corte se ve de un vistazo. Sin
    ella, la línea 60 sale en el segundo 61,2 y hay que ir restando.

    **Y aquí el método falló dos veces**, que es lo que merece quedar escrito:

    1. El primer intento esperaba dos segundos de verdad y comprobaba el total.
       Pasaba con el código roto: en dos segundos el desvío es de milisegundos.
    2. El segundo usaba un reloj falso y comprobaba el total a 120 vueltas.
       **También pasaba con el código roto**, y el motivo es bonito: el bucle
       termina cuando se cumple el **tiempo**, no cuando se cumplen las vueltas.
       Al desviarse no acaba más tarde, acaba con menos líneas. El total era
       correcto en los dos casos.

    Lo que distingue las dos versiones es la **correspondencia** entre el número
    de línea y su instante. Eso es lo que se comprueba ahora, y es lo único que
    la corrección cambia de verdad.
    """

    SOBRECARGA = 0.020  # lo que cuesta una vuelta, aparte de dormir

    async def _recorrer(self, monkeypatch, segundos, cada):
        """Consume el goteo con un reloj y un `sleep` de mentira.

        El reloj solo avanza cuando alguien duerme, más una sobrecarga fija por
        vuelta: así el resultado depende de **cuánto se pide dormir**, que es lo
        que se está juzgando, y 120 vueltas se simulan al instante.
        """
        from src.api.routes import diagnostico

        reloj = {"t": 0.0}

        async def dormir(cuanto):
            reloj["t"] += max(0.0, cuanto) + self.SOBRECARGA

        monkeypatch.setattr(diagnostico.time, "monotonic", lambda: reloj["t"])
        monkeypatch.setattr(diagnostico.asyncio, "sleep", dormir)

        salida = []
        async for trozo in diagnostico._goteo(segundos, cada):
            salida.append(trozo)
            if len(salida) > 10_000:  # red por si el bucle no termina
                raise AssertionError("el goteo no termina")
        return salida

    @staticmethod
    def _instantes(salida):
        """{numero de linea: instante}, ignorando la linea de cierre."""
        pares = {}
        for linea in salida:
            m = re.match(r"linea=(\d+) t=([\d.]+)", linea.strip())
            if m:
                pares[int(m.group(1))] = float(m.group(2))
        return pares

    def test_la_linea_n_sale_en_el_segundo_n(self, monkeypatch):
        import asyncio

        salida = asyncio.run(self._recorrer(monkeypatch, segundos=120, cada=1.0))
        instantes = self._instantes(salida)

        desviadas = {
            n: t for n, t in instantes.items() if abs(t - n) > 0.05
        }

        assert not desviadas, (
            "Estas líneas no salen en su segundo: "
            + ", ".join(f"linea={n} en t={t:.2f}" for n, t in list(desviadas.items())[:5])
        )

    def test_salen_todas_las_lineas_que_deberian(self, monkeypatch):
        """La otra cara del desvío: al ir adelantado, el goteo llega al tope de
        tiempo con menos líneas de las que se pidieron.
        """
        import asyncio

        salida = asyncio.run(self._recorrer(monkeypatch, segundos=120, cada=1.0))
        instantes = self._instantes(salida)

        assert max(instantes) == 120, (
            f"Se esperaban 120 líneas de goteo y salieron {max(instantes)}"
        )

    def test_el_desvio_simulado_es_lo_bastante_grande_para_notarse(self):
        """Si la sobrecarga fuera despreciable, las dos pruebas de arriba
        pasarían con el código roto y no dirían nada. Con 120 vueltas a 20 ms el
        desvío acumulado es de más de dos segundos.
        """
        assert 120 * self.SOBRECARGA > 2.0

    def test_termina_y_cierra(self, monkeypatch):
        import asyncio

        salida = asyncio.run(self._recorrer(monkeypatch, segundos=10, cada=0.5))

        assert salida[-1].startswith("fin ")
        assert len(salida) == 22  # la inmediata, 20 del goteo y el cierre


class TestNoSePuedePedirUnaHora:
    """El tope existe porque la pregunta se contesta con 180 segundos —el corte
    que se busca está en 120— y dejar pedir una hora solo sirve para retener una
    conexión una hora.
    """

    def test_por_encima_del_tope_se_rechaza(self, como_propietario):
        from src.api.routes.diagnostico import SEGUNDOS_MAXIMOS

        respuesta = como_propietario.get(
            f"/diagnostico/goteo?segundos={SEGUNDOS_MAXIMOS + 1}"
        )

        assert respuesta.status_code == 422

    def test_el_tope_da_de_sobra_para_lo_que_se_mide(self):
        """120 es el corte que se busca. Un tope por debajo haría imposible la
        única medición para la que existe esto.
        """
        from src.api.routes.diagnostico import SEGUNDOS_MAXIMOS

        assert SEGUNDOS_MAXIMOS > 120

    def test_por_defecto_pasa_del_corte_que_se_busca(self, como_propietario):
        """Sin argumentos tiene que servir para la medición: si el valor por
        defecto fuera 60, se pediría, no se cortaría, y se concluiría que no hay
        techo.
        """
        import inspect

        from src.api.routes.diagnostico import goteo

        porDefecto = inspect.signature(goteo).parameters["segundos"].default
        assert porDefecto.default > 120

    @pytest.mark.parametrize("malo", [0, -1])
    def test_una_duracion_absurda_se_rechaza(self, como_propietario, malo):
        assert como_propietario.get(
            f"/diagnostico/goteo?segundos={malo}"
        ).status_code == 422


class TestNoEstorbaAlRestoDeMorgan:
    def test_no_se_registra_en_la_web(self, como_propietario):
        """No es funcionalidad. Que aparezca en la interfaz invitaría a pulsarlo
        sin saber que retiene una conexión tres minutos.
        """
        import pathlib

        web = pathlib.Path("web/src")
        encontrado = [
            p.name for p in web.rglob("*.ts*")
            if "diagnostico/goteo" in p.read_text(encoding="utf-8", errors="replace")
        ]

        assert encontrado == []

    def test_esta_en_el_esquema_para_quien_lo_busque(self, como_propietario):
        """Sin estar en la interfaz, pero documentada: quien administra tiene
        que poder encontrarla sin leer el código.
        """
        esquema = como_propietario.get("/openapi.json").json()

        assert "/diagnostico/goteo" in esquema["paths"]
