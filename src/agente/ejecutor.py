"""
Lo que hace el agente con cada orden que llega (3.0-D), con su motor de ejecución (3.4).

**El agente no confía en la nube** (§3 del plan). Antes de ejecutar nada comprueba,
él mismo, aunque la nube ya lo haya comprobado:

1. Que la orden es **para él**: su `agent_id`. Una con otro se rechaza y se anota
   como anomalía (una nube comprometida mandando a quien no toca).
2. Que el **protocolo** es uno que habla.
3. Que **no ha vencido**: `vence_en_ms` lo mide con su propio reloj desde que la
   recibe (el reloj del PC puede no coincidir con el de la nube).
4. Que la **capacidad existe y está habilitada** en este agente.
5. Que los argumentos son un objeto.

## El motor de ejecución (3.4)

Cada orden pasa por los estados del plan —`PENDING → RUNNING → COMPLETED | FAILED`, y
`CANCEL_REQUESTED → CANCELLED`— y **cada paso queda en el diario del PC**
(`diario.py`), en disco. Lo que arregla, reproducido antes de cambiar nada (3.4-A):

- **El plazo ya no abandona la operación: la cancela.** Antes, al vencer, el ejecutor
  dejaba de esperar pero la capacidad seguía en su hilo: la nube recibía «no terminó a
  tiempo» y el archivo se escribía igual 1,5 s después. Ahora, poco antes de vencer, se
  le pide parar (`control.py`) y se espera a que lo haga en su siguiente punto seguro. Si
  ya había pasado el punto sin vuelta, termina, y se dice. Y si aun así no acaba, se
  contesta **la verdad** —«sigue en marcha»— y el diario apunta cómo acabó.
- **Un corte no hace olvidar la orden.** Antes, con la conexión cortada a mitad, la
  orden no quedaba apuntada, y repetirla con el mismo `command_id` la ejecutaba otra
  vez. Ahora está en el diario desde que llega, y la nube puede **consultarla**.
- **Se puede cancelar** (`cancelar`): una orden en cola no llega a empezar; una en marcha
  para en su siguiente punto seguro.

**Una orden a la vez**: el canal las pasa por aquí en orden, sin concurrencia.
"""

import asyncio
import concurrent.futures
import contextvars
import platform
import time
from collections import OrderedDict
from typing import Awaitable, Callable

from src import __version__
from src.agente import auditoria, aviso, control, salida
from src.agente.diario import (CANCEL_REQUESTED, CANCELLED, COMPLETED, FAILED, PENDING, REJECTED,
                               REPETIBLES, RUNNING, TERMINALES, Diario)
from src.agente.protocolo import PROTOCOLO_ACTUAL, PROTOCOLO_MINIMO

#: Respuestas completas recordadas en memoria (incluidas las lecturas, que el diario no
#: guarda): una orden repetida mientras el agente vive se contesta con ellas.
RECORDADAS = 256

#: Cuánto antes de vencer se le pide parar a la operación, para que dé tiempo a
#: contestar antes de que la nube deje de esperar.
MARGEN_RESPUESTA = 2.0
#: Lo que se espera, tras pedirle parar, a que lo haga.
GRACIA = 1.5

#: Margen al comparar la edad de una orden con su plazo: la diferencia de relojes se
#: estima con la latencia de un mensaje, que en mi red son ~120 ms.
TOLERANCIA_RELOJ = 0.5


def _respuesta(command_id: str, estado: str, resultado: dict, **extra) -> dict:
    return {"tipo": "resultado", "command_id": command_id, "estado": estado,
            "resultado": resultado, **extra}


def _fallo(error: str, motivo: str | None = None) -> dict:
    r = {"success": False, "data": None, "error": error}
    if motivo:
        r["motivo"] = motivo
    return r


class Ejecutor:
    def __init__(self, agent_id: str, capacidades: dict[str, Callable[..., dict]] | None = None,
                 avisar: Callable[[dict], Awaitable[None]] | None = None):
        self.agent_id = agent_id
        self.capacidades: dict[str, Callable[..., dict]] = {**self._fijas()}
        self.capacidades.update(capacidades or {})
        self._vistas: "OrderedDict[str, dict]" = OrderedDict()
        self.diario = Diario()
        #: command_id → control de las órdenes que están en cola o en marcha AQUÍ.
        self._controles: dict[str, control.Control] = {}
        #: Las que están corriendo ahora mismo (no las que esperan en la cola).
        self._corriendo: set[str] = set()
        #: Cómo se le cuenta a la nube un cambio de estado (lo pone el canal).
        self.avisar = avisar
        #: Cuánto va el reloj de la nube por delante del de este PC, estimado con su hora
        #: en el saludo y en cada latido (3.6). None hasta saberlo.
        self.desfase: float | None = None
        #: Los hilos de las capacidades. Un futuro de hilo, no de asyncio: avisa al acabar
        #: aunque el bucle ya no exista, y así el diario sabe siempre cómo terminó.
        self._hilos = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="capacidad")

    def observar_hora_nube(self, hora, nueva_conexion: bool = False) -> None:
        """La hora de la nube en un mensaje recién llegado (3.6).

        Cada mensaje da una estimación de la diferencia entre los dos relojes, **de menos**
        por lo que tardó en llegar. Se queda la mayor de la conexión, la del mensaje que
        menos tardó: si se tomara la última, los latidos retenidos en una conexión medio
        abierta, que llegan de golpe y tarde, harían creer que las órdenes son más jóvenes
        de lo que son. Con cada conexión se empieza de nuevo (el reloj del PC pudo cambiar).
        """
        if not isinstance(hora, (int, float)):
            return
        estimada = hora - time.time()
        if nueva_conexion or self.desfase is None:
            self.desfase = estimada
        else:
            self.desfase = max(self.desfase, estimada)

    def nombres(self) -> list[str]:
        return sorted(self.capacidades)

    def reemplazar(self, capacidades: dict[str, Callable[..., dict]]) -> None:
        """Las capacidades cambiaron (la política local): las fijas se quedan siempre."""
        self.capacidades = {**self._fijas(), **capacidades}

    def _fijas(self) -> dict[str, Callable[..., dict]]:
        """Las que no dependen de la política: `estado`, y desde la 4.17 `abrir_ajustes`, que
        solo **abre** la ventana «Morgan en tu PC» (lo pide la web con un botón). No cambia
        nada: la política la cambia la persona, en esa ventana."""
        return {"estado": self._estado, "abrir_ajustes": self._abrir_ajustes}

    @staticmethod
    def _abrir_ajustes(**_) -> dict:
        from src.agente.ajustes import abrir_en_segundo_plano

        if abrir_en_segundo_plano():
            return {"success": True, "data": {"abierta": True}, "error": None}
        return {"success": False, "data": None, "error": "No se pudo abrir la ventana de ajustes en el PC."}

    def _estado(self) -> dict:
        return {
            "success": True,
            "data": {
                "agent_id": self.agent_id,
                "agent_version": __version__,
                "protocol_version": PROTOCOLO_ACTUAL,
                "sistema": f"{platform.system()} {platform.release()}".strip(),
                "capacidades": self.nombres(),
            },
            "error": None,
        }

    async def _contar(self, command_id: str, estado: str, **extra) -> None:
        if self.avisar is not None and command_id:
            try:
                await self.avisar({"tipo": "estado", "command_id": command_id, "estado": estado, **extra})
            except Exception:
                pass            # sin canal ahora: el diario lo tiene, la nube lo consultará

    # --- La cola, vista desde el canal ---

    def conocida(self, command_id: str) -> dict | None:
        """Qué contestar si una orden que llega ya se conoce (en cola, en marcha o hecha),
        en vez de encolarla otra vez. None: es nueva."""
        if not command_id:
            return None
        repetida = self._repetida(command_id)
        if repetida is not None:
            return repetida
        entrada = self.diario.de(command_id)
        if entrada is not None and command_id in self._controles:
            return {"tipo": "estado", "command_id": command_id, "estado": entrada["estado"]}
        return None

    async def en_cola(self, orden: dict, posicion: int) -> None:
        """La orden entró en la cola del canal: PENDING, y se le cuenta a la nube."""
        command_id = str(orden.get("command_id") or "")
        if not command_id or command_id in self._controles or self.diario.de(command_id):
            return
        self._controles[command_id] = control.Control()
        self.diario.nueva(command_id, orden)
        await self._contar(command_id, PENDING, posicion=posicion)

    def cancelar(self, command_id: str, motivo: str = "la_persona") -> dict:
        """Pide parar una orden. Devuelve su estado, para contárselo a la nube."""
        entrada = self.diario.de(command_id)
        ctrl = self._controles.get(command_id)
        if entrada is None or ctrl is None or entrada["estado"] in TERMINALES:
            return {"tipo": "estado", "command_id": command_id,
                    "estado": entrada["estado"] if entrada else "DESCONOCIDA"}
        parara = ctrl.pedir(motivo)
        auditoria.anotar("cancelacion_pedida", {"command_id": command_id,
                                                "capability": entrada.get("capability")},
                         motivo=motivo, parara=parara)
        if parara:
            self.diario.pasar(command_id, CANCEL_REQUESTED, motivo=motivo)
            return {"tipo": "estado", "command_id": command_id, "estado": CANCEL_REQUESTED}
        # Ya pasó el punto sin vuelta: termina, y se dice (decisión mía).
        return {"tipo": "estado", "command_id": command_id, "estado": RUNNING, "sin_vuelta": True}

    def cancelar_todas(self, motivo: str) -> int:
        """Pide parar todas las que estén en cola o en marcha (al parar o pausar el agente,
        5.1). Devuelve a cuántas se les pidió. Se llama desde el hilo que vigila la parada."""
        pedidas = 0
        for command_id in list(self._controles):
            if self.cancelar(command_id, motivo).get("estado") == CANCEL_REQUESTED:
                pedidas += 1
        return pedidas

    def consultar(self, command_id: str) -> dict:
        """Qué pasó con una orden: lo que la nube pregunta tras un corte (3.4)."""
        entrada = self.diario.de(command_id)
        if entrada is None:
            return {"tipo": "consulta", "command_id": command_id, "estado": "DESCONOCIDA"}
        respuesta = self._vistas.get(command_id)
        if respuesta is None and "resultado" in entrada:
            respuesta = _respuesta(command_id, entrada["estado"], entrada["resultado"])
        return {"tipo": "consulta", "command_id": command_id, "estado": entrada["estado"],
                "respuesta": respuesta,
                "repetible": entrada.get("capability") in REPETIBLES and respuesta is None}

    # --- Ejecutar ---

    def _rechazo(self, orden: dict, motivo: str, recibida: float) -> dict:
        auditoria.anotar("rechazada", orden, motivo=motivo)
        command_id = str(orden.get("command_id") or "")
        if command_id and self.diario.de(command_id):
            self.diario.pasar(command_id, REJECTED, motivo=motivo)
        self._controles.pop(command_id, None)
        return {
            **_respuesta(orden.get("command_id"), REJECTED,
                         _fallo(f"El agente la rechazó: {motivo}")),
            "motivo": motivo,
            "tiempos": {"cola_ms": 0, "ejecucion_ms": round((time.monotonic() - recibida) * 1000, 1)},
        }

    def _repetida(self, command_id: str) -> dict | None:
        """La respuesta de una orden que ya se atendió, sin volver a hacerla."""
        if command_id in self._vistas:
            return {**self._vistas[command_id], "repetida": True}
        entrada = self.diario.de(command_id)
        if entrada is None:
            return None
        if entrada["estado"] not in TERMINALES:
            if command_id in self._corriendo:
                # Todavía en marcha aquí: contestará ella. A la nube, cómo va.
                return {"tipo": "estado", "command_id": command_id, "estado": entrada["estado"]}
            return None         # en la cola, esperando su turno: es este
        if "resultado" in entrada:
            return {**_respuesta(command_id, entrada["estado"], entrada["resultado"]), "repetida": True}
        if entrada["estado"] in (CANCELLED, REJECTED) or entrada.get("capability") not in REPETIBLES:
            # Nunca se rehace una cancelada, ni una que cambia cosas y cuyo resultado se
            # perdió: se dice cómo acabó.
            return {**_respuesta(command_id, entrada["estado"], _fallo(
                f"Esa orden ya se atendió ({entrada['estado']}).", entrada.get("motivo"))), "repetida": True}
        return None     # una lectura ya hecha, sin su resultado: repetirla no hace daño

    async def procesar(self, orden: dict, recibida: float | None = None) -> dict:
        """La respuesta para esta orden. Nunca lanza: todo acaba en una respuesta."""
        recibida = time.monotonic() if recibida is None else recibida
        command_id = str(orden.get("command_id") or "")
        auditoria.anotar("recibida", orden)

        if command_id:
            repetida = self._repetida(command_id)
            if repetida is not None:
                auditoria.anotar("repetida", orden)
                return repetida

        if orden.get("agent_id") != self.agent_id:
            return self._rechazo(orden, "otro_agente", recibida)
        # Cualquier versión que este agente sepa hablar: la nube le habla en la que
        # negociaron al saludar, que puede ser más vieja que la suya (3.1-E).
        version = orden.get("protocol_version")
        if not isinstance(version, int) or not PROTOCOLO_MINIMO <= version <= PROTOCOLO_ACTUAL:
            return self._rechazo(orden, "protocolo", recibida)
        vence = orden.get("vence_en_ms")
        if not isinstance(vence, (int, float)):
            return self._rechazo(orden, "vencida", recibida)
        # Un plazo de cero o negativo ya está vencido: lo rechaza la comprobación de
        # abajo, con el reloj del PC.
        limite = recibida + vence / 1000
        # Y lo que tardó en llegar (3.6). Reproducido con una conexión medio abierta: la
        # nube se rindió a los 3 s y le dijo a la persona que su PC no respondía; la orden,
        # retenida, llegó después y se ejecutaba como nueva a los 4,5 s. Una petición
        # fantasma. Con su hora de salida, se sabe cuánto lleva viajando.
        enviada = orden.get("enviada_en")
        if isinstance(enviada, (int, float)) and self.desfase is not None:
            edad = time.time() + self.desfase - enviada
            if edad > vence / 1000 + TOLERANCIA_RELOJ:
                return self._rechazo(orden, "vencida_en_camino", recibida)
            limite = min(limite, recibida + max(0.0, vence / 1000 - max(0.0, edad)))
        capacidad = str(orden.get("capability"))
        funcion = self.capacidades.get(capacidad)
        if funcion is None:
            return self._rechazo(orden, "capacidad_no_disponible", recibida)
        argumentos = orden.get("arguments")
        if not isinstance(argumentos, dict):
            return self._rechazo(orden, "argumentos", recibida)
        if time.monotonic() >= limite:
            return self._rechazo(orden, "vencida", recibida)

        ctrl = self._controles.setdefault(command_id, control.Control()) if command_id else control.Control()
        if command_id and self.diario.de(command_id) is None:
            self.diario.nueva(command_id, orden)
        return await self._ejecutar(orden, command_id, capacidad, funcion, argumentos,
                                    recibida, limite, ctrl)

    async def _ejecutar(self, orden, command_id, capacidad, funcion, argumentos,
                        recibida, limite, ctrl) -> dict:
        empieza = time.monotonic()
        if command_id:
            self._corriendo.add(command_id)
        try:
            return await self._ejecutar_de_verdad(orden, command_id, capacidad, funcion, argumentos,
                                                  recibida, limite, ctrl, empieza)
        finally:
            self._corriendo.discard(command_id)

    async def _ejecutar_de_verdad(self, orden, command_id, capacidad, funcion, argumentos,
                                  recibida, limite, ctrl, empieza) -> dict:
        if ctrl.pedida.is_set():
            # Cancelada mientras esperaba en la cola: ni empieza.
            resultado, estado = _fallo(f"Cancelada antes de empezar ({ctrl.motivo}).", "cancelada"), CANCELLED
            self._controles.pop(command_id, None)
            return self._cerrar(orden, command_id, capacidad, estado, resultado, recibida, empieza, {})

        if command_id:
            self.diario.pasar(command_id, RUNNING)
        await self._contar(command_id, RUNNING)

        # Para que la capacidad sepa cuándo vence la orden (la notificación, 3.3) y si
        # alguien le pidió parar (3.4). `to_thread` lleva las dos al hilo.
        aviso.VENCE.set(limite)
        control.ACTUAL.set(ctrl)
        de_hilo = self._hilos.submit(contextvars.copy_context().run, lambda: funcion(**argumentos))
        hilo = asyncio.wrap_future(de_hilo)

        try:
            hechos, _ = await asyncio.wait({hilo}, timeout=max(0.01, limite - MARGEN_RESPUESTA - time.monotonic()))
            if not hechos:
                ctrl.pedir("vencida")
                hechos, _ = await asyncio.wait({hilo}, timeout=GRACIA)
        except asyncio.CancelledError:
            # Quien esperaba se fue (antes de la 3.4, el canal al cortarse). La operación
            # sigue en su hilo: su control y su diario siguen vivos hasta que acabe, y
            # una orden repetida no la vuelve a lanzar (reproducido en la 3.4-A).
            de_hilo.add_done_callback(lambda f: self._acabo_tarde(orden, command_id, capacidad, f))
            raise
        if not hechos:
            # No paró a tiempo (una capacidad sin puntos seguros): se dice la verdad, y
            # el diario apuntará cómo acabó cuando acabe.
            de_hilo.add_done_callback(lambda f: self._acabo_tarde(orden, command_id, capacidad, f))
            resultado = _fallo("Sigue en marcha en el PC: no terminó en su plazo. Se puede "
                               "preguntar después si acabó.", "sigue_en_marcha")
            respuesta = _respuesta(command_id, RUNNING, resultado,
                                   tiempos=self._tiempos(recibida, empieza))
            auditoria.anotar("ejecutada", orden, estado=RUNNING, motivo="sigue_en_marcha")
            return respuesta

        self._controles.pop(command_id, None)
        if not hilo.cancelled():
            hilo.exception()    # recogida: si no, asyncio la escribe en el registro como perdida
        resultado, estado = self._resultado_de(de_hilo, ctrl)
        notas = {}
        adjunto = resultado.pop("_archivo", None) if isinstance(resultado, dict) else None
        # Lo que una escritura deja para la auditoría del PC (3.3): qué contestó la
        # persona y las huellas. Se anota aquí y no sale.
        if isinstance(resultado, dict):
            notas = resultado.pop("_auditoria", None) or {}
            # La frontera de salida (3.1-A): todo resultado pasa por aquí antes de salir
            # del PC. Tapa secretos en lo leído y corta lo que pase del tope.
            resultado, del_filtro = salida.filtrar(capacidad, resultado)
            notas.update(del_filtro)
            if estado == COMPLETED and not resultado.get("success"):
                estado = FAILED
        respuesta = self._cerrar(orden, command_id, capacidad, estado, resultado, recibida, empieza, notas)
        # Una copia para descargar no viaja dentro del resultado (3.1-E): la capacidad
        # deja aquí la ruta y el canal la manda en trozos antes del resultado.
        if adjunto and estado == COMPLETED:
            respuesta = {**respuesta, "_archivo": adjunto}
        return respuesta

    @staticmethod
    def _resultado_de(hilo, ctrl) -> tuple[dict, str]:
        try:
            resultado = hilo.result()
        except control.Cancelada as exc:
            quien = "la persona" if exc.motivo == "la_persona" else exc.motivo
            return _fallo(f"Cancelada ({quien}). No se ha cambiado nada.", "cancelada"), CANCELLED
        except TypeError:
            return _fallo("Argumentos que esta capacidad no admite."), FAILED
        except Exception as exc:
            # Un error seguro: el tipo, no la traza (que puede llevar rutas del PC).
            return _fallo(f"Falló en el agente ({type(exc).__name__})."), FAILED
        if not isinstance(resultado, dict):
            return _fallo("La capacidad no devolvió un resultado."), FAILED
        estado = COMPLETED if resultado.get("success") else FAILED
        if ctrl.pedida.is_set() and estado == COMPLETED:
            # Se pidió parar cuando ya había pasado el punto sin vuelta: está hecho, y se
            # dice (decisión mía: «se deja y se dice»).
            datos = resultado.get("data")
            aviso_tarde = "La cancelación llegó cuando ya estaba hecho: se hizo."
            resultado = {**resultado, "data": {**datos, "cancelacion": aviso_tarde}
                         if isinstance(datos, dict) else datos}
        return resultado, estado

    @staticmethod
    def _tiempos(recibida: float, empieza: float) -> dict:
        return {"cola_ms": round((empieza - recibida) * 1000, 1),
                "ejecucion_ms": round((time.monotonic() - empieza) * 1000, 1)}

    def _cerrar(self, orden, command_id, capacidad, estado, resultado, recibida, empieza, notas) -> dict:
        respuesta = _respuesta(command_id, estado, resultado, tiempos=self._tiempos(recibida, empieza))
        auditoria.anotar("ejecutada", orden, estado=estado, ms=respuesta["tiempos"]["ejecucion_ms"], **notas)
        if command_id:
            self.diario.pasar(command_id, estado, resultado=resultado)
            self._vistas[command_id] = respuesta
            while len(self._vistas) > RECORDADAS:
                self._vistas.popitem(last=False)
        return respuesta

    def _acabo_tarde(self, orden, command_id, capacidad, hilo) -> None:
        """Una operación que siguió después de contestar: el diario apunta cómo acabó."""
        self._controles.pop(command_id, None)
        resultado, estado = self._resultado_de(hilo, control.Control())
        if isinstance(resultado, dict):
            resultado.pop("_archivo", None)
            notas = resultado.pop("_auditoria", None) or {}
            resultado, _ = salida.filtrar(capacidad, resultado)
        else:
            notas = {}
        auditoria.anotar("acabo_tarde", orden, estado=estado, **notas)
        if command_id:
            self.diario.pasar(command_id, estado, resultado=resultado)
