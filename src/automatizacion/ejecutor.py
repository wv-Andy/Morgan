"""
El reloj y la ejecución de las automatizaciones (4.14).

**Quién llama**: el reloj de Supabase (`pg_cron`, cada minuto y solo si toca algo) a
`POST /automatizaciones/reloj`, y un reloj dentro del proceso que mira cada minuto mientras
el servidor está despierto (`arrancar`). Los dos acaban en `Reloj.tic`, y una ejecución
solo la lanza quien la **reclama** (`repositorio.reclamar`): nunca dos veces.

**Cómo se ejecuta** cada una, sin nadie delante (mis decisiones, docs/plan-4.x.md):

1. Como su dueño y **con su rol real** (`como_usuario` a secas daría USER: un administrador
   perdería lo suyo), en una conversación temporal que no queda en su historial.
2. Si necesita el PC y no está conectado, **espera hasta una hora** (decisión 4), mirando
   cada `REINTENTO_PC`; si no llega, se salta y se avisa.
3. Cuenta en su cupo diario; sin cupo, se salta y se avisa.
4. Solo con las herramientas de consulta (`contexto.PERMITIDAS`).
5. Lo que contó va a la bandeja. Tres fallos seguidos (el modelo, el tiempo, un error) la
   **pausan sola**, y se avisa. Saltarla (PC apagado, sin cupo) no es un fallo.
"""

import logging
import threading
import time

from src.automatizacion import horario
from src.automatizacion.contexto import en_automatizacion
from src.automatizacion.repositorio import nuevo_id, repositorio_de_automatizaciones

logger = logging.getLogger(__name__)

#: Mi decisión 4: con el PC apagado, se espera hasta una hora…
ESPERA_PC = 3600.0
#: …mirando cada diez minutos (cada vez despierta a Render: seis como mucho).
REINTENTO_PC = 600.0
#: Tres fallos seguidos la pausan (límite de partida aprobado).
MAX_FALLOS = 3
#: Cuántas se ejecutan por tic, como mucho; las demás, en el siguiente.
POR_TIC = 10
#: El texto de un aviso, como mucho.
MAX_TEXTO = 4000
#: Cada cuánto mira el reloj interno.
CADA = 60.0

PROMPT = (
    "[Automatización «{nombre}», programada {cuando}. Nadie está delante: no hagas preguntas "
    "ni propongas planes; haz lo que se pide solo consultando, y responde con un informe breve "
    "para la bandeja de avisos (qué miraste y qué encontraste; si algo no se pudo, por qué). "
    # Medido con el modelo real (4.14): unas noticias agotaron los pasos buscando y leyendo
    # páginas; y una orden imposible acabó en un informe con datos inventados («X archivos»).
    "Sé eficiente: una o dos búsquedas bastan, y no abras más de dos páginas. Nunca inventes "
    "datos: cuenta solo lo que te devolvieron las herramientas.]\n\n"
    "{instruccion}"
)


PROMPT_FIJO = (
    "[Automatización «{nombre}» con pasos fijos, programada {cuando}. Los ejecuta Morgan tal "
    "como la persona los aprobó.]"
)


class Reloj:
    def __init__(self, container):
        self.container = container
        self.repo = repositorio_de_automatizaciones(container.repositories)
        self._cerrojo = threading.Lock()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None

    # --- Quién llama ---

    def tic(self, ahora: float | None = None) -> int:
        """Ejecuta lo que toca ahora. Devuelve cuántas se ejecutaron (o se saltaron).
        Si ya hay un tic en marcha en este proceso, no hace nada: el otro las cogerá."""
        if not self._cerrojo.acquire(blocking=False):
            return 0
        try:
            hechas = 0
            for auto in self.repo.pendientes(ahora or time.time(), limite=POR_TIC):
                try:
                    if self.ejecutar(auto, time.time() if ahora is None else ahora):
                        hechas += 1
                except Exception:
                    logger.exception("La automatización %s falló al ejecutarse", auto.get("id"))
            return hechas
        finally:
            self._cerrojo.release()

    def tic_en_segundo_plano(self) -> bool:
        """Para la ruta del reloj: contesta ya y ejecuta en otro hilo."""
        if self._cerrojo.locked():
            return False
        threading.Thread(target=self.tic, name="morgan-reloj-tic", daemon=True).start()
        return True

    def arrancar(self) -> None:
        """El reloj interno: un hilo que mira cada minuto."""
        if self._hilo is not None:
            return

        def bucle() -> None:
            while not self._parar.wait(CADA):
                try:
                    self.tic()
                except Exception:
                    logger.exception("El reloj interno falló en un tic")

        self._hilo = threading.Thread(target=bucle, name="morgan-reloj", daemon=True)
        self._hilo.start()

    def parar(self) -> None:
        self._parar.set()

    # --- Una ejecución ---

    def ejecutar(self, auto: dict, ahora: float) -> bool:
        """Reclama y ejecuta una. False si otro se adelantó o si sigue esperando al PC."""
        siguiente = horario.siguiente(auto["horario"], auto["zona"], ahora)
        programada = auto.get("esperando_pc_desde") or auto["proxima"]

        if auto["necesita_pc"] and not self._pc_conectado(auto["user_id"]):
            if ahora - programada < ESPERA_PC:
                # A esperar (decisión 4). Se reclama igual: así no la coge otro tic.
                self.repo.reclamar(auto["id"], auto["reclamo"], {
                    "proxima": min(ahora + REINTENTO_PC, programada + ESPERA_PC),
                    "esperando_pc_desde": programada})
                return False
            if not self.repo.reclamar(auto["id"], auto["reclamo"], {
                    "proxima": siguiente, "esperando_pc_desde": None}):
                return False
            self._cerrar(auto, ahora, "saltada", (
                f"No se ejecutó: tu PC no se conectó en la hora siguiente a las "
                f"{self._hora(programada, auto['zona'])}. La próxima, {self._cuando(siguiente, auto['zona'])}."))
            return True

        if not self.repo.reclamar(auto["id"], auto["reclamo"], {"proxima": siguiente, "esperando_pc_desde": None}):
            return False
        # Ya es suya: pase lo que pase a partir de aquí, la bandeja lo cuenta. Sin esto, un
        # error inesperado tras reclamarla la perdía sin aviso (medido en sus pruebas).
        try:
            return self._ejecutar_reclamada(auto, ahora, programada)
        except Exception:
            logger.exception("La automatización %s falló tras reclamarla", auto["id"])
            self._cerrar(auto, ahora, "fallo",
                         "No se pudo ejecutar: hubo un error inesperado. Se volverá a intentar a su hora.")
            return True

    def _ejecutar_reclamada(self, auto: dict, ahora: float, programada: float) -> bool:
        cuenta = self._cuenta(auto["user_id"])
        if cuenta is None:
            # La cuenta ya no está o está bloqueada: no se ejecuta nada a su nombre.
            self.repo.anotar(auto["id"], {"activa": False, "ultimo_estado": "saltada", "ultima": ahora})
            return True

        from src.identidad import como_usuario
        from src.identidad.cuotas import CuotaAgotada

        with como_usuario(auto["user_id"], cuenta.rol_efectivo):
            try:
                self.container.uso.apuntar(auto["user_id"], "mensajes", cuenta.rol_efectivo)
            except CuotaAgotada:
                self._cerrar(auto, ahora, "saltada",
                             "No se ejecutó: hoy ya no te quedaba cupo de mensajes. Mañana vuelve a intentarlo.")
                return True
            texto, herramientas, fallo = self._turno(auto, programada)

        self._cerrar(auto, ahora, "fallo" if fallo else "hecha", texto, herramientas)
        return True

    def _turno(self, auto: dict, programada: float) -> tuple[str, list[str], str | None]:
        from src.observabilidad import midiendo

        agente = self.container.agent
        if agente is None:
            return "No se ejecutó: el modelo no estaba disponible.", [], "modelo"
        sesion = f"auto-{auto['id']}-{int(programada)}"
        if auto.get("pasos"):
            return self._turno_fijo(agente, auto, programada, sesion)
        mensaje = PROMPT.format(nombre=auto["nombre"], instruccion=auto["instruccion"],
                                cuando=self._cuando(programada, auto["zona"]))
        try:
            with midiendo() as medicion, en_automatizacion(auto):
                texto = agente.chat(mensaje, session_id=sesion, temporary=True)
            mensajes = agente.sessions.get(sesion).messages
            herramientas = sorted({m.tool_name for m in mensajes if m.role == "tool" and m.tool_name})
            fallo = medicion.datos.get("fallo")
            if not (texto or "").strip():
                fallo = fallo or "vacia"
            return (texto or "").strip() or "La ejecución no devolvió ningún texto.", herramientas, fallo
        except Exception:
            logger.exception("Fallo en el turno de la automatización %s", auto["id"])
            return "No se pudo ejecutar: hubo un error inesperado. Se volverá a intentar a su hora.", [], "error"
        finally:
            agente.sessions.drop(sesion)

    def _turno_fijo(self, agente, auto: dict, programada: float, sesion: str) -> tuple[str, list[str], str | None]:
        """Los pasos fijos (4.15, mis decisiones): un plan de esta ejecución, con `{fecha}`
        y `{hora}` ya puestas, aprobado con la aprobación que la persona dio al crearla, y el
        ejecutor de planes de siempre. El modelo no elige nada: si todo sale, ni se le llama;
        si algo falla, solo redacta el aviso, sin herramientas (`contexto.permitida`)."""
        from src.automatizacion.pasos import expandir

        planificador = self.container.planificador
        pasos = expandir(auto["pasos"], time.time(), auto["zona"])
        herramientas = [p["herramienta"] for p in pasos]
        plan = None
        try:
            plan = planificador.crear(f"Automatización «{auto['nombre']}»", pasos, session_id=sesion)
            if str(plan.estado) != "aprobado":
                planificador.aprobar(plan.id, quien=f"automatización «{auto['nombre']}» (aprobada al crearla)")
            mensaje = PROMPT_FIJO.format(nombre=auto["nombre"], cuando=self._cuando(programada, auto["zona"]))
            with en_automatizacion({**auto, "_pasos": pasos}):
                texto = agente.chat(mensaje, session_id=sesion, temporary=True, ejecutar_plan=plan.id)
            cerrado = planificador.obtener(plan.id)
            salio = cerrado is not None and str(cerrado.estado) == "completado"
            return (texto or "").strip() or "La ejecución no devolvió ningún texto.", herramientas, (
                None if salio else "plan")
        except Exception:
            logger.exception("Fallo en los pasos fijos de la automatización %s", auto["id"])
            return "No se pudo ejecutar: hubo un error inesperado. Se volverá a intentar a su hora.", herramientas, "error"
        finally:
            agente.sessions.drop(sesion)
            if plan is not None:
                try:
                    planificador.repositorio.delete(plan.id)     # era de esta ejecución, no de nadie
                except Exception:
                    logger.warning("No se pudo borrar el plan %s de una automatización", plan.id)

    def _cerrar(self, auto: dict, ahora: float, estado: str, texto: str, herramientas: list[str] | None = None) -> None:
        """El aviso a la bandeja y cómo quedó la automatización."""
        fallos = auto.get("fallos_seguidos", 0) + 1 if estado == "fallo" else (
            0 if estado == "hecha" else auto.get("fallos_seguidos", 0))
        valores = {"ultima": ahora, "ultimo_estado": estado, "fallos_seguidos": fallos}
        if fallos >= MAX_FALLOS:
            valores["activa"] = False
            texto += (f"\n\nHa fallado {fallos} veces seguidas, así que la he pausado. "
                      "Puedes reanudarla en Automatizaciones.")
        self.repo.anotar(auto["id"], valores)
        self.repo.crear_aviso({
            "id": nuevo_id("av"), "user_id": auto["user_id"], "automatizacion_id": auto["id"],
            "titulo": auto["nombre"], "texto": texto[:MAX_TEXTO], "estado": estado,
            "herramientas": herramientas or [], "leido": False, "creado_en": ahora})
        auditor = getattr(self.container, "audit_logger", None)
        if auditor is not None:
            auditor.log("automatizacion", "safe", True, {"id": auto["id"], "user_id": auto["user_id"],
                                                         "estado": estado}, success=estado != "fallo")

    # --- Lo que mira ---

    def _pc_conectado(self, user_id: str) -> bool:
        from src.canal.registro import REGISTRO

        return bool(REGISTRO.todas(user_id))

    def _cuenta(self, user_id: str):
        from src.identidad.cuentas import ServicioDeCuentas
        from src.identidad.repositorio import repositorio_de_cuentas

        repo = repositorio_de_cuentas(self.container.repositories)
        fila = repo.obtener(user_id)
        if not fila or str(fila.get("status") or "activo") != "activo":
            return None
        return ServicioDeCuentas._a_usuario(fila)

    @staticmethod
    def _hora(momento: float, zona: str) -> str:
        from datetime import datetime

        return datetime.fromtimestamp(momento, tz=horario.zona(zona)).strftime("%H:%M")

    @staticmethod
    def _cuando(momento: float, zona: str) -> str:
        from datetime import datetime

        local = datetime.fromtimestamp(momento, tz=horario.zona(zona))
        return f"el {horario.DIAS[local.weekday()]} {local:%d/%m} a las {local:%H:%M}"
