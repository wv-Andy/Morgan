"""
La sonda del canal (3.0-B): qué conexión aguanta Render entre la nube y un PC.

Es un **instrumento**, como `/diagnostico/goteo`: no sirve para usar Morgan, sirve
para decidir con números cómo hablará la nube con el agente local
([agente-local.md](../../../docs/agente-local.md) §7). El plan maestro (§9) prohíbe
elegir la tecnología de antemano, así que aquí están las cuatro candidatas haciendo
exactamente lo mismo:

| Canal | Cómo le llega un mensaje al «agente» | Cómo contesta |
|---|---|---|
| **WebSocket** | La nube lo empuja por el socket | Por el mismo socket |
| **Streaming** | Una línea más en una respuesta NDJSON que no termina | Un `POST` aparte |
| **Sondeo largo** | La nube retiene un `GET` hasta que hay mensaje o vence el plazo | Un `POST` aparte |
| **Sondeo corto** | El agente pregunta cada N segundos | Un `POST` aparte |

El experimento es siempre el mismo: la «nube» emite (`POST …/emitir`), el «agente»
lo recibe por su canal y contesta. **La ida y vuelta se mide con el reloj del
servidor**, desde que llega la emisión hasta que llega la respuesta: un solo reloj,
sin el desfase entre máquinas, y sin contar el viaje del propio `POST` de emisión,
que en el agente de verdad no existe (la nube decide dentro de su proceso).

## Solo en el despliegue de prueba

Se monta únicamente con el modo de prueba de carga (`src/prueba_de_carga.py`), que ya
se niega a encenderse con claves de modelos reales o con el Supabase de producción.
**En producción esta ruta no existe.** Retiene conexiones abiertas a propósito, y eso,
abierto, es una forma barata de agotar un servidor.

## Un hallazgo que va al contrato

Los middlewares de Morgan (identidad, CSRF, token compartido) son `@app.middleware
("http")`: **una conexión WebSocket no pasa por ninguno.** Por eso el socket de esta
sonda comprueba el token él mismo, en el saludo, y el canal de verdad de la 3.0-D
tendrá que hacer lo mismo. Las rutas HTTP de aquí sí pasan por la identidad de
siempre: con un token personal (`Bearer mgn_…`) o con sesión.
"""

import asyncio
import json
import logging
import secrets
import time
from collections import OrderedDict, deque

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.identidad import usuario_actual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sonda-canal", tags=["Sonda del canal (3.0-B)"])

#: Mensajes en espera por buzón. Lleno, la emisión se rechaza (429): es también el
#: comportamiento que se quiere del canal de verdad (backpressure, §13 del plan).
MENSAJES_POR_BUZON = 16

#: Buzones vivos a la vez en el proceso. Con más, se olvida el más antiguo.
BUZONES_MAXIMOS = 20

#: Lo más que se retiene un sondeo largo. Por encima está el corte del proxy que se
#: quiere encontrar, así que se deja pedir hasta aquí y no más.
ESPERA_MAXIMA = 150

#: Latidos cada cuánto, como mucho. 0 = sin latidos (para medir cuánto silencio
#: aguanta la conexión antes de que alguien la corte).
LATIDO_MAXIMO = 120

#: Latidos seguidos sin noticias del cliente antes de darlo por muerto.
LATIDOS_PERDIDOS_TOLERADOS = 3

#: Tope de la duración de una conexión de la sonda: una hora.
DURACION_MAXIMA = 3600

SIN_BUFER = {
    "Cache-Control": "no-store, no-transform",
    "X-Accel-Buffering": "no",
}


class Buzon:
    """Los mensajes de una sesión de la sonda, y las idas y vueltas medidas."""

    def __init__(self) -> None:
        self.cola: asyncio.Queue[dict] = asyncio.Queue(maxsize=MENSAJES_POR_BUZON)
        #: id del mensaje → cuándo llegó su emisión (reloj monótono del servidor).
        self.emitidos: dict[str, float] = {}
        self.idas_y_vueltas: deque[dict] = deque(maxlen=500)


_buzones: "OrderedDict[str, Buzon]" = OrderedDict()

#: Cómo terminó cada conexión, visto desde el servidor. Es lo que el cliente no
#: puede saber solo: si fue la nube la que dio al cliente por muerto, y cuándo.
_finales: deque[dict] = deque(maxlen=200)


def _buzon(user_id: str, sesion: str) -> Buzon:
    clave = f"{user_id}:{sesion}"
    buzon = _buzones.get(clave)
    if buzon is None:
        if len(_buzones) >= BUZONES_MAXIMOS:
            _buzones.popitem(last=False)
        buzon = _buzones[clave] = Buzon()
    _buzones.move_to_end(clave)
    return buzon


def _anotar_final(user_id: str, sesion: str, canal: str, inicio: float, motivo: str) -> None:
    final = {
        "user_id": user_id, "sesion": sesion, "canal": canal,
        "duracion_s": round(time.monotonic() - inicio, 1), "motivo": motivo,
        "momento": time.time(),
    }
    _finales.append(final)
    logger.info("Sonda del canal: %s terminó a los %.1f s (%s)", canal, final["duracion_s"], motivo)


def _validar_sesion(sesion: str) -> str:
    if not sesion or len(sesion) > 40 or not sesion.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail={
            "code": "SESION_INVALIDA", "message": "La sesión de la sonda son letras, números y guiones.",
        })
    return sesion


def _mensaje(buzon: Buzon) -> dict:
    ident = secrets.token_hex(6)
    buzon.emitidos[ident] = time.monotonic()
    # Acotado: una emisión que nadie contesta no puede quedarse para siempre.
    if len(buzon.emitidos) > 1000:
        buzon.emitidos.pop(next(iter(buzon.emitidos)))
    return {"tipo": "mensaje", "id": ident, "t_servidor": time.time()}


def _contestado(buzon: Buzon, ident: str, canal: str) -> float | None:
    llegada = buzon.emitidos.pop(ident, None)
    if llegada is None:
        return None
    ms = round((time.monotonic() - llegada) * 1000, 1)
    buzon.idas_y_vueltas.append({"id": ident, "canal": canal, "ms": ms})
    return ms


# --- La «nube» emite ---------------------------------------------------------


@router.post("/{sesion}/emitir", summary="La nube emite un mensaje al agente de prueba")
async def emitir(sesion: str) -> dict:
    buzon = _buzon(usuario_actual(), _validar_sesion(sesion))
    mensaje = _mensaje(buzon)
    try:
        buzon.cola.put_nowait(mensaje)
    except asyncio.QueueFull:
        buzon.emitidos.pop(mensaje["id"], None)
        raise HTTPException(status_code=429, detail={
            "code": "BUZON_LLENO", "message": "El agente de prueba no está recogiendo mensajes.",
        })
    return {"success": True, "id": mensaje["id"]}


class Respuesta(BaseModel):
    id: str
    canal: str


@router.post("/{sesion}/respuesta", summary="El agente de prueba contesta un mensaje")
async def respuesta(sesion: str, datos: Respuesta) -> dict:
    buzon = _buzon(usuario_actual(), _validar_sesion(sesion))
    return {"success": True, "ms": _contestado(buzon, datos.id, datos.canal[:20])}


@router.get("/{sesion}/resultados", summary="Idas y vueltas y finales de conexión medidos")
async def resultados(sesion: str) -> dict:
    user_id = usuario_actual()
    buzon = _buzon(user_id, _validar_sesion(sesion))
    return {
        "success": True,
        "idas_y_vueltas": list(buzon.idas_y_vueltas),
        "finales": [f for f in _finales if f["user_id"] == user_id and f["sesion"] == sesion],
    }


# --- Sondeo (largo o corto) ----------------------------------------------------


@router.get("/{sesion}/esperar", summary="Sondeo: espera un mensaje hasta `max` segundos")
async def esperar(
    sesion: str,
    max: float = Query(25, ge=0, le=ESPERA_MAXIMA, description="0 = sondeo corto"),
) -> dict:
    user_id = usuario_actual()
    buzon = _buzon(user_id, _validar_sesion(sesion))
    inicio = time.monotonic()
    try:
        if max == 0:
            return {"success": True, "mensaje": buzon.cola.get_nowait()}
        return {"success": True, "mensaje": await asyncio.wait_for(buzon.cola.get(), timeout=max)}
    except (asyncio.QueueEmpty, asyncio.TimeoutError):
        return {"success": True, "mensaje": None}
    except asyncio.CancelledError:
        _anotar_final(user_id, sesion, f"sondeo max={max:g}", inicio, "cortado por el otro lado")
        raise


# --- Streaming ---------------------------------------------------------------


@router.get("/{sesion}/flujo", summary="Streaming NDJSON: mensajes y latidos")
async def flujo(
    sesion: str,
    latido: float = Query(15, ge=0, le=LATIDO_MAXIMO, description="0 = sin latidos"),
) -> StreamingResponse:
    user_id = usuario_actual()
    buzon = _buzon(user_id, _validar_sesion(sesion))
    canal = f"flujo latido={latido:g}"

    async def lineas():
        inicio = time.monotonic()
        # La primera línea sale al momento: si no llega enseguida, hay un búfer en
        # medio y el resto no se puede interpretar (lo mismo que en el goteo).
        yield json.dumps({"tipo": "inicio", "t_servidor": time.time()}) + "\n"
        motivo = "duración máxima"
        try:
            while time.monotonic() - inicio < DURACION_MAXIMA:
                try:
                    mensaje = await asyncio.wait_for(
                        buzon.cola.get(), timeout=latido if latido else DURACION_MAXIMA,
                    )
                except asyncio.TimeoutError:
                    yield json.dumps({"tipo": "latido", "t_servidor": time.time()}) + "\n"
                    continue
                yield json.dumps(mensaje) + "\n"
        except asyncio.CancelledError:
            motivo = "cortado por el otro lado"
            raise
        finally:
            _anotar_final(user_id, sesion, canal, inicio, motivo)

    return StreamingResponse(lineas(), media_type="application/x-ndjson", headers=SIN_BUFER)


# --- WebSocket ---------------------------------------------------------------


def _usuario_del_token(websocket: WebSocket) -> str | None:
    """El dueño del token del saludo, o None. Los middlewares no llegan aquí."""
    from src.api.dependencies import get_container
    from src.config import get_settings
    from src.identidad.repositorio import repositorio_de_cuentas
    from src.identidad.tokens import LECTURA, ServicioDeTokens, token_de_la_cabecera

    if not get_settings().tokens_api:
        return None
    valor = token_de_la_cabecera(websocket.headers.get("authorization"))
    if valor is None:
        return None
    try:
        repo = repositorio_de_cuentas(get_container().repositories)
        resuelto = ServicioDeTokens(repo).resolver(valor)
    except Exception:
        logger.warning("La sonda del canal no pudo comprobar un token", exc_info=True)
        return None
    if resuelto is None or LECTURA not in resuelto["token"]["alcances"]:
        return None
    return resuelto["usuario"]["id"]


@router.websocket("/{sesion}/ws")
async def socket(websocket: WebSocket, sesion: str, latido: float = LATIDO_MAXIMO):
    user_id = _usuario_del_token(websocket)
    if user_id is None:
        # 4401: un código propio en el rango de la aplicación. Cerrar ANTES de
        # aceptar es lo que un canal de verdad tiene que hacer: sin credencial, ni
        # una trama.
        await websocket.close(code=4401)
        return
    try:
        _validar_sesion(sesion)
    except HTTPException:
        await websocket.close(code=4400)
        return

    latido = max(0.0, min(float(latido), LATIDO_MAXIMO))
    await websocket.accept()
    buzon = _buzon(user_id, sesion)
    canal = f"ws latido={latido:g}"
    inicio = time.monotonic()
    ultima_noticia = time.monotonic()
    motivo = "duración máxima"

    async def escuchar():
        nonlocal ultima_noticia
        while True:
            datos = json.loads(await websocket.receive_text())
            ultima_noticia = time.monotonic()
            if datos.get("tipo") == "respuesta":
                ms = _contestado(buzon, str(datos.get("id")), "ws")
                await websocket.send_text(json.dumps({"tipo": "anotada", "id": datos.get("id"), "ms": ms}))

    escucha = asyncio.create_task(escuchar())
    try:
        await websocket.send_text(json.dumps({"tipo": "inicio", "t_servidor": time.time()}))
        while time.monotonic() - inicio < DURACION_MAXIMA:
            if escucha.done():
                escucha.result()  # relanza la desconexión del cliente
            try:
                mensaje = await asyncio.wait_for(
                    buzon.cola.get(), timeout=latido if latido else 5,
                )
                await websocket.send_text(json.dumps(mensaje))
                continue
            except asyncio.TimeoutError:
                pass
            if not latido:
                continue
            # Sin noticias del cliente en varios latidos: se le da por muerto. Es lo
            # que detecta un PC suspendido, que deja la conexión medio abierta.
            if time.monotonic() - ultima_noticia > LATIDOS_PERDIDOS_TOLERADOS * latido:
                motivo = f"cliente mudo {LATIDOS_PERDIDOS_TOLERADOS} latidos"
                await websocket.close(code=4408)
                return
            await websocket.send_text(json.dumps({"tipo": "latido", "t_servidor": time.time()}))
    except WebSocketDisconnect as exc:
        motivo = f"el cliente cerró ({exc.code})"
    except Exception as exc:
        motivo = f"error: {type(exc).__name__}"
    finally:
        escucha.cancel()
        _anotar_final(user_id, sesion, canal, inicio, motivo)


# --- Órdenes de verdad al agente local (3.0-D) ---------------------------------
#
# Ninguna herramienta usa todavía el canal del agente (eso es la 3.0-E). Para medir el
# canal completo en Render —el despacho, el agente de verdad en el PC y la vuelta— hace
# falta disparar una orden desde fuera, y eso es esta ruta. Como el resto de la sonda,
# **solo existe en el despliegue de prueba**. Manda la orden al agente de quien llama:
# el destino sale del contexto, igual que en el despacho de verdad.


@router.post("/orden-agente/{capacidad}", summary="Manda una orden al agente local de quien llama")
async def orden_al_agente(capacidad: str, argumentos: dict | None = Body(default=None)) -> dict:
    """Con argumentos en el cuerpo (3.0.5), para medir también `read_file` y
    `search_files`. El agente los valida igual que los de una orden de verdad."""
    from src.canal.despacho import ErrorDelCanal, enviar_async

    try:
        respuesta = await enviar_async(usuario_actual(), capacidad[:40], argumentos or {})
    except ErrorDelCanal as exc:
        raise HTTPException(status_code=409, detail={"code": type(exc).__name__, "message": str(exc)})
    return {"success": True, "respuesta": respuesta}


@router.post("/copiar", summary="Copia un archivo del PC y lo deja descargable (3.1-E)")
async def copiar(argumentos: dict | None = Body(default=None)) -> dict:
    """Lo mismo que hace Morgan cuando se le pide un archivo, con los tiempos.

    Ejercita el camino entero —orden, trozos, comprobación, guardado— para medirlo en
    Render con un archivo grande de verdad, que es lo que no se puede medir en local.
    """
    import asyncio

    from src.api.dependencies import get_container
    from src.canal.herramientas import CopiarDelEquipo

    herramienta = CopiarDelEquipo(get_container().uploads)
    t0 = time.perf_counter()
    resultado = await asyncio.to_thread(herramienta.execute, path=str((argumentos or {}).get("path") or ""))
    return {"success": resultado["success"], "resultado": resultado,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


@router.get("/registro-agentes", summary="Qué ve el registro de agentes de este proceso")
async def registro_de_agentes() -> dict:
    """Para medir en un despliegue real si el registro conserva la conexión del agente.

    Dice cuántos agentes ve este proceso, si el de quien pregunta está y desde cuándo, y
    el identificador del proceso: con varios procesos sirviendo, cada uno tiene su
    registro, y eso es justo lo que se quiere poder ver.
    """
    import os

    from src.canal.registro import REGISTRO

    propia = REGISTRO.de(usuario_actual())
    return {
        "success": True,
        "pid": os.getpid(),
        "conectados": REGISTRO.conectados(),
        "el_mio": propia is not None,
        "conectado_hace_s": round(time.time() - propia.conectado_en, 1) if propia else None,
    }

