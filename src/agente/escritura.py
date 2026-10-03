"""
Lo que el agente sabe cambiar en el PC (3.3): crear, editar, crear carpetas, mover y
borrar. Ver el diseño en docs/plan-3.0.md §V3.3 y mis decisiones del 2026-09-24.

## El flujo del plan, y quién hace cada eslabón

`Request → Plan → Policy → Confirmation → Execute → Verify → Audit`

- **Request → Plan**: en la nube. Cada una de estas solo se ejecuta con un paso de plan
  **aprobado por la persona**, con esos argumentos exactos, una vez (`exige_plan`).
- **Policy**: aquí, `motor.evaluar(...)`, contra las **carpetas de escritura** (una lista
  aparte) y nunca en las zonas prohibidas. Lo hace `_autorizar`, que es **el único
  camino** hacia una escritura: ninguna capacidad toca el disco sin pasar por él.
- **Confirmation**: también en `_autorizar`, si el motor la exige (borrar, por
  defecto): una notificación de Windows (`aviso.py`). Solo «Permitir» es un sí.
- **Execute**: sin estados a medias. Se escribe en un temporal junto al destino y se
  cambia de golpe. Crear y mover **no pisan** nada que exista. Editar vuelve a mirar
  el archivo justo antes de cambiarlo y no sigue si alguien lo tocó entre medias.
- **Verify**: se relee lo escrito y se compara su huella con la esperada.
- **Audit**: cada resultado lleva en `_auditoria` qué contestó la persona y las
  huellas; el ejecutor lo anota en la auditoría del PC y no lo deja salir.

## Lo que no hacen, a propósito

- **No crean ni cambian programas o scripts** (`.exe`, `.bat`, `.ps1`…; decisión
  mía): crear uno es la puerta a ejecutar, que es la 3.5.
- **No escriben binarios**: el contenido es texto (UTF-8).
- **No borran carpetas con cosas dentro**, y lo borrado va **a la Papelera** (decisión
  mía): un error se deshace desde Windows.
- **No devuelven contenido del archivo**: dicen qué hicieron, no lo que había.
"""

import functools
import hashlib
import inspect
import json
import os
import secrets
import sys
import time
from pathlib import Path

from src.agente import aviso, control, motor
from src.agente import estado as almacen
from src.agente.politica import Denegado, Politica, es_ejecutable

#: Lo más que se escribe de una vez (crear, o el texto nuevo de una edición).
MAX_CONTENIDO = 256 * 1024
#: El archivo más grande que se edita: se lee entero para cambiarlo.
MAX_EDITABLE = 2 * 1024 * 1024
#: Cuánto se guarda la versión anterior de lo editado, para poder volver atrás.
DIAS_RESPALDO = 7
#: Cuánto se recuerda lo hecho para no hacerlo dos veces (`_una_sola_vez`).
RECUERDA_HECHAS = 3600
MAX_HECHAS = 200


def _no(motivo: str, mensaje: str, **auditoria) -> dict:
    r = {"success": False, "data": None, "error": mensaje, "motivo": motivo}
    if auditoria:
        r["_auditoria"] = auditoria
    return r


def _ok(datos: dict, **auditoria) -> dict:
    return {"success": True, "data": datos, "error": None, "_auditoria": auditoria}


def _huella(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _autorizar(capacidad: str, que: str, rutas: list[tuple[str | None, dict]], detalle: str | None = None):
    """Política y confirmación, para una o dos rutas (mover tiene origen y destino).

    Devuelve `(rutas_reales, auditoria)` o `(None, rechazo)`. **Es el único camino**
    hacia una escritura: las cinco capacidades lo llaman antes de tocar nada.
    """
    operacion = motor.OPERACION_DE.get(capacidad)

    def evaluar_todas():
        politica = Politica.cargar()
        reales, exige = [], False
        for ruta, opciones in rutas:
            decision = motor.evaluar(operacion or "?", ruta, capacidad=capacidad,
                                     politica=politica, **opciones)
            if not decision:
                return None, decision, False
            reales.append(decision.ruta)
            exige = exige or decision.exige_confirmacion
        return reales, None, exige

    reales, negada, exige = evaluar_todas()
    if reales is None:
        return None, _no(negada.motivo, negada.mensaje)
    if not exige:
        return reales, {"confirmacion": "no_hacia_falta"}

    antes = [_identidad(r) for r in reales]
    detalle = detalle or " → ".join(str(r) for r in reales)
    respuesta = aviso.preguntar(f"Morgan quiere {que}", detalle)
    # Si la persona paró el turno mientras se le preguntaba (3.4), es una cancelación,
    # no un «no»: se dice como tal.
    control.punto_seguro()
    if respuesta != aviso.PERMITIDA:
        return None, _no("no_confirmada",
                         "La persona no lo permitió en su PC (o no contestó a tiempo). "
                         "No se ha cambiado nada.", confirmacion=respuesta)

    # Lo que se hace tiene que ser **lo que se enseñó** (3.3.5, «confirmación que no
    # coincide»). La persona puede tardar hasta dos minutos en contestar, y en ese rato
    # el archivo puede cambiarse por otro con el mismo nombre, o su carpeta por un
    # enlace a otro sitio: encontrado atacándolo, se borraba igual. Se vuelve a pasar
    # por la política y se exige el mismo objeto del disco.
    despues, negada, _ = evaluar_todas()
    if despues is None or [_clave(r) for r in despues] != [_clave(r) for r in reales]             or [_identidad(r) for r in despues] != antes:
        return None, _no("cambiado", "Cambió mientras la persona decidía: no se ha hecho nada. "
                         "Hay que volver a pedirlo.", confirmacion=aviso.PERMITIDA)
    return reales, {"confirmacion": aviso.PERMITIDA}


# --- Repetir sin hacerlo dos veces (3.3.5) ---

def _fichero_de_hechas() -> Path:
    return almacen.carpeta() / "hechas.json"


def _hechas() -> dict:
    try:
        datos = json.loads(_fichero_de_hechas().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    limite = time.time() - RECUERDA_HECHAS
    return {k: v for k, v in datos.items() if isinstance(v, dict) and v.get("momento", 0) > limite}


def _sigue_igual(comprobacion: dict) -> bool:
    """Si el disco está como lo dejó el cambio apuntado."""
    try:
        if "archivo" in comprobacion:
            return _huella(Path(comprobacion["archivo"]).read_bytes()) == comprobacion["sha256"]
        if "carpeta" in comprobacion:
            return Path(comprobacion["carpeta"]).is_dir()
        if "no_existe" in comprobacion:
            ya_no = not os.path.lexists(comprobacion["no_existe"])
            return ya_no and ("existe" not in comprobacion or os.path.lexists(comprobacion["existe"]))
    except OSError:
        return False
    return False


def _una_sola_vez(comprobacion):
    """Que una orden repetida no se haga dos veces, **aunque llegue con otro
    `command_id`** (3.3.5, «petición duplicada» y «respuesta perdida»).

    Encontrado atacándolo: si la respuesta se pierde, el paso del plan no se gasta y el
    modelo puede repetir la llamada. La memoria de órdenes del ejecutor no la reconoce
    (otro `command_id`, o el agente se reinició), y entonces:

    - una edición cuyo texto nuevo contiene el viejo **se aplicaba otra vez** («pan» →
      «pan integral» → «pan integral integral»);
    - crear, mover o borrar algo ya hecho devolvía un **error**, y Morgan le decía a la
      persona que había fallado lo que sí se hizo.

    Así que cada cambio hecho se apunta, en disco, con **cómo dejó las cosas**
    (`comprobacion(datos)`). Si en la hora siguiente llega la misma capacidad con los
    mismos argumentos y el disco sigue como lo dejó, se contesta «ya estaba hecho», con
    lo mismo que la primera vez, sin tocar nada. Si el disco cambió, se hace de nuevo,
    con todas sus comprobaciones.
    """
    def envolver(funcion):
        firma_de = inspect.signature(funcion)

        @functools.wraps(funcion)
        def capacidad(*args, **kwargs):
            try:
                atados = firma_de.bind(*args, **kwargs)
            except TypeError:
                return funcion(*args, **kwargs)     # que la capacidad diga qué falta
            atados.apply_defaults()
            argumentos = {k: v for k, v in atados.arguments.items() if k != "_"}
            # El paso del plan del que sale (`origen`, «plan#paso») también cuenta (4.15): un
            # reintento de la MISMA orden trae el mismo y no se hace dos veces, pero cada
            # ejecución de una automatización trae su propio plan. Sin esto, una que copia
            # cada hora con los mismos argumentos contestaba «ya estaba hecho» sin hacerlo
            # si la anterior había llegado unos segundos tarde.
            if (origen := (atados.arguments.get("_") or {}).get("origen")):
                argumentos["origen"] = origen
            firma = _huella(json.dumps([funcion.__name__, argumentos], sort_keys=True,
                                       default=str, ensure_ascii=False).encode("utf-8"))
            hechas = _hechas()
            anterior = hechas.get(firma)
            if anterior and _sigue_igual(anterior["comprobacion"]):
                return _ok({**anterior["datos"], "ya_hecho": True},
                           confirmacion="no_hacia_falta", ya_hecho=True)

            resultado = funcion(*args, **kwargs)
            if resultado.get("success"):
                hechas[firma] = {"momento": time.time(), "datos": resultado["data"],
                                 "comprobacion": comprobacion(resultado["data"])}
                recientes = sorted(hechas.items(), key=lambda kv: kv[1]["momento"])[-MAX_HECHAS:]
                try:
                    almacen.escribir_atomico(_fichero_de_hechas(), json.dumps(dict(recientes)).encode("utf-8"))
                except OSError:
                    pass
            return resultado
        return capacidad
    return envolver


def _clave(ruta: Path) -> str:
    return os.path.normcase(str(ruta))


def _identidad(ruta: Path | None):
    """Qué objeto del disco es: el mismo nombre puede ser otro archivo después."""
    if ruta is None:
        return None
    try:
        info = os.lstat(ruta)
    except OSError:
        return None
    return (info.st_ino, info.st_dev, info.st_size, info.st_mtime_ns)


def _temporal_junto_a(destino: Path) -> Path:
    """Un temporal en la misma carpeta: así el cambio final es un renombrado atómico."""
    return destino.parent / f".morgan-{secrets.token_hex(6)}.tmp"


def _ruta_final(ruta: Path | None = None, *, descriptor: int | None = None) -> str | None:
    """Dónde está **de verdad** un archivo o carpeta, según Windows y no según su nombre
    (`GetFinalPathNameByHandleW`). Con `descriptor`, el del archivo ya abierto: esa
    respuesta no cambia aunque alguien cambie después las carpetas del camino."""
    if sys.platform != "win32":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    propio = descriptor is None
    if propio:
        FILE_SHARE_TODO, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS = 7, 3, 0x02000000
        mango = k.CreateFileW(str(ruta), 0, FILE_SHARE_TODO, None, OPEN_EXISTING,
                              FILE_FLAG_BACKUP_SEMANTICS, None)
        if mango in (None, wintypes.HANDLE(-1).value):
            return None
    else:
        mango = msvcrt.get_osfhandle(descriptor)
    try:
        buf = ctypes.create_unicode_buffer(32768)
        if not k.GetFinalPathNameByHandleW(mango, buf, 32768, 0):
            return None
        final = buf.value
        return final[4:] if final.startswith("\\\\?\\") else final
    finally:
        if propio:
            k.CloseHandle(mango)


def _en_su_sitio(final: str | None, padre: Path) -> bool:
    """Si lo que se creó quedó en la carpeta aprobada. Sin forma de saberlo, sí."""
    return final is None or os.path.normcase(os.path.dirname(final)) == os.path.normcase(str(padre))


#: Lo que se dice cuando algo acaba donde no debía (3.3.5).
FUERA_DE_SITIO = ("La carpeta cambió mientras se escribía (¿un enlace a otro sitio?): "
                  "no se ha hecho nada.")


def _escribir_temporal(destino: Path, datos: bytes) -> Path:
    """El temporal, **comprobado en su sitio** antes de usarlo (3.3.5).

    Encontrado atacándolo: si justo después de la política la carpeta se cambia por un
    enlace a otro sitio, el temporal se escribía allí y el renombrado lo dejaba fuera de
    las carpetas de escritura. Hace falta un programa que ya corra con los permisos de
    la persona (fuera del modelo de amenazas), pero comprobarlo es barato: se pregunta
    a Windows dónde está el archivo **abierto**, que no depende del nombre. Y el
    renombrado final va por el mismo camino que el temporal: si el camino cambia
    después, el temporal ya no está ahí y el renombrado falla.
    """
    temporal = _temporal_junto_a(destino)
    with open(temporal, "xb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
        final = _ruta_final(descriptor=f.fileno())
    if not _en_su_sitio(final, destino.parent):
        try:
            os.unlink(final)
        except OSError:
            pass
        raise Denegado("cambiado", FUERA_DE_SITIO)
    return temporal


def _texto(contenido) -> bytes | dict:
    if not isinstance(contenido, str):
        return _no("contenido", "El contenido tiene que ser texto.")
    if "\x00" in contenido:
        return _no("contenido", "El contenido tiene un carácter nulo: no es texto.")
    datos = contenido.encode("utf-8")
    if len(datos) > MAX_CONTENIDO:
        return _no("demasiado_grande", f"Como mucho {MAX_CONTENIDO // 1024} KB de una vez.")
    return datos


# --- Crear ---

@_una_sola_vez(lambda d: {"archivo": d["path"], "sha256": d["sha256"]})
def create_file(path: str, content: str = "", **_) -> dict:
    """Crea un archivo de texto **nuevo**. Si ya existe, no lo pisa."""
    datos = _texto(content)
    if isinstance(datos, dict):
        return datos
    reales, auditoria = _autorizar("create_file", "CREAR un archivo", [(path, {"nueva": True})])
    if reales is None:
        return auditoria
    destino = reales[0]

    # A partir de aquí no se para (3.4): o no se ha tocado nada, o se termina y se dice.
    control.sin_vuelta()
    try:
        temporal = _escribir_temporal(destino, datos)
    except Denegado as exc:
        return _no(exc.motivo, str(exc), **auditoria)
    try:
        # En Windows, renombrar sobre algo que ya existe falla: si alguien lo creó
        # después de comprobarlo, no se pisa.
        os.rename(temporal, destino)
    except FileExistsError:
        temporal.unlink(missing_ok=True)
        return _no("ya_existe", "Ya existe algo con ese nombre: no se pisa.", **auditoria)
    except OSError:
        temporal.unlink(missing_ok=True)
        raise

    esperada = _huella(datos)
    if _huella(destino.read_bytes()) != esperada:
        return _no("no_verificada", "Se escribió, pero al releerlo no coincide.", **auditoria)
    return _ok({"path": str(destino), "bytes": len(datos), "sha256": esperada},
               **auditoria, sha256_despues=esperada)


def _ya_existe_la_carpeta(path: str) -> Path | None:
    """Si `path` es una carpeta que ya existe **donde se puede escribir** (dentro de una
    carpeta de escritura, o una de ellas). Fuera, nada: no se confirma qué existe."""
    try:
        politica = Politica.cargar()
        real = Path(os.path.normpath(path)).resolve(strict=True)
        if not real.is_dir():
            return None
        if any(_clave(real) == _clave(Path(c).resolve()) for c in politica.escritura):
            return real
        return politica.resolver_escritura(path, archivo=False)
    except (Denegado, OSError, TypeError, ValueError):
        return None


@_una_sola_vez(lambda d: {"carpeta": d["path"]})
def create_folder(path: str, **_) -> dict:
    reales, auditoria = _autorizar("create_folder", "CREAR una carpeta", [(path, {"nueva": True})])
    if reales is None:
        # Si ya existe, está hecho (4.6). Medido con el modelo real: planeaba «crear la
        # carpeta destino si no existe» antes de copiar o descomprimir, y el plan se paraba
        # en ese paso porque la carpeta ya estaba. No se toca nada.
        existente = _ya_existe_la_carpeta(path) if auditoria.get("motivo") == "ya_existe" else None
        if existente is not None:
            return _ok({"path": str(existente), "ya_existia": True, "comprobado": "la carpeta ya existía"},
                       confirmacion="no_hacia_falta")
        return auditoria
    destino = reales[0]
    control.sin_vuelta()
    try:
        os.mkdir(destino)
    except FileExistsError:
        return _no("ya_existe", "Ya existe algo con ese nombre.", **auditoria)
    final = _ruta_final(destino)
    if not _en_su_sitio(final, destino.parent):
        try:
            os.rmdir(final)                     # está vacía: la acabamos de crear
        except OSError:
            pass
        return _no("cambiado", FUERA_DE_SITIO, **auditoria)
    if not destino.is_dir():
        return _no("no_verificada", "La carpeta no aparece después de crearla.", **auditoria)
    return _ok({"path": str(destino), "comprobado": "la carpeta existe"}, **auditoria)


# --- Editar ---

def _respaldar(ruta: Path, datos: bytes) -> str:
    """Guarda la versión anterior en la carpeta del agente (Morgan no puede leerla ni
    tocarla desde la nube). Y de paso borra las de más de `DIAS_RESPALDO` días."""
    carpeta = almacen.carpeta() / "respaldos"
    carpeta.mkdir(parents=True, exist_ok=True)
    limite = time.time() - DIAS_RESPALDO * 86400
    for viejo in carpeta.iterdir():
        try:
            if viejo.stat().st_mtime < limite:
                viejo.unlink()
        except OSError:
            pass
    nombre = f"{time.strftime('%Y%m%d-%H%M%S')}-{_huella(datos)[:8]}-{ruta.name}"
    (carpeta / nombre).write_bytes(datos)
    return nombre


@_una_sola_vez(lambda d: {"archivo": d["path"], "sha256": d["sha256"]})
def edit_file(path: str, old_text: str = "", new_text: str = "", **_) -> dict:
    """Cambia un fragmento **exacto** por otro. El fragmento tiene que estar una sola vez.

    Cambia un trozo y no reescribe el archivo entero: el modelo no tiene que mandar (ni
    inventarse) lo que no cambia, y cabe en el tope de tokens de Groq.
    """
    if not isinstance(old_text, str) or not old_text:
        return _no("fragmento", "Falta el texto que hay que cambiar (old_text).")
    nuevo = _texto(new_text)
    if isinstance(nuevo, dict):
        return nuevo
    if es_ejecutable(Path(str(path)).name):
        return _no("ejecutable", "Morgan no crea ni cambia programas ni scripts.")
    reales, auditoria = _autorizar("edit_file", "EDITAR un archivo", [(path, {"archivo": True})])
    if reales is None:
        return auditoria
    ruta = reales[0]
    leido = _leer_para_cambiar(ruta, auditoria)
    if isinstance(leido, dict):
        return leido
    original, texto, bom = leido

    antes, despues = old_text, new_text
    if texto.count(antes) == 0 and "\r\n" in texto and "\n" in antes and "\r\n" not in antes:
        # El modelo escribe saltos de línea `\n`; un archivo de Windows los tiene `\r\n`.
        antes, despues = antes.replace("\n", "\r\n"), despues.replace("\n", "\r\n")
    veces = texto.count(antes)
    if veces == 0:
        return _no("no_encontrado", "Ese texto no está en el archivo. Léelo otra vez y copia el "
                   "fragmento exacto.", **auditoria)
    if veces > 1:
        return _no("varias_veces", f"Ese texto está {veces} veces: añade las líneas de alrededor "
                   "para que sea único.", **auditoria)
    datos = texto.replace(antes, despues).encode("utf-8")
    datos = (b"\xef\xbb\xbf" + datos) if bom else datos
    return _reescribir(ruta, original, datos, auditoria)


def _leer_para_cambiar(ruta: Path, auditoria: dict):
    """El archivo que se va a cambiar, como texto: `(original, texto, bom)`, o el rechazo."""
    if es_ejecutable(ruta.name):
        return _no("ejecutable", "Morgan no crea ni cambia programas ni scripts.", **auditoria)
    if ruta.stat().st_size > MAX_EDITABLE:
        return _no("demasiado_grande", f"Solo se editan archivos de hasta {MAX_EDITABLE // (1024 * 1024)} MB.",
                   **auditoria)
    original = ruta.read_bytes()
    if b"\x00" in original:
        return _no("binario", "Es un archivo binario: no se edita como texto.", **auditoria)
    try:
        texto = original.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _no("no_es_texto", "No está en UTF-8: no se edita, para no estropearlo.", **auditoria)
    return original, texto, original.startswith(b"\xef\xbb\xbf")


@_una_sola_vez(lambda d: {"archivo": d["path"], "sha256": d["sha256"]})
def append_file(path: str, content: str = "", origen: str = "", **_) -> dict:
    """Añade texto **al final** de un archivo de texto que ya existe (4.1, decisión mía).

    Sin esto, añadir una línea a un registro cuya última línea se repite era imposible con
    `edit_file` (su fragmento tiene que estar una sola vez), y el modelo, medido, propuso
    borrar el registro y crearlo de nuevo. Lo añadido empieza en su propia línea y con los
    saltos de línea del archivo; lo de antes no se toca, y queda respaldado como al editar.

    `origen` es el paso del plan del que sale (lo pone la nube, 4.1) y **entra en la huella
    de `_una_sola_vez`**: el mismo paso repetido (una respuesta perdida) no se añade dos
    veces, pero otro plan que pide añadir lo mismo, sí. Medido: sin él, una segunda «copia
    hecha» el mismo día se daba por ya hecha y no se anotaba.
    """
    if not isinstance(content, str) or not content:
        return _no("vacio", "No hay nada que añadir (content).")
    nuevo = _texto(content)
    if isinstance(nuevo, dict):
        return nuevo
    if es_ejecutable(Path(str(path)).name):
        return _no("ejecutable", "Morgan no crea ni cambia programas ni scripts.")
    reales, auditoria = _autorizar("append_file", "AÑADIR al final de un archivo", [(path, {"archivo": True})])
    if reales is None:
        return auditoria
    ruta = reales[0]
    leido = _leer_para_cambiar(ruta, auditoria)
    if isinstance(leido, dict):
        return leido
    original, texto, bom = leido

    salto = "\r\n" if "\r\n" in texto else "\n"
    anadido = content.replace("\r\n", "\n").replace("\n", salto)
    if texto and not texto.endswith("\n"):
        anadido = salto + anadido
    datos = (texto + anadido).encode("utf-8")
    datos = (b"\xef\xbb\xbf" + datos) if bom else datos
    if len(datos) > MAX_EDITABLE:
        return _no("demasiado_grande", f"Con lo añadido pasaría de {MAX_EDITABLE // (1024 * 1024)} MB.",
                   **auditoria)
    return _reescribir(ruta, original, datos, auditoria)


def _reescribir(ruta: Path, original: bytes, datos: bytes, auditoria: dict) -> dict:
    """Cambia `ruta` de `original` a `datos`: respaldo, escritura atómica, sin pisar un
    cambio ajeno de entre medias, y releído. Lo comparten editar y añadir."""
    huella_antes = _huella(original)
    control.sin_vuelta()
    respaldo = _respaldar(ruta, original)
    try:
        temporal = _escribir_temporal(ruta, datos)
    except Denegado as exc:
        return _no(exc.motivo, str(exc), **auditoria)
    try:
        # TOCTOU (§18, la 3.3.5 lo atacará): justo antes de cambiarlo, ¿sigue siendo el
        # archivo que se leyó? Si alguien lo tocó entre medias, no se pisa su cambio.
        if _huella(ruta.read_bytes()) != huella_antes:
            temporal.unlink(missing_ok=True)
            return _no("cambiado", "El archivo cambió mientras se editaba: no se toca.", **auditoria)
        os.replace(temporal, ruta)
    except OSError:
        temporal.unlink(missing_ok=True)
        raise

    esperada = _huella(datos)
    if _huella(ruta.read_bytes()) != esperada:
        return _no("no_verificada", "Se escribió, pero al releerlo no coincide.", **auditoria)
    return _ok({"path": str(ruta), "bytes": len(datos), "sha256": esperada},
               **auditoria, sha256_antes=huella_antes, sha256_despues=esperada, respaldo=respaldo)


# --- Mover y renombrar ---

@_una_sola_vez(lambda d: {"no_existe": d["src"], "existe": d["dst"]})
def move_file(src: str, dst: str, **_) -> dict:
    """Mueve o renombra. Los dos lados, en carpetas de escritura; el destino, nuevo."""
    reales, auditoria = _autorizar("move_file", "MOVER o RENOMBRAR",
                                   [(src, {}), (dst, {"nueva": True})])
    if reales is None:
        return auditoria
    origen, destino = reales
    if origen.is_file() and es_ejecutable(origen.name):
        return _no("ejecutable", "Morgan no crea ni cambia programas ni scripts.", **auditoria)
    control.sin_vuelta()
    try:
        os.rename(origen, destino)          # falla si el destino ya existe: no pisa
    except FileExistsError:
        return _no("ya_existe", "Ya existe algo con ese nombre en el destino: no se pisa.", **auditoria)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 17:
            return _no("otro_disco", "No se mueve entre discos distintos.", **auditoria)
        raise
    final = _ruta_final(destino)
    if not _en_su_sitio(final, destino.parent):
        try:
            os.rename(final, origen)            # se deshace: vuelve a donde estaba
        except OSError:
            pass
        return _no("cambiado", FUERA_DE_SITIO, **auditoria)
    if os.path.lexists(origen) or not os.path.lexists(destino):
        return _no("no_verificada", "Después de moverlo no está donde debería.", **auditoria)
    return _ok({"src": str(origen), "dst": str(destino),
                "comprobado": "ya no está en el origen y sí en el destino"}, **auditoria)


# --- Borrar ---

def a_la_papelera(ruta: Path) -> None:
    """Manda un archivo o carpeta a la Papelera de reciclaje de Windows.

    `FOF_ALLOWUNDO` es la Papelera. **Pero** Windows borra del todo, sin avisar, lo que
    no cabe en ella o lo que está en un disco sin Papelera. Por eso no se ofrece en
    discos extraíbles (lo mira `delete_file`) y `FOF_WANTNUKEWARNING` hace que, si aun
    así fuera a borrarse del todo, Windows pregunte en el PC en vez de hacerlo.
    """
    if sys.platform != "win32":
        raise Denegado("no_disponible", "Solo en Windows.")
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION = 3, 0x4, 0x10
    FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x40, 0x400, 0x4000
    operacion = SHFILEOPSTRUCTW(
        None, FO_DELETE, str(ruta) + "\0", None,
        FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING,
        False, None, None)
    codigo = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operacion))
    if codigo != 0 or operacion.fAnyOperationsAborted:
        raise Denegado("no_borrado", "Windows no lo mandó a la Papelera.")


def _disco_con_papelera(ruta: Path) -> bool:
    """Un disco fijo del PC: los extraíbles no tienen Papelera y se borraría del todo."""
    if sys.platform != "win32":
        return False
    import ctypes

    DRIVE_FIXED = 3
    return ctypes.windll.kernel32.GetDriveTypeW(ruta.anchor) == DRIVE_FIXED


@_una_sola_vez(lambda d: {"no_existe": d["path"]})
def delete_file(path: str, **_) -> dict:
    """Manda a la Papelera un archivo, o una carpeta **vacía**."""
    politica_previa = motor.evaluar("borrar", path, capacidad="delete_file")
    # La comprobación de verdad es la de `_autorizar`, abajo. Esta solo evita preguntar
    # a la persona por algo que luego no se podría borrar igualmente.
    if politica_previa and politica_previa.ruta is not None:
        ruta = politica_previa.ruta
        if ruta.is_dir() and any(ruta.iterdir()):
            return _no("carpeta_no_vacia", "Esa carpeta tiene cosas dentro: solo se borran carpetas vacías.")
        if not _disco_con_papelera(ruta):
            return _no("sin_papelera", "Ese disco no tiene Papelera: se borraría del todo, así que no se borra.")

    reales, auditoria = _autorizar("delete_file", "BORRAR (a la Papelera)", [(path, {})])
    if reales is None:
        return auditoria
    ruta = reales[0]
    # Otra vez, después de la confirmación: pudo cambiar mientras la persona decidía.
    if ruta.is_dir() and any(ruta.iterdir()):
        return _no("carpeta_no_vacia", "Esa carpeta tiene cosas dentro: solo se borran carpetas vacías.",
                   **auditoria)
    if not _disco_con_papelera(ruta):
        return _no("sin_papelera", "Ese disco no tiene Papelera: no se borra.", **auditoria)
    control.sin_vuelta()
    try:
        a_la_papelera(ruta)
    except Denegado as exc:
        return _no(exc.motivo, str(exc), **auditoria)
    if os.path.lexists(ruta):
        return _no("no_verificada", "Después de borrarlo sigue ahí.", **auditoria)
    return _ok({"path": str(ruta), "papelera": True}, **auditoria)


#: Las de la 3.3, por el nombre con el que las anuncia el agente.
ESCRITURA = {"create_file": create_file, "edit_file": edit_file, "append_file": append_file,
             "create_folder": create_folder, "move_file": move_file, "delete_file": delete_file}
assert set(ESCRITURA) <= set(motor.OPERACION_DE)
