"""
El agente siempre en marcha (3.0): arranque automático, uno solo a la vez, y parar.

Lo pedí al cerrar la 3.0: **con el PC encendido y con internet, Morgan tiene
acceso**, sin abrir el agente a mano. El plan lo ponía en la 3.8 (instalación); se
adelanta porque es poco y no cambia ninguna frontera: el PC sigue decidiendo con su
política qué se lee.

Tres piezas, y por qué hace falta cada una:

- **Un acceso directo en la carpeta de Inicio** de Windows, con `pythonw.exe` (sin
  ventana). No pide permisos de administrador, a diferencia de una tarea programada.
- **Un solo agente a la vez**, con un fichero bloqueado. Sin esto, el automático y uno
  abierto a mano serían el mismo `agent_id` y se sustituirían uno al otro sin parar
  (la nube cierra la conexión vieja con 4410 y la vieja reconecta al momento).
- **Un registro en fichero y una forma de pararlo**: sin ventana no hay dónde leer ni
  dónde pulsar Ctrl+C. `parar` deja una señal que el agente mira cada poco.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from src.agente import estado as almacen

NOMBRE_ACCESO = "Morgan agente local.lnk"
#: El acceso del menú Inicio a la ventana de ajustes (4.17).
NOMBRE_AJUSTES = "Morgan en tu PC.lnk"

#: El registro de un agente sin ventana: se empieza de cero si pasa de esto.
TAMANO_MAXIMO_REGISTRO = 1_000_000


def raiz_del_proyecto() -> Path:
    if congelado():
        return Path(sys.executable).parent       # la carpeta del programa (5.0)
    return Path(__file__).resolve().parents[2]


def carpeta_de_inicio() -> Path:
    """La carpeta de Inicio del usuario (o `MORGAN_CARPETA_INICIO`, para pruebas)."""
    propia = os.environ.get("MORGAN_CARPETA_INICIO")
    if propia:
        return Path(propia)
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def acceso_directo() -> Path:
    return carpeta_de_inicio() / NOMBRE_ACCESO


def carpeta_del_menu() -> Path:
    """Los programas del menú Inicio del usuario (o `MORGAN_CARPETA_MENU`, para pruebas)."""
    propia = os.environ.get("MORGAN_CARPETA_MENU")
    if propia:
        return Path(propia)
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def acceso_de_ajustes() -> Path:
    return carpeta_del_menu() / NOMBRE_AJUSTES


def pythonw(python: Path | None = None) -> Path:
    """El intérprete sin ventana del mismo entorno que ejecuta esto (o del de `python`:
    una versión instalada del agente, 3.8)."""
    base = Path(python) if python is not None else Path(sys.executable)
    candidato = base.with_name("pythonw.exe")
    return candidato if candidato.exists() else base


#: El agente congelado (5.0, decisión W2) son dos ejecutables sobre los mismos ficheros: el de
#: las órdenes, con consola, y este, sin ventana, para lo que corre de fondo (vigilar,
#: conectar, la ventana de ajustes). Uno con consola abriría una ventana negra al arrancar.
FONDO = "morgan-agente-fondo.exe"


def congelado() -> bool:
    """Si esto es el agente congelado con PyInstaller (el programa de Windows) y no Python."""
    return bool(getattr(sys, "frozen", False))


def orden(*argumentos: str, python: Path | None = None, sin_consola: bool = True) -> list[str]:
    """Cómo arrancar el agente con esos argumentos: con Python (`pythonw -m src.agente …`) o,
    congelado, su propio ejecutable. Un solo sitio para el arranque, el vigilante, los
    ajustes y la actualización."""
    if congelado() and python is None:
        exe = Path(sys.executable)
        fondo = exe.with_name(FONDO)
        return [str(fondo if sin_consola and fondo.exists() else exe), *argumentos]
    interprete = pythonw(python) if sin_consola else (Path(python) if python else Path(sys.executable))
    return [str(interprete), "-m", "src.agente", *argumentos]


# --- Uno solo a la vez ---------------------------------------------------------------


class YaEnMarcha(RuntimeError):
    pass


class Candado:
    """Un fichero bloqueado mientras el agente corre. Si otro lo tiene, `YaEnMarcha`.
    `nombre`: el del agente o el del vigilante (3.6), que también es uno solo."""

    def __init__(self, nombre: str = "agente.lock") -> None:
        self._fichero = None
        self.nombre = nombre

    def tomar(self) -> "Candado":
        almacen.carpeta().mkdir(parents=True, exist_ok=True)
        fichero = open(almacen.carpeta() / self.nombre, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                fichero.seek(0)
                msvcrt.locking(fichero.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fichero.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fichero.close()
            raise YaEnMarcha("Ya hay un agente en marcha en este PC.") from None
        self._fichero = fichero
        return self

    def soltar(self) -> None:
        if self._fichero is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    self._fichero.seek(0)
                    msvcrt.locking(self._fichero.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._fichero.close()
            self._fichero = None


def en_marcha(nombre: str = "agente.lock") -> bool:
    """Si hay un agente (o su vigilante) corriendo ahora en este PC."""
    candado = Candado(nombre)
    try:
        candado.tomar()
    except YaEnMarcha:
        return True
    candado.soltar()
    return False


# --- Parar ----------------------------------------------------------------------------


def senal_de_parada() -> Path:
    return almacen.carpeta() / "parar"


def pedir_parada() -> None:
    almacen.carpeta().mkdir(parents=True, exist_ok=True)
    senal_de_parada().write_text(str(time.time()), encoding="utf-8")


#: Lo que `parar` espera a que el agente y su vigilante se vayan.
ESPERA_PARADA = 30.0


def esperar_a_que_pare(hasta: float | None = None) -> bool:
    """True cuando ya no corren ni el agente ni su vigilante (sus candados, libres).

    Entonces **borra la señal de parada**, si nadie la recogió: si no, el próximo vigilante
    que se lance la vería y se iría nada más nacer. Medido en la prueba de punta a punta de
    la 3.8: una versión rota ya se había parado sola, `actualizar` pidió parar igualmente, y
    la versión anterior, relanzada para volver atrás, no llegó a arrancar."""
    limite = time.monotonic() + (ESPERA_PARADA if hasta is None else hasta)
    while en_marcha() or en_marcha("vigilante.lock"):
        if time.monotonic() > limite:
            return False
        time.sleep(0.2)
    senal_de_parada().unlink(missing_ok=True)
    return True


def vigilar_parada(parar, cada: float = 2.0, hasta=None) -> None:
    """Hilo: si aparece la señal, la borra y para el agente. `hasta` corta la vigilancia."""
    while hasta is None or not hasta():
        if senal_de_parada().exists():
            try:
                senal_de_parada().unlink()
            except FileNotFoundError:
                pass
            parar()
            return
        time.sleep(cada)


# --- El registro sin ventana -------------------------------------------------------------


def abrir_registro():
    """El fichero al que escribe un agente sin consola. Se vacía si ha crecido de más."""
    almacen.carpeta().mkdir(parents=True, exist_ok=True)
    ruta = almacen.carpeta() / "agente.log"
    if ruta.exists() and ruta.stat().st_size > TAMANO_MAXIMO_REGISTRO:
        ruta.unlink()
    return open(ruta, "a", encoding="utf-8", buffering=1)


# --- Arranque automático ------------------------------------------------------------------


def activar(python: Path | None = None, carpeta: Path | None = None) -> Path:
    """Crea el acceso directo en la carpeta de Inicio. Devuelve su ruta. Con `python` y
    `carpeta`, a esa versión instalada del agente (3.8); si no, a esta."""
    # El vigilante, no el agente (3.6): lo lanza él y lo levanta si se cae.
    return _crear_acceso(acceso_directo(), python, carpeta, ["vigilar"],
                         "Morgan: agente local (solo lee lo que permitas)", estilo=7)


def crear_acceso_de_ajustes(python: Path | None = None, carpeta: Path | None = None) -> Path:
    """«Morgan en tu PC» en el menú Inicio (4.17): la ventana de ajustes, para quien no usa
    la consola. Apunta a la versión activa, como el arranque: se rehace al actualizar."""
    return _crear_acceso(acceso_de_ajustes(), python, carpeta, ["ajustes"],
                         "Morgan: qué carpetas ve y qué puede hacer en este PC", estilo=1)


def quitar_acceso_de_ajustes() -> bool:
    try:
        acceso_de_ajustes().unlink()
        return True
    except FileNotFoundError:
        return False


def _crear_acceso(destino: Path, python: Path | None, carpeta: Path | None, argumentos: list[str],
                  descripcion: str, estilo: int) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    programa, *resto = orden(*argumentos, python=python)
    # Un .lnk se crea con el objeto COM de Windows; sin dependencias nuevas, a través
    # de PowerShell. Rutas entre comillas simples de PowerShell (con '' escapado).
    def ps(texto: str) -> str:
        return "'" + str(texto).replace("'", "''") + "'"

    guion = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + ps(destino) + ");"
        "$s.TargetPath = " + ps(programa) + ";"
        "$s.Arguments = " + ps(subprocess.list2cmdline(resto)) + ";"
        "$s.WorkingDirectory = " + ps(carpeta or raiz_del_proyecto()) + ";"
        "$s.Description = " + ps(descripcion) + ";"
        f"$s.WindowStyle = {int(estilo)};"
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", guion],
                   check=True, capture_output=True)
    return destino


def desactivar() -> bool:
    try:
        acceso_directo().unlink()
        return True
    except FileNotFoundError:
        return False


def activado() -> bool:
    return acceso_directo().exists()


def lanzar_en_segundo_plano(python: Path | None = None, carpeta: Path | None = None) -> None:
    """Arranca ya un agente sin ventana, como lo haría Windows al iniciar sesión (con
    `python` y `carpeta`, el de esa versión instalada, 3.8)."""
    banderas = 0
    if sys.platform == "win32":
        banderas = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # La salida va al registro desde aquí: con stdout a DEVNULL, pythonw ya no ve
    # stdout como None y el agente no sabría que tiene que escribir en el fichero
    # (pasó en la primera activación en mi PC: corría, sin registro).
    with abrir_registro() as registro:
        subprocess.Popen(
            orden("vigilar", python=python),
            cwd=str(carpeta or raiz_del_proyecto()), creationflags=banderas, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=registro, stderr=registro,
        )
