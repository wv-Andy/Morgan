"""
El agente instalado y sus actualizaciones (3.8).

Hasta la 3.8 el agente corría desde una copia del repositorio, con el entorno de todo
Morgan, y «actualizar» era `git pull`. Instalado, vive en la carpeta del agente:

    %LOCALAPPDATA%\\Morgan\\agente\\app\\
        3.8.0\\          el código (src\\agente) y su propio entorno (venv\\)
        3.8.5\\
        activa.json     cuál corre, cuál había antes y si la actual está por confirmar

**Actualizar** (mis decisiones, 2026-09-25: firmada por él; se instala si la persona
dice sí): `Release → Verify → Install → Restart → Health Check`.

1. Pedir a la nube el manifiesto y su firma, y comprobarla (`firma.py`): tiene que ser
   mía y **más nueva** que la que corre.
2. Bajar el paquete y comprobar que es el del manifiesto (hash y tamaño).
3. Instalarlo en su carpeta, con su entorno, y **probar que arranca** (`version`).
4. Esperar a que el agente no tenga órdenes, pararlo y cambiar la activa.
5. Lanzar el vigilante nuevo y **esperar 2 minutos a que esté sano** (`READY`, con su
   versión). Si no: pararlo y **volver a la anterior**, y decirlo.

**Quien juzga es el código viejo**, que se sabe sano: todo esto lo hace `actualizar`, un
proceso aparte lanzado desde la versión que corría. Si el PC se apaga a mitad, la nueva
se confirma sola al llegar a `READY`; si nunca llega, `volver` a mano.
"""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from src import __version__
from src.agente import estado as almacen
from src.agente import firma

logger = logging.getLogger(__name__)

#: Lo que tiene la versión nueva para demostrar que está sana.
PLAZO_SANA = 120.0
#: Un salto del reloj mayor que esto entre dos miradas es que el PC estuvo suspendido
#: (el mismo criterio que el vigilante).
SUSPENDIDO = 30.0
#: Lo que se espera a que el agente acabe sus órdenes antes de cambiarlo.
ESPERA_ORDENES = 600.0
#: Cada cuánto mira el agente si hay versión nueva, y cuánto dura un «Luego».
MIRAR_CADA = 6 * 3600.0
POSPONER = 24 * 3600.0


#: Lo que dice el agente congelado (el programa de Windows, 5.0) si se le pide actualizarse,
#: instalarse o volver atrás: eso lo hace el programa entero, no el agente por su cuenta.
PROGRAMA_DE_WINDOWS = ("Este agente es parte del programa Morgan para Windows: se actualiza "
                       "instalando la versión nueva del programa, no por su cuenta.")


def app() -> Path:
    return almacen.carpeta() / "app"


#: La marca de que el agente de este PC lo emparejó el programa de Windows (5.0.1).
MARCA_DEL_PROGRAMA = "programa.json"
NO_ES_DEL_PROGRAMA = ("El agente de este PC no lo instaló el programa de Windows (es el de la línea "
                      "de PowerShell): no se toca. Se quita solo el programa.")


def marcar_del_programa() -> None:
    """El programa de Windows (el agente congelado) apunta que este agente es suyo al
    emparejarlo. Solo entonces su desinstalador lo desempareja y borra su estado.

    **Por qué** (medido, 2026-10-04): instalé el programa en un PC con el agente de la
    línea de PowerShell, que comparte la carpeta del estado; al desinstalar el programa, su
    gancho lanzó `desinstalar` y se llevó por delante al otro agente: desemparejado en la
    nube, su política y su historial borrados."""
    from src.agente import arranque

    if not arranque.congelado():
        return
    almacen.carpeta().mkdir(parents=True, exist_ok=True)
    (almacen.carpeta() / MARCA_DEL_PROGRAMA).write_text(
        json.dumps({"programa": sys.executable, "desde": time.time()}), encoding="utf-8")


def es_del_programa() -> bool:
    return (almacen.carpeta() / MARCA_DEL_PROGRAMA).exists()


def instalada(raiz: Path | None = None) -> bool:
    """Si el código que corre es una versión instalada (y no una copia del repositorio)."""
    from src.agente import arranque

    raiz = (raiz or arranque.raiz_del_proyecto()).resolve()
    try:
        raiz.relative_to(app().resolve())
        return True
    except ValueError:
        return False


def carpeta_de(version: str) -> Path:
    return app() / version


def python_de(version: str) -> Path:
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    return carpeta_de(version) / "venv" / scripts / ("python.exe" if sys.platform == "win32" else "python")


# --- Cuál está activa -------------------------------------------------------------------


def leer_activa() -> dict:
    try:
        datos = json.loads((app() / "activa.json").read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _guardar_activa(datos: dict) -> None:
    app().mkdir(parents=True, exist_ok=True)
    almacen.escribir_atomico(app() / "activa.json", json.dumps(datos, ensure_ascii=False).encode("utf-8"))
    if datos.get("version"):
        _escribir_lanzador(datos["version"])


def lanzador() -> Path:
    return almacen.carpeta() / "morgan-agente.cmd"


def _escribir_lanzador(version: str) -> None:
    """`morgan-agente.cmd`, en la carpeta del agente: los comandos del agente con la versión
    activa, desde cualquier sitio (`morgan-agente.cmd carpetas añadir "C:\\…"`).

    **`-P` y `PYTHONPATH`**, a propósito: con `python -m`, Python busca primero en la
    carpeta desde la que se ejecuta, y una carpeta `src` cualquiera (la de un proyecto
    tuyo) suplantaría al agente. Con `-P` no mira ahí."""
    firma.clave_de_version(version)
    texto = ("@echo off\r\nsetlocal\r\n"
             f'set "PYTHONPATH=%~dp0app\\{version}"\r\n'
             f'"%~dp0app\\{version}\\venv\\Scripts\\python.exe" -P -m src.agente %*\r\n')
    almacen.escribir_atomico(lanzador(), texto.encode("utf-8"))


def cambiar_a(version: str) -> None:
    """La activa pasa a ser `version`, por confirmar; la de ahora queda como anterior. Y el
    arranque de Windows, a ella."""
    from src.agente import arranque

    actual = leer_activa().get("version")
    _guardar_activa({"version": version, "anterior": actual if actual != version else leer_activa().get("anterior"),
                     "pendiente": {"desde": time.time()}})
    if arranque.activado():
        arranque.activar(python_de(version), carpeta_de(version))
    # Siempre, no solo si ya estaba (4.17): quien actualiza desde una versión anterior no lo
    # tenía, y así le aparece «Morgan en tu PC» en el menú Inicio sin reinstalar.
    try:
        arranque.crear_acceso_de_ajustes(python_de(version), carpeta_de(version))
    except Exception:
        logger.warning("No se pudo crear «Morgan en tu PC» en el menú Inicio", exc_info=True)


def confirmar_si_pendiente(version: str) -> bool:
    """La versión que corre llegó a `READY`: queda confirmada, y se borran las que no son
    ni ella ni la anterior. Lo llama el agente al conectarse (por si nadie más lo vio)."""
    datos = leer_activa()
    if datos.get("version") != version or not datos.get("pendiente"):
        return False
    datos["pendiente"] = None
    _guardar_activa(datos)
    _limpiar(datos)
    # «Morgan en tu PC» en el menú Inicio, si falta. Medido al actualizar mi PC a la 4.17:
    # la actualización la hace el agente VIEJO, que no sabe crearlo; el nuevo, al
    # confirmarse, sí (4.17, después de publicarla).
    from src.agente import arranque

    if not arranque.acceso_de_ajustes().exists():
        try:
            arranque.crear_acceso_de_ajustes(python_de(version), carpeta_de(version))
        except Exception:
            logger.warning("No se pudo crear «Morgan en tu PC» en el menú Inicio", exc_info=True)
    return True


def _limpiar(datos: dict) -> None:
    import shutil

    quedan = {datos.get("version"), datos.get("anterior")}
    for carpeta in app().iterdir():
        if carpeta.is_dir() and carpeta.name not in quedan:
            shutil.rmtree(carpeta, ignore_errors=True)


def volver_atras(motivo: str = "") -> str | None:
    """La anterior pasa a ser la activa, y el arranque de Windows, a ella. Devuelve cuál,
    o None si no hay anterior a la que volver."""
    from src.agente import arranque

    datos = leer_activa()
    anterior = datos.get("anterior")
    if not anterior or not python_de(anterior).exists():
        return None
    _guardar_activa({"version": anterior, "anterior": None, "pendiente": None,
                     "fallida": {"version": datos.get("version"), "motivo": motivo, "cuando": time.time()}})
    if arranque.activado():
        arranque.activar(python_de(anterior), carpeta_de(anterior))
    if arranque.acceso_de_ajustes().exists():
        arranque.crear_acceso_de_ajustes(python_de(anterior), carpeta_de(anterior))
    return anterior


# --- Preguntar a la nube ------------------------------------------------------------------


def _cabeceras() -> dict:
    credencial = almacen.credencial()
    return {"Authorization": f"Bearer {credencial}"} if credencial else {}


def novedad(http=None, nube: str | None = None, cabeceras: dict | None = None) -> dict | None:
    """El manifiesto de la versión publicada **si es más nueva** que esta y la
    firmé yo. `FirmaNoValida` si hay algo publicado que no lo es."""
    import httpx

    nube = nube or almacen.cargar().nube
    cliente = http or httpx.Client(timeout=30)
    r = cliente.get(f"{nube}/agente/actualizacion", headers=cabeceras if cabeceras is not None else _cabeceras())
    r.raise_for_status()
    cuerpo = r.json()
    if not cuerpo.get("manifiesto"):
        return None
    manifiesto = firma.verificar(cuerpo["manifiesto"], cuerpo.get("firma") or "")
    return manifiesto if firma.es_mas_nueva(manifiesto, __version__) else None


def bajar(manifiesto: dict, http=None, nube: str | None = None, cabeceras: dict | None = None) -> bytes:
    import httpx

    nube = nube or almacen.cargar().nube
    cliente = http or httpx.Client(timeout=120)
    r = cliente.get(f"{nube}/agente/paquete", headers=cabeceras if cabeceras is not None else _cabeceras())
    r.raise_for_status()
    firma.comprobar_paquete(r.content, manifiesto)
    return r.content


# --- Instalar una versión -------------------------------------------------------------------


def preparar(manifiesto: dict, datos: bytes, ejecutar: Callable = subprocess.run,
             python: str | None = None) -> Path:
    """Abre el paquete en su carpeta, le crea su entorno, instala sus dependencias y
    comprueba que arranca y que es la versión que dice. Si algo falla, no deja nada."""
    import shutil

    version = manifiesto["version"]
    firma.clave_de_version(version)             # también es un nombre de carpeta: solo dígitos y puntos
    nueva = app() / f"{version}.nueva"
    shutil.rmtree(nueva, ignore_errors=True)
    try:
        firma.abrir(datos, nueva)
        base = python or getattr(sys, "_base_executable", None) or sys.executable
        ejecutar([base, "-m", "venv", str(nueva / "venv")], check=True, capture_output=True, timeout=300)
        py = nueva / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / \
            ("python.exe" if sys.platform == "win32" else "python")
        ejecutar([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-q",
                  "-r", str(nueva / "requirements-agente.txt")], check=True, capture_output=True, timeout=900)
        dice = ejecutar([str(py), "-m", "src.agente", "version"], check=True, capture_output=True,
                        timeout=60, cwd=str(nueva), text=True)
        if (dice.stdout or "").strip() != version:
            raise firma.FirmaNoValida(f"La versión instalada dice {dice.stdout!r}, no {version}.")
    except Exception:
        shutil.rmtree(nueva, ignore_errors=True)
        raise
    destino = carpeta_de(version)
    shutil.rmtree(destino, ignore_errors=True)
    nueva.rename(destino)
    return destino


def esperar_sana(version: str, plazo: float = PLAZO_SANA, dormir: Callable = time.sleep,
                 ahora: Callable = time.time, desde: float | None = None) -> bool:
    """Si en `plazo` la salud dice `READY`, con esa versión, un pulso reciente y **de un
    agente que arrancó después de `desde`** (cuando se lanzó).

    Sin lo último, la salud del agente que se acababa de parar (misma versión al
    reinstalar, pulso de hace segundos) pasaba por la del nuevo: la evaluación de la
    3.8.5 dio por buenos así dos escenarios que no lo eran."""
    from src.agente import salud

    desde = ahora() if desde is None else desde
    limite = ahora() + plazo
    while ahora() < limite:
        datos = salud.leer()
        pulso, arrancado = datos.get("pulso"), datos.get("arrancado")
        if (datos.get("estado") == "READY" and datos.get("version") == version
                and isinstance(pulso, (int, float)) and ahora() - pulso < 3 * salud.PULSO
                and isinstance(arrancado, (int, float)) and arrancado >= desde - 1):
            return True
        antes = ahora()
        dormir(1.0)
        # **Una suspensión no cuenta** (auditoría de la 3.x): si el PC duerme justo después de
        # actualizar, al despertar el plazo estaría gastado y se volvería atrás desde una
        # versión buena. Lo dormido de más se le devuelve al plazo.
        dormido = ahora() - antes - 1.0
        if dormido > SUSPENDIDO:
            limite += dormido
    return False


def _sin_ordenes(dormir: Callable, ahora: Callable, espera: float = ESPERA_ORDENES) -> bool:
    from src.agente import salud

    limite = ahora() + espera
    while ahora() < limite:
        datos = salud.leer()
        if not datos.get("cola") and not datos.get("en_marcha"):
            return True
        dormir(2.0)
    return False


def actualizar(decir: Callable[[str], None] = print, preguntar: Callable[[str], bool] | None = None,
               http=None, ejecutar: Callable = subprocess.run, dormir: Callable = time.sleep,
               ahora: Callable = time.time) -> int:
    """El camino entero. 0: actualizado (o ya estaba al día); 1: no se pudo, y sigue la de
    antes; 2: este agente no está instalado o no está emparejado.

    **Una sola a la vez** (4.1). Medido en mi PC: `actualizar` a mano mientras se
    pulsaba «Instalar» en el aviso. Las dos preparaban la misma carpeta; una falló al crear
    el entorno y dijo «sigue la 3.9.5» mientras la otra cambiaba a la 4.0.5. Si las dos
    hubieran llegado a cambiar de versión, se habrían parado y lanzado la una a la otra."""
    from src.agente import arranque

    if arranque.congelado():
        decir(PROGRAMA_DE_WINDOWS)
        return 2
    candado = arranque.Candado("actualizar.lock")
    try:
        candado.tomar()
    except arranque.YaEnMarcha:
        decir("Ya se está actualizando este agente (desde otra ventana o desde el aviso): "
              "espera a que termine. No se ha tocado nada.")
        return 1
    try:
        return _actualizar(decir, preguntar, http, ejecutar, dormir, ahora)
    finally:
        candado.soltar()


def _actualizar(decir, preguntar, http, ejecutar, dormir, ahora) -> int:
    from src.agente import arranque

    if almacen.cargar() is None or almacen.credencial() is None:
        decir("Este PC no está emparejado.")
        return 2
    if not instalada():
        decir("Este agente corre desde una copia del código de Morgan: se actualiza con git. "
              "Para instalarlo y que se actualice solo, sigue los pasos de la web (Tu equipo).")
        return 2
    try:
        manifiesto = novedad(http)
    except firma.FirmaNoValida as exc:
        decir(f"Hay una versión publicada que NO se instala: {exc}")
        return 1
    except Exception as exc:
        decir(f"No se pudo preguntar a la nube: {type(exc).__name__}.")
        return 1
    if manifiesto is None:
        decir(f"Ya tienes la última versión ({__version__}).")
        return 0
    nueva = manifiesto["version"]
    if preguntar is not None and not preguntar(f"Hay una versión nueva del agente: {nueva} (tienes {__version__}). ¿Instalarla?"):
        decir("No se instala.")
        return 0
    try:
        decir(f"Bajando e instalando la {nueva}…")
        preparar(manifiesto, bajar(manifiesto, http), ejecutar=ejecutar)
    except Exception as exc:
        decir(f"No se pudo instalar la {nueva}: {exc or type(exc).__name__}. Sigue la {__version__}.")
        return 1
    if not _sin_ordenes(dormir, ahora):
        decir(f"El agente lleva {int(ESPERA_ORDENES / 60)} minutos con órdenes: se instalará la próxima vez.")
        return 1
    arranque.pedir_parada()
    arranque.esperar_a_que_pare()
    cambiar_a(nueva)
    lanzada = ahora()
    arranque.lanzar_en_segundo_plano(python_de(nueva), carpeta_de(nueva))
    decir(f"La {nueva}, en marcha. Comprobando que está sana (hasta {int(PLAZO_SANA)} s)…")
    if esperar_sana(nueva, dormir=dormir, ahora=ahora, desde=lanzada):
        confirmar_si_pendiente(nueva)
        decir(f"Actualizado a la {nueva}.")
        return 0
    # **Su propio vigilante puede haber vuelto atrás ya** (3.8.5: si se cae en bucle, se
    # rinde antes de estos 120 s). Medido en la evaluación: aquí se paraba la anterior,
    # que ya corría sana, y como ya no había «anterior» a la que volver, el PC se quedaba
    # sin agente. Solo se para y se vuelve si la activa sigue siendo la nueva.
    anterior = None
    if leer_activa().get("version") == nueva:
        arranque.pedir_parada()
        arranque.esperar_a_que_pare()
        anterior = volver_atras(f"no llegó a sano en {int(PLAZO_SANA)} s")
    if anterior is None and leer_activa().get("version") != nueva:
        anterior = leer_activa().get("version")
    if anterior:
        if not (arranque.en_marcha() or arranque.en_marcha("vigilante.lock")):
            arranque.lanzar_en_segundo_plano(python_de(anterior), carpeta_de(anterior))
        decir(f"La {nueva} no arrancó sana: se volvió a la {anterior}.")
    else:
        decir(f"La {nueva} no arrancó sana y no hay anterior a la que volver. Mira agente.log.")
    return 1


# --- Instalar por primera vez -----------------------------------------------------------------


def instalar(decir: Callable[[str], None] = print, nube: str | None = None, codigo: str | None = None,
             paquete: Path | None = None, http=None, ejecutar: Callable = subprocess.run,
             emparejar=None, nombre: str | None = None, dormir: Callable = time.sleep,
             ahora: Callable = time.time) -> int:
    """Dos caminos, el mismo final (un agente instalado, emparejado, que arranca con Windows
    y que se comprobó que conecta):

    - **Un PC nuevo**: `scripts/instalar-agente.ps1` ya dejó esta versión en su carpeta y
      llama aquí desde ella, con el paquete que bajó (`paquete`). Aquí se comprueba **la
      firma** (el script solo pudo mirar el hash) y se empareja con `codigo`.
    - **Un agente que corre desde una copia del código** (el mío hasta la 3.8): baja la
      versión publicada, la instala en su carpeta (`preparar`) y se pasa a ella. Ya está
      emparejado: sirve su credencial.
    """
    import socket

    from src.agente import arranque

    emparejado = almacen.cargar() is not None and almacen.credencial() is not None
    nube = (nube or (almacen.cargar().nube if emparejado else "")).rstrip("/")
    cabeceras = {"X-Morgan-Codigo": codigo} if codigo else (_cabeceras() if emparejado else {})
    if not nube or not cabeceras:
        decir("Hace falta el código de la web (Ajustes → Tu equipo) y la dirección de la nube.")
        return 2
    try:
        import httpx

        cliente = http or httpx.Client(timeout=60)
        cuerpo = cliente.get(f"{nube}/agente/actualizacion", headers=cabeceras)
        cuerpo.raise_for_status()
        cuerpo = cuerpo.json()
        if not cuerpo.get("manifiesto"):
            decir("No hay ninguna versión del agente publicada.")
            return 1
        manifiesto = firma.verificar(cuerpo["manifiesto"], cuerpo.get("firma") or "")
        version = manifiesto["version"]
        if instalada():
            if version != __version__:
                raise firma.FirmaNoValida(f"Esto es la {__version__} y lo publicado, la {version}.")
            firma.comprobar_paquete(Path(paquete).read_bytes() if paquete else bajar(manifiesto, cliente, nube, cabeceras),
                                    manifiesto)
        else:
            decir(f"Instalando la {version} en {carpeta_de(version)}…")
            preparar(manifiesto, bajar(manifiesto, cliente, nube, cabeceras), ejecutar=ejecutar)
    except firma.FirmaNoValida as exc:
        decir(f"NO se instala: {exc}")
        if instalada():
            _borrar_despues([carpeta_de(__version__)])
        return 1
    except Exception as exc:
        decir(f"No se pudo: {exc or type(exc).__name__}.")
        return 1

    if codigo or not emparejado:
        # **Con un código, se empareja otra vez** aunque hubiera un emparejamiento: quien lo
        # teclea quiere este PC en esa cuenta. Medido en la 3.8.5: al reinstalar un PC
        # revocado, se reutilizaba la credencial revocada y el agente no conectaba nunca.
        # El de antes se da de baja primero (si aún valía, deja de valer).
        from src.agente.emparejar import desemparejar
        from src.agente.emparejar import emparejar as emparejar_de_verdad

        if emparejado:
            desemparejar(lambda _: None)
        if not (emparejar or emparejar_de_verdad)(nube, codigo, nombre or socket.gethostname()):
            return 1
    if arranque.en_marcha() or arranque.en_marcha("vigilante.lock"):
        decir("Parando el agente que corría…")
        arranque.pedir_parada()
        arranque.esperar_a_que_pare()
    _guardar_activa({"version": version, "anterior": None, "pendiente": None})
    arranque.activar(python_de(version), carpeta_de(version))
    try:
        arranque.crear_acceso_de_ajustes(python_de(version), carpeta_de(version))
    except Exception:
        decir("(No se pudo crear «Morgan en tu PC» en el menú Inicio; la ventana se abre igual "
              f"con \"{lanzador()}\" ajustes.)")
    lanzada = ahora()
    arranque.lanzar_en_segundo_plano(python_de(version), carpeta_de(version))
    decir("Comprobando que conecta…")
    if esperar_sana(version, dormir=dormir, ahora=ahora, desde=lanzada):
        decir(f"Listo: el agente {version} está instalado, conectado y arrancará con Windows.\n"
              "Se abre «Morgan en tu PC» para que elijas qué carpetas ve y qué puede hacer. Lo "
              "encuentras también en el menú Inicio, y la web te lo puede abrir aquí.")
        # 4.17: al instalarlo no puede hacer nada hasta que se eligen carpetas; se abre ya la
        # ventana para eso, en vez de mandar a la consola.
        from src.agente.ajustes import abrir_en_segundo_plano

        abrir_en_segundo_plano()
        return 0
    decir(f"Instalado, pero no conectó en {int(PLAZO_SANA)} s. Mira {almacen.carpeta() / 'agente.log'}.")
    return 1


# --- Desinstalar ---------------------------------------------------------------------------------


def desinstalar(decir: Callable[[str], None] = print, http=None, lanzar=None) -> int:
    """Decisión mía (3.8): se va todo **menos los respaldos** de los archivos que Morgan
    modificó, y se dice dónde quedan. Desempareja (la nube deja de aceptar su credencial),
    quita el arranque, para el agente y borra la carpeta del agente.

    Si el propio código que corre está dentro (un agente instalado), su carpeta no se puede
    borrar mientras corre: la borra un proceso aparte un momento después (`lanzar`)."""
    import shutil

    from src.agente import arranque
    from src.agente.emparejar import desemparejar

    arranque.pedir_parada()
    if not arranque.esperar_a_que_pare():
        decir("El agente no se para: no se desinstala. Mira agente.log.")
        return 1
    arranque.senal_de_parada().unlink(missing_ok=True)
    if almacen.cargar() is not None and almacen.credencial() is not None:
        desemparejar(decir, http=http)
    arranque.desactivar()
    arranque.quitar_acceso_de_ajustes()
    carpeta = almacen.carpeta()
    respaldos = carpeta / "respaldos"
    tardias = []
    for cosa in carpeta.iterdir() if carpeta.exists() else []:
        if cosa == respaldos:
            continue
        if (cosa == app() and instalada()) or cosa == lanzador():
            # El Python que corre esto está ahí dentro; y `morgan-agente.cmd` es el que lo
            # lanzó: `cmd` lee el .cmd mientras corre, y borrarlo antes de que acabe daba
            # «No se ha encontrado el archivo por lotes» (visto en la prueba de punta a punta).
            tardias.append(cosa)
            continue
        if cosa.is_dir():
            shutil.rmtree(cosa, ignore_errors=True)
        else:
            cosa.unlink(missing_ok=True)
    if tardias:
        (lanzar or _borrar_despues)(tardias)
    if respaldos.exists() and any(respaldos.iterdir()):
        decir(f"Desinstalado. Quedan los respaldos de tus archivos en {respaldos} (bórralos cuando quieras).")
    else:
        respaldos.rmdir() if respaldos.exists() else None
        decir("Desinstalado. No quedaba ningún respaldo.")
    return 0


def _borrar_despues(carpetas: list[Path]) -> None:
    """Un proceso aparte que espera a que este acabe y borra lo que no se podía borrar
    en marcha. `rmdir` de Windows, sin nada que interpretar: las rutas van entre comillas."""
    banderas = 0
    if sys.platform == "win32":
        banderas = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    ordenes = " & ".join(f'rmdir /s /q "{c}"' if c.is_dir() else f'del /f /q "{c}"' for c in carpetas)
    subprocess.Popen(f'cmd /d /c "timeout /t 3 /nobreak >nul & {ordenes}"', creationflags=banderas,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


# --- Lo que hace el agente en marcha -----------------------------------------------------------


def mirar_novedades(parado: Callable[[], bool], aviso=None, http=None, lanzar=None,
                    dormir: Callable = time.sleep, ahora: Callable = time.time) -> None:
    """Hilo del agente: al poco de conectar y cada 6 horas, si hay versión nueva firmada,
    lo pregunta con una notificación (decisión mía: nunca sin que la persona diga sí).
    «Luego» la calla 24 horas. «Instalar» lanza `actualizar --si`, desde esta versión."""
    from src.agente import salud

    if not instalada():
        return
    dormir(60)
    while not parado():
        try:
            manifiesto = novedad(http)
        except Exception:
            manifiesto = None
        if manifiesto is not None:
            salud.apuntar(novedad=manifiesto["version"])
            activa = leer_activa()
            pospuesta = activa.get("pospuesta") or {}
            # La que ya falló aquí (volvió atrás sola) no se ofrece otra vez: preguntaría en cada
            # arranque y cada 6 horas por instalar algo que no arranca (auditoría de la 3.x). A
            # mano, `actualizar` sigue pudiendo; y una versión más nueva sí se ofrece.
            fallida = (activa.get("fallida") or {}).get("version")
            if fallida != manifiesto["version"] and (
                    pospuesta.get("version") != manifiesto["version"] or ahora() > pospuesta.get("hasta", 0)):
                if aviso is None:
                    from src.agente import aviso as aviso_mod
                    aviso = aviso_mod.preguntar
                respuesta = aviso(f"Morgan: versión {manifiesto['version']} del agente",
                                  f"Tienes la {__version__}. Al instalarla el agente se reinicia un momento; "
                                  "si la nueva no arranca bien, vuelve sola a la actual.",
                                  espera=600, botones=("Luego", "Instalar"))
                if respuesta == "permitida":
                    # Sin `return`: si la instalación falla antes de cambiar (sin red, una
                    # dependencia), este agente sigue y tiene que seguir mirando. Si cambia,
                    # este proceso se para igual.
                    (lanzar or _lanzar_actualizar)()
                    final = ahora() + MIRAR_CADA
                    while not parado() and ahora() < final:
                        dormir(30)
                    continue
                datos = leer_activa()
                datos["pospuesta"] = {"version": manifiesto["version"], "hasta": ahora() + POSPONER}
                _guardar_activa(datos)
        final = ahora() + MIRAR_CADA
        while not parado() and ahora() < final:
            dormir(30)


def _lanzar_actualizar() -> None:
    """`actualizar --si` en un proceso aparte, con el Python de esta versión: el que va a
    juzgar a la nueva es el código que ya se sabe sano."""
    from src.agente import arranque

    banderas = 0
    if sys.platform == "win32":
        banderas = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with arranque.abrir_registro() as registro:
        subprocess.Popen(arranque.orden("actualizar", "--si"),
                         cwd=str(arranque.raiz_del_proyecto()), creationflags=banderas, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=registro, stderr=registro)
