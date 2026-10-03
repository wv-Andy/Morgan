"""
Las automatizaciones desde el chat (4.14).

- `create_automation`: 🔴 y **solo con un plan aprobado** (decisión mía, 2026-09-30:
  siempre pregunta, también con el permiso automático). Así un archivo con instrucciones
  escondidas no puede dejar nada programado sin que la persona lo vea.
- `list_automations`: 🟢, las que tiene y cuándo tocan.

Pausar, reanudar y borrar van en la vista Automatizaciones de la web.
"""

import re
import time

from src.automatizacion import horario as horarios
from src.automatizacion import pasos as pasos_fijos
from src.automatizacion.contexto import zona_actual
from src.automatizacion.repositorio import nuevo_id
from src.identidad import usuario_actual
from src.tools.base import RiskLevel, Tool, ToolCategory
from src.tools.validation import validate_tool_args

#: Límite de partida aprobado: como mucho 10 activas por persona.
MAX_ACTIVAS = 10
MAX_NOMBRE, MAX_INSTRUCCION = 80, 1000
#: Lo que no va en pasos fijos (4.15): planificar, tareas, automatizaciones, y la memoria y el
#: conocimiento de Morgan (que un archivo con instrucciones escondidas no deje nada sembrado).
NO_PROGRAMABLES = frozenset({
    "create_automation", "list_automations", "create_plan", "get_plan", "list_plans", "create_task",
    "get_task", "list_tasks", "complete_task", "fail_task", "cancel_task", "retry_task",
    "remember_fact", "forget_fact", "add_knowledge", "remove_knowledge", "index_document"})
#: Verdes, pero piden «Permitir» en el PC cada vez: sin nadie, no se harían nunca.
PIDEN_PERMITIR = frozenset({"clipboard", "screenshot"})
#: Lo que pide cambiar algo (4.14: solo se consulta). Medido con el modelo real: «cada noche
#: borra los temporales de Descargas» se propuso y se creó; al ejecutarse no pudo borrar y el
#: aviso se inventó una búsqueda. Verbos claros, por palabra entera; «envía el resumen» no
#: entra a propósito (el modelo lo escribe para decir «cuéntamelo»). Solo imperativos e
#: infinitivos: «instalados» o «borrador» no piden nada.
CAMBIA = re.compile(
    r"\b(borra|borrar|bórr\w+|elimina|eliminar|elimín\w+|suprime|suprimir|mueve|mover|muév\w+|"
    r"renombra|renombrar|desinstala|desinstalar|instala|instalar|instál\w+|vacía|vaciar|apaga|apagar|"
    r"reinicia|reiniciar|cierra|cerrar|edita|editar|modifica|modificar|sobrescribe|sobrescribir|"
    r"escribe en|guarda en|crea (?:un|una|el|la|los|las) (?:archivo|carpeta|fichero)s?|"
    r"delete|remove|install|uninstall|move|rename)\b", re.IGNORECASE)


class _DeAutomatizaciones(Tool):
    def __init__(self, repo):
        self.repo = repo

    @property
    def category(self) -> str:
        return ToolCategory.GENERAL.value

    @property
    def requires_local(self) -> bool:
        return False


class CreateAutomationTool(_DeAutomatizaciones):
    name = "create_automation"
    description = (
        "Programa una orden que Morgan hace sola, a su hora, y deja el resultado en la bandeja. "
        "Siempre en un plan (create_plan) que la persona aprueba. O instruccion (solo consulta: "
        "buscar, leer, mirar el PC) o pasos fijos que cambian algo (herramienta y argumentos "
        "exactos, verdes o amarillos, iguales cada vez; {fecha} y {hora} se sustituyen). "
        "horario: {tipo: diaria, hora: HH:MM} · {tipo: semanal, dias: [0=lunes…6=domingo], hora} · "
        "{tipo: cada_horas, cada ≥ 1}."
    )
    parameters = {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Corto, en palabras: «Copia de Proyectos»."},
            "instruccion": {"type": "string", "description": "Qué consultar cada vez, completo."},
            "pasos": {"type": "array", "items": {"type": "object", "properties": {
                "herramienta": {"type": "string"}, "argumentos": {"type": "object"},
                "descripcion": {"type": "string"}}}},
            "horario": {"type": "object", "properties": {
                "tipo": {"type": "string", "enum": list(horarios.TIPOS)},
                "hora": {"type": "string"}, "dias": {"type": "array", "items": {"type": "integer"}},
                "cada": {"type": "integer"}}},
            "necesita_pc": {"type": "boolean"},
        },
        "required": ["nombre", "horario"],
    }
    permission_level = RiskLevel.HIGH_RISK.value

    #: El catálogo, para validar los pasos fijos (4.15). Lo pone quien la registra.
    registro = None

    @property
    def exige_plan(self) -> bool:
        return True

    def prevalidar(self, argumentos: dict) -> str | None:
        """Lo imposible no se propone (como en la 4.12): un horario que no vale, pasar de 10,
        una consulta que pide cambiar algo, o unos pasos fijos que no se podrían hacer solos."""
        if not isinstance(argumentos, dict):
            return None
        _, motivo = horarios.validar(argumentos.get("horario"))
        if motivo:
            return f"{motivo} No lo propongas así en un plan."
        if argumentos.get("pasos"):
            if (motivo := self._pasos_imposibles(argumentos["pasos"])):
                return motivo
        else:
            instruccion = str(argumentos.get("instruccion") or "").strip()
            if not instruccion:
                return "Falta la instrucción (qué consultar) o los pasos fijos (qué cambiar)."
            if (cambio := CAMBIA.search(instruccion)):
                return (f"Una instrucción solo consulta: «{cambio.group(0)}» cambia algo. Para eso, pasos "
                        "fijos (herramienta y argumentos exactos, verdes o amarillos) que la persona aprueba "
                        "una vez; si no se puede así, díselo.")
        try:
            if self.repo.contar_activas(usuario_actual()) >= MAX_ACTIVAS:
                return (f"Ya tiene {MAX_ACTIVAS} automatizaciones activas, el máximo. Que pause o borre "
                        "alguna en Automatizaciones antes de crear otra.")
        except Exception:
            return None
        return None

    def _pasos_imposibles(self, pasos) -> str | None:
        """Mis decisiones (4.15): solo verdes y amarillos, y que se puedan hacer sin nadie."""
        from src.identidad.permiso_automatico import entra
        from src.tools.planificacion import es_consulta

        normal, motivo = pasos_fijos.normalizar(pasos)
        if motivo:
            return f"{motivo} No lo propongas así en un plan."
        for i, paso in enumerate(normal, start=1):
            nombre = paso["herramienta"]
            herramienta = self.registro.get(nombre) if self.registro is not None else None
            if herramienta is None:
                return f"Paso {i}: «{nombre}» no existe (o el PC no está conectado). No lo propongas."
            if nombre in NO_PROGRAMABLES or es_consulta(nombre, paso["argumentos"]):
                return (f"Paso {i}: «{nombre}» no va en pasos fijos: una consulta no le sirve a nadie (lo que "
                        "lee no llega a los demás) y planes, tareas y memoria tampoco. Para consultar, usa "
                        "instruccion.")
            if nombre in PIDEN_PERMITIR or not entra(herramienta.permission_level):
                return (f"Paso {i}: «{nombre}» es rojo o pide «Permitir» en el PC, y sin nadie delante no se "
                        "haría nunca. Solo pasos verdes y amarillos. Díselo a la persona.")
            if (raras := pasos_fijos.marcas_desconocidas(paso["argumentos"])):
                return (f"Paso {i}: {', '.join(sorted(raras))} no se sustituye por nada: solo {{fecha}} "
                        "(AAAA-MM-DD) y {hora} (HH-MM). No lo propongas así.")
            probados = pasos_fijos.expandir(paso["argumentos"], time.time(), zona_actual())
            # Los argumentos, contra el esquema de la herramienta: lo que falta, lo que sobra
            # y los tipos (`validate_tool_args`, la misma que antes de ejecutar).
            if (motivo := validate_tool_args(herramienta, probados)):
                return f"Paso {i}: {motivo}"
            prevalidar = getattr(herramienta, "prevalidar", None)
            if prevalidar is not None and (motivo := prevalidar(probados)):
                return f"Paso {i}: {motivo}"
        return None

    def execute(self, nombre: str = "", instruccion: str = "", horario=None, necesita_pc: bool = False,
                pasos=None, **_) -> dict:
        argumentos = {"horario": horario, "instruccion": instruccion, "pasos": pasos}
        if (motivo := self.prevalidar(argumentos)):
            return {"success": False, "data": None, "error": motivo}
        normal, _ = horarios.validar(horario)
        fijos = pasos_fijos.normalizar(pasos)[0] if pasos else None
        if fijos:
            # Si algún paso es del PC, la necesita, lo diga o no el modelo.
            necesita_pc = necesita_pc or any(
                type(self.registro.get(p["herramienta"])).__module__ == "src.canal.herramientas" for p in fijos)
        zona = zona_actual()
        ahora = time.time()
        proxima = horarios.siguiente(normal, zona or "UTC", ahora)
        datos = {
            "id": nuevo_id("auto"), "user_id": usuario_actual(),
            "nombre": (str(nombre).strip() or str(instruccion).strip() or "Automatización")[:MAX_NOMBRE],
            "instruccion": (str(instruccion).strip() or "; ".join(
                p["descripcion"] or p["herramienta"] for p in fijos or []))[:MAX_INSTRUCCION],
            "horario": normal, "zona": zona or "UTC", "necesita_pc": bool(necesita_pc), "activa": True,
            "proxima": proxima, "reclamo": 0, "fallos_seguidos": 0, "creado_en": ahora, "pasos": fijos,
        }
        self.repo.crear(datos)
        return {"success": True, "error": None, "data": {
            "id": datos["id"], "nombre": datos["nombre"], "cuando": horarios.describir(normal),
            "zona": datos["zona"] + ("" if zona else " (no sé la tuya: abre Morgan en la web para usarla)"),
            "primera": _en_palabras(proxima, datos["zona"]),
            "para_la_persona": (f"Programada {horarios.describir(normal)} ({datos['zona']}); la primera, "
                                f"{_en_palabras(proxima, datos['zona'])}. Lo que cuente cada vez lo verás en "
                                "Automatizaciones.")}}


class ListAutomationsTool(_DeAutomatizaciones):
    name = "list_automations"
    description = "Las automatizaciones programadas de la persona: qué hacen, cuándo y cómo fue la última."
    parameters = {"type": "object", "properties": {}, "required": []}
    permission_level = RiskLevel.SAFE.value

    def execute(self, **_) -> dict:
        return {"success": True, "error": None, "data": {"automatizaciones": [
            {"nombre": a["nombre"], "instruccion": a["instruccion"][:200],
             "cuando": horarios.describir(a["horario"]), "activa": a["activa"],
             "proxima": _en_palabras(a["proxima"], a["zona"]) if a["activa"] else None,
             "ultima": a.get("ultimo_estado"),
             "pasos_fijos": [p["herramienta"] for p in a.get("pasos") or []] or None}
            for a in self.repo.listar(usuario_actual())],
            "nota": "Pausar, reanudar o borrar: en la vista Automatizaciones de la web."}}


def _en_palabras(momento: float, zona: str) -> str:
    from datetime import datetime

    local = datetime.fromtimestamp(momento, tz=horarios.zona(zona))
    return f"{horarios.DIAS[local.weekday()]} {local:%d/%m} a las {local:%H:%M}"


def automation_tools(repo, registro=None) -> list[Tool]:
    crear = CreateAutomationTool(repo)
    crear.registro = registro
    return [crear, ListAutomationsTool(repo)]
