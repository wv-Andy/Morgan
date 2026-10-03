"""
La marca de «último uso» no se escribe en cada petición.

**El número que lo justifica.** Escribirla costaba un viaje completo a Supabase
en **cada** petición autenticada. Medido en producción con la instrumentación:
~220 ms, que en `/auth/yo` eran más de la mitad de los 400 ms que tardaba.

Y lo que se compra con esa escritura es que la lista de sesiones abiertas diga
«hace un momento» en lugar de «hace un minuto». El propio código ya lo decía:
«anotar último uso es informativo».

Así que se escribe como mucho una vez cada cinco minutos por sesión. Lo que se
pierde es precisión al segundo en un dato informativo; lo que se gana es un
viaje de red menos en todas las peticiones de todo el mundo.

**Lo que estas pruebas protegen** es que no se vuelva a escribir en cada
petición sin que nadie lo note. Es el tipo de coste que no falla nunca: solo
suma doscientos milisegundos a todo.
"""

import time

import pytest

from src.identidad.repositorio import RepositorioDeCuentas
from src.identidad.cuentas import (
    FRENOS,
    MAXIMOS_TOQUES_RECORDADOS,
    MINUTOS_ENTRE_TOQUES,
    ServicioDeCuentas,
)


class RepoQueCuenta(RepositorioDeCuentas):
    """Lo mínimo para ver cuántas veces se escribe la marca.

    Hereda de `RepositorioDeCuentas` porque el servicio comprueba el tipo: lo
    que no lo sea lo envuelve en `CuentasSQLite` por comodidad, y entonces el
    doble deja de usarse. Los métodos abstractos se rellenan de golpe más abajo:
    esta prueba solo usa dos, y escribir treinta que lanzan es ruido.
    """

    def __init__(self, usuario: dict | None = None):
        self.toques = 0
        self.usuario = usuario or {
            "id": "usr-ana",
            "email": "ana@ejemplo.co",
            "username": "ana",
            "status": "activo",
            "role": "user",
            "display_name": "Ana",
            "avatar_url": None,
            "email_verificado": True,
            "creado_en": 1.0,
            "ultima_actividad": 1.0,
        }

    def usuario_de_sesion(self, sesion_id, ahora):
        return self.usuario

    def tocar_sesion(self, sesion_id, ahora):
        self.toques += 1


# Los abstractos que no se usan, rellenados de una vez. Si alguno se llegara a
# llamar, lanza: es mejor un error ruidoso que un `None` que se cuela.
for _nombre in RepositorioDeCuentas.__abstractmethods__:
    if not hasattr(RepoQueCuenta, _nombre) or getattr(
        getattr(RepoQueCuenta, _nombre), "__isabstractmethod__", False
    ):
        def _no_deberia(*args, _n=_nombre, **kwargs):
            raise AssertionError(f"Esta prueba no debería llamar a {_n}()")

        setattr(RepoQueCuenta, _nombre, _no_deberia)
RepoQueCuenta.__abstractmethods__ = frozenset()


@pytest.fixture(autouse=True)
def frenos_sin_echar():
    """El freno pasó de `self` a estado del PROCESO en la V2.0.3, y eso obliga
    a limpiarlo entre pruebas.

    **Antes no hacía falta, y eso era el defecto.** El contador vivía en la
    instancia y `ServicioDeCuentas` se construye en cada petición, así que
    empezaba a cero siempre: el freno que este archivo daba por probado no
    frenaba nada en producción. Estas mismas pruebas pasaban igual.

    Que ahora haya que limpiar es la señal de que el freno existe de verdad.
    """
    FRENOS.reiniciar()
    yield
    FRENOS.reiniciar()


@pytest.fixture
def servicio():
    repo = RepoQueCuenta()
    servicio = ServicioDeCuentas(repo)
    return servicio, repo


class TestNoSeEscribeEnCadaPeticion:
    def test_la_primera_vez_si(self, servicio):
        svc, repo = servicio

        svc.usuario_de_sesion("un-token")

        assert repo.toques == 1

    def test_y_las_siguientes_no(self, servicio):
        """Veinte peticiones seguidas son un viaje de red, no veinte."""
        svc, repo = servicio

        for _ in range(20):
            svc.usuario_de_sesion("un-token")

        assert repo.toques == 1, (
            f"Se escribió {repo.toques} veces en 20 peticiones. Cada una cuesta "
            "~220 ms contra Supabase, y la marca es informativa"
        )

    def test_pasado_el_plazo_se_vuelve_a_escribir(self, servicio, monkeypatch):
        """No se deja de anotar: se anota menos. La lista de sesiones sigue
        diciendo cuándo se usó cada una.
        """
        svc, repo = servicio
        svc.usuario_de_sesion("un-token")

        # Se envejece la marca en lugar de esperar cinco minutos.
        FRENOS.ultimos_toques["un-token-envejecido"] = 0.0
        monkeypatch.setattr(
            "src.identidad.cuentas._hash_token", lambda t: "un-token-envejecido"
        )

        svc.usuario_de_sesion("da-igual")

        assert repo.toques == 2

    def test_cada_sesion_lleva_su_cuenta(self, servicio):
        """Dos personas distintas no comparten el plazo: si lo compartieran, la
        marca de una dejaría sin anotar a la otra.
        """
        svc, repo = servicio

        for token in ("token-de-ana", "token-de-bea", "token-de-carlos"):
            svc.usuario_de_sesion(token)

        assert repo.toques == 3

    def test_una_sesion_que_no_vale_no_escribe_nada(self):
        repo = RepoQueCuenta()
        repo.usuario_de_sesion = lambda s, a: None
        svc = ServicioDeCuentas(repo)

        assert svc.usuario_de_sesion("un-token") is None
        assert repo.toques == 0


class TestElRegistroNoCreceSinLimite:
    def test_se_vacia_al_llegar_al_tope(self, servicio):
        """Sin esto, un despliegue con muchas sesiones a lo largo de días
        acumularía una entrada por cada una y nadie las quitaría nunca.
        """
        svc, _ = servicio
        FRENOS.ultimos_toques = {f"s{i}": time.time() for i in range(MAXIMOS_TOQUES_RECORDADOS)}

        svc.usuario_de_sesion("uno-mas")

        assert len(FRENOS.ultimos_toques) <= MAXIMOS_TOQUES_RECORDADOS

    def test_el_tope_no_es_ridiculamente_bajo(self):
        """Un tope pequeño haría que el registro se vaciara a menudo y el ahorro
        desapareciera: cada vaciado devuelve a todo el mundo a escribir.
        """
        assert MAXIMOS_TOQUES_RECORDADOS >= 1000


class TestUnFalloAlAnotarNoTumbaLaPeticion:
    def test_se_registra_y_se_sigue(self, caplog):
        """Ya estaba así y se mantiene: esto corre en peticiones de gente que
        está correctamente identificada, y un hipo del almacén al escribir una
        marca informativa respondía 500 a todo el mundo a la vez.
        """
        repo = RepoQueCuenta()

        def revienta(sesion_id, ahora):
            raise RuntimeError("la base dice que no")

        repo.tocar_sesion = revienta
        svc = ServicioDeCuentas(repo)

        with caplog.at_level("WARNING"):
            usuario = svc.usuario_de_sesion("un-token")

        assert usuario is not None
        assert caplog.records, "el fallo al anotar no queda registrado"
        assert any(
            "uso" in r.getMessage().lower() or "sesi" in r.getMessage().lower()
            for r in caplog.records
        ), f"nada reconocible en el registro: {[r.getMessage() for r in caplog.records]}"


def test_el_plazo_es_razonable():
    """Ni tan corto que no ahorre, ni tan largo que la lista de sesiones mienta."""
    assert 1 <= MINUTOS_ENTRE_TOQUES <= 30
