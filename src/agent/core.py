"""
Agente central de Morgan (V0.4).

Orquesta el Agent Loop multi-turno con soporte para múltiples proveedores LLM,
validación de argumentos, permisos contextuales, detección de bucles y eventos.
"""

import json
from dataclasses import dataclass, field
import logging
import re
import sys
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from typing import Any
from rich.console import Console

from src.eventos_turno import emitir
from src.automatizacion.contexto import NO_PERMITIDA, automatizacion_actual, permitida
from src.observabilidad import anotar, etapa, medicion_actual
from src.config import get_settings
from src.agent import fuga
from src.agent.prompt import PARRAFO_SIN_ACCESO, SYSTEM_PROMPT, prompt_para
from src.agent.events import AgentEventHandler
from src.agent.sessions import SessionStore, ConversationSession, DEFAULT_SESSION_ID
from src.agent.history import ConversationHistory, build_title
from src.models.base import LLMProvider, ChatMessage, LLMResponse
from src.models.errors import log_llm_error
from src.security.permissions import PermissionManager
from src.identidad.permiso_automatico import entra as permiso_entra
from src.tools.base import NIVELES_QUE_CAMBIAN
from src.tools.registry import ToolRegistry
from src.tools.validation import validate_tool_args

logger = logging.getLogger(__name__)
console = Console()


_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _linea_de_fecha(ahora=None) -> str:
    """La fecha y la hora actuales, para el prompt de cada turno.

    Con el día de la semana escrito: «el jueves» no se puede resolver sin él, y un
    modelo calcula mal el día de la semana a partir de una fecha.
    """
    from datetime import datetime, timezone

    ahora = ahora or datetime.now(timezone.utc)
    return (
        f"Fecha y hora actuales (UTC): {_DIAS[ahora.weekday()]} "
        f"{ahora:%Y-%m-%d %H:%M}. Para horas en la zona de la persona, usa la de su "
        "calendario si lo tiene conectado."
    )


@dataclass
class _EstadoDelTurno:
    """Lo que un turno recuerda entre una llamada y la siguiente."""

    #: Llamadas que ya fallaron en este turno: herramienta + argumentos exactos.
    #: Repetirlas da el mismo error y gasta las vueltas que quedan.
    fallidas: dict = field(default_factory=dict)
    #: Si este turno ya leyó algo del PC (3.1-B).
    leyo_del_pc: bool = False
    #: Corregir, con tope (4.0-C, decisión mía): tras un fallo de algo que cambia cosas,
    #: la siguiente llamada que cambia cosas es **el** intento con otro camino; si también
    #: falla, no se intenta ningún otro cambio en este turno.
    correccion_pendiente: bool = False
    sin_mas_intentos: bool = False
    urls_conocidas: set = field(default_factory=set)
    #: El plan aprobado de este turno ya se ejecutó entero y bien (4.0-D). Medido con el
    #: modelo real: tras el informe, a veces proponía otro plan para lo mismo, que quedaba
    #: pendiente y, aprobado, repetía el cambio (una segunda línea en un registro).
    plan_hecho: bool = False
    #: `create_plan` en este turno: cuántas veces se rechazó y si alguna salió (4.1).
    planes_rechazados: int = 0
    plan_creado: bool = False
    #: El resumen de un plan aprobado que salió entero (4.1.5, decisión mía): lo
    #: escribe el núcleo y el turno no llama al modelo. Ver `_ejecutar_plan`.
    resumen_del_plan: str | None = None
    #: Un plan que `create_plan` acaba de dejar pendiente de aprobar, sin avisos (4.1.5):
    #: el turno lo cierra el núcleo. Ver `_texto_de_propuesta`.
    plan_propuesto: dict | None = None
    #: Si en este turno falló algo que no sea un `create_plan` rechazado (4.2). Entonces el
    #: turno no lo cierra la plantilla: el modelo tiene que contar qué falló.
    fallo_algo: bool = False
    #: Si hay alguna tarea o plan abierto, calculado una vez por turno (4.3) y otra solo
    #: tras una herramienta de `_ABREN_O_CIERRAN`. Antes eran dos consultas a la base en
    #: **cada** vuelta del modelo.
    algo_abierto: bool | None = None
    #: Si ya se le recordó al modelo, en este turno, que un plan se crea con `create_plan`
    #: y no escribiéndolo (4.3). Una vez; si vuelve a escribirlo, se dice que no hay plan.
    plan_recordado: bool = False
    #: La persona no permitió en su PC algo de este turno (4.4). Es su decisión: ni otro
    #: camino ni otro plan en este turno.
    no_confirmado: bool = False
    #: Las herramientas que ya se le ofrecieron al modelo en este turno (4.5): se siguen
    #: ofreciendo aunque dejen de estar disponibles a mitad. Ver `Agent._schemas_for`.
    ofrecidas: set = field(default_factory=set)
    #: Si este turno tiene el permiso automático (4.6): lo verde y amarillo que exige plan
    #: se hace sin esperar al botón. Se mira una vez, al empezar.
    automatico: bool = False
    #: Para distinguir en el PC dos «añade X» de turnos distintos, sin plan (4.6).
    id_turno: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


#: Los niveles de riesgo de lo que cambia algo (4.0-C). Leer y consultar no cuentan: sirven
#: para averiguar qué pasó antes del intento.
_CAMBIAN = NIVELES_QUE_CAMBIAN
UN_INTENTO = ("Tienes UN intento con otro camino (nunca lo mismo que falló). Si tampoco sale, "
              "díselo a la persona con lo que intentaste.")
YA_PROBASTE = ("Era tu intento con otro camino y tampoco salió: no intentes más cambios en esta "
               "respuesta; dile a la persona qué intentaste y qué falló.")
PLAN_YA_HECHO = ("No se guardó: el plan aprobado ya se ejecutó y salió bien. No propongas otro para "
                 "lo mismo; cuéntale el resultado a la persona. Si quiere algo más, te lo pedirá.")
#: Una respuesta que dice haber propuesto un plan (4.3). Medido con el modelo real: a partir
#: del tercer turno de una conversación, el modelo **imitaba** la plantilla de la 4.2 («Te
#: propongo este plan: …» con sus pasos) sin llamar a `create_plan`, y no había nada que
#: aprobar. En el historial solo veía ese texto: la compactación (4.1.5) quita las llamadas.
_PARECE_UN_PLAN = re.compile(
    r"(te propongo|he (creado|preparado|propuesto|elaborado))\s+(este|el|un|una propuesta de)\s+plan",
    re.IGNORECASE)
RECUERDA_CREATE_PLAN = ("[Nota de Morgan, no de la persona: no has creado ningún plan, solo lo has escrito. "
                        "No lo escribas en tu respuesta: llama a create_plan con cada paso, su herramienta "
                        "y sus argumentos exactos.]")
NOTA_SIN_PLAN = ("\n\n_(Nota de Morgan: el plan no llegó a guardarse, así que no hay nada que "
                 "aprobar todavía. Si quieres, pídemelo de nuevo.)_")
NO_CONFIRMADO = ("La persona no lo permitió en su PC (o no contestó a tiempo): es su decisión. No lo "
                 "intentes de otra forma ni vuelvas a proponerlo; díselo, y si quiere, que lo pida otra vez.")
SIN_MAS_INTENTOS = ("No se ejecutó: ya probaste otro camino y tampoco salió. Dile a la persona qué "
                    "intentaste y qué falló; si quiere, lo intentará de otra forma en su próximo mensaje.")


def texto_con_adjuntos(mensaje: str, adjuntos: list[dict] | None) -> str:
    """El mensaje con la lista de archivos que lo acompañan, **para el modelo**.

    Lo necesita en el texto para saber que existen y con qué identificador pedirlos. El
    vínculo autoritativo vive en los metadatos del mensaje. **No se guarda**: en la base
    queda lo que escribió la persona (4.3; antes, al recargar, veía esta lista con los
    identificadores dentro de su propio mensaje)."""
    if not adjuntos:
        return mensaje
    lineas = [f'- {a["familia"]} "{a["nombre"]}" (id: {a["id"]})' for a in adjuntos]
    return "Archivos adjuntos a este mensaje:\n" + "\n".join(lineas) + f"\n\n{mensaje}"


def _es_consulta(paso) -> bool:
    """Si un paso de plan lee algo que el modelo tiene que ver para contestar (4.8)."""
    from src.tools.planificacion import es_consulta

    return es_consulta(getattr(paso, "herramienta", None), getattr(paso, "argumentos", None))


def _texto_de_propuesta(plan: dict) -> str:
    """Lo que se le dice a la persona al proponerle un plan (4.2).

    Con sus pasos, en una línea cada uno: no cuesta ni un token de modelo, y hace falta
    donde el plan no se ve al lado (la terminal no tiene panel de planes; en el móvil, el
    de arriba puede quedar fuera de la pantalla). Hasta 8; el resto, contado."""
    objetivo = str(plan.get("objetivo") or "").strip().rstrip(".") or "el cambio que pediste"
    pasos = [p or {} for p in plan.get("pasos") or []]
    lineas = [f"{i}. {str(p.get('descripcion') or p.get('herramienta') or '').strip()[:120]}"
              for i, p in enumerate(pasos[:8], start=1)]
    if len(pasos) > 8:
        lineas.append(f"… y {len(pasos) - 8} más.")
    texto = f"Te propongo este plan: «{objetivo}»."
    if lineas:
        texto += "\n\n" + "\n".join(lineas) + "\n\n"
    else:
        texto += " "
    texto += "Revísalo y apruébalo si te parece bien."
    if any(p.get("herramienta") in _SE_CONFIRMAN_EN_EL_PC for p in pasos):
        texto += " Al aprobarlo, tu PC te pedirá confirmar lo que borra o ejecuta."
    return texto


#: Lo que el agente, por defecto, confirma además en el PC con una notificación (3.3, 3.5).
_SE_CONFIRMAN_EN_EL_PC = frozenset({"delete_file", "run_change_command", "kill_process"})


class Agent:
    """Agente central que coordina el ciclo de razonamiento y ejecución de herramientas."""

    def __init__(
        self,
        model: LLMProvider,
        tool_registry: ToolRegistry,
        permission_manager: PermissionManager,
        max_iterations: int | None = None,
        tasks=None,
        event_handler: AgentEventHandler | None = None,
        sessions: SessionStore | None = None,
        conversation_history: ConversationHistory | None = None,
        verificador=None,
        planes=None,
    ):
        self.model = model
        self.tools = tool_registry
        self.permissions = permission_manager
        settings = get_settings()
        # Gestor de tareas, opcional: sin el, Morgan funciona igual pero no lleva
        # cuenta de lo que hace.
        self.tasks = tasks
        # Comprueba que el efecto de una herramienta ocurrio de verdad (V1.7).
        # Se construye por defecto porque no depende de nada: mira el sistema de
        # archivos y la salida, no la red ni la base. Y sin el, Morgan puede
        # afirmar que hizo algo que no hizo.
        if verificador is None:
            from src.tasks.verificacion import Verificador

            verificador = Verificador()
        self.verificador = verificador
        self.max_iterations = (
            max_iterations if max_iterations is not None else settings.max_iterations
        )
        # Tope de reloj para el turno completo. El límite de iteraciones por sí
        # solo no acota el tiempo: seis iteraciones con reintentos y conmutación
        # de proveedor podían bloquear una petición más de 15 minutos.
        self.turn_timeout = settings.turn_timeout
        self.event_handler = event_handler or AgentEventHandler()
        # 'is not None' y no 'or': SessionStore define __len__, de modo que un almacén
        # recién creado (y por tanto vacío) es falsy y se descartaba silenciosamente.
        self.sessions = sessions if sessions is not None else SessionStore()
        # Sin historial persistente el agente funciona igual, solo que la
        # conversación se pierde al reiniciar el servidor.
        # Ojo: no puede llamarse 'history', que ya es una propiedad publica.
        self.conversation_history = conversation_history
        # El planificador, para saber si esta conversación tiene planes. Sin él
        # `_hay_algo_abierto` no veía ninguno, y el recorte del catálogo quitaba
        # `get_plan` justo después de crear un plan que esperaba aprobación.
        # Ver `_ATADAS_A_LA_CONVERSACION`.
        self.planes = planes
        self.system_prompt = self._prompt_para_el_catalogo()
        #: La memoria de **quien pregunta**, calculada en cada turno (4.0). Antes vivía en el
        #: prompt compartido (`set_memory_context`) y, con varias cuentas, **la memoria de
        #: una persona iba en los turnos de otra**: se sustituía por la de quien guardó un
        #: recuerdo o sus ajustes la última vez. Reproducido con dos cuentas antes de
        #: arreglarlo. Una función, llamada dentro del turno (con su persona en el contexto).
        self.memoria = None
        #: Los títulos del conocimiento de quien pregunta, por turno (4.1). Ver
        #: `src/tools/conocimiento.py::indice_para_el_turno`.
        self.indice_conocimiento = None
        #: Qué decir si la persona tiene un PC emparejado y ahora no está conectado (4.3).
        #: Ver `src/identidad/agentes.py::aviso_si_esta_desconectado`.
        self.aviso_del_equipo = None
        #: Si quien pregunta dio el permiso automático (4.6, pedido por mí): una función
        #: sin argumentos que dice sí o no para el turno en curso. Ver
        #: `src/identidad/permiso_automatico.py`.
        self.permiso_automatico = None

    @property
    def messages(self) -> list[ChatMessage]:
        """Historial de la sesión por defecto (vista compatible con el uso desde la CLI)."""
        return self.sessions.get(DEFAULT_SESSION_ID).messages

    @property
    def history(self) -> list[dict]:
        """Proporciona una vista compatible hacia atrás del historial de mensajes."""
        hist = []
        for m in self.messages:
            if m.role in ("user", "model"):
                hist.append({"role": m.role, "content": m.content})
        return hist

    def chat(
        self,
        user_message: str,
        session_id: str | None = None,
        temporary: bool = False,
        attachments: list[dict] | None = None,
        contexto_espacio: str | None = None,
        tope_turno: int | None = None,
        ejecutar_plan: str | None = None,
    ) -> str:
        return self.process(
            user_message,
            session_id=session_id,
            temporary=temporary,
            attachments=attachments,
            contexto_espacio=contexto_espacio,
            tope_turno=tope_turno,
            ejecutar_plan=ejecutar_plan,
        )

    def process(
        self,
        user_message: str,
        session_id: str | None = None,
        temporary: bool = False,
        attachments: list[dict] | None = None,
        contexto_espacio: str | None = None,
        tope_turno: int | None = None,
        ejecutar_plan: str | None = None,
    ) -> str:
        """
        Procesa el mensaje del usuario ejecutando el bucle multi-turno del agente.

        `ejecutar_plan` (4.0-A): el id de un plan de esta conversación que la persona
        acaba de aprobar. Sus pasos los ejecuta el agente, con los argumentos aprobados,
        antes de que hable el modelo; el modelo recibe el informe y se lo cuenta.

        `tope_turno` sustituye a `self.turn_timeout` **solo en este turno**. Va por
        parámetro y no cambiando el atributo porque el agente es uno para todas las
        personas: `/chat/stream` puede esperar 170 s en la nube y `/chat` no, y los
        dos atienden a la vez con el mismo objeto.

        Cada `session_id` tiene su propio historial y su propio lock: dos peticiones
        concurrentes de sesiones distintas no se mezclan, y dos de la misma sesión
        se atienden en orden en lugar de corromper la lista de mensajes.
        """
        session = self.sessions.get(session_id)
        # Una vez temporal, siempre temporal: basta con que se pida en el primer
        # turno para que la conversacion entera quede fuera de la base de datos.
        session.temporary = session.temporary or temporary
        with session.lock:
            self._hydrate(session)
            first_message = not session.messages
            self._compactar(session)
            turn_start = len(session.messages)

            try:
                return self._run_turn(
                    session, user_message, attachments, contexto_espacio, tope_turno,
                    ejecutar_plan=ejecutar_plan,
                )
            finally:
                self._persist(session, turn_start, user_message if first_message else None)
                # Acota el historial aunque el turno haya terminado con una excepción.
                session.trim()

    def _hydrate(self, session: ConversationSession) -> None:
        """Carga una vez la ventana reciente del historial persistido."""
        if self.conversation_history is None or session.hydrated or session.temporary:
            return

        session.hydrated = True
        if session.messages:
            return

        recovered = self.conversation_history.load(session.session_id)
        if recovered:
            session.messages.extend(recovered)
            console.print(f"  [dim]Historial recuperado: {len(recovered)} mensajes[/dim]")

    def _compactar(self, session: ConversationSession) -> None:
        """Los turnos anteriores, solo con su texto (4.1.5): lo que pidió la persona y lo que
        contestó Morgan, los últimos `ventana`. Las llamadas a herramientas y sus resultados
        se quedan en su turno.

        Medido con el modelo real: en memoria la conversación guardaba hasta 200 mensajes
        **con** los resultados de las herramientas (el contenido entero de un archivo leído),
        y se reenviaban en cada llamada de los turnos siguientes; tras un reinicio, en
        cambio, solo se recuperan 20 de texto. Y peor: cuando contestaba Gemini, las
        llamadas de Groq le llegaban como texto («He usado estas herramientas: …») y **las
        imitaba** como respuesta. Si hace falta algo de un turno anterior, se vuelve a leer.
        """
        ventana = self.conversation_history.window if self.conversation_history is not None else 20
        texto = [m for m in session.messages
                 if m.role in ("user", "model", "assistant") and not m.tool_calls and (m.content or "").strip()
                 and m.guardar != ""]
        if len(texto) == len(session.messages) and len(texto) <= ventana:
            return
        session.messages[:] = texto[-ventana:] if ventana > 0 else []

    def _persist(self, session: ConversationSession, start: int, title_hint: str | None) -> None:
        """Guarda los mensajes que ha producido este turno."""
        if self.conversation_history is None or session.temporary:
            return

        nuevos = session.messages[start:]
        if nuevos:
            self.conversation_history.save_turn(
                session.session_id,
                nuevos,
                title_hint=build_title(title_hint) if title_hint else None,
            )

    # Herramientas que escriben en la memoria permanente. En una conversacion
    # temporal no se ofrecen: prometimos que no dejaria rastro, y una llamada a
    # remember_fact lo dejaria. No basta con no persistir los mensajes.
    _MEMORIA_PERSISTENTE = ("remember_fact", "forget_fact")

    #: Herramientas que **solo pueden operar sobre una tarea o un plan que ya
    #: exista**. Sin ninguno abierto, no hay llamada válida que hacer con ellas.
    #:
    #: Se quitan del catálogo cuando la conversación no tiene nada abierto, y
    #: eso no es una apuesta: es una precondición comprobable. Si no hay tareas,
    #: `get_task` no tiene a qué apuntar.
    #:
    #: **Por qué importa.** Medido: el catálogo son ~3.326 tokens de los ~3.716
    #: que pesa una petición real en producción, o sea el 90%. Estas ocho son
    #: 2.414 caracteres, el 17% del catálogo, y van en CADA llamada. Y el cupo
    #: diario de Groq son 200.000 tokens, unos 35 turnos: un turno hace entre
    #: una y tres llamadas, y cada una manda el catálogo otra vez. Medido en
    #: `docs/mediciones.md`.
    #:
    #: Hay un segundo beneficio, y puede que sea el mayor: **menos herramientas
    #: significa más acierto**. Medido con un modelo pequeño, con una sola
    #: herramienta declarada acertaba y con 49 delante se perdía y llegaba a
    #: inventarse la respuesta. Ahorrar cuota y acertar más apuntan al mismo
    #: sitio.
    #:
    #: Las que NO están aquí, y por qué: `create_task` y `create_plan` son las
    #: que empiezan algo, así que tienen que estar siempre; si no, no habría
    #: manera de crear la primera tarea.
    _SOLO_CON_TAREA_ABIERTA = (
        "get_task",
        "list_tasks",
        "cancel_task",
        "retry_task",
        "complete_task",
        "fail_task",
        "get_plan",
        "list_plans",
    )

    #: Herramientas que reciben **la conversación de este turno**, puesta por el
    #: agente y no por el modelo.
    #:
    #: ## El defecto que lo trae, reproducido
    #:
    #: `create_plan` y `list_plans` pedían `session_id` como un parámetro más, y
    #: el modelo **no lo conoce**: no aparece en el prompt ni en los mensajes.
    #: Así que el plan se guardaba sin conversación. Y además el agente nunca
    #: recibía el planificador, así que `_hay_algo_abierto` no veía planes en
    #: absoluto. Juntas, las dos cosas hacían esto:
    #:
    #:     create_plan ok: True | estado: pendiente | session_id guardado: None
    #:     despues de crear el plan -> get_plan ofrecido: False
    #:
    #: El prompt le dice al modelo que, cuando la persona apruebe, `get_plan` se
    #: lo dirá. **Y el turno siguiente ya no tenía `get_plan`**: el recorte del
    #: catálogo lo quitaba por no haber «nada abierto». Nació con ese recorte, en
    #: la 2.0.3, que es donde empezó a importar que los planes se vieran.
    #:
    #: ## Por qué la pone el agente y se quita del esquema
    #:
    #: Dejar que el modelo la rellene es pedirle un dato que no tiene: en el
    #: mejor caso la omite, en el peor se la inventa y el plan acaba en otra
    #: conversación del mismo usuario. El agente sí la sabe. Y quitarla del
    #: esquema ahorra además los tokens de anunciar un parámetro inútil.
    #:
    #: Se pone **después** de validar los argumentos: el validador rechaza lo que
    #: no está en el esquema, y la conversación ya no lo está.
    _ATADAS_A_LA_CONVERSACION = ("create_plan", "list_plans")

    # Herramientas del propio sistema de tareas: anotarlas como pasos llenaria la
    # tarea de ruido sobre si misma.
    _HERRAMIENTAS_DE_TAREAS = (
        "create_task", "get_task", "list_tasks", "advance_task",
        "complete_task", "fail_task", "cancel_task", "retry_task",
    )

    def _atar_tarea_a_la_sesion(self, session: ConversationSession, resultado: dict) -> None:
        """Asocia una tarea recien creada con la conversacion que la origino."""
        datos = resultado.get("data") or {}
        task_id = datos.get("id")
        if not task_id:
            return

        try:
            tarea = self.tasks.obtener(task_id)
            if tarea is not None and tarea.session_id is None:
                tarea.session_id = session.session_id
                self.tasks.repositorio.update(tarea)
        except Exception:
            logger.debug("No se pudo atar la tarea a la sesion", exc_info=True)

    def _detener_por_tiempo(
        self,
        session: ConversationSession,
        messages: list[ChatMessage],
        iteration: int,
        tope: int | None = None,
    ) -> str:
        """Corta el turno y deja dicho por qué.

        Está en un método porque se llama desde dos sitios —al empezar cada
        vuelta y antes de cada herramienta— y el mensaje tiene que ser el mismo.
        Dos textos parecidos para la misma causa hacen que quien lee un informe
        crea que son dos averías distintas.
        """
        tope = tope or self.turn_timeout
        aviso = (
            f"He superado el tiempo máximo previsto para esta solicitud "
            f"({tope} s) y he detenido la ejecución. "
            "Puede que el servicio del modelo esté lento. Vuelve a intentarlo "
            "o divide la tarea en pasos más pequeños."
        )
        console.print(f"  [yellow]⏱ Turno detenido por tiempo ({tope} s)[/yellow]")
        # Con el reparto de lo que llevaba, no solo el tope. Era el punto de la
        # 2.1 «con observabilidad se podrá ver qué paso concreto se pasa», y el
        # aviso decía el tope y la conversación: saber que se pasó sin saber EN
        # QUÉ obliga a reproducirlo, que es lo que la medición existe para no
        # tener que hacer. `modelo=48210x4;herramienta.search_web=31400` dice
        # a quién mirar.
        medicion = medicion_actual()
        logger.warning(
            "Turno detenido por exceder el tope de %s s en la sesión '%s'. "
            "Reparto hasta aquí: %s",
            tope,
            session.session_id,
            medicion.cabecera() if medicion is not None else "sin medir",
        )
        anotar("fallo", "tiempo")     # para las automatizaciones (4.14)
        messages.append(ChatMessage(role="model", content=aviso))
        self.event_handler.on_turn_complete(iteration)
        return aviso

    def _anotar_paso(
        self,
        session: ConversationSession,
        herramienta: str,
        argumentos: dict,
        resultado: dict,
    ) -> None:
        """Registra en la tarea activa la herramienta que se acaba de ejecutar.

        No falla nunca hacia fuera: llevar la cuenta es accesorio y no puede
        tumbar el turno que estaba haciendo el trabajo de verdad.
        """
        if self.tasks is None:
            return

        if herramienta in self._HERRAMIENTAS_DE_TAREAS:
            # Una excepcion: al crear una tarea hay que atarla a esta conversacion.
            # La herramienta no puede hacerlo por si misma —no conoce la sesion—,
            # y sin ese vinculo los pasos siguientes no encuentran a que tarea
            # pertenecen y la tarea se queda con sus pasos en blanco.
            if herramienta == "create_task" and resultado.get("success"):
                self._atar_tarea_a_la_sesion(session, resultado)
            return

        try:
            activas = self.tasks.listar(session_id=session.session_id, solo_activas=True)
            if not activas:
                return

            # Si la tarea traia un paso previsto, se conserva su descripcion: es
            # la legible, la que el usuario queria leer. La herramienta se guarda
            # en su propio campo y la interfaz la muestra aparte. Solo cuando no
            # hay plan se describe el paso con la llamada.
            tarea = activas[0]
            if tarea.paso_actual is not None:
                descripcion = ""
            else:
                detalle = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(argumentos.items())[:2])
                descripcion = f"{herramienta}({detalle})" if detalle else herramienta

            # El veredicto viaja con el paso: es lo que despues impide dar por
            # completada una tarea cuyos pasos se comprobaron y salieron mal.
            comprobado = resultado.get("verificacion") or {}

            self.tasks.avanzar(
                tarea.id,
                descripcion,
                herramienta=herramienta,
                resultado=None if not resultado.get("success") else str(resultado.get("data"))[:300],
                error=resultado.get("error") if not resultado.get("success") else None,
                verificacion=comprobado.get("veredicto"),
                verificacion_motivo=comprobado.get("motivo"),
            )
        except Exception:
            logger.debug("No se pudo anotar el paso en la tarea activa", exc_info=True)

    def _prompt_para_el_catalogo(self) -> str:
        """El prompt sin lo que habla solo de herramientas que no hay.

        **Ante la duda, entero.** Si el catálogo no se puede leer, se manda el
        prompt completo: equivocarse hacia ese lado son unos tokens, y hacia el
        otro es quitarle al modelo instrucciones que sí le servían. Ver
        `prompt_para`.
        """
        try:
            herramientas = self.tools.list_tools()
            disponibles = {t.name for t in herramientas if t.disponible()}
        except Exception:
            logger.debug("No se pudo leer el catálogo; prompt entero", exc_info=True)
            return SYSTEM_PROMPT
        if not disponibles:
            return SYSTEM_PROMPT
        equipo = any(getattr(t, "dinamica", False) and t.name in disponibles for t in herramientas)
        return prompt_para(disponibles, equipo_remoto=equipo, equipos=self._equipos_conectados() if equipo else None)

    @staticmethod
    def _equipos_conectados() -> list[str]:
        """Los nombres de los PC conectados de la persona de este turno (3.7)."""
        try:
            from src.canal.registro import REGISTRO
            from src.identidad import usuario_actual

            return [c.nombre for c in REGISTRO.todas(usuario_actual())]
        except Exception:
            return []

    def _memoria_del_turno(self) -> str:
        """El resumen de la memoria de quien pregunta, o "" (4.0). Nunca lanza: una base que
        falla no puede dejar a nadie sin respuesta."""
        if self.memoria is None:
            return ""
        try:
            return self.memoria() or ""
        except Exception:
            logger.warning("No se pudo leer la memoria de este turno", exc_info=True)
            return ""

    def _indice_del_turno(self) -> str:
        """Los títulos del conocimiento de quien pregunta, o "" (4.1). Nunca lanza."""
        if self.indice_conocimiento is None:
            return ""
        try:
            return self.indice_conocimiento() or ""
        except Exception:
            logger.warning("No se pudo leer el índice del conocimiento", exc_info=True)
            return ""

    def _aviso_del_equipo(self) -> str:
        """El aviso de PC emparejado y desconectado, si aplica (4.3): solo cuando ninguna
        herramienta del PC está disponible en este turno. Nunca lanza."""
        if self.aviso_del_equipo is None:
            return ""
        try:
            if any(getattr(t, "dinamica", False) and t.disponible() for t in self.tools.list_tools()):
                return ""
            return self.aviso_del_equipo() or ""
        except Exception:
            logger.debug("No se pudo mirar si tiene un PC emparejado", exc_info=True)
            return ""

    def _prompt_base_del_turno(self) -> str:
        """El prompt de este turno, antes de espacio y fecha.

        Casi siempre el de `self.system_prompt`, calculado una vez. Pero si hay
        herramientas que dependen del turno (las del PC de la persona, 3.0-E) y alguna
        está disponible **para quien pregunta**, se recalcula: con su PC conectado no
        puede decirle «no tienes acceso al equipo». La memoria no va aquí: es de cada
        persona y se añade en el turno (`_memoria_del_turno`).
        """
        try:
            if not any(getattr(t, "dinamica", False) and t.disponible() for t in self.tools.list_tools()):
                return self.system_prompt
        except Exception:
            logger.debug("No se pudo mirar la disponibilidad; prompt de siempre", exc_info=True)
            return self.system_prompt
        return self._prompt_para_el_catalogo()

    #: Las herramientas tras las que hay que volver a mirar si hay algo abierto (4.3).
    _ABREN_O_CIERRAN = frozenset({"create_task", "complete_task", "fail_task", "cancel_task",
                                  "retry_task", "create_plan"})

    def _schemas_for(self, session: ConversationSession, abierto: bool | None = None,
                     ofrecidas: set[str] | None = None) -> list[dict]:
        """Esquemas de herramientas visibles para esta sesión.

        Se quita lo que **no puede usarse en este turno**, que es distinto de
        adivinar qué va a hacer falta:

        - En una conversación temporal, las de memoria persistente.
        - Sin ninguna tarea ni plan abiertos, las que solo operan sobre uno que
          ya exista. Ver `_SOLO_CON_TAREA_ABIERTA`.

        Las dos son precondiciones comprobables. Nunca se quita algo que el
        modelo pudiera llegar a necesitar: eso sería ahorrar cuota a costa de
        que Morgan no pueda hacer su trabajo, y el cambio no valdría la pena.
        """
        esquemas = self.tools.get_schemas()

        fuera: set[str] = set()

        # Las que no se pueden usar en este turno (las del PC, si no está conectado).
        # Salvo las que ya se ofrecieron en este turno (`ofrecidas`, 4.5): medido en
        # producción, el PC se desconectó a mitad de un turno, sus herramientas
        # desaparecieron de la llamada siguiente, el modelo pidió `list_files` igual y
        # Groq rechazó la petición entera («not in request.tools»). Si se sigue
        # ofreciendo, la llamada llega y la herramienta dice que el PC no está.
        fuera.update(t.name for t in self.tools.list_tools()
                     if not t.disponible() and t.name not in (ofrecidas or ()))

        if session.temporary:
            fuera.update(self._MEMORIA_PERSISTENTE)

        # En una automatización, solo las de consulta (4.14, decisión mía).
        fuera.update(t.name for t in self.tools.list_tools() if not permitida(t.name))

        if not (abierto if abierto is not None else self._hay_algo_abierto(session)):
            fuera.update(self._SOLO_CON_TAREA_ABIERTA)

        if not fuera:
            return esquemas
        return [e for e in esquemas if e.get("name") not in fuera]

    def _hay_algo_abierto(self, session: ConversationSession) -> bool:
        """Si esta conversación tiene alguna tarea o plan con el que operar.

        **Ante la duda, dice que sí.** Si no se puede averiguar —no hay gestor
        de tareas, el almacén falla— se deja el catálogo entero: el coste de
        equivocarse hacia ese lado son unos tokens, y hacia el otro es que
        Morgan no pueda cancelar una tarea que sí existe.
        """
        # Sin gestor de tareas se salta SOLO la consulta de tareas, no la de
        # planes. Antes esto era un `return False` que salía antes de mirar los
        # planes, así que un Morgan sin tareas tampoco veía sus planes nunca.
        if self.tasks is not None:
            try:
                if self.tasks.listar(session_id=session.session_id, solo_activas=True):
                    return True
            except Exception:
                logger.debug(
                    "No se pudo comprobar si hay tareas abiertas; se deja el "
                    "catálogo completo", exc_info=True,
                )
                return True

        planes = getattr(self, "planes", None)
        if planes is None:
            return False
        try:
            return bool(planes.listar(session_id=session.session_id))
        except Exception:
            logger.debug("No se pudo comprobar si hay planes", exc_info=True)
            return True

    def _run_turn(
        self,
        session: ConversationSession,
        user_message: str,
        attachments: list[dict] | None = None,
        contexto_espacio: str | None = None,
        tope_turno: int | None = None,
        ejecutar_plan: str | None = None,
    ) -> str:
        messages = session.messages
        tope = tope_turno or self.turn_timeout

        # El prompt de ESTE turno. Las instrucciones del espacio de trabajo se
        # añaden aquí, a una variable local, y **nunca a `self.system_prompt`**.
        #
        # El agente es uno solo para todas las personas: la API lo construye una
        # vez y atiende a todo el mundo con él. Escribir las instrucciones en el
        # prompt compartido haría que las del proyecto de una persona se colaran
        # en el turno siguiente de otra. La memoria sí va en el prompt
        # compartido, pero se sustituye entero en cada cambio; esto no se podría
        # sustituir sin carreras entre dos turnos simultáneos.
        base_del_turno = self._prompt_base_del_turno()
        # La memoria de quien pregunta, de este turno (4.0): nunca del prompt compartido.
        memoria_del_turno = self._memoria_del_turno()
        if memoria_del_turno:
            base_del_turno = f"{base_del_turno}\n\n{memoria_del_turno}"
        indice = self._indice_del_turno()
        if indice:
            base_del_turno = f"{base_del_turno}\n\n{indice}"
        aviso = self._aviso_del_equipo()
        if aviso and PARRAFO_SIN_ACCESO in base_del_turno:
            base_del_turno = base_del_turno.replace(PARRAFO_SIN_ACCESO, aviso)
        elif aviso:
            base_del_turno = f"{base_del_turno}\n\n{aviso}"
        prompt_del_turno = (
            f"{base_del_turno}\n\n{contexto_espacio}"
            if contexto_espacio else base_del_turno
        )
        # La fecha, que el modelo no sabía (V2.0.17). Sin ella, «créame una cita
        # mañana» salía con un día inventado. Va aquí y no en `self.system_prompt`
        # porque cambia en cada turno y el agente es compartido. Es la del
        # servidor, en UTC: la zona de la persona la da su calendario.
        prompt_del_turno = f"{prompt_del_turno}\n\n{_linea_de_fecha()}"

        # 1. Agregar mensaje del usuario al historial
        para_el_modelo = texto_con_adjuntos(user_message, attachments)
        messages.append(
            ChatMessage(role="user", content=para_el_modelo, attachments=attachments,
                        guardar=user_message if para_el_modelo != user_message else None)
        )

        iteration = 0
        call_signatures_history: list[str] = []
        #: Llamadas que ya fallaron en este turno: herramienta + argumentos exactos.
        #: Repetirlas da el mismo error y gasta las vueltas que quedan (ver abajo).
        #: La fuga por internet (3.1-B, decisión mía): en cuanto este turno lee algo
        #: del PC, solo se abren direcciones que dio la persona o que salieron de una
        #: búsqueda. Ver `src/agent/fuga.py`.
        estado = _EstadoDelTurno(urls_conocidas=fuga.conocidas(user_message))
        estado.automatico = self._permiso_automatico_del_turno()

        # Un plan que la persona acaba de aprobar (4.0-A, decisión mía: aprobar es la
        # orden). Lo ejecuta el agente y el informe va en el mensaje, para el modelo.
        if ejecutar_plan:
            informe = self._ejecutar_plan(session, ejecutar_plan, estado)
            messages[-1] = ChatMessage(role="user", content=f"{para_el_modelo}\n\n{informe}",
                                       attachments=attachments, guardar=user_message)
            if estado.resumen_del_plan:
                anotar("proveedor", "Morgan (sin modelo)")
                messages.append(ChatMessage(role="model", content=estado.resumen_del_plan))
                self.event_handler.on_turn_complete(0)
                return estado.resumen_del_plan
        deadline = time.monotonic() + tope

        while iteration < self.max_iterations:
            if time.monotonic() >= deadline:
                return self._detener_por_tiempo(session, messages, iteration, tope)

            iteration += 1
            self.event_handler.on_thinking_start(iteration)
            # Al canal del turno, no al `event_handler`: ese es uno para todas
            # las personas. Ver `src/eventos_turno.py`.
            emitir("pensando", vuelta=iteration)

            if estado.algo_abierto is None:
                estado.algo_abierto = self._hay_algo_abierto(session)
            tool_schemas = self._schemas_for(session, abierto=estado.algo_abierto,
                                             ofrecidas=estado.ofrecidas)
            estado.ofrecidas.update(e.get("name") for e in tool_schemas)

            try:
                # 2. Consultar al LLM Provider
                response: LLMResponse = self.model.generate(
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    system_prompt=prompt_del_turno,
                )
            except Exception as e:
                # El str() de la excepcion es el cuerpo crudo de la respuesta HTTP
                # del proveedor. Se registra entero, pero a la interfaz solo va una
                # explicacion legible.
                error_msg = log_llm_error(e, contexto=self.model.model_name)
                anotar("fallo", "modelo")     # para las automatizaciones (4.14)
                console.print(f"  [red]Error LLM:[/red] {error_msg}")
                messages.append(ChatMessage(role="model", content=error_msg))
                return error_msg

            # 3. Si el modelo responde texto directamente, finaliza el turno
            if response.type == "text" or not response.tool_calls:
                final_text = response.content or ""
                # Lo dice el núcleo, no el modelo (4.1). Medido en la 4.0.5: con su plan
                # rechazado por `create_plan`, el modelo contestó «He preparado el plan…»
                # y no había nada que aprobar.
                parece_un_plan = not estado.plan_creado and bool(_PARECE_UN_PLAN.search(final_text))
                if parece_un_plan and not estado.plan_recordado and iteration < self.max_iterations:
                    # Se le recuerda una vez y se sigue: la persona no ve el texto imitado.
                    # La nota no se guarda ni pasa a los turnos siguientes (`guardar=""`).
                    estado.plan_recordado = True
                    messages.append(ChatMessage(role="user", content=RECUERDA_CREATE_PLAN, guardar=""))
                    continue
                if (estado.planes_rechazados or parece_un_plan) and not estado.plan_creado:
                    final_text += NOTA_SIN_PLAN
                messages.append(ChatMessage(role="model", content=final_text))
                self.event_handler.on_turn_complete(iteration)
                return final_text

            # 4. Si el modelo solicita llamadas a herramientas (Tool Calls)
            # Guardamos la intención del modelo en el contexto
            messages.append(
                ChatMessage(
                    role="model",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                    raw_parts=response.raw_parts,
                )
            )

            # Procesamos cada llamada solicitada
            for indice, tool_call in enumerate(response.tool_calls):
                # El plazo se mira AQUI tambien, no solo al empezar la vuelta.
                #
                # Cuando el modelo pide seis busquedas en una sola respuesta,
                # entre dos comprobaciones de arriba caben minutos enteros, y el
                # turno se pasaba del tope sin que nadie lo mirara. En la nube eso
                # no es un detalle: el proxy del borde corta a los 120 s con una
                # pagina de error suya, y el trabajo hecho se tira. Medido: con el
                # tope en 110 s, un turno murio en el proxy a los 120,1 s — justo
                # lo que ese numero existia para evitar.
                if time.monotonic() >= deadline:
                    # A las que quedan se les contesta igualmente. Un `tool_call`
                    # sin su resultado deja el historial incoherente, y el
                    # proveedor rechaza el turno siguiente por una razon que no
                    # se parece en nada a esta.
                    for pendiente in response.tool_calls[indice:]:
                        messages.append(ChatMessage(
                            role="tool",
                            tool_name=pendiente.name,
                            tool_call_id=pendiente.id,
                            tool_result={
                                "success": False,
                                "data": None,
                                "error": "No se ejecutó: el turno se detuvo por tiempo antes de llegar aquí.",
                            },
                        ))
                    return self._detener_por_tiempo(session, messages, iteration, tope)

                tool_name = tool_call.name
                tool_args = tool_call.arguments or {}

                # Detección de bucles repetitivos infinitos (misma herramienta + mismos args)
                signature = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
                call_signatures_history.append(signature)
                if call_signatures_history.count(signature) >= 3:
                    loop_msg = (
                        f"Se interrumpió la ejecución: bucle repetitivo detectado con la herramienta '{tool_name}'."
                    )
                    messages.append(ChatMessage(role="tool", tool_name=tool_name, tool_call_id=tool_call.id, tool_result={"success": False, "error": loop_msg}))
                    break

                tool_result = self._ejecutar_una(session, tool_name, tool_args, estado)
                if tool_name in self._ABREN_O_CIERRAN:
                    estado.algo_abierto = None          # se vuelve a mirar en la siguiente vuelta
                if tool_name == "create_plan" and tool_result.get("error") != PLAN_YA_HECHO:
                    if tool_result.get("success"):
                        estado.plan_creado = True
                        datos = tool_result.get("data") or {}
                        if datos.get("estado") in ("pendiente", "aprobado") and not datos.get(
                                "herramientas_que_no_existen"):
                            estado.plan_propuesto = datos
                    else:
                        estado.planes_rechazados += 1

                # Inyectar el resultado como mensaje de rol tool
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_name=tool_name,
                        tool_call_id=tool_call.id,
                        tool_result=tool_result,
                    )
                )

            # Un plan propuesto y pendiente de aprobar (4.1.5): lo que queda es decírselo a
            # la persona, y la web ya lo enseña entero, con su botón, encima del chat.
            # Medido con el modelo real: esa llamada costaba unos 4.300 tokens de entrada y
            # 400-870 de salida en repetir el plan en una tabla. Si el plan trae un aviso
            # (herramientas que no existen), sigue el modelo, que tiene que corregirlo.
            # Solo si no falló nada más y no es el turno de un plan aprobado: ahí, si un paso
            # falló y el modelo propone otro camino (4.0-C), tiene que contar el fallo. Visto
            # al auditar la 4.2: la plantilla lo habría tapado con «Te propongo este plan».
            # Con el permiso automático (4.6), un plan verde o amarillo no espera al botón:
            # se aprueba y se ejecuta aquí. Si todo sale, el resumen lo escribe el núcleo,
            # como al aprobar desde la web; si algo falla, el modelo lo cuenta. Uno con algo
            # rojo sigue a la propuesta de siempre.
            #
            # Y uno que no necesita aprobación (4.8), también: medido con el modelo real, para
            # «abre VS Code» planeaba un paso verde, el plan quedaba «aprobado» sin que nadie
            # lo ejecutara, y el modelo tenía que llamar otra vez a la herramienta.
            if (estado.plan_propuesto is not None and not estado.fallo_algo and not ejecutar_plan
                    and (estado.automatico or estado.plan_propuesto.get("estado") == "aprobado")):
                informe = self._aprobar_solo(session, estado)
                if informe is not None:
                    estado.plan_propuesto = None
                    if estado.resumen_del_plan:
                        anotar("proveedor", "Morgan (sin modelo)")
                        messages.append(ChatMessage(role="model", content=estado.resumen_del_plan))
                        self.event_handler.on_turn_complete(iteration)
                        return estado.resumen_del_plan
                    messages.append(ChatMessage(role="user", content=informe, guardar=""))
                    continue

            if (estado.plan_propuesto is not None and not estado.fallo_algo and not ejecutar_plan
                    and estado.plan_propuesto.get("estado") == "pendiente"):
                texto = _texto_de_propuesta(estado.plan_propuesto)
                messages.append(ChatMessage(role="model", content=texto))
                self.event_handler.on_turn_complete(iteration)
                return texto

        # Si se excedieron las iteraciones máximas sin respuesta final
        limit_notice = (
            "He alcanzado el límite máximo de pasos permitidos para esta solicitud. "
            "Por favor indícame cómo deseas continuar."
        )
        anotar("fallo", "pasos")      # para las automatizaciones (4.14)
        messages.append(ChatMessage(role="model", content=limit_notice))
        self.event_handler.on_turn_complete(iteration)
        return limit_notice

    @staticmethod
    def _imposible(tool, argumentos: dict) -> str | None:
        """El porqué de que esta llamada no podría salir nunca, si la herramienta lo sabe
        (`prevalidar`, 4.12). Nunca lanza."""
        prevalidar = getattr(tool, "prevalidar", None)
        if prevalidar is None:
            return None
        try:
            return prevalidar(argumentos)
        except Exception:
            return None

    def _permiso_automatico_del_turno(self) -> bool:
        """Si quien pregunta tiene el permiso automático (4.6). Nunca lanza: ante un fallo, no."""
        if self.permiso_automatico is None or automatizacion_actual() is not None:
            return False      # en una automatización no hay nadie que lo haya dado ahora
        try:
            return bool(self.permiso_automatico())
        except Exception:
            logger.warning("No se pudo mirar el permiso automático", exc_info=True)
            return False

    def _aprobar_solo(self, session: ConversationSession, estado: "_EstadoDelTurno") -> str | None:
        """Aprueba y ejecuta el plan recién propuesto si es verde o amarillo (4.6), y devuelve
        el informe; None si no entra (tiene algo rojo) y debe esperar a la persona. El
        riesgo es el del plan guardado, que calcula el planificador, no el modelo."""
        from src.identidad import usuario_actual
        from src.identidad.permiso_automatico import entra

        planes = getattr(self, "planes", None)
        plan_id = (estado.plan_propuesto or {}).get("id")
        plan = planes.obtener(plan_id) if planes is not None and plan_id else None
        if plan is None:
            return None
        if str(plan.estado) == "aprobado":
            # No necesitaba aprobación (4.8). Solo si ningún paso es una consulta: lo leído
            # tiene que verlo el modelo para contestar, y el resumen «Listo» lo taparía.
            if any(_es_consulta(p) for p in plan.pasos):
                return None
            return self._ejecutar_plan(session, plan.id, estado)
        if not estado.automatico or not entra(plan.riesgo):
            return None
        planes.aprobar(plan.id, quien=f"{usuario_actual()} (automático)")
        return self._ejecutar_plan(session, plan.id, estado)

    def _ejecutar_plan(self, session: ConversationSession, plan_id: str,
                       estado: "_EstadoDelTurno") -> str:
        """Ejecuta un plan aprobado de esta conversación, paso a paso (4.0-A).

        **Lo ejecuta el agente, no el modelo**: en orden y con **los argumentos que se
        aprobaron**, por el mismo camino que una llamada del modelo (`_ejecutar_una`:
        permisos, autorización del plan, verificación, anotación). Así lo que se hace es
        lo que la persona aprobó: el modelo no puede cambiar una ruta ni saltarse un paso.
        **Se para en el primer paso que no sale** (4.0.5): lo que viene después suele darlo
        por hecho, y el modelo no siempre lo declara en `depende_de`. Medido con el modelo
        real: «cambia kiwi por mango y, solo si sale, crea hecho.txt» llegó con los tres
        pasos sin dependencias; el cambio falló y se creó hecho.txt igual. Devuelve el
        informe que leerá el modelo para contárselo a la persona.

        Solo un plan **de esta conversación y aprobado**: uno de otra, o de otra persona
        (el planificador solo ve los suyos), no existe. Ejecutarlo dos veces no hace nada
        dos veces: en cuanto se ejecuta un paso, el plan deja de estar «aprobado»
        (V2.0.16), y un plan que no lo está no se ejecuta."""
        from src.tasks.plan import TransicionInvalida

        planes = getattr(self, "planes", None)
        plan = planes.obtener(plan_id) if planes is not None else None
        if plan is None or plan.session_id != session.session_id:
            return "[No hay en esta conversación un plan con ese identificador: no se ejecutó nada.]"
        if str(plan.estado) != "aprobado":
            return (f"[El plan «{plan.objetivo}» está {plan.estado}: solo se ejecuta uno aprobado, "
                    "así que no se ejecutó nada.]")
        planes.marcar_ejecutando(plan_id)
        salio: dict[int, bool] = {}
        lineas: list[str] = []
        hechos: list[str] = []
        todos_con_herramienta = True
        for paso in sorted(plan.pasos, key=lambda p: p.orden):
            titulo = f"{paso.orden}. {paso.descripcion}"
            if not paso.herramienta:
                todos_con_herramienta = False
                lineas.append(f"{titulo}: sin herramienta (es tuyo: explícalo o pregunta).")
                continue
            fallaron = [orden for orden, bien in salio.items() if not bien]
            if fallaron:
                salio[paso.orden] = False
                lineas.append(f"{titulo} ({paso.herramienta}): NO se ejecutó: el paso "
                              f"{fallaron[0]} no salió, y un plan se para en el primer fallo.")
                continue
            resultado = self._ejecutar_una(session, paso.herramienta, dict(paso.argumentos), estado)
            salio[paso.orden] = bool(resultado.get("success"))
            veredicto = (resultado.get("verificacion") or {}).get("veredicto")
            if salio[paso.orden]:
                lineas.append(f"{titulo} ({paso.herramienta}): hecho"
                              + (", y comprobado." if veredicto == "correcto" else "."))
                # Lo que la herramienta quiera que sepa la persona (4.14: cuándo es la primera
                # ejecución de una automatización y dónde verla), en el mismo «Listo».
                datos = resultado.get("data")
                nota = datos.get("para_la_persona") if isinstance(datos, dict) else None
                hechos.append((paso.descripcion or paso.herramienta).rstrip(".")
                              + (" (comprobado)" if veredicto == "correcto" else "")
                              + (f". {str(nota).strip().rstrip('.')}" if nota else ""))
            else:
                lineas.append(f"{titulo} ({paso.herramienta}): NO salió: "
                              f"{str(resultado.get('error') or 'sin motivo')[:300]}")
        todo_bien = all(salio.values())
        estado.plan_hecho = todo_bien
        # Todo salió y no queda nada que explicar o preguntar: el resumen lo escribe el
        # núcleo (4.1.5, decisión mía). Medido: esa llamada al modelo costaba unos
        # 4.500 tokens solo para decir «listo», y a veces hacía algo más sin pedirlo
        # (traer una copia del archivo, proponer otro plan). Si algo falló, lo redacta el
        # modelo: tiene que explicar y, si puede, intentar otro camino (4.0-C).
        if todo_bien and hechos and todos_con_herramienta:
            estado.resumen_del_plan = ("Listo: " + hechos[0] + "." if len(hechos) == 1 else
                                       "Listo:\n" + "\n".join(f"- {h}" for h in hechos))
        try:
            actual = planes.obtener(plan_id)
            if actual is not None and str(actual.estado) == "ejecutando":
                planes.cerrar(plan_id, todo_bien)
        except (TransicionInvalida, KeyError):
            logger.debug("El plan %s ya estaba cerrado", plan_id)
        # Una automatización de pasos fijos (4.15) no llama al modelo ni si algo falla: el aviso
        # es el informe tal cual. Medido con el modelo real: al redactarlo, se inventó «la
        # carpeta estaba vacía» y «se probó otro método», sin haber hecho nada de eso.
        if not todo_bien and (automatizacion_actual() or {}).get("_pasos") is not None:
            anotar("fallo", "plan")
            estado.resumen_del_plan = "No salió todo:\n" + "\n".join(f"- {linea}" for linea in lineas)
        cierre = ("Cuéntaselo a la persona en pocas líneas." if todo_bien else
                  f"{NO_CONFIRMADO} Cuéntaselo en pocas líneas." if estado.no_confirmado else
                  "Si algo no salió, tienes UN intento con otro camino (nunca lo mismo; si exige "
                  "plan, propónlo). Después, cuéntaselo a la persona en pocas líneas, diciendo qué "
                  "no salió y por qué.")
        return (f"[Plan aprobado «{plan.objetivo}»: Morgan ya ejecutó sus pasos, con los argumentos "
                f"aprobados. No los repitas.\n" + "\n".join(lineas) + f"\n{cierre}]")

    def _ejecutar_una(self, session: ConversationSession, tool_name: str, tool_args: dict,
                      estado: "_EstadoDelTurno") -> dict:
        """Una llamada a una herramienta de principio a fin: que exista y esté disponible,
        sus argumentos, el plan aprobado o el permiso, la fuga por internet, lo que ya
        falló, la ejecución, la verificación y la anotación en la tarea. Devuelve lo que
        verá el modelo.

        **Un solo camino** (4.0-A): lo usan las llamadas del modelo y el ejecutor de un
        plan aprobado, con los mismos permisos y la misma verificación."""
        self.event_handler.on_tool_call_start(tool_name, tool_args)
        # Solo el nombre: los argumentos pueden llevar rutas o secretos,
        # y este evento sale hacia el navegador.
        emitir("herramienta", nombre=tool_name, estado="empieza")
        tool = self.tools.get(tool_name)

        if not tool:
            err_result = {
                "success": False,
                "data": None,
                "error": f"La herramienta '{tool_name}' no existe en el catálogo.",
            }
            return err_result

        if tool_name == "create_plan" and estado.plan_hecho:
            return {"success": False, "data": None, "error": PLAN_YA_HECHO}
        if tool_name == "create_plan" and estado.no_confirmado:
            return {"success": False, "data": None, "error": NO_CONFIRMADO}

        # Una automatización solo consulta (4.14): aunque la pida por su nombre.
        if not permitida(tool_name, tool_args):
            return {"success": False, "data": None, "error": NO_PERMITIDA}

        # Que no esté en el catálogo del turno no impide que el modelo la pida
        # por su nombre: se comprueba aquí también (3.0-E). Las del PC de la
        # persona, sin su agente conectado, no se intentan: se dice por qué.
        if not tool.disponible():
            return {
                "success": False,
                "data": None,
                "error": f"'{tool_name}' no está disponible ahora: {tool.motivo_no_disponible}",
            }

        # Validar argumentos requeridos
        val_error = self._validate_args(tool, tool_args)
        if val_error:
            err_result = {
                "success": False,
                "data": None,
                "error": f"Argumentos inválidos: {val_error}",
            }
            return err_result

        # Lo que actúa fuera de Morgan en nombre de alguien no pasa por la
        # confirmación de siempre: pasa por un plan aprobado, con estos
        # argumentos exactos, una vez. Ver `Tool.exige_plan`.
        autorizacion = None
        # Con el permiso automático (4.6), lo verde y amarillo no espera a un plan. Si hay
        # un paso de plan aprobado para esto, se usa igual: así el paso queda hecho.
        sin_esperar = False
        if getattr(tool, "exige_plan", False):
            autorizacion = self._autorizado_por_plan(session, tool, tool_args)
            if not autorizacion.concedida:
                if estado.automatico and permiso_entra(tool.permission_level):
                    autorizacion, sin_esperar = None, True
                elif (imposible := self._imposible(tool, tool_args)):
                    # Lo que no podría salir, se dice ya, en vez de «usa create_plan» (4.12).
                    return {"success": False, "data": None, "error": imposible}
                else:
                    return {
                        "success": False,
                        "data": None,
                        "error": self._sin_plan_aprobado(tool_name, tool_args, autorizacion),
                    }

        # Validar permisos contextuales con los argumentos
        elif not self.permissions.check_permission(tool, tool_args):
            perm_result = {
                "success": False,
                "data": None,
                "error": self._por_que_no_se_pudo(tool_name),
            }
            return perm_result

        # Una dirección que nadie ha dado, después de leer el PC (3.1-B).
        # Solo si de verdad sale una dirección: `open_app` con accion=aplicacion o carpeta no
        # lleva `url`, y su `path` lo resuelve el PC dentro de lo permitido (una dirección web
        # ahí se rechaza). Medido en la 4.13: comparar la cadena vacía bloqueaba «abre el
        # proyecto Morgan en VS Code» en cuanto se había leído algo del PC.
        arg_url = fuga.SALEN_A_INTERNET.get(tool_name)
        direccion = str(tool_args.get(arg_url) or "") if arg_url else ""
        sale = bool(direccion.strip()) or str(tool_args.get("accion") or "").lower() == "url"
        if arg_url and sale and estado.leyo_del_pc and not fuga.se_puede_abrir(
            direccion, estado.urls_conocidas
        ):
            logger.warning("Se bloquea una salida a internet tras leer el PC: %s", tool_name)
            return {"success": False, "data": None, "error": fuga.NO_CONOCIDA}

        # Lo mismo, otra vez, después de fallar: no se ejecuta (3.1).
        #
        # Medido conmigo el 2026-09-19: `copy_file` falló por un error interno y
        # el modelo la repitió cuatro veces con los mismos argumentos, gastando
        # el turno y acabando en «he alcanzado el límite de pasos». Repetir una
        # llamada que acaba de fallar **da el mismo error**: lo útil es decírselo
        # y que pruebe otro camino, con lo que además le quedan vueltas para
        # hacerlo.
        firma_fallo = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
        if firma_fallo in estado.fallidas:
            return {
                "success": False,
                "data": None,
                "error": (
                    f"Esto ya lo intentaste en esta misma respuesta y falló igual: "
                    f"{estado.fallidas[firma_fallo]} No lo repitas. Prueba otra forma "
                    f"—otra herramienta, o los mismos datos de otra manera— y si no "
                    f"hay ninguna, dile a la persona qué ha fallado y qué puede hacer."
                ),
            }

        # Corregir, con tope (4.0-C). Sin tope, un modelo insistente gasta cuota y tiempo
        # probando cambios sin llegar; y la decisión mía es un intento, no varios.
        # `exige_plan`, aunque hoy todas las que lo exigen son de riesgo moderado o más: es
        # redundante a propósito, para que una futura de riesgo bajo no se salte el tope.
        cambia = bool(getattr(tool, "exige_plan", False)) or str(tool.permission_level) in _CAMBIAN
        if cambia and estado.sin_mas_intentos:
            return {"success": False, "data": None, "error": SIN_MAS_INTENTOS}
        es_correccion = cambia and estado.correccion_pendiente
        if es_correccion:
            estado.correccion_pendiente = False

        # Ejecución de la herramienta
        console.print(f"  [dim]⚡ Usando herramienta:[/dim] [bold cyan]{tool_name}[/bold cyan]")
        try:
            # Por nombre y no en un solo cajon «herramientas»: cuando un
            # turno tarda, lo que hace falta saber es CUAL tardo.
            argumentos = dict(tool_args)
            # De qué paso de qué plan sale lo que se hace en el PC (4.1): lo pone el
            # núcleo desde la autorización, nunca el modelo. Con él, el agente distingue
            # un reintento del mismo paso (que no repite) de otro plan que pide lo mismo
            # (que sí). Medido: dos «anota la copia de hoy», en dos planes aprobados,
            # añadieron una sola línea y la segunda se contó como hecha.
            argumentos.pop("_origen", None)
            if autorizacion is not None and autorizacion.paso is not None and getattr(tool, "en_el_pc", False):
                argumentos["_origen"] = f"{autorizacion.plan.id}#{autorizacion.paso.orden}"
            elif sin_esperar and getattr(tool, "en_el_pc", False):
                argumentos["_origen"] = f"auto:{estado.id_turno}"
            if tool_name in self._ATADAS_A_LA_CONVERSACION:
                # Sobrescribe lo que trajera el modelo: el dato bueno es
                # el del agente. Ver `_ATADAS_A_LA_CONVERSACION`.
                argumentos["session_id"] = session.session_id
            with etapa(f"herramienta.{tool_name}"):
                tool_result = tool.execute(**argumentos)
        except Exception as e:
            tool_result = {
                "success": False,
                "data": None,
                "error": f"Excepción interna en la herramienta: {e}",
            }

        success = tool_result.get("success", False)
        if success and tool_name in fuga.DEL_PC:
            estado.leyo_del_pc = True
        if success and tool_name == "search_web":
            # Lo que ella misma encontró sí se puede abrir.
            estado.urls_conocidas |= fuga.conocidas(json.dumps(tool_result, default=str))
        if not success:
            estado.fallidas[firma_fallo] = str(tool_result.get("error") or "")[:200]
            if tool_name != "create_plan":
                estado.fallo_algo = True
        self.event_handler.on_tool_call_result(tool_name, success, tool_result.get("error"))
        emitir("herramienta", nombre=tool_name, estado="ok" if success else "fallo")

        if autorizacion is not None:
            self._consumir_autorizacion(tool, tool_args, autorizacion, success)

        # --- Verificacion (V1.7) ---
        #
        # Que la herramienta no fallara no significa que el objetivo se
        # cumpliera: un create_file puede dejar el archivo vacio y
        # devolver success. Aqui se comprueba EL EFECTO contra el mundo
        # real y el veredicto se le devuelve al modelo, que es lo que le
        # permite corregirse en vez de dar por hecho que salio bien.
        tool_result = self._verificar(tool_name, tool_args, tool_result,
                                      en_el_pc=bool(getattr(tool, "en_el_pc", False)))

        # Un cambio que no salió (la herramienta falló o la verificación dice que el efecto no
        # está) da UN intento con otro camino; si el que no salió era ese intento, se acabó.
        if cambia and not tool_result.get("success") and tool_result.get("motivo") == "no_confirmada":
            # No es un fallo que corregir (4.4). Medido con el modelo real: tras un borrado sin
            # confirmar, la corrección de la 4.0-C lo volvía a proponer, y quedaba otro plan
            # pendiente para lo que la persona no había permitido.
            estado.no_confirmado = True
            estado.sin_mas_intentos = True
            tool_result = {**tool_result, "error": f"{tool_result.get('error') or 'No salió.'} {NO_CONFIRMADO}"}
        elif cambia and not tool_result.get("success") and (automatizacion_actual() or {}).get("_pasos") is not None:
            # Pasos fijos (4.15): no hay otro camino que probar, y el error va tal cual a la
            # bandeja. Medido: «Tienes UN intento con otro camino» le llegaba a la persona.
            estado.sin_mas_intentos = True
        elif cambia and not tool_result.get("success"):
            aviso = YA_PROBASTE if es_correccion else UN_INTENTO
            if es_correccion:
                estado.sin_mas_intentos = True
            else:
                estado.correccion_pendiente = True
            tool_result = {**tool_result, "error": f"{tool_result.get('error') or 'No salió.'} {aviso}"}

        # El paso se anota solo. Hacer que el modelo llamara a
        # 'advance_task' despues de cada herramienta duplicaba los viajes
        # de ida y vuelta al proveedor: el turno de la prueba tardo 126 s
        # y se corto por el limite. El agente ya sabe que ejecuto y como
        # fue, asi que preguntarselo al modelo era pagar por un dato que
        # ya estaba en la mano.
        self._anotar_paso(session, tool_name, tool_args, tool_result)

        if success:
            console.print("  [dim green]✔ Herramienta completada con éxito[/dim green]")
        else:
            console.print(f"  [dim yellow]⚠ Herramienta reportó: {tool_result.get('error')}[/dim yellow]")

        return tool_result

    def _verificar(self, tool_name: str, args: dict, resultado: dict, en_el_pc: bool = False) -> dict:
        """Comprueba el efecto de una herramienta y anota el veredicto.

        Devuelve el resultado con la verificación dentro, para que viaje al
        modelo junto a lo demás.

        **Solo añade algo cuando hay algo que decir.** Para lo que Morgan no sabe
        comprobar no se escribe «no verificable» en cada resultado: seria ruido
        en el contexto y acabaria enseñando a ignorarlo.
        """
        if self.verificador is None:
            return resultado

        comprobacion = self.verificador.verificar(tool_name, args, resultado, en_el_pc=en_el_pc)

        if comprobacion.veredicto == "no_verificable":
            return resultado

        resultado = dict(resultado)
        resultado["verificacion"] = comprobacion.to_dict()

        if comprobacion.fallido and resultado.get("success"):
            # El caso que da sentido a todo esto: la llamada dijo que si y el
            # efecto dice que no. Se corrige el veredicto, porque dejarlo en
            # `success: True` es exactamente la afirmacion falsa que la V1.7
            # existe para impedir.
            resultado["success"] = False
            resultado["error"] = (
                f"La herramienta terminó pero no consiguió su efecto: "
                f"{comprobacion.motivo}"
            )
            console.print(
                f"  [dim yellow]⚠ Verificación: {comprobacion.motivo}[/dim yellow]"
            )
        elif comprobacion.correcto:
            console.print("  [dim green]✔ Verificado[/dim green]")

        return resultado

    def _autorizado_por_plan(self, session: ConversationSession, tool, tool_args: dict):
        """Busca en esta conversación un paso aprobado para esta llamada exacta.

        **Sin planificador no hay autorización posible**, y eso es lo correcto: una
        herramienta que exige plan no se ejecuta nunca antes que ejecutarse sin él.
        Una conversación temporal tampoco tiene planes guardados.
        """
        from src.tasks.planificador import Autorizacion

        # La excepción: una automatización de pasos fijos (4.15) corre en una conversación
        # temporal (no ensucia el historial) con su propio plan aprobado, el de esa ejecución.
        fija = (automatizacion_actual() or {}).get("_pasos") is not None
        if self.planes is None or (session.temporary and not fija):
            return Autorizacion(motivo="sin_plan")
        return self.planes.buscar_paso_aprobado(session.session_id, tool.name, tool_args)

    def _consumir_autorizacion(self, tool, tool_args: dict, autorizacion, success: bool) -> None:
        """Gasta la aprobación **solo si la herramienta funcionó**, y lo deja auditado.

        Si falló, el efecto aprobado no ha ocurrido y el paso sigue disponible para
        reintentarlo. Si funcionó, ya no sirve para una segunda vez.
        """
        audit = getattr(self.permissions, "audit", None)
        if audit is not None:
            try:
                audit.log(
                    tool_name=tool.name,
                    risk_level=tool.risk_level,
                    authorized=True,
                    args={**tool_args, "autorizado_por_plan": f"{autorizacion.plan.id}#{autorizacion.paso.orden}"},
                    success=success,
                )
            except Exception:
                logger.warning("No se pudo auditar la ejecución autorizada por plan", exc_info=True)

        if not success:
            return
        try:
            self.planes.marcar_paso_ejecutado(autorizacion.plan.id, autorizacion.paso.orden)
        except Exception:
            # El efecto ya ocurrió. Que no se pueda apuntar no lo deshace, y
            # lanzar aquí tiraría el turno con el trabajo hecho. Queda en el log.
            logger.error(
                "La herramienta %s se ejecutó con el plan %s pero no se pudo marcar el paso %s",
                tool.name, autorizacion.plan.id, autorizacion.paso.orden, exc_info=True,
            )

    @staticmethod
    def _sin_plan_aprobado(tool_name: str, tool_args: dict, autorizacion) -> str:
        """Qué se le dice al modelo cuando falta la aprobación, según por qué falta.

        Con el camino escrito, igual que `_por_que_no_se_pudo`: una regla en el
        prompt es una sugerencia; una respuesta del sistema en el momento del
        fallo es un camino.
        """
        argumentos = json.dumps(tool_args, ensure_ascii=False, sort_keys=True)
        if autorizacion.motivo == "pendiente":
            return (
                f"'{tool_name}' está en el plan {autorizacion.plan.id}, que todavía espera "
                "aprobación. NO la ejecutes: dile a la persona que lo apruebe en la "
                "interfaz y espera a que te lo confirme."
            )
        if autorizacion.motivo == "otros_argumentos":
            aprobados = "; ".join(
                json.dumps(a, ensure_ascii=False, sort_keys=True) for a in autorizacion.aprobados
            )
            return (
                f"Lo aprobado para '{tool_name}' tiene otros argumentos: {aprobados}. "
                "Solo se puede ejecutar exactamente lo aprobado. Si hace falta otra cosa, "
                "propón un plan nuevo con create_plan."
            )
        return (
            f"'{tool_name}' solo se ejecuta con un plan aprobado, porque actúa en nombre "
            "de la persona fuera de Morgan. Usa create_plan con un paso de esta "
            f"herramienta y exactamente estos argumentos: {argumentos}. Cuando la "
            "persona lo apruebe, vuelve a llamarla con los mismos."
        )

    def _por_que_no_se_pudo(self, tool_name: str) -> str:
        """Qué se le dice al modelo cuando una herramienta se deniega.

        No basta con «se denegó»: sin una salida, el modelo reintenta lo mismo o
        se rinde, y la persona se queda mirando un fallo que sí tenía solución.

        Cuando la denegación viene de que **no hay consola con la que confirmar**
        —la web, la API—, la salida existe y es planificar: proponer el trabajo,
        que la persona lo apruebe desde la interfaz y entonces ejecutarlo.

        Esto está aquí y no en el system prompt porque **el prompt no basta**. Se
        comprobó en el gate de la V1.6: con un modelo la instrucción funcionaba y
        con otro no, y el segundo iba directo a borrar. Un mensaje que llega en el
        momento del rechazo, dentro del bucle, no depende de que el modelo se
        acuerde de una regla que leyó al principio.
        """
        # El mensaje dice "denegó" siempre, sea cual sea el motivo: es la
        # palabra que el modelo tiene que entender para no reintentar lo mismo.
        base = f"No se pudo ejecutar '{tool_name}': se denegó el permiso."

        if getattr(self.permissions, "interactive", True):
            # Hay consola: alguien dijo que no, y eso es una respuesta, no un
            # obstaculo que rodear.
            return base + " La persona no lo autorizó."

        if self.tools.get("create_plan") is None:
            return base

        return (
            base + " Aquí no hay consola con la que preguntar en el momento. Usa "
            "'create_plan' con los pasos que quieres dar: la persona podrá "
            "aprobarlos desde la interfaz y después podrás ejecutarlos."
        )

    def _validate_args(self, tool, args: dict[str, Any]) -> str | None:
        """Valida los argumentos propuestos por el modelo contra el esquema de la herramienta."""
        return validate_tool_args(tool, args)

    def clear_history(self, session_id: str | None = None) -> None:
        """Limpia el historial de conversación en memoria de una sesión."""
        self.sessions.get(session_id).clear()

    def get_tools_info(self) -> list[dict]:
        """Devuelve información de las herramientas disponibles."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "permission_level": t.permission_level,
            }
            for t in self.tools.list_tools()
        ]
