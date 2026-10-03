"""
El portapapeles y los avisos (4.10, mi lista: «copia esto al portapapeles», «¿qué
tengo copiado?», «avísame cuando termine»). Las dos capacidades **nacen apagadas**.

- **`clipboard`**, 🟢: `escribir` deja un texto en el portapapeles (visible y reversible:
  sin plan, permisos aprobados por mí). `leer` lo trae, pero **pide «Permitir» cada vez**,
  sin poder quitarse: lo copiado puede ser una contraseña. La notificación no enseña el
  contenido, solo cuánto ocupa. Solo texto, con topes.
- **`notify`**, 🟢: una notificación de Windows, sin botones. «Avísame cuando termine»
  dentro de un turno (un plan que acaba con un aviso); los de trabajos largos, sin nadie
  delante, son la automatización (4.14).
"""

import sys
import time

from src.agente import aviso, control, motor

MAX_TEXTO = 20 * 1024
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _ok(datos, **auditoria) -> dict:
    return {"success": True, "data": datos, "error": None, "_auditoria": auditoria}


def _no(motivo: str, mensaje: str, **auditoria) -> dict:
    r = {"success": False, "data": None, "error": mensaje, "motivo": motivo}
    if auditoria:
        r["_auditoria"] = auditoria
    return r


def _api():
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return ctypes, user32, kernel32


def _abrir(user32) -> bool:
    """Otro programa puede tenerlo abierto un momento: se reintenta un poco."""
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def leer_texto() -> str | None:
    """El texto del portapapeles, o None si no hay texto."""
    ctypes, user32, kernel32 = _api()
    if not _abrir(user32):
        raise OSError("Otro programa tiene el portapapeles ocupado.")
    try:
        mango = user32.GetClipboardData(CF_UNICODETEXT)
        if not mango:
            return None
        puntero = kernel32.GlobalLock(mango)
        if not puntero:
            return None
        try:
            return ctypes.wstring_at(puntero)
        finally:
            kernel32.GlobalUnlock(mango)
    finally:
        user32.CloseClipboard()


def escribir_texto(texto: str) -> None:
    ctypes, user32, kernel32 = _api()
    datos = texto.encode("utf-16-le") + b"\x00\x00"
    memoria = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(datos))
    if not memoria:
        raise OSError("No hay memoria para el portapapeles.")
    puntero = kernel32.GlobalLock(memoria)
    ctypes.memmove(puntero, datos, len(datos))
    kernel32.GlobalUnlock(memoria)
    if not _abrir(user32):
        kernel32.GlobalFree(memoria)
        raise OSError("Otro programa tiene el portapapeles ocupado.")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, memoria):
            kernel32.GlobalFree(memoria)          # si no lo aceptó, sigue siendo nuestro
            raise OSError("Windows no aceptó el texto.")
    finally:
        user32.CloseClipboard()


def clipboard(accion: str = "", texto: str = "", **_) -> dict:
    decision = motor.evaluar("portapapeles")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    if sys.platform != "win32":
        return _no("no_disponible", "El portapapeles es de Windows.")
    accion = str(accion or "").lower()

    if accion == "escribir":
        if not isinstance(texto, str) or not texto:
            return _no("argumentos", "Falta el texto.")
        if len(texto.encode("utf-8")) > MAX_TEXTO or "\x00" in texto:
            return _no("argumentos", f"Solo texto, hasta {MAX_TEXTO // 1024} KB.")
        control.sin_vuelta()
        try:
            escribir_texto(texto)
            vuelto = leer_texto()
        except OSError as exc:
            return _no("ocupado", str(exc))
        if vuelto != texto:
            return _no("no_verificada", "Se escribió, pero al leerlo no coincide.")
        return _ok({"caracteres": len(texto), "comprobado": "el portapapeles tiene ese texto"},
                   confirmacion="no_hacia_falta")

    if accion == "leer":
        try:
            actual = leer_texto()
        except OSError as exc:
            return _no("ocupado", str(exc))
        if actual is None:
            return _ok({"texto": None, "aviso": "No hay texto copiado (puede haber una imagen o nada)."},
                       confirmacion="no_hacia_falta")
        # Siempre se pregunta (decisión mía): puede ser una contraseña. Sin enseñarla.
        respuesta = aviso.preguntar("Morgan quiere LEER lo que tienes copiado",
                                    f"{len(actual)} caracteres. Pueden ser una contraseña: permite solo si "
                                    "tú se lo pediste.")
        control.punto_seguro()
        if respuesta != aviso.PERMITIDA:
            return _no("no_confirmada", "La persona no lo permitió en su PC (o no contestó a tiempo).",
                       confirmacion=respuesta)
        recortado = actual[:MAX_TEXTO]
        return _ok({"texto": recortado, "caracteres": len(actual),
                    "is_truncated": len(actual) > MAX_TEXTO}, confirmacion=respuesta)

    return _no("argumentos", "accion tiene que ser leer o escribir.")


def notify(titulo: str = "", mensaje: str = "", **_) -> dict:
    decision = motor.evaluar("avisar")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    if not str(titulo or "").strip() and not str(mensaje or "").strip():
        return _no("argumentos", "Falta qué avisar.")
    resultado = aviso.avisar(str(titulo or "Morgan")[:80], str(mensaje or "")[:240])
    if resultado != "mostrada":
        return _no("no_disponible", "Este PC no puede enseñar notificaciones ahora.")
    return _ok({"mostrada": True, "comprobado": "Windows enseñó la notificación"})


AVISOS = {"clipboard": clipboard, "notify": notify}
