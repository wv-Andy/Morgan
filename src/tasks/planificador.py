"""
El planificador: arma planes, los somete a aprobación y los ejecuta (V1.6).

Es la pieza que separa **decidir qué hacer** de **hacerlo**. Su trabajo es
pequeño a propósito:

1. Recibe un objetivo y unos pasos propuestos por el modelo.
2. Le pone a cada paso el riesgo real de su herramienta, **no el que diga el
   modelo**.
3. Decide si el plan puede ejecutarse ya o necesita que alguien lo apruebe.
4. Guarda el plan, para que sobreviva a un reinicio y se pueda decidir después.

## Por qué el riesgo no lo pone el modelo

Es la decisión que sostiene todo lo demás. Si el nivel de riesgo viniera en la
propuesta, bastaría con que el modelo escribiera `"riesgo": "safe"` junto a un
`delete_file` para saltarse la aprobación — por error o porque algo en el
contexto se lo sugirió. El riesgo sale del **registro de herramientas**, que es
donde está declarado desde la V0.4 y donde no llega nada de lo que el modelo diga.

Lo que el modelo aporta es el plan: qué pasos, en qué orden y para qué. Cuánto
cuesta cada uno lo dice Morgan.
"""

import json
import logging
import secrets
import time
from dataclasses import dataclass, field

from src.identidad import usuario_actual
from src.tasks.plan import (
    GRAVEDAD,
    UMBRAL_CONFIRMACION,
    EstadoPlan,
    PasoPlaneado,
    Plan,
    exigir_transicion,
)
from src.tools.base import RiskLevel

logger = logging.getLogger(__name__)

# Un plan con más pasos que esto casi nunca es un plan: es el modelo enumerando.
# Cortarlo aquí evita que una propuesta desbocada llene la base y la pantalla.
MAX_PASOS = 20


def _canonico(argumentos: dict | None) -> str:
    """Los argumentos en una forma comparable: mismas claves y valores, mismo texto."""
    return json.dumps(argumentos or {}, sort_keys=True, ensure_ascii=False, default=str)


#: Lo que los modelos escriben, como texto, cuando un paso no usa herramienta.
SIN_HERRAMIENTA = frozenset({"none", "null", "ninguna", "ninguno", "n/a", "-"})


@dataclass
class Autorizacion:
    """El resultado de buscar un paso aprobado para una llamada.

    `motivo` es `None` cuando hay autorización; si no, uno de `sin_plan`,
    `pendiente` u `otros_argumentos`.
    """

    plan: Plan | None = None
    paso: PasoPlaneado | None = None
    motivo: str | None = None
    aprobados: list[dict] = field(default_factory=list)

    @property
    def concedida(self) -> bool:
        return self.motivo is None and self.paso is not None


class PlanNoAprobado(RuntimeError):
    """Se intentó ejecutar un plan que todavía espera aprobación."""

    def __init__(self, plan_id: str):
        super().__init__(
            f"El plan {plan_id} necesita aprobación antes de ejecutarse."
        )


class Planificador:
    """Crea, aprueba y consulta planes."""

    def __init__(self, repositorio, tool_registry=None, auditor=None):
        self.repositorio = repositorio
        self.tool_registry = tool_registry
        self.auditor = auditor

    # --- Riesgo --------------------------------------------------------------

    def riesgo_de(self, herramienta: str | None) -> str:
        """El riesgo declarado de una herramienta.

        Una herramienta que no existe se trata como **crítica**, no como segura.
        Es fail-safe: si el modelo inventa un nombre, lo peor que puede pasar es
        que se pida una aprobación de más; al revés, se ejecutaría sin preguntar
        algo que nadie ha clasificado.
        """
        if not herramienta or self.tool_registry is None:
            return RiskLevel.CRITICAL.value

        tool = self.tool_registry.get(herramienta)
        if tool is None:
            logger.warning(
                "El plan menciona una herramienta que no existe: %s", herramienta
            )
            return RiskLevel.CRITICAL.value

        riesgo = str(getattr(tool, "risk_level", RiskLevel.MODERATE.value))
        # Una herramienta que exige plan nunca cuenta como segura. Un plan cuyos
        # pasos son todos seguros NACE APROBADO, así que declararla `safe` haría
        # que su aprobación se diera sola: exactamente lo contrario de lo que
        # `exige_plan` existe para garantizar.
        if getattr(tool, "exige_plan", False) and GRAVEDAD.get(riesgo, 0) < UMBRAL_CONFIRMACION:
            return RiskLevel.MODERATE.value
        return riesgo

    # --- Autorización por plan (V2.0.16) --------------------------------------

    def buscar_paso_aprobado(
        self, session_id: str | None, herramienta: str, argumentos: dict
    ) -> "Autorizacion":
        """Si hay un paso aprobado y sin ejecutar para esta llamada exacta.

        Busca **solo en esta conversación**: un plan aprobado en otra no autoriza
        nada aquí. Y compara los argumentos **enteros y normalizados**: aprobar
        «crear el evento de las 10» no autoriza a crear el de las 11.

        Nunca lanza. Devuelve siempre una `Autorizacion` con el paso, o con el
        motivo por el que no lo hay, que es lo que se le dice al modelo para que
        pueda proponer el plan correcto.
        """
        if not session_id:
            return Autorizacion(motivo="sin_plan")

        buscado = _canonico(argumentos)
        pendiente = None
        con_otros: list[dict] = []
        try:
            planes = self.listar(session_id=session_id, limite=50)
        except Exception:
            logger.warning("No se pudieron leer los planes de la conversación", exc_info=True)
            return Autorizacion(motivo="sin_plan")

        for plan in planes:
            for paso in sorted(plan.pasos, key=lambda p: p.orden):
                if paso.herramienta != herramienta or paso.ejecutado:
                    continue
                if plan.estado in (EstadoPlan.APROBADO.value, EstadoPlan.EJECUTANDO.value):
                    if _canonico(paso.argumentos) == buscado:
                        return Autorizacion(plan=plan, paso=paso)
                    con_otros.append(paso.argumentos)
                elif plan.estado == EstadoPlan.PENDIENTE.value and _canonico(paso.argumentos) == buscado:
                    pendiente = plan

        if pendiente is not None:
            return Autorizacion(motivo="pendiente", plan=pendiente)
        if con_otros:
            return Autorizacion(motivo="otros_argumentos", aprobados=con_otros)
        return Autorizacion(motivo="sin_plan")

    def marcar_paso_ejecutado(self, plan_id: str, orden: int) -> Plan:
        """Consume la aprobación de un paso, y cierra el plan si ya no queda nada.

        Se llama **después** de que la herramienta funcione. Si fallara, el paso
        sigue aprobado y se puede reintentar: la persona aprobó el efecto, y el
        efecto no ha ocurrido.

        Dos turnos no pueden consumir el mismo paso a la vez: los planes son de
        una conversación, y los turnos de una conversación se atienden en serie
        con su cerrojo (`SessionStore`).
        """
        plan = self._exigir(plan_id)
        for paso in plan.pasos:
            if paso.orden == orden:
                paso.ejecutado = True

        if plan.estado == EstadoPlan.APROBADO.value:
            plan.estado = EstadoPlan.EJECUTANDO.value
        if all(p.ejecutado for p in plan.pasos if p.necesita_confirmacion):
            plan.estado = EstadoPlan.COMPLETADO.value

        self.repositorio.update(plan)
        self._auditar(plan, "plan.paso_ejecutado", f"paso {orden}")
        return plan

    # --- Creación ------------------------------------------------------------

    def crear(
        self,
        objetivo: str,
        pasos: list[dict],
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> Plan:
        """Arma un plan a partir de lo que propone el modelo."""
        if not (objetivo or "").strip():
            raise ValueError("Un plan necesita un objetivo.")

        if not pasos:
            raise ValueError("Un plan necesita al menos un paso.")

        if len(pasos) > MAX_PASOS:
            raise ValueError(
                f"Un plan no puede tener más de {MAX_PASOS} pasos. "
                "Divide el trabajo en varios."
            )

        planeados: list[PasoPlaneado] = []
        for indice, crudo in enumerate(pasos, start=1):
            herramienta = (crudo.get("herramienta") or "").strip() or None
            # «none» escrito como texto es «sin herramienta» (4.1). Medido con el modelo real:
            # un paso «none» delante de `append_file` contó como herramienta inexistente, el
            # plan aprobado se paró en él (4.0.5) y la línea no se añadió.
            if herramienta and herramienta.lower() in SIN_HERRAMIENTA:
                herramienta = None
            planeados.append(PasoPlaneado(
                orden=int(crudo.get("orden") or indice),
                descripcion=(crudo.get("descripcion") or "").strip(),
                herramienta=herramienta,
                argumentos=crudo.get("argumentos") or {},
                # El riesgo se pone AQUI, no se lee de la propuesta. Si viniera
                # del modelo, bastaria con escribir "safe" junto a un borrado
                # para saltarse la aprobacion.
                riesgo=self.riesgo_de(herramienta),
                depende_de=[int(d) for d in (crudo.get("depende_de") or [])],
                motivo=(crudo.get("motivo") or "").strip() or None,
            ))

        plan = Plan(
            id=f"plan-{secrets.token_urlsafe(8)}",
            objetivo=objetivo.strip(),
            pasos=planeados,
            session_id=session_id,
            task_id=task_id,
            creado_en=time.time(),
        )

        plan.estado = (
            EstadoPlan.PENDIENTE.value if plan.necesita_aprobacion
            else EstadoPlan.APROBADO.value
        )

        self.repositorio.create(plan)

        logger.info(
            "Plan %s creado: %d pasos, riesgo %s, %s",
            plan.id, len(plan.pasos), plan.riesgo,
            "pendiente de aprobación" if plan.necesita_aprobacion else "ejecutable",
        )
        return plan

    # --- Consulta ------------------------------------------------------------

    def obtener(self, plan_id: str) -> Plan | None:
        return self.repositorio.get(plan_id)

    def listar(
        self,
        session_id: str | None = None,
        estados: tuple[str, ...] | None = None,
        limite: int = 50,
    ) -> list[Plan]:
        return self.repositorio.list(
            session_id=session_id, estados=estados, limit=limite
        )

    # --- Decisión ------------------------------------------------------------

    def aprobar(self, plan_id: str, quien: str | None = None) -> Plan:
        plan = self._exigir(plan_id)
        exigir_transicion(plan.estado, EstadoPlan.APROBADO.value)

        plan.estado = EstadoPlan.APROBADO.value
        plan.decidido_en = time.time()
        plan.decidido_por = quien or usuario_actual()
        self.repositorio.update(plan)

        self._auditar(plan, "plan.aprobado")
        return plan

    def rechazar(self, plan_id: str, motivo: str | None = None, quien: str | None = None) -> Plan:
        """Rechaza un plan. **No deja nada a medias**, porque nada se ejecutó."""
        plan = self._exigir(plan_id)
        exigir_transicion(plan.estado, EstadoPlan.RECHAZADO.value)

        plan.estado = EstadoPlan.RECHAZADO.value
        plan.decidido_en = time.time()
        plan.decidido_por = quien or usuario_actual()
        plan.motivo_rechazo = (motivo or "").strip()[:300] or None
        self.repositorio.update(plan)

        self._auditar(plan, "plan.rechazado", plan.motivo_rechazo)
        return plan

    # --- Ejecución -----------------------------------------------------------

    def marcar_ejecutando(self, plan_id: str) -> Plan:
        plan = self._exigir(plan_id)

        if not plan.ejecutable:
            raise PlanNoAprobado(plan_id)

        exigir_transicion(plan.estado, EstadoPlan.EJECUTANDO.value)
        plan.estado = EstadoPlan.EJECUTANDO.value
        self.repositorio.update(plan)
        return plan

    def cerrar(self, plan_id: str, exito: bool) -> Plan:
        plan = self._exigir(plan_id)
        destino = (
            EstadoPlan.COMPLETADO.value if exito else EstadoPlan.FALLIDO.value
        )
        exigir_transicion(plan.estado, destino)

        plan.estado = destino
        self.repositorio.update(plan)
        return plan

    # --- Interno -------------------------------------------------------------

    def _exigir(self, plan_id: str) -> Plan:
        plan = self.repositorio.get(plan_id)
        if plan is None:
            raise KeyError(f"No existe el plan {plan_id}")
        return plan

    def _auditar(self, plan: Plan, accion: str, detalle: str | None = None) -> None:
        """Deja constancia de la decisión.

        Aprobar un plan arriesgado es la clase de acción que conviene poder
        reconstruir: quién dijo que sí, cuándo y a qué.
        """
        if self.auditor is None:
            return

        try:
            self.auditor.registrar_evento(
                accion=accion,
                actor=plan.decidido_por or usuario_actual(),
                objetivo=plan.id,
                detalle=detalle or f"riesgo {plan.riesgo}: {plan.objetivo[:120]}",
            )
        except Exception:
            logger.warning("No se pudo auditar la decisión sobre el plan", exc_info=True)
