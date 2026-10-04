"""
Los avisos de la bandeja, también como notificación en el PC (5.2).

**Decisión mía (2026-10-04)**, que cambia la 3 de la automatización («solo la bandeja»):
además de la bandeja de la web, el aviso sale como notificación de Windows en cada PC de la
persona que esté conectado **y tenga encendido «Mandarte avisos a este PC»** (`notify`, que nace
apagado). La bandeja sigue igual: es donde está el aviso entero.

Va como una orden `notify` de siempre (`despacho.enviar_async`, a ese PC por su `agent_id`): el
agente la comprueba con su política, la apunta en su diario y sale en «Lo último que hizo».
Sirve para los avisos de las automatizaciones y para las alertas de errores al propietario.

**Nunca espera ni lanza**: se manda en el bucle del servidor y, si falla (el PC se acaba de
desconectar, no contesta), queda en el registro como advertencia. Una advertencia y no un error:
un error iría a la bandeja del propietario (`src/alertas.py`) y saldría como otro aviso.
"""

import asyncio
import logging

from src.canal.registro import REGISTRO

logger = logging.getLogger(__name__)

#: Lo que cabe en la notificación (`notify` corta en 80 y 240): el resto, en la bandeja.
LARGO_TITULO = 80
LARGO_TEXTO = 200
PLAZO = 20.0


def _texto(texto: str) -> str:
    texto = " ".join(str(texto or "").split())
    if len(texto) <= LARGO_TEXTO:
        return texto
    return texto[:LARGO_TEXTO].rstrip() + "… (entero, en la bandeja de Morgan)"


def _si_falla(futuro) -> None:
    try:
        futuro.result()
    except Exception as exc:
        logger.warning("No se pudo mandar un aviso al PC (%s)", type(exc).__name__)


def avisar(user_id: str, titulo: str, texto: str) -> int:
    """Manda el aviso a los PC conectados de `user_id` con `notify` encendido. Devuelve a
    cuántos se mandó (sin esperar a que contesten)."""
    from src.canal import despacho

    loop = REGISTRO.loop
    if loop is None or loop.is_closed():
        return 0
    destinos = [c for c in REGISTRO.todas(user_id) if "notify" in c.capacidades]
    argumentos = {"titulo": f"Morgan · {titulo}"[:LARGO_TITULO], "mensaje": _texto(texto)}
    for conexion in destinos:
        futuro = asyncio.run_coroutine_threadsafe(
            despacho.enviar_async(user_id, "notify", dict(argumentos), plazo=PLAZO,
                                  equipo=conexion.agent_id), loop)
        futuro.add_done_callback(_si_falla)
    return len(destinos)
