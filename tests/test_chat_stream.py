"""
`/chat/stream`: el progreso de un turno, mientras pasa (V2.0.14).

**Por qué existe.** `/chat` no dice nada hasta el final, y el proxy de Vercel corta
una respuesta callada a los 120 s. Por eso en la nube el turno se rendía a los 85 s
y un encargo de tres pasos —106 a 145 s— no cabía por la web. La sonda del goteo
midió que una respuesta que emite bytes llega a los 180 s. Diseño aprobado en
`docs/agente.md`.

**Una nota sobre cómo se prueba el tiempo.** El `TestClient` acumula el cuerpo:
medido, un generador que emite «uno», espera 1,5 s y emite «dos» entregó las dos
líneas juntas a los 1,55 s. Así que lo que depende de **cuándo** sale una línea se
prueba contra el generador directamente o contra un uvicorn de verdad en un
puerto local, que además es lo único que comprueba que los middlewares no
acumulan la respuesta por el camino.
"""

import asyncio
import contextvars
import json
import socket
import threading
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from src.agent.core import Agent
from src.agent.sessions import SessionStore
from src.api.app import app
from src.api.dependencies import get_container
from src.api.routes import chat as ruta_chat
from src.eventos_turno import CanalDelTurno, emitir, escuchando
from src.models.base import LLMProvider, LLMResponse, ToolCallRequest


class ProveedorGuionado(LLMProvider):
    """Pide una herramienta según el mensaje, y luego contesta.

    Con `puerta` se queda esperando antes de contestar la primera llamada, que es
    lo que permite probar qué sale mientras el turno todavía no ha terminado.
    """

    def __init__(self, puerta: threading.Event | None = None, retardo: float = 0.0):
        self.puerta = puerta
        self.retardo = retardo
        self.termino = threading.Event()

    @property
    def model_name(self) -> str:
        return "guionado"

    def generate(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        if self.puerta is not None:
            self.puerta.wait(10)
        if self.retardo:
            time.sleep(self.retardo)

        ultimo_usuario = next(m for m in reversed(messages) if m.role == "user")
        if messages[-1].role == "tool":
            self.termino.set()
            return LLMResponse(type="text", content=f"hecho: {ultimo_usuario.content}")

        herramienta = "recall_memory" if "memoria" in ultimo_usuario.content else "system_info"
        return LLMResponse(
            type="tool_call",
            tool_calls=[ToolCallRequest(name=herramienta, arguments={}, id="c1")],
        )


def _eventos(texto: str) -> list[dict]:
    return [json.loads(linea) for linea in texto.splitlines() if linea.strip()]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def agente(monkeypatch):
    """Pone un agente con un proveedor guionado y lo devuelve todo a su sitio."""
    container = get_container()
    original = container.agent

    def montar(proveedor: LLMProvider):
        container.agent = Agent(
            model=proveedor,
            tool_registry=container.tool_registry,
            permission_manager=container.permission_manager,
            sessions=SessionStore(),
            conversation_history=container.conversation_history,
        )
        return container.agent

    yield montar
    container.agent = original


class TestLosEventos:
    def test_cuenta_el_turno_en_orden_y_termina_con_la_respuesta(self, client, agente):
        agente(ProveedorGuionado())

        r = client.post("/chat/stream", json={"message": "hola", "session_id": "st-1"})

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        tipos = [(e["tipo"], e.get("nombre"), e.get("estado")) for e in _eventos(r.text)]
        assert tipos == [
            ("inicio", None, None),
            ("pensando", None, None),
            ("herramienta", "system_info", "empieza"),
            ("herramienta", "system_info", "ok"),
            ("pensando", None, None),
            ("fin", None, None),
        ]
        fin = _eventos(r.text)[-1]
        assert fin["response"] == "hecho: hola"
        assert fin["model"] == "guionado"
        assert "elapsed_seconds" in fin

    def test_las_cabeceras_que_impiden_acumular(self, client, agente):
        agente(ProveedorGuionado())

        r = client.post("/chat/stream", json={"message": "hola", "session_id": "st-2"})

        assert r.headers["cache-control"] == "no-store, no-transform"
        assert r.headers["x-accel-buffering"] == "no"

    def test_las_herramientas_viajan_sin_argumentos(self, client, agente):
        """Un argumento puede llevar una ruta o un secreto, y esto sale al navegador."""
        agente(ProveedorGuionado())

        r = client.post("/chat/stream", json={"message": "hola", "session_id": "st-3"})

        for evento in _eventos(r.text):
            if evento["tipo"] == "herramienta":
                assert set(evento) == {"tipo", "nombre", "estado", "t"}

    def test_un_fallo_no_filtra_el_texto_de_la_excepcion(self, client, agente, monkeypatch):
        agentito = agente(ProveedorGuionado())

        def revienta(*args, **kwargs):
            raise RuntimeError(r"C:\Users\secreto\ruta clave=sk-no-deberia-verse")

        monkeypatch.setattr(agentito, "chat", revienta)
        r = client.post("/chat/stream", json={"message": "hola", "session_id": "st-4"})

        ultimo = _eventos(r.text)[-1]
        assert ultimo["tipo"] == "error"
        assert ultimo["code"] == "AGENT_EXECUTION_ERROR"
        assert "secreto" not in r.text and "sk-no" not in r.text

    def test_lo_que_falla_antes_de_empezar_es_un_error_normal(self, client, agente):
        """Un adjunto que no existe no puede llegar como un 200 que luego falla."""
        agente(ProveedorGuionado())

        r = client.post(
            "/chat/stream",
            json={"message": "hola", "session_id": "st-5", "attachments": ["no-existe"]},
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "ATTACHMENT_NOT_FOUND"

    def test_el_turno_usa_el_tope_del_stream(self, client, agente, monkeypatch):
        """170 s en la nube, no los 85 de `/chat`: esta respuesta habla."""
        agentito = agente(ProveedorGuionado())
        ajustes = replace(ruta_chat.get_settings(), turn_timeout_stream=137)
        monkeypatch.setattr(ruta_chat, "get_settings", lambda: ajustes)
        vistos = []
        original = agentito.chat

        def espia(*args, **kwargs):
            vistos.append(kwargs.get("tope_turno"))
            return original(*args, **kwargs)

        monkeypatch.setattr(agentito, "chat", espia)
        client.post("/chat/stream", json={"message": "hola", "session_id": "st-6"})

        assert vistos == [137]


class TestElTopeEsDelTurno:
    def test_el_tope_del_turno_manda_sobre_el_del_agente(self, non_interactive_permissions):
        """Va por parámetro porque el agente es uno para todos: `/chat` y
        `/chat/stream` atienden a la vez con topes distintos."""
        from src.tools.registry import ToolRegistry

        agentito = Agent(
            model=ProveedorGuionado(retardo=1.2),
            tool_registry=ToolRegistry(),
            permission_manager=non_interactive_permissions,
            sessions=SessionStore(),
        )
        agentito.turn_timeout = 600

        respuesta = agentito.chat("hola", session_id="tope", tope_turno=1)

        assert "(1 s)" in respuesta
        assert agentito.turn_timeout == 600


class TestCuandoSaleCadaCosa:
    """Contra el generador directamente: el `TestClient` acumula.

    El generador es asíncrono desde la 2.0.27, así que cada prueba corre su propio
    bucle de eventos.
    """

    def _generador(self, container, canal, latido):
        return ruta_chat._eventos_del_turno(
            container, canal, {}, {"terminado": False, "abandonado": False},
            threading.Lock(), None, "g", latido,
        )

    def test_inicio_sale_antes_de_que_pase_nada(self):
        async def probar():
            generador = self._generador(get_container(), CanalDelTurno(), latido=5.0)
            inicio = time.monotonic()
            primera = json.loads(await anext(generador))
            await generador.aclose()
            return primera, time.monotonic() - inicio

        primera, segundos = asyncio.run(probar())
        assert primera["tipo"] == "inicio"
        assert segundos < 0.5

    def test_sin_novedades_manda_latidos(self):
        async def probar():
            generador = self._generador(get_container(), CanalDelTurno(), latido=0.1)
            await anext(generador)  # inicio
            tipos = [json.loads(await anext(generador))["tipo"] for _ in range(3)]
            await generador.aclose()
            return tipos

        assert asyncio.run(probar()) == ["latido", "latido", "latido"]

    def test_un_evento_reinicia_la_cuenta_del_latido(self):
        async def probar():
            canal = CanalDelTurno()
            generador = self._generador(get_container(), canal, latido=0.3)
            await anext(generador)
            canal.poner("pensando", vuelta=1)
            tipo = json.loads(await anext(generador))["tipo"]
            await generador.aclose()
            return tipo

        assert asyncio.run(probar()) == "pensando"

    def test_un_evento_desde_el_hilo_del_turno_despierta_sin_esperar_al_latido(self):
        """La espera es en el bucle de eventos: el hilo del turno tiene que avisar.

        Si el aviso se perdiera, el evento saldría con el siguiente latido —aquí,
        a los 5 s— y la web vería la herramienta cuando ya ha terminado.
        """
        async def probar():
            canal = CanalDelTurno()
            generador = self._generador(get_container(), canal, latido=5.0)
            await anext(generador)
            threading.Timer(0.2, lambda: canal.poner("pensando", vuelta=2)).start()
            inicio = time.monotonic()
            evento = json.loads(await anext(generador))
            await generador.aclose()
            return evento, time.monotonic() - inicio

        evento, segundos = asyncio.run(probar())
        assert evento["tipo"] == "pensando"
        assert segundos < 1.0, f"el evento esperó al latido: {segundos:.2f} s"


class TestCadaUnoSuCanal:
    def test_sin_canal_emitir_no_hace_nada(self):
        emitir("pensando", vuelta=1)  # la CLI, `/chat`, una prueba: no revienta

    def test_el_hilo_escribe_en_el_canal_de_su_peticion(self):
        canal = CanalDelTurno()
        with escuchando(canal):
            contexto = contextvars.copy_context()

        hilo = threading.Thread(target=lambda: contexto.run(emitir, "pensando", vuelta=7))
        hilo.start()
        hilo.join()

        assert canal.cola.get_nowait()["vuelta"] == 7

    def test_dos_turnos_a_la_vez_no_cruzan_eventos(self, client, agente):
        """La pieza delicada del diseño: el agente es UNO para todas las personas.

        Si los eventos fueran por su `event_handler`, los de un turno saldrían en
        el stream del otro. Los dos turnos esperan en la misma puerta, así que
        están vivos a la vez.
        """
        puerta = threading.Event()
        agente(ProveedorGuionado(puerta=puerta))
        respuestas: dict[str, str] = {}

        def pedir(mensaje, sesion):
            respuestas[sesion] = TestClient(app).post(
                "/chat/stream", json={"message": mensaje, "session_id": sesion}
            ).text

        hilos = [
            threading.Thread(target=pedir, args=("mira el sistema", "st-a")),
            threading.Thread(target=pedir, args=("mira la memoria", "st-b")),
        ]
        for hilo in hilos:
            hilo.start()
        time.sleep(0.5)
        puerta.set()
        for hilo in hilos:
            hilo.join(20)

        herramientas_a = {e["nombre"] for e in _eventos(respuestas["st-a"]) if e["tipo"] == "herramienta"}
        herramientas_b = {e["nombre"] for e in _eventos(respuestas["st-b"]) if e["tipo"] == "herramienta"}
        assert herramientas_a == {"system_info"}
        assert herramientas_b == {"recall_memory"}
        assert _eventos(respuestas["st-a"])[-1]["response"] == "hecho: mira el sistema"
        assert _eventos(respuestas["st-b"])[-1]["response"] == "hecho: mira la memoria"


class TestUnRespaldoSeCuenta:
    def test_la_cadena_avisa_cuando_contesta_un_respaldo(self):
        from src.models.fallback import FallbackProvider

        class Roto(ProveedorGuionado):
            @property
            def model_name(self) -> str:
                return "principal-roto"

            def generate(self, *args, **kwargs):
                raise ConnectionError("caído")

        class Sano(ProveedorGuionado):
            @property
            def model_name(self) -> str:
                return "respaldo-sano"

            def generate(self, *args, **kwargs):
                return LLMResponse(type="text", content="ok")

        canal = CanalDelTurno()
        with escuchando(canal):
            FallbackProvider(Roto(), Sano()).generate(messages=[])

        evento = canal.cola.get_nowait()
        assert evento["tipo"] == "respaldo"
        assert evento["proveedor"] == "respaldo-sano"


# --- Contra un servidor de verdad ---------------------------------------------


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor():
    """Un uvicorn real: lo único que ve si algo por el camino acumula la respuesta."""
    import uvicorn

    puerto = _puerto_libre()
    config = uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning")
    server = uvicorn.Server(config)
    hilo = threading.Thread(target=server.run, daemon=True)
    hilo.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{puerto}"
    server.should_exit = True
    hilo.join(5)


class TestPorLaRedDeVerdad:
    def test_inicio_llega_mientras_el_turno_sigue_vivo(self, servidor, agente):
        """Si un middleware acumulara el cuerpo, `inicio` llegaría con `fin`."""
        puerta = threading.Event()
        agente(ProveedorGuionado(puerta=puerta))

        with httpx.stream(
            "POST", f"{servidor}/chat/stream",
            json={"message": "hola", "session_id": "red-1"}, timeout=20,
        ) as r:
            lineas = r.iter_lines()
            primera = json.loads(next(lineas))
            # El turno sigue parado en la puerta: nada de lo que viene ha pasado.
            assert primera["tipo"] == "inicio"
            assert not puerta.is_set()
            puerta.set()
            resto = [json.loads(linea) for linea in lineas if linea]

        assert resto[-1]["tipo"] == "fin"

    def test_cortar_la_conexion_no_para_el_turno_y_se_guarda(self, servidor, agente, client):
        """Decisión B del diseño: «Detener» deja de mirar; el turno termina y se guarda."""
        puerta = threading.Event()
        proveedor = ProveedorGuionado(puerta=puerta)
        agente(proveedor)
        sesion = "red-corte"

        with httpx.stream(
            "POST", f"{servidor}/chat/stream",
            json={"message": "algo largo", "session_id": sesion}, timeout=20,
        ) as r:
            assert json.loads(next(r.iter_lines()))["tipo"] == "inicio"
        # Conexión cerrada. Ahora se deja terminar al turno.
        puerta.set()
        assert proveedor.termino.wait(10), "el turno se paró al cerrar la conexión"

        respuestas = []
        for _ in range(50):
            mensajes = client.get(f"/sessions/{sesion}/messages").json()["messages"]
            respuestas = [m["content"] for m in mensajes if m["role"] == "assistant"]
            if respuestas:
                break
            time.sleep(0.1)
        assert respuestas and "hecho: algo largo" in respuestas[-1]


class TestUnStreamNoOcupaUnHilo:
    """Lo que midió la prueba de carga de la 2.3-C (V2.0.27).

    Con 60 turnos largos abiertos, `/health` tardaba 9,7 s y el `inicio` de un
    turno nuevo 11,5 s: un generador **síncrono** se itera en la reserva de 40
    hilos de Starlette, y cada stream tenía uno ocupado esperando en la cola.
    Render reinicia el servicio si `/health` no contesta.

    Se prueba la forma y no el tiempo: dentro de pytest el bloqueo no se
    reproduce de forma fiable (medido: con el generador antiguo, un uvicorn
    lanzado desde la suite no llegaba a retener el hilo, y fuera sí). El
    efecto medido está en `scripts/carga.py` y en docs/mediciones.md.
    """

    def test_el_cuerpo_es_el_generador_asincrono_y_no_el_envoltorio_de_hilos(self, agente):
        from fastapi.responses import StreamingResponse

        from src.api.schemas import ChatRequest

        puerta = threading.Event()
        agente(ProveedorGuionado(puerta=puerta))
        try:
            respuesta = ruta_chat.chat_stream(
                ChatRequest(message="hola", session_id="forma"), container=get_container(),
            )
            assert isinstance(respuesta, StreamingResponse)
            cuerpo = respuesta.body_iterator
            # Starlette envuelve un iterador síncrono en `iterate_in_threadpool`.
            assert cuerpo.ag_code.co_name == "_eventos_del_turno", (
                f"el stream se itera con {cuerpo.ag_code.co_name}: ocupa un hilo por turno"
            )
        finally:
            puerta.set()

    def test_health_no_pasa_por_la_reserva_de_hilos(self):
        from src.api.routes.health import health_check
        import inspect

        assert inspect.iscoroutinefunction(health_check)


class TestQuienRespondioDeVerdad:
    def test_si_la_ultima_llamada_no_la_atiende_nadie_no_dice_que_fue_el_primero(self):
        """Medido el 2026-09-16: la primera llamada la atendió Groq, la tercera nadie,
        y la respuesta —el aviso de error de Morgan— seguía diciendo «Groq»."""
        from src.api.routes.chat import _quien_respondio
        from src.models.fallback import FallbackProvider
        from src.observabilidad import midiendo

        class Intermitente(ProveedorGuionado):
            def __init__(self):
                super().__init__()
                self.llamadas = 0

            @property
            def model_name(self) -> str:
                return "a-veces"

            def generate(self, *args, **kwargs):
                self.llamadas += 1
                if self.llamadas > 1:
                    raise TimeoutError("se pasó del plazo")
                return LLMResponse(type="text", content="primera bien")

        cadena = FallbackProvider(Intermitente())
        with midiendo() as medicion:
            cadena.generate(messages=[])
            with pytest.raises(TimeoutError):
                cadena.generate(messages=[])

            assert _quien_respondio(get_container(), medicion) == "ninguno respondio"


class TestUnTurnoALaVezPorConversacion:
    """4.5, medido en producción (mi prueba desde el móvil): una respuesta larga
    perdió la conexión, la web ofreció «Reintentar» y el reintento lanzó un segundo
    turno mientras el primero seguía. La misma pregunta se procesó tres veces."""

    def test_el_reintento_con_la_conexion_cortada_no_lanza_otro_turno(self, servidor, agente, client):
        puerta = threading.Event()
        proveedor = ProveedorGuionado(puerta=puerta)
        agente(proveedor)
        sesion = "red-reintento"

        with httpx.stream(
            "POST", f"{servidor}/chat/stream",
            json={"message": "algo largo", "session_id": sesion}, timeout=20,
        ) as r:
            assert json.loads(next(r.iter_lines()))["tipo"] == "inicio"
        # La conexión se cortó; el turno sigue. El reintento llega:
        otra = client.post("/chat/stream", json={"message": "algo largo", "session_id": sesion})
        assert otra.status_code == 409
        assert otra.json()["error"]["code"] == "TURNO_EN_CURSO"
        # Tampoco por `/chat`.
        assert client.post("/chat", json={"message": "otra cosa", "session_id": sesion}).status_code == 409
        # Otra conversación sí puede.
        puerta.set()
        assert client.post("/chat/stream", json={"message": "hola", "session_id": "otra"}).status_code == 200

        assert proveedor.termino.wait(10)
        for _ in range(50):
            r = client.post("/chat/stream", json={"message": "y ahora", "session_id": sesion})
            if r.status_code != 409:
                break
            time.sleep(0.1)
        assert r.status_code == 200, "al terminar, la conversación queda libre"
        preguntas = [m["content"] for m in client.get(f"/sessions/{sesion}/messages").json()["messages"]
                     if m["role"] == "user"]
        assert preguntas == ["algo largo", "y ahora"]

    def test_lo_que_falla_antes_de_empezar_libera_la_conversacion(self, client, agente):
        agente(ProveedorGuionado())
        falla = client.post("/chat/stream", json={"message": "x", "session_id": "st-lib",
                                                  "attachments": ["no-existe"]})
        assert falla.status_code == 422
        assert client.post("/chat/stream", json={"message": "hola", "session_id": "st-lib"}).status_code == 200

    def test_un_rechazo_no_gasta_cupo(self, client, agente, monkeypatch):
        from src.api.routes import chat as ruta

        agente(ProveedorGuionado())
        apuntados = []
        container = get_container()
        monkeypatch.setattr(container.uso, "apuntar", lambda *a, **k: apuntados.append(a))
        monkeypatch.setattr(ruta, "_EN_CURSO", {(ruta.usuario_actual(), "st-cupo")})
        assert client.post("/chat/stream", json={"message": "x", "session_id": "st-cupo"}).status_code == 409
        assert apuntados == []
