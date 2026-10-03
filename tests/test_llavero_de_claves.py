"""
Varias claves del mismo proveedor: el llavero y la rotación de Groq.

## Qué se fija aquí, y por qué importa cada cosa

La cuota de Groq es **de la cuenta**, así que dos claves de dos cuentas son dos
cupos. Eso está medido —el contador de peticiones de las dos claves se movió por
separado— y es lo único que sube el techo de capacidad, que la medición de la
2.0.5 señaló como el límite real que queda.

Las tres propiedades que no se pueden perder:

1. **El llavero nunca se queda vacío.** Si todas las claves parecen agotadas se
   usa la primera igual. Es la misma asimetría de `cuota.py`: un rechazo cuesta
   0,02 s y quedarse sin clave es que Morgan no contesta.
2. **Solo se rota por cuota.** Un 401 o un 400 le pasarían igual a todas las
   claves, así que reintentar solo multiplicaría por N el tiempo que tarda en
   fallar.
3. **El gasto se apunta por clave.** Sumar dos cupos en un solo contador haría
   que el aviso del 80% saltara a mitad del primero.
"""

import pytest

from src.models.cuota import CUOTAS, familia
from src.models.llavero import Llavero

#: Claves falsas con la forma de una de verdad. Tienen que pasar `claves.py`,
#: cuyo mínimo son 16 caracteres: ninguna clave real baja de 39.
CLAVE_A = "gsk_primera-clave-falsa-de-la-cuenta-uno00"
CLAVE_B = "gsk_segunda-clave-falsa-de-la-cuenta-dos00"
CLAVE_C = "gsk_tercera-clave-falsa-de-la-cuenta-tres0"


@pytest.fixture(autouse=True)
def _cuotas_limpias():
    """El registro de cuotas es estado de proceso: se limpia entre pruebas."""
    CUOTAS.reiniciar()
    yield
    CUOTAS.reiniciar()


class TestElLlavero:
    def test_una_sola_clave_no_se_numera(self):
        """Numerar algo sin hermanos solo añade ruido, y rompería la
        continuidad de lo que ya estaba apuntado como `Groq`."""
        assert Llavero("Groq", (CLAVE_A,)).etiquetas == ("Groq",)

    def test_varias_claves_se_numeran_desde_uno(self):
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B, CLAVE_C))

        assert llavero.etiquetas == ("Groq#1", "Groq#2", "Groq#3")

    def test_la_etiqueta_nunca_contiene_la_clave(self):
        """Un registro con medio secreto dentro es un secreto filtrado, y estos
        registros se leen desde el panel de Render."""
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B))

        for etiqueta in llavero.etiquetas:
            assert CLAVE_A not in etiqueta
            assert CLAVE_B not in etiqueta
            assert len(etiqueta) < 16, f"«{etiqueta}» es sospechosamente larga"

    def test_un_llavero_sin_claves_es_un_error(self):
        with pytest.raises(ValueError, match="sin ninguna clave"):
            Llavero("Groq", ())

    def test_las_cadenas_vacias_no_cuentan_como_clave(self):
        with pytest.raises(ValueError):
            Llavero("Groq", ("", None))  # type: ignore[arg-type]

    def test_el_orden_por_defecto_es_el_de_la_lista(self):
        """En serie y no por turnos: se gasta la primera y la segunda queda
        intacta. Ver la cabecera de `llavero.py`."""
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B))

        assert [c for _, c in llavero.turnos()] == [CLAVE_A, CLAVE_B]

    def test_una_clave_agotada_pasa_al_final(self):
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B))

        CUOTAS.marcar_agotado("Groq#1", 300)

        assert [e for e, _ in llavero.turnos()] == ["Groq#2", "Groq#1"]

    def test_con_todas_agotadas_se_devuelven_todas(self):
        """**La propiedad de seguridad.** Reordenar no puede dejar a Morgan sin
        clave: saltar es una optimización, no una regla."""
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B))

        CUOTAS.marcar_agotado("Groq#1", 300)
        CUOTAS.marcar_agotado("Groq#2", 300)

        turnos = llavero.turnos()
        assert len(turnos) == 2
        assert [c for _, c in turnos] == [CLAVE_A, CLAVE_B]

    def test_turnos_nunca_acorta_la_lista(self):
        """El invariante en una sola frase, para los tres casos de agotamiento."""
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B, CLAVE_C))

        for agotadas in ([], ["Groq#1"], ["Groq#1", "Groq#2"],
                         ["Groq#1", "Groq#2", "Groq#3"]):
            CUOTAS.reiniciar()
            for e in agotadas:
                CUOTAS.marcar_agotado(e, 300)
            assert len(llavero.turnos()) == 3, f"con {agotadas} agotada(s)"

    def test_la_ventana_de_agotamiento_caduca(self):
        """Es una estimación con fecha, no un destierro."""
        llavero = Llavero("Groq", (CLAVE_A, CLAVE_B))

        CUOTAS.marcar_agotado("Groq#1", 1)
        assert [e for e, _ in llavero.turnos()][0] == "Groq#2"

        import time

        time.sleep(1.05)
        assert [e for e, _ in llavero.turnos()][0] == "Groq#1"


class TestLaFamiliaDeUnaEtiqueta:
    """El tope de cuota se conoce por proveedor, y las etiquetas llegan de tres
    formas distintas. Sin esto, `Groq#2` no encontraría el tope de nadie y el
    aviso del 80% habría dejado de salir sin decir nada.
    """

    @pytest.mark.parametrize("etiqueta", [
        "groq", "Groq", "GROQ",
        "Groq:openai/gpt-oss-120b",
        "Groq#2",
        "Groq#2:openai/gpt-oss-120b",
    ])
    def test_todas_las_formas_dicen_groq(self, etiqueta):
        assert familia(etiqueta) == "groq"

    def test_y_el_tope_se_encuentra_con_una_clave_numerada(self):
        from src.models.cuota import AVISAR_AL, CUOTA_DIARIA_CONOCIDA

        CUOTAS.apuntar_uso("Groq#2", int(CUOTA_DIARIA_CONOCIDA["groq"] * AVISAR_AL), 0)

        assert CUOTAS.resumen()["Groq#2"]["tope_conocido"] == 200_000


class TestLaConfiguracionLeeLasClavesNumeradas:
    def _sin_claves_de_groq(self, monkeypatch):
        """Se ponen a vacío, **no se borran**.

        `load_settings()` llama a `load_dotenv()`, que rellena las variables que
        no existen leyendo el `.env` de verdad. Borrarlas hace justo lo
        contrario de lo que se quiere: deja hueco para que entren las claves
        reales del disco y la prueba mida otra cosa. Con la cadena vacía la
        variable existe y `_env_key` la trata como no configurada.
        """
        for n in range(1, 8):
            monkeypatch.setenv(
                "GROQ_API_KEY" if n == 1 else f"GROQ_API_KEY_{n}", "",
            )

    def test_una_sola_clave(self, monkeypatch):
        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)

        s = load_settings()
        assert s.groq_api_keys == (CLAVE_A,)
        assert s.groq_api_key == CLAVE_A

    def test_dos_claves_numeradas(self, monkeypatch):
        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)
        monkeypatch.setenv("GROQ_API_KEY_2", CLAVE_B)

        assert load_settings().groq_api_keys == (CLAVE_A, CLAVE_B)

    def test_la_primera_de_la_lista_es_groq_api_key(self, monkeypatch):
        """Las dos salen de la misma lectura para que no puedan discrepar: hay
        código que sigue leyendo `groq_api_key`, como la transcripción de audio.
        """
        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)
        monkeypatch.setenv("GROQ_API_KEY_2", CLAVE_B)

        s = load_settings()
        assert s.groq_api_key == s.groq_api_keys[0] == CLAVE_A

    def test_un_hueco_corta_la_serie(self, monkeypatch, caplog):
        """Si hay `_2` y `_4` pero no `_3`, se queda con dos **y lo dice**.

        Seguir adelante esconderia una variable mal nombrada, que es justo el
        fallo que esto pretende hacer visible: tres de los 18 secretos salieron
        mal cuando se recreó el servicio de producción.
        """
        import logging

        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)
        monkeypatch.setenv("GROQ_API_KEY_2", CLAVE_B)
        monkeypatch.setenv("GROQ_API_KEY_4", CLAVE_C)

        with caplog.at_level(logging.WARNING, logger="src.config"):
            claves = load_settings().groq_api_keys

        assert claves == (CLAVE_A, CLAVE_B)
        assert "GROQ_API_KEY_4" in caplog.text
        assert "GROQ_API_KEY_3" in caplog.text, (
            "El aviso tiene que decir qué variable falta, no solo que falta una"
        )

    def test_la_misma_clave_dos_veces_no_cuenta_dos(self, monkeypatch, caplog):
        """Repetirla no da más cuota —es la misma cuenta— y parecería que sí."""
        import logging

        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)
        monkeypatch.setenv("GROQ_API_KEY_2", CLAVE_A)

        with caplog.at_level(logging.WARNING, logger="src.config"):
            claves = load_settings().groq_api_keys

        assert claves == (CLAVE_A,)
        assert "GROQ_API_KEY_2" in caplog.text

    def test_sin_ninguna_clave_la_lista_esta_vacia(self, monkeypatch):
        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)

        s = load_settings()
        assert s.groq_api_keys == ()
        assert s.groq_api_key is None

    def test_hay_un_tope_de_claves(self, monkeypatch):
        """Para que el arranque no recorra variables sin fin."""
        from src.config import MAXIMO_CLAVES_POR_PROVEEDOR, load_settings

        self._sin_claves_de_groq(monkeypatch)
        for n in range(1, MAXIMO_CLAVES_POR_PROVEEDOR + 3):
            monkeypatch.setenv(
                "GROQ_API_KEY" if n == 1 else f"GROQ_API_KEY_{n}",
                f"gsk_clave-falsa-numero-{n}-con-longitud-suficiente",
            )

        assert len(load_settings().groq_api_keys) == MAXIMO_CLAVES_POR_PROVEEDOR

    def test_la_vista_para_registros_dice_cuantas_y_no_cuales(self, monkeypatch):
        from src.config import load_settings

        self._sin_claves_de_groq(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)
        monkeypatch.setenv("GROQ_API_KEY_2", CLAVE_B)

        vista = load_settings().redacted()

        assert "2" in vista["groq_api_key"]
        assert CLAVE_A not in str(vista) and CLAVE_B not in str(vista)


# --- La rotación de verdad, en el proveedor ----------------------------------


class _ClienteFalso:
    """Un doble del cliente de Groq que cuenta llamadas y puede fallar.

    Cada instancia representa **una clave**, y por eso lleva su propia cuenta:
    las pruebas de aquí no comprueban qué se envía, comprueban **a cuál se
    llama y en qué orden**, que es lo único que la rotación puede equivocar.
    """

    def __init__(self, nombre: str, error: Exception | None = None):
        self.nombre = nombre
        self.error = error
        self.llamadas = 0
        self.chat = self  # para que `cliente.chat.completions` funcione
        self.completions = self

    def create(self, **_):
        from unittest.mock import MagicMock

        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return MagicMock(
            choices=[MagicMock(message=MagicMock(tool_calls=None,
                                                 content=self.nombre))],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        )


def _proveedor_con(monkeypatch, *clientes: _ClienteFalso):
    """Un `GroqProvider` con un cliente falso por clave, en ese orden."""
    from unittest.mock import MagicMock, patch

    from src.config import reset_settings

    claves = (CLAVE_A, CLAVE_B, CLAVE_C)[:len(clientes)]
    for n, clave in enumerate(claves, start=1):
        monkeypatch.setenv("GROQ_API_KEY" if n == 1 else f"GROQ_API_KEY_{n}", clave)
    for n in range(len(claves) + 1, 8):
        monkeypatch.setenv(f"GROQ_API_KEY_{n}", "")
    reset_settings()

    with patch("src.models.groq.Groq", MagicMock()):
        from src.models.groq import GroqProvider

        proveedor = GroqProvider()

    assert len(proveedor._llavero) == len(clientes), (
        f"se esperaban {len(clientes)} claves y el llavero tiene "
        f"{len(proveedor._llavero)}"
    )
    proveedor._clientes = dict(zip(proveedor._llavero.etiquetas, clientes))
    return proveedor


def _un_429(mensaje="Rate limit reached: 429 too many requests"):
    return RuntimeError(mensaje)


class TestLaRotacionEntreClaves:
    def _mensaje(self):
        from src.models.base import ChatMessage

        return [ChatMessage(role="user", content="hola")]

    def test_con_dos_claves_solo_se_llama_a_la_primera(self, monkeypatch):
        """En serie, no por turnos: la segunda es una reserva y queda intacta."""
        a, b = _ClienteFalso("a"), _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        assert proveedor.generate(self._mensaje()).content == "a"
        assert (a.llamadas, b.llamadas) == (1, 0)

    def test_un_429_en_la_primera_pasa_a_la_segunda(self, monkeypatch):
        """Lo que sube el techo: la segunda cuenta tiene su propio cupo."""
        a, b = _ClienteFalso("a", _un_429()), _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        assert proveedor.generate(self._mensaje()).content == "b"
        assert (a.llamadas, b.llamadas) == (1, 1)

    def test_y_el_turno_siguiente_empieza_ya_por_la_segunda(self, monkeypatch):
        """El rechazo se recuerda. Sin esto se paga un 429 en cada llamada, y un
        turno hace entre una y cinco."""
        a, b = _ClienteFalso("a", _un_429()), _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        proveedor.generate(self._mensaje())
        proveedor.generate(self._mensaje())

        assert a.llamadas == 1, "se ha vuelto a llamar a una clave agotada"
        assert b.llamadas == 2

    def test_un_401_no_rota(self, monkeypatch):
        """**Solo se rota por cuota.**

        Un 401 o un 400 le pasarían igual a todas las claves: es la misma
        petición y el mismo modelo. Reintentar solo multiplicaría por N lo que
        tarda en fallar, y en producción eso se paga en el plazo del turno.
        """
        a = _ClienteFalso("a", RuntimeError("401 Invalid API Key"))
        b = _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        with pytest.raises(RuntimeError, match="401"):
            proveedor.generate(self._mensaje())

        assert b.llamadas == 0, "un 401 ha gastado un viaje a la segunda clave"

    def test_si_las_dos_se_agotan_se_propaga_el_error(self, monkeypatch):
        """Para que la cadena pueda pasar a Gemini. Tragárselo dejaría a Morgan
        sin contestar teniendo dos proveedores más disponibles."""
        a = _ClienteFalso("a", _un_429())
        b = _ClienteFalso("b", _un_429("429 rate limit on the second one"))
        proveedor = _proveedor_con(monkeypatch, a, b)

        with pytest.raises(RuntimeError, match="429"):
            proveedor.generate(self._mensaje())

        assert (a.llamadas, b.llamadas) == (1, 1)

    def test_las_dos_quedan_apuntadas_como_agotadas(self, monkeypatch):
        """Incluida la última. Es lo que hace que la cadena posponga Groq entero
        cuando de verdad no queda ninguna clave."""
        a = _ClienteFalso("a", _un_429())
        b = _ClienteFalso("b", _un_429())
        proveedor = _proveedor_con(monkeypatch, a, b)

        with pytest.raises(RuntimeError):
            proveedor.generate(self._mensaje())

        assert CUOTAS.agotado("Groq#1") and CUOTAS.agotado("Groq#2")

    def test_con_todas_agotadas_se_llama_igual(self, monkeypatch):
        """La propiedad de seguridad, vista desde el proveedor: una ventana de
        agotamiento equivocada no puede dejar a Morgan sin llamar a nadie."""
        a, b = _ClienteFalso("a"), _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        CUOTAS.marcar_agotado("Groq#1", 300)
        CUOTAS.marcar_agotado("Groq#2", 300)

        assert proveedor.generate(self._mensaje()).content == "a"

    def test_tres_claves_se_prueban_las_tres(self, monkeypatch):
        a = _ClienteFalso("a", _un_429())
        b = _ClienteFalso("b", _un_429())
        c = _ClienteFalso("c")
        proveedor = _proveedor_con(monkeypatch, a, b, c)

        assert proveedor.generate(self._mensaje()).content == "c"
        assert (a.llamadas, b.llamadas, c.llamadas) == (1, 1, 1)

    def test_el_gasto_se_apunta_a_la_clave_que_respondio(self, monkeypatch):
        """Por clave y no por proveedor: dos claves son dos cupos, y sumarlos
        juntos haría que el aviso del 80% saltara a mitad del primero."""
        a, b = _ClienteFalso("a", _un_429()), _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        proveedor.generate(self._mensaje())

        resumen = CUOTAS.resumen()
        assert resumen["Groq#2"]["tokens"] == 15
        assert resumen["Groq#1"]["tokens"] == 0, (
            "el gasto se ha apuntado a la clave que no respondió"
        )

    def test_una_sola_clave_se_apunta_sin_numero(self, monkeypatch):
        """Continuidad: lo que ya estaba apuntado como `Groq` sigue igual."""
        a = _ClienteFalso("a")
        proveedor = _proveedor_con(monkeypatch, a)

        proveedor.generate(self._mensaje())

        assert "Groq" in CUOTAS.resumen()
        assert not any("#" in k for k in CUOTAS.resumen())


class TestLaCadenaRevisaTodasLasClaves:
    """Con varias claves, una mal copiada no puede llevarse por delante a las
    buenas.

    Es el fallo que `claves.py` vino a evitar, en su versión con llavero: al
    recrear el servicio de producción hubo que teclear 18 secretos y tres
    salieron mal, uno de ellos con un símbolo de libra dentro.
    """

    #: Con una libra dentro, igual que la de Gemini que rompió producción.
    IMPOSIBLE = "gsk_con-una-libra-£-dentro-no-puede-funcionar"

    def _solo_estas(self, monkeypatch, *claves):
        for n, clave in enumerate(claves, start=1):
            monkeypatch.setenv(
                "GROQ_API_KEY" if n == 1 else f"GROQ_API_KEY_{n}", clave,
            )
        for n in range(len(claves) + 1, 8):
            monkeypatch.setenv(f"GROQ_API_KEY_{n}", "")

    def test_una_clave_imposible_se_cae_sola(self, monkeypatch, caplog):
        import logging

        from src.config import load_settings
        from src.models.chain import _construir

        self._solo_estas(monkeypatch, CLAVE_A, self.IMPOSIBLE)

        with caplog.at_level(logging.ERROR, logger="src.models.chain"):
            proveedor = _construir("groq", load_settings())

        assert proveedor is not None, "una clave mala se ha llevado a la buena"
        assert proveedor.claves_disponibles == 1
        assert "GROQ_API_KEY_2" in caplog.text, (
            "El aviso tiene que decir QUÉ variable arreglar en el panel"
        )

    def test_si_todas_son_imposibles_no_hay_proveedor(self, monkeypatch):
        from src.config import load_settings
        from src.models.chain import _construir

        self._solo_estas(monkeypatch, self.IMPOSIBLE, "corta")

        assert _construir("groq", load_settings()) is None

    def test_a_las_claves_descartadas_no_se_las_llama(self, monkeypatch):
        """La cadena le pasa las claves ya revisadas al proveedor en lugar de
        dejar que las relea. Si una se descartó, no puede volver por la puerta
        de atrás."""
        from src.config import load_settings
        from src.models.chain import _construir

        self._solo_estas(monkeypatch, CLAVE_A, self.IMPOSIBLE, CLAVE_C)

        proveedor = _construir("groq", load_settings())

        assert proveedor is not None
        assert [c for _, c in proveedor._llavero.turnos()] == [CLAVE_A, CLAVE_C]


class TestStatusDiceCuantasClaves:
    """Con varias claves por proveedor, `/status` tiene que decir cuantas.

    Es la version nueva de un fallo real: cuando se recreo el servicio de
    produccion, dos claves de modelo salieron mal escritas y **todo parecia
    funcionar**. `/status` decia que el modelo estaba disponible y listaba la
    cadena entera; lo unico distinto era que el proveedor de pago contestaba
    todos los turnos.

    Si `GROQ_API_KEY_2` esta mal copiada, Morgan se cae a una sola clave y la
    cuota vuelve a la mitad. El aviso existe, pero solo en el registro.
    """

    def _proveedor_con(self, cuantas: int):
        from unittest.mock import MagicMock, patch

        from src.config import reset_settings

        with patch.dict("os.environ", {
            **{
                ("GROQ_API_KEY" if n == 1 else f"GROQ_API_KEY_{n}"):
                f"gsk_clave-falsa-numero-{n}-con-longitud-de-sobra"
                for n in range(1, cuantas + 1)
            },
            **{f"GROQ_API_KEY_{n}": "" for n in range(cuantas + 1, 8)},
        }):
            reset_settings()
            with patch("src.models.groq.Groq", MagicMock()):
                from src.models.groq import GroqProvider

                return GroqProvider()

    def test_con_dos_claves_lo_dice(self):
        from src.api.routes.health import _cuantas_claves

        assert _cuantas_claves(self._proveedor_con(2)) == " (2 claves)"

    def test_con_una_no_dice_nada(self):
        """Decir «(1 claves)» seria ruido en el caso normal."""
        from src.api.routes.health import _cuantas_claves

        assert _cuantas_claves(self._proveedor_con(1)) == ""

    def test_nunca_dice_cuales(self):
        from src.api.routes.health import _cuantas_claves

        proveedor = self._proveedor_con(2)
        texto = _cuantas_claves(proveedor)

        for _, clave in proveedor._llavero.turnos():
            assert clave not in texto
        assert "gsk_" not in texto

    def test_funciona_a_traves_de_la_cadena(self):
        """El modelo que llega a `/status` suele ser un `FallbackProvider`, no
        el proveedor suelto. Si solo funcionara con el suelto, el numero no
        saldria nunca en produccion."""
        from src.api.routes.health import _cuantas_claves
        from src.models.fallback import FallbackProvider
        from src.models.mock import MockLLMProvider

        cadena = FallbackProvider(self._proveedor_con(2), MockLLMProvider())

        assert _cuantas_claves(cadena) == " (2 claves)"

    def test_un_proveedor_sin_llavero_no_rompe_nada(self):
        """`MockLLMProvider` y los demas no tienen `claves_disponibles`."""
        from src.api.routes.health import _cuantas_claves
        from src.models.mock import MockLLMProvider

        assert _cuantas_claves(MockLLMProvider()) == ""

    def test_el_numero_de_claves_llega_a_status(self):
        """Que el dato exista no sirve si no sale por la ruta.

        Esta prueba nació de una mutación que **sobrevivió**: quitar la llamada
        a `_cuantas_claves` del detalle de `/status` no rompía nada. Las cinco
        pruebas de arriba comprobaban la función y ninguna comprobaba que su
        resultado llegara a algún sitio.

        Es el mismo error, en pequeño, que la lección que este proyecto lleva
        cuatro veces aprendiendo: una prueba verde demuestra que el código pasa,
        no que la prueba sirva.
        """
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from src.agent.core import Agent
        from src.api.app import app
        from src.api.dependencies import CoreContainer, get_container

        proveedor = self._proveedor_con(2)
        contenedor = CoreContainer()
        with patch.object(Agent, "__init__", lambda self, *a, **k: None):
            agente = Agent()
        agente.model = proveedor
        contenedor.agent = agente

        app.dependency_overrides[get_container] = lambda: contenedor
        try:
            datos = TestClient(app).get("/status").json()
        finally:
            app.dependency_overrides.pop(get_container, None)

        detalle = datos["components"]["llm"]["details"]
        assert "2 claves" in detalle, (
            f"/status no dice cuantas claves hay: «{detalle}»"
        )


# --- Los dos limites de Groq, que no son el mismo ----------------------------


class TestLaEsperaEnMilisegundos:
    """Groq tiene dos limites y los dos dan un `429`:

    | Limite | Lo que dice | Que conviene hacer |
    |---|---|---|
    | 8.000 tokens por **minuto** | «try again in 112ms» | esperar |
    | 200.000 tokens por **dia** | «try again in 10m53s» | irse a otro |

    El patron de milisegundos **no existia**, asi que el primero se leia como
    «no dice cuanto» y caia en `VENTANA_POR_DEFECTO`: una espera de 112
    milisegundos se convertia en un veto de 60 segundos sobre esa clave.
    """

    @pytest.mark.parametrize("mensaje, esperado", [
        ("Please try again in 112.499999ms", 0.112499999),
        ("Please try again in 570ms", 0.57),
        ("please TRY AGAIN IN 1500ms", 1.5),
    ])
    def test_se_leen_los_milisegundos(self, mensaje, esperado):
        from src.models.cuota import segundos_hasta_reintentar

        assert segundos_hasta_reintentar(mensaje) == pytest.approx(esperado)

    def test_y_los_segundos_siguen_leyendose(self):
        """El patron nuevo va antes; que no se coma al viejo."""
        from src.models.cuota import segundos_hasta_reintentar

        assert segundos_hasta_reintentar(
            "Please try again in 6m27.504s") == pytest.approx(387.504)
        assert segundos_hasta_reintentar(
            "Please try again in 10.5s") == pytest.approx(10.5)

    def test_una_espera_de_decimas_no_se_redondea_a_un_segundo(self):
        """El suelo era 1 segundo, y aplastaba los 112 ms contra el.

        Redondear hacia arriba convertia una pausa inapreciable en un veto que
        duraba casi diez veces mas que la espera que el proveedor pedia.
        """
        espera = CUOTAS.marcar_agotado("Groq#1", 0.112)

        assert espera == pytest.approx(0.112), (
            f"una espera de 112 ms se ha guardado como {espera} s"
        )

    def test_el_mensaje_real_de_produccion_se_lee_entero(self):
        """Copiado literal de los registros, no reescrito a mano."""
        from src.models.cuota import (
            es_rechazo_por_cuota,
            segundos_hasta_reintentar,
        )

        real = (
            "Error code: 429 - {'error': {'message': 'Rate limit reached for "
            "model `openai/gpt-oss-120b` in organization `org_01m29xs3` "
            "service tier `on_demand` on tokens per minute (TPM): Limit 8000, "
            "Used 4331, Requested 3684. Please try again in 112.499999ms. "
            "Need more tokens? Upgrade to Dev Tier today', "
            "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
        )

        assert es_rechazo_por_cuota(RuntimeError(real))
        assert segundos_hasta_reintentar(real) == pytest.approx(0.1125)


class TestUnaEsperaCortaSeEsperaEnLugarDeAbandonarGroq:
    """El defecto que encontró la medición del primer punto de la 2.1.

    Morgan trataba los dos límites de Groq igual. Con las dos claves al límite
    **por minuto**, abandonaba Groq por una espera de 0,1 s, se iba a Gemini
    —que tardó 29 s en dar un 504— y acabó pagando a OpenAI. El turno costó
    **93 segundos y dinero** cuando esperar una décima habría bastado.

    El orden correcto es de más barato a más caro: otra clave, esperar,
    rendirse.
    """

    def _mensaje(self):
        from src.models.base import ChatMessage

        return [ChatMessage(role="user", content="hola")]

    def test_con_todas_al_limite_por_minuto_se_espera_y_se_reintenta(
        self, monkeypatch
    ):
        a = _ClienteFalso("a", _un_429("429 rate limit. Please try again in 50ms"))
        b = _ClienteFalso("b", _un_429("429 rate limit. Please try again in 50ms"))
        proveedor = _proveedor_con(monkeypatch, a, b)

        # A la segunda vuelta, la primera clave ya funciona: es lo que pasa de
        # verdad cuando la ventana del minuto se rellena.
        def dejar_de_fallar():
            a.error = None

        dormido = []
        monkeypatch.setattr(
            "src.models.groq.time.sleep",
            lambda s: (dormido.append(s), dejar_de_fallar()),
        )

        assert proveedor.generate(self._mensaje()).content == "a"
        assert dormido, "no ha esperado: se ha ido a otro proveedor"
        assert dormido[0] == pytest.approx(0.05)
        assert a.llamadas == 2, "no ha reintentado la clave que pidio esperar"

    def test_una_espera_larga_no_se_espera(self, monkeypatch):
        """Esa sí es la cuota diaria, y esperar diez minutos no es una opción:
        ahí cambiar de proveedor es lo correcto."""
        largo = "429 rate limit. Please try again in 10m53.184s"
        a = _ClienteFalso("a", _un_429(largo))
        b = _ClienteFalso("b", _un_429(largo))
        proveedor = _proveedor_con(monkeypatch, a, b)

        dormido = []
        monkeypatch.setattr("src.models.groq.time.sleep", dormido.append)

        with pytest.raises(RuntimeError):
            proveedor.generate(self._mensaje())

        assert not dormido, "ha esperado once minutos en mitad de un turno"
        assert (a.llamadas, b.llamadas) == (1, 1)

    def test_la_espera_total_no_pasa_del_presupuesto(self, monkeypatch):
        """Un proveedor que siempre dijera «vuelve en 100ms» no puede dejar el
        turno dando vueltas hasta agotar su plazo.

        Lo que acota esto es un **presupuesto de reloj**, y por eso la prueba
        mueve un reloj falso: con el de verdad detenido, medir tiempo no mide
        nada. La primera version parcheaba `sleep` sin tocar `monotonic` y
        **el bucle no terminaba**; lo que lo destapó fue justo esta prueba.
        """
        corto = "429 rate limit. Please try again in 100ms"
        a = _ClienteFalso("a", _un_429(corto))
        b = _ClienteFalso("b", _un_429(corto))
        proveedor = _proveedor_con(monkeypatch, a, b)

        from src.models.cuota import ESPERA_CORTA_MAXIMA

        reloj = [1_000.0]
        dormido = []

        def dormir(s):
            dormido.append(s)
            reloj[0] += s

        monkeypatch.setattr("src.models.groq.time.sleep", dormir)
        monkeypatch.setattr("src.models.groq.time.monotonic", lambda: reloj[0])

        with pytest.raises(RuntimeError):
            proveedor.generate(self._mensaje())

        assert sum(dormido) <= ESPERA_CORTA_MAXIMA, (
            f"ha esperado {sum(dormido):.1f} s con un presupuesto de "
            f"{ESPERA_CORTA_MAXIMA}"
        )
        assert dormido, "no ha esperado nada, teniendo presupuesto"

    def test_hay_un_tope_de_vueltas_aunque_el_reloj_no_avance(self, monkeypatch):
        """La garantía, aparte de la política.

        El presupuesto de reloj expresa la intención. Si el reloj no avanza
        —porque alguien lo congeló, o porque el sistema lo hace raro— el tope
        de vueltas es lo que asegura que el bucle termina. Sin él, esto no
        volvía.
        """
        corto = "429 rate limit. Please try again in 100ms"
        a = _ClienteFalso("a", _un_429(corto))
        b = _ClienteFalso("b", _un_429(corto))
        proveedor = _proveedor_con(monkeypatch, a, b)

        siestas = []

        def dormir(s):
            # Sin tope de vueltas y con el reloj congelado, el bucle no acaba
            # nunca. Esto lo convierte en un fallo visible en vez de una prueba
            # colgada: la batería de mutaciones se quedó esperando diez minutos
            # la primera vez que se quitó el tope.
            siestas.append(s)
            if len(siestas) > 50:
                raise AssertionError("el bucle no termina: no hay tope de vueltas")

        monkeypatch.setattr("src.models.groq.time.sleep", dormir)
        monkeypatch.setattr("src.models.groq.time.monotonic", lambda: 1_000.0)

        with pytest.raises(RuntimeError):
            proveedor.generate(self._mensaje())

        assert a.llamadas <= 8 and b.llamadas <= 8, (
            f"el bucle ha dado demasiadas vueltas: {a.llamadas} y {b.llamadas}"
        )

    def test_el_presupuesto_corta_antes_que_el_tope_de_vueltas(self, monkeypatch):
        """La mutación que sobrevivió al repetir la batería bien.

        Con esperas de 100 ms, las 8 vueltas del tope suman menos de un segundo
        y nunca llegan a los 10 de presupuesto: quitar el presupuesto no rompía
        ninguna prueba. Con esperas de 3 s sí se distingue. El presupuesto corta
        a la cuarta (3+3+3 = 9, y otros 3 ya no caben). Sin él, el tope dejaría
        esperar 21 segundos.
        """
        tres = "429 rate limit. Please try again in 3s"
        a = _ClienteFalso("a", _un_429(tres))
        b = _ClienteFalso("b", _un_429(tres))
        proveedor = _proveedor_con(monkeypatch, a, b)

        from src.models.cuota import ESPERA_CORTA_MAXIMA

        reloj = [1_000.0]
        dormido = []

        def dormir(s):
            dormido.append(s)
            reloj[0] += s

        monkeypatch.setattr("src.models.groq.time.sleep", dormir)
        monkeypatch.setattr("src.models.groq.time.monotonic", lambda: reloj[0])

        with pytest.raises(RuntimeError):
            proveedor.generate(self._mensaje())

        assert sum(dormido) <= ESPERA_CORTA_MAXIMA, (
            f"ha esperado {sum(dormido):.0f} s con {ESPERA_CORTA_MAXIMA:.0f} de "
            "presupuesto: lo único que lo ha parado es el tope de vueltas"
        )
        assert len(dormido) == 3

    def test_una_espera_que_llega_en_dos_trozos_se_completa(self, monkeypatch):
        """El caso que el contador de vueltas se dejaba fuera.

        Medido contra Groq: una llamada esperó 1,65 s, volvió a encontrarse el
        límite pidiendo 0,44 s más, y con «una sola vuelta» se rendía **a 0,44
        segundos de funcionar**. La ventana del minuto se rellena por partes.
        """
        a = _ClienteFalso("a", _un_429("429 rate limit. try again in 1.65s"))
        b = _ClienteFalso("b", _un_429("429 rate limit. try again in 13.5s"))
        proveedor = _proveedor_con(monkeypatch, a, b)

        reloj = [1_000.0]
        dormido = []

        def dormir(s):
            dormido.append(s)
            reloj[0] += s
            # Segunda vuelta: sigue al límite, pero ya por muy poco.
            if len(dormido) == 1:
                a.error = _un_429("429 rate limit. try again in 440ms")
            else:
                a.error = None

        monkeypatch.setattr("src.models.groq.time.sleep", dormir)
        monkeypatch.setattr("src.models.groq.time.monotonic", lambda: reloj[0])

        assert proveedor.generate(self._mensaje()).content == "a"
        assert len(dormido) == 2, f"esperó {len(dormido)} vez/veces, no dos"
        assert dormido == pytest.approx([1.65, 0.44])

    def test_primero_se_prueba_la_otra_clave_antes_de_esperar(self, monkeypatch):
        """Rotar es gratis e instantáneo; esperar cuesta tiempo. El orden
        importa y esto lo fija."""
        a = _ClienteFalso("a", _un_429("429 rate limit. Please try again in 50ms"))
        b = _ClienteFalso("b")
        proveedor = _proveedor_con(monkeypatch, a, b)

        dormido = []
        monkeypatch.setattr("src.models.groq.time.sleep", dormido.append)

        assert proveedor.generate(self._mensaje()).content == "b"
        assert not dormido, "ha esperado teniendo otra clave libre"

    def test_un_429_sin_decir_cuanto_no_se_espera(self, monkeypatch):
        """Si no dice cuándo volver, no se sabe si son milisegundos o diez
        minutos. Esperar a ciegas puede costar el plazo del turno."""
        a = _ClienteFalso("a", _un_429("429 too many requests"))
        b = _ClienteFalso("b", _un_429("429 too many requests"))
        proveedor = _proveedor_con(monkeypatch, a, b)

        dormido = []
        monkeypatch.setattr("src.models.groq.time.sleep", dormido.append)

        with pytest.raises(RuntimeError):
            proveedor.generate(self._mensaje())

        assert not dormido


class TestElUmbralSaleDeLaMedicion:
    """El umbral de espera no es una intuicion: son los cuatro caminos medidos.

    | Camino cuando Groq esta al limite del minuto | Coste |
    |---|---|
    | Esperar lo que Groq pide | 0,1 a 9,3 s, gratis |
    | Dejar que el SDK reintente solo | 13 a 26 s |
    | Pasar a Gemini | 29 a 36 s, y acabo en 504 |
    | Pasar a OpenAI | 1 a 1,7 s, y cuesta dinero |
    """

    def test_cubre_la_espera_mas_larga_medida_del_limite_por_minuto(self):
        """9,25 s es lo que Groq pidio con la ventana muy pasada. Un umbral que
        no lo cubriera dejaria el caso frecuente fuera."""
        from src.models.cuota import ESPERA_CORTA_MAXIMA

        assert ESPERA_CORTA_MAXIMA >= 9.3, (
            f"el umbral es {ESPERA_CORTA_MAXIMA} s y la espera por minuto "
            "medida llego a 9,3 s: el caso que esto existe para arreglar "
            "quedaria fuera"
        )

    def test_y_deja_fuera_las_del_limite_diario(self):
        """«try again in 10m53s» tiene que irse a otro proveedor. Esperar once
        minutos dentro de un turno de 85 segundos no es una opcion."""
        from src.models.cuota import ESPERA_CORTA_MAXIMA

        assert ESPERA_CORTA_MAXIMA < 60, (
            "un umbral de un minuto o mas convierte la cuota diaria en una "
            "espera, y el turno tiene plazo"
        )

    def test_una_espera_de_nueve_segundos_se_espera(self, monkeypatch):
        """El caso medido, de punta a punta."""
        real = ("Error code: 429 - Rate limit reached ... on tokens per minute "
                "(TPM): Limit 8000. Please try again in 9.254999999s")
        a = _ClienteFalso("a", _un_429(real))
        b = _ClienteFalso("b", _un_429(real))
        proveedor = _proveedor_con(monkeypatch, a, b)

        dormido = []

        def esperar(s):
            dormido.append(s)
            a.error = None

        monkeypatch.setattr("src.models.groq.time.sleep", esperar)

        assert proveedor.generate(
            [__import__("src.models.base", fromlist=["ChatMessage"])
             .ChatMessage(role="user", content="hola")]
        ).content == "a"
        assert dormido and dormido[0] == pytest.approx(9.255, abs=0.01)


class TestElSdkNoReintentaPorSuCuenta:
    """`max_retries=0` en los clientes de Groq, y hay medicion detras.

    | | SDK reintentando | sin reintentar |
    |---|---|---|
    | 6 llamadas seguidas | 83,4 s | 3,7 s |
    | reparto entre las 2 claves | 8 y 2 | 3 y 3 |

    Esperaba 13 a 26 segundos cuando el mensaje de Groq pedia decimas, y **se
    tragaba el 429**, asi que el llavero no se enteraba y la segunda clave
    apenas se usaba: se perdia justo lo que el llavero existe para dar.
    """

    def test_los_clientes_se_montan_sin_reintentos(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from src.config import reset_settings

        monkeypatch.setenv("GROQ_API_KEY", CLAVE_A)
        monkeypatch.setenv("GROQ_API_KEY_2", CLAVE_B)
        for n in range(3, 8):
            monkeypatch.setenv(f"GROQ_API_KEY_{n}", "")
        # Aunque la configuracion pida reintentos, Groq no los usa.
        monkeypatch.setenv("MORGAN_LLM_MAX_RETRIES", "3")
        reset_settings()

        doble = MagicMock()
        with patch("src.models.groq.Groq", doble):
            from src.models.groq import GroqProvider

            GroqProvider()

        assert doble.call_count == 2, "un cliente por clave"
        for llamada in doble.call_args_list:
            assert llamada.kwargs["max_retries"] == 0, (
                "el SDK vuelve a reintentar por su cuenta: se traga el 429 y "
                "el llavero deja de rotar"
            )

    def test_el_ajuste_sigue_existiendo_para_los_demas(self):
        """Quitarlo de Groq no es quitarlo del proyecto: los otros proveedores
        no tienen llavero con el que rotar, asi que a ellos les sirve."""
        from src.config import Settings

        assert Settings.llm_max_retries >= 1


class TestUnLlaveroVacioNoCuelgaElTurno:
    """Lo encontró la batería de mutaciones, repetida bien.

    Al mutar `Llavero.turnos()` para que filtrara en lugar de reordenar, con
    todas las claves agotadas devolvía una lista vacía, y el bucle de Groq
    **giraba para siempre**: la prueba no fallaba, se colgaba. Hoy el llavero
    no devuelve nunca una lista vacía, pero si lo hiciera, un turno que no
    vuelve es mucho peor que un error.
    """

    def test_un_llavero_vacio_falla_en_vez_de_colgarse(self, monkeypatch):
        a = _ClienteFalso("a")
        proveedor = _proveedor_con(monkeypatch, a, _ClienteFalso("b"))
        monkeypatch.setattr(proveedor._llavero, "turnos", lambda: [])

        siestas = []

        def dormir(s):
            siestas.append(s)
            if len(siestas) > 5:
                raise AssertionError("el bucle no termina con un llavero vacío")

        monkeypatch.setattr("src.models.groq.time.sleep", dormir)

        from src.models.base import ChatMessage

        with pytest.raises(RuntimeError, match="ninguna clave"):
            proveedor.generate([ChatMessage(role="user", content="hola")])

        assert a.llamadas == 0
