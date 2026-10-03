"""
La confirmación en el PC (3.3): una notificación de Windows con «Permitir» y «Rechazar».

Lo elegí en la 3.2: se ve aunque el agente corra sin ventana (`pythonw`, el
arranque automático), que es como corre casi siempre. **Medido el 2026-09-24** en su
PC, con el agente sin ventana: la notificación sale, pulsé «Permitir» y el agente
lo supo a los 19 s.

## Qué cuenta como «sí»

**Solo pulsar «Permitir».** Todo lo demás es un no: pulsar «Rechazar», cerrar la
notificación, no contestar en `ESPERA` segundos, que Windows no pueda enseñarla, o que
este PC no tenga con qué (sin la biblioteca, o fuera de Windows). Fallar cerrando.

## Lo que enseña

La operación y la ruta **tal cual**, sin lo que sirve para disfrazar un nombre: las
marcas bidi (`fdp.exe` escrito al revés se ve como un PDF) y los caracteres de control.
Y todo va **escapado** dentro del XML de la notificación: un nombre de archivo con
`<action …>` dentro no puede añadir un botón.
"""

import contextvars
import logging
import threading
import time
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

#: Cuánto se espera a que la persona conteste. Por debajo del plazo de la orden en la
#: nube (180 s) con margen: si no, la nube daría la orden por perdida mientras el PC
#: todavía podría ejecutarla.
ESPERA = 120.0

#: Con qué nombre sale la notificación («Morgan»). Una aplicación sin instalador se da
#: de alta en el registro del usuario con este identificador.
AUMID = "Morgan.Agente"

PERMITIDA = "permitida"

#: `ToastDismissalReason.UserCanceled`: la persona la cerró (medido el 2026-09-24: una
#: notificación sin tocar volvió con este motivo cuando la cerré, a los 14,6 s).
CERRADA_POR_LA_PERSONA = 0

#: Cuándo vence la orden que se está ejecutando (reloj monótono del PC). Lo pone el
#: ejecutor; `asyncio.to_thread` lo lleva al hilo de la capacidad.
#:
#: **Por qué**: una orden que esperó en la cola de la nube puede tener menos plazo que
#: `ESPERA`. Si la notificación esperase más, la nube daría la orden por perdida y el PC
#: todavía podría ejecutarla después de un «Permitir» tardío. Así, la pregunta se cierra
#: siempre `MARGEN` segundos antes de que venza la orden.
VENCE: contextvars.ContextVar[float | None] = contextvars.ContextVar("vence", default=None)
MARGEN = 10.0


def visible(texto: str, largo: int = 240) -> str:
    """Lo que se enseña: sin controles ni marcas bidi, y sin pasar de `largo`."""
    from src.agente.salida import _BIDI

    limpio = "".join(" " if c < " " or c == "\x7f" or c in _BIDI else c for c in str(texto))
    if len(limpio) > largo:
        mitad = largo // 2 - 1
        limpio = limpio[:mitad] + "…" + limpio[-mitad:]
    return limpio


def xml(titulo: str, detalle: str, botones: tuple[str, str] = ("Rechazar", "Permitir")) -> str:
    """La notificación. `scenario="reminder"` la deja en pantalla hasta que se conteste.
    `botones`: el que no hace nada y el que sí, en ese orden (3.8: «Luego» e «Instalar»)."""
    t = escape(visible(titulo), {'"': "&quot;"})
    d = escape(visible(detalle), {'"': "&quot;"})
    no, si = (escape(visible(b, 20), {'"': "&quot;"}) for b in botones)
    return (
        '<toast scenario="reminder">'
        f'<visual><binding template="ToastGeneric"><text>{t}</text><text>{d}</text></binding></visual>'
        # «Rechazar» primero: lo que se pulsa por costumbre o con prisa es el primer
        # botón, y ese tiene que ser el que no hace nada. Medido el 2026-09-24: en la
        # prueba, notificaciones que decían «NO pulses nada» volvieron con «Permitir».
        '<actions>'
        f'<action content="{no}" arguments="rechazar" activationType="foreground"/>'
        f'<action content="{si}" arguments="permitir" activationType="foreground"/>'
        '</actions></toast>'
    )


def _registrar_nombre() -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}") as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Morgan")


def preguntar(titulo: str, detalle: str, espera: float = ESPERA,
              botones: tuple[str, str] = ("Rechazar", "Permitir")) -> str:
    """Pregunta en el PC. Devuelve `permitida`, `rechazada`, `cerrada`, `sin_respuesta` o
    `no_disponible`. **Solo `permitida` es un sí.** Nunca lanza."""
    vence = VENCE.get()
    if vence is not None:
        espera = min(espera, vence - time.monotonic() - MARGEN)
        if espera < 5:
            return "sin_tiempo"
    try:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import (ToastActivatedEventArgs, ToastDismissedEventArgs,
                                                    ToastNotification,
                                                    ToastNotificationManager)
    except Exception:
        logger.warning("Este PC no puede enseñar notificaciones: se deniega lo que exige confirmación")
        return "no_disponible"

    decidido = threading.Event()
    respuesta = {"valor": "sin_respuesta"}
    candado = threading.Lock()

    def decidir(valor: str) -> None:
        # Solo cuenta la primera: un clic que llegue tarde, cuando ya se dio por
        # perdida, no cambia nada.
        with candado:
            if not decidido.is_set():
                respuesta["valor"] = valor
                decidido.set()

    def al_cerrar(argumentos, tipo) -> None:
        try:
            motivo = int(tipo._from(argumentos).reason)
        except Exception:
            motivo = CERRADA_POR_LA_PERSONA
        if motivo == CERRADA_POR_LA_PERSONA:
            decidir("cerrada")
        # Las otras dos no son un «no»: `ApplicationHidden` la quita este mismo código
        # cuando ya hay respuesta, y `TimedOut` la manda al centro de notificaciones,
        # donde todavía se puede pulsar. Se sigue esperando hasta `espera`.

    def al_activar(_origen, argumentos):
        try:
            boton = ToastActivatedEventArgs._from(argumentos).arguments
        except Exception:
            boton = ""
        decidir(PERMITIDA if boton == "permitir" else "rechazada")

    try:
        _registrar_nombre()
        documento = XmlDocument()
        documento.load_xml(xml(titulo, detalle, botones))
        notificacion = ToastNotification(documento)
        notificacion.add_activated(al_activar)
        notificacion.add_dismissed(lambda _o, a: al_cerrar(a, ToastDismissedEventArgs))
        notificacion.add_failed(lambda *_: decidir("no_disponible"))
        avisador = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
        avisador.show(notificacion)
    except Exception:
        logger.warning("No se pudo enseñar la notificación", exc_info=True)
        return "no_disponible"

    # Se espera a trozos para enterarse si alguien pide parar (3.4): la notificación se
    # retira en vez de quedarse con un «Permitir» que ya no vale.
    from src.agente import control

    final = time.monotonic() + espera
    while not decidido.wait(min(0.25, max(0.0, final - time.monotonic()))):
        if control.pedida():
            decidir("cancelada")
            break
        if time.monotonic() >= final:
            break
    decidir("sin_respuesta")
    if respuesta["valor"] != PERMITIDA:
        try:
            avisador.hide(notificacion)      # que no quede un «Permitir» que ya no vale
        except Exception:
            pass
    return respuesta["valor"]


def avisar(titulo: str, texto: str) -> str:
    """Una notificación **sin botones** (4.10, «avísame cuando termine»): se enseña y ya.
    Devuelve `mostrada` o `no_disponible`. Nunca lanza."""
    try:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager
    except Exception:
        return "no_disponible"
    t = escape(visible(titulo, 80), {'"': "&quot;"})
    d = escape(visible(texto, 240), {'"': "&quot;"})
    try:
        _registrar_nombre()
        documento = XmlDocument()
        documento.load_xml('<toast><visual><binding template="ToastGeneric">'
                           f"<text>{t}</text><text>{d}</text></binding></visual></toast>")
        ToastNotificationManager.create_toast_notifier_with_id(AUMID).show(ToastNotification(documento))
    except Exception:
        logger.warning("No se pudo enseñar el aviso", exc_info=True)
        return "no_disponible"
    return "mostrada"
