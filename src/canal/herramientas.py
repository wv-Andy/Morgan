"""
Las herramientas del equipo, en la nube (3.0-E): representantes que mandan la petición
al agente local de la persona del turno.

**Mismo nombre y mismos argumentos** que las del Morgan de tu equipo (`list_files`,
`read_file`, `search_files`, `system_info`): el modelo ya las conoce. La diferencia
está en lo que hacen: su `execute()` no toca ningún disco, llama al despacho
(`despacho.enviar`), que manda la orden al agente de **esa** persona.

**Existen solo mientras el equipo está conectado** (§22-§23 del plan). `disponible()`
dice si la persona de este turno tiene su agente conectado y si anunció esa capacidad
(un agente sin carpetas permitidas no anuncia leer archivos). El núcleo quita del
catálogo del turno las que no lo estén, y con ello el prompt vuelve a decir «no tienes
acceso al equipo». Así Morgan sabe que el PC está apagado **antes** de intentarlo.

Pasan por todo lo de siempre en el núcleo: validación de argumentos, permisos,
auditoría y eventos. El agente, además, lo vuelve a comprobar todo en el PC.

**El contenido de un archivo vuelve envuelto como dato no confiable**
(`<untrusted_file_data>`, §20): lo envuelve la nube, que es quien lo pone en el prompt,
y escapa un cierre del envoltorio escrito dentro del propio archivo, que si no
serviría para salirse de él.
"""

import logging
from typing import Any

from src.canal.despacho import AgenteDesconectado, AgenteSinRespuesta, ErrorDelCanal, enviar
from src.eventos_turno import emitir
from src.canal.registro import REGISTRO
from src.identidad import usuario_actual
from src.tools.base import RiskLevel, Tool, ToolCategory
from src.tools.filesystem import ListFilesTool, ReadFileTool, SearchFilesTool
from src.tools.system import SystemInfoTool

logger = logging.getLogger(__name__)

CIERRE = "</untrusted_file_data>"

#: Lo que se añade a la descripción de cada una: dónde actúa y con qué límites.
#:
#: **Corto a propósito** (3.1-E). Medido en mi PC el 2026-09-19: con el catálogo
#: del equipo, tres búsquedas y varios listados en el historial, una petición al modelo
#: llegó a 8.459 tokens y Groq la rechazó —su tope son 8.000 por minuto—; Gemini estaba
#: saturado y no quedaba respaldo, así que el turno murió. Cada palabra de más aquí se
#: paga en **todas** las llamadas del turno.
NOTA = " En el PC de la persona, y solo en las carpetas que permitió allí."


#: El argumento para elegir el PC (3.7). **Solo aparece con varios conectados**: con uno,
#: nada cambia y no se paga ni un token de más (el tope de Groq).
EQUIPO = {"type": "string", "description": "Nombre del equipo en el que actuar (hay varios conectados)"}


def _conectados() -> list:
    return REGISTRO.todas(usuario_actual())


def _con_equipo(parametros: dict) -> dict:
    """Los parámetros, más `equipo` si la persona tiene varios PC conectados."""
    if len(_conectados()) < 2:
        return parametros
    return {**parametros, "properties": {**parametros.get("properties", {}), "equipo": EQUIPO}}


def _la_ofrece_alguno(nombre: str) -> bool:
    """Si algún PC conectado de la persona de este turno anuncia esa capacidad."""
    return any(nombre in c.capacidades for c in _conectados())


def envolver(texto: str, origen: str) -> str:
    """Aísla el contenido como dato no confiable, sin que el propio texto pueda cerrarlo."""
    seguro = texto.replace(CIERRE, "&lt;/untrusted_file_data&gt;")
    return f'<untrusted_file_data source="{origen}">\n{seguro}\n{CIERRE}'


class HerramientaDelEquipo(Tool):
    """El representante en la nube de una herramienta que corre en el PC."""

    #: El núcleo recalcula el prompt del turno cuando alguna de estas está disponible.
    dinamica = True
    #: Su efecto está en el PC, no en el disco de la nube: la verificación usa lo que
    #: comprobó el agente allí (auditoría de la 3.x).
    en_el_pc = True
    motivo_no_disponible = "el PC de la persona no está conectado (el agente local no está en marcha)."

    def __init__(self, local: Tool):
        self._local = local

    @property
    def name(self) -> str:
        return self._local.name

    @property
    def description(self) -> str:
        return self._local.description.rstrip() + NOTA

    @property
    def parameters(self) -> dict:
        return _con_equipo(self._local.parameters)

    @property
    def permission_level(self) -> str:
        return self._local.permission_level

    @property
    def category(self) -> str:
        return self._local.category

    @property
    def requires_local(self) -> bool:
        # No toca el disco de este servidor: por eso puede estar en la nube.
        return False

    def disponible(self) -> bool:
        """Si la persona de ESTE turno tiene algún PC conectado que anuncia esto."""
        return _la_ofrece_alguno(self.name)

    def execute(self, equipo: str | None = None, **kwargs: Any) -> dict:
        try:
            respuesta = enviar(self.name, kwargs, equipo=equipo)
        except ErrorDelCanal as exc:
            return {"success": False, "data": None, "error": str(exc)}

        resultado = respuesta.get("resultado") or {}
        if respuesta.get("estado") == "REJECTED":
            return {"success": False, "data": None,
                    "error": resultado.get("error") or "Tu equipo rechazó la petición."}

        datos = resultado.get("data")
        if self.name == "read_file" and isinstance(datos, dict) and isinstance(datos.get("content"), str):
            datos = {**datos, "content": envolver(datos["content"], f"tu PC: {datos.get('path', '')}")}
        return {
            "success": bool(resultado.get("success")),
            "data": datos,
            "error": resultado.get("error"),
        }


class CopiarDelEquipo(Tool):
    """Trae una copia de un archivo del PC y la deja lista para descargar (3.1-E).

    No es un representante de una herramienta local como las de arriba: no existe en el
    Morgan de tu equipo, donde el archivo ya está a mano. Aquí hace falta porque la
    persona está en el móvil y su PC en casa.

    **El contenido no pasa por el modelo**: viaja en trozos por el canal, se comprueba
    (tamaño y huella) y se guarda en los archivos de esa persona, que **se borran solos a
    las 24 h** (decisión mía). El modelo solo recibe el nombre, el tamaño y el enlace
    para enseñárselo.
    """

    dinamica = True
    en_el_pc = True
    motivo_no_disponible = "el PC de la persona no está conectado (el agente local no está en marcha)."

    #: Un archivo de 20 MB por una conexión doméstica no cabe en el plazo normal de una
    #: orden (20 s): subir 20 MB a 5 Mbit/s son más de 30 s.
    PLAZO = 180.0

    def __init__(self, store):
        self.store = store

    @property
    def name(self) -> str:
        return "copy_file"

    @property
    def description(self) -> str:
        return (
            "Copia un archivo del PC y devuelve un enlace para descargarlo. Úsala cuando "
            "pidan el archivo («pásame», «mándame»), no su contenido (eso es read_file). "
            "Cualquier formato, hasta 20 MB." + NOTA
        )

    @property
    def parameters(self) -> dict:
        return _con_equipo({
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Ruta completa del archivo en el PC"}},
            "required": ["path"],
        })

    @property
    def permission_level(self) -> str:
        return RiskLevel.SAFE.value

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def requires_local(self) -> bool:
        return False

    def disponible(self) -> bool:
        return _la_ofrece_alguno(self.name)

    def execute(self, path: str = "", equipo: str | None = None, **kwargs: Any) -> dict:
        from src.uploads.store import ArchivoRechazado

        try:
            respuesta = enviar(self.name, {"path": path}, plazo=self.PLAZO, equipo=equipo)
        except ErrorDelCanal as exc:
            return {"success": False, "data": None, "error": str(exc)}

        resultado = respuesta.get("resultado") or {}
        if not resultado.get("success"):
            return {"success": False, "data": None,
                    "error": resultado.get("error") or "Tu equipo no pudo copiar ese archivo."}

        datos = resultado.get("data") or {}
        try:
            archivo = self.store.guardar_copia_del_equipo(
                datos.get("name") or "archivo", respuesta.get("_contenido") or b"", datos.get("tipo"),
            )
        except ArchivoRechazado as exc:
            return {"success": False, "data": None, "error": str(exc)}

        logger.info("Copia del equipo guardada: %s (%s bytes)", archivo.id, archivo.tamano)
        # Un aviso para la web: que pinte **un botón de descargar** bajo la respuesta,
        # sin depender de cómo el modelo escriba el enlace. probé la primera
        # versión (2026-09-19) y le salió «una especie de enlace», no un botón: el
        # modelo había escrito la dirección a su manera. Del archivo viaja lo que la
        # persona ya ve en sus archivos: su nombre, su tamaño y su identificador.
        emitir("archivo_listo", upload_id=archivo.id, nombre=archivo.nombre_original,
               bytes=archivo.tamano)
        return {
            "success": True,
            "data": {
                "nombre": archivo.nombre_original,
                "bytes": archivo.tamano,
                "upload_id": archivo.id,
                "enlace": f"/api/uploads/{archivo.id}/contenido",
                "caduca": "24 horas",
                "aviso": ("Dale a la persona el enlace tal cual, en un enlace de Markdown "
                          "con el nombre del archivo. No intentes leer el contenido aquí."),
            },
            "error": None,
        }


#: Las de escritura (3.3). **Aún más cortas que las de lectura**: sin la NOTA ni «solo con
#: plan», que ya dice una vez la sección del prompt (`PUEDE_ESCRIBIR`) en vez de cinco.
#: Medido: con la NOTA y rutas descritas, las cinco sumaban 2.000 caracteres (~500
#: tokens) en cada llamada del turno; así, 1.141 (~290).
_RUTA = {"type": "string"}
ESCRITURA_EN_EL_EQUIPO = {
    "create_file": ("Crea un archivo de texto nuevo; no pisa uno existente.",
                    {"path": _RUTA, "content": _RUTA}, ["path", "content"], RiskLevel.MODERATE),
    "edit_file": ("Cambia old_text (exacto, una sola vez) por new_text en un archivo de texto.",
                  {"path": _RUTA, "old_text": _RUTA, "new_text": _RUTA},
                  ["path", "old_text", "new_text"], RiskLevel.MODERATE),
    "append_file": ("Añade content, en su propia línea, al final de un archivo de texto.",
                    {"path": _RUTA, "content": _RUTA}, ["path", "content"], RiskLevel.MODERATE),
    "create_folder": ("Crea una carpeta.", {"path": _RUTA}, ["path"], RiskLevel.MODERATE),
    "move_file": ("Mueve o renombra; no pisa el destino.",
                  {"src": _RUTA, "dst": _RUTA}, ["src", "dst"], RiskLevel.MODERATE),
    "delete_file": ("Manda a la Papelera un archivo o carpeta vacía.",
                    {"path": _RUTA}, ["path"], RiskLevel.CRITICAL),
}


class EscribirEnElEquipo(Tool):
    """Una de las que cambian cosas en el PC (3.3).

    **Solo con un plan aprobado** (`exige_plan`): la persona aprueba en la web el paso
    con estos argumentos exactos, una vez. El núcleo no la ejecuta sin él, y el modelo
    no puede aprobarse a sí mismo ni cambiar la ruta después. En el PC, además, el
    agente lo vuelve a comprobar todo con su política y, para borrar, pregunta a la
    persona con una notificación.
    """

    dinamica = True
    en_el_pc = True
    motivo_no_disponible = "el PC de la persona no está conectado (el agente local no está en marcha)."

    #: Borrar puede esperar a que la persona conteste en su PC (hasta 120 s).
    PLAZO = 180.0

    def __init__(self, nombre: str):
        self._nombre = nombre
        self._descripcion, self._propiedades, self._requeridos, self._riesgo = ESCRITURA_EN_EL_EQUIPO[nombre]

    @property
    def name(self) -> str:
        return self._nombre

    @property
    def description(self) -> str:
        return self._descripcion

    @property
    def parameters(self) -> dict:
        return _con_equipo({"type": "object", "properties": self._propiedades, "required": self._requeridos})

    @property
    def permission_level(self) -> str:
        return self._riesgo.value

    @property
    def category(self) -> str:
        return ToolCategory.FILESYSTEM.value

    @property
    def requires_local(self) -> bool:
        return False

    @property
    def exige_plan(self) -> bool:
        return True

    def disponible(self) -> bool:
        return _la_ofrece_alguno(self.name)

    def execute(self, equipo: str | None = None, **kwargs: Any) -> dict:
        argumentos = {k: v for k, v in kwargs.items() if k in self._propiedades}
        if kwargs.get("_origen"):
            # El paso del plan del que sale (4.1). Un agente anterior lo ignora.
            argumentos["origen"] = str(kwargs["_origen"])[:120]
        try:
            respuesta = enviar(self.name, argumentos, plazo=self.PLAZO, equipo=equipo)
        except (AgenteSinRespuesta, AgenteDesconectado) as exc:
            # La orden salió y la respuesta no volvió: pudo hacerse o no. Repetirla a
            # ciegas es lo que la 3.3.5 llama «respuesta perdida».
            return {"success": False, "data": None,
                    "error": f"{exc} No se sabe si llegó a hacerse: compruébalo (list_files) antes de repetirlo."}
        except ErrorDelCanal as exc:
            return {"success": False, "data": None, "error": str(exc)}
        resultado = respuesta.get("resultado") or {}
        if respuesta.get("estado") == "REJECTED":
            return {"success": False, "data": None,
                    "error": resultado.get("error") or "Tu equipo rechazó la petición."}
        # El motivo del agente viaja (4.4): «no_confirmada» es una decisión de la persona,
        # no un fallo que se pueda intentar por otro camino.
        return {"success": bool(resultado.get("success")), "data": resultado.get("data"),
                "error": resultado.get("error"), "motivo": resultado.get("motivo")}


#: La terminal y los procesos (3.5). Sin shell: un programa del catálogo y sus argumentos.
#: `run_command` solo consulta y no pide plan; lo que cambia, sí (decisión mía).
_ARGS = {"type": "array", "items": {"type": "string"}}
TERMINAL_EN_EL_EQUIPO = {
    "run_command": (
        "Ejecuta en el PC un programa del catálogo que solo CONSULTA: git status/log/diff/"
        "show/branch, ipconfig, ping, nslookup, python/node --version, python -m pip list, "
        "npm ls/outdated, winget search/list/show. Sin shell: programa y lista de argumentos.",
        {"program": _RUTA, "args": _ARGS, "cwd": _RUTA}, ["program"], RiskLevel.LOW_RISK, False, 45.0),
    "run_change_command": (
        "Ejecuta en el PC un comando del catálogo que CAMBIA cosas: git fetch/pull/add/"
        "commit/switch, python -m pip install/uninstall, npm install/ci/uninstall/run/test, "
        "winget install/upgrade/uninstall --id, o un script propio (python x.py, node x.js) "
        "de cwd. La persona lo confirma en su PC.",
        {"program": _RUTA, "args": _ARGS, "cwd": _RUTA}, ["program", "cwd"], RiskLevel.HIGH_RISK, True, 180.0),
    "get_processes": (
        "Procesos del PC, de más a menos memoria (o uno, con pid).",
        {"name": _RUTA, "pid": {"type": "integer"}}, [], RiskLevel.SAFE, False, 20.0),
    "kill_process": (
        "Termina un proceso del PC de la persona (pid y nombre, que tienen que coincidir). "
        "Lo confirma en su PC.",
        {"pid": {"type": "integer"}, "name": _RUTA}, ["pid", "name"], RiskLevel.CRITICAL, True, 180.0),
}


class TerminalDelEquipo(EscribirEnElEquipo):
    """Una de la terminal o los procesos del PC (3.5). Como las de escritura, pero no
    todas exigen plan: consultar no cambia nada."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = TERMINAL_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan


#: Los archivos más allá de crear y editar (4.6, mi lista). Mismo formato que la
#: terminal: descripción, propiedades, requeridas, riesgo, si exige plan y plazo. Copiar y
#: comprimir escriben algo nuevo (🟡, con plan o con el permiso automático); los
#: metadatos solo leen (🟢).
_LISTA = {"type": "array", "items": {"type": "string"}}
ARCHIVOS_EN_EL_EQUIPO = {
    "file_info": (
        "Tipo, tamaño, fechas y atributos de un archivo; de una carpeta, cuántos archivos tiene y cuánto ocupa.",
        {"path": _RUTA}, ["path"], RiskLevel.SAFE, False, 20.0),
    "copy_path": (
        "Copia un archivo o carpeta a un sitio nuevo dentro del PC; no pisa nada. Sin programas ni scripts.",
        {"src": _RUTA, "dst": _RUTA}, ["src", "dst"], RiskLevel.MODERATE, True, 180.0),
    "compress": (
        "accion=comprimir: paths en un .zip nuevo (dst). accion=descomprimir: el .zip de path "
        "en la carpeta dst (nueva, o una vacía). Comprime también scripts; nunca los saca.",
        {"accion": {"type": "string", "enum": ["comprimir", "descomprimir"]},
         "paths": _LISTA, "path": _RUTA, "dst": _RUTA}, ["accion", "dst"], RiskLevel.MODERATE, True, 180.0),
}


class ArchivoDelEquipo(EscribirEnElEquipo):
    """Una de `ARCHIVOS_EN_EL_EQUIPO` (4.6). Como las de escritura, pero los metadatos
    solo leen y no exigen plan."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = ARCHIVOS_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan


#: El PC por dentro (4.7): una herramienta con `aspecto`, por el presupuesto de tokens.
#: Solo lee (🟢); en el PC nace apagada.
DIAGNOSTICO_EN_EL_EQUIPO = {
    "pc_diagnostics": (
        "El PC por dentro. aspecto=rendimiento (CPU, memoria y qué consume más: «¿por qué va "
        "lento?»), red (interfaces e IP), puertos (qué escucha y qué programa) o servicios "
        "(de Windows; filtro por nombre). Discos, GPU y batería: system_info.",
        {"aspecto": {"type": "string", "enum": ["rendimiento", "red", "puertos", "servicios"]},
         "filtro": _RUTA}, ["aspecto"], RiskLevel.SAFE, False, 30.0),
    # El contexto (4.13): también solo lee, y en el PC nace encendida (dentro de lo permitido).
    "pc_context": (
        "Los proyectos de código del PC (carpeta, tipo, rama de git) y los editores instalados. "
        "Úsala cuando digan «el proyecto X», «mi proyecto» o «mi editor» sin la ruta; buscar: "
        "parte del nombre.",
        {"buscar": _RUTA}, [], RiskLevel.SAFE, False, 30.0),
}


#: Abrir y cerrar aplicaciones (4.8). Abrir, 🟢 y sin plan (permisos aprobados por mí);
#: cerrar, 🔴, con plan y «Permitir». En el PC, las dos nacen apagadas.
APLICACIONES_EN_EL_EQUIPO = {
    "open_app": (
        "Abre en el PC. accion=aplicacion (nombre de una instalada, también de la Store o un "
        "juego; path opcional, p. ej. una carpeta para VS Code), archivo (path, con su programa), url, carpeta (path, en el "
        "Explorador) o listar (las instaladas). Nunca consolas ni scripts. Sin plan.",
        {"accion": {"type": "string", "enum": ["aplicacion", "archivo", "url", "carpeta", "listar"]},
         "nombre": _RUTA, "path": _RUTA, "url": _RUTA}, ["accion"], RiskLevel.SAFE, False, 30.0),
    "close_app": (
        "Cierra un programa como su X (pid y nombre, de get_processes). Si pregunta si guardar, "
        "no se cierra salvo forzar. La persona lo confirma en su PC.",
        {"pid": {"type": "integer"}, "name": _RUTA, "forzar": {"type": "boolean"}},
        ["pid", "name"], RiskLevel.CRITICAL, True, 180.0),
}


class AplicacionDelEquipo(EscribirEnElEquipo):
    """Una de `APLICACIONES_EN_EL_EQUIPO` (4.8)."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = APLICACIONES_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan


#: Arrancar, parar o reiniciar un servicio de Windows (4.9): 🔴, plan y «Permitir».
SERVICIOS_EN_EL_EQUIPO = {
    "service_control": (
        "Inicia, para o reinicia un servicio de Windows (nombre corto, de pc_diagnostics). "
        "Muchos piden administrador: entonces no se puede, y se dice. Lo confirma en su PC.",
        {"nombre": _RUTA, "accion": {"type": "string", "enum": ["iniciar", "parar", "reiniciar"]}},
        ["nombre", "accion"], RiskLevel.CRITICAL, True, 180.0),
}


#: El portapapeles y los avisos (4.10), 🟢 y sin plan. Leer el portapapeles pide «Permitir»
#: en el PC cada vez; lo leído viaja como dato no confiable, igual que un archivo.
AVISOS_EN_EL_EQUIPO = {
    "clipboard": (
        "El portapapeles del PC. accion=escribir (texto) o leer (la persona lo permite en su PC).",
        {"accion": {"type": "string", "enum": ["leer", "escribir"]}, "texto": _RUTA},
        ["accion"], RiskLevel.SAFE, False, 150.0),
    "notify": (
        "Enseña una notificación en el PC (p. ej., al terminar algo que pidió). Sin plan.",
        {"titulo": _RUTA, "mensaje": _RUTA}, ["mensaje"], RiskLevel.SAFE, False, 20.0),
}


class AvisoDelEquipo(EscribirEnElEquipo):
    """Una de `AVISOS_EN_EL_EQUIPO` (4.10)."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = AVISOS_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan

    def execute(self, equipo: str | None = None, **kwargs: Any) -> dict:
        resultado = super().execute(equipo=equipo, **kwargs)
        datos = resultado.get("data")
        if isinstance(datos, dict) and isinstance(datos.get("texto"), str):
            # Lo copiado puede traer instrucciones escondidas: es un dato, no una orden.
            resultado["data"] = {**datos, "texto": envolver(datos["texto"], "tu portapapeles")}
        return resultado


#: Las ventanas (4.11), 🟢 y sin plan: visible y reversible.
VENTANAS_EN_EL_EQUIPO = {
    "windows": (
        "Las ventanas del PC. accion=listar, enfocar, minimizar, maximizar, restaurar, mover "
        "(x, y, ancho, alto) o lado_a_lado (y otra_id u otro_titulo). Una ventana: por titulo o id.",
        {"accion": {"type": "string", "enum": ["listar", "enfocar", "minimizar", "maximizar", "restaurar",
                                               "mover", "lado_a_lado"]},
         "id": {"type": "integer"}, "titulo": _RUTA, "otra_id": {"type": "integer"}, "otro_titulo": _RUTA,
         "x": {"type": "integer"}, "y": {"type": "integer"}, "ancho": {"type": "integer"},
         "alto": {"type": "integer"}}, ["accion"], RiskLevel.SAFE, False, 20.0),
}


class VentanaDelEquipo(EscribirEnElEquipo):
    """La de `VENTANAS_EN_EL_EQUIPO` (4.11)."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = VENTANAS_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan


class CapturaDelEquipo(CopiarDelEquipo):
    """Una captura de la pantalla del PC (4.11). Como `copy_file`: la imagen viaja por el
    canal, queda entre los archivos de la persona (24 h, con su botón de descargar) y **no
    pasa por el modelo**; para verla, el modelo usa `analyze_image` con su `upload_id`. En
    el PC se pide «Permitir» cada vez."""

    @property
    def name(self) -> str:
        return "screenshot"

    @property
    def description(self) -> str:
        return ("Captura de la pantalla del PC: alcance=pantalla, ventana (id o titulo, de windows) "
                "o region (x, y, ancho, alto). La persona lo permite en su PC. Para ver qué hay, "
                "analyze_image con el upload_id que devuelve.")

    @property
    def parameters(self) -> dict:
        return _con_equipo({"type": "object", "properties": {
            "alcance": {"type": "string", "enum": ["pantalla", "ventana", "region"]},
            "id": {"type": "integer"}, "titulo": _RUTA, "x": {"type": "integer"}, "y": {"type": "integer"},
            "ancho": {"type": "integer"}, "alto": {"type": "integer"}}, "required": []})

    def execute(self, equipo: str | None = None, **kwargs: Any) -> dict:
        from src.uploads.store import ArchivoRechazado

        argumentos = {k: v for k, v in kwargs.items()
                      if k in ("alcance", "id", "titulo", "x", "y", "ancho", "alto")}
        try:
            respuesta = enviar(self.name, argumentos, plazo=self.PLAZO, equipo=equipo)
        except ErrorDelCanal as exc:
            return {"success": False, "data": None, "error": str(exc)}
        resultado = respuesta.get("resultado") or {}
        if not resultado.get("success"):
            return {"success": False, "data": None, "motivo": resultado.get("motivo"),
                    "error": resultado.get("error") or "Tu equipo no pudo hacer la captura."}
        datos = resultado.get("data") or {}
        contenido = respuesta.get("_contenido") or b""
        if not contenido.startswith(b"\x89PNG"):
            return {"success": False, "data": None, "error": "Lo que llegó del PC no es una imagen."}
        try:
            archivo = self.store.guardar_copia_del_equipo(datos.get("name") or "captura.png", contenido,
                                                          "image/png")
        except ArchivoRechazado as exc:
            return {"success": False, "data": None, "error": str(exc)}
        emitir("archivo_listo", upload_id=archivo.id, nombre=archivo.nombre_original, bytes=archivo.tamano)
        return {"success": True, "data": {
            "upload_id": archivo.id, "de": datos.get("de"), "ancho": datos.get("ancho"), "alto": datos.get("alto"),
            "aviso": ("Para saber qué se ve, usa analyze_image con este upload_id. La captura queda "
                      "24 h en sus archivos, con su botón de descargar.")}, "error": None}


#: El control de la interfaz (4.12). Leer los controles, 🟢; manejar ratón y teclado, 🔴,
#: siempre con plan, y en el PC «Permitir» al empezar el plan.
INTERFAZ_EN_EL_EQUIPO = {
    "ui_read": (
        "Los controles de una ventana del PC (botones, campos, enlaces…) por su nombre, para "
        "ui_control. Ventana por titulo o id (de windows).",
        {"titulo": _RUTA, "id": {"type": "integer"}, "filtro": _RUTA}, [], RiskLevel.SAFE, False, 30.0),
    "ui_control": (
        "Maneja una ventana del PC: accion=clic (elemento), escribir (elemento, texto) o teclas "
        "(p. ej. ctrl+s, enter). El elemento, por su nombre de ui_read. Nunca contraseñas, consolas "
        "ni el Explorador. La persona lo permite en su PC.",
        {"accion": {"type": "string", "enum": ["clic", "escribir", "teclas"]}, "titulo": _RUTA,
         "id": {"type": "integer"}, "elemento": _RUTA, "tipo": _RUTA, "texto": _RUTA, "teclas": _RUTA},
        ["accion"], RiskLevel.HIGH_RISK, True, 180.0),
}


class InterfazDelEquipo(EscribirEnElEquipo):
    """Una de `INTERFAZ_EN_EL_EQUIPO` (4.12)."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = INTERFAZ_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan

    def prevalidar(self, argumentos: dict) -> str | None:
        """Lo que se sabe imposible **antes** de pedir un plan (4.12). Medido con el modelo
        real: propuso planes para pulsar Win+R y escribir en una contraseña, que el PC habría
        rechazado al ejecutarlos; la persona aprobaba algo que no podía salir. El PC sigue
        comprobándolo todo: esto solo ahorra la propuesta inútil."""
        from src.agente.teclas import PERMITIDAS, parece_clave, se_puede

        if self._nombre != "ui_control" or not isinstance(argumentos, dict):
            return None
        accion = str(argumentos.get("accion") or "").lower()
        if accion == "teclas" and not se_puede(argumentos.get("teclas")):
            return (f"«{argumentos.get('teclas')}» no se puede pulsar desde Morgan (nunca la tecla Windows). "
                    f"Sí: {PERMITIDAS}. No lo propongas en un plan: díselo a la persona.")
        if accion == "escribir" and parece_clave(argumentos.get("elemento")):
            return ("Morgan nunca escribe en un campo de contraseña. No lo propongas en un plan: "
                    "díselo a la persona.")
        return None


class ServicioDelEquipo(EscribirEnElEquipo):
    """La de `SERVICIOS_EN_EL_EQUIPO` (4.9)."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = SERVICIOS_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan


class DiagnosticoDelEquipo(EscribirEnElEquipo):
    """La de `DIAGNOSTICO_EN_EL_EQUIPO` (4.7). Solo lee: no exige plan."""

    def __init__(self, nombre: str):
        self._nombre = nombre
        (self._descripcion, self._propiedades, self._requeridos, self._riesgo,
         self._exige_plan, self.PLAZO) = DIAGNOSTICO_EN_EL_EQUIPO[nombre]

    @property
    def exige_plan(self) -> bool:
        return self._exige_plan


class SistemaDelEquipo(HerramientaDelEquipo):
    """`system_info` del PC, con lo que el agente da desde la 4.7. La descripción es propia:
    con la del Morgan local («CPU, RAM, disco»), medido con el modelo real, a «¿cuánta
    batería me queda?» contestó que no tenía herramienta para eso."""

    @property
    def description(self) -> str:
        return ("Windows, procesador, RAM, discos y espacio libre, tarjeta gráfica, batería y "
                "cuánto lleva encendido el PC." + NOTA)


class BuscarEnElEquipo(HerramientaDelEquipo):
    """`search_files` con los filtros que el agente sabe desde la 4.6: extensión, fecha,
    tamaño y texto de dentro. El esquema es propio: el de Morgan en local no los tiene."""

    @property
    def description(self) -> str:
        return ("Busca en las carpetas permitidas. query: parte del NOMBRE. contenido: texto que "
                "aparece DENTRO del archivo. También extension, fechas de modificación (AAAA-MM-DD) "
                "y tamaño en KB; con al menos uno." + NOTA)

    @property
    def parameters(self) -> dict:
        return _con_equipo({"type": "object", "properties": {
            "query": _RUTA, "path": _RUTA, "extension": _RUTA,
            "modificado_desde": _RUTA, "modificado_hasta": _RUTA,
            "tamano_min_kb": {"type": "number"}, "tamano_max_kb": {"type": "number"},
            "contenido": _RUTA, "max_results": {"type": "integer"},
        }, "required": []})


def herramientas_del_equipo(store=None) -> list[Tool]:
    representantes: list[Tool] = [
        HerramientaDelEquipo(t)
        for t in (ListFilesTool(), ReadFileTool())
    ]
    representantes.append(SistemaDelEquipo(SystemInfoTool()))
    representantes.append(BuscarEnElEquipo(SearchFilesTool()))
    if store is not None:
        representantes.append(CopiarDelEquipo(store))
    representantes += [EscribirEnElEquipo(n) for n in ESCRITURA_EN_EL_EQUIPO]
    representantes += [TerminalDelEquipo(n) for n in TERMINAL_EN_EL_EQUIPO]
    representantes += [ArchivoDelEquipo(n) for n in ARCHIVOS_EN_EL_EQUIPO]
    representantes += [DiagnosticoDelEquipo(n) for n in DIAGNOSTICO_EN_EL_EQUIPO]
    representantes += [AplicacionDelEquipo(n) for n in APLICACIONES_EN_EL_EQUIPO]
    representantes += [ServicioDelEquipo(n) for n in SERVICIOS_EN_EL_EQUIPO]
    representantes += [AvisoDelEquipo(n) for n in AVISOS_EN_EL_EQUIPO]
    representantes += [VentanaDelEquipo(n) for n in VENTANAS_EN_EL_EQUIPO]
    representantes += [InterfazDelEquipo(n) for n in INTERFAZ_EN_EL_EQUIPO]
    if store is not None:
        representantes.append(CapturaDelEquipo(store))
    return representantes
