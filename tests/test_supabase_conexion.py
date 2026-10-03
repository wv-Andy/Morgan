"""
Una sola conexión con Supabase, reutilizada.

**Por qué importa, con el número delante.** Render está en Frankfurt y el
proyecto de Supabase en `us-east-1`, así que cada consulta cruza el Atlántico.
Y `httpx.request()` —la función de módulo que se usaba— abre un cliente nuevo
por llamada: conexión TCP nueva y apretón de manos TLS nuevo cada vez.

Eso son tres viajes de ida y vuelta en lugar de uno:

    TCP 90 ms + TLS 90 ms + petición 90 ms  =  ~340 ms

Medido con la instrumentación, un turno en producción:

    total=4753ms  bd=3750ms (11 consultas)  modelo=998ms  resto=5ms

**11 consultas a 341 ms cada una.** La base de datos era el 79% del turno, el
modelo el 21%, y el trabajo propio de Morgan 5 milisegundos.

Reutilizando la conexión, solo la primera consulta paga el apretón y las demás
cuestan un viaje. Es el mismo código haciendo lo mismo, y ahorra ~250 ms por
consulta sin mover nada de sitio.

**Lo que esto no arregla**: los 90 ms de cruzar el Atlántico. Eso es una
decisión de infraestructura —poner las dos cosas en la misma región— y está en
`docs/roadmap-2.0.md`.
"""

import httpx
import pytest

from src.memory.supabase_repositories import SupabaseClient


@pytest.fixture
def contador(monkeypatch):
    """Cuenta cuántos clientes de httpx se crean."""
    creados = []
    original = httpx.Client.__init__

    def contando(self, *args, **kwargs):
        creados.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", contando)
    return creados


class TestLaConexionSeReutiliza:
    def test_se_abre_una_sola_al_construir(self, contador):
        cliente = SupabaseClient("https://ejemplo.supabase.co", "clave-de-mentira")

        assert len(contador) == 1
        cliente.cerrar()

    def test_y_ninguna_mas_por_consulta(self, contador):
        """Es lo que ahorra los dos apretones de manos por consulta."""
        cliente = SupabaseClient("https://ejemplo.supabase.co", "clave-de-mentira")
        al_construir = len(contador)

        for _ in range(5):
            # Falla porque el dominio no existe, y da igual: lo que se cuenta es
            # cuántos clientes se abren, no si la consulta llega.
            with pytest.raises(Exception):
                cliente.select("sessions", "select=id")

        assert len(contador) == al_construir, (
            f"Se abrieron {len(contador) - al_construir} conexiones nuevas en 5 "
            "consultas. Cada una paga TCP y TLS otra vez, y con Render y "
            "Supabase en continentes distintos eso son ~250 ms tirados por "
            "consulta"
        )
        cliente.cerrar()

    def test_el_pool_mantiene_las_conexiones_vivas(self):
        """Sin `max_keepalive_connections` el pool no sirve de nada: la conexión
        se cerraría al devolverla y la siguiente volvería a empezar.
        """
        cliente = SupabaseClient("https://ejemplo.supabase.co", "clave-de-mentira")

        # `httpx` guarda los límites en el pool del transporte, no en el
        # cliente. Se mira ahí porque es donde acaban de verdad: comprobar lo
        # que se le pasó al constructor no dice si llegó a aplicarse.
        pool = cliente._http._transport._pool

        assert pool._max_keepalive_connections >= 5, (
            "El pool no mantiene conexiones vivas, así que reutilizar el cliente "
            "no ahorra los apretones de manos"
        )
        cliente.cerrar()

    def test_las_cabeceras_van_en_el_cliente_y_no_en_cada_llamada(self):
        """La clave y el `apikey` se ponen una vez. Repetirlas por llamada es
        más superficie para olvidarse de una.
        """
        cliente = SupabaseClient("https://ejemplo.supabase.co", "una-clave")

        assert cliente._http.headers["apikey"] == "una-clave"
        assert cliente._http.headers["authorization"] == "Bearer una-clave"
        cliente.cerrar()

    def test_se_puede_cerrar(self):
        """Para las pruebas y para un apagado ordenado: dejar conexiones
        abiertas hace que el proceso no termine cuando debería.
        """
        cliente = SupabaseClient("https://ejemplo.supabase.co", "clave")

        cliente.cerrar()

        assert cliente._http.is_closed


class TestElPoolNoCierraConexionesEnUso:
    """Medido en un despliegue como el de producción (2.3-C, V2.0.38): con 20
    conexiones abiertas y solo 10 vivas, 25 personas a la vez daban
    `httpx.ReadError: [Errno 9] Bad file descriptor` y los turnos fallaban con 500.
    El pool cerraba las conexiones que sobraban al devolverlas, y ese cierre se
    cruzaba con otro hilo que ya la estaba usando."""

    def test_tantas_vivas_como_abiertas(self):
        cliente = SupabaseClient("https://ejemplo.supabase.co", "clave")
        try:
            pool = cliente._http._transport._pool
            assert pool._max_keepalive_connections == pool._max_connections, (
                "con menos vivas que abiertas, el pool cierra conexiones que otro hilo usa"
            )
        finally:
            cliente.cerrar()

    def test_y_sitio_para_la_concurrencia_real(self):
        """La reserva de hilos de Starlette es de 40, y cada turno corre además
        en el suyo."""
        cliente = SupabaseClient("https://ejemplo.supabase.co", "clave")
        try:
            assert cliente._http._transport._pool._max_connections >= 40
        finally:
            cliente.cerrar()
