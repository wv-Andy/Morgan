"""
El contexto del PC (4.13, el bloque 18 de mi lista): «cuando dices "abre el
proyecto", Morgan puede determinar qué proyecto, qué editor utilizar y dónde está».

`pc_context`, 🟢, de lectura y **solo dentro de las carpetas permitidas**:

- **Los proyectos**: carpetas con `.git`, `package.json`, `pyproject.toml`,
  `requirements.txt`, una solución de Visual Studio, `pom.xml`, `Cargo.toml` o `go.mod`, con
  su tipo y su rama de git, de la más reciente a la más antigua.
- **Los editores instalados** (VS Code, Visual Studio, PyCharm…), del menú Inicio.

No recorre los discos enteros (en mi PC se permiten `C:\\` y `D:\\`): mira las carpetas
personales y las de código habituales, hasta `PROFUNDIDAD` niveles, con tope de tiempo, y
recuerda el resultado `RECUERDA` segundos.
"""

import os
import re
import time
from pathlib import Path

from src.agente import control, motor
from src.agente.politica import CARPETAS_SENSIBLES, Politica, _dentro_del_agente, _es_reanalisis

PROFUNDIDAD = 4
MAX_PROYECTOS = 40
MAX_SEGUNDOS = 4.0
RECUERDA = 300.0
#: Qué delata un proyecto, y de qué tipo.
MARCAS = {"package.json": "node", "pyproject.toml": "python", "requirements.txt": "python",
          "setup.py": "python", "pom.xml": "java", "build.gradle": "java", "Cargo.toml": "rust",
          "go.mod": "go", "composer.json": "php", "Gemfile": "ruby", "CMakeLists.txt": "c/c++"}
#: Las carpetas de código habituales, además de las personales.
DE_CODIGO = ("source", "source/repos", "repos", "Projects", "proyectos", "dev", "code", "git", "GitHub",
             "workspace", "src")
#: Por palabra entera: «zed» no puede casar con «Customized».
EDITORES = re.compile(r"\b(visual studio code|visual studio 20\d\d|pycharm|intellij|webstorm|sublime text|"
                      r"notepad\+\+|cursor|android studio|eclipse|zinjai|code::blocks|codeblocks|rider|clion|"
                      r"g?vim|neovim|atom|fleet|zed)(?!\w)", re.IGNORECASE)
RUIDOSAS = {"node_modules", ".git", "venv", ".venv", "env", "__pycache__", "site-packages", "dist", "build",
            ".next", "target", "bin", "obj", "appdata", "$recycle.bin", ".cache", "windows", "program files",
            "program files (x86)", "programdata", "system volume information", "onedrivetemp"}

_RECORDADO: dict = {}


def _ok(datos) -> dict:
    return {"success": True, "data": datos, "error": None}


def _rama(carpeta: Path) -> str | None:
    """La rama de git, leyendo `.git/HEAD` (sin lanzar git)."""
    try:
        cabeza = (carpeta / ".git" / "HEAD").read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    return cabeza.rsplit("/", 1)[-1] if cabeza.startswith("ref:") else cabeza[:8] or None


def _tipo(carpeta: Path, nombres: set[str]) -> str | None:
    for marca, tipo in MARCAS.items():
        if marca in nombres:
            return tipo
    if any(n.lower().endswith(".sln") for n in nombres):
        return ".net"
    return "git" if ".git" in nombres else None


def _raices(politica: Politica) -> list[tuple[Path, bool]]:
    """Dónde se busca, y si esa raíz puede ser un proyecto ella misma: las carpetas de código
    habituales y las personales, si están dentro de lo permitido, y después las permitidas
    (quien permite solo `D:\\code\\app` también tiene que verla). Un escritorio con un
    `package.json` suelto no es un proyecto; una carpeta permitida a propósito, sí."""
    from src.agente.capacidades import carpetas_personales

    casa = Path.home()
    candidatas = [(casa / d, False) for d in DE_CODIGO]
    candidatas += [(Path(p), False) for p in carpetas_personales().values()] + [(casa, False)]
    candidatas += [(Path(c), len(Path(c).parts) > 1) for c in politica.carpetas]
    vistas, salida = set(), []
    for c, puede_serlo in candidatas:
        clave = os.path.normcase(os.path.normpath(str(c)))
        if clave in vistas or not c.is_dir():
            continue
        vistas.add(clave)
        try:
            politica.resolver(str(c), archivo=False)
        except Exception:
            continue            # fuera de lo permitido, bloqueada o de credenciales
        salida.append((c, puede_serlo and os.path.normcase(str(c)) != os.path.normcase(str(casa))))
    return salida


def proyectos(politica: Politica) -> tuple[list[dict], bool]:
    """Los proyectos dentro de lo permitido, del más reciente al más antiguo, y si se miró todo.
    A lo ancho: primero lo de arriba, y cada carpeta una sola vez (el escritorio cuelga de la
    casa, y la casa de un disco permitido)."""
    encontrados, vistos = [], set()
    limite = time.monotonic() + MAX_SEGUNDOS
    completo = True
    pendientes = [(r, 0, puede) for r, puede in _raices(politica)]
    while pendientes:
        if time.monotonic() > limite or len(encontrados) >= MAX_PROYECTOS:
            completo = False
            break
        carpeta, nivel, puede_serlo = pendientes.pop(0)
        clave = os.path.normcase(str(carpeta))
        if clave in vistos:
            continue
        vistos.add(clave)
        try:
            entradas = list(os.scandir(carpeta))
        except OSError:
            continue
        nombres = {e.name for e in entradas}
        tipo = _tipo(carpeta, nombres)
        if tipo and (nivel > 0 or puede_serlo):
            try:
                tocado = carpeta.stat().st_mtime
                for e in entradas:
                    if e.is_file(follow_symlinks=False):
                        tocado = max(tocado, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                tocado = 0
            encontrados.append({"nombre": carpeta.name, "path": str(carpeta), "tipo": tipo,
                                "rama": _rama(carpeta) if ".git" in nombres else None, "_t": tocado})
            continue            # dentro de un proyecto no se buscan más proyectos
        if nivel >= PROFUNDIDAD:
            continue
        for e in entradas:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            bajo = e.name.lower()
            ruta = Path(e.path)
            if (bajo in RUIDOSAS or bajo in CARPETAS_SENSIBLES or bajo.startswith(".") or _es_reanalisis(ruta)
                    or _dentro_del_agente(ruta) or politica.esta_bloqueada(ruta)):
                continue
            pendientes.append((ruta, nivel + 1, True))
        control.punto_seguro()
    encontrados.sort(key=lambda p: p["_t"], reverse=True)
    for p in encontrados:
        p["modificado"] = time.strftime("%Y-%m-%d", time.localtime(p.pop("_t"))) if p.get("_t") else None
    return encontrados, completo


def editores() -> list[str]:
    try:
        from src.agente.aplicaciones import instaladas
    except Exception:
        return []
    salida = []
    for app in instaladas():
        if EDITORES.search(app["nombre"]):
            salida.append(app["nombre"])
    return sorted(set(salida))


def pc_context(buscar: str = "", **_) -> dict:
    """Los proyectos y los editores del PC. `buscar`: solo los proyectos cuyo nombre lo contenga."""
    politica = Politica.cargar()
    decision = motor.evaluar("contexto", politica=politica)
    if not decision:
        return {"success": False, "data": None, "error": decision.mensaje, "motivo": decision.motivo}
    if not politica.carpetas:
        return {"success": False, "data": None, "motivo": "sin_carpetas",
                "error": "Este PC no ha permitido ninguna carpeta."}
    clave = (Politica.firma(), str(Path.home()))
    guardado = _RECORDADO.get(clave)
    if guardado and time.monotonic() - guardado[0] < RECUERDA:
        lista, completo, eds = guardado[1]
    else:
        lista, completo = proyectos(politica)
        eds = editores()
        _RECORDADO.clear()
        _RECORDADO[clave] = (time.monotonic(), (lista, completo, eds))
    if buscar:
        b = str(buscar).lower()
        lista = [p for p in lista if b in p["nombre"].lower() or b in p["path"].lower()]
    datos = {"proyectos": lista[:MAX_PROYECTOS], "editores": eds,
             "pista": ("Para abrir uno: open_app con accion=aplicacion, el editor como nombre y su "
                       "path. Si hay varios que encajan, pregunta cuál.")}
    if not completo:
        datos["aviso"] = "No se miró todo (tope de tiempo): si falta alguno, search_files lo encuentra."
    return _ok(datos)


CONTEXTO = {"pc_context": pc_context}
