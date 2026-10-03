"""
El agente conectado a la nube (3.0-D): abre el canal y lo mantiene.

Lo decidido con medición en la 3.0-B (docs/mediciones.md §6):

- **WebSocket abierto por el agente**, directo a Render (no a través de Vercel).
- **Latido en los dos sentidos**: la nube manda uno cada 5 s y el agente contesta. Si
  pasan dos sin noticias de la nube (10 s), el agente da la conexión por muerta y
  reconecta. Es lo único que detecta una suspensión (la conexión queda medio abierta)
  y un redespliegue de Render (su proxy la deja colgada sin avisar: medido).
- **Reconexión con backoff exponencial y jitter**, de 1 a 30 s. Al despertar, la red
  de Windows da un par de tirones: el primer fallo no es definitivo.

Y lo que dice cada cierre de la nube:

| Código | Qué significa | Qué hace el agente |
|---|---|---|
| 1012 | La nube se reinicia (despliegue) | Reconecta **en el acto** |
| mensaje `reinicio` | La nube avisa antes de apagarse (el proxy de Render no reenvía el 1012) | Cierra y reconecta **en el acto** |

**El código llega en un mensaje `cierre` antes del cierre de verdad**: medido en Render,
su proxy no reenvía los códigos de cierre de un WebSocket y el agente veía cortes sin
código. Sin ese mensaje, un agente revocado solo se paraba al intentar reconectar, y uno
incompatible reintentaría para siempre.
| 4401 | Credencial mala o equipo revocado | **Para** (`REVOKED`): reintentar no lo arreglaría |
| 4426 | Protocolo incompatible | **Para** (`INCOMPATIBLE`): hay que actualizar |
| 4409 | La cuenta ya tiene otro equipo conectado | Espera y reintenta despacio |
| 4410 | Sustituida por otra conexión suya | Reconecta |
| 4408, otros | Sin latidos, cortes | Reconecta con backoff |

Las órdenes se atienden **de una en una**, en su propia tarea: mientras una se
ejecuta, el canal sigue contestando latidos.

## Desde la 3.4 (protocolo 3)

- **El trabajador de órdenes vive por encima de las conexiones.** Antes, un corte
  cancelaba la tarea que atendía la orden en curso: su respuesta se perdía y la orden
  quedaba sin apuntar (reproducido en la 3.4-A: repetirla la ejecutaba otra vez). Ahora
  sigue, acaba, queda en el diario del PC, y su resultado sale por la conexión que haya
  en ese momento; si no hay ninguna, la nube lo **consulta** al volver.
- **Mensajes nuevos de la nube**: `cancelar` (se atiende al momento, sin esperar en la
  cola) y `consultar` (qué pasó con una orden). Y el agente cuenta cada cambio de estado
  (`estado`: en cola con su posición, en marcha, cancelándose).
- **Tope propio de cola** (`TOPE_COLA`): el agente no se fía de que la nube mande de una
  en una. Con más, `REJECTED` al momento.
"""

import asyncio
import base64
import json
import logging
import platform
import random
import time
from typing import Callable

from src import __version__
import os

from src.agente import auditoria, salud
from src.agente.ejecutor import Ejecutor
from src.agente.estado import EstadoAgente
from src.agente.protocolo import PROTOCOLO_ACTUAL, TROZO_COPIA

logger = logging.getLogger(__name__)

#: Latidos seguidos sin noticias de la nube antes de reconectar. Dos, no tres: en un
#: redespliegue de Render el proxy deja la conexión colgada sin avisar (medido), y
#: cada latido de espera es tiempo sin canal. Con el latido de 5 s, unos 10 s.
LATIDOS_TOLERADOS = 2
LATIDO_POR_DEFECTO = 5.0
ESPERA_MINIMA = 1.0
ESPERA_MAXIMA = 30.0
ESPERA_OTRO_AGENTE = 30.0
#: Lo que se espera la respuesta a un cierre antes de darlo por hecho.
CIERRE_MAXIMO = 2.0

#: Cierres tras los que no tiene sentido reintentar.
DEFINITIVOS = {4401: EstadoAgente.REVOKED, 4426: EstadoAgente.INCOMPATIBLE}


#: Código propio (no viaja): la política cambió y hay que volver a anunciar capacidades.
REANUNCIAR = -1

#: Órdenes esperando en el PC, sin contar la que está en marcha. La nube manda una a la
#: vez (y espera cuatro en su lado): cinco aquí ya es una nube que se porta mal.
TOPE_COLA = 5


def _codigo_de(mensaje: dict) -> int | None:
    codigo = mensaje.get("codigo")
    return codigo if isinstance(codigo, int) else None


def url_del_canal(nube: str) -> str:
    return nube.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/agente/canal"


class Canal:
    """El canal de un agente emparejado. `correr()` no vuelve hasta que se le para."""

    def __init__(
        self,
        nube: str,
        credencial: str,
        ejecutor: Ejecutor,
        al_cambiar: Callable[[EstadoAgente, str], None] | None = None,
        espera_minima: float = ESPERA_MINIMA,
        revisar_capacidades: Callable[[], tuple[float, dict]] | None = None,
        rotar_credencial: Callable[[str], str] | None = None,
    ):
        self.url = url_del_canal(nube)
        self.credencial = credencial
        #: Pide a la nube una credencial nueva y la guarda (3.8). Lo usa el canal cuando la
        #: bienvenida dice que toca rotar.
        self.rotar_credencial = rotar_credencial
        self.ejecutor = ejecutor
        self.al_cambiar = al_cambiar or (lambda estado, detalle: None)
        self.espera_minima = espera_minima
        #: Devuelve (firma de la política, capacidades). Si la firma cambia con el canal
        #: abierto, se recargan, y si cambian las que se anunciaron se reconecta para
        #: volver a anunciarlas (3.0-E).
        self.revisar_capacidades = revisar_capacidades
        self._firma: float | None = None
        self.estado = EstadoAgente.PAIRED
        self._parar = asyncio.Event()
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        #: De la conexión en curso: si llegó a READY, y por qué se cortó (para el registro).
        self._estuvo_listo = False
        self._motivo = ""
        #: Cuándo llegó el último latido de la nube (para la salud, 3.6).
        self._ultimo_latido: float | None = None
        #: La cola y su trabajador, que sobreviven a los cortes (3.4).
        self._cola: asyncio.Queue | None = None
        self._trabajo: asyncio.Task | None = None
        self._en_marcha = False
        #: La conexión por la que se mandan resultados: solo **después** de la
        #: bienvenida. La nube espera el saludo como primer mensaje, y un resultado que
        #: acabó durante el corte no puede colarse antes.
        self._lista = None
        self.ejecutor.avisar = self._contar

    def _pasar_a(self, estado: EstadoAgente, detalle: str = "") -> None:
        self.estado = estado
        salud.apuntar(estado=estado.value, desde=time.time())
        logger.info("Agente: %s %s", estado.value, detalle)
        self.al_cambiar(estado, detalle)

    def parar(self) -> None:
        """Para el canal. Se puede llamar desde otro hilo (Ctrl+C, pruebas)."""
        if self._loop is None:
            self._parar.set()
            return

        def _ahora():
            self._parar.set()
            if self._ws is not None:
                asyncio.ensure_future(self._ws.close(code=1000, reason="agente parado"))

        try:
            self._loop.call_soon_threadsafe(_ahora)
        except RuntimeError:
            # El bucle ya se cerró: el canal ya está parado. Pararlo dos veces no puede
            # reventar (lo encontró el estrés de la 3.4.5).
            self._parar.set()

    async def correr(self) -> EstadoAgente:
        """Conecta y reconecta hasta que se le para o la nube dice que no vale la pena."""
        self._loop = asyncio.get_running_loop()
        self._cola = asyncio.Queue()
        self._trabajo = asyncio.create_task(self._trabajar())
        # Y su padre: con un entorno virtual, `pythonw.exe` es un lanzador que arranca el
        # Python de verdad, y el vigilante solo conoce al lanzador (medido en producción).
        salud.apuntar(pid=os.getpid(), ppid=os.getppid(), arrancado=time.time(), estado=self.estado.value,
                      version=__version__)
        pulso = asyncio.create_task(self._pulso())
        try:
            return await self._conectar_siempre()
        finally:
            self._trabajo.cancel()
            pulso.cancel()

    async def _pulso(self) -> None:
        """Cada 5 s, que el bucle sigue vivo (3.6). Si deja de latir, el vigilante lo ve:
        un agente colgado no puede apuntarlo, y eso es justo lo que se mide."""
        while True:
            salud.apuntar(pulso=time.time(), ultimo_latido=self._ultimo_latido,
                          cola=self._cola.qsize() if self._cola else 0, en_marcha=self._en_marcha)
            await asyncio.sleep(salud.PULSO)

    async def _conectar_siempre(self) -> EstadoAgente:
        espera = self.espera_minima
        while not self._parar.is_set():
            self._estuvo_listo, self._motivo = False, ""
            codigo = await self._una_conexion()
            if self._estuvo_listo:
                # Un corte tras una conexión buena empieza de cero. Medido en mi PC
                # (3.0.5): la espera seguía creciendo entre cortes separados por
                # horas —1,1 → 2,3 → 4,2 → 10,3 → 18,9 → 44,6 s—, y tras unos cuantos
                # cortes normales cada uno dejaba a Morgan ~45 s sin su PC.
                espera = self.espera_minima
            if codigo in DEFINITIVOS:
                self._pasar_a(DEFINITIVOS[codigo], f"la nube cerró con {codigo}")
                return self.estado
            if self._parar.is_set():
                break
            if codigo in (1012, 4410, REANUNCIAR):
                pausa = random.uniform(0, 0.5)       # se muda en el acto
                espera = self.espera_minima
            elif codigo == 4409:
                pausa = ESPERA_OTRO_AGENTE
            else:
                pausa = espera + random.uniform(0, espera / 2)
                espera = min(espera * 2, ESPERA_MAXIMA)
            motivo = f" ({self._motivo})" if self._motivo else ""
            self._pasar_a(EstadoAgente.RECONNECTING, f"cierre {codigo}{motivo}; vuelve en {pausa:.1f} s")
            try:
                await asyncio.wait_for(self._parar.wait(), timeout=pausa)
            except asyncio.TimeoutError:
                pass
        self._pasar_a(EstadoAgente.DISCONNECTED, "parado")
        return self.estado

    async def _rotar(self, ws) -> None:
        """La credencial tiene más de 90 días (3.8): pedir una nueva, guardarla y
        reconectar con ella. Al conectar con la nueva, la nube la estrena y la vieja deja de
        valer. Si algo falla por el camino, se sigue con la actual: vale hasta ese momento."""
        try:
            nueva = await asyncio.to_thread(self.rotar_credencial, self.credencial)
        except Exception:
            logger.warning("No se pudo rotar la credencial: se sigue con la actual", exc_info=True)
            return
        self.credencial = nueva
        auditoria.anotar("credencial_rotada")
        try:
            await ws.close(code=1000, reason="credencial rotada")
        except Exception:
            pass

    async def _una_conexion(self) -> int | None:
        """Una conexión de principio a fin. Devuelve el código de cierre (None: corte)."""
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        self._pasar_a(EstadoAgente.CONNECTING)
        if self.revisar_capacidades is not None:
            self._firma, capacidades = self.revisar_capacidades()
            self.ejecutor.reemplazar(capacidades)
        try:
            async with connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {self.credencial}"},
                ping_interval=None,       # el latido es de la aplicación, en los dos sentidos
                open_timeout=30,
                # Al cerrar una conexión medio muerta, la respuesta al cierre no llega:
                # por defecto se esperaba 10 s más antes de reconectar.
                close_timeout=CIERRE_MAXIMO,
                max_size=2 * 2**20,
            ) as ws:
                self._ws = ws
                self._pasar_a(EstadoAgente.AUTHENTICATING)
                await ws.send(json.dumps({
                    "tipo": "saludo",
                    "protocol_version": PROTOCOLO_ACTUAL,
                    "agent_version": __version__,
                    "sistema": f"{platform.system()} {platform.release()}".strip(),
                    "capacidades": self.ejecutor.nombres(),
                }))
                bienvenida = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if bienvenida.get("tipo") == "cierre":
                    return _codigo_de(bienvenida)
                if bienvenida.get("tipo") != "bienvenida":
                    return None
                latido = float(bienvenida.get("latido") or LATIDO_POR_DEFECTO)
                self.ejecutor.observar_hora_nube(bienvenida.get("hora"), nueva_conexion=True)
                self._pasar_a(EstadoAgente.CONNECTED, bienvenida.get("compatibilidad", ""))
                # READY: conectado, compatible y con la política cargada (las
                # capacidades anunciadas salen de ella). Sin carpetas permitidas,
                # solo se anuncian `estado` y `system_info`.
                self._pasar_a(EstadoAgente.READY, ",".join(self.ejecutor.nombres()))
                self._estuvo_listo = True
                self._lista = ws
                auditoria.anotar("conectado", compatibilidad=bienvenida.get("compatibilidad"))
                if bienvenida.get("rotar_credencial") and self.rotar_credencial is not None:
                    asyncio.create_task(self._rotar(ws))
                return await self._atender(ws, latido)
        except ConnectionClosed as exc:
            if not exc.rcvd:
                self._motivo = "la conexión se cortó sin aviso"
            return exc.rcvd.code if exc.rcvd else None
        except InvalidStatus as exc:
            # La nube cierra antes de aceptar (credencial mala): el cliente lo ve como
            # un rechazo del saludo HTTP. Se trata como el 4401 que es.
            logger.warning("La nube rechazó la conexión: %s", exc)
            return 4401 if getattr(exc.response, "status_code", 0) in (401, 403) else None
        except (OSError, asyncio.TimeoutError) as exc:
            logger.info("Sin conexión con la nube: %s", type(exc).__name__)
            self._motivo = f"sin red: {type(exc).__name__}"
            return None

    async def _enviar_archivo(self, ws, command_id: str, ruta: str) -> None:
        """El archivo en trozos de `TROZO_COPIA`, en base64 (3.1-E).

        Se lee desde un hilo: un archivo de 20 MB en el bucle dejaría sin contestar los
        latidos, y la nube daría el canal por muerto.
        """
        indice = 0
        with open(ruta, "rb") as f:
            while True:
                trozo = await asyncio.to_thread(f.read, TROZO_COPIA)
                if not trozo:
                    break
                await ws.send(json.dumps({
                    "tipo": "fragmento",
                    "command_id": command_id,
                    "indice": indice,
                    "datos": base64.b64encode(trozo).decode("ascii"),
                }))
                indice += 1

    def _cambio_la_politica(self) -> bool:
        """Si la política cambió y con ella lo que este agente puede anunciar."""
        if self.revisar_capacidades is None:
            return False
        firma, capacidades = self.revisar_capacidades()
        if firma == self._firma:
            return False
        self._firma = firma
        antes = set(self.ejecutor.nombres())
        self.ejecutor.reemplazar(capacidades)
        return set(self.ejecutor.nombres()) != antes

    async def _mandar(self, mensaje: dict) -> bool:
        """Por la conexión que haya ahora. Sin conexión no se pierde nada: el diario lo
        tiene y la nube lo consulta al volver."""
        ws = self._lista
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(mensaje))
            return True
        except Exception:
            return False

    async def _contar(self, mensaje: dict) -> None:
        await self._mandar(mensaje)

    async def _trabajar(self) -> None:
        # De una en una, en orden: sin concurrencia accidental (§12 del plan).
        while True:
            orden, recibida = await self._cola.get()
            self._en_marcha = True
            try:
                respuesta = await self.ejecutor.procesar(orden, recibida)
            finally:
                self._en_marcha = False
            archivo = respuesta.pop("_archivo", None)
            if archivo and self._lista is not None:
                # Una copia para descargar (3.1-E): el archivo va **antes** que el
                # resultado, en trozos, porque un mensaje no puede con 20 MB. El
                # resultado cierra la espera de la nube, así que va el último.
                try:
                    await self._enviar_archivo(self._lista, respuesta["command_id"], archivo)
                except Exception:
                    continue        # la conexión cayó a mitad: la nube repetirá la copia
            await self._mandar(respuesta)

    async def _recibir_orden(self, mensaje: dict) -> None:
        conocida = self.ejecutor.conocida(str(mensaje.get("command_id") or ""))
        if conocida is not None:
            # Ya está en cola, en marcha o hecha: no se encola otra vez (3.4).
            await self._mandar(conocida)
            return
        if self._cola.qsize() >= TOPE_COLA:
            await self._mandar(self.ejecutor._rechazo(mensaje, "ocupado", time.monotonic()))
            return
        await self.ejecutor.en_cola(mensaje, self._cola.qsize() + (2 if self._en_marcha else 1))
        await self._cola.put((mensaje, time.monotonic()))

    async def _atender(self, ws, latido: float) -> int | None:
        try:
            while not self._parar.is_set():
                try:
                    texto = await asyncio.wait_for(ws.recv(), timeout=LATIDOS_TOLERADOS * latido)
                except asyncio.TimeoutError:
                    # Dos latidos sin noticias: la conexión está medio muerta (una
                    # suspensión, un corte que nadie avisó). Se cierra y se reconecta.
                    self._motivo = "sin latidos de la nube"
                    await ws.close(code=4408, reason="sin latidos de la nube")
                    return
                mensaje = json.loads(texto)
                tipo = mensaje.get("tipo")
                if tipo == "latido":
                    self.ejecutor.observar_hora_nube(mensaje.get("hora"))
                    self._ultimo_latido = time.time()
                    await ws.send(json.dumps({"tipo": "pong"}))
                elif tipo == "cierre":
                    return _codigo_de(mensaje)
                elif tipo == "reinicio":
                    # La nube se apaga (un despliegue): mudarse ya a la instancia
                    # nueva, que en Render ya atiende. Se trata como un 1012.
                    await ws.close(code=1000, reason="la nube se reinicia")
                    return 1012
                elif tipo == "orden":
                    await self._recibir_orden(mensaje)
                elif tipo == "cancelar":
                    # Al momento, sin pasar por la cola: la orden que hay que parar es
                    # justo la que la ocupa.
                    await self._mandar(self.ejecutor.cancelar(
                        str(mensaje.get("command_id") or ""), str(mensaje.get("motivo") or "la_persona")[:40]))
                elif tipo == "consultar":
                    await self._mandar(self.ejecutor.consultar(str(mensaje.get("command_id") or "")))
                if self._trabajo is not None and self._trabajo.done():
                    self._trabajo.result()
                if self._cambio_la_politica():
                    await ws.close(code=1000, reason="capacidades cambiadas")
                    return REANUNCIAR
            await ws.close(code=1000, reason="agente parado")
        finally:
            # La orden en marcha NO se cancela (3.4): sigue, y su resultado sale por la
            # próxima conexión o lo consulta la nube.
            if self._lista is ws:
                self._lista = None
