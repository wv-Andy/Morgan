"""
En qué se le va el tiempo a Morgan.

**Por qué existe este módulo, y por qué se probó.** El diagnóstico de una
pregunta trivial que tardaba 64 segundos se hizo por deducción: levantar Morgan
en local, medir lo mismo, medir el proveedor aparte y restar la red. Costó una
hora y **salió mal**: se concluyó que el modelo era el 0,7% del problema.

La primera medición con tiempos por etapa dijo que era el 98%, y que la cuota
diaria de Groq estaba agotada, y que respondía el respaldo. Una línea.

Así que lo que estas pruebas protegen no es una funcionalidad: es la capacidad
de no volver a diagnosticar a ciegas. Está contado en `docs/mediciones.md`.
"""

import time

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.observabilidad_middleware import CABECERA
from src.observabilidad import (
    Medicion,
    anotar,
    etapa,
    medicion_actual,
    midiendo,
)


class TestSeMideLoQueSeMarca:
    def test_una_etapa_suma_su_tiempo(self):
        with midiendo() as medicion:
            with etapa("modelo"):
                time.sleep(0.05)

        resumen = medicion.resumen()
        assert resumen["modelo_ms"] >= 45
        assert resumen["total_ms"] >= resumen["modelo_ms"]

    def test_varias_etapas_del_mismo_nombre_se_acumulan(self):
        """Un turno hace varias llamadas al modelo. Lo que interesa saber es
        cuánto tiempo en total y cuántas veces, no el detalle de cada una: con
        cinco líneas separadas no se ve que el modelo es el 98%.
        """
        with midiendo() as medicion:
            for _ in range(3):
                with etapa("modelo"):
                    time.sleep(0.02)

        resumen = medicion.resumen()
        assert resumen["modelo_veces"] == 3
        assert resumen["modelo_ms"] >= 55

    def test_una_sola_vez_no_ensucia_el_resumen(self):
        with midiendo() as medicion:
            with etapa("bd"):
                pass

        assert "bd_veces" not in medicion.resumen()

    def test_lo_que_falla_tambien_ha_gastado_tiempo(self):
        """Una herramienta que revienta ha consumido lo suyo. Esconderlo dejaría
        un hueco en `resto` sin explicación, que es justo lo que hace inútil una
        medición.
        """
        with midiendo() as medicion:
            with pytest.raises(ValueError):
                with etapa("herramienta.revienta"):
                    time.sleep(0.03)
                    raise ValueError("me rompí")

        assert medicion.resumen()["herramienta.revienta_ms"] >= 25

    def test_el_resto_es_lo_que_no_espera_a_nadie(self):
        """`resto` es el trabajo propio de Morgan. Fue el número que reveló que
        un turno son 85 ms de Morgan y 27.000 de esperar al modelo.
        """
        with midiendo() as medicion:
            time.sleep(0.05)
            with etapa("modelo"):
                time.sleep(0.05)

        resumen = medicion.resumen()
        assert resumen["resto_ms"] >= 40
        assert resumen["modelo_ms"] >= 40


class TestQuienRespondio:
    def test_se_anota_quien_contesto(self):
        with midiendo() as medicion:
            anotar("proveedor", "NVIDIA:deepseek")

        assert medicion.resumen()["proveedor"] == "NVIDIA:deepseek"

    def test_y_quien_fallo_antes(self):
        """Sin esto no se sabía que Groq devolvía 429 en cada llamada y
        contestaba el respaldo. El campo `model` de la respuesta decía «Groq».
        """
        with midiendo() as medicion:
            anotar("proveedor_fallo", "Groq")
            anotar("proveedor", "NVIDIA")

        resumen = medicion.resumen()
        assert resumen["proveedor_fallo"] == "Groq"
        assert resumen["proveedor"] == "NVIDIA"


class TestFueraDeUnaPeticionNoCuesta:
    """La CLI, las pruebas y los trabajos de fondo pasan por el mismo código."""

    def test_sin_medicion_abierta_no_hay_medicion(self):
        assert medicion_actual() is None

    def test_y_marcar_una_etapa_no_falla(self):
        with etapa("modelo"):
            pass

        assert medicion_actual() is None

    def test_anotar_tampoco(self):
        anotar("proveedor", "el que sea")

        assert medicion_actual() is None


class TestElResumenEsUnaFoto:
    """El turno del agente corre en otro hilo, que recibe una copia del contexto
    pero **el mismo objeto** `Medicion`. Y la petición puede resumirlo al
    cumplirse su plazo de 100 s mientras el hilo sigue escribiendo.

    Lo que los que llaman necesitan de `resumen()` es que lo que devuelve no
    cambie debajo de sus pies: se serializa a JSON y se mete en una cabecera
    después de obtenerlo.

    > **Sobre la copia defensiva de `resumen()`**: está para que una etapa nueva
    > que aparezca a mitad del recorrido no provoque un «dictionary changed size
    > during iteration». Eso **no está cubierto por una prueba**, y se dice en
    > lugar de fingirlo: reproducir esa carrera de forma fiable no se consiguió,
    > y una prueba que solo falla algunas veces acaba desactivada. Lo que sigue
    > sí es comprobable, y es la garantía que se usa.
    """

    def test_lo_que_devuelve_no_cambia_despues(self):
        medicion = Medicion()
        medicion.sumar("modelo", 0.5)

        resumen = medicion.resumen()
        antes = dict(resumen)

        medicion.sumar("modelo", 10.0)
        medicion.sumar("herramienta.nueva", 3.0)

        assert resumen == antes, (
            "El resumen cambió al seguir midiendo. Se serializa a JSON y se "
            "mete en una cabecera después de obtenerlo: tiene que ser una foto"
        )

    def test_y_un_resumen_posterior_si_ve_lo_nuevo(self):
        medicion = Medicion()
        medicion.sumar("modelo", 0.5)
        medicion.resumen()

        medicion.sumar("herramienta.nueva", 3.0)

        assert "herramienta.nueva_ms" in medicion.resumen()


class TestLaCabecera:
    def test_se_lee_de_un_vistazo(self):
        with midiendo() as medicion:
            medicion.sumar("bd", 0.31)
            medicion.sumar("modelo", 0.52)
            cabecera = medicion.cabecera()

        assert cabecera.startswith("total=")
        assert "bd=310" in cabecera
        assert "modelo=520" in cabecera
        assert "resto=" in cabecera

    def test_dice_cuantas_veces_cuando_es_mas_de_una(self):
        """`modelo=510x3` en lugar de `modelo=510`.

        Sin esto, el primer punto de la 2.1 —«cuántas llamadas al modelo hace un
        turno de verdad»— **no se podía contestar desde fuera**. El dato estaba
        en `resumen()`, que solo se registra cuando la petición pasa de lenta,
        así que un turno normal no lo decía en ninguna parte.

        Y la diferencia importa: tres llamadas de 170 ms no es lo mismo que una
        de 510. Lo primero se arregla haciendo menos viajes; lo segundo no se
        arregla.
        """
        with midiendo() as medicion:
            medicion.sumar("modelo", 0.17)
            medicion.sumar("modelo", 0.17)
            medicion.sumar("modelo", 0.17)
            cabecera = medicion.cabecera()

        assert "modelo=510x3" in cabecera, cabecera

    def test_con_una_sola_vez_no_pone_el_numero(self):
        """`modelo=520x1` sería ruido en el caso normal."""
        with midiendo() as medicion:
            medicion.sumar("modelo", 0.52)
            cabecera = medicion.cabecera()

        assert "modelo=520;" in cabecera + ";", cabecera
        assert "x1" not in cabecera

    def test_cada_etapa_lleva_su_propia_cuenta(self):
        """Las consultas a la base y las llamadas al modelo se cuentan aparte.
        Un solo número para las dos no diría de dónde salen los viajes."""
        with midiendo() as medicion:
            for _ in range(11):
                medicion.sumar("bd", 0.045)
            medicion.sumar("modelo", 0.34)
            medicion.sumar("modelo", 0.34)
            cabecera = medicion.cabecera()

        assert "bd=495x11" in cabecera, cabecera
        assert "modelo=680x2" in cabecera, cabecera

    def test_el_total_nunca_lleva_cuenta(self):
        """`total` no es una etapa: es el reloj de la petición."""
        with midiendo() as medicion:
            medicion.sumar("modelo", 0.1)
            medicion.sumar("modelo", 0.1)
            cabecera = medicion.cabecera()

        assert not cabecera.split(";")[0].count("x"), cabecera


class TestTodaRutaLlegaMedida:
    """En un middleware y no en cada ruta: así una ruta nueva nace medida sin
    tener que acordarse, y las 52 que ya existían no cambiaron una línea.
    """

    @pytest.fixture
    def cliente(self):
        return TestClient(app)

    @pytest.mark.parametrize("ruta", ["/sessions", "/tools", "/status", "/memory"])
    def test_la_cabecera_viene_en_las_lecturas(self, cliente, ruta):
        respuesta = cliente.get(ruta)

        assert CABECERA in respuesta.headers, (
            f"{ruta} no trae {CABECERA}. Sin ella no hay forma de saber por qué "
            "una lectura tarda un segundo en producción y 20 ms en local"
        )
        assert respuesta.headers[CABECERA].startswith("total=")

    def test_tambien_cuando_la_peticion_se_rechaza(self, cliente):
        """Un 401 o un 403 también ha costado tiempo, y a veces son los lentos:
        la identidad y el CSRF corren antes de la ruta.
        """
        respuesta = cliente.post("/sessions", json={})

        assert CABECERA in respuesta.headers

    def test_el_health_no_se_mide(self, cliente):
        """Existe para responder rápido y que alguien lo consulte cada pocos
        segundos. Medirlo llena el registro de líneas que no llevan a nada.
        """
        assert CABECERA not in cliente.get("/health").headers

    def test_el_navegador_puede_leerla(self):
        """`allow_headers: *` es para las que MANDA el cliente. Para que pueda
        leer una de la respuesta hay que nombrarla en `expose_headers`, o la
        cabecera llega y el JavaScript no la ve.
        """
        from src.api.app import create_app

        aplicacion = create_app()
        cors = [m for m in aplicacion.user_middleware if "CORS" in str(m)]

        assert cors, "Ya no hay middleware de CORS"
        expuestas = cors[0].kwargs.get("expose_headers") or []
        assert CABECERA in expuestas, (
            f"{CABECERA} no está en expose_headers: el navegador la recibe y no "
            "la puede leer"
        )


class TestSeSabeCuandoContestaElRespaldo:
    """Con el proveedor principal agotado, un turno pasa de segundos a un
    minuto. Medido: Groq devuelve 429, NVIDIA agota el plazo de 30 s, y contesta
    Gemini a los 64.

    Sin decirlo, eso se lee como que Morgan se ha roto. Y hasta ahora no se
    decía: el campo `model` de la respuesta devolvía la cadena configurada
    entera, así que afirmaba «Groq» justo cuando Groq no había respondido.
    """

    def test_el_primero_de_la_cadena_no_marca_respaldo(self):
        from src.models.fallback import FallbackProvider
        from src.models.mock import MockLLMProvider

        principal = MockLLMProvider()
        principal.queue_text("respondo yo")

        with midiendo() as medicion:
            FallbackProvider(principal).generate(messages=[])

        assert "respaldo" not in medicion.resumen()

    def test_y_el_segundo_si(self):
        from src.models.base import LLMProvider
        from src.models.fallback import FallbackProvider
        from src.models.mock import MockLLMProvider

        class Caido(LLMProvider):
            @property
            def model_name(self) -> str:
                return "el-que-no-va"

            def generate(self, messages, tools=None, system_prompt=None):
                raise ConnectionError("agotado")

        respaldo = MockLLMProvider()
        respaldo.queue_text("respondo yo, que soy el segundo")

        with midiendo() as medicion:
            FallbackProvider(Caido(), respaldo).generate(messages=[])

        resumen = medicion.resumen()
        assert resumen.get("respaldo") == "si", (
            "No se anota que contestó un respaldo, así que la web no puede "
            "explicar por qué la respuesta tardó un minuto"
        )
        assert resumen.get("proveedor_fallo") == "el-que-no-va"

    def test_la_web_lo_explica_sin_decir_que_modelo(self):
        """Desde la V2.0.29 la web **no enseña qué modelo contestó** (decisión
        mía: a quien usa Morgan le da igual, y es un detalle interno). Lo que se
        conserva es la explicación, sin nombres: si contestó el de reserva, el
        tiempo de la respuesta lo dice al pasar por encima. Sin ella, un turno de
        un minuto se lee como una avería.

        Se comprueba en el frontend porque es donde tiene que verse, o no verse.
        """
        from pathlib import Path

        web = Path(__file__).resolve().parent.parent / "web" / "src"
        chat = (web / "components" / "Chat.tsx").read_text(encoding="utf-8")

        assert "res.etapas?.respaldo" in chat, "La web no lee si contestó un respaldo"
        assert "msg.respaldo ?" in chat, "Lo lee y no lo explica en ningún sitio"
        for delator in ("msg.model", "res.model", "Motor:"):
            assert delator not in chat, f"La conversación vuelve a enseñar el modelo ({delator})"


class TestSiNoContestaNadieSeDice:
    """El mismo defecto que el campo `model` ya tuvo, en el caso peor.

    Cuando los tres eslabones agotan su plazo, nadie anota `proveedor`. El
    ayudante caía entonces a `model_name`, que imprime la cadena configurada
    —«Groq [Respaldo: Gemini, NVIDIA]»— y afirma que respondió Groq.

    Medido en producción: un turno donde los tres agotaron los 30 s tardó **93
    segundos** y decía haber respondido Groq.
    """

    def test_con_fallos_y_sin_respuesta_no_se_nombra_a_nadie(self):
        from src.api.routes.chat import _quien_respondio

        class Contenedor:
            class agent:
                class model:
                    model_name = "Groq:x [Respaldo: gemini, NVIDIA:y]"

        with midiendo():
            anotar("proveedor_fallo", "Groq:x")
            quien = _quien_respondio(Contenedor)

        assert quien == "ninguno respondio", (
            f"Dice «{quien}» cuando no contestó nadie. Es la cadena "
            "configurada, no quien respondió"
        )

    def test_si_alguien_contesto_se_le_nombra(self):
        from src.api.routes.chat import _quien_respondio

        class Contenedor:
            class agent:
                class model:
                    model_name = "la cadena entera"

        with midiendo():
            anotar("proveedor_fallo", "Groq:x")
            anotar("proveedor", "gemini-3.6-flash")
            assert _quien_respondio(Contenedor) == "gemini-3.6-flash"

    def test_fuera_de_una_peticion_se_cae_a_la_cadena(self):
        """En la CLI no hay medición, y ahí la cadena configurada es la mejor
        respuesta disponible.
        """
        from src.api.routes.chat import _quien_respondio

        class Contenedor:
            class agent:
                class model:
                    model_name = "la cadena entera"

        assert _quien_respondio(Contenedor) == "la cadena entera"


class TestLosTokensDelTurno:
    """4.1.5: el coste de un turno en tokens, sumando todas sus llamadas al modelo."""

    def test_se_suman_las_llamadas_y_salen_en_el_resumen_y_la_cabecera(self):
        from src.models.cuota import CUOTAS
        from src.observabilidad import midiendo

        with midiendo() as medicion:
            CUOTAS.apuntar_uso("prueba-tokens", 1200, 80)
            CUOTAS.apuntar_uso("prueba-tokens", 1500, 40)
        r = medicion.resumen()
        assert (r["tokens_entrada"], r["tokens_salida"]) == (2700, 120)
        assert medicion.cabecera().endswith("tokens=2700+120")

    def test_sin_llamadas_no_se_anade_nada(self):
        from src.observabilidad import midiendo

        with midiendo() as medicion:
            pass
        assert "tokens_entrada" not in medicion.resumen() and "tokens=" not in medicion.cabecera()

    def test_fuera_de_una_peticion_no_falla(self):
        from src.observabilidad import sumar_tokens

        sumar_tokens(10, 2)
