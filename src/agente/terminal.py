"""
La terminal y los procesos del PC (3.5): un catálogo cerrado de programas, **sin shell**.

Diseño y mis decisiones en docs/plan-3.0.md §V3.5 (2026-09-25).

## Por qué no PowerShell

La terminal del Morgan local (`execute_command`) ejecuta PowerShell libre y se protege
con una lista de patrones prohibidos. Una lista de lo prohibido se salta con un alias,
un comando en base64 o cualquier cosa que no se pensó: desde la nube sería justo lo que
el gate de la 3.5 prohíbe (`Cloud → ejecución arbitraria de comandos`). Aquí:

- **Un programa y una lista de argumentos**, lanzados sin shell (`subprocess` con una
  lista, nunca una cadena). No hay `;`, `|`, `&&`, redirecciones ni variables: no hay
  nada que inyectar.
- **Solo ejecutables `.exe`**, buscados en el PATH del sistema y **nunca en la carpeta de
  trabajo** (Windows busca primero ahí, y un `git.exe` puesto en ella suplantaría al de
  verdad). Un `.cmd` o un `.bat` pasa por `cmd.exe`, que sí interpreta los argumentos
  (el fallo conocido como «BatBadBut»): por eso `npm`, que en Windows es `npm.cmd`, no
  está.
- **Cada programa, con sus reglas**: qué subcomandos, qué opciones y qué valores. Lo que
  no está en la regla, no pasa. Y cada uso dice si **consulta** o **cambia** cosas.
- **Nunca** los que por sí mismos ejecutan lo que se les diga (`NUNCA`).

## Git ejecuta cosas por su cuenta

Su configuración puede lanzar programas (`core.fsmonitor` en cada `git status`, un
`diff.external`) y sus *hooks* corren en `commit` y `pull`. Un repositorio descargado en
un zip trae su `.git/config` y sus *hooks*. Cuando lo lanza Morgan, git va con esas
cosas **apagadas** (`PREFIJO_GIT`); y desde la 3.5 Morgan **no escribe** dentro de `.git`
ni en `.gitconfig` (`politica.py`).

## Procesos

Ver la lista e información de uno (consulta) y **terminar** (cambia), solo procesos **de
la persona**: nunca los del sistema, ni el propio agente, ni Morgan (decisión mía).
"""

import getpass
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.agente import aviso, control
from src.agente import estado as almacen
from src.agente.politica import Denegado, Politica

#: Lo más que vuelve de la salida de un comando (lo que pase, se corta).
MAX_SALIDA = 64 * 1024
#: Plazos de un comando que consulta y de uno que cambia. La orden puede traer menos.
PLAZO_CONSULTA = 30.0
PLAZO_CAMBIO = 120.0

#: Programas que no se ofrecen nunca: por sí mismos son «ejecuta lo que te diga».
NUNCA = frozenset({
    "powershell", "pwsh", "cmd", "wscript", "cscript", "mshta", "rundll32", "regsvr32",
    "reg", "regedit", "schtasks", "at", "sc", "bcdedit", "diskpart", "format", "wmic",
    "bash", "sh", "wsl", "msiexec", "certutil", "bitsadmin", "forfiles", "runas",
    "installutil", "msbuild", "cmstp", "odbcconf", "pcalua", "explorer", "start",
})

#: Variables del entorno que no se le pasan al programa: pueden llevar secretos. Se mira
#: **cada palabra** del nombre (`GROQ_API_KEY` → GROQ, API, KEY), no el texto: buscar
#: «AUTH» dentro del nombre quitaba también `GIT_AUTHOR_NAME` (encontrado al probarlo).
SECRETO_EN_EL_NOMBRE = frozenset({"KEY", "KEYS", "SECRET", "SECRETS", "PASSWORD", "PASSWD",
                                  "PASS", "TOKEN", "TOKENS", "CREDENTIAL", "CREDENTIALS",
                                  "AUTH", "APIKEY", "PRIVATE", "SESSION", "COOKIE", "PAT"})

# Lo que puede ser cada valor. Ninguno puede empezar por «-»: sería otra opción.
_RAMA = r"[A-Za-z0-9._][A-Za-z0-9._/\-]{0,99}"
_REVISION = rf"(?:{_RAMA}|HEAD(?:~\d{{1,3}})?|[0-9a-fA-F]{{4,40}})"
_RUTA_RELATIVA = r"[^\-\x00-\x1f:*?\"<>|][^\x00-\x1f:*?\"<>|]{0,199}"
_HOST = r"[A-Za-z0-9][A-Za-z0-9.\-:]{0,252}"
_NUMERO = r"\d{1,4}"
_FECHA = r"[0-9A-Za-z .:\-]{1,40}"
_TEXTO = r"[^\x00-\x1f]{1,500}"
_PAQUETE = r"[A-Za-z0-9][A-Za-z0-9._\-]{0,99}"
# La 4.9 (mi lista: npm, pip, scripts de tus carpetas, winget).
#: Un paquete de pip con su versión (`requests==2.32.3`, `black>=24`, `uvicorn[standard]`).
_PAQUETE_PIP = r"[A-Za-z0-9][A-Za-z0-9._\-]{0,99}(?:\[[A-Za-z0-9,._\-]{1,60}\])?(?:(?:==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!\-]{1,40})?"
#: Un paquete de npm (`react`, `@types/node`, `vite@5.4.2`).
_PAQUETE_NPM = r"(?:@[a-z0-9][a-z0-9._\-]{0,60}/)?[a-z0-9][a-z0-9._\-]{0,100}(?:@[A-Za-z0-9.^~<>=*|\- ]{1,40})?"
#: Un script de `package.json` (`build`, `test:unit`).
_SCRIPT_NPM = r"[A-Za-z0-9][A-Za-z0-9:_\-.]{0,60}"
#: Un script propio, relativo a la carpeta de trabajo, con su extensión.
_SCRIPT_PY = r"[^\-\x00-\x1f:*?\"<>|][^\x00-\x1f:*?\"<>|]{0,199}\.pyw?"
_SCRIPT_JS = r"[^\-\x00-\x1f:*?\"<>|][^\x00-\x1f:*?\"<>|]{0,199}\.(?:js|mjs|cjs)"
#: Un identificador de winget (`Microsoft.VisualStudioCode`) y lo que se busca.
_ID_WINGET = r"[A-Za-z0-9][A-Za-z0-9.\-_+]{0,99}"
_BUSQUEDA = r"[^\-\x00-\x1f][^\x00-\x1f]{0,99}"


@dataclass(frozen=True)
class Uso:
    """Una forma permitida de llamar a un programa."""

    cambia: bool
    #: Opciones sin valor.
    opciones: frozenset = frozenset()
    #: Opciones con valor (`-n 5` o `--max-count=5`) → cómo tiene que ser el valor.
    con_valor: dict = field(default_factory=dict)
    #: Cómo tiene que ser cada argumento suelto, y cuántos como mucho.
    posicional: str | None = None
    max_posicionales: int = 0
    #: Los sueltos van detrás de `--` (rutas: que ninguna se tome por una opción).
    tras_separador: bool = False
    #: Argumentos que se ponen siempre, detrás del subcomando.
    fijos: tuple = ()
    #: Opciones que tienen que estar (`commit` sin `-m` abriría un editor).
    obligatorias: tuple = ()
    #: Si necesita una carpeta de trabajo (un repositorio).
    carpeta: bool = False
    #: Límites de un valor numérico, por opción.
    rango: dict = field(default_factory=dict)
    #: Por defecto, si no se dan (`ping` hace 4 intentos, no infinitos).
    por_defecto: tuple = ()


@dataclass(frozen=True)
class Programa:
    nombre: str
    ejecutable: str
    #: Subcomando (tupla de palabras) → su uso. `()`: el programa sin subcomando.
    usos: dict
    #: Argumentos que van siempre, delante del subcomando.
    prefijo: tuple = ()
    #: Si «/opción» es una opción (los programas de Windows) y no una ruta.
    barra_es_opcion: bool = False


def _git(*, cambia=False, **k) -> Uso:
    return Uso(cambia=cambia, carpeta=True, **k)


_SIN_DIFF_EXTERNO = ("--no-ext-diff", "--no-textconv")
_PIP_SEGURO = ("--only-binary", ":all:", "--disable-pip-version-check", "--no-input")
_NPM_SEGURO = ("--ignore-scripts", "--no-audit", "--no-fund")
_WINGET_CONSULTA = ("--source", "winget", "--disable-interactivity", "--accept-source-agreements")
_WINGET_CAMBIO = ("--exact", "--source", "winget", "--disable-interactivity",
                  "--accept-package-agreements", "--accept-source-agreements")

CATALOGO: dict[str, Programa] = {p.nombre: p for p in (
    Programa("git", "git.exe", {
        ("status",): _git(opciones=frozenset({"-s", "--short", "-b", "--branch", "--porcelain"})),
        ("log",): _git(opciones=frozenset({"--oneline", "--stat", "--graph", "--decorate", "--all", "--no-merges"}),
                       con_valor={"-n": _NUMERO, "--max-count": _NUMERO, "--since": _FECHA,
                                  "--until": _FECHA, "--author": _TEXTO},
                       posicional=_REVISION, max_posicionales=1, fijos=_SIN_DIFF_EXTERNO,
                       por_defecto=("-n", "20")),
        ("diff",): _git(opciones=frozenset({"--stat", "--name-only", "--cached", "--staged"}),
                        posicional=_REVISION, max_posicionales=2, fijos=_SIN_DIFF_EXTERNO),
        ("show",): _git(opciones=frozenset({"--stat", "--name-only"}),
                        posicional=_REVISION, max_posicionales=1, fijos=_SIN_DIFF_EXTERNO),
        ("branch",): _git(opciones=frozenset({"-a", "--all", "-r", "-v"})),
        ("remote",): _git(opciones=frozenset({"-v"})),
        ("fetch",): _git(cambia=True, opciones=frozenset({"--all", "--prune"}),
                         posicional=_RAMA, max_posicionales=1),
        ("pull",): _git(cambia=True, posicional=_RAMA, max_posicionales=2, fijos=("--ff-only",)),
        ("add",): _git(cambia=True, posicional=_RUTA_RELATIVA, max_posicionales=20, tras_separador=True),
        ("commit",): _git(cambia=True, opciones=frozenset({"-a"}), con_valor={"-m": _TEXTO},
                          obligatorias=("-m",)),
        ("switch",): _git(cambia=True, posicional=_RAMA, max_posicionales=1),
    }, prefijo=("--no-pager",)),
    Programa("ping", "PING.EXE", {
        (): Uso(cambia=False, con_valor={"-n": _NUMERO}, rango={"-n": (1, 10)},
                posicional=_HOST, max_posicionales=1, por_defecto=("-n", "4")),
    }),
    Programa("ipconfig", "ipconfig.exe", {
        (): Uso(cambia=False, opciones=frozenset({"/all"})),
    }, barra_es_opcion=True),
    Programa("nslookup", "nslookup.exe", {
        # Sin argumento, nslookup es interactivo: el anfitrión es obligatorio.
        (): Uso(cambia=False, posicional=_HOST, max_posicionales=1),
    }),
    Programa("python", "python.exe", {
        ("--version",): Uso(cambia=False),
        ("-m", "pip", "list"): Uso(cambia=False, opciones=frozenset({"--outdated"})),
        ("-m", "pip", "show"): Uso(cambia=False, posicional=_PAQUETE, max_posicionales=1),
        # 4.9. Instalar, **solo ruedas** (`--only-binary :all:`): un paquete en código fuente
        # ejecuta su `setup.py` al instalarse, y eso sería ejecutar lo que traiga.
        ("-m", "pip", "install"): Uso(cambia=True, opciones=frozenset({"--user", "--upgrade", "-U"}),
                                      con_valor={"-r": _RUTA_RELATIVA}, posicional=_PAQUETE_PIP,
                                      max_posicionales=10, fijos=_PIP_SEGURO),
        ("-m", "pip", "uninstall"): Uso(cambia=True, posicional=_PAQUETE, max_posicionales=10,
                                        fijos=("-y", "--no-input")),
        # Un script **propio** de la carpeta de trabajo (4.9, «scripts de tus carpetas»):
        # Morgan no puede crear ni cambiar un `.py` (`EJECUTABLES`), así que es de la persona.
        # Sin opciones: `-c` y `-m` no pasan.
        (): Uso(cambia=True, carpeta=True, posicional=_SCRIPT_PY, max_posicionales=1),
    }),
    Programa("node", "node.exe", {
        ("--version",): Uso(cambia=False),
        (): Uso(cambia=True, carpeta=True, posicional=_SCRIPT_JS, max_posicionales=1),
    }),
    # npm, **sin cmd.exe** (4.9): en Windows `npm` es `npm.cmd`, que pasa por `cmd.exe` y le
    # deja interpretar los argumentos («BatBadBut»). Se lanza `node.exe npm-cli.js`, el mismo
    # npm, con los argumentos en una lista. Instalar, siempre con `--ignore-scripts`: los
    # paquetes no ejecutan nada al instalarse.
    Programa("npm", "node.exe", {
        ("--version",): Uso(cambia=False),
        ("ls",): Uso(cambia=False, carpeta=True, con_valor={"--depth": _NUMERO}, rango={"--depth": (0, 5)},
                     por_defecto=("--depth", "0")),
        ("outdated",): Uso(cambia=False, carpeta=True),
        ("install",): Uso(cambia=True, carpeta=True, opciones=frozenset({"--save-dev", "-D", "--save-exact", "-E"}),
                          posicional=_PAQUETE_NPM, max_posicionales=10, fijos=_NPM_SEGURO),
        ("ci",): Uso(cambia=True, carpeta=True, fijos=_NPM_SEGURO),
        ("uninstall",): Uso(cambia=True, carpeta=True, posicional=_PAQUETE_NPM, max_posicionales=10,
                            fijos=_NPM_SEGURO),
        # Los scripts de su `package.json`: la persona ve en «Permitir» qué ejecutan.
        ("run",): Uso(cambia=True, carpeta=True, posicional=_SCRIPT_NPM, max_posicionales=1),
        ("test",): Uso(cambia=True, carpeta=True),
    }),
    # winget (4.9): buscar y ver, y lo que cambia **por identificador exacto**, del origen
    # `winget` y sin preguntas en la consola (si hace falta administrador, Windows lo pide).
    Programa("winget", "winget.exe", {
        ("search",): Uso(cambia=False, posicional=_BUSQUEDA, max_posicionales=1, fijos=_WINGET_CONSULTA),
        ("list",): Uso(cambia=False, opciones=frozenset({"--upgrade-available"}),
                       con_valor={"--name": _BUSQUEDA, "--id": _ID_WINGET}, fijos=_WINGET_CONSULTA),
        ("show",): Uso(cambia=False, con_valor={"--id": _ID_WINGET}, obligatorias=("--id",),
                       fijos=(*_WINGET_CONSULTA, "--exact")),
        ("install",): Uso(cambia=True, con_valor={"--id": _ID_WINGET}, obligatorias=("--id",),
                          fijos=_WINGET_CAMBIO),
        ("upgrade",): Uso(cambia=True, con_valor={"--id": _ID_WINGET}, obligatorias=("--id",),
                          fijos=_WINGET_CAMBIO),
        ("uninstall",): Uso(cambia=True, con_valor={"--id": _ID_WINGET}, obligatorias=("--id",),
                            fijos=("--exact", "--disable-interactivity", "--accept-source-agreements")),
    }),
)}
assert not set(CATALOGO) & NUNCA


def _hooks_vacios() -> str:
    """Una carpeta vacía del agente, para que git no encuentre *hooks* que correr."""
    carpeta = almacen.carpeta() / "sin_hooks"
    carpeta.mkdir(parents=True, exist_ok=True)
    return str(carpeta)


def prefijo_git() -> tuple:
    return ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={_hooks_vacios()}",
            "-c", "diff.external=", "-c", "core.pager=")


# --- Validar lo que se pide ---

def validar(programa: str, argumentos) -> tuple[Programa, Uso, list[str]]:
    """El programa, su uso y los argumentos **finales**, o `Denegado` con el porqué."""
    if not isinstance(programa, str) or programa.lower() in NUNCA:
        raise Denegado("programa_prohibido", "Ese programa no se ejecuta nunca desde Morgan.")
    prog = CATALOGO.get(programa.lower())
    if prog is None:
        raise Denegado("fuera_del_catalogo",
                       f"«{programa}» no está en el catálogo. Los hay: {', '.join(sorted(CATALOGO))}.")
    if argumentos is None:
        argumentos = []
    if (not isinstance(argumentos, list) or len(argumentos) > 40
            or not all(isinstance(a, str) and 0 < len(a) <= 500 for a in argumentos)):
        raise Denegado("argumentos", "Los argumentos son una lista de textos cortos.")
    # Redundante a propósito (las reglas de cada valor ya los rechazan; una mutación que
    # la quite no rompe nada hoy): si mañana una regla se escribe con `.`, esto sigue ahí.
    if any(re.search(r"[\x00-\x1f\x7f]", a) for a in argumentos):
        raise Denegado("argumentos", "Un argumento lleva caracteres de control.")

    clave = max((k for k in prog.usos if tuple(argumentos[:len(k)]) == k), key=len, default=None)
    if clave is None:
        raise Denegado("uso_no_permitido",
                       f"Eso no se permite con {prog.nombre}. Se puede: "
                       + "; ".join(" ".join((prog.nombre, *k)) for k in prog.usos) + ".")
    uso = prog.usos[clave]
    resto = argumentos[len(clave):]
    # Lo que el catálogo ya pone por su cuenta, si el modelo lo repite, se quita en vez de
    # rechazar la orden (4.9, medido: planeó `winget install -e --id …`, y `-e` es `--exact`).
    fijas = {a for a in uso.fijos if a.startswith("-")}
    if prog.nombre == "winget" and "--exact" in fijas:
        fijas.add("-e")
    resto = [a for a in resto if a not in fijas]

    opciones, sueltos, dados, i = [], [], set(), 0
    while i < len(resto):
        a = resto[i]
        es_opcion = a.startswith("-") or (prog.barra_es_opcion and a.startswith("/"))
        if a == "--" and uso.tras_separador:
            sueltos.extend(resto[i + 1:])
            break
        if es_opcion:
            nombre, igual, valor = a.partition("=")
            if nombre in uso.con_valor:
                if not igual:
                    if i + 1 >= len(resto):
                        raise Denegado("argumentos", f"A «{nombre}» le falta su valor.")
                    valor, i = resto[i + 1], i + 1
                if not re.fullmatch(uso.con_valor[nombre], valor):
                    raise Denegado("argumentos", f"El valor de «{nombre}» no vale.")
                if nombre in uso.rango:
                    minimo, maximo = uso.rango[nombre]
                    if not minimo <= int(valor) <= maximo:
                        raise Denegado("argumentos", f"«{nombre}» va de {minimo} a {maximo}.")
                opciones += [nombre, valor]
                dados.add(nombre)
            elif a in uso.opciones:
                opciones.append(a)
                dados.add(a)
            elif prog.nombre in ("python", "node") and nombre in ("-c", "-e", "-m", "--eval", "-p", "--print"):
                # Medido con el modelo real (4.9): rechazado `python -c`, propuso crear un
                # script temporal para ejecutarlo. Se le dice que no hay otro camino.
                raise Denegado("opcion_no_permitida",
                               "Morgan no ejecuta código suelto en el PC. No busques otra forma: "
                               "ni crear un script (no puede) ni otro programa. Díselo a la persona.")
            else:
                raise Denegado("opcion_no_permitida", f"La opción «{a}» no se permite aquí.")
        else:
            sueltos.append(a)
        i += 1

    if len(sueltos) > uso.max_posicionales:
        raise Denegado("argumentos", "Demasiados argumentos para eso.")
    for s in sueltos:
        # `posicional is None` es redundante con el tope de arriba (sin regla, el tope es 0),
        # a propósito: un uso sin regla para los sueltos nunca acepta uno.
        if uso.posicional is None or not re.fullmatch(uso.posicional, s) or ".." in Path(s).parts:
            raise Denegado("argumentos", f"«{s}» no vale como argumento aquí.")
    faltan = [o for o in uso.obligatorias if o not in dados]
    if faltan:
        raise Denegado("argumentos", f"Falta {', '.join(faltan)}.")
    if prog.nombre == "nslookup" and not sueltos:
        raise Denegado("argumentos", "Falta el nombre a consultar.")
    for j in range(0, len(uso.por_defecto), 2):
        if uso.por_defecto[j] not in dados:
            opciones += list(uso.por_defecto[j:j + 2])

    finales = [*clave, *uso.fijos, *opciones, *(["--"] if uso.tras_separador and sueltos else []), *sueltos]
    return prog, uso, finales


def localizar(ejecutable: str, prohibidas: list[str] = ()) -> Path | None:
    """El ejecutable, **solo** en las carpetas absolutas del PATH (nunca «.» ni la de
    trabajo), y nunca dentro de una carpeta donde Morgan puede escribir."""
    claves = [os.path.normcase(str(Path(p).resolve())) for p in prohibidas]
    for carpeta in os.environ.get("PATH", "").split(os.pathsep):
        carpeta = carpeta.strip().strip('"')
        if not carpeta or not os.path.isabs(carpeta):
            continue
        candidato = Path(carpeta) / ejecutable
        if candidato.is_file():
            clave = os.path.normcase(str(candidato.resolve()))
            if any(clave.startswith(c.rstrip("\\/") + os.sep) for c in claves):
                continue
            return candidato
    return None


def entorno_limpio() -> dict:
    """El entorno de la persona, sin lo que puede llevar secretos, y sin preguntar nada."""
    limpio = {k: v for k, v in os.environ.items()
              if not set(re.split(r"[^A-Z0-9]+", k.upper())) & SECRETO_EN_EL_NOMBRE}
    limpio.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="", PYTHONIOENCODING="utf-8")
    return limpio


# --- Ejecutar ---

def _matar_arbol(pid: int) -> None:
    import psutil

    try:
        raiz = psutil.Process(pid)
        procesos = [*raiz.children(recursive=True), raiz]
    except psutil.Error:
        return
    for p in procesos:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, vivos = psutil.wait_procs(procesos, timeout=3)
    for p in vivos:
        try:
            p.kill()
        except psutil.Error:
            pass


def _correr(argv: list[str], carpeta: str, plazo: float, cancelable: bool) -> dict:
    """Lanza sin shell, recoge la salida con tope, y para si vence o si se cancela."""
    CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP = 0x08000000, 0x00000200
    inicio = time.monotonic()
    proceso = subprocess.Popen(
        argv, cwd=carpeta, env=entorno_limpio(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )
    trozos, total = [], {"bytes": 0}

    def leer():
        while True:
            trozo = proceso.stdout.read(8192)
            if not trozo:
                break
            if total["bytes"] < MAX_SALIDA:
                trozos.append(trozo[:MAX_SALIDA - total["bytes"]])
            total["bytes"] += len(trozo)

    lector = threading.Thread(target=leer, daemon=True)
    lector.start()
    motivo = None
    while proceso.poll() is None:
        if cancelable and control.pedida():
            motivo = "cancelado"
        elif time.monotonic() - inicio > plazo:
            motivo = "plazo"
        if motivo:
            _matar_arbol(proceso.pid)
            break
        time.sleep(0.05)
    proceso.wait(timeout=10)
    lector.join(timeout=5)
    if motivo == "cancelado":
        control.punto_seguro()          # lanza Cancelada: el ejecutor lo cuenta como tal
    salida = b"".join(trozos).decode("utf-8", errors="replace")
    return {"codigo": proceso.returncode, "salida": salida, "cortada": total["bytes"] > MAX_SALIDA,
            "vencido": motivo == "plazo", "segundos": round(time.monotonic() - inicio, 2)}


def _plazo(maximo: float) -> float:
    vence = aviso.VENCE.get()
    return maximo if vence is None else max(1.0, min(maximo, vence - time.monotonic() - 3))


def _programas_encendidos(politica: Politica) -> list[str]:
    return [p for p in politica.programas if p in CATALOGO]


def _ejecutar(capacidad: str, programa: str, argumentos, cwd: str | None, cambia: bool) -> dict:
    from src.agente import motor

    try:
        prog, uso, finales = validar(programa, argumentos)
    except Denegado as exc:
        return {"success": False, "data": None, "error": str(exc), "motivo": exc.motivo}
    politica = Politica.cargar()
    if prog.nombre not in _programas_encendidos(politica):
        encendidos = _programas_encendidos(politica)
        return {"success": False, "data": None, "motivo": "programa_apagado",
                "error": f"«{prog.nombre}» no está encendido en este PC. Encendidos: "
                         f"{', '.join(encendidos) or 'ninguno'}. Se enciende allí: "
                         f"python -m src.agente programa activar {prog.nombre}"}
    if uso.cambia != cambia:
        otra = "run_change_command" if uso.cambia else "run_command"
        return {"success": False, "data": None, "motivo": "otra_herramienta",
                "error": f"Eso {'cambia' if uso.cambia else 'solo consulta'}: va por {otra}."}
    if uso.carpeta and not cwd:
        return {"success": False, "data": None, "motivo": "falta_carpeta",
                "error": "Eso necesita la carpeta del repositorio (cwd)."}

    exe = localizar(prog.ejecutable, politica.escritura)
    if exe is None:
        return {"success": False, "data": None, "motivo": "no_instalado",
                "error": f"{prog.nombre} no está instalado en este PC (o no está en el PATH)."}
    delante: tuple = prog.prefijo + (prefijo_git() if prog.nombre == "git" else ())
    if prog.nombre == "npm":
        # El npm que trae Node, junto a su `node.exe`, sin pasar por `npm.cmd` (4.9).
        cli = exe.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if not cli.is_file():
            return {"success": False, "data": None, "motivo": "no_instalado",
                    "error": "npm no está junto a node en este PC."}
        delante = (str(cli),)
    argv = [str(exe), *delante, *finales]
    linea = " ".join([prog.nombre, *finales])
    extra = ""
    if cambia and prog.nombre in ("python", "node") and finales and not finales[0].startswith("-"):
        # Un script propio: tiene que estar **dentro** de la carpeta de trabajo (4.9).
        try:
            base = Path(cwd).resolve(strict=True)
            script = (base / finales[0]).resolve(strict=True)
            script.relative_to(base)
        except (OSError, ValueError, TypeError):
            return {"success": False, "data": None, "motivo": "script",
                    "error": "Ese script no está dentro de la carpeta de trabajo (cwd)."}
    if cambia and prog.nombre == "npm" and finales[:1] in (["run"], ["test"]):
        # Lo que ejecuta ese script, para que la persona lo vea al pulsar «Permitir».
        nombre_script = finales[1] if finales[0] == "run" else "test"
        try:
            import json

            scripts = json.loads((Path(cwd) / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError, TypeError):
            scripts = {}
        if nombre_script not in scripts:
            return {"success": False, "data": None, "motivo": "script",
                    "error": f"El package.json de esa carpeta no tiene el script «{nombre_script}»."}
        extra = f"\nEjecuta: {str(scripts[nombre_script])[:300]}"

    if cambia:
        from src.agente.escritura import _autorizar

        sin_carpeta = not uso.carpeta and not cwd
        reales, auditoria = _autorizar(
            capacidad, f"EJECUTAR «{linea}»",
            [(None, {"sin_carpeta": True})] if sin_carpeta else [(cwd, {"archivo": False})],
            detalle=f"{linea}\n" + ("(sin carpeta)" if sin_carpeta else f"en {cwd}") + extra)
        if reales is None:
            return auditoria
        carpeta = _hooks_vacios() if sin_carpeta else str(reales[0])
    else:
        decision = motor.evaluar("comando", cwd, archivo=False, capacidad=capacidad)
        if not decision:
            return {"success": False, "data": None, "error": decision.mensaje, "motivo": decision.motivo}
        auditoria = {"confirmacion": "no_hacia_falta"}
        carpeta = str(decision.ruta) if decision.ruta else _hooks_vacios()

    if cambia:
        control.sin_vuelta()            # un git a medias deja el repositorio bloqueado
    hecho = _correr(argv, carpeta, _plazo(PLAZO_CAMBIO if cambia else PLAZO_CONSULTA), cancelable=not cambia)
    datos = {"comando": linea, "carpeta": cwd, **hecho}
    exito = hecho["codigo"] == 0 and not hecho["vencido"]
    if exito and cambia:
        # Lo que se sabe de un comando que cambia algo (4.0-B): que terminó bien. Una
        # consulta no cambia nada: no hay efecto que comprobar.
        datos["comprobado"] = "terminó con el código 0"
    error = None if exito else ("No terminó en su plazo: se paró." if hecho["vencido"]
                                else f"Terminó con el código {hecho['codigo']}.")
    return {"success": exito, "data": datos, "error": error,
            "_auditoria": {**auditoria, "comando": linea[:200], "codigo": hecho["codigo"]}}


def run_command(program: str = "", args: list | None = None, cwd: str | None = None, **_) -> dict:
    """Un programa del catálogo que **consulta** (git status, ipconfig, ping…)."""
    return _ejecutar("run_command", program, args, cwd, cambia=False)


def run_change_command(program: str = "", args: list | None = None, cwd: str | None = None, **_) -> dict:
    """Un programa del catálogo que **cambia** cosas (git pull, git commit…). Con plan y,
    por defecto, «Permitir» en el PC (decisión mía)."""
    return _ejecutar("run_change_command", program, args, cwd, cambia=True)


# --- Procesos ---

#: Los del sistema que no se terminan nunca (los mismos que protege el Morgan local).
PROTEGIDOS = frozenset({
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "winlogon.exe", "svchost.exe", "explorer.exe", "dwm.exe",
    "fontdrvhost.exe", "lsaiso.exe", "memory compression", "secure system",
})
#: Los de Morgan: el agente, la API o la consola. Terminarlos sería apagar a Morgan.
DE_MORGAN = ("src.agente", "src.api", "src.main")


def _usuario() -> str:
    return getpass.getuser().lower()


def _es_de_la_persona(p) -> bool:
    try:
        return (p.username() or "").split("\\")[-1].lower() == _usuario()
    except Exception:
        return False


def _es_de_morgan(p) -> bool:
    try:
        if p.pid in (os.getpid(), os.getppid()):
            return True
        linea = " ".join(p.cmdline())
    except Exception:
        return False
    return any(m in linea for m in DE_MORGAN)


def _describir(p, detalle: bool = False) -> dict:
    with p.oneshot():
        info = {"pid": p.pid, "nombre": p.name(),
                "memoria_mb": round(p.memory_info().rss / 2**20, 1),
                "tuyo": _es_de_la_persona(p)}
        if detalle:
            info.update(inicio=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.create_time())),
                        hilos=p.num_threads(), estado=p.status())
            try:
                info["ruta"] = p.exe()
            except Exception:
                pass
    return info


def get_processes(name: str | None = None, pid: int | None = None, limit: int = 30, **_) -> dict:
    """Los procesos del PC, de más a menos memoria; o uno, con más detalle. Sin la línea
    de órdenes: puede llevar contraseñas o claves."""
    import psutil
    from src.agente import motor

    decision = motor.evaluar("procesos")
    if not decision:
        return {"success": False, "data": None, "error": decision.mensaje, "motivo": decision.motivo}
    if pid is not None:
        try:
            return {"success": True, "data": _describir(psutil.Process(int(pid)), detalle=True), "error": None}
        except (psutil.Error, ValueError, TypeError):
            return {"success": False, "data": None, "error": "No hay ningún proceso con ese pid."}
    lista = []
    for p in psutil.process_iter():
        try:
            if name and str(name).lower() not in p.name().lower():
                continue
            lista.append(_describir(p))
        except psutil.Error:
            continue
    lista.sort(key=lambda d: d["memoria_mb"], reverse=True)
    tope = max(1, min(int(limit or 30), 200))
    return {"success": True, "data": {"procesos": lista[:tope], "total": len(lista)}, "error": None}


def kill_process(pid: int | None = None, name: str = "", **_) -> dict:
    """Termina un proceso **de la persona**. Pide el pid **y** el nombre: si no coinciden
    (el pid se reutiliza), no se toca nada."""
    import psutil
    from src.agente.escritura import _autorizar

    try:
        proceso = psutil.Process(int(pid))
        nombre_real = proceso.name()
        nacido = proceso.create_time()
    except (psutil.Error, ValueError, TypeError):
        return {"success": False, "data": None, "motivo": "no_existe", "error": "No hay ningún proceso con ese pid."}
    if not name or nombre_real.lower() != str(name).lower():
        return {"success": False, "data": None, "motivo": "no_coincide",
                "error": f"El pid {pid} es «{nombre_real}», no «{name}»: no se toca."}
    if nombre_real.lower() in PROTEGIDOS:
        return {"success": False, "data": None, "motivo": "protegido", "error": "Es un proceso del sistema: no se termina."}
    if _es_de_morgan(proceso):
        return {"success": False, "data": None, "motivo": "protegido", "error": "Es parte de Morgan: no se termina."}
    if not _es_de_la_persona(proceso):
        return {"success": False, "data": None, "motivo": "ajeno", "error": "No es un proceso tuyo: no se termina."}

    reales, auditoria = _autorizar("kill_process", f"TERMINAR «{nombre_real}» (pid {pid})", [(None, {})],
                                   detalle=f"{nombre_real} · pid {pid}")
    if reales is None:
        return auditoria
    # Otra vez, tras la confirmación: ¿sigue siendo el mismo proceso?
    try:
        if proceso.create_time() != nacido or proceso.name() != nombre_real:
            return {"success": False, "data": None, "motivo": "cambiado",
                    "error": "El proceso cambió mientras se decidía: no se toca.", "_auditoria": auditoria}
    except psutil.Error:
        return {"success": True, "data": {"pid": pid, "nombre": nombre_real, "ya_no_estaba": True},
                "error": None, "_auditoria": auditoria}
    control.sin_vuelta()
    proceso.terminate()
    try:
        proceso.wait(timeout=5)
        forzado = False
    except psutil.TimeoutExpired:
        proceso.kill()
        forzado = True
        try:
            proceso.wait(timeout=3)
        except psutil.TimeoutExpired:
            return {"success": False, "data": None, "motivo": "sigue_vivo",
                    "error": "No se ha cerrado ni forzándolo.", "_auditoria": auditoria}
    return {"success": True, "data": {"pid": pid, "nombre": nombre_real, "forzado": forzado,
                                      "comprobado": "el proceso ya no existe"},
            "error": None, "_auditoria": {**auditoria, "proceso": nombre_real}}


TERMINAL = {"run_command": run_command, "run_change_command": run_change_command}
PROCESOS = {"get_processes": get_processes, "kill_process": kill_process}
