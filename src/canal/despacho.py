"""
Mandar una petición al agente local de la persona del turno (3.0-D).

**A quién se manda sale del contexto de la petición, nunca de un argumento.** Por eso
`enviar` no recibe un usuario: lo lee de `usuario_actual()`, igual que todos los
repositorios. Es lo que hace imposible, por construcción, que el turno de A llegue al
agente de B (amenaza E del contrato), por mucho que el modelo o el cuerpo de una
petición digan otra cosa.

Lo que hace, en orden:

1. **Espera a que el agente aparezca**, unos segundos. Medido en la 3.0-B: durante un
   despliegue conviven dos instancias ~13-15 s y el agente puede estar aún en la vieja.
   Sin esta espera, la nube diría «tu equipo no está conectado» teniéndolo.
2. ~~Comprueba en la base que el agente sigue activo~~: **ya no en cada orden** (3.0.5,
   decisión mía). Costaba ~60 ms, un tercio de la orden (medido en Render). Lo cubre
   que revocar, cambiar o restablecer la contraseña y borrar la cuenta **cierran la
   conexión al momento**, y que el canal revalida cada 30 s (`REVALIDAR_CADA`). El hueco
   que queda, aceptado: una revocación que llegue por **otra instancia** de la nube
   (dos conviven unos segundos en un despliegue) tarda hasta 30 s en cortar.
3. **Cola**: una operación en vuelo y cuatro esperando; con más, `AgenteOcupado` al
   momento.
4. **Envía** la orden con `request_id` (el turno), `command_id` (esta operación), el
   `agent_id` del destino y el tiempo que le queda (`vence_en_ms`), y **espera** el
   resultado hasta el plazo.
5. **Audita** la operación con sus identificadores y el desglose de tiempos.

**Por qué el plazo viaja como «milisegundos que quedan» y no como una hora.** El
contrato decía `deadline`, una marca de tiempo. Pero la comprueba el reloj del PC, y un
PC puede ir minutos adelantado o atrasado: las órdenes buenas se rechazarían por
caducadas, o valdrían de más. Un plazo relativo lo mide el agente con su propio reloj
desde que la recibe.
"""

import asyncio
import base64
import hashlib
import json
import logging
import threading
import time
import uuid

from src.agente.protocolo import MAX_COPIA
from src.canal.registro import EN_ESPERA, EN_VUELO, REGISTRO, Conexion
from src.identidad import usuario_actual

logger = logging.getLogger(__name__)

#: Cuánto se espera a que el agente aparezca antes de decir que no está. Cubre el
#: relevo de instancias de un despliegue. Medido en Render (3.0-D): con 10 s, una
#: orden en pleno despliegue se sirvió a los 10,04 s, en el límite, porque el agente
#: tardó 13,3 s en volver. El aviso de reinicio (`reinicio.py`) debería acortar eso a
#: un par de segundos; esto es la red de seguridad si el aviso no llegara. Y solo
#: se nota en ese hueco: sin agente conectado al empezar el turno, las herramientas
#: del equipo ni siquiera se ofrecen (3.0-E).
ESPERA_REAPARICION = 15.0

#: Plazo de una operación, cola incluida (§15 del contrato).
PLAZO = 20.0


class ErrorDelCanal(RuntimeError):
    """Base: el mensaje es para la persona, tal cual."""


class AgenteNoConectado(ErrorDelCanal):
    def __init__(self) -> None:
        super().__init__(
            "Tu equipo no está conectado: el agente local no está en marcha o el PC está "
            "apagado. Enciéndelo o arranca el agente y vuelve a pedirlo."
        )


class AgenteOcupado(ErrorDelCanal):
    def __init__(self) -> None:
        super().__init__("Tu equipo está ocupado con otras peticiones. Prueba en unos segundos.")


class AgenteSinRespuesta(ErrorDelCanal):
    def __init__(self) -> None:
        super().__init__("Tu equipo no respondió a tiempo.")


class AgenteDesconectado(ErrorDelCanal):
    def __init__(self) -> None:
        super().__init__("Tu equipo se desconectó mientras hacía lo que pediste.")


def _nombres(conexiones) -> str:
    return ", ".join(f"«{getattr(c, 'nombre', c)}»" for c in conexiones) or "ninguno"


class EquipoAmbiguo(ErrorDelCanal):
    """Varios PC conectados y no se dijo en cuál (3.7). Decisión mía: nunca adivinar."""

    def __init__(self, conexiones) -> None:
        super().__init__(
            f"Tienes varios equipos conectados ({_nombres(conexiones)}). Hay que decir en cuál "
            "(argumento equipo); si no está claro, pregúntaselo a la persona.")


class EquipoNoConectado(ErrorDelCanal):
    def __init__(self, equipo: str, conexiones) -> None:
        super().__init__(f"«{equipo}» no está conectado. Conectados: {_nombres(conexiones)}.")


class EquipoSinCapacidad(ErrorDelCanal):
    def __init__(self, nombre: str) -> None:
        super().__init__(f"«{nombre}» no ofrece eso: no lo tiene encendido en ese PC.")


def elegir(user_id: str, equipo: str | None) -> Conexion | None:
    """El PC al que va una orden (3.7). Con `equipo`, el que tenga ese nombre (o ese
    `agent_id`); sin él, el único conectado. Con varios y sin decir cuál, `EquipoAmbiguo`:
    escribir en el PC equivocado sería grave, así que no se adivina. `None`: todavía no
    hay ninguno que valga (se espera a que aparezca)."""
    vivas = REGISTRO.todas(user_id)
    if equipo:
        buscado = str(equipo).strip().lower()
        iguales = [c for c in vivas if c.nombre.strip().lower() == buscado or c.agent_id == equipo]
        if len(iguales) > 1:
            raise EquipoAmbiguo(iguales)        # dos PC con el mismo nombre
        return iguales[0] if iguales else None
    volviendo = REGISTRO.recientes(user_id, ESPERA_REAPARICION)
    if len(vivas) + len(volviendo) > 1:
        # Uno cortado hace un momento sigue contando: si no, esta orden iría al otro
        # sin preguntar (3.7.5).
        raise EquipoAmbiguo([*vivas, *volviendo])
    return vivas[0] if vivas else None


#: Lo que no es cómo acabó una orden, sino un paso por el camino: no va al historial.
_DE_PASO = {"desconectado_recuperando"}


def _al_historial(conexion: Conexion | None, user_id: str, capability: str, request_id: str,
                  command_id: str, resultado: str, total_ms: float) -> None:
    """La orden al historial de la base (3.7), **en otro hilo**: esto corre en el bucle de
    los sockets, y una escritura en Supabase (~60 ms) lo pararía para todos. Si falla, se
    avisa y se sigue: el historial no puede tumbar una orden."""
    if resultado in _DE_PASO:
        return
    datos = {"command_id": command_id, "user_id": user_id,
             "agent_id": conexion.agent_id if conexion else None, "request_id": request_id,
             "capability": capability, "estado": resultado, "ms": round(total_ms, 1),
             "creado_en": time.time()}

    def escribir():
        try:
            from src.api.dependencies import get_container
            from src.identidad.repositorio import repositorio_de_cuentas

            repositorio_de_cuentas(get_container().repositories).anotar_orden(datos)
        except Exception:
            logger.warning("No se pudo anotar una orden en el historial", exc_info=True)

    threading.Thread(target=escribir, name="historial-orden", daemon=True).start()


def _auditar(conexion: Conexion | None, user_id: str, capability: str, request_id: str,
             command_id: str, resultado: str, total_ms: float, tiempos: dict | None = None) -> None:
    _al_historial(conexion, user_id, capability, request_id, command_id, resultado, total_ms)
    try:
        from src.api.dependencies import get_container

        tiempos = tiempos or {}
        agente_ms = (tiempos.get("cola_ms") or 0) + (tiempos.get("ejecucion_ms") or 0)
        get_container().audit_logger.registrar_evento(
            "orden_agente", actor=user_id,
            objetivo=conexion.agent_id if conexion else None,
            resultado=resultado,
            detalle=(
                f"{capability} req={request_id} cmd={command_id} total={total_ms:.0f}ms "
                f"agente={agente_ms:.0f}ms red={max(0.0, total_ms - agente_ms):.0f}ms"
            ),
        )
    except Exception:
        logger.warning("No se pudo auditar una orden al agente", exc_info=True)


def recibir_fragmento(conexion: Conexion, mensaje: dict) -> None:
    """Un trozo de un archivo que se está copiando del PC (3.1-E).

    **Solo si hay una orden esperando con ese `command_id`**: un agente comprometido no
    puede llenar la memoria de la nube mandando trozos de nada. Y el total no pasa del
    tope de una copia; si lo pasa, se marca el error y se tiran los trozos: el resultado
    fallará al comprobarlo.
    """
    command_id = str(mensaje.get("command_id") or "")
    if command_id not in conexion.esperando:
        logger.warning("Trozo de una copia que nadie pidió")
        return
    estado = conexion.fragmentos.setdefault(command_id, {"trozos": [], "bytes": 0, "error": None})
    if estado["error"]:
        return
    try:
        datos = base64.b64decode(str(mensaje.get("datos") or ""), validate=True)
    except (ValueError, TypeError):
        estado["error"] = "Un trozo del archivo llegó mal."
        estado["trozos"].clear()
        return
    if estado["bytes"] + len(datos) > MAX_COPIA:
        estado["error"] = f"La copia pasa del tope de {MAX_COPIA // (1024 * 1024)} MB."
        estado["trozos"].clear()
        return
    estado["trozos"].append(datos)
    estado["bytes"] += len(datos)


def recibir_estado(conexion: Conexion, mensaje: dict) -> None:
    """Un cambio de estado de una orden (protocolo 3): en cola, en marcha, cancelándose.
    Solo de una que esta conexión tiene en vuelo; el resto se ignora."""
    avisar = conexion.estados.get(str(mensaje.get("command_id") or ""))
    if avisar is not None:
        avisar({k: mensaje.get(k) for k in ("estado", "posicion", "sin_vuelta") if k in mensaje})


def recibir_consulta(conexion: Conexion, mensaje: dict) -> None:
    """La respuesta del agente a «¿qué pasó con esta orden?». Solo si se le preguntó."""
    futuro = conexion.consultas.get(str(mensaje.get("command_id") or ""))
    if futuro is not None and not futuro.done():
        futuro.set_result(mensaje)


async def _esperar_conexion(user_id: str, agent_id: str, hasta: float) -> Conexion | None:
    """Que vuelva **ese** PC: tras un corte, la orden se busca en el que la tenía, nunca
    en otro de la misma persona (3.7)."""
    conexion = REGISTRO.de(user_id, agent_id)
    while conexion is None and time.monotonic() < hasta:
        await asyncio.sleep(0.25)
        conexion = REGISTRO.de(user_id, agent_id)
    return conexion


def _orden(conexion: Conexion, request_id: str, command_id: str, capability: str,
           arguments: dict, queda: float) -> str:
    return json.dumps({
        "tipo": "orden",
        "protocol_version": conexion.protocolo,
        "request_id": request_id,
        "command_id": command_id,
        "agent_id": conexion.agent_id,
        "capability": capability,
        "arguments": arguments,
        "vence_en_ms": max(0, int(queda * 1000)),
        # Cuándo salió (3.6): una orden retenida en una conexión medio abierta no se
        # ejecuta si llega más tarde que su plazo. Un agente viejo lo ignora.
        "enviada_en": time.time(),
    })


#: Tras un corte, cuántas veces se vuelve a buscar al agente para preguntarle.
RECUPERACIONES = 2
#: Lo que se espera la respuesta a una consulta.
ESPERA_CONSULTA = 10.0


async def _recuperar(user_id: str, agent_id: str, capability: str, arguments: dict, request_id: str,
                     command_id: str, limite: float, novedades) -> tuple[dict, Conexion, str]:
    """Tras un corte con la orden en vuelo, **preguntar en vez de adivinar** (3.4).

    Antes, un corte a mitad se contestaba «no se sabe si llegó a hacerse». Ahora se
    espera a que el agente vuelva y se le pregunta por la orden:

    - si ya terminó, su respuesta (del diario del PC);
    - si **nunca llegó**, se reenvía con el mismo `command_id`, sin peligro: el agente
      no ejecuta dos veces el mismo;
    - si sigue en cola o en marcha, se sigue esperando su resultado;
    - si era una lectura cuyo resultado no se guardó, se repite (no hace daño). Y una
      copia siempre se repite: sus trozos venían por la conexión que se cortó.

    Devuelve la respuesta, la conexión por la que llegó y el `command_id` con que llegó.
    """
    for _ in range(RECUPERACIONES):
        conexion = await _esperar_conexion(user_id, agent_id, min(limite, time.monotonic() + ESPERA_REAPARICION))
        if conexion is None or conexion.protocolo < 3:
            break
        loop = conexion.loop
        consulta = loop.create_future()
        futuro = loop.create_future()
        usado = command_id
        conexion.consultas[command_id] = consulta
        conexion.esperando[command_id] = futuro
        if novedades is not None:
            conexion.estados[command_id] = novedades.put
        conexion.pendientes += 1
        try:
            async with asyncio.timeout(max(0.1, limite - time.monotonic())):
                async with conexion.turno:
                    if not conexion.viva:
                        raise AgenteDesconectado()
                    await conexion.enviar(json.dumps({"tipo": "consultar", "command_id": command_id}))
                    dicha = await asyncio.wait_for(consulta, ESPERA_CONSULTA)
                    estado = dicha.get("estado")
                    if dicha.get("respuesta") and capability != "copy_file":
                        return dicha["respuesta"], conexion, usado
                    if estado in ("PENDING", "RUNNING", "CANCEL_REQUESTED"):
                        return await futuro, conexion, usado
                    if estado == "DESCONOCIDA":
                        await conexion.enviar(_orden(conexion, request_id, command_id, capability,
                                                     arguments, limite - time.monotonic()))
                        return await futuro, conexion, usado
                    if dicha.get("repetible") or capability == "copy_file":
                        usado = f"{command_id}-r"
                        conexion.esperando[usado] = otro = loop.create_future()
                        if novedades is not None:
                            conexion.estados[usado] = novedades.put
                        await conexion.enviar(_orden(conexion, request_id, usado, capability,
                                                     arguments, limite - time.monotonic()))
                        return await otro, conexion, usado
                    return ({"tipo": "resultado", "command_id": command_id, "estado": estado,
                             "resultado": {"success": False, "data": None,
                                           "error": f"Tu equipo dice que esa orden acabó así: {estado}."}},
                            conexion, usado)
        except AgenteDesconectado:
            continue                    # otro corte: se vuelve a buscar
        except TimeoutError:
            raise AgenteSinRespuesta() from None
        finally:
            conexion.pendientes -= 1
            conexion.consultas.pop(command_id, None)
            for cid in (command_id, f"{command_id}-r"):
                if cid != usado:
                    conexion.esperando.pop(cid, None)
                    conexion.estados.pop(cid, None)
    raise AgenteDesconectado()


async def cancelar_async(user_id: str, command_id: str, motivo: str = "la_persona") -> bool:
    """Pide al agente parar una orden. No espera el turno de la conexión: la orden que
    hay que parar es justo la que lo ocupa. Va al PC que tiene esa orden en vuelo (3.7)."""
    conexion = next((c for c in REGISTRO.todas(user_id) if command_id in c.esperando), None)
    if conexion is None or conexion.protocolo < 3:
        return False
    try:
        await conexion.enviar(json.dumps({"tipo": "cancelar", "command_id": command_id, "motivo": motivo}))
        return True
    except Exception:
        return False


async def enviar_async(
    user_id: str, capability: str, arguments: dict, request_id: str | None = None,
    plazo: float | None = None, command_id: str | None = None, novedades=None,
    equipo: str | None = None,
) -> dict:
    """La versión del bucle del servidor. `enviar` es la que usan las herramientas.

    `novedades`: una cola de hilos donde se dejan los cambios de estado de la orden
    (protocolo 3), para que el hilo del turno se los cuente a la persona.
    """
    plazo = PLAZO if plazo is None else plazo
    request_id = request_id or str(uuid.uuid4())
    command_id = command_id or str(uuid.uuid4())
    inicio = time.monotonic()

    conexion = elegir(user_id, equipo)
    limite_espera = inicio + ESPERA_REAPARICION
    while conexion is None and time.monotonic() < limite_espera:
        await asyncio.sleep(0.25)
        conexion = elegir(user_id, equipo)
    if conexion is None:
        _auditar(None, user_id, capability, request_id, command_id, "no_conectado", 0)
        if equipo:
            raise EquipoNoConectado(equipo, REGISTRO.todas(user_id))
        raise AgenteNoConectado()
    if capability != "estado" and capability not in conexion.capacidades:
        raise EquipoSinCapacidad(conexion.nombre)

    if conexion.pendientes >= EN_VUELO + EN_ESPERA:
        _auditar(conexion, user_id, capability, request_id, command_id, "ocupado", 0)
        raise AgenteOcupado()

    conexion.pendientes += 1
    futuro = conexion.loop.create_future()
    usada, usado = conexion, command_id
    try:
        async with asyncio.timeout(plazo):
            async with conexion.turno:  # una en vuelo; el resto espera aquí
                if not conexion.viva:
                    raise AgenteDesconectado()
                conexion.esperando[command_id] = futuro
                if novedades is not None:
                    conexion.estados[command_id] = novedades.put
                await conexion.enviar(_orden(conexion, request_id, command_id, capability, arguments,
                                             plazo - (time.monotonic() - inicio)))
                respuesta = await futuro
    except TimeoutError:
        _auditar(conexion, user_id, capability, request_id, command_id, "sin_respuesta",
                 (time.monotonic() - inicio) * 1000)
        raise AgenteSinRespuesta() from None
    except AgenteDesconectado:
        if conexion.protocolo < 3:
            _auditar(conexion, user_id, capability, request_id, command_id, "desconectado",
                     (time.monotonic() - inicio) * 1000)
            raise
        # Protocolo 3: preguntar en vez de adivinar.
        _auditar(conexion, user_id, capability, request_id, command_id, "desconectado_recuperando",
                 (time.monotonic() - inicio) * 1000)
        try:
            respuesta, usada, usado = await _recuperar(
                user_id, conexion.agent_id, capability, arguments, request_id, command_id,
                inicio + plazo + ESPERA_REAPARICION, novedades)
        except ErrorDelCanal:
            _auditar(conexion, user_id, capability, request_id, command_id, "desconectado",
                     (time.monotonic() - inicio) * 1000)
            raise
    finally:
        conexion.pendientes -= 1
        conexion.esperando.pop(command_id, None)
        conexion.estados.pop(command_id, None)
        trozos = conexion.fragmentos.pop(command_id, None)

    if usada is not conexion or usado != command_id:
        usada.esperando.pop(usado, None)
        usada.estados.pop(usado, None)
        trozos = usada.fragmentos.pop(usado, None)

    if capability == "copy_file":
        # Lo que llegó en trozos va aparte del resultado, y se comprueba contra lo que
        # el agente dijo que mandaba: tamaño y huella (3.1-E).
        respuesta["_contenido"] = _archivo_recibido(trozos, respuesta.get("resultado") or {})

    total_ms = (time.monotonic() - inicio) * 1000
    _auditar(usada, user_id, capability, request_id, command_id,
             str(respuesta.get("estado", "?")).lower(), total_ms, respuesta.get("tiempos"))
    respuesta["total_ms"] = round(total_ms, 1)
    return respuesta


class CopiaNoValida(ErrorDelCanal):
    def __init__(self, detalle: str) -> None:
        super().__init__(f"La copia del archivo no llegó bien: {detalle}")


def _archivo_recibido(trozos: dict | None, resultado: dict) -> bytes:
    """Los trozos juntos, comprobados contra lo que el agente dijo que mandaba.

    **La huella se comprueba en la nube**, no se cree: si el archivo cambió mientras se
    leía, o un trozo se perdió, no se guarda una copia a medias.
    """
    if not resultado.get("success"):
        return b""
    if trozos is None:
        raise CopiaNoValida("no llegó nada")
    if trozos["error"]:
        raise CopiaNoValida(trozos["error"])
    contenido = b"".join(trozos["trozos"])
    datos = resultado.get("data") or {}
    if datos.get("bytes") != len(contenido):
        raise CopiaNoValida(f"llegaron {len(contenido)} bytes y el PC anunció {datos.get('bytes')}")
    if datos.get("sha256") != hashlib.sha256(contenido).hexdigest():
        raise CopiaNoValida("la huella no coincide")
    return contenido


class TurnoParado(ErrorDelCanal):
    def __init__(self) -> None:
        super().__init__("La persona paró el turno: no se hace nada más en su PC.")


#: Lo que se cuenta en el progreso del turno según el estado de la orden en el PC.
def _contar(novedad: dict) -> None:
    from src.eventos_turno import emitir

    estado = novedad.get("estado")
    if estado in ("PENDING", "RUNNING", "CANCEL_REQUESTED") or novedad.get("sin_vuelta"):
        emitir("equipo", estado="SIN_VUELTA" if novedad.get("sin_vuelta") else estado,
               posicion=novedad.get("posicion"))


def enviar(capability: str, arguments: dict | None = None, request_id: str | None = None,
           plazo: float | None = None, equipo: str | None = None) -> dict:
    """Manda una petición al agente **de la persona de este turno** y espera el resultado.

    Se llama desde el hilo del turno (las herramientas corren fuera del bucle del
    servidor). El trabajo se pasa al bucle donde viven los sockets. Llamarla desde ese
    mismo bucle lo bloquearía entero: se prohíbe.

    Mientras espera (3.4), cuenta a la persona cómo va la orden en su PC y, si pulsa
    «Detener» (`paradas`), pide al agente que la cancele. Una orden nueva de un turno ya
    parado ni sale.
    """
    import concurrent.futures
    import queue

    from src.canal import paradas

    if paradas.parado():
        raise TurnoParado()
    user_id = usuario_actual()
    arguments = arguments or {}
    plazo = PLAZO if plazo is None else plazo

    loop = REGISTRO.loop
    if loop is None:
        # Nunca se ha conectado un agente a este proceso: no hay bucle al que pasar
        # nada. Se espera lo mismo que en el caso normal, por el relevo de instancias.
        limite = time.monotonic() + ESPERA_REAPARICION
        while REGISTRO.loop is None and time.monotonic() < limite:
            time.sleep(0.25)
        loop = REGISTRO.loop
        if loop is None:
            raise AgenteNoConectado()

    if threading.current_thread() is REGISTRO.hilo:
        raise RuntimeError("enviar() no se puede llamar desde el bucle del servidor")

    command_id = str(uuid.uuid4())
    novedades: queue.Queue = queue.Queue()
    futuro = asyncio.run_coroutine_threadsafe(
        enviar_async(user_id, capability, arguments, request_id, plazo, command_id, novedades, equipo), loop
    )
    # El plazo, más la espera a que aparezca, más la de una recuperación tras un corte.
    limite = time.monotonic() + 2 * ESPERA_REAPARICION + plazo + 5
    pedida = False
    while True:
        try:
            return futuro.result(timeout=0.25)
        except concurrent.futures.TimeoutError:
            pass
        while not novedades.empty():
            _contar(novedades.get_nowait())
        if not pedida and paradas.parado():
            pedida = True
            asyncio.run_coroutine_threadsafe(cancelar_async(user_id, command_id), loop)
        if time.monotonic() > limite:
            futuro.cancel()
            raise AgenteSinRespuesta()
