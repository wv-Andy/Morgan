"""
Avisar a los agentes de que la nube se reinicia, antes de apagarse (3.0-D).

**Por qué existe, medido.** La 3.0-B pedía cerrar los sockets con 1012 al apagarse
para que el agente se mudara a la instancia nueva en el acto. Se hizo (uvicorn con
`websockets-sansio`) y se midió en un redespliegue real de Render
(docs/mediciones.md §6): la instancia vieja se apagó en una décima, pero **el proxy de
Render no reenvía el cierre**: mantuvo la conexión del agente colgada 8,5 s más y la
soltó sin código. El agente tardó 13,3 s en volver, y una orden que esperaba se sirvió
a los 10,04 s, en el límite.

Un **mensaje normal** sí atraviesa el proxy, como cualquier dato. Así que al recibir la
señal de apagado, antes de dejar que uvicorn cierre nada, la nube manda
`{"tipo": "reinicio"}` a cada agente conectado; el agente reconecta al momento y cae
en la instancia nueva, que en un despliegue de Render ya está atendiendo.

**Cómo, sin romper el apagado de uvicorn.** Uvicorn instala su propio manejador de
SIGTERM al arrancar; esto se instala después, en el arranque de la app, y lo encadena:
avisa, espera un momento a que salgan los mensajes y llama al de uvicorn, que sigue con
el apagado de siempre. Solo desde el hilo principal (las señales solo se atienden ahí):
en las pruebas, que sirven la app desde otro hilo, no se instala.
"""

import asyncio
import json
import logging
import signal
import threading

from src.canal.registro import REGISTRO

logger = logging.getLogger(__name__)

#: Lo que se espera tras avisar antes de dejar seguir el apagado: lo justo para que
#: los mensajes salgan por el socket.
MARGEN = 0.3


async def avisar_reinicio() -> int:
    """Manda el aviso a cada agente conectado. Devuelve a cuántos."""
    avisados = 0
    for conexion in REGISTRO.todas_las_conexiones():
        if not conexion.viva:
            continue
        try:
            await conexion.enviar(json.dumps({"tipo": "reinicio"}))
            avisados += 1
        except Exception:
            pass
    # WARNING y no INFO: el despliegue solo enseña avisos, y sin esta línea no se
    # puede saber en un redespliegue si el aviso salió (se necesitó para medirlo).
    logger.warning("Apagado: aviso de reinicio enviado a %d agente(s)", avisados)
    return avisados


def instalar(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Encadena el aviso al manejador de SIGTERM que haya (el de uvicorn). Devuelve si lo hizo."""
    if threading.current_thread() is not threading.main_thread():
        return False
    loop = loop or asyncio.get_running_loop()
    anterior = signal.getsignal(signal.SIGTERM)

    async def avisar_y_seguir(sig, frame):
        try:
            await avisar_reinicio()
            await asyncio.sleep(MARGEN)
        finally:
            if callable(anterior):
                anterior(sig, frame)
            elif anterior == signal.SIG_DFL:
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                signal.raise_signal(signal.SIGTERM)

    def manejador(sig, frame):
        # Desde una señal no se toca el bucle directamente: se le pasa el trabajo.
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(avisar_y_seguir(sig, frame)))

    signal.signal(signal.SIGTERM, manejador)
    logger.warning("Aviso de reinicio a los agentes locales: instalado sobre SIGTERM")
    return True
