"""
Los archivos, más allá de crear y editar (4.6, mi lista): copiar dentro del PC,
comprimir y descomprimir `.zip`, y los metadatos de un archivo o carpeta.

Renombrar ya lo hace `move_file`. Buscar por extensión, fecha, tamaño y contenido está en
`capacidades.search_files`.

## Las mismas reglas que la escritura (3.3)

- **Lo que se crea pasa por `escritura._autorizar`**, el único camino hacia una escritura:
  carpetas de escritura, nunca zonas prohibidas, un destino **nuevo** (no se pisa nada).
- **Lo que se lee pasa por la política de lectura**: carpetas permitidas, bloqueadas,
  enlaces, credenciales.
- **Nada de programas ni scripts** (`es_ejecutable`): no se copian ni se sacan de un
  `.zip`; si hay alguno dentro de una carpeta, se omite y se dice. **Comprimir sí los mete**
  (decisión mía, 2026-10-01): un `.zip` no ejecuta nada, y sin ellos la copia de un
  proyecto de código salía sin el código. Sacarlos de un `.zip` sigue sin poderse.
- **Sin estados a medias**: se hace en un temporal junto al destino y se renombra de golpe.
- **Se comprueba**: la huella de lo copiado, las entradas del `.zip`.

## Lo nuevo de un `.zip`

Un `.zip` lo puede haber hecho cualquiera. Al descomprimir: ninguna entrada sale de la
carpeta de destino (rutas absolutas, `..`: *zip slip*), ni enlaces, ni programas; y topes
de entradas, de tamaño total y de proporción por entrada, contra las «bombas» que ocupan
unos KB comprimidas y gigas al sacarlas. El tamaño se mide **mientras se saca**, no se
cree lo que dice el índice del `.zip`.
"""

import mimetypes
import os
import shutil
import stat
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from src.agente import control, motor
from src.agente.escritura import (
    FUERA_DE_SITIO, _autorizar, _en_su_sitio, _huella, _no, _ok, _ruta_final,
    _temporal_junto_a, _una_sola_vez,
)
from src.agente.politica import (
    CARPETAS_SENSIBLES, Denegado, Politica, _dentro_del_agente, _es_reanalisis, _es_sensible,
    es_ejecutable,
)

#: Lo más que se copia o se comprime de una vez.
MAX_ARCHIVOS = 2000
MAX_BYTES = 500 * 1024 * 1024
#: Al descomprimir: entradas, tamaño total sacado y proporción por entrada.
MAX_ENTRADAS_ZIP = 10000
MAX_BYTES_ZIP = 1024 * 1024 * 1024
MAX_PROPORCION = 200
#: Para el tamaño de una carpeta en `file_info`: se para al llegar aquí y lo dice.
MAX_CONTADOS = 20000
MAX_SEGUNDOS_CONTAR = 3.0
TROZO = 1024 * 1024


# --- Recorrer una carpeta con la política (lo que se puede leer de ella) ---

def _recorrer(raiz: Path, politica: Politica, con_programas: bool = False):
    """Los archivos que se pueden leer dentro de `raiz`, con su ruta relativa, y lo
    omitido con su motivo. Sin seguir enlaces ni bajar a carpetas de credenciales,
    bloqueadas o del agente. `con_programas`: también programas y scripts (solo al
    comprimir: un `.zip` no ejecuta nada)."""
    archivos, omitidos = [], []
    pendientes = [raiz]
    while pendientes:
        actual = pendientes.pop()
        try:
            with os.scandir(actual) as it:
                entradas = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            omitidos.append((str(actual), "no se pudo leer"))
            continue
        for entrada in entradas:
            ruta = Path(entrada.path)
            relativa = ruta.relative_to(raiz)
            try:
                if entrada.is_symlink() or _es_reanalisis(ruta):
                    omitidos.append((str(relativa), "enlace"))
                    continue
                es_carpeta = entrada.is_dir(follow_symlinks=False)
            except OSError:
                omitidos.append((str(relativa), "no se pudo leer"))
                continue
            if politica.esta_bloqueada(ruta) or _dentro_del_agente(ruta):
                omitidos.append((str(relativa), "bloqueada"))
                continue
            if es_carpeta:
                if entrada.name.lower() in CARPETAS_SENSIBLES:
                    omitidos.append((str(relativa), "credenciales"))
                else:
                    pendientes.append(ruta)
                continue
            if _es_sensible(entrada.name):
                omitidos.append((str(relativa), "credenciales"))
            elif es_ejecutable(entrada.name) and not con_programas:
                omitidos.append((str(relativa), "programa o script"))
            else:
                archivos.append((ruta, relativa, entrada.stat(follow_symlinks=False).st_size))
    return archivos, omitidos


def _cabe(archivos) -> str | None:
    if len(archivos) > MAX_ARCHIVOS:
        return f"Son {len(archivos)} archivos; como mucho {MAX_ARCHIVOS} de una vez."
    total = sum(t for _, _, t in archivos)
    if total > MAX_BYTES:
        return f"Son {total // 2**20} MB; como mucho {MAX_BYTES // 2**20} MB de una vez."
    return None


def _resumen_omitidos(omitidos) -> list[str]:
    return [f"{ruta} ({motivo})" for ruta, motivo in omitidos[:20]]


def _copiar_bytes(origen: Path, destino_abierto) -> None:
    with open(origen, "rb") as f:
        while True:
            trozo = f.read(TROZO)
            if not trozo:
                break
            destino_abierto.write(trozo)


def _huella_de(ruta: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(TROZO), b""):
            h.update(trozo)
    return h.hexdigest()


def _deshacer(ruta: Path) -> None:
    try:
        if ruta.is_dir():
            shutil.rmtree(ruta)
        else:
            ruta.unlink()
    except OSError:
        pass


# --- Metadatos ---

def legible(bytes_: int) -> str:
    """El tamaño en palabras. Medido con el modelo real (4.6): con solo `size` en bytes,
    dijo «ocupa 14 KB» de una carpeta de 14 bytes."""
    if bytes_ < 1024:
        return f"{bytes_} bytes"
    for unidad in ("KB", "MB", "GB", "TB"):
        bytes_ /= 1024
        if bytes_ < 1024 or unidad == "TB":
            return f"{bytes_:.1f} {unidad}".replace(".", ",")
    return ""

def _fecha(segundos: float) -> str:
    return datetime.fromtimestamp(segundos).isoformat(timespec="seconds")


def file_info(path: str, **_) -> dict:
    """Tipo, tamaño, fechas y atributos de un archivo o carpeta permitidos. De una
    carpeta, además, cuántas cosas tiene y cuánto ocupa (hasta un tope)."""
    politica = Politica.cargar()
    decision = motor.evaluar("info", path, politica=politica)
    if not decision:
        return {"success": False, "data": None, "error": decision.mensaje, "motivo": decision.motivo}
    ruta = decision.ruta
    info = ruta.stat()
    datos = {
        "path": str(ruta),
        "type": "directory" if ruta.is_dir() else "file",
        "modified": _fecha(info.st_mtime),
        "created": _fecha(getattr(info, "st_birthtime", info.st_ctime)),
        "accessed": _fecha(info.st_atime),
    }
    atributos = getattr(info, "st_file_attributes", 0)
    datos["attributes"] = [n for n, bit in (("solo_lectura", stat.FILE_ATTRIBUTE_READONLY),
                                            ("oculto", stat.FILE_ATTRIBUTE_HIDDEN),
                                            ("sistema", stat.FILE_ATTRIBUTE_SYSTEM),
                                            ("comprimido", stat.FILE_ATTRIBUTE_COMPRESSED),
                                            ("cifrado", stat.FILE_ATTRIBUTE_ENCRYPTED))
                           if atributos & bit] if atributos else (
        ["solo_lectura"] if not os.access(ruta, os.W_OK) else [])
    if ruta.is_file():
        tipo, _codificacion = mimetypes.guess_type(ruta.name)
        datos.update({"size": info.st_size, "tamano": legible(info.st_size),
                      "extension": ruta.suffix.lower().lstrip(".") or None,
                      "mime": tipo, "ejecutable": es_ejecutable(ruta.name)})
        return _ok(datos)

    # Una carpeta: cuánto tiene, hasta un tope de archivos y de tiempo.
    archivos = carpetas = total = 0
    completo = True
    limite = time.monotonic() + MAX_SEGUNDOS_CONTAR
    pendientes = [ruta]
    while pendientes:
        if archivos >= MAX_CONTADOS or time.monotonic() > limite:
            completo = False
            break
        actual = pendientes.pop()
        try:
            with os.scandir(actual) as it:
                for entrada in it:
                    try:
                        if entrada.is_symlink():
                            continue
                        if entrada.is_dir(follow_symlinks=False):
                            carpetas += 1
                            if not _es_reanalisis(Path(entrada.path)):
                                pendientes.append(Path(entrada.path))
                        else:
                            archivos += 1
                            total += entrada.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
        control.punto_seguro()
    datos.update({"files": archivos, "folders": carpetas, "size": total, "tamano": legible(total),
                  "complete": completo})
    if not completo:
        datos["aviso"] = "Es muy grande: se paró de contar antes de terminar; son al menos esas cifras."
    return _ok(datos)


# --- Copiar dentro del PC ---

def _leible(path: str, politica: Politica):
    decision = motor.evaluar("leer", path, politica=politica)
    if not decision:
        return None, _no(decision.motivo, decision.mensaje)
    return decision.ruta, None


def _comprobacion_de_copia(d: dict) -> dict:
    return ({"archivo": d["dst"], "sha256": d["sha256"]} if d.get("sha256")
            else {"carpeta": d["dst"]})


@_una_sola_vez(_comprobacion_de_copia)
def copy_path(src: str, dst: str, **_) -> dict:
    """Copia un archivo o una carpeta a un sitio **nuevo** de una carpeta de escritura.
    El original no se toca."""
    politica = Politica.cargar()
    origen, rechazo = _leible(src, politica)
    if origen is None:
        return rechazo
    if origen.is_file():
        if es_ejecutable(origen.name):
            return _no("ejecutable", "Morgan no copia programas ni scripts.")
        archivos, omitidos = [(origen, Path(origen.name), origen.stat().st_size)], []
    else:
        archivos, omitidos = _recorrer(origen, politica)
    demasiado = _cabe(archivos)
    if demasiado:
        return _no("demasiado_grande", demasiado)
    if Path(dst).suffix and es_ejecutable(Path(dst).name):
        return _no("ejecutable", "Morgan no crea programas ni scripts.")

    reales, auditoria = _autorizar("copy_path", "COPIAR", [(dst, {"nueva": True})],
                                   detalle=f"{origen} → {dst}")
    if reales is None:
        return auditoria
    destino = reales[0]
    control.sin_vuelta()

    if origen.is_file():
        temporal = _temporal_junto_a(destino)
        try:
            with open(temporal, "xb") as f:
                _copiar_bytes(origen, f)
                f.flush()
                os.fsync(f.fileno())
                final = _ruta_final(descriptor=f.fileno())
            if not _en_su_sitio(final, destino.parent):
                _deshacer(temporal)
                return _no("cambiado", FUERA_DE_SITIO, **auditoria)
            os.rename(temporal, destino)
        except FileExistsError:
            _deshacer(temporal)
            return _no("ya_existe", "Ya existe algo con ese nombre: no se pisa.", **auditoria)
        except OSError as exc:
            _deshacer(temporal)
            return _no("no_copiado", f"No se pudo copiar: {exc.strerror or exc}", **auditoria)
        esperada = _huella_de(origen)
        if _huella_de(destino) != esperada:
            return _no("no_verificada", "Se copió, pero al releerlo no coincide.", **auditoria)
        return _ok({"src": str(origen), "dst": str(destino), "bytes": destino.stat().st_size,
                    "sha256": esperada, "comprobado": "la copia es idéntica al original"},
                   **auditoria, sha256_despues=esperada)

    # Una carpeta: en una temporal junto al destino, y se renombra al final.
    temporal = _temporal_junto_a(destino)
    try:
        os.mkdir(temporal)
        final = _ruta_final(temporal)
        if not _en_su_sitio(final, destino.parent):
            _deshacer(temporal)
            return _no("cambiado", FUERA_DE_SITIO, **auditoria)
        for ruta, relativa, _tam in archivos:
            nuevo = temporal / relativa
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            with open(nuevo, "xb") as f:
                _copiar_bytes(ruta, f)
        os.rename(temporal, destino)
    except FileExistsError:
        _deshacer(temporal)
        return _no("ya_existe", "Ya existe algo con ese nombre: no se pisa.", **auditoria)
    except OSError as exc:
        _deshacer(temporal)
        return _no("no_copiado", f"No se pudo copiar: {exc.strerror or exc}", **auditoria)
    copiados = sum(1 for p in destino.rglob("*") if p.is_file())
    if copiados != len(archivos):
        return _no("no_verificada", f"Se copiaron {copiados} de {len(archivos)} archivos.", **auditoria)
    datos = {"src": str(origen), "dst": str(destino), "files": copiados,
             "bytes": sum(t for _, _, t in archivos),
             "comprobado": f"los {copiados} archivos están en la copia"}
    if omitidos:
        datos["omitidos"] = _resumen_omitidos(omitidos)
    return _ok(datos, **auditoria)


# --- Comprimir y descomprimir ---

def _comprobacion_de_zip(d: dict) -> dict:
    return ({"archivo": d["dst"], "sha256": d["sha256"]} if d.get("sha256")
            else {"carpeta": d["dst"]})


@_una_sola_vez(_comprobacion_de_zip)
def compress(accion: str = "", paths: list | None = None, path: str = "", dst: str = "", **_) -> dict:
    """`accion="comprimir"`: `paths` (archivos o carpetas) en un `.zip` nuevo, `dst`.
    `accion="descomprimir"`: el `.zip` de `path` en una carpeta nueva, `dst`."""
    if accion == "comprimir":
        # `path` también vale, si no viene `paths`: con una sola cosa, el modelo lo usa.
        return _comprimir(paths if isinstance(paths, list) else [paths] if paths else
                          [path] if path else [], dst)
    if accion == "descomprimir":
        return _descomprimir(path, dst)
    return _no("accion", "accion tiene que ser «comprimir» o «descomprimir».")


def _comprimir(paths: list, dst: str) -> dict:
    if not paths or len(paths) > 50 or not all(isinstance(p, str) for p in paths):
        return _no("argumentos", "Hacen falta entre 1 y 50 rutas en paths.")
    if not str(dst).lower().endswith(".zip"):
        return _no("argumentos", "El destino tiene que acabar en .zip.")
    politica = Politica.cargar()
    todos, omitidos, nombres = [], [], set()
    for texto in paths:
        origen, rechazo = _leible(texto, politica)
        if origen is None:
            return rechazo
        if origen.is_file():
            # Programas y scripts también (decisión mía, 4.16): un .zip no ejecuta nada.
            elegidos = [(origen, Path(origen.name), origen.stat().st_size)]
        else:
            dentro, fuera = _recorrer(origen, politica, con_programas=True)
            elegidos = [(r, Path(origen.name) / rel, t) for r, rel, t in dentro]
            omitidos += [(f"{origen.name}/{rel}", m) for rel, m in fuera]
        for ruta, nombre, tam in elegidos:
            clave = str(nombre).lower()
            if clave in nombres:
                return _no("repetido", f"Dos cosas se llamarían igual dentro del .zip: {nombre}.")
            nombres.add(clave)
            todos.append((ruta, nombre, tam))
    if not todos:
        return _no("vacio", "No queda nada que comprimir.")
    demasiado = _cabe(todos)
    if demasiado:
        return _no("demasiado_grande", demasiado)

    reales, auditoria = _autorizar("compress", "COMPRIMIR", [(dst, {"nueva": True})])
    if reales is None:
        return auditoria
    destino = reales[0]
    control.sin_vuelta()
    temporal = _temporal_junto_a(destino)
    try:
        with open(temporal, "xb") as f:
            final = _ruta_final(descriptor=f.fileno())
            with zipfile.ZipFile(f, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for ruta, nombre, _tam in todos:
                    z.write(ruta, PurePosixPath(*nombre.parts).as_posix())
        if not _en_su_sitio(final, destino.parent):
            _deshacer(temporal)
            return _no("cambiado", FUERA_DE_SITIO, **auditoria)
        os.rename(temporal, destino)
    except FileExistsError:
        _deshacer(temporal)
        return _no("ya_existe", "Ya existe algo con ese nombre: no se pisa.", **auditoria)
    except OSError as exc:
        _deshacer(temporal)
        return _no("no_comprimido", f"No se pudo comprimir: {exc.strerror or exc}", **auditoria)
    with zipfile.ZipFile(destino) as z:
        if z.testzip() is not None or len(z.namelist()) != len(todos):
            return _no("no_verificada", "El .zip no se lee bien después de hacerlo.", **auditoria)
    huella = _huella_de(destino)
    datos = {"dst": str(destino), "files": len(todos), "bytes": destino.stat().st_size,
             "sha256": huella, "comprobado": f"el .zip se lee y tiene {len(todos)} archivos"}
    if omitidos:
        datos["omitidos"] = _resumen_omitidos(omitidos)
    return _ok(datos, **auditoria, sha256_despues=huella)


def _nombre_seguro(nombre: str) -> PurePosixPath | None:
    """La ruta de una entrada, si se queda dentro de la carpeta de destino."""
    limpio = nombre.replace("\\", "/")
    if not limpio or limpio.startswith("/") or ":" in limpio:
        return None
    partes = PurePosixPath(limpio).parts
    if any(p in ("..", "") for p in partes):
        return None
    return PurePosixPath(*partes)


def _descomprimir(path: str, dst: str) -> dict:
    politica = Politica.cargar()
    origen, rechazo = _leible(path, politica)
    if origen is None:
        return rechazo
    if not origen.is_file() or not zipfile.is_zipfile(origen):
        return _no("no_es_zip", "Eso no es un .zip.")

    with zipfile.ZipFile(origen) as z:
        entradas = z.infolist()
        if len(entradas) > MAX_ENTRADAS_ZIP:
            return _no("demasiado_grande", f"Tiene {len(entradas)} entradas; como mucho {MAX_ENTRADAS_ZIP}.")
        elegidas, omitidos, nombres = [], [], set()
        for e in entradas:
            nombre = _nombre_seguro(e.filename)
            if nombre is None:
                return _no("zip_peligroso", f"Una entrada saldría de la carpeta de destino: {e.filename!r}. "
                                            "No se ha sacado nada.")
            if stat.S_ISLNK(e.external_attr >> 16):
                omitidos.append((e.filename, "enlace"))
                continue
            if e.is_dir():
                continue
            if es_ejecutable(nombre.name):
                omitidos.append((e.filename, "programa o script"))
                continue
            if _es_sensible(nombre.name) or any(p.lower() in CARPETAS_SENSIBLES for p in nombre.parts):
                omitidos.append((e.filename, "credenciales"))
                continue
            if e.compress_size and e.file_size / e.compress_size > MAX_PROPORCION:
                return _no("zip_peligroso", f"{e.filename!r} se infla más de {MAX_PROPORCION} veces "
                                            "al sacarlo: parece una bomba. No se ha sacado nada.")
            clave = str(nombre).lower()
            if clave in nombres:
                return _no("zip_peligroso", f"Dos entradas se llaman igual: {e.filename!r}.")
            nombres.add(clave)
            elegidas.append((e, nombre))
        if sum(e.file_size for e, _ in elegidas) > MAX_BYTES_ZIP:
            return _no("demasiado_grande", f"Sacado ocuparía más de {MAX_BYTES_ZIP // 2**20} MB.")

        # Una carpeta vacía que ya existe también vale (medido con el modelo real: creaba
        # la carpeta en un paso y la descompresión fallaba por «ya existe»). Se sustituye
        # por la nueva al final, solo si sigue vacía.
        try:
            vacia = Path(dst).is_dir() and not any(Path(dst).iterdir())
        except (OSError, TypeError):
            vacia = False
        reales, auditoria = _autorizar("compress", "DESCOMPRIMIR",
                                       [(dst, {"archivo": False} if vacia else {"nueva": True})],
                                       detalle=f"{origen} → {dst}")
        if reales is None:
            return auditoria
        destino = reales[0]
        control.sin_vuelta()
        temporal = _temporal_junto_a(destino)
        sacados = 0
        try:
            os.mkdir(temporal)
            final = _ruta_final(temporal)
            if not _en_su_sitio(final, destino.parent):
                _deshacer(temporal)
                return _no("cambiado", FUERA_DE_SITIO, **auditoria)
            for e, nombre in elegidas:
                nuevo = temporal.joinpath(*nombre.parts)
                nuevo.parent.mkdir(parents=True, exist_ok=True)
                escrito = 0
                with z.open(e) as entrada, open(nuevo, "xb") as salida:
                    # El tamaño se cuenta mientras se saca: el índice del .zip puede mentir.
                    for trozo in iter(lambda: entrada.read(TROZO), b""):
                        escrito += len(trozo)
                        sacados += len(trozo)
                        if escrito > max(e.file_size, 0) or sacados > MAX_BYTES_ZIP:
                            raise Denegado("zip_peligroso", "Sacado ocupa más de lo que dice el .zip.")
                        salida.write(trozo)
            if vacia:
                if any(destino.iterdir()):
                    raise FileExistsError(str(destino))
                os.rmdir(destino)
            os.rename(temporal, destino)
        except Denegado as exc:
            _deshacer(temporal)
            return _no(exc.motivo, f"{exc} No se ha sacado nada.", **auditoria)
        except FileExistsError:
            _deshacer(temporal)
            return _no("ya_existe", "Ya existe algo con ese nombre: no se pisa.", **auditoria)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            _deshacer(temporal)
            return _no("no_descomprimido", f"No se pudo descomprimir: {exc}", **auditoria)

    hay = sum(1 for p in destino.rglob("*") if p.is_file())
    if hay != len(elegidas):
        return _no("no_verificada", f"Se sacaron {hay} de {len(elegidas)} archivos.", **auditoria)
    datos = {"src": str(origen), "dst": str(destino), "files": hay, "bytes": sacados,
             "comprobado": f"los {hay} archivos están en la carpeta"}
    if omitidos:
        datos["omitidos"] = _resumen_omitidos(omitidos)
    return _ok(datos, **auditoria)


#: Las de la 4.6, por el nombre con el que las anuncia el agente.
ARCHIVOS_ESCRITURA = {"copy_path": copy_path, "compress": compress}
ARCHIVOS_LECTURA = {"file_info": file_info}
