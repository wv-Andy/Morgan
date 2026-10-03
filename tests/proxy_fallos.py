"""
Un proxy TCP entre el agente y la nube que falla a voluntad (3.6).

Para medir la resiliencia sin apagar el Wi-Fi ni suspender mi PC: se pone en
medio de la conexión del agente real con la nube real y puede

- `cortar()`: cerrar todas las conexiones por los dos lados (como quitar la red);
- `retener(sentido)`: dejar de pasar bytes **sin cerrar nada** y guardarlos (como una
  conexión medio abierta: un PC suspendido, un router que se cuelga);
- `soltar()`: pasar de golpe lo retenido y seguir con normalidad (el PC que despierta).

`sentido`: "nube→pc", "pc→nube" o "ambos".
"""

import asyncio
import threading


class ProxyDeFallos:
    def __init__(self, destino_puerto: int, destino_host: str = "127.0.0.1"):
        self.destino = (destino_host, destino_puerto)
        self.puerto: int | None = None
        self.retenido = {"nube→pc": False, "pc→nube": False}
        self._colas: list = []           # (escritor, datos) retenidos, en orden
        self._conexiones: list = []
        self._bucle = asyncio.new_event_loop()
        self._listo = threading.Event()
        threading.Thread(target=self._correr, daemon=True).start()
        self._listo.wait(10)

    # --- Desde el hilo de la prueba ---

    def _en_bucle(self, funcion):
        return asyncio.run_coroutine_threadsafe(funcion(), self._bucle).result(10)

    def cortar(self) -> None:
        async def hacer():
            for lector_escritores in list(self._conexiones):
                for escritor in lector_escritores:
                    escritor.transport.abort()
            self._conexiones.clear()
        self._en_bucle(hacer)

    def retener(self, sentido: str = "ambos") -> None:
        for s in (("nube→pc", "pc→nube") if sentido == "ambos" else (sentido,)):
            self.retenido[s] = True

    def soltar(self) -> None:
        async def hacer():
            self.retenido = {"nube→pc": False, "pc→nube": False}
            pendientes, self._colas = self._colas, []
            for escritor, datos in pendientes:
                try:
                    escritor.write(datos)
                    await escritor.drain()
                except Exception:
                    pass
        self._en_bucle(hacer)

    def cerrar(self) -> None:
        self.cortar()
        self._bucle.call_soon_threadsafe(self._bucle.stop)

    # --- En su bucle ---

    def _correr(self) -> None:
        asyncio.set_event_loop(self._bucle)

        async def arrancar():
            servidor = await asyncio.start_server(self._atender, "127.0.0.1", 0)
            self.puerto = servidor.sockets[0].getsockname()[1]
            self._listo.set()

        self._bucle.run_until_complete(arrancar())
        self._bucle.run_forever()

    async def _atender(self, lector_pc, escritor_pc) -> None:
        try:
            lector_nube, escritor_nube = await asyncio.open_connection(*self.destino)
        except OSError:
            escritor_pc.close()
            return
        self._conexiones.append((escritor_pc, escritor_nube))

        async def pasar(lector, escritor, sentido):
            try:
                while True:
                    datos = await lector.read(65536)
                    if not datos:
                        break
                    if self.retenido[sentido]:
                        self._colas.append((escritor, datos))
                        continue
                    escritor.write(datos)
                    await escritor.drain()
            except Exception:
                pass
            finally:
                if not self.retenido[sentido]:
                    try:
                        escritor.close()
                    except Exception:
                        pass

        await asyncio.gather(pasar(lector_pc, escritor_nube, "pc→nube"),
                             pasar(lector_nube, escritor_pc, "nube→pc"))
