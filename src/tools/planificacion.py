"""
Herramientas de planificación (V1.6).

Permiten a Morgan **decir qué va a hacer antes de hacerlo**. La diferencia con las
herramientas de tareas es de tiempo verbal: una tarea registra lo que ya pasó, un
plan describe lo que va a pasar.

Son `requires_local = False`: trabajan sobre la base, no sobre el disco, así que
existen igual en la nube. Y son `safe` ellas mismas —planificar no ejecuta nada—
aunque el plan que produzcan sea de riesgo crítico. Confundir esas dos cosas
llevaría a pedir permiso para *pensar*.
"""

import logging
from typing import Any

from src.tools.base import NIVELES_QUE_CAMBIAN, RiskLevel, Tool, ToolCategory

logger = logging.getLogger(__name__)

#: Lo que solo trae datos para el modelo (4.0-D). En un plan, antes de un cambio, no sirve:
#: el plan aprobado se ejecuta tal cual (4.0-A) y lo que lee un paso no llega a los
#: siguientes. Medido con el modelo real: con una lista adjunta, planificó «leer el
#: adjunto» y luego `create_file` con el contenido `<CONTENIDO>`, que se escribió tal cual.
def es_consulta(herramienta: str | None, argumentos: dict | None = None) -> bool:
    """Si un paso lee algo para contestar: una de `CONSULTAS`, o una herramienta con
    `accion=listar` (las ventanas, las aplicaciones: 4.8 y 4.11)."""
    return herramienta in CONSULTAS or (isinstance(argumentos, dict) and argumentos.get("accion") == "listar")


CONSULTAS = frozenset({
    "read_file", "read_upload", "analyze_image", "transcribe_audio", "list_uploads",
    "search_knowledge", "list_knowledge_sources", "recall_memory",
    "list_files", "search_files", "search_code", "inspect_project",
    "read_webpage", "search_web",
    "git_status", "git_diff", "github_leer_archivo", "github_listar_issues",
    "github_listar_prs", "github_listar_repos",
    "get_processes", "system_info", "get_environment", "run_command", "calendario_ver_eventos",
    # Los metadatos y el PC por dentro (4.6, 4.7): también se leen para contestar.
    "file_info", "pc_diagnostics",
    # Los proyectos y los editores (4.13): se miran antes de abrir uno.
    "pc_context",
    # Los controles de una ventana (4.12): se leen antes de un plan que los maneja.
    "ui_read",
    # La verificación ya es automática tras cada cambio; dentro de un plan, «comprobar el
    # paso 1» antes del 3 no le llega al 3 (medido en la 4.0.5).
    "verify_step",
})


class _HerramientaDePlanes(Tool):
    def __init__(self, planificador):
        self.planificador = planificador

    @property
    def category(self) -> str:
        return ToolCategory.GENERAL.value

    @property
    def requires_local(self) -> bool:
        return False

    @property
    def permission_level(self) -> str:
        # Planificar no ejecuta nada. Pedir permiso para pensar seria absurdo, y
        # ademas contraproducente: haria que el modelo evitara planificar.
        return RiskLevel.SAFE.value

    @staticmethod
    def _ok(datos: Any) -> dict:
        return {"success": True, "data": datos, "error": None}

    @staticmethod
    def _error(mensaje: str) -> dict:
        return {"success": False, "data": None, "error": mensaje}


class CreatePlanTool(_HerramientaDePlanes):
    """Propone un plan y lo somete a aprobación si hace falta."""

    def _consultas_antes_de_un_cambio(self, pasos: list) -> list[str]:
        """Ver abajo. Desde la 4.11, «cambio» es **cualquier acción** (también una verde,
        como mover una ventana): los argumentos de un plan se fijan al crearlo, así que
        ninguna acción puede esperar un dato que dé un paso anterior. Medido con el modelo
        real: «pon Word y Firefox lado a lado» planeó listar las ventanas y luego moverlas
        sin sus ids, y el segundo paso falló."""
        return self._consultas_antes_de_una_accion(pasos)

    def _consultas_antes_de_una_accion(self, pasos: list) -> list[str]:
        """Las consultas que van antes de algún cambio en este plan (4.0-D), o [].

        Un plan solo de consultas, o una consulta después de los cambios, no se toca: no
        dejan un cambio esperando un dato que nunca le llegará."""
        registro = self.planificador.tool_registry
        if registro is None:
            return []
        vistas: list[str] = []
        for paso in pasos:
            if not isinstance(paso, dict) or not paso.get("herramienta"):
                continue
            nombre = paso["herramienta"]
            if es_consulta(nombre, paso.get("argumentos")):
                vistas.append(nombre)
                continue
            if registro.get(nombre) is not None and vistas:
                return sorted(set(vistas))
        return []

    @property
    def name(self) -> str:
        return "create_plan"

    @property
    def description(self) -> str:
        return (
            "Antes de cambiar algo, llama aquí (no lo escribas en tu respuesta): cada "
            "paso con su herramienta y sus argumentos exactos. Lo delicado lo aprueba "
            "la persona. Para consultar, no hace falta."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "objetivo": {
                    "type": "string",
                    "description": "En una frase",
                },
                "pasos": {
                    "type": "array",
                    "description": "En orden",
                    # Los campos opcionales de un paso admiten `null` (2.3-D).
                    #
                    # Medido con el modelo real: en 4 de 5 planes, un paso sin
                    # herramienta —«preguntar a la persona»— llegaba con
                    # `"herramienta": null`. Groq valida el esquema en su lado y
                    # con `"type": "string"` rechazaba la llamada ENTERA con un
                    # 400; el turno caía al respaldo y tardaba 24-35 s, o fallaba.
                    # El planificador ya trataba un nulo como «sin herramienta»:
                    # era solo el esquema el que lo prohibía.
                    "items": {
                        "type": "object",
                        "properties": {
                            "descripcion": {
                                "type": "string",
                                "description": "En lenguaje llano",
                            },
                            "herramienta": {
                                "type": ["string", "null"],
                                "description": "Nombre exacto de la herramienta que usarás, "
                                               "o null si el paso no usa ninguna (por ejemplo, "
                                               "preguntar algo a la persona)",
                            },
                            "argumentos": {
                                "type": ["object", "null"],
                                "description": "Exactos",
                            },
                            "depende_de": {
                                "type": ["array", "null"],
                                "items": {"type": "integer"},
                                "description": "Pasos que deben ir antes",
                            },
                            "motivo": {
                                "type": ["string", "null"],
                                "description": "Por qué",
                            },
                        },
                        "required": ["descripcion"],
                    },
                },
                # Sin `session_id`: el modelo no la conoce y la pone el agente.
                # Ver `Agent._ATADAS_A_LA_CONVERSACION`.
            },
            "required": ["objetivo", "pasos"],
        }

    def execute(self, objetivo: str = "", pasos: list | None = None, **kwargs) -> dict:
        # Lo que se sabe imposible no se propone (4.12, `prevalidar`): la persona aprobaría
        # algo que el PC va a rechazar.
        registro = self.planificador.tool_registry
        for paso in pasos or []:
            if not isinstance(paso, dict) or registro is None:
                continue
            herramienta = registro.get(paso.get("herramienta") or "")
            prevalidar = getattr(herramienta, "prevalidar", None)
            motivo = prevalidar(paso.get("argumentos") or {}) if prevalidar else None
            if motivo:
                return self._error(f"No guardé el plan: {motivo}")
        antes = self._consultas_antes_de_un_cambio(pasos or [])
        if antes:
            return self._error(
                f"No guardé el plan: {', '.join(antes)} va antes de un cambio, y un plan "
                "aprobado se ejecuta tal cual: lo que lee un paso NO llega a los siguientes. "
                "Haz esas consultas ahora (no necesitan plan) y propón el plan solo con los "
                "cambios, con el contenido exacto ya escrito, sin marcadores."
            )
        try:
            plan = self.planificador.crear(
                objetivo=objetivo,
                pasos=pasos or [],
                session_id=kwargs.get("session_id"),
                task_id=kwargs.get("task_id"),
            )
        except ValueError as exc:
            # Un plan mal formado es un error del modelo, no del sistema:
            # devolverlo como resultado le permite corregirse.
            return self._error(str(exc))

        datos = plan.to_dict()

        # Pasos con herramientas que aquí no existen (2.3-D). Medido con el modelo
        # real en la nube: pedido un plan para ordenar apuntes en carpetas, propuso
        # `list_files`, `make_directory` y `move_file`, que en la nube no están.
        # El plan sigue siendo seguro —una herramienta desconocida cuenta como
        # crítica y pide aprobación—, pero la persona acababa aprobando pasos que
        # nadie puede ejecutar. Se le dice al modelo para que lo corrija.
        registro = self.planificador.tool_registry
        inexistentes = sorted({
            paso.herramienta for paso in plan.pasos
            if paso.herramienta and registro is not None and registro.get(paso.herramienta) is None
        })
        aviso = ""
        if inexistentes:
            datos["herramientas_que_no_existen"] = inexistentes
            aviso = (
                f"ATENCIÓN: aquí no existen estas herramientas: {', '.join(inexistentes)}. "
                "Nadie podrá ejecutar esos pasos. Rehaz el plan solo con las herramientas "
                "que tienes, o explícale al usuario que eso no se puede hacer desde aquí. "
            )

        # El mensaje importa tanto como los datos: es lo que el modelo lee para
        # decidir si sigue o se detiene. Tiene que ser inequivoco.
        if plan.necesita_aprobacion:
            datos["siguiente_paso"] = (
                "NO EJECUTES NADA todavía. Este plan toca algo delicado "
                f"(riesgo {plan.riesgo}) y el usuario debe aprobarlo. Dile qué vas "
                "a hacer y espera su respuesta."
            )
        else:
            datos["siguiente_paso"] = (
                "El plan no necesita aprobación: puedes ejecutarlo ya, paso a paso."
            )
        datos["siguiente_paso"] = aviso + datos["siguiente_paso"]

        return self._ok(datos)


class GetPlanTool(_HerramientaDePlanes):
    """Consulta un plan, sobre todo para saber si ya se aprobó."""

    @property
    def name(self) -> str:
        return "get_plan"

    @property
    def description(self) -> str:
        return (
            "Consulta el estado de un plan. Úsalo para saber si el usuario ya "
            "aprobó uno que estaba pendiente antes de empezar a ejecutarlo."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Identificador del plan"},
            },
            "required": ["plan_id"],
        }

    def execute(self, plan_id: str = "", **kwargs) -> dict:
        plan = self.planificador.obtener(plan_id)

        if plan is None:
            return self._error(f"No existe ningún plan con el identificador '{plan_id}'.")

        datos = plan.to_dict()
        datos["siguiente_paso"] = _que_hacer_con(plan)
        return self._ok(datos)


def _que_hacer_con(plan) -> str:
    """Qué debe hacer el modelo con un plan, según cómo esté.

    Se le dice explícitamente en lugar de dejar que lo deduzca del estado: la
    diferencia entre «pendiente» y «rechazado» es obvia para una persona y no
    tanto para un modelo que ha visto quince estados distintos en su contexto.
    """
    from src.tasks.plan import EstadoPlan

    mensajes = {
        EstadoPlan.PENDIENTE.value: (
            "Sigue pendiente de aprobación. NO ejecutes nada; pregunta al usuario."
        ),
        EstadoPlan.APROBADO.value: (
            "Aprobado. Puedes ejecutarlo, siguiendo los pasos en orden."
        ),
        EstadoPlan.RECHAZADO.value: (
            "El usuario lo RECHAZÓ. No lo ejecutes ni propongas uno equivalente: "
            "pregúntale qué prefiere en su lugar."
        ),
        EstadoPlan.EJECUTANDO.value: "Ya se está ejecutando.",
        EstadoPlan.COMPLETADO.value: "Ya se completó.",
        EstadoPlan.FALLIDO.value: "Falló. Revisa qué pasó antes de reintentar.",
    }
    return mensajes.get(plan.estado, "Estado desconocido; no ejecutes nada.")


class ListPlansTool(_HerramientaDePlanes):
    @property
    def name(self) -> str:
        return "list_plans"

    @property
    def description(self) -> str:
        return "Lista los planes recientes de esta conversación, con su estado."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                # Sin `session_id`, por lo mismo que en create_plan: la pone el
                # agente, y así «de esta conversación» es verdad.
                "solo_pendientes": {
                    "type": "boolean",
                    "description": "Solo los que esperan aprobación",
                },
            },
        }

    def execute(self, **kwargs) -> dict:
        from src.tasks.plan import EstadoPlan

        estados = (
            (EstadoPlan.PENDIENTE.value,) if kwargs.get("solo_pendientes") else None
        )
        planes = self.planificador.listar(
            session_id=kwargs.get("session_id"), estados=estados, limite=20
        )

        return self._ok({
            "total": len(planes),
            "planes": [
                {
                    "id": p.id,
                    "objetivo": p.objetivo,
                    "estado": p.estado,
                    "riesgo": p.riesgo,
                    "pasos": len(p.pasos),
                }
                for p in planes
            ],
        })


def plan_tools(planificador) -> list[Tool]:
    """Las herramientas de planificación, listas para registrar."""
    return [
        CreatePlanTool(planificador),
        GetPlanTool(planificador),
        ListPlansTool(planificador),
    ]
