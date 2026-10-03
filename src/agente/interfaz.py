"""
El control de la interfaz (4.12, mi lista: «hacer clic, escribir, presionar teclas,
seleccionar elementos, leer elementos accesibles de la UI; interactuar con aplicaciones que
no tengan API»). Lo más delicado de la lista: Morgan maneja el ratón y el teclado.

Con **UI Automation** (la accesibilidad de Windows), llamada por `ctypes` y sin
dependencias: se trabaja con los controles **por su nombre** (el botón «Guardar», el campo
«Buscar»), no con clics a ciegas en coordenadas. Dos capacidades, **apagadas al nacer**:

- **`ui_read`**, 🟢: los controles de una ventana (tipo, nombre, si está activo). Nunca el
  contenido de un campo de contraseña.
- **`ui_control`**, 🔴, **siempre con un plan** (los pasos exactos) **y «Permitir» al
  empezar** ese plan (permisos aprobados por mí): `clic`, `escribir` y `teclas`.

## Lo que nunca

- **Campos de contraseña**: ni se leen ni se escribe en ellos.
- **Ventanas que ejecutan o administran**: consolas, PowerShell, Terminal, el Explorador
  (un doble clic ejecuta), el Administrador de tareas, el registro, la Configuración, las
  herramientas de administración. Las de administrador (UAC) ni se alcanzan: Windows no deja
  a un programa normal tocar uno elevado.
- **La tecla Windows** y cualquier combinación fuera de una lista corta (Intro, Tab, Esc,
  flechas, Ctrl+C/V/X/Z/S…).
- **Escribir sin que la ventana esté delante**: antes de cada envío se comprueba que la
  ventana de primer plano es la pedida; si otra se cruza, se para.
"""

import ctypes
import sys
import time
from ctypes import POINTER, byref, c_int, c_void_p, wintypes

from src.agente import aviso, control, motor

MAX_CONTROLES = 80
MAX_TEXTO = 2000
#: Un «Permitir» vale para todo su plan, este rato.
VIGENCIA_DEL_PERMISO = 600.0

#: Programas cuyas ventanas no se tocan nunca.
PROGRAMAS_PROHIBIDOS = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "openconsole.exe",
    "conhost.exe", "explorer.exe", "taskmgr.exe", "regedit.exe", "mmc.exe", "systemsettings.exe",
    "consent.exe", "logonui.exe", "control.exe", "msconfig.exe", "powershell_ise.exe", "mintty.exe",
    "bash.exe", "wsl.exe", "ubuntu.exe", "python.exe", "pythonw.exe", "node.exe",
})

TIPOS = {50000: "boton", 50002: "casilla", 50003: "desplegable", 50004: "campo", 50005: "enlace",
         50007: "elemento", 50011: "menu", 50013: "opcion", 50019: "pestana", 50024: "rama",
         50029: "fila", 50030: "documento", 50031: "boton", 50015: "deslizador"}

#: Teclas sueltas y combinaciones que se permiten (`teclas.py`, compartido con la nube).
from src.agente.teclas import COMBINACIONES, PERMITIDAS, TECLAS  # noqa: E402

_PERMITIDOS: dict[str, float] = {}


def _ok(datos, **auditoria) -> dict:
    return {"success": True, "data": datos, "error": None, "_auditoria": auditoria}


def _no(motivo: str, mensaje: str, **auditoria) -> dict:
    r = {"success": False, "data": None, "error": mensaje, "motivo": motivo}
    if auditoria:
        r["_auditoria"] = auditoria
    return r


# --- UI Automation por ctypes ---

class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def _guid(texto: str) -> _GUID:
    g = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(texto), byref(g)) != 0:
        raise OSError(f"GUID no válido: {texto}")
    return g


def _metodo(obj, indice: int, *argumentos):
    """El método `indice` de la tabla COM de `obj` (los índices, de UIAutomationClient.h)."""
    tabla = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    funcion = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argumentos)(tabla[indice])
    return lambda *a: funcion(obj, *a)


def _soltar(obj) -> None:
    if obj and getattr(obj, "value", obj):
        try:
            _metodo(obj, 2)()                     # IUnknown::Release
        except OSError:
            pass


def _texto(obj, indice: int) -> str:
    oleaut32 = ctypes.windll.oleaut32
    oleaut32.SysFreeString.argtypes = [c_void_p]
    salida = c_void_p()
    _metodo(obj, indice, POINTER(c_void_p))(byref(salida))
    try:
        return ctypes.wstring_at(salida.value) if salida.value else ""
    finally:
        oleaut32.SysFreeString(salida)


def _entero(obj, indice: int) -> int:
    v = c_int()
    _metodo(obj, indice, POINTER(c_int))(byref(v))
    return v.value


# IUIAutomation: 6 ElementFromHandle, 21 CreateTrueCondition.
# IUIAutomationElement: 3 SetFocus, 6 FindAll, 14 GetCurrentPatternAs, 21 ControlType, 23 Name,
# 28 IsEnabled, 35 IsPassword, 43 BoundingRectangle. IUIAutomationElementArray: 3 Length, 4 GetElement.
_CLSID_UIA = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_IID_UIA = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
_PATRONES = {"invocar": (10000, "{fb377fbe-8ea6-46d5-9c73-6499642d3059}"),
             "valor": (10002, "{a94cd8b1-0844-4cd6-9d2d-640537ab39e9}"),
             "alternar": (10015, "{94cf8058-9b8d-4ab9-8bfd-4cd0a33c8c70}"),
             "seleccionar": (10010, "{a8efa66a-0fda-421a-9194-38021f3578ea}")}


class _Uia:
    """Una sesión de UI Automation sobre una ventana; suelta lo que pide al cerrar."""

    def __init__(self, hwnd: int):
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        self._sueltos: list = []
        self.uia = c_void_p()
        if ctypes.windll.ole32.CoCreateInstance(byref(_guid(_CLSID_UIA)), None, 1, byref(_guid(_IID_UIA)),
                                                byref(self.uia)) != 0:
            raise OSError("UI Automation no está disponible en este PC.")
        self.raiz = c_void_p()
        _metodo(self.uia, 6, c_void_p, POINTER(c_void_p))(hwnd, byref(self.raiz))
        self._sueltos += [self.uia, self.raiz]

    def controles(self) -> list[dict]:
        condicion, lista = c_void_p(), c_void_p()
        _metodo(self.uia, 21, POINTER(c_void_p))(byref(condicion))
        _metodo(self.raiz, 6, c_int, c_void_p, POINTER(c_void_p))(4, condicion, byref(lista))  # descendientes
        self._sueltos += [condicion, lista]
        salida = []
        for k in range(_entero(lista, 3)):
            e = c_void_p()
            _metodo(lista, 4, c_int, POINTER(c_void_p))(k, byref(e))
            self._sueltos.append(e)
            tipo = TIPOS.get(_entero(e, 21))
            if tipo is None:
                continue
            nombre = _texto(e, 23).strip()
            if not nombre and tipo not in ("campo", "documento"):
                continue
            r = wintypes.RECT()
            _metodo(e, 43, POINTER(wintypes.RECT))(byref(r))
            salida.append({"tipo": tipo, "nombre": aviso.visible(nombre, 80), "activo": bool(_entero(e, 28)),
                           "contrasena": bool(_entero(e, 35)), "_e": e,
                           "_centro": ((r.left + r.right) // 2, (r.top + r.bottom) // 2),
                           "_visible": r.right > r.left and r.bottom > r.top})
        return salida

    def patron(self, elemento, cual: str):
        identificador, iid = _PATRONES[cual]
        p = c_void_p()
        hr = _metodo(elemento, 14, c_int, POINTER(_GUID), POINTER(c_void_p))(identificador, byref(_guid(iid)), byref(p))
        if hr != 0 or not p.value:
            return None
        self._sueltos.append(p)
        return p

    def cerrar(self) -> None:
        for obj in reversed(self._sueltos):
            _soltar(obj)
        self._sueltos.clear()


# --- Teclado y ratón (SendInput) ---

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("relleno", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _enviar(entradas: list) -> None:
    arreglo = (_INPUT * len(entradas))(*entradas)
    ctypes.windll.user32.SendInput(len(entradas), arreglo, ctypes.sizeof(_INPUT))


def _tecla(vk: int, soltar: bool = False, unicode: int = 0) -> "_INPUT":
    banderas = (0x0002 if soltar else 0) | (0x0004 if unicode else 0)          # KEYUP, UNICODE
    return _INPUT(1, _UNION(ki=_KEYBDINPUT(0 if unicode else vk, unicode, banderas, 0, 0)))


def _escribir_teclas(texto: str, hwnd: int) -> None:
    for caracter in texto:
        if ctypes.windll.user32.GetForegroundWindow() != hwnd:
            raise _Cruzada()
        codigo = ord(caracter)
        if caracter == "\n":
            _enviar([_tecla(0x0D), _tecla(0x0D, True)])
        else:
            _enviar([_tecla(0, unicode=codigo), _tecla(0, True, unicode=codigo)])
        control.punto_seguro()


def _pulsar(combinacion: str, hwnd: int) -> None:
    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        raise _Cruzada()
    partes = combinacion.split("+")
    modificadores = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12}
    mods = [modificadores[p] for p in partes[:-1]]
    final = partes[-1]
    vk = TECLAS.get(final) or (ord(final.upper()) if len(final) == 1 else None)
    if vk is None:
        raise ValueError(final)
    _enviar([_tecla(m) for m in mods] + [_tecla(vk), _tecla(vk, True)] + [_tecla(m, True) for m in reversed(mods)])


def _clic(x: int, y: int, hwnd: int) -> None:
    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        raise _Cruzada()
    ctypes.windll.user32.SetCursorPos(x, y)
    _enviar([_INPUT(0, _UNION(mi=_MOUSEINPUT(0, 0, 0, 0x0002, 0, 0))),
             _INPUT(0, _UNION(mi=_MOUSEINPUT(0, 0, 0, 0x0004, 0, 0)))])


def _traer_delante(hwnd: int) -> bool:
    """La ventana, en primer plano. Windows a veces no deja a un programa traer otra ventana
    delante (el «bloqueo de primer plano»): una pulsación de Alt lo libera, y se reintenta.
    Medido al probarlo: sin esto, a ratos la acción se paraba como si se hubiera cruzado otra."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    for intento in range(3):
        if user32.GetForegroundWindow() == hwnd:
            return True
        # Se engancha la entrada al hilo de la ventana que está delante: así Windows deja
        # cambiarla. Y, si no, una pulsación de Alt libera el bloqueo.
        delante = user32.GetForegroundWindow()
        suyo = user32.GetWindowThreadProcessId(delante, None)
        propio = kernel32.GetCurrentThreadId()
        enganchado = bool(suyo and suyo != propio and user32.AttachThreadInput(propio, suyo, True))
        try:
            if intento:
                _enviar([_tecla(0x12), _tecla(0x12, True)])
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if enganchado:
                user32.AttachThreadInput(propio, suyo, False)
        time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


class _Cruzada(Exception):
    """Otra ventana se puso delante: se para."""


# --- Lo que se ofrece ---

def _ventana(id_ventana, titulo):
    from src.agente import pantalla

    ventana, rechazo = pantalla._elegir(id_ventana, titulo)
    if rechazo:
        return None, rechazo
    if ventana["programa"].lower() in PROGRAMAS_PROHIBIDOS:
        return None, _no("prohibida", f"«{ventana['titulo']}» es de {ventana['programa']}: Morgan no maneja "
                                      "consolas, el Explorador ni herramientas del sistema.")
    if ventana["estado"] == "minimizada":
        return None, _no("minimizada", "Esa ventana está minimizada.")
    return ventana, None


def ui_read(id: int | None = None, titulo: str = "", filtro: str = "", **_) -> dict:
    """Los controles de una ventana: tipo, nombre, si está activo y si es de contraseña."""
    decision = motor.evaluar("interfaz_leer")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    if sys.platform != "win32":
        return _no("no_disponible", "Es de Windows.")
    ventana, rechazo = _ventana(id, titulo)
    if rechazo:
        return rechazo
    sesion = _Uia(ventana["id"])
    try:
        controles = sesion.controles()
    finally:
        sesion.cerrar()
    if filtro:
        controles = [c for c in controles if str(filtro).lower() in c["nombre"].lower()]
    publicos = [{k: v for k, v in c.items() if not k.startswith("_")} for c in controles if c["_visible"]]
    return _ok({"ventana": ventana["titulo"], "controles": publicos[:MAX_CONTROLES], "total": len(publicos),
                "is_truncated": len(publicos) > MAX_CONTROLES})


def _permitido(origen: str, que: str, ventana: dict) -> dict | None:
    """«Permitir» una vez por plan (decisión mía): el primer paso pregunta; los demás del
    mismo plan, en los minutos siguientes, no. Sin plan, se pregunta cada vez."""
    plan = (origen or "").split("#")[0] if "#" in (origen or "") else None
    ahora = time.monotonic()
    if plan and _PERMITIDOS.get(plan, 0) > ahora:
        return None
    respuesta = aviso.preguntar("Morgan quiere usar el RATÓN y el TECLADO",
                                f"En «{ventana['titulo']}»: {que}" + (". Vale para todo este plan." if plan else "."))
    control.punto_seguro()
    if respuesta != aviso.PERMITIDA:
        return _no("no_confirmada", "La persona no lo permitió en su PC (o no contestó a tiempo).",
                   confirmacion=respuesta)
    if plan:
        _PERMITIDOS[plan] = ahora + VIGENCIA_DEL_PERMISO
    return None


def _uno(controles: list[dict], nombre: str, tipo: str | None, sin_contrasenas: bool = False):
    buscado = str(nombre or "").strip().lower()
    candidatos = [c for c in controles if c["_visible"] and (not tipo or c["tipo"] == tipo)
                  and not (sin_contrasenas and c["contrasena"])]
    exactos = [c for c in candidatos if c["nombre"].lower() == buscado]
    parecidos = exactos or [c for c in candidatos if buscado and buscado in c["nombre"].lower()]
    if not parecidos:
        return None, _no("no_existe", f"No hay ningún control «{nombre}» en esa ventana (ui_read los enseña).")
    if len(parecidos) > 1:
        return None, _no("ambiguo", "Hay varios: " + "; ".join(f"{c['tipo']} «{c['nombre']}»" for c in parecidos[:8])
                         + ". Di cuál con su tipo o su nombre completo.")
    return parecidos[0], None


def ui_control(accion: str = "", id: int | None = None, titulo: str = "", elemento: str = "",
               tipo: str | None = None, texto: str = "", teclas: str = "", origen: str = "", **_) -> dict:
    """accion: clic (elemento) | escribir (elemento, texto) | teclas (teclas, p. ej. «ctrl+s»)."""
    decision = motor.evaluar("interfaz", capacidad="ui_control")
    if not decision:
        return _no(decision.motivo, decision.mensaje)
    if sys.platform != "win32":
        return _no("no_disponible", "Es de Windows.")
    accion = str(accion or "").lower()
    if accion not in ("clic", "escribir", "teclas"):
        return _no("argumentos", "accion: clic, escribir o teclas.")
    combinacion = str(teclas or "").lower().replace(" ", "")
    if accion == "teclas" and combinacion not in TECLAS and combinacion not in COMBINACIONES:
        return _no("tecla_prohibida", f"«{teclas}» no se permite. Sí: {PERMITIDAS}.")
    if accion == "escribir" and (not isinstance(texto, str) or not texto or len(texto) > MAX_TEXTO
                                 or any(c < " " and c not in "\n\t" for c in texto)):
        return _no("argumentos", f"Hace falta un texto, de hasta {MAX_TEXTO} caracteres.")

    ventana, rechazo = _ventana(id, titulo)
    if rechazo:
        return rechazo
    hwnd = ventana["id"]
    sesion = _Uia(hwnd)
    try:
        objetivo = None
        if accion in ("clic", "escribir"):
            # Al escribir, los de contraseña ni cuentan: nunca se escribe en ellos.
            objetivo, rechazo = _uno(sesion.controles(), elemento, tipo, sin_contrasenas=accion == "escribir")
            if rechazo:
                return rechazo
            if objetivo["contrasena"]:
                return _no("contrasena", "Es un campo de contraseña: Morgan no escribe ahí.")
            if not objetivo["activo"]:
                return _no("inactivo", f"«{objetivo['nombre']}» está desactivado ahora.")
        que = {"clic": lambda: f"hacer clic en {objetivo['tipo']} «{objetivo['nombre']}»",
               "escribir": lambda: f"escribir {len(texto)} caracteres en «{objetivo['nombre']}»",
               "teclas": lambda: f"pulsar {combinacion}"}[accion]()
        no = _permitido(origen, que, ventana)
        if no:
            return no

        control.sin_vuelta()
        # Por su nombre (invocar, poner el valor), la ventana no hace falta delante; el
        # teclado y el ratón, sí (`_traer_delante`, más abajo, solo entonces).
        no_delante = _no("no_delante", "Windows no dejó poner esa ventana delante: no se ha hecho nada.",
                         confirmacion="permitida")
        try:
            if accion == "clic":
                for cual, indice in (("invocar", 3), ("alternar", 3), ("seleccionar", 3)):
                    p = sesion.patron(objetivo["_e"], cual)
                    if p is not None:
                        _metodo(p, indice)()
                        break
                else:
                    if not _traer_delante(hwnd):
                        return no_delante
                    _clic(*objetivo["_centro"], hwnd)
                return _ok({"hecho": que, "comprobado": "se pulsó"}, confirmacion="permitida")
            if accion == "escribir":
                p = sesion.patron(objetivo["_e"], "valor")
                if p is not None:
                    oleaut32 = ctypes.windll.oleaut32
                    oleaut32.SysAllocString.restype = c_void_p
                    oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
                    oleaut32.SysFreeString.argtypes = [c_void_p]
                    cadena = oleaut32.SysAllocString(texto)
                    try:
                        hr = _metodo(p, 3, c_void_p)(cadena)                 # IValuePattern::SetValue
                    finally:
                        oleaut32.SysFreeString(cadena)
                    if hr == 0:
                        quedo = _texto(p, 4)                                 # get_CurrentValue
                        if quedo == texto:
                            return _ok({"hecho": que, "comprobado": "el campo tiene ese texto"},
                                       confirmacion="permitida")
                if not _traer_delante(hwnd):
                    return no_delante
                _metodo(objetivo["_e"], 3)()                                 # SetFocus
                _escribir_teclas(texto, hwnd)
                return _ok({"hecho": que, "comprobado": "se escribió con el teclado"}, confirmacion="permitida")
            if not _traer_delante(hwnd):
                return no_delante
            _pulsar(combinacion, hwnd)
            return _ok({"hecho": que, "comprobado": "se pulsó"}, confirmacion="permitida")
        except _Cruzada:
            return _no("cruzada", "Otra ventana se puso delante: se ha parado sin terminar.",
                       confirmacion="permitida")
    finally:
        sesion.cerrar()


INTERFAZ = {"ui_read": ui_read, "ui_control": ui_control}
