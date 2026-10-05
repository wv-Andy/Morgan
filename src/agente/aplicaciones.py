"""
Abrir y cerrar aplicaciones (4.8, mi lista: «Abre VS Code», «abre este PDF», «abre
Chrome y ve a esta página», «abre la carpeta»; cerrar y reiniciar).

## Abrir (`open_app`, 🟢, apagada al nacer)

Abrir no cambia archivos, así que no pide plan (permisos aprobados por mí, plan-4.x.md);
queda en la auditoría, y con `confirmar abrir si` se pregunta en el PC. Pero abrir es
**lanzar programas**, así que:

- **Una aplicación, solo de las instaladas**: los accesos del menú Inicio (los de todos y los
  de la persona) y, desde la 5.3, **el catálogo de apps de Windows** (`shell:AppsFolder`, lo
  mismo que enseña el menú Inicio): las de la Store y las integradas (Calculadora, Fotos,
  Spotify, Teams…), que no tienen acceso `.lnk`, y los juegos de Steam y Epic. Medido en mi PC:
  con solo los accesos, 79 de 141 apps no se podían abrir. Del catálogo **no** entra nada del
  sistema salvo las ayudas de accesibilidad y el mapa de caracteres (`del_catalogo`): ni las
  herramientas de administración, ni el Panel de control, ni «Ejecutar», ni los enlaces a
  páginas. Nunca un ejecutable por su ruta, ni lo que da una consola (`cmd`, PowerShell,
  Terminal, WSL, el editor del registro…: `terminal.NUNCA`), ni un acceso que apunte a un
  script. Con un argumento, solo **una ruta de las carpetas permitidas** (abrir el proyecto
  en VS Code), y solo a las de un acceso: las del catálogo se abren solas.
- **Un archivo**, de las carpetas permitidas, con su programa de siempre, **nunca uno que
  se ejecute** (`.exe`, `.bat`, `.ps1`, `.lnk`…: `es_ejecutable`).
- **Una página**, `http` o `https` y sin usuario ni contraseña en la dirección. La nube,
  además, no deja abrir tras leer el PC una dirección que nadie dio (3.1-B, `fuga`).
- **Una carpeta** permitida, en el Explorador.

## Cerrar (`close_app`, 🔴, plan y «Permitir»)

Como `kill_process`, pero **con buenos modales**: se le pide a sus ventanas que se cierren
(lo mismo que la X), y si una pregunta «¿guardar los cambios?», se queda ahí y se dice. Solo
con `forzar` se termina. Mismas protecciones: pid y nombre tienen que coincidir, nada del
sistema ni de Morgan, solo procesos de la persona.
"""

import os
import re
import struct
import sys
import unicodedata
from pathlib import Path

from src.agente import control, motor
from src.agente.politica import es_ejecutable

#: Lo que nunca se abre como aplicación, además de `terminal.NUNCA`: por su nombre en el menú
#: Inicio, que es lo que la persona y el modelo ven.
#: Medido en mi PC (95 accesos): además de las obvias, Git CMD, las de MSYS2, Python,
#: IDLE y Node.js abren una consola o un intérprete.
NOMBRES_PROHIBIDOS = ("simbolo del sistema", "command prompt", "powershell", "terminal", "windows terminal",
                      "editor del registro", "registry editor", "wsl", "ubuntu", "bash", "ejecutar",
                      "run", "developer command prompt", "developer powershell", "x64 native tools",
                      "x86 native tools", "git cmd", "msys2", "mingw", "cygwin", "idle", "pydoc", "python",
                      "node.js", "uninst", "desinstal", "uninstall", "morgan")
#: Carpetas del menú Inicio cuyo contenido no se abre: las herramientas de administración
#: del sistema (el Programador de tareas, los servicios, el diagnóstico de memoria, que
#: reinicia el PC…) y lo que arranca con la sesión (ahí está el propio agente).
CARPETAS_PROHIBIDAS = {"administrative tools", "windows tools", "system tools", "windows administrative tools",
                       "herramientas administrativas", "herramientas de windows", "herramientas del sistema",
                       "startup", "inicio"}
#: Destinos que abren una consola o un intérprete, aunque su acceso se llame de otra forma.
INTERPRETES = {"python", "pythonw", "py", "pyw", "node", "git-cmd", "git-bash", "mintty", "msys2", "mingw32",
               "mingw64", "ucrt64", "clang64", "clangarm64", "conhost", "wt", "mmc", "bash", "sh"}
#: Destinos de un acceso que nunca se lanzan: los que ejecutan texto.
EXTENSIONES_PROHIBIDAS = {"bat", "cmd", "ps1", "vbs", "vbe", "js", "jse", "wsf", "wsh", "hta", "msc",
                          "py", "pyw", "reg", "scr", "com", "pif"}
MAX_LISTA = 60


def _ok(datos, **auditoria) -> dict:
    return {"success": True, "data": datos, "error": None, "_auditoria": auditoria}


def _no(motivo: str, mensaje: str) -> dict:
    return {"success": False, "data": None, "error": mensaje, "motivo": motivo}


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


# --- Las aplicaciones instaladas ---

def carpetas_del_menu() -> list[Path]:
    carpetas = []
    for variable in ("ProgramData", "APPDATA"):
        base = os.environ.get(variable)
        if base:
            carpetas.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return carpetas


def destino_del_acceso(lnk: Path) -> str | None:
    """A qué apunta un `.lnk` (MS-SHLLINK: `LinkInfo.LocalBasePath`), sin COM. None si no
    se sabe; entonces decide el nombre."""
    try:
        datos = lnk.read_bytes()[:65536]
        if len(datos) < 0x4C or struct.unpack_from("<I", datos, 0)[0] != 0x4C:
            return None
        banderas = struct.unpack_from("<I", datos, 0x14)[0]
        pos = 0x4C
        if banderas & 0x01:                                   # HasLinkTargetIDList
            pos += 2 + struct.unpack_from("<H", datos, pos)[0]
        if not banderas & 0x02:                               # HasLinkInfo
            return None
        inicio = pos
        cabecera = struct.unpack_from("<I", datos, inicio + 4)[0]
        if cabecera >= 0x24:                                  # la ruta en Unicode
            desp = struct.unpack_from("<I", datos, inicio + 0x1C)[0]
            if desp:
                fin = datos.find(b"\x00\x00", inicio + desp)
                while fin != -1 and (fin - inicio - desp) % 2:
                    fin = datos.find(b"\x00\x00", fin + 1)
                return datos[inicio + desp:fin].decode("utf-16-le", errors="ignore") or None
        desp = struct.unpack_from("<I", datos, inicio + 0x10)[0]
        fin = datos.find(b"\x00", inicio + desp)
        return datos[inicio + desp:fin].decode("mbcs" if sys.platform == "win32" else "latin-1",
                                               errors="ignore") or None
    except (OSError, struct.error, LookupError):
        return None


def _prohibida(nombre: str, destino: str | None) -> str | None:
    """Por qué no se abre esta aplicación, o None."""
    from src.agente.terminal import NUNCA

    limpio = _sin_tildes(nombre)
    if any(re.search(rf"\b{re.escape(p)}\b", limpio) for p in NOMBRES_PROHIBIDOS):
        return "Es una consola o una herramienta del sistema: Morgan no la abre."
    if destino:
        base = Path(destino.replace("\\", "/")).name.lower()
        raiz, _, ext = base.rpartition(".")
        if (raiz or base) in NUNCA or (raiz or base) in INTERPRETES or ext in EXTENSIONES_PROHIBIDAS:
            return "Ese acceso lanza una consola o un script: Morgan no lo abre."
    return None


def instaladas() -> list[dict]:
    """Los accesos del menú Inicio, sin repetidos, sin desinstaladores y sin los de las
    carpetas de herramientas del sistema (`CARPETAS_PROHIBIDAS`)."""
    vistas, salida = set(), []
    for carpeta in carpetas_del_menu():
        if not carpeta.is_dir():
            continue
        for lnk in sorted(carpeta.rglob("*.lnk")):
            nombre = lnk.stem
            partes = {_sin_tildes(x) for x in lnk.relative_to(carpeta).parts[:-1]}
            if partes & CARPETAS_PROHIBIDAS or _sin_tildes(nombre) in CARPETAS_PROHIBIDAS:
                continue
            clave = _sin_tildes(nombre)
            if clave in vistas or re.search(r"\b(uninstall|desinstalar)\b", clave):
                continue
            vistas.add(clave)
            salida.append({"nombre": nombre, "acceso": lnk})
    return salida


# --- El catálogo de apps de Windows (5.3) ---

#: Una app empaquetada (la Store o las integradas): «Familia_editor!App».
_EMPAQUETADA = re.compile(r"^([\w.\-]+)_[a-z0-9]{13}![\w.\-]+$", re.I)
#: Los juegos que se lanzan por su tienda: Steam y Epic. Ningún otro protocolo (un manejador
#: de protocolo puede hacer cualquier cosa con lo que se le pase).
_JUEGO = re.compile(r"^(steam://rungameid/\d+|com\.epicgames\.launcher://apps/[\w%:.\-]+\?action=launch&silent=true)$",
                    re.I)
#: Familias empaquetadas que dan una consola o un intérprete, por si su nombre no lo dice.
FAMILIAS_PROHIBIDAS = ("microsoft.windowsterminal", "microsoft.powershell", "canonicalgrouplimited",
                       "microsoftcorporationii.windowssubsystemforlinux", "pythonsoftwarefoundation",
                       "microsoft.desktopappinstaller",
                       # Power Automate ejecuta flujos y scripts: es una forma de lanzar cualquier cosa.
                       "microsoft.powerautomatedesktop")
#: Las carpetas del sistema tal como las nombra el catálogo (System32, SysWOW64, Windows).
_CARPETAS_DEL_SISTEMA = {"{1ac14e77-02e7-4e5d-b744-2eb1ae5198b7}", "{d65231b0-b2f1-4857-a4ce-a8e7c6ea7d27}",
                         "{f38bf404-1d43-42f2-9305-67de0b28fc23}"}
#: Del sistema, solo estas: las ayudas de accesibilidad (lupa, narrador, acceso por voz, teclado
#: en pantalla, subtítulos) y el mapa de caracteres. Las demás
#: (servicios, el Programador de tareas, msconfig, el diagnóstico de memoria que reinicia el
#: PC, el liberador de espacio que borra…) son de administración.
DEL_SISTEMA_PERMITIDAS = {"charmap", "magnify", "narrator", "voiceaccess", "osk", "livecaptions"}
#: Del propio Windows sin ruta, solo el Explorador. Ni el Panel de control, ni «Ejecutar», ni
#: las herramientas de administración (`Microsoft.AutoGenerated.*`, los complementos de MMC).
DEL_SHELL_PERMITIDAS = {"microsoft.windows.explorer"}


def del_catalogo(nombre: str, id_app: str) -> str | None:
    """Por qué una app del catálogo de Windows no se abre, o None si se puede."""
    if _prohibida(nombre, None):
        return _prohibida(nombre, None)
    ident = (id_app or "").strip()
    empaquetada = _EMPAQUETADA.match(ident)
    if empaquetada:
        if empaquetada.group(1).lower().startswith(FAMILIAS_PROHIBIDAS):
            return "Es una consola o un intérprete: Morgan no la abre."
        return None
    if _JUEGO.match(ident):
        return None
    if ident.lower() in DEL_SHELL_PERMITIDAS:
        return None
    if ident.startswith("{") and "}" in ident:
        carpeta, _, resto = ident.partition("}")
        ruta = Path(resto.lstrip("\\/").replace("\\", "/"))
        if f"{carpeta}}}".lower() in _CARPETAS_DEL_SISTEMA:
            if ruta.suffix.lower() == ".exe" and ruta.stem.lower() in DEL_SISTEMA_PERMITIDAS:
                return None
            return "Es una herramienta del sistema: Morgan no la abre."
        if ruta.suffix.lower() == ".exe":
            return _prohibida(nombre, str(ruta))
    return "No es una aplicación que Morgan pueda abrir."


def catalogo_de_windows() -> list[dict]:
    """Las apps del catálogo de Windows (`shell:AppsFolder`): [{nombre, id}]. Por COM con
    ctypes, como UI Automation (`interfaz.py`): sin PowerShell ni dependencias. Si algo falla,
    lista vacía: quedan los accesos del menú."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import POINTER, byref, c_ulong, c_void_p

    from src.agente.interfaz import _guid, _metodo, _soltar

    ole32 = ctypes.windll.ole32
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    iniciado = ole32.CoInitializeEx(None, 0x2) in (0, 1)
    salida, carpeta, lista = [], c_void_p(), c_void_p()
    try:
        # SHGetKnownFolderItem(FOLDERID_AppsFolder) → IShellItem; BindToHandler(BHID_EnumItems)
        # → IEnumShellItems. IShellItem: 3 BindToHandler, 5 GetDisplayName. IEnumShellItems: 3 Next.
        if ctypes.windll.shell32.SHGetKnownFolderItem(
                byref(_guid("{1e87508d-89c2-42f0-8a7e-645a0f50ca58}")), 0, None,
                byref(_guid("{43826d1e-e718-42ee-bc55-a1e261c37bfe}")), byref(carpeta)) != 0:
            return []
        _metodo(carpeta, 3, c_void_p, c_void_p, c_void_p, POINTER(c_void_p))(
            None, byref(_guid("{94f60519-2850-4924-aa5a-d15e84868039}")),
            byref(_guid("{70629033-e363-4a28-a567-0db78006e6d7}")), byref(lista))
        siguiente = _metodo(lista, 3, c_ulong, POINTER(c_void_p), POINTER(c_ulong))
        while len(salida) < 2000:
            app, cuantas = c_void_p(), c_ulong()
            siguiente(1, byref(app), byref(cuantas))
            if not cuantas.value:
                break
            try:
                textos = []
                for forma in (0, 0x80018001):          # SIGDN_NORMALDISPLAY, _PARENTRELATIVEPARSING
                    texto = c_void_p()
                    _metodo(app, 5, c_ulong, POINTER(c_void_p))(forma, byref(texto))
                    textos.append(ctypes.wstring_at(texto.value) if texto.value else "")
                    ole32.CoTaskMemFree(texto)
                if textos[0] and textos[1]:
                    salida.append({"nombre": textos[0], "id": textos[1]})
            except OSError:
                pass
            finally:
                _soltar(app)
    except OSError:
        return salida
    finally:
        _soltar(lista)
        _soltar(carpeta)
        if iniciado:
            ole32.CoUninitialize()
    return salida


def todas() -> list[dict]:
    """Los accesos del menú y, de lo que no tiene acceso, lo del catálogo que se puede abrir.
    Si una app está en los dos, gana el acceso: se sabe a qué apunta y admite una ruta."""
    salida = instaladas()
    vistas = {_sin_tildes(a["nombre"]) for a in salida}
    for app in catalogo_de_windows():
        clave = _sin_tildes(app["nombre"])
        if clave in vistas or del_catalogo(app["nombre"], app["id"]):
            continue
        vistas.add(clave)
        salida.append({"nombre": app["nombre"], "id": app["id"]})
    return salida


def _motivo(app: dict) -> str | None:
    """Por qué no se abre, sea un acceso o una del catálogo."""
    if "acceso" in app:
        return _prohibida(app["nombre"], destino_del_acceso(app["acceso"]))
    return del_catalogo(app["nombre"], app["id"])


def _buscar(nombre: str) -> list[dict]:
    palabras = _sin_tildes(nombre).split()
    if not palabras:
        return []
    candidatas = [a for a in todas() if all(p in _sin_tildes(a["nombre"]) for p in palabras)]
    exacta = [a for a in candidatas if _sin_tildes(a["nombre"]) == " ".join(palabras)]
    return exacta or candidatas


def _abrir(que, argumentos: str = "") -> None:
    if sys.platform != "win32":
        raise OSError("Abrir aplicaciones es de Windows.")
    control.sin_vuelta()
    os.startfile(str(que), "open", argumentos) if argumentos else os.startfile(str(que))


def _confirmar(que: str, detalle: str) -> dict | None:
    """Si la persona pidió confirmar al abrir (`confirmar abrir si`), se pregunta en el PC."""
    from src.agente import aviso

    respuesta = aviso.preguntar(f"Morgan quiere {que}", detalle)
    control.punto_seguro()
    if respuesta != aviso.PERMITIDA:
        return {**_no("no_confirmada", "La persona no lo permitió en su PC (o no contestó a tiempo)."),
                "_auditoria": {"confirmacion": respuesta}}
    return None


def open_app(accion: str = "", nombre: str = "", path: str = "", url: str = "", **_) -> dict:
    """accion: listar | aplicacion (nombre, y path opcional) | archivo (path) | url (url) |
    carpeta (path)."""
    accion = str(accion or "").lower()
    if accion in ("archivo", "carpeta") and not str(path or "").strip():
        return _no("argumentos", "Falta la ruta (path).")
    ruta_de = {"archivo": path, "carpeta": path, "aplicacion": path or None}.get(accion)
    decision = motor.evaluar("abrir", ruta_de or None, archivo={"archivo": True, "carpeta": False}.get(accion))
    if not decision:
        return _no(decision.motivo, decision.mensaje)

    if accion == "listar":
        # Las prohibidas no se enseñan: que el modelo no las proponga.
        lista = [a["nombre"] for a in (_buscar(nombre) if nombre else todas()) if not _motivo(a)]
        return _ok({"aplicaciones": lista[:MAX_LISTA], "total": len(lista),
                    "is_truncated": len(lista) > MAX_LISTA})

    if accion == "aplicacion":
        encontradas = _buscar(nombre)
        if not encontradas:
            return _no("no_instalada", f"No encuentro «{nombre}» entre las aplicaciones instaladas "
                                       "(accion=listar las enseña).")
        # Primero se descartan las prohibidas, y luego se pregunta cuál. Medido con el modelo
        # real: «abre PowerShell» encontraba cuatro, contestaba «¿cuál prefieres?» y el modelo
        # se lo preguntaba a la persona, como si alguna se pudiera abrir.
        motivos = [_motivo(a) for a in encontradas]
        permitidas = [a for a, m in zip(encontradas, motivos) if not m]
        if not permitidas:
            return _no("prohibida", next(m for m in motivos if m))
        if len(permitidas) > 1:
            return _no("ambigua", "Hay varias: " + ", ".join(a["nombre"] for a in permitidas[:8])
                       + ". Di cuál.")
        app = permitidas[0]
        argumento = ""
        if path and "acceso" not in app:
            return _no("argumentos", f"«{app['nombre']}» se abre sola, sin una ruta: para abrir un archivo con "
                                     "su programa de siempre, accion=archivo.")
        if path:
            if es_ejecutable(decision.ruta.name):
                return _no("ejecutable", "Eso es un programa o un script: no se le pasa a otra aplicación.")
            argumento = f'"{decision.ruta}"'
        if decision.exige_confirmacion:
            no = _confirmar(f"ABRIR {app['nombre']}", str(decision.ruta or ""))
            if no:
                return no
        # Las del catálogo, por su identificador: lo mismo que pulsarla en el menú Inicio.
        _abrir(app["acceso"] if "acceso" in app else f"shell:AppsFolder\\{app['id']}", argumento)
        return _ok({"abierta": app["nombre"], "con": str(decision.ruta) if path else None},
                   confirmacion="no_hacia_falta" if not decision.exige_confirmacion else "permitida")

    if accion == "archivo":
        if es_ejecutable(decision.ruta.name):
            return _no("ejecutable", "Eso es un programa o un script: Morgan no lo abre.")
        if decision.exige_confirmacion and (no := _confirmar("ABRIR un archivo", str(decision.ruta))):
            return no
        _abrir(decision.ruta)
        return _ok({"abierto": str(decision.ruta)})

    if accion == "carpeta":
        if decision.exige_confirmacion and (no := _confirmar("ABRIR una carpeta", str(decision.ruta))):
            return no
        _abrir(decision.ruta)
        return _ok({"abierta": str(decision.ruta)})

    if accion == "url":
        from urllib.parse import urlparse

        partes = urlparse(str(url or "").strip())
        if partes.scheme not in ("http", "https") or not partes.hostname:
            return _no("url", "Solo direcciones http o https.")
        if partes.username or partes.password or any(c in url for c in '\r\n"<>'):
            return _no("url", "Esa dirección lleva datos que no se abren (usuario, contraseña o caracteres raros).")
        if decision.exige_confirmacion and (no := _confirmar("ABRIR una página", url)):
            return no
        _abrir(partes.geturl())
        return _ok({"abierta": partes.geturl()})

    return _no("argumentos", "accion tiene que ser listar, aplicacion, archivo, url o carpeta.")


# --- Cerrar ---

def _ventanas_de(pid: int) -> list[int]:
    """Las ventanas visibles de primer nivel de un proceso."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ventanas: list[int] = []
    TIPO = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def una(hwnd, _):
        propio = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(propio))
        if propio.value == pid and user32.IsWindowVisible(hwnd):
            ventanas.append(hwnd)
        return True

    user32.EnumWindows(TIPO(una), 0)
    return ventanas


def close_app(pid: int | None = None, name: str = "", forzar: bool = False, **_) -> dict:
    """Cierra un programa de la persona como lo haría la X de su ventana; con `forzar`, lo
    termina si no se cierra."""
    import psutil
    from src.agente.escritura import _autorizar
    from src.agente.terminal import PROTEGIDOS, _es_de_la_persona, _es_de_morgan

    try:
        proceso = psutil.Process(int(pid))
        nombre_real = proceso.name()
        nacido = proceso.create_time()
    except (psutil.Error, ValueError, TypeError):
        return _no("no_existe", "No hay ningún proceso con ese pid.")
    if not name or nombre_real.lower() != str(name).lower():
        return _no("no_coincide", f"El pid {pid} es «{nombre_real}», no «{name}»: no se toca.")
    if nombre_real.lower() in PROTEGIDOS or _es_de_morgan(proceso):
        return _no("protegido", "Es del sistema o de Morgan: no se cierra.")
    if not _es_de_la_persona(proceso):
        return _no("ajeno", "No es un proceso tuyo: no se cierra.")

    reales, auditoria = _autorizar("close_app", f"CERRAR «{nombre_real}»" + (" (forzando si hace falta)" if forzar else ""),
                                   [(None, {})], detalle=f"{nombre_real} · pid {pid}")
    if reales is None:
        return auditoria
    try:
        if proceso.create_time() != nacido:
            return {**_no("cambiado", "El proceso cambió mientras se decidía: no se toca."), "_auditoria": auditoria}
    except psutil.Error:
        return {"success": True, "data": {"pid": pid, "nombre": nombre_real, "ya_no_estaba": True},
                "error": None, "_auditoria": auditoria}

    control.sin_vuelta()
    import ctypes

    WM_CLOSE = 0x0010
    for hwnd in _ventanas_de(int(pid)):
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        proceso.wait(timeout=8)
        return {"success": True, "data": {"pid": pid, "nombre": nombre_real, "forzado": False,
                                          "comprobado": "el programa se cerró"}, "error": None, "_auditoria": auditoria}
    except psutil.TimeoutExpired:
        pass
    if not forzar:
        return {**_no("sigue_abierto", f"«{nombre_real}» no se ha cerrado: quizá pregunta si guardar los "
                                       "cambios en su ventana. Con forzar se termina, perdiendo lo no guardado."),
                "_auditoria": auditoria}
    proceso.kill()
    try:
        proceso.wait(timeout=3)
    except psutil.TimeoutExpired:
        return {**_no("sigue_vivo", "No se ha cerrado ni forzándolo."), "_auditoria": auditoria}
    return {"success": True, "data": {"pid": pid, "nombre": nombre_real, "forzado": True,
                                      "comprobado": "el proceso ya no existe"}, "error": None, "_auditoria": auditoria}


APLICACIONES = {"open_app": open_app, "close_app": close_app}
