"""
Ventanas y capturas (4.11, mi lista: «maximizar, minimizar, mover, organizar
ventanas», «captura de pantalla, de ventana, de región», «¿qué tengo abierto?»). Las dos
capacidades **nacen apagadas**. Todo con `ctypes` y la API de Windows: sin dependencias.

- **`windows`**, 🟢 y sin plan: listar las ventanas visibles (título, programa, estado,
  tamaño), enfocar, minimizar, maximizar, restaurar, mover y poner dos lado a lado. Visible
  y reversible (permisos aprobados por mí). Los títulos dicen qué hay abierto: cuentan
  como leído del PC para la protección contra fugas.
- **`screenshot`**, 🟢 pero con **«Permitir» cada vez**, sin que la política lo quite: una
  captura ve todo lo que hay en pantalla. La imagen (PNG, a lo sumo 1.600 px de ancho) viaja
  como una copia del PC (3.1-E): queda entre los archivos de la persona, se borra sola a las
  24 h, y el modelo la mira con `analyze_image` (Gemini: 20 al día en la capa gratuita).
"""

import struct
import sys
import time
import zlib
from pathlib import Path

from src.agente import aviso, control, motor
from src.agente import estado as almacen

MAX_ANCHO = 1600
MAX_VENTANAS = 40
#: Las capturas se guardan un rato en la carpeta del agente, hasta que salen hacia la nube.
VIDA_DE_UNA_CAPTURA = 3600


def _ok(datos, **extra) -> dict:
    return {"success": True, "data": datos, "error": None, **extra}


def _no(motivo: str, mensaje: str, **auditoria) -> dict:
    r = {"success": False, "data": None, "error": mensaje, "motivo": motivo}
    if auditoria:
        r["_auditoria"] = auditoria
    return r


def _win32():
    import ctypes
    from ctypes import wintypes

    return ctypes, wintypes, ctypes.windll.user32, ctypes.windll.gdi32


def _consciente_de_ppp():
    """Que las coordenadas sean las de la pantalla de verdad, no las escaladas."""
    try:
        import ctypes

        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:
        pass


# --- Ventanas ---

def _escondida(hwnd) -> bool:
    """Las de las apps en segundo plano de Windows están «ocultas por DWM» aunque visibles."""
    try:
        import ctypes

        oculta = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(oculta), ctypes.sizeof(oculta))
        return bool(oculta.value)
    except Exception:
        return False


def ventanas() -> list[dict]:
    import psutil

    ctypes, wintypes, user32, _ = _win32()
    _consciente_de_ppp()
    salida: list[dict] = []
    TIPO = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 4, -20, 0x00000080

    def una(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER) or _escondida(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        largo = user32.GetWindowTextLengthW(hwnd)
        if not largo:
            return True
        buf = ctypes.create_unicode_buffer(largo + 1)
        user32.GetWindowTextW(hwnd, buf, largo + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            programa = psutil.Process(pid.value).name()
        except psutil.Error:
            programa = "?"
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        salida.append({"id": int(hwnd), "titulo": aviso.visible(buf.value, 120), "programa": programa,
                       "estado": "minimizada" if user32.IsIconic(hwnd) else
                       "maximizada" if user32.IsZoomed(hwnd) else "normal",
                       "x": r.left, "y": r.top, "ancho": r.right - r.left, "alto": r.bottom - r.top})
        return True

    user32.EnumWindows(TIPO(una), 0)
    return salida


def _elegir(id_ventana=None, titulo: str = "") -> tuple[dict | None, dict | None]:
    """La ventana pedida (por su id de `listar`, o por un trozo de su título), o el porqué no."""
    todas = ventanas()
    if id_ventana not in (None, ""):
        try:
            elegida = next((v for v in todas if v["id"] == int(id_ventana)), None)
        except (TypeError, ValueError):
            elegida = None
        return (elegida, None) if elegida else (None, _no("no_existe", "No hay ninguna ventana con ese id."))
    buscado = str(titulo or "").lower().strip()
    if not buscado:
        return None, _no("argumentos", "Di cuál: su id (accion=listar) o parte del título.")
    candidatas = [v for v in todas if buscado in v["titulo"].lower() or buscado in v["programa"].lower()]
    if not candidatas:
        return None, _no("no_existe", f"No hay ninguna ventana con «{titulo}».")
    if len(candidatas) > 1:
        return None, _no("ambigua", "Hay varias: " + "; ".join(f"{v['id']}: {v['titulo']}" for v in candidatas[:8])
                         + ". Di cuál por su id.")
    return candidatas[0], None


def _area_de_trabajo():
    ctypes, wintypes, user32, _ = _win32()
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)       # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def windows(accion: str = "", id: int | None = None, titulo: str = "", x: int | None = None,
            y: int | None = None, ancho: int | None = None, alto: int | None = None,
            otra_id: int | None = None, otro_titulo: str = "", **_) -> dict:
    """accion: listar | enfocar | minimizar | maximizar | restaurar | mover (x, y, ancho, alto)
    | lado_a_lado (id o titulo a la izquierda; otra_id u otro_titulo a la derecha)."""
    decision = motor.evaluar("ventanas")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    if sys.platform != "win32":
        return _no("no_disponible", "Las ventanas son de Windows.")
    accion = str(accion or "").lower()
    if accion == "listar":
        lista = ventanas()
        return _ok({"ventanas": lista[:MAX_VENTANAS], "total": len(lista)})

    ventana, rechazo = _elegir(id, titulo)
    if rechazo:
        return rechazo
    _, _, user32, _ = _win32()
    hwnd = ventana["id"]
    control.sin_vuelta()
    if accion == "minimizar":
        user32.ShowWindow(hwnd, 6)
    elif accion == "maximizar":
        user32.ShowWindow(hwnd, 3)
    elif accion == "restaurar":
        user32.ShowWindow(hwnd, 9)
    elif accion == "enfocar":
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    elif accion == "mover":
        try:
            nx, ny, na, nl = (int(v) if v is not None else d for v, d in
                              ((x, ventana["x"]), (y, ventana["y"]), (ancho, ventana["ancho"]), (alto, ventana["alto"])))
        except (TypeError, ValueError):
            return _no("argumentos", "x, y, ancho y alto son números.")
        if na < 100 or nl < 60:
            return _no("argumentos", "Tan pequeña no se vería: al menos 100 × 60.")
        user32.ShowWindow(hwnd, 9)
        user32.MoveWindow(hwnd, nx, ny, na, nl, True)
    elif accion == "lado_a_lado":
        otra, rechazo = (_elegir(otra_id, otro_titulo) if otra_id not in (None, "") or otro_titulo
                         else (None, _no("argumentos", "Falta la ventana de la derecha: otra_id u otro_titulo.")))
        if rechazo:
            return rechazo
        ax, ay, aa, al = _area_de_trabajo()
        for h, izquierda in ((hwnd, True), (otra["id"], False)):
            user32.ShowWindow(h, 9)
            user32.MoveWindow(h, ax if izquierda else ax + aa // 2, ay, aa // 2, al, True)
    else:
        return _no("argumentos", "accion: listar, enfocar, minimizar, maximizar, restaurar, mover o lado_a_lado.")
    time.sleep(0.2)
    despues = next((v for v in ventanas() if v["id"] == hwnd), None)
    return _ok({"ventana": ventana["titulo"], "accion": accion, "ahora": despues and despues["estado"]})


# --- Capturas ---

def _png(ancho: int, alto: int, bgra: bytes) -> bytes:
    """Un PNG RGB a partir de píxeles BGRA de arriba abajo, sin bibliotecas de imágenes."""
    rgb = bytearray(ancho * alto * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    fila = ancho * 3
    crudo = b"".join(b"\x00" + bytes(rgb[i * fila:(i + 1) * fila]) for i in range(alto))

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(crudo, 6)) + trozo(b"IEND", b""))


def capturar(x: int, y: int, ancho: int, alto: int) -> tuple[bytes, int, int]:
    """La zona de la pantalla (coordenadas del escritorio virtual), reducida a `MAX_ANCHO`."""
    ctypes, wintypes, user32, gdi32 = _win32()
    _consciente_de_ppp()
    escala = min(1.0, MAX_ANCHO / max(1, ancho))
    fa, fl = max(1, int(ancho * escala)), max(1, int(alto * escala))
    pantalla = user32.GetDC(None)
    memoria = gdi32.CreateCompatibleDC(pantalla)
    mapa = gdi32.CreateCompatibleBitmap(pantalla, fa, fl)
    anterior = gdi32.SelectObject(memoria, mapa)
    try:
        gdi32.SetStretchBltMode(memoria, 4)                               # HALFTONE
        gdi32.StretchBlt(memoria, 0, 0, fa, fl, pantalla, x, y, ancho, alto, 0x00CC0020 | 0x40000000)

        class CABECERA(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        cabecera = CABECERA(ctypes.sizeof(CABECERA), fa, -fl, 1, 32, 0, 0, 0, 0, 0, 0)   # de arriba abajo
        pixeles = ctypes.create_string_buffer(fa * fl * 4)
        if not gdi32.GetDIBits(memoria, mapa, 0, fl, pixeles, ctypes.byref(cabecera), 0):
            raise OSError("Windows no dio los píxeles de la pantalla.")
        return _png(fa, fl, pixeles.raw), fa, fl
    finally:
        gdi32.SelectObject(memoria, anterior)
        gdi32.DeleteObject(mapa)
        gdi32.DeleteDC(memoria)
        user32.ReleaseDC(None, pantalla)


def _escritorio() -> tuple[int, int, int, int]:
    _, _, user32, _ = _win32()
    _consciente_de_ppp()
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def _carpeta_de_capturas() -> Path:
    carpeta = almacen.carpeta() / "capturas"
    carpeta.mkdir(parents=True, exist_ok=True)
    limite = time.time() - VIDA_DE_UNA_CAPTURA
    for vieja in carpeta.glob("*.png"):
        try:
            if vieja.stat().st_mtime < limite:
                vieja.unlink()
        except OSError:
            pass
    return carpeta


def screenshot(alcance: str = "pantalla", id: int | None = None, titulo: str = "", x: int | None = None,
               y: int | None = None, ancho: int | None = None, alto: int | None = None, **_) -> dict:
    """alcance: pantalla | ventana (id o titulo) | region (x, y, ancho, alto)."""
    decision = motor.evaluar("captura")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    if sys.platform != "win32":
        return _no("no_disponible", "Las capturas son de Windows.")
    alcance = str(alcance or "pantalla").lower()
    ex, ey, ea, el = _escritorio()
    que = "la pantalla entera"
    if alcance == "pantalla":
        zona = (ex, ey, ea, el)
    elif alcance == "ventana":
        ventana, rechazo = _elegir(id, titulo)
        if rechazo:
            return rechazo
        if ventana["estado"] == "minimizada":
            return _no("minimizada", "Esa ventana está minimizada: no se ve nada que capturar.")
        zona = (ventana["x"], ventana["y"], ventana["ancho"], ventana["alto"])
        que = f"la ventana «{ventana['titulo']}»"
    elif alcance == "region":
        try:
            zona = tuple(int(v) for v in (x, y, ancho, alto))
        except (TypeError, ValueError):
            return _no("argumentos", "Una región necesita x, y, ancho y alto.")
        que = f"la zona {zona[2]}×{zona[3]} en ({zona[0]}, {zona[1]})"
    else:
        return _no("argumentos", "alcance: pantalla, ventana o region.")
    # Recortada a lo que hay de escritorio.
    zx, zy = max(zona[0], ex), max(zona[1], ey)
    za, zl = min(zona[0] + zona[2], ex + ea) - zx, min(zona[1] + zona[3], ey + el) - zy
    if za < 8 or zl < 8:
        return _no("argumentos", "Esa zona queda fuera de la pantalla.")

    # Siempre se pregunta (decisión mía): una captura ve todo lo que hay en pantalla.
    respuesta = aviso.preguntar("Morgan quiere HACER UNA CAPTURA",
                                f"De {que}. La imagen sube a Morgan: permite solo si tú lo pediste y no "
                                "hay nada privado a la vista.")
    control.punto_seguro()
    if respuesta != aviso.PERMITIDA:
        return _no("no_confirmada", "La persona no lo permitió en su PC (o no contestó a tiempo).",
                   confirmacion=respuesta)
    try:
        datos, fa, fl = capturar(zx, zy, za, zl)
    except OSError as exc:
        return _no("no_capturada", str(exc), confirmacion=respuesta)
    ruta = _carpeta_de_capturas() / f"captura-{time.strftime('%Y%m%d-%H%M%S')}.png"
    ruta.write_bytes(datos)
    import hashlib

    return {**_ok({"name": ruta.name, "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest(),
                   "tipo": "image/png", "ancho": fa, "alto": fl, "de": que}),
            "_archivo": str(ruta), "_auditoria": {"confirmacion": respuesta, "de": que}}


PANTALLA = {"windows": windows, "screenshot": screenshot}
