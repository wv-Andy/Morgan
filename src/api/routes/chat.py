"""
Ruta de interacción y razonamiento de chat para Morgan API (V1.0).
"""

import asyncio
import contextvars
import json
import logging
import queue
import threading
import time
from typing import AsyncIterator, Callable, TypeVar
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from src.api.dependencies import CoreContainer, get_container
from src.api.schemas import ChatRequest, ChatResponse, ErrorResponse
from src.config import get_settings
from src.eventos_turno import CanalDelTurno, escuchando
from src.observabilidad import medicion_actual, vigilar_turno
from src.identidad import rol_actual, usuario_actual
from src.identidad.cuotas import CuotaAgotada

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


T = TypeVar("T")

#: Lo que se contesta cuando se deja de esperar. No es un error: el turno sigue
#: vivo, y la frase tiene que decir exactamente eso o la gente lo repite y paga
#: el modelo dos veces.
AVISO_SIGO_TRABAJANDO = (
    "Esto está tardando más de lo que la red aguanta esperando, así que te "
    "contesto ya para no dejarte colgado. **Sigo trabajando en ello.** Vuelve a "
    "abrir esta conversación en un momento y verás la respuesta completa; no "
    "hace falta que me lo repitas."
)

#: Lo que se contesta si ya hay un turno en marcha en esa conversación (4.5).
TURNO_EN_CURSO = (
    "Morgan sigue con tu mensaje anterior en esta conversación. La respuesta aparecerá "
    "aquí en cuanto termine; no hace falta repetirlo."
)

# --- Un turno a la vez por conversación (4.5) ----------------------------------
#
# Medido en producción (mi prueba, 2026-09-30): desde el móvil, una respuesta de
# 60-75 s perdió la conexión, la web ofreció «Reintentar», y el reintento lanzó un
# segundo turno **mientras el primero seguía** (el turno no depende de que alguien
# escuche, decisión B del streaming). La misma pregunta se procesó tres veces, se pagó
# tres veces y se tocó el techo de Groq por minuto. Y dos turnos a la vez en la misma
# conversación escriben en el mismo historial.
#
# Se da de alta **antes** de apuntar el mensaje en el cupo: un reintento rechazado no
# cuesta un mensaje. Se da de baja cuando el turno termina de verdad, no cuando se deja
# de esperarlo.

_EN_CURSO: set[tuple[str, str]] = set()
_CERROJO_EN_CURSO = threading.Lock()


def _ocupar_conversacion(request: ChatRequest) -> tuple[str, str] | None:
    """Marca la conversación como ocupada, o 409 si ya lo estaba. Sin `session_id`, nada."""
    if not request.session_id:
        return None
    clave = (usuario_actual(), request.session_id)
    with _CERROJO_EN_CURSO:
        if clave in _EN_CURSO:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "TURNO_EN_CURSO", "message": TURNO_EN_CURSO},
            )
        _EN_CURSO.add(clave)
    return clave


def _liberar_conversacion(clave: tuple[str, str] | None) -> None:
    if clave is not None:
        with _CERROJO_EN_CURSO:
            _EN_CURSO.discard(clave)


def _quien_respondio(container: CoreContainer, medicion=None) -> str:
    """Que modelo contesto de verdad, no la cadena entera.

    `container.agent.model.model_name` devuelve
    «Groq:... [Respaldo: NVIDIA:..., gemini-...]», que es la cadena configurada.
    Eso decia «Groq» incluso cuando Groq habia devuelto un 429 y habia
    contestado el respaldo, asi que el campo `model` de la respuesta era **falso
    en el caso que mas importa saber**: cuando algo va lento porque el principal
    no esta respondiendo.

    Lo encontro la observabilidad el mismo dia que se puso: la cuota diaria de
    Groq estaba agotada, todas las llamadas caian a NVIDIA —un modelo de
    razonamiento, mas lento— y la respuesta seguia diciendo Groq.
    """
    medicion = medicion if medicion is not None else medicion_actual()
    if medicion is not None:
        respondio = medicion.datos.get("proveedor")
        if respondio:
            return respondio
        if medicion.datos.get("proveedor_fallo"):
            # Fallo alguno y ninguno llego a contestar: la cadena entera se
            # agoto. Devolver aqui `model_name` imprimia la cadena configurada
            # —«Groq [Respaldo: Gemini, NVIDIA]»— que es justo la mentira que
            # este ayudante existe para evitar, solo que en el caso peor.
            #
            # Medido: un turno donde los tres agotaron su plazo de 30 s tardo
            # 93 segundos y decia haber respondido Groq.
            return "ninguno respondio"
    return container.agent.model.model_name


def _etapas(medicion=None) -> dict | None:
    """El reparto del tiempo de este turno, si alguien lo esta midiendo.

    Devuelve `None` fuera de una peticion HTTP —la CLI, las pruebas— en lugar de
    un diccionario vacio: vacio se lee como «no tardo nada», y `None` como «no
    se midio». No es lo mismo.
    """
    medicion = medicion if medicion is not None else medicion_actual()
    return medicion.resumen() if medicion is not None else None


def _sin_esperar_de_mas(trabajo: Callable[[], T], segundos: int) -> tuple[T | None, bool]:
    """Ejecuta `trabajo` y deja de esperarlo pasados `segundos`.

    Devuelve `(resultado, se_paso)`. Con `segundos <= 0` se espera lo que haga
    falta, que es lo correcto en local.

    **No mata nada.** El hilo sigue, el turno termina y `_persist` guarda la
    respuesta como en cualquier otro turno: quien recargue la conversación la
    encuentra. Matarlo tiraría un trabajo que ya está pagado al proveedor.

    Por qué hace falta, si el agente ya tiene su propio plazo: ese se mira
    ENTRE pasos, así que no acota lo que tarda un paso que ya arrancó. Medido en
    producción con el turno en 85 s: tres de cinco peticiones seguidas murieron
    igual a los 120,1 s en el proxy del borde. Esto es de otra naturaleza — no le
    pide al agente que se dé prisa, deja de esperarle.

    El contexto se copia a mano. `contextvars` no cruza a un hilo nuevo por su
    cuenta, y ahí viven el usuario y el rol de la petición: sin copiarlo, el
    turno abandonado escribiría sin dueño.
    """
    if segundos <= 0:
        return trabajo(), False

    contexto = contextvars.copy_context()
    salida: list[T] = []
    fallo: list[BaseException] = []

    # El mismo objeto de medición que ve el hilo: `copy_context` copia la
    # variable, no el objeto al que apunta. Es lo que permite leer al final lo
    # que el turno abandonado midió después de que la respuesta ya se fuera.
    medicion = medicion_actual()
    inicio = time.monotonic()

    # Un cerrojo decide, de una vez, si el turno se abandona o se recoge.
    #
    # Sin él había una carrera: si el hilo terminaba justo después de que
    # venciera la espera, `is_alive()` podía decir que seguía vivo, se
    # devolvía «sigo trabajando» con el resultado ya hecho, y además nadie
    # registraba en qué se le había ido el tiempo.
    cerrojo = threading.Lock()
    estado = {"terminado": False, "abandonado": False}

    def correr() -> None:
        try:
            salida.append(contexto.run(trabajo))
        except BaseException as exc:  # noqa: BLE001 - se relanza en el hilo que espera
            fallo.append(exc)
        finally:
            with cerrojo:
                estado["terminado"] = True
                abandonado = estado["abandonado"]
            if abandonado:
                # **Lo único que dice qué paso se pasó.** El registro de la
                # petición se escribió al devolver «sigo trabajando», así que
                # su reparto se queda antes del paso culpable. Este es el de
                # después, con el turno ya entero.
                logger.warning(
                    "El turno abandonado terminó a los %.1f s (se dejó de esperar "
                    "a los %s). Reparto completo: %s",
                    time.monotonic() - inicio,
                    segundos,
                    medicion.cabecera() if medicion is not None else "sin medir",
                )

    hilo = threading.Thread(target=correr, name="turno-morgan", daemon=True)
    hilo.start()
    hilo.join(segundos)

    with cerrojo:
        if not estado["terminado"]:
            estado["abandonado"] = True
            return None, True
    if fallo:
        raise fallo[0]
    return salida[0], False


def _resolver_adjuntos(
    container: CoreContainer, identificadores: list[str]
) -> tuple[list[dict], list[str]]:
    """Convierte identificadores en metadatos, separando los que ya no existen."""
    adjuntos: list[dict] = []
    faltantes: list[str] = []

    for identificador in identificadores:
        archivo = container.uploads.obtener(identificador)
        if archivo is None:
            faltantes.append(identificador)
            continue
        adjuntos.append({
            "id": archivo.id,
            "nombre": archivo.nombre_original,
            "mime": archivo.mime,
            "familia": archivo.familia,
        })

    return adjuntos, faltantes


# La lista de adjuntos que ve el modelo la antepone el núcleo desde la 4.3
# (`src.agent.core.texto_con_adjuntos`): aquí se hacía, y se guardaba en la base con
# el mensaje, así que al recargar la conversación la persona veía «Archivos adjuntos a
# este mensaje: … (id: …)» en su propio mensaje, y en el título si era el primero.


def _espacio_del_turno(container: CoreContainer, request: ChatRequest) -> str | None:
    """Fija el espacio de trabajo de este turno y devuelve lo que añade al prompt.

    **La conversación guardada manda.** Si ya existe, el turno se atiende en SU
    espacio, diga lo que diga la cabecera: una conversación de un proyecto no
    puede ver los archivos de otro porque la web tuviera otro seleccionado.

    Si es nueva, se crea aquí en el espacio de la petición, que el middleware ya
    comprobó que es de quien pide. Crearla aquí y no al guardar el primer
    mensaje es a propósito: en Supabase, guardar un mensaje asegura la
    conversación con una escritura que sobrescribe lo que recibe, y meter el
    espacio ahí haría que el sincronizador —que no corre en ningún espacio— las
    devolviera a «General».

    Un espacio que ya no existe se trata como «General»: la conversación volvió
    ahí al borrarlo.
    """
    from src.espacios.contexto import espacio_actual, fijar_espacio
    from src.espacios.modelos import bloque_para_el_prompt
    from src.espacios.repositorio import repositorio_de_espacios
    from src.memory.db import MemoryStorageError

    espacio_id = espacio_actual()

    if request.session_id and not request.temporary:
        try:
            guardada = container.repositories.sessions.get(request.session_id)
            if guardada is not None:
                espacio_id = guardada.espacio_id
            elif espacio_id:
                container.repositories.sessions.create(
                    request.session_id, espacio_id=espacio_id,
                )
        except MemoryStorageError:
            # Sin poder leer la conversación no se sabe su espacio. Se sigue en
            # el de la petición: lo peor que pasa es que este turno no vea sus
            # archivos, y es mejor que no contestar.
            logger.warning(
                "No se pudo leer el espacio de la conversación '%s'",
                request.session_id, exc_info=True,
            )

    espacio = None
    if espacio_id:
        repositorio = repositorio_de_espacios(container.repositories)
        try:
            espacio = repositorio.obtener(espacio_id) if repositorio else None
        except MemoryStorageError:
            logger.warning("No se pudo leer el espacio '%s'", espacio_id, exc_info=True)

    fijar_espacio(espacio.id if espacio is not None else None)
    return bloque_para_el_prompt(espacio)


def _preparar_turno(
    container: CoreContainer, request: ChatRequest
) -> tuple[str, list[dict], str | None, tuple[str, str] | None]:
    """Lo que hacen `/chat` y `/chat/stream` antes de lanzar el turno.

    En un solo sitio a propósito: si cada ruta comprobara por su cuenta el modo
    degradado, los adjuntos y el cupo, la segunda se quedaría atrás en cuanto se
    tocara la primera. Es lo que pasó con los dos `fetch` de la web.

    Todo lo que falla aquí lo hace **antes** de empezar a emitir, así que llega
    como una respuesta de error normal, con su código HTTP, y no a mitad de un
    stream que ya dijo 200.

    Devuelve `(mensaje, adjuntos, contexto_espacio, ocupada)`. `ocupada` es la marca de
    la conversación en curso: quien llama la libera cuando el turno termina.
    """
    if container.agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DEGRADED_MODE",
                "message": "Morgan está operando en modo degradado. El razonamiento IA no está disponible.",
                "details": container.llm_error or "Configure GROQ_API_KEY o GEMINI_API_KEY en .env",
            },
        )

    ocupada = _ocupar_conversacion(request)
    try:
        return (*_preparar_el_resto(container, request), ocupada)
    except BaseException:
        _liberar_conversacion(ocupada)
        raise


def _preparar_el_resto(
    container: CoreContainer, request: ChatRequest
) -> tuple[str, list[dict], str | None]:
    # El espacio va ANTES que los adjuntos: se buscan en el espacio de la
    # conversación, no en el que tuviera la web seleccionado.
    contexto_espacio = _espacio_del_turno(container, request)

    # Los adjuntos se resuelven aqui: si uno ya no existe conviene decirlo antes
    # de gastar un turno entero para que Morgan acabe respondiendo que no lo
    # encuentra.
    adjuntos, faltantes = _resolver_adjuntos(container, request.attachments)
    if faltantes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ATTACHMENT_NOT_FOUND",
                "message": (
                    "Estos archivos ya no están disponibles, puede que hayan "
                    f"caducado: {', '.join(faltantes)}."
                ),
            },
        )

    # Se apunta ANTES de empezar, no al terminar: si se contara al final, un
    # turno que falla a mitad saldria gratis y bastaria con provocar fallos para
    # saltarse el cupo. El usuario local esta exento —tu equipo, tus claves— y en
    # un chat temporal se cuenta igual, porque el modelo se paga lo mismo.
    try:
        container.uso.apuntar(usuario_actual(), "mensajes", rol_actual())
    except CuotaAgotada as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "CUOTA_AGOTADA", "message": str(exc)},
        ) from exc

    return request.message, adjuntos, contexto_espacio


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Enviar mensaje a Morgan Core",
    responses={
        503: {"model": ErrorResponse, "description": "Modo degradado o LLM no disponible"},
        500: {"model": ErrorResponse, "description": "Error interno durante la ejecución"},
    },
)
def chat(
    request: ChatRequest,
    container: CoreContainer = Depends(get_container),
) -> ChatResponse:
    """
    Envía un mensaje de usuario al agente Morgan Core.
    Ejecuta el bucle autónomo de razonamiento y uso de herramientas.
    """
    mensaje, adjuntos, contexto_espacio, ocupada = _preparar_turno(container, request)

    def turno() -> str:
        # Se libera al terminar el turno de verdad: puede seguir en su hilo después
        # de que esta respuesta se haya ido (`_sin_esperar_de_mas`).
        try:
            return container.agent.chat(
                mensaje,
                session_id=request.session_id,
                temporary=request.temporary,
                attachments=adjuntos,
                contexto_espacio=contexto_espacio,
                ejecutar_plan=request.ejecutar_plan,
            )
        finally:
            _liberar_conversacion(ocupada)

    start_time = time.time()
    try:
        response_text, se_paso = _sin_esperar_de_mas(turno, get_settings().http_deadline)
        elapsed = round(time.time() - start_time, 3)
        vigilar_turno(medicion_actual(), elapsed)

        if se_paso:
            logger.warning(
                "Se dejó de esperar el turno de la sesión '%s' a los %s s; sigue en su hilo",
                request.session_id,
                get_settings().http_deadline,
            )
            return ChatResponse(
                success=True,
                response=AVISO_SIGO_TRABAJANDO,
                model=_quien_respondio(container),
                elapsed_seconds=elapsed,
                etapas=_etapas(),
            )

        return ChatResponse(
            success=True,
            response=response_text or "",
            model=_quien_respondio(container),
            elapsed_seconds=elapsed,
            etapas=_etapas(),
        )
    except Exception:
        # El detalle va al log del servidor: str(e) podía contener rutas del disco.
        logger.exception("Fallo durante la ejecución del agente")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "AGENT_EXECUTION_ERROR",
                "message": "Error durante la ejecución del agente. "
                           "Consulta logs/morgan.log para el detalle.",
                "details": None,
            },
        )


# --- Streaming (V2.0.14) -----------------------------------------------------
#
# Diseño aprobado en docs/agente.md. `/chat` se queda tal cual.

#: Cada cuánto se manda señal de vida si no ha pasado nada. El proxy de Vercel
#: corta una respuesta **callada** a los 120 s —medido—, y una que habla llega
#: a los 180 —medido también—. 10 está muy por debajo de cualquier límite
#: plausible y no hace falta conocer el exacto para elegirlo.
LATIDO_SEGUNDOS = 10.0

#: Las mismas que la sonda del goteo: sin ellas, algo por el camino puede
#: acumular la respuesta y entregarla junta al final, que es exactamente lo que
#: el streaming existe para evitar.
CABECERAS_STREAM = {
    "Cache-Control": "no-store, no-transform",
    "X-Accel-Buffering": "no",
}

#: Marca que pone el hilo del turno en la cola al terminar, pase lo que pase.
_FIN_DEL_TURNO = object()


def _linea(evento: dict) -> str:
    return json.dumps(evento, ensure_ascii=False) + "\n"


async def _eventos_del_turno(
    container: CoreContainer,
    canal: CanalDelTurno,
    resultado: dict,
    estado: dict,
    cerrojo: threading.Lock,
    medicion,
    session_id: str | None,
    latido: float,
    turno: str | None = None,
) -> AsyncIterator[str]:
    """Las líneas NDJSON de un turno, según van pasando.

    `inicio` sale **antes de cualquier espera**. Es la lección de la sonda: si
    el primer byte no llega enseguida hay un búfer en medio, y entonces lo que
    venga después no se puede interpretar.

    **Asíncrono a propósito** (V2.0.27). Un generador síncrono se ejecuta en la
    reserva de 40 hilos de Starlette y ocupa uno durante todo el turno, esperando
    en la cola. Medido en la prueba de carga: con 60 turnos largos a la vez,
    `/health` tardaba 9,7 s — y Render reinicia el servicio si no contesta. Aquí
    se espera en el bucle de eventos, y el hilo del turno lo despierta.
    """
    completo = False
    bucle = asyncio.get_running_loop()
    hay_algo = asyncio.Event()
    canal.al_poner = lambda: bucle.call_soon_threadsafe(hay_algo.set)
    try:
        # `turno`: con él, «Detener» puede parar lo que el turno hace en el PC (3.4).
        yield _linea({"tipo": "inicio", "t": 0.0, **({"turno": turno} if turno else {})})

        ultimo = time.monotonic()
        while True:
            # Primero se baja la señal y luego se mira la cola: si el turno
            # encola justo entre las dos cosas, la señal vuelve a subir y la
            # espera de abajo termina enseguida. Al revés se perdería el aviso.
            hay_algo.clear()
            try:
                evento = canal.cola.get_nowait()
            except queue.Empty:
                espera = latido - (time.monotonic() - ultimo)
                if espera > 0:
                    try:
                        await asyncio.wait_for(hay_algo.wait(), espera)
                        continue
                    except asyncio.TimeoutError:
                        pass
                yield _linea({"tipo": "latido", "t": canal.segundos()})
                ultimo = time.monotonic()
                continue

            if evento is _FIN_DEL_TURNO:
                break
            yield _linea(evento)
            ultimo = time.monotonic()

        if "error" in resultado:
            # El detalle va al log. `str(exc)` puede traer rutas del disco o el
            # cuerpo crudo de la respuesta de un proveedor, y esto sale hacia el
            # navegador.
            logger.error(
                "Fallo durante la ejecución del agente (stream)",
                exc_info=resultado["error"],
            )
            yield _linea({
                "tipo": "error",
                "code": "AGENT_EXECUTION_ERROR",
                "message": "Error durante la ejecución del agente. "
                           "Consulta logs/morgan.log para el detalle.",
                "t": canal.segundos(),
            })
        else:
            yield _linea({
                "tipo": "fin",
                "success": True,
                "response": resultado.get("texto") or "",
                "model": _quien_respondio(container, medicion),
                "elapsed_seconds": canal.segundos(),
                "etapas": _etapas(medicion),
                "t": canal.segundos(),
            })
        completo = True
    finally:
        canal.al_poner = None
        if not completo:
            # Quien escuchaba se fue: cerró la pestaña o pulsó «Detener». El
            # turno **no se para** (decisión B del diseño): termina en su hilo y
            # se guarda, igual que un turno abandonado por `/chat`.
            with cerrojo:
                if not estado["terminado"]:
                    estado["abandonado"] = True
            logger.info(
                "Se dejó de escuchar el turno de la sesión '%s' a los %.1f s; sigue en su hilo",
                session_id, canal.segundos(),
            )


@router.post(
    "/chat/stream",
    summary="Enviar mensaje a Morgan Core, recibiendo el progreso",
    responses={
        200: {
            "description": "Una línea JSON por evento (NDJSON): `inicio`, `pensando`, "
                           "`herramienta`, `respaldo`, `latido`, y al final `fin` o `error`.",
            "content": {"application/x-ndjson": {}},
        },
        503: {"model": ErrorResponse, "description": "Modo degradado o LLM no disponible"},
    },
)
def chat_stream(
    request: ChatRequest,
    container: CoreContainer = Depends(get_container),
) -> StreamingResponse:
    """Lo mismo que `/chat`, pero contando lo que pasa mientras pasa.

    El tope del turno es `turn_timeout_stream`: 170 s en la nube frente a los 85
    de `/chat`, porque esta respuesta habla y el proxy solo corta el silencio.
    No hay `http_deadline`: con latidos no hace falta contestar antes del corte.

    El turno corre en su propio hilo y **no depende de que alguien escuche**. Si
    la conexión se cierra, termina y se guarda (decisión B del diseño).
    """
    mensaje, adjuntos, contexto_espacio, ocupada = _preparar_turno(container, request)
    try:
        return _lanzar_el_turno(container, request, mensaje, adjuntos, contexto_espacio, ocupada)
    except BaseException:
        _liberar_conversacion(ocupada)
        raise


def _lanzar_el_turno(container, request, mensaje, adjuntos, contexto_espacio, ocupada) -> StreamingResponse:
    canal = CanalDelTurno()
    medicion = medicion_actual()
    tope = get_settings().turn_timeout_stream
    resultado: dict = {}
    cerrojo = threading.Lock()
    estado = {"terminado": False, "abandonado": False}

    # El turno se da de alta para que «Detener» pueda parar lo que hace en el PC
    # (3.4, decisión mía). Solo el botón: una conexión que se cae no para nada.
    from src.canal import paradas

    turno, parada = paradas.abrir(usuario_actual())

    # El canal se fija y el contexto se copia AQUÍ, en la petición: la copia
    # lleva el usuario, el rol, el espacio, la medición, el canal y la señal de
    # parar. El hilo del turno escribe en la cola de ESTA petición y en ninguna otra.
    with escuchando(canal):
        marca = paradas.PARADA.set(parada)
        try:
            contexto = contextvars.copy_context()
        finally:
            paradas.PARADA.reset(marca)

    def correr() -> None:
        try:
            resultado["texto"] = contexto.run(
                lambda: container.agent.chat(
                    mensaje,
                    session_id=request.session_id,
                    temporary=request.temporary,
                    attachments=adjuntos,
                    contexto_espacio=contexto_espacio,
                    tope_turno=tope,
                    ejecutar_plan=request.ejecutar_plan,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - se informa por el stream
            resultado["error"] = exc
        finally:
            # Aquí y no en el generador: el turno termina aunque nadie escuche.
            paradas.cerrar(turno)
            _liberar_conversacion(ocupada)
            vigilar_turno(medicion, canal.segundos())
            with cerrojo:
                estado["terminado"] = True
                abandonado = estado["abandonado"]
            canal.meter(_FIN_DEL_TURNO)
            if abandonado:
                logger.warning(
                    "El turno sin nadie escuchando terminó a los %.1f s. Reparto completo: %s",
                    canal.segundos(),
                    medicion.cabecera() if medicion is not None else "sin medir",
                )

    threading.Thread(target=correr, name="turno-morgan-stream", daemon=True).start()

    return StreamingResponse(
        _eventos_del_turno(
            container, canal, resultado, estado, cerrojo, medicion,
            request.session_id, LATIDO_SEGUNDOS, turno,
        ),
        media_type="application/x-ndjson",
        headers=CABECERAS_STREAM,
    )


class PararTurno(BaseModel):
    turno: str = Field(..., min_length=1, max_length=64)


@router.post("/chat/parar", summary="Parar lo que un turno está haciendo en el PC")
def parar_turno(peticion: PararTurno) -> dict:
    """El botón «Detener» de la web (3.4, decisión mía): cancela lo que el turno
    está haciendo en el PC de la persona, en su siguiente punto seguro, y ninguna orden
    nueva de ese turno sale hacia el PC. El turno en sí termina y se guarda, como
    siempre. Solo lo para su dueño.
    """
    from src.canal import paradas

    return {"success": True, "parado": paradas.parar(peticion.turno, usuario_actual())}
