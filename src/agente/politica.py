"""
La política local del agente (3.0-E): qué deja tocar este PC, decidido en este PC.

**Es una autoridad local** (§15-§16 del plan). Vive en `politica.json`, en la carpeta
del agente; se cambia solo con `python -m src.agente carpetas …` en el propio PC, y **el
protocolo no tiene ningún mensaje para cambiarla**: una nube comprometida no puede
quitarse sus propias restricciones.

Lo que decide, en la 3.0 (contrato §11; la 3.1 y la 3.2 la formalizan y la auditan):

- **Carpetas permitidas: ninguna por defecto.** Nace restrictiva (decisión 7: gente
  que no conozco). Sin carpetas, el agente conecta pero no ofrece leer archivos.
- **Pero lo que se permite lo decide cada persona en su PC, sin topes** (decisión
  mía, 2026-09-19): un disco entero, la carpeta de usuario o las del sistema. La primera
  versión lo prohibía; quise acceso total a su equipo salvo las credenciales, y es
  su PC. Lo que sigue sin leerse nunca, se permita lo que se permita, está abajo.
- **Se comprueba el objeto real**, no la cadena (§18): la ruta se resuelve entera y tiene
  que quedar dentro de una carpeta permitida. `C:\\Permitida\\..\\Otra` no cuela.
- **Ningún punto de reanálisis** en el camino (symlink, junction): cualquiera se
  rechaza, porque es la forma clásica de que una carpeta permitida apunte fuera.
- **Nada de rutas de red ni de dispositivo** (`\\\\servidor\\…`, `\\\\?\\…`, `\\\\.\\…`).
- **Archivos sensibles**, rechazados por nombre: `.env`, claves, almacenes de
  credenciales. Lista cerrada aquí; clasificación de verdad en la 3.1.
- **La carpeta del propio agente**, nunca: guarda su credencial y su auditoría. Con un
  disco entero permitido, sin esto Morgan podría pedirle que le leyera la credencial con
  la que habla con la nube.
- **Lecturas acotadas**: 256 KB como mucho, y los binarios no se leen.
"""

import fnmatch
import json
import logging
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.agente import estado as almacen

logger = logging.getLogger(__name__)

#: Lo más que sale del PC en una lectura (§19: leer es sacar datos).
MAX_BYTES = 256 * 1024

#: Nombres de archivo que no se leen nunca, aunque estén en una carpeta permitida.
SENSIBLES = (
    ".env", ".env.*", "*.env",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    "*.pem", "*.key", "*.pfx", "*.p12", "*.kdbx", "*.keychain", "*.ppk",
    ".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc",
    "credentials", "credentials.*", "*.credentials",
    "Login Data", "Cookies", "Web Data", "key3.db", "key4.db", "logins.json",
    # La clave con la que Chrome y Edge cifran las contraseñas guardadas.
    "Local State",
)

#: Carpetas que no se atraviesan nunca: guardan credenciales de herramientas.
CARPETAS_SENSIBLES = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config",
    # Las credenciales de Windows (%APPDATA%\Microsoft\Credentials) y las claves
    # maestras de DPAPI (…\Protect): con un disco entero permitido, quedarían al alcance.
    "credentials", "protect",
}

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class Denegado(PermissionError):
    """La política local no lo permite. `motivo` es corto y estable (va a la auditoría)."""

    def __init__(self, motivo: str, mensaje: str):
        self.motivo = motivo
        super().__init__(mensaje)


def _clave(ruta: Path | str) -> str:
    """Para comparar rutas en Windows: sin distinguir mayúsculas y con las barras iguales."""
    return os.path.normcase(os.path.normpath(str(ruta)))


def _es_reanalisis(ruta: Path) -> bool:
    """Si esta entrada (sin seguirla) es un symlink, un junction u otro punto de reanálisis."""
    try:
        info = os.lstat(ruta)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def nombres_del_archivo(ruta: Path) -> list[Path]:
    """Todos los nombres del **mismo** archivo, los enlaces duros incluidos (3.1-D).

    Un enlace duro no es un enlace que se siga: es **otro nombre del mismo archivo**, así
    que `resolve()` no lo deshace y un nombre inocente dentro de una carpeta permitida
    puede ser el `.env` de al lado. Medido en mi PC: `inocente.txt` enlazado a
    `.env` se leía entero.

    Windows sabe enumerarlos (`FindFirstFileNameW`), y los devuelve sin unidad: se les
    pone la de la ruta. Si no se puede enumerar, se devuelve la ruta tal cual y deciden
    las demás comprobaciones.
    """
    try:
        if sys.platform != "win32" or os.stat(ruta).st_nlink <= 1:
            return [ruta]
    except OSError:
        return [ruta]

    import ctypes
    from ctypes import wintypes

    k = ctypes.windll.kernel32
    k.FindFirstFileNameW.restype = wintypes.HANDLE
    k.FindFirstFileNameW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
    k.FindNextFileNameW.restype = wintypes.BOOL
    k.FindNextFileNameW.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
    k.FindClose.argtypes = [wintypes.HANDLE]

    unidad = ruta.drive or str(ruta.anchor).rstrip("\\/")
    largo = 4096
    buf = ctypes.create_unicode_buffer(largo)
    cuantos = wintypes.DWORD(largo)
    mango = k.FindFirstFileNameW(str(ruta), 0, ctypes.byref(cuantos), buf)
    if mango in (0, -1, 2**64 - 1):
        return [ruta]
    nombres = [Path(unidad + buf.value)]
    try:
        while True:
            cuantos = wintypes.DWORD(largo)
            if not k.FindNextFileNameW(mango, ctypes.byref(cuantos), buf):
                break
            nombres.append(Path(unidad + buf.value))
    finally:
        k.FindClose(mango)
    return nombres


def _es_sensible(nombre: str) -> bool:
    bajo = nombre.lower()
    return any(fnmatch.fnmatch(bajo, patron.lower()) for patron in SENSIBLES)


def _dentro_del_agente(ruta: Path) -> bool:
    """Si la ruta está en la carpeta del propio agente (su credencial, su auditoría)."""
    try:
        propia = _clave(almacen.carpeta().resolve())
    except OSError:
        return False
    clave = _clave(ruta)
    return clave == propia or clave.startswith(propia.rstrip("\\/") + os.sep)


#: Las capacidades que la persona puede encender y apagar una a una (3.2, decisión
#: mía). `estado` no está: es la que dice quién es el agente y siempre responde.
DE_LECTURA = ("list_files", "read_file", "search_files", "copy_file", "system_info",
              # Los metadatos de un archivo o carpeta (4.6): leer, encendida al nacer.
              "file_info",
              # Los proyectos y los editores (4.13): solo dentro de las carpetas permitidas, lo
              # mismo que ya encuentra search_files, así que también nace encendida.
              "pc_context")
#: Las que cambian cosas en el PC (3.3). **Nacen apagadas**: Morgan es para cualquiera
#: que se registre (decisión 7), y a nadie se le cambia nada en su PC sin haberlo
#: encendido allí, una a una.
#: `append_file` (4.1, decisión mía): añadir al final, también apagada al nacer.
DE_ESCRITURA = ("create_file", "edit_file", "append_file", "create_folder", "move_file", "delete_file",
                # Copiar dentro del PC y comprimir o descomprimir (4.6), apagadas al nacer.
                "copy_path", "compress")
#: La terminal y los procesos (3.5). También **nacen apagadas**; y de la terminal, además,
#: cada programa del catálogo se enciende aparte (`programas`).
DE_EJECUCION = ("run_command", "run_change_command", "get_processes", "kill_process")
#: El PC por dentro (4.7): rendimiento, red, puertos y servicios. Solo lee, pero dice más de
#: lo que hay en el PC que `system_info`: **nace apagada**, como las de la terminal.
DE_DIAGNOSTICO = ("pc_diagnostics",)
#: Abrir y cerrar aplicaciones (4.8). Abrir lanza programas: **nace apagada**, como cerrar.
DE_APLICACIONES = ("open_app", "close_app")
#: Arrancar y parar servicios de Windows (4.9): cambia, **nace apagada**.
DE_SERVICIOS = ("service_control",)
#: El portapapeles y los avisos (4.10). **Nacen apagadas**; leer el portapapeles, además,
#: pide «Permitir» cada vez.
DE_AVISOS = ("clipboard", "notify")
#: Ventanas y capturas (4.11). **Nacen apagadas**; una captura, además, pide «Permitir» cada vez.
DE_PANTALLA = ("windows", "screenshot")
#: El control de la interfaz (4.12): leer los controles y manejar ratón y teclado. **Nacen
#: apagadas**; manejar, además, con plan y «Permitir» al empezarlo.
DE_INTERFAZ = ("ui_read", "ui_control")
CAPACIDADES = (DE_LECTURA + DE_ESCRITURA + DE_EJECUCION + DE_DIAGNOSTICO + DE_APLICACIONES
               + DE_SERVICIOS + DE_AVISOS + DE_PANTALLA + DE_INTERFAZ)
ENCENDIDA_POR_DEFECTO = {c: c in DE_LECTURA for c in CAPACIDADES}

#: Extensiones que no se crean, no se editan y no se dan a un archivo al renombrarlo,
#: **en ninguna carpeta** (3.3, decisión mía). Crear uno es la puerta a ejecutar, y
#: ejecutar es la 3.5, con sus propias reglas. Cuenta la última extensión, que es la que
#: mira Windows: `factura.pdf.exe` es un `.exe`.
EJECUTABLES = frozenset({
    "exe", "com", "scr", "pif", "cpl", "dll", "sys", "drv", "ocx", "msi", "msp", "msc",
    "bat", "cmd", "ps1", "psm1", "psd1", "ps1xml", "vbs", "vbe", "js", "jse", "wsf",
    "wsh", "hta", "jar", "lnk", "url", "scf", "reg", "inf", "application", "appref-ms",
    "gadget", "settingcontent-ms", "library-ms", "search-ms", "xll", "iqy", "slk",
    # Los scripts, desde la 3.5 (decisión mía): escribir uno y que un intérprete lo
    # ejecute sería ejecutar cualquier cosa.
    "py", "pyw", "pyc", "pyz", "mjs", "cjs", "ts", "sh", "bash", "zsh", "rb",
    "pl", "php", "lua", "r", "ahk", "au3", "kix", "applescript",
})
#: Configuración que **ejecuta** programas (3.5): la de git lanza `core.fsmonitor` en cada
#: `git status`. No se crea ni se edita, y dentro de una carpeta `.git` no se escribe nada
#: (sus *hooks* no tienen extensión y corren con cada `commit`).
CONFIGURACION_QUE_EJECUTA = frozenset({".gitconfig", ".gitmodules"})
#: Nombres que Windows **interpreta** al abrir la carpeta: `desktop.ini` puede apuntar el
#: icono a un servidor de fuera y filtrar la contraseña de Windows al mirarla.
NOMBRES_QUE_WINDOWS_INTERPRETA = frozenset({"desktop.ini", "autorun.inf", "folder.htt"})
#: Nombres de dispositivo: `NUL.txt` o `CON` no son archivos en Windows.
DISPOSITIVOS = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                          *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))})

#: Carpetas de Inicio de Windows, por su identificador: lo que se deja ahí se ejecuta
#: solo al iniciar sesión.
INICIO = ("{B97D20BB-F46A-4C97-BA10-5E3608430854}", "{82A5EA35-D9CD-47C5-9629-E15D2F714E6E}")
#: Donde no se escribe **nunca**, se permita lo que se permita (3.3): el sistema, los
#: programas y los datos de las aplicaciones (de ahí arrancan programas solos y ahí
#: guardan su configuración). La carpeta del agente y las de Inicio se añaden aparte.
VARIABLES_DE_SISTEMA = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                        "ProgramW6432", "ProgramData", "APPDATA", "LOCALAPPDATA")


def carpeta_conocida(guid: str) -> str | None:
    """Dónde tiene Windows una carpeta especial (KNOWNFOLDERID), o None."""
    if sys.platform != "win32":
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(guid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2],
             (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    ruta = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ruta)) != 0:
        return None
    try:
        return ruta.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ruta)


def zonas_prohibidas() -> list[str]:
    """Las carpetas donde Morgan no escribe nunca, como claves para comparar."""
    candidatas = [str(almacen.carpeta())]
    candidatas += [os.environ[v] for v in VARIABLES_DE_SISTEMA if os.environ.get(v)]
    candidatas += [c for c in (carpeta_conocida(g) for g in INICIO) if c]
    zonas = []
    for c in candidatas:
        try:
            zonas.append(_clave(Path(c).resolve()))
        except OSError:
            zonas.append(_clave(c))
    return zonas


def en_zona_prohibida(ruta: Path) -> bool:
    clave = _clave(ruta)
    return any(clave == z or clave.startswith(z.rstrip("\\/") + os.sep) for z in zonas_prohibidas())


def es_ejecutable(nombre: str) -> bool:
    return "." in nombre and nombre.rsplit(".", 1)[1].lower() in EJECUTABLES


def nombre_para_escribir(nombre: str) -> None:
    """Si con este nombre se puede crear algo; si no, `Denegado` con el porqué (3.3)."""
    from src.agente.salida import _BIDI

    if not nombre or nombre in (".", "..") or len(nombre) > 255:
        raise Denegado("nombre", "Ese nombre no vale.")
    if any(c < " " or c == "\x7f" or c in _BIDI or c in '<>:"/\\|?*' for c in nombre):
        # Las marcas bidi, además, sirven para disfrazar la extensión: `fdp.exe` escrito
        # al revés se ve como un PDF.
        raise Denegado("nombre", "Ese nombre lleva caracteres que no se permiten.")
    if nombre[-1] in ". ":
        # Windows quita los puntos y espacios finales: `nota.txt.` sería otro archivo.
        raise Denegado("nombre", "Un nombre no puede acabar en punto ni en espacio.")
    if nombre.split(".")[0].strip().lower() in DISPOSITIVOS:
        raise Denegado("nombre", "Ese nombre es de un dispositivo de Windows.")
    if es_ejecutable(nombre):
        raise Denegado("ejecutable", "Morgan no crea ni cambia programas ni scripts.")
    if nombre.lower() in CONFIGURACION_QUE_EJECUTA:
        raise Denegado("ejecutable", "Esa configuración puede lanzar programas: Morgan no la toca.")
    if nombre.lower() in NOMBRES_QUE_WINDOWS_INTERPRETA:
        raise Denegado("nombre", "Windows interpreta ese archivo al abrir la carpeta: no se crea.")
    if _es_sensible(nombre):
        raise Denegado("sensible", "Ese nombre es de un archivo de credenciales: no se toca.")

#: Los topes del código. La persona puede **bajarlos**, nunca subirlos (decisión
#: mía): quien quiera sacar menos de su PC puede; nadie amplía lo que sale editando un
#: fichero. `MAX_BYTES` vive arriba; el de la copia, en el protocolo.
def _tope_copia() -> int:
    from src.agente.protocolo import MAX_COPIA

    return MAX_COPIA


#: Qué operaciones exigen que la persona confirme **en su PC**, con una notificación de
#: Windows con «Permitir» y «Rechazar» (decidido en la 3.2).
#:
#: **Solo borrar** (3.3, decisión mía): él usa Morgan desde el móvil, lejos del PC,
#: y todo lo que exija la notificación no se puede hacer si no está delante. Crear,
#: editar y mover ya pasan por un plan que aprueba en el móvil con los argumentos
#: exactos. Se cambia en el PC por operación (`python -m src.agente confirmar`).
CONFIRMACION_POR_DEFECTO = {"escribir": False, "borrar": True, "ejecutar": True, "terminar": True,
                            # Abrir (4.8): no cambia archivos; se puede pedir con `confirmar abrir si`.
                            "abrir": False}

#: La versión del fichero. **3** (3.3): trae las carpetas de escritura. Los ficheros de
#: la 2 guardaban `confirmar` sin que nadie lo hubiera elegido (no había orden para
#: cambiarlo), así que al leerlos se toma el de ahora.
VERSION = 3


@dataclass
class Politica:
    """Lo que este PC deja hacer. **Solo se cambia en el PC** (§16 del plan): el
    protocolo no tiene ningún mensaje para tocarla."""

    carpetas: list[str] = field(default_factory=list)
    #: Carpetas prohibidas **aunque caigan dentro de una permitida**. Ganan siempre.
    bloqueadas: list[str] = field(default_factory=list)
    #: Qué ofrece este agente. Lo apagado ni se anuncia a la nube.
    capacidades: dict[str, bool] = field(default_factory=lambda: dict(ENCENDIDA_POR_DEFECTO))
    #: Carpetas donde se puede **escribir** (3.3). Una lista aparte, vacía al empezar
    #: (decisión mía): que Morgan pueda leer una carpeta no significa que deba poder
    #: cambiarla.
    escritura: list[str] = field(default_factory=list)
    #: Programas del catálogo de la terminal (3.5) que la persona encendió. Ninguno al
    #: empezar.
    programas: list[str] = field(default_factory=list)
    #: Topes propios, siempre por debajo de los del código.
    lectura_bytes: int = MAX_BYTES
    copia_bytes: int = 0          # 0 = el tope del código (se resuelve al usarlo)
    confirmar: dict[str, bool] = field(default_factory=lambda: dict(CONFIRMACION_POR_DEFECTO))

    # --- Persistencia (solo en el PC) ---

    @classmethod
    def cargar(cls) -> "Politica":
        try:
            datos = json.loads((almacen.carpeta() / "politica.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return cls()
        if not isinstance(datos, dict) or not cls._tipos_correctos(datos):
            # Un fichero con un campo del tipo que no toca **no se interpreta a medias**
            # (3.2.5): si `bloqueadas` fuese `null`, ignorarlo borraría los bloqueos y
            # dejaría el PC MÁS abierto. Sin política, no se lee nada.
            logger.warning("La política local no se entiende: este PC no permite nada hasta arreglarla")
            return cls()
        carpetas = [str(c) for c in datos.get("carpetas", []) if isinstance(c, str)]
        # Lo que no valga ya (una carpeta borrada, o que ahora es un enlace) no cuenta.
        politica = cls([c for c in carpetas if cls._carpeta_valida(c) is None])
        # Una bloqueada no se valida como una permitida: puede no existir todavía y
        # sigue valiendo, porque prohibir algo que aún no está es lo que se quiere.
        politica.bloqueadas = [str(b) for b in datos.get("bloqueadas", []) if isinstance(b, str)]
        politica.escritura = [str(c) for c in datos.get("escritura", [])
                              if isinstance(c, str) and cls._carpeta_de_escritura_valida(c) is None]
        politica.programas = [str(p) for p in datos.get("programas", []) if isinstance(p, str)]
        guardadas = datos.get("capacidades")
        if isinstance(guardadas, dict):
            politica.capacidades = {c: bool(guardadas.get(c, ENCENDIDA_POR_DEFECTO[c]))
                                    for c in CAPACIDADES}
        limites = datos.get("limites") if isinstance(datos.get("limites"), dict) else {}
        politica.lectura_bytes = cls._tope(limites.get("lectura_kb"), 1024, MAX_BYTES)
        politica.copia_bytes = cls._tope(limites.get("copia_mb"), 1024 * 1024, _tope_copia())
        confirmar = datos.get("confirmar")
        if isinstance(confirmar, dict) and datos.get("version", 0) >= VERSION:
            politica.confirmar = {k: bool(confirmar.get(k, v))
                                  for k, v in CONFIRMACION_POR_DEFECTO.items()}
        return politica

    @staticmethod
    def _tipos_correctos(datos: dict) -> bool:
        """Si cada campo que está es del tipo que debe. Faltar está bien (el fichero de
        la 3.0 solo tenía carpetas); estar mal, no."""
        esperado = {"carpetas": list, "bloqueadas": list, "escritura": list, "programas": list,
                    "capacidades": dict,
                    "limites": dict, "confirmar": dict, "version": int}
        return all(isinstance(datos[campo], tipo)
                   for campo, tipo in esperado.items() if campo in datos)

    @staticmethod
    def _tope(valor, unidad: int, maximo: int) -> int:
        """El límite de la persona, **acotado al del código**: solo se puede bajar."""
        try:
            pedido = int(valor) * unidad
        except (TypeError, ValueError):
            return maximo
        return max(unidad, min(pedido, maximo))

    def guardar(self) -> None:
        # Atómica (3.6): una política cortada a mitad se leería como rota y dejaría el PC
        # cerrado hasta arreglarla a mano.
        almacen.escribir_atomico(almacen.carpeta() / "politica.json", json.dumps({
            "version": VERSION,
            "carpetas": self.carpetas,
            "bloqueadas": self.bloqueadas,
            "escritura": self.escritura,
            "programas": self.programas,
            "capacidades": {c: self.capacidades.get(c, ENCENDIDA_POR_DEFECTO[c]) for c in CAPACIDADES},
            "limites": {"lectura_kb": self.lectura_bytes // 1024,
                        "copia_mb": (self.copia_bytes or _tope_copia()) // (1024 * 1024)},
            "confirmar": self.confirmar,
        }, ensure_ascii=False, indent=2).encode("utf-8"))

    @staticmethod
    def firma() -> float:
        """Cuándo cambió la política por última vez (para re-anunciar capacidades)."""
        try:
            return (almacen.carpeta() / "politica.json").stat().st_mtime
        except FileNotFoundError:
            return 0.0

    # --- Qué carpetas pueden permitirse ---

    @staticmethod
    def _carpeta_valida(texto: str) -> str | None:
        """None si puede permitirse; si no, por qué."""
        ruta = Path(texto)
        if texto.startswith(("\\\\", "//")):
            return "Las carpetas de red no se permiten."
        if not ruta.is_absolute():
            return "Hace falta la ruta completa (por ejemplo C:\\Users\\ana\\Documentos\\Proyecto)."
        if not ruta.is_dir():
            return "Esa carpeta no existe."
        # Los enlaces, en la ruta TAL COMO SE ESCRIBIÓ y antes de resolverla: resolver
        # sigue el junction hasta la carpeta real, y en esa ya no hay ningún enlace que
        # ver. La primera versión miraba después y dejaba permitir un junction.
        normalizada = Path(os.path.normpath(texto))
        for parte in [normalizada, *normalizada.parents]:
            if _es_reanalisis(parte):
                return "Es un enlace o un punto de unión: permite la carpeta real."
        resuelta = ruta.resolve()
        if _dentro_del_agente(resuelta) and resuelta != Path(resuelta.anchor):
            return "Es la carpeta del propio agente, con su credencial: no se permite."
        if any(p.name.lower() in CARPETAS_SENSIBLES for p in [resuelta, *resuelta.parents]):
            return "Esa carpeta guarda credenciales."
        return None

    def anadir(self, texto: str) -> str:
        motivo = self._carpeta_valida(texto)
        if motivo:
            raise ValueError(motivo)
        resuelta = str(Path(texto).resolve())
        if _clave(resuelta) not in {_clave(c) for c in self.carpetas}:
            self.carpetas.append(resuelta)
        return resuelta

    def quitar(self, texto: str) -> bool:
        antes = len(self.carpetas)
        clave = _clave(Path(texto).resolve()) if texto else ""
        self.carpetas = [c for c in self.carpetas if _clave(c) != clave]
        return len(self.carpetas) < antes

    # --- Dónde se puede escribir (3.3) ---

    @classmethod
    def _carpeta_de_escritura_valida(cls, texto: str) -> str | None:
        """Lo de una carpeta para leer, y además fuera de donde no se escribe nunca."""
        motivo = cls._carpeta_valida(texto)
        if motivo:
            return motivo
        if en_zona_prohibida(Path(texto).resolve()):
            return ("Es una carpeta del sistema, de programas, de datos de aplicaciones, de "
                    "Inicio o del agente: ahí Morgan no escribe nunca.")
        return None

    def permitir_escritura(self, texto: str) -> str:
        motivo = self._carpeta_de_escritura_valida(texto)
        if motivo:
            raise ValueError(motivo)
        resuelta = str(Path(texto).resolve())
        if _clave(resuelta) not in {_clave(c) for c in self.escritura}:
            self.escritura.append(resuelta)
        return resuelta

    def quitar_escritura(self, texto: str) -> bool:
        antes = len(self.escritura)
        clave = _clave(Path(texto).resolve()) if texto else ""
        self.escritura = [c for c in self.escritura if _clave(c) != clave]
        return len(self.escritura) < antes

    def bloquear(self, texto: str) -> str:
        """Prohíbe una carpeta **aunque esté dentro de una permitida** (3.2).

        No se exige que exista: prohibir algo que todavía no está es justo lo que se
        quiere («nunca leas mi carpeta Privado», antes de crearla).
        """
        if not texto or not texto.strip():
            raise ValueError("Falta la carpeta.")
        ruta = Path(texto.strip())
        if not ruta.is_absolute():
            raise ValueError("Hace falta la ruta completa.")
        entera = str(ruta.resolve()) if ruta.exists() else os.path.normpath(str(ruta))
        if _clave(entera) not in {_clave(b) for b in self.bloqueadas}:
            self.bloqueadas.append(entera)
        return entera

    def desbloquear(self, texto: str) -> bool:
        antes = len(self.bloqueadas)
        ruta = Path(texto or "")
        claves = {_clave(os.path.normpath(str(ruta)))}
        if ruta.exists():
            claves.add(_clave(str(ruta.resolve())))
        self.bloqueadas = [b for b in self.bloqueadas if _clave(b) not in claves]
        return len(self.bloqueadas) < antes

    def esta_bloqueada(self, ruta: Path) -> bool:
        """Si esa ruta cae en algo prohibido. **Gana sobre lo permitido.**"""
        clave = _clave(ruta)
        return any(clave == _clave(b) or clave.startswith(_clave(b).rstrip("\\/") + os.sep)
                   for b in self.bloqueadas)

    # --- La comprobación de cada acceso ---

    def raiz_de(self, ruta: Path, raices: list[str] | None = None) -> Path | None:
        clave = _clave(ruta)
        for carpeta in (self.carpetas if raices is None else raices):
            base = _clave(carpeta)
            if clave == base or clave.startswith(base.rstrip("\\/") + os.sep):
                return Path(carpeta)
        return None

    def resolver(self, texto: str, *, archivo: bool | None = None) -> Path:
        """La ruta real y permitida, o `Denegado`. **Sobre el objeto, no sobre la cadena.**"""
        if not self.carpetas:
            raise Denegado("sin_carpetas", "Este PC no ha permitido ninguna carpeta.")
        return self._resolver_en(self._cadena(texto), self.carpetas, archivo)

    @staticmethod
    def _cadena(texto) -> Path:
        """Lo que se mira en la ruta escrita, antes de tocar el disco."""
        if not isinstance(texto, str) or not texto.strip() or "\x00" in texto:
            raise Denegado("ruta_invalida", "La ruta no es válida.")
        texto = texto.strip()
        if texto.startswith(("\\\\", "//")):
            raise Denegado("ruta_de_red", "Las rutas de red y de dispositivo no se permiten.")
        # Flujos alternativos de NTFS (3.1-D): `notas.txt:oculto` es otro contenido dentro
        # del mismo archivo, invisible al listar. El agente no tiene por qué leerlos, y
        # medido en mi PC se leían. Los dos puntos de la unidad van en el hueco 1.
        if ":" in texto[2:]:
            raise Denegado("flujo", "Los flujos alternativos de un archivo no se leen.")
        ruta = Path(texto)
        if not ruta.is_absolute():
            raise Denegado("ruta_relativa", "Hace falta la ruta completa, dentro de una carpeta permitida.")
        return Path(os.path.normpath(texto))

    def _resolver_en(self, normalizada: Path, raices: list[str], archivo: bool | None) -> Path:
        """El objeto real, dentro de `raices` (las de leer o las de escribir)."""
        # Primero, dentro de una carpeta permitida SIN resolver nada: si la cadena ya
        # sale fuera (con `..`, otro disco), no se toca el disco para averiguar más.
        raiz = self.raiz_de(normalizada, raices)
        if raiz is None:
            if raices is self.escritura:
                raise Denegado("fuera", "Esa carpeta no está entre las que este PC deja escribir: "
                                        "se permite en el PC (python -m src.agente escritura añadir).")
            raise Denegado("fuera", "Esa ruta está fuera de las carpetas que este PC permite.")
        # Lo bloqueado gana sobre lo permitido (3.2, decisión mía). Se mira **dos
        # veces**, aquí con la cadena y abajo con el objeto real, igual que lo permitido:
        # la primera evita tocar el disco para algo que ya se sabe prohibido, y la
        # segunda es la que vale si la cadena y el objeto no coinciden. Son redundantes
        # a propósito (una mutación que quite cualquiera de las dos no rompe nada hoy).
        if self.esta_bloqueada(normalizada):
            raise Denegado("bloqueada", "Esa carpeta está bloqueada en este PC.")

        # Ningún punto de reanálisis entre la carpeta permitida y el objeto.
        actual = normalizada
        while True:
            if _es_reanalisis(actual):
                raise Denegado("enlace", "En el camino hay un enlace o un punto de unión: no se sigue.")
            if _clave(actual) == _clave(raiz) or actual == actual.parent:
                break
            actual = actual.parent

        try:
            real = normalizada.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise Denegado("no_existe", "Esa ruta no existe.") from None
        # Y otra vez con la ruta real: lo que se abre es esto, no lo que decía la cadena.
        if self.raiz_de(real, raices) is None:
            raise Denegado("fuera", "Esa ruta está fuera de las carpetas que este PC permite.")
        if self.esta_bloqueada(real):
            raise Denegado("bloqueada", "Esa carpeta está bloqueada en este PC.")
        if _dentro_del_agente(real):
            raise Denegado("agente", "Es la carpeta del propio agente, con su credencial: no se lee.")
        partes = real.relative_to(self.raiz_de(real, raices)).parts
        if any(p.lower() in CARPETAS_SENSIBLES for p in partes):
            raise Denegado("sensible", "Esa carpeta guarda credenciales: no se lee.")
        if real.is_file() and _es_sensible(real.name):
            raise Denegado("sensible", "Ese archivo puede tener credenciales o secretos: no se lee.")
        if real.is_file():
            # Un archivo con varios nombres (enlaces duros, 3.1-D) se juzga por todos:
            # basta uno sensible o fuera de lo permitido para que no se lea. Si no, un
            # `inocente.txt` dentro de la carpeta permitida sirve el `.env` de al lado.
            for otro in nombres_del_archivo(real):
                if _es_sensible(otro.name):
                    raise Denegado("sensible", "Ese archivo es también otro con credenciales: no se lee.")
                if self.raiz_de(otro, raices) is None or _dentro_del_agente(otro) or self.esta_bloqueada(otro):
                    raise Denegado("enlace_duro", "Ese archivo tiene otro nombre fuera de las carpetas permitidas.")
        if archivo is True and not real.is_file():
            raise Denegado("no_es_archivo", "Eso no es un archivo.")
        if archivo is False and not real.is_dir():
            raise Denegado("no_es_carpeta", "Eso no es una carpeta.")
        return real

    def resolver_escritura(self, texto: str, *, nueva: bool = False,
                           archivo: bool | None = None, raiz: bool = False) -> Path:
        """La ruta real donde se va a escribir, o `Denegado` (3.3).

        Todo lo de una lectura —carpetas, bloqueos, enlaces, flujos, nombres sensibles,
        enlaces duros—, pero **contra las carpetas de escritura**, y además:

        - **Nunca** en las zonas prohibidas (`zonas_prohibidas`), se permita lo que se
          permita: si no, una escritura permitida podría abrir la propia política o
          dejar algo que arranque solo.
        - `nueva=True` (crear, destino de mover): **no puede existir**, su carpeta sí,
          y el nombre tiene que valer para escribir (`nombre_para_escribir`).
        - Nunca la carpeta permitida entera: se trabaja dentro de ella.
        """
        if not self.escritura:
            raise Denegado("sin_carpetas_escritura",
                           "Este PC no ha permitido escribir en ninguna carpeta.")
        if isinstance(texto, str) and texto != texto.strip():
            # Al leer se recortan los espacios de los lados; al escribir no: se haría algo
            # distinto de lo que la persona aprobó (`nota.txt ` acabaría en `nota.txt`).
            raise Denegado("nombre", "La ruta empieza o acaba en espacio.")
        normalizada = self._cadena(texto)
        if nueva:
            nombre_para_escribir(normalizada.name)
            if os.path.lexists(normalizada):
                raise Denegado("ya_existe", "Ya existe algo con ese nombre: no se pisa.")
            real = self._resolver_en(normalizada.parent, self.escritura, False) / normalizada.name
        else:
            real = self._resolver_en(normalizada, self.escritura, archivo)
            if not raiz and any(_clave(real) == _clave(c) for c in self.escritura):
                raise Denegado("raiz", "La carpeta permitida entera no se toca: solo lo que hay dentro.")
        if any(parte.lower() == ".git" for parte in real.parts):
            # Los *hooks* de git son programas sin extensión que corren con cada commit, y
            # su configuración lanza otros (3.5).
            raise Denegado("zona_prohibida", "Dentro de una carpeta .git Morgan no escribe nada.")
        if en_zona_prohibida(real):
            raise Denegado("zona_prohibida",
                           "Ahí Morgan no escribe nunca (sistema, programas, datos de "
                           "aplicaciones, Inicio o el propio agente).")
        return real
