"""
La pregunta «¿autorizas esto?»: cuándo se hace, y qué pasa cuando no se puede.

**Por qué existe este archivo.** `_request_permission` era el bloque sin
ejecutar de `src/security/permissions.py` —el módulo que decide si una
herramienta corre—. La matriz de riesgo ya estaba probada en
`test_permissions.py`; lo que no lo estaba es la confirmación en sí, y ahí viven
tres propiedades de las que depende todo lo demás:

1. **Sin consola se deniega**, nunca se espera. Preguntar en un servidor HTTP
   bloquearía el hilo leyendo de un stdin que nadie va a atender.
2. **Un fallo de entrada se deniega.** `EOFError` es lo que llega cuando la
   entrada se cerró, y tratarlo como un «sí» convertiría una tubería cortada en
   una autorización.
3. **Lo crítico no se recuerda.** Se puede autorizar una operación moderada
   para el resto de la sesión; un borrado o una orden de shell arbitraria, no.

La consola se simula. Lo que se comprueba es la decisión, no cómo se pinta.
"""

import pytest

from src.security.permissions import PermissionManager
from src.tools.base import Tool


class Herramienta(Tool):
    """Una herramienta de mentira con el riesgo que diga la prueba."""

    def __init__(self, nombre: str, nivel: str):
        self._nombre = nombre
        self._nivel = nivel

    @property
    def name(self) -> str:
        return self._nombre

    @property
    def description(self) -> str:
        return "Una herramienta de prueba"

    @property
    def permission_level(self) -> str:
        return self._nivel

    def execute(self, **kwargs):
        return {"success": True, "data": "ok", "error": None}


class AuditoriaDeMentira:
    """Recoge lo que se registra, porque «queda auditado» es media defensa."""

    def __init__(self):
        self.entradas: list[dict] = []

    def log(self, tool_name, risk_level, authorized, args=None,
            success=True, error=None, **_):
        self.entradas.append({
            "herramienta": tool_name,
            "riesgo": risk_level,
            "autorizado": authorized,
            "error": error,
        })


@pytest.fixture
def auditoria():
    return AuditoriaDeMentira()


@pytest.fixture
def confirmaciones(monkeypatch):
    """Sustituye el `Confirm.ask` de rich por respuestas guionizadas.

    Devuelve la lista de preguntas hechas, que es lo que permite comprobar que
    **no** se preguntó algo.
    """
    def montar(*respuestas):
        pendientes = list(respuestas)
        preguntas: list[str] = []

        def responder(pregunta, default=False, **_):
            preguntas.append(pregunta)
            if not pendientes:
                raise AssertionError(f"Pregunta de más: {pregunta!r}")
            siguiente = pendientes.pop(0)
            if isinstance(siguiente, BaseException):
                raise siguiente
            return siguiente

        monkeypatch.setattr(
            "src.security.permissions.Confirm.ask", responder
        )
        return preguntas

    return montar


@pytest.fixture
def gestor(auditoria, monkeypatch):
    """Un gestor con consola, y sin el atajo del propietario.

    `_sin_confirmaciones()` corta la cadena antes de llegar a la pregunta, así
    que aquí se desactiva: lo que se está probando es justo lo que hay después.
    """
    monkeypatch.setattr(
        PermissionManager, "_sin_confirmaciones", staticmethod(lambda: False)
    )
    monkeypatch.setenv("MORGAN_MODERATE_PERMISSION_MODE", "ask")

    import src.config as config
    config.reset_settings()
    try:
        yield PermissionManager(audit_logger=auditoria, interactive=True)
    finally:
        config.reset_settings()


class TestSinConsolaSeDeniega:
    """El caso de producción: Morgan en la nube no tiene a quién preguntar.

    Esto es lo que hace que el servidor no se cuelgue, y también lo que explica
    por qué en la nube solo se registran las herramientas que no necesitan
    confirmación.
    """

    @pytest.fixture
    def sin_consola(self, auditoria, monkeypatch):
        monkeypatch.setattr(
            PermissionManager, "_sin_confirmaciones", staticmethod(lambda: False)
        )
        return PermissionManager(audit_logger=auditoria, interactive=False)

    @pytest.mark.parametrize("nivel", ["moderate", "sensitive", "critical"])
    def test_lo_que_necesita_confirmacion_se_deniega(self, sin_consola, nivel):
        assert sin_consola.check_permission(Herramienta("x", nivel)) is False

    def test_lo_seguro_sigue_pasando(self, sin_consola):
        """Denegar TODO dejaría a Morgan sin poder ni mirar la hora en la nube."""
        assert sin_consola.check_permission(Herramienta("x", "safe")) is True

    def test_no_se_pregunta_nada(self, sin_consola, confirmaciones):
        """Ni siquiera se intenta: un `Confirm.ask` sobre un stdin cerrado es lo
        que colgaría el hilo.
        """
        preguntas = confirmaciones()

        sin_consola.check_permission(Herramienta("x", "critical"))

        assert preguntas == []

    def test_la_denegacion_queda_registrada(self, sin_consola, auditoria):
        """Sin el registro, un Morgan que calla y no hace nada es
        indistinguible de uno roto.
        """
        sin_consola.check_permission(Herramienta("borrar_todo", "critical"))

        assert auditoria.entradas[-1]["autorizado"] is False
        assert auditoria.entradas[-1]["herramienta"] == "borrar_todo"


class TestLaRespuestaSeRespeta:
    def test_un_si_autoriza(self, gestor, confirmaciones):
        confirmaciones(True, False)  # autorizo; no lo recuerdes

        assert gestor.check_permission(Herramienta("x", "moderate")) is True

    def test_un_no_deniega(self, gestor, confirmaciones):
        confirmaciones(False)

        assert gestor.check_permission(Herramienta("x", "moderate")) is False

    def test_tras_un_no_no_se_ofrece_recordar(self, gestor, confirmaciones):
        """Recordar una negativa no existe, y preguntarlo sugeriría que sí."""
        preguntas = confirmaciones(False)

        gestor.check_permission(Herramienta("x", "moderate"))

        assert len(preguntas) == 1

    def test_lo_que_se_decide_es_lo_que_se_registra(self, gestor, confirmaciones,
                                                    auditoria):
        confirmaciones(False)
        gestor.check_permission(Herramienta("x", "high_risk"))

        assert auditoria.entradas[-1]["autorizado"] is False
        assert auditoria.entradas[-1]["riesgo"] == "high_risk"


class TestUnFalloDeEntradaSeDeniega:
    """`EOFError` es lo que llega cuando la entrada se cerró a media pregunta: un
    Ctrl-D, una tubería cortada, un proceso relanzado sin terminal.

    Si eso se tratara como un «sí», cerrar la terminal en el momento justo
    autorizaría la operación. Y el `default=False` de rich no cubre este caso:
    con la entrada cerrada no hay valor por defecto que devolver, hay excepción.
    """

    @pytest.mark.parametrize("fallo", [EOFError(), OSError()])
    def test_se_deniega(self, gestor, confirmaciones, fallo):
        confirmaciones(fallo)

        assert gestor.check_permission(Herramienta("x", "critical")) is False

    def test_un_ctrl_c_no_se_convierte_en_una_autorizacion(self, gestor,
                                                           confirmaciones):
        """`KeyboardInterrupt` NO se captura, y es lo correcto: debe subir y
        cortar el turno. Lo que no puede es acabar en un `return True`.
        """
        confirmaciones(KeyboardInterrupt())

        with pytest.raises(KeyboardInterrupt):
            gestor.check_permission(Herramienta("x", "critical"))


class TestRecordarDuranteLaSesion:
    def test_recordado_no_se_vuelve_a_preguntar(self, gestor, confirmaciones):
        preguntas = confirmaciones(True, True)  # autorizo, y recuérdalo

        herramienta = Herramienta("leer_algo", "moderate")
        assert gestor.check_permission(herramienta) is True
        assert gestor.check_permission(herramienta) is True

        assert len(preguntas) == 2, (
            "Se ha vuelto a preguntar por algo que se dijo recordar"
        )

    def test_sin_recordar_se_pregunta_cada_vez(self, gestor, confirmaciones):
        preguntas = confirmaciones(True, False, True, False)

        herramienta = Herramienta("leer_algo", "moderate")
        gestor.check_permission(herramienta)
        gestor.check_permission(herramienta)

        assert len(preguntas) == 4

    def test_recordar_una_no_recuerda_las_demas(self, gestor, confirmaciones):
        """Se autoriza *una herramienta*, no un nivel de riesgo."""
        confirmaciones(True, True, False)

        gestor.check_permission(Herramienta("una", "moderate"))

        assert gestor.check_permission(Herramienta("otra", "moderate")) is False

    def test_un_fallo_al_preguntar_si_recordar_no_lo_recuerda(self, gestor,
                                                              confirmaciones):
        """El lado seguro del error. Recordarlo por defecto convertiría un fallo
        de entrada en un permiso que dura toda la sesión.

        La autorización ya concedida se mantiene: la persona dijo «sí» a *esta*
        ejecución antes de que la entrada se rompiera, y retirárselo sería
        descartar una respuesta que sí llegó.
        """
        confirmaciones(True, EOFError())

        herramienta = Herramienta("leer_algo", "moderate")
        assert gestor.check_permission(herramienta) is True
        assert "leer_algo" not in gestor._session_allowed


class TestLoCriticoNoSeRecuerdaNunca:
    """La propiedad que más importa de este archivo.

    Una operación moderada se puede autorizar para el resto de la sesión: el
    coste de equivocarse es local y visible. Una crítica —una orden de shell
    arbitraria, un borrado, matar un proceso— se pregunta **siempre**, porque la
    siguiente vez no se parece a esta: cambian los argumentos, y es en los
    argumentos donde está el daño.
    """

    @pytest.mark.parametrize("nivel", ["critical", "sensitive"])
    def test_no_se_ofrece_recordarlo(self, gestor, confirmaciones, nivel):
        preguntas = confirmaciones(True)

        gestor.check_permission(Herramienta("ejecutar_orden", nivel))

        assert len(preguntas) == 1, (
            f"Se ha ofrecido recordar una autorización de nivel {nivel}"
        )

    @pytest.mark.parametrize("nivel", ["critical", "sensitive"])
    def test_se_pregunta_otra_vez_a_la_siguiente(self, gestor, confirmaciones, nivel):
        preguntas = confirmaciones(True, True)

        herramienta = Herramienta("ejecutar_orden", nivel)
        gestor.check_permission(herramienta)
        gestor.check_permission(herramienta)

        assert len(preguntas) == 2
        assert "ejecutar_orden" not in gestor._session_allowed

    def test_y_lo_moderado_si(self, gestor, confirmaciones):
        """El contraste. Sin esta prueba, un `if False` de más pasaría las tres
        de arriba y nadie notaría que ya no se puede recordar nada.
        """
        confirmaciones(True, True)

        gestor.check_permission(Herramienta("leer_algo", "moderate"))

        assert "leer_algo" in gestor._session_allowed


class TestLoQueLaPreguntaTieneQueDecir:
    """Una confirmación que no dice qué se va a hacer no es una confirmación.

    Es el punto donde la persona es la única defensa que queda, y decide con lo
    que se le enseñe. Los argumentos son lo que más importa: `delete_file` suena
    igual de mal con cualquier ruta, y la ruta es la diferencia entre un fichero
    temporal y el proyecto entero.
    """

    def test_se_enseñan_los_argumentos(self, gestor, confirmaciones, capsys):
        confirmaciones(False)

        gestor.check_permission(
            Herramienta("delete_file", "critical"),
            {"path": "C:/Users/ana/Documents/proyecto"},
        )

        salida = capsys.readouterr().out
        assert "C:/Users/ana/Documents/proyecto" in salida
        assert "delete_file" in salida

    def test_un_argumento_larguisimo_se_recorta(self, gestor, confirmaciones, capsys):
        """El modelo puede pasar un fichero entero como argumento. Volcarlo
        empuja fuera de la pantalla la línea que dice qué se autoriza, que es la
        que hay que leer.
        """
        confirmaciones(False)

        gestor.check_permission(
            Herramienta("create_file", "critical"), {"contenido": "x" * 5000}
        )

        salida = capsys.readouterr().out
        assert "..." in salida
        assert "x" * 500 not in salida

    def test_se_dice_el_nivel_de_riesgo(self, gestor, confirmaciones, capsys):
        confirmaciones(False)

        gestor.check_permission(Herramienta("x", "critical"))

        assert "CRITICAL" in capsys.readouterr().out


class TestLaRedDeAbajo:
    """El último `return False` de `check_permission` es **inalcanzable hoy**, y
    tiene que seguir estando.

    Comprobado: los seis niveles de `LEVELS` los atienden los pasos 5, 6 y 7, y
    el paso 0 rechaza cualquier cosa que no esté en `LEVELS`. No queda ningún
    valor que llegue al final.

    Se deja porque lo que protege no es un valor, es un **cambio futuro**: quien
    añada un nivel séptimo a `LEVELS` y olvide encajarlo en la matriz obtendrá
    una denegación con su motivo en la auditoría, no una autorización silenciosa
    por caerse del último `if`. Es la diferencia entre un despiste y un agujero.
    """

    def test_un_nivel_nuevo_sin_regla_se_deniega(self, gestor, monkeypatch):
        monkeypatch.setattr(
            PermissionManager, "LEVELS",
            (*PermissionManager.LEVELS, "nivel_que_alguien_añadira"),
        )

        permitido = gestor.check_permission(
            Herramienta("x", "nivel_que_alguien_añadira")
        )

        assert permitido is False, (
            "Un nivel de riesgo aceptado pero sin regla se está autorizando"
        )

    def test_y_se_dice_por_qué(self, gestor, auditoria, monkeypatch):
        """Sin el motivo, el síntoma es «la herramienta no hace nada» y la causa
        está a siete pasos de distancia.
        """
        monkeypatch.setattr(
            PermissionManager, "LEVELS", (*PermissionManager.LEVELS, "nuevo"),
        )

        gestor.check_permission(Herramienta("x", "nuevo"))

        assert "desconocido" in (auditoria.entradas[-1]["error"] or "")

    def test_hoy_todos_los_niveles_declarados_tienen_regla(self):
        """La otra mitad del invariante, y la que avisa antes: si este test
        falla, alguien amplió `LEVELS` sin tocar la matriz.
        """
        from src.tools.base import RiskLevel

        con_regla = {
            RiskLevel.SAFE.value, RiskLevel.LOW_RISK.value, "safe",
            RiskLevel.MODERATE.value, "moderate",
            RiskLevel.HIGH_RISK.value, RiskLevel.CRITICAL.value,
            "sensitive", "critical",
        }
        sin_regla = set(PermissionManager.LEVELS) - con_regla

        assert not sin_regla, f"Niveles declarados y sin atender: {sin_regla}"
