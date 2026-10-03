"""
Lo que el agente sabe hacer en la 3.0 (3.0-E): leer, y solo leer.

`list_files`, `read_file`, `search_files` y `system_info`, **con los mismos nombres y
argumentos que las herramientas que el modelo ya conoce** (`src/tools/filesystem.py`,
`src/tools/system.py`). No se reutiliza su cuerpo, a propósito: está pensado para el
Morgan de tu equipo, donde lee el dueño, y lee el archivo entero, no mira binarios ni
comprueba carpetas (los seis huecos de plan-3.0.md, Parte III §2). Aquí, cada acceso
pasa por la política local (`politica.py`) y lo que sale del PC va acotado.

Cada función devuelve la forma de siempre de las herramientas: `success`, `data`,
`error`. Una denegación de la política es un `success: False` con su motivo, no una
excepción: el modelo tiene que poder decirle a la persona por qué no.
"""

import fnmatch
import hashlib
import mimetypes
import os
import platform
import sys
import time
import unicodedata
from datetime import datetime
from collections import deque
from pathlib import Path

from src.agente import control, motor
from src.agente.protocolo import MAX_COPIA, TROZO_COPIA
from src.agente.politica import (
    CARPETAS_SENSIBLES,
    MAX_BYTES,
    Denegado,
    Politica,
    _dentro_del_agente,
    _es_reanalisis,
    _es_sensible,
)

MAX_ENTRADAS = 500
MAX_RESULTADOS = 100
MAX_PROFUNDIDAD = 8
MAX_REVISADOS = 200_000
#: Lo que puede durar una búsqueda: por debajo del plazo de la orden (20 s en la nube).
MAX_SEGUNDOS_BUSQUEDA = 8.0
#: Lo que se sigue buscando una vez aparece el primer resultado.
EXTRA_TRAS_ENCONTRAR = 2.0

#: Carpetas donde casi nunca está lo que la persona busca, y que tienen muchísimos
#: archivos. **No se excluyen**: se recorren al final, cuando lo demás ya se miró.
#:
#: Medido con mi PC (2026-09-19, acceso total): buscar «lista-de-la-compra.txt»
#: en C: recorría el disco en profundidad y en orden alfabético —$Recycle.Bin, Program
#: Files, ProgramData…—, gastaba el tope de 20.000 archivos en 0,17 s y nunca llegaba a
#: Users/<yo>/Documents. Morgan contestaba que no estaba.
RUIDOSAS = frozenset({
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", "appdata", "recovery", "$windows.~bt",
    "$windows.~ws", "windows.old", "perflogs", "msocache", "node_modules", ".git",
    "venv", ".venv", "__pycache__", "site-packages", ".cache", ".npm", ".nuget",
    ".gradle", ".m2", ".cargo", ".rustup", ".next",
})
#: Se mira **todo lo que se va a leer**, no solo el principio (3.1.5). Antes eran los
#: primeros 8 KB, y en el ataque de la 3.1.5 un archivo con un byte nulo en el byte
#: 20.000 pasaba como texto: un binario que empiece con texto —muchos formatos lo
#: hacen— entraba y le devolvía basura al modelo. Mirar 256 KB cuesta microsegundos.


def _rechazo(decision) -> dict:
    """Un «no» del motor, con la forma de siempre de las herramientas."""
    return {"success": False, "data": None, "error": decision.mensaje, "motivo": decision.motivo}


def _no(exc: Denegado) -> dict:
    return {"success": False, "data": None, "error": str(exc), "motivo": exc.motivo}


def _ok(datos) -> dict:
    return {"success": True, "data": datos, "error": None}


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


def _coincidencia(query: str):
    """Cómo casa un nombre con lo que se busca, sin mirar tildes ni mayúsculas.

    Con comodines (`*.pdf`), como siempre. Sin ellos, **por palabras sueltas**, en
    cualquier orden: «juegos switch» encuentra «Switch - Juegos». Antes se buscaba el
    texto literal, y «programacion» no encontraba «Programación».
    """
    consulta = _sin_tildes(query or "")
    if any(c in consulta for c in "*?[]"):
        return lambda nombre: fnmatch.fnmatch(_sin_tildes(nombre), consulta)
    palabras = consulta.split()
    return lambda nombre: all(p in _sin_tildes(nombre) for p in palabras)


#: Las carpetas personales de Windows, por su identificador (KNOWNFOLDERID), no por su
#: nombre. **Por qué**: en mi PC el Escritorio de verdad es
#: OneDrive/Desktop y los Documentos OneDrive/Documentos; la carpeta Users/<yo>/Desktop
#: existe pero está vacía. Morgan adivinaba la ruta clásica y le decía que su escritorio
#: estaba vacío (2026-09-19). Solo Windows sabe dónde están.
CONOCIDAS = {
    "escritorio": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documentos": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "descargas": "{374DE290-123F-4565-9164-39C4925E467B}",
    "imagenes": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "musica": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


def _carpeta_conocida(guid: str) -> str | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    import uuid

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


def carpetas_personales() -> dict[str, str]:
    """Dónde están de verdad el Escritorio, Documentos… **si la política las permite**.
    Una fuera de lo permitido ni se nombra."""
    politica = Politica.cargar()
    halladas = {}
    for nombre, guid in CONOCIDAS.items():
        ruta = _carpeta_conocida(guid)
        if not ruta:
            continue
        try:
            halladas[nombre] = str(politica.resolver(ruta, archivo=False))
        except Denegado:
            continue
    return halladas


def _personales_dentro(raiz: Path) -> list[Path]:
    """Las carpetas personales que caen dentro de `raiz`, para buscar en ellas primero."""
    clave_raiz = os.path.normcase(str(raiz)).rstrip("\\/") + os.sep
    return [Path(r) for r in carpetas_personales().values()
            if (os.path.normcase(r) + os.sep).startswith(clave_raiz)]


def list_files(path: str | None = None, show_hidden: bool = False, **_) -> dict:
    """Sin ruta, las carpetas permitidas y dónde están de verdad las personales: así el
    modelo sabe por dónde empezar y no adivina `Users/<nombre>/Desktop`."""
    politica = Politica.cargar()
    decision = motor.evaluar("listar", path, archivo=False, politica=politica)
    if not decision:
        return _rechazo(decision)
    if decision.ruta is None:
        return _ok({"carpetas_permitidas": politica.carpetas,
                    "carpetas_bloqueadas": politica.bloqueadas,
                    "carpetas_personales": carpetas_personales()})
    carpeta = decision.ruta

    entradas = []
    for entrada in sorted(carpeta.iterdir(), key=lambda e: e.name.lower()):
        if not show_hidden and entrada.name.startswith("."):
            continue
        if _es_reanalisis(entrada):
            entradas.append({"name": entrada.name, "type": "enlace (no se sigue)"})
            continue
        if entrada.is_dir() and entrada.name.lower() in CARPETAS_SENSIBLES:
            continue
        if _dentro_del_agente(entrada) or politica.esta_bloqueada(entrada):
            continue
        try:
            info = entrada.stat()
        except OSError:
            continue
        entradas.append({
            "name": entrada.name,
            "type": "directory" if entrada.is_dir() else "file",
            "size": None if entrada.is_dir() else info.st_size,
        })
        if len(entradas) >= MAX_ENTRADAS:
            break
    return _ok({"path": str(carpeta), "entries": entradas,
                "is_truncated": len(entradas) >= MAX_ENTRADAS})


def read_file(path: str, max_lines: int = 500, offset: int = 1, **_) -> dict:
    decision = motor.evaluar("leer", path, archivo=True)
    if not decision:
        return _rechazo(decision)
    ruta, tope = decision.ruta, decision.limite_bytes or MAX_BYTES

    # TOCTOU (§18): entre comprobar y abrir, alguien podría cambiar el archivo por un
    # enlace a otro sitio. Se abre, y se comprueba que lo abierto ES lo comprobado.
    antes = os.stat(ruta)
    with open(ruta, "rb") as f:
        despues = os.fstat(f.fileno())
        if (antes.st_ino, antes.st_dev) != (despues.st_ino, despues.st_dev):
            return _no(Denegado("cambiado", "El archivo cambió mientras se abría: no se lee."))
        # Solo lo necesario, nunca el archivo entero (el hueco del Morgan local).
        crudo = f.read(tope + 1)

    if b"\x00" in crudo:
        return _no(Denegado("binario", "Es un archivo binario: no se lee como texto."))
    recortado_por_tamano = len(crudo) > tope
    texto = crudo[:tope].decode("utf-8", errors="replace")

    lineas = texto.splitlines(keepends=True)
    offset = max(1, int(offset or 1))
    max_lines = max(1, min(int(max_lines or 500), 5000))
    elegidas = lineas[offset - 1: offset - 1 + max_lines]
    return _ok({
        "path": str(ruta),
        "content": "".join(elegidas),
        "offset": offset,
        "lines_returned": len(elegidas),
        "is_truncated": recortado_por_tamano or offset - 1 + max_lines < len(lineas),
        "bytes_max": tope,
    })


#: Buscar dentro de los archivos (4.6): solo texto, hasta este tamaño cada uno, y como
#: mucho esto en total por búsqueda.
MAX_BYTES_CONTENIDO = 1024 * 1024
MAX_BYTES_CONTENIDO_TOTAL = 64 * 1024 * 1024


class _Filtros:
    """Lo que la búsqueda mira además del nombre (4.6): extensión, fecha, tamaño y
    contenido. `error` si algo no se entiende, para decirlo en vez de buscar mal."""

    def __init__(self, extension=None, modificado_desde=None, modificado_hasta=None,
                 tamano_min_kb=None, tamano_max_kb=None, contenido=None):
        self.error = None
        extensiones = extension if isinstance(extension, list) else [extension] if extension else []
        self.extensiones = {"." + str(e).strip().lower().lstrip(".*") for e in extensiones if str(e).strip()}
        self.desde = self._fecha(modificado_desde, 0)
        self.hasta = self._fecha(modificado_hasta, 86400)
        self.minimo = self._kb(tamano_min_kb)
        self.maximo = self._kb(tamano_max_kb)
        self.contenido = _sin_tildes(str(contenido)).strip() if contenido else ""
        self.leidos = 0

    def _fecha(self, texto, mas: int):
        if not texto:
            return None
        try:
            return datetime.strptime(str(texto)[:10], "%Y-%m-%d").timestamp() + mas
        except ValueError:
            self.error = f"La fecha «{texto}» no se entiende: usa AAAA-MM-DD."
            return None

    def _kb(self, valor):
        if valor in (None, ""):
            return None
        try:
            return int(float(valor) * 1024)
        except (TypeError, ValueError):
            self.error = f"El tamaño «{valor}» no se entiende: en KB, con un número."
            return None

    @property
    def activos(self) -> bool:
        return bool(self.extensiones or self.desde or self.hasta or self.minimo is not None
                    or self.maximo is not None or self.contenido)

    def pasa(self, entrada) -> bool:
        """Si un archivo cumple lo pedido. Lo barato primero; el contenido, al final."""
        if self.extensiones and os.path.splitext(entrada.name)[1].lower() not in self.extensiones:
            return False
        if self.desde or self.hasta or self.minimo is not None or self.maximo is not None:
            try:
                info = entrada.stat(follow_symlinks=False)
            except OSError:
                return False
            if (self.desde and info.st_mtime < self.desde) or (self.hasta and info.st_mtime >= self.hasta):
                return False
            if (self.minimo is not None and info.st_size < self.minimo) or (
                    self.maximo is not None and info.st_size > self.maximo):
                return False
        return not self.contenido or self._contiene(entrada.path)

    def _contiene(self, ruta: str) -> bool:
        if self.leidos >= MAX_BYTES_CONTENIDO_TOTAL:
            return False
        try:
            with open(ruta, "rb") as f:
                datos = f.read(MAX_BYTES_CONTENIDO)
        except OSError:
            return False
        self.leidos += len(datos)
        if b"\x00" in datos:
            return False                    # binario: no se busca dentro
        return self.contenido in _sin_tildes(datos.decode("utf-8", errors="ignore"))


def search_files(query: str = "", path: str | None = None, recursive: bool = True,
                 max_results: int = 50, extension=None, modificado_desde=None,
                 modificado_hasta=None, tamano_min_kb=None, tamano_max_kb=None,
                 contenido=None, **_) -> dict:
    """Busca dentro de las carpetas permitidas. Sin ruta, en todas. Por nombre y, desde
    la 4.6, por extensión, fecha de modificación, tamaño y texto de dentro."""
    filtros = _Filtros(extension, modificado_desde, modificado_hasta, tamano_min_kb,
                       tamano_max_kb, contenido)
    if filtros.error:
        return {"success": False, "data": None, "error": filtros.error, "motivo": "argumentos"}
    if not (query or "").strip() and not filtros.activos:
        return {"success": False, "data": None, "motivo": "argumentos",
                "error": "Di qué buscar: un nombre, una extensión, una fecha, un tamaño o un texto."}
    politica = Politica.cargar()
    decision = motor.evaluar("buscar", path, archivo=False, politica=politica)
    if not decision:
        return _rechazo(decision)
    raices = [decision.ruta] if decision.ruta else [Path(c) for c in politica.carpetas]
    if not raices:
        return _rechazo(motor.Decision(False, "sin_carpetas",
                                       "Este PC no ha permitido ninguna carpeta."))

    coincide = _coincidencia(query)
    tope = max(1, min(int(max_results or 50), MAX_RESULTADOS))
    encontrados, revisados = [], 0
    limite = time.monotonic() + MAX_SEGUNDOS_BUSQUEDA
    completa = True

    # Por niveles, no en profundidad: lo que está a pocas carpetas de la raíz aparece
    # antes que lo enterrado. Las carpetas personales que caigan dentro (el Escritorio,
    # Documentos… donde Windows las tenga, OneDrive incluido) van delante de todo. Y las
    # ruidosas, en una segunda cola que solo se empieza cuando la primera se acaba.
    primero: deque = deque()
    vistas: set[str] = set()

    def encolar(cola: deque, carpeta: Path, nivel: int) -> None:
        clave = os.path.normcase(str(carpeta))
        if clave not in vistas:
            vistas.add(clave)
            cola.append((carpeta, nivel))

    for raiz in raices:
        for personal in _personales_dentro(raiz):
            encolar(primero, personal, len(personal.parts) - len(raiz.parts))
    for raiz in raices:
        encolar(primero, raiz, 0)
    despues: deque = deque()
    while (primero or despues) and len(encontrados) < tope:
        if revisados >= MAX_REVISADOS or time.monotonic() > limite:
            completa = False
            break
        actual, nivel = primero.popleft() if primero else despues.popleft()
        try:
            with os.scandir(actual) as it:
                entradas = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue        # sin permiso, o desapareció: se sigue con lo demás
        for entrada in entradas:
            if revisados % 500 == 0:
                control.punto_seguro()      # se puede cancelar a mitad (3.4)
            if revisados % 2000 == 0 and time.monotonic() > limite:
                completa = False      # una carpeta enorme no se come el plazo
                break
            ruta = Path(entrada.path)
            try:
                es_carpeta = entrada.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if politica.esta_bloqueada(ruta):
                continue
            if es_carpeta:
                # Ni se enseñan ni se baja a enlaces, junctions, carpetas de
                # credenciales o la del agente.
                if (entrada.name.lower() in CARPETAS_SENSIBLES
                        or _dentro_del_agente(ruta) or _es_reanalisis(ruta)):
                    continue
                if recursive and nivel + 1 <= MAX_PROFUNDIDAD:
                    encolar(despues if entrada.name.lower() in RUIDOSAS else primero, ruta, nivel + 1)
            else:
                revisados += 1
                if _es_sensible(entrada.name):
                    continue
            # Las carpetas también se encuentran: «mi carpeta de programación» es una
            # carpeta, y antes solo salían archivos. Con filtros (4.6), solo archivos.
            if es_carpeta and filtros.activos:
                continue
            if (not (query or "").strip() or coincide(entrada.name)) and (
                    es_carpeta or filtros.pasa(entrada)):
                if not encontrados:
                    # Con algo encontrado, no se apura el plazo buscando más: medido en el
                    # mi PC, cada búsqueda duraba sus 8 s enteros aunque lo que pedía
                    # apareciera en el primer segundo.
                    limite = min(limite, time.monotonic() + EXTRA_TRAS_ENCONTRAR)
                encontrados.append({"name": entrada.name, "path": str(ruta),
                                    "type": "directory" if es_carpeta else "file"})
                if len(encontrados) >= tope:
                    break

    datos = {"matches": encontrados, "count": len(encontrados),
             "is_truncated": len(encontrados) >= tope or not completa}
    if filtros.contenido and filtros.leidos >= MAX_BYTES_CONTENIDO_TOTAL:
        completa = False
    if not completa:
        # Que el modelo no diga «no está» cuando lo que pasó es que no se miró todo.
        datos["aviso"] = (
            f"Búsqueda incompleta: se revisaron {revisados} archivos y se paró antes de "
            "mirarlo todo. " + ("Puede haber más coincidencias." if encontrados else
                                "Que no aparezca no significa que no exista; prueba con una "
                                "carpeta más concreta (por ejemplo, una de las carpetas "
                                "personales) o con otras palabras."))
    return _ok(datos)


def system_info(**_) -> dict:
    decision = motor.evaluar("estado")
    if not decision:
        return _rechazo(decision)
    """El equipo, sin nada personal: **ni variables de entorno** (§19) ni nombre de usuario."""
    datos = {
        "sistema": platform.system(),
        "version": platform.release(),
        "arquitectura": platform.machine(),
        "procesadores": os.cpu_count(),
    }
    try:
        import psutil

        memoria = psutil.virtual_memory()
        datos["memoria_gb"] = round(memoria.total / 2**30, 1)
        datos["memoria_libre_gb"] = round(memoria.available / 2**30, 1)
    except Exception:
        pass
    # Desde la 4.7: Windows, procesador, discos, GPU, batería, cuánto lleva encendido.
    try:
        from src.agente.equipo import resumen

        datos.update(resumen())
    except Exception:
        pass
    return _ok(datos)


def copy_file(path: str, **_) -> dict:
    """Prepara una **copia del archivo para que su dueño la descargue** (3.1-E).

    Lo pedí yo: «que Morgan copie un documento y me deje descargarlo en el móvil».
    No es una lectura: el contenido **no pasa por el modelo** y no se filtran secretos
    (es su archivo, va para él tal cual). Por eso puede ser binario —un PDF, un Word,
    una foto— y llega hasta los 20 MB, en vez de los 256 KB de una lectura.

    Lo que **sí** se aplica, igual que a una lectura: las carpetas permitidas, los
    nombres sensibles, los enlaces duros, los flujos y el resto de la política.

    Devuelve los datos del archivo y deja su ruta en `_archivo`: el canal la manda en
    trozos antes del resultado, y el ejecutor no la deja salir dentro de él.
    """
    decision = motor.evaluar("copiar", path, archivo=True)
    if not decision:
        return _rechazo(decision)
    ruta = decision.ruta
    tope = decision.limite_bytes or MAX_COPIA

    tamano = ruta.stat().st_size
    if tamano > tope:
        return _no(Denegado("demasiado_grande",
                            f"Ese archivo ocupa {tamano // (1024 * 1024)} MB y el tope para "
                            f"copiar es {tope // (1024 * 1024)} MB."))

    resumen = hashlib.sha256()
    with open(ruta, "rb") as f:
        antes = os.stat(ruta)
        despues = os.fstat(f.fileno())
        if (antes.st_ino, antes.st_dev) != (despues.st_ino, despues.st_dev):
            return _no(Denegado("cambiado", "El archivo cambió mientras se abría: no se copia."))
        while trozo := f.read(TROZO_COPIA):
            control.punto_seguro()          # se puede cancelar a mitad (3.4)
            resumen.update(trozo)

    return {**_ok({"name": ruta.name, "bytes": tamano, "sha256": resumen.hexdigest(),
                   "tipo": mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"}),
            "_archivo": str(ruta)}


#: Las de la 3.0, más la copia de la 3.1. `system_info` no necesita carpetas; el resto, sí.
LECTURA_DE_ARCHIVOS = {"list_files": list_files, "read_file": read_file,
                       "search_files": search_files, "copy_file": copy_file}
SIEMPRE = {"system_info": system_info}


def disponibles(politica: Politica | None = None) -> dict:
    """Las que este agente anuncia ahora: las que necesitan carpetas solo si las hay, y
    **solo las que la persona tenga encendidas** en su PC (3.2). Las de escritura
    (3.3), solo si hay carpetas de escritura."""
    from src.agente.escritura import ESCRITURA
    from src.agente.terminal import CATALOGO, PROCESOS, TERMINAL

    politica = politica or Politica.cargar()
    # La terminal (3.5), solo con algún programa del catálogo encendido.
    con_programas = any(p in CATALOGO for p in politica.programas)
    from src.agente.archivos import ARCHIVOS_ESCRITURA, ARCHIVOS_LECTURA
    from src.agente.aplicaciones import APLICACIONES
    from src.agente.interfaz import INTERFAZ
    from src.agente.pantalla import PANTALLA
    from src.agente.portapapeles import AVISOS
    from src.agente.equipo import DIAGNOSTICO, SERVICIOS
    from src.agente.contexto import CONTEXTO

    todas = {**SIEMPRE, **(LECTURA_DE_ARCHIVOS if politica.carpetas else {}),
             **(ARCHIVOS_LECTURA if politica.carpetas else {}),
             **(CONTEXTO if politica.carpetas else {}),
             **(ESCRITURA if politica.escritura else {}),
             **(ARCHIVOS_ESCRITURA if politica.escritura else {}), **DIAGNOSTICO, **APLICACIONES, **SERVICIOS, **AVISOS,
             **PANTALLA, **INTERFAZ, **PROCESOS,
             **(TERMINAL if con_programas else {})}
    encendidas = motor.capacidades_encendidas(politica)
    return {nombre: f for nombre, f in todas.items() if nombre in encendidas}
