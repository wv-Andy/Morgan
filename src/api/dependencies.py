"""
Inyección de dependencias para Morgan API (V1.0).

Proporciona acceso desacoplado y reutilizable a los subsistemas del Core de Morgan:
- Agent
- ToolRegistry (25 herramientas en 7 dominios)
- MemoryManager (SQLite)
- PermissionManager (5 niveles de riesgo)
- AuditLogger
"""

import logging
import threading
from typing import Optional

from src.config import get_settings
from src.logging_config import setup_logging
from src.agent.core import Agent
from src.health import ServiceHealth
from src.api.health_checks import register_default_checks
from src.agent.history import ConversationHistory
from src.memory.sqlite_repositories import SQLiteRepositoryFactory
from src.memory.supabase_repositories import SupabaseRepositoryFactory
from src.tools.multimodal import (
    AnalyzeImageTool,
    ListUploadsTool,
    ReadUploadTool,
    TranscribeAudioTool,
)
from src.identidad.cuotas import ControlDeUso
from src.tasks import TaskManager
from src.tools.tareas import task_tools
from src.tools.planificacion import plan_tools
from src.tools.conocimiento import knowledge_tools
from src.tasks.planificador import Planificador
from src.uploads import UploadStore
from src.uploads.almacenamiento import AlmacenSupabase
from src.memory.sync import SyncQueue, SyncWorker
from src.memory.manager import MemoryManager
from src.security.audit import AuditLogger
from src.security.permissions import PermissionManager
from src.tools.registry import ToolRegistry
from src.tools.system import SystemInfoTool
from src.tools.filesystem import (
    ListFilesTool,
    ReadFileTool,
    SearchFilesTool,
    CreateFileTool,
    CopyFileTool,
    MoveFileTool,
    RenameFileTool,
    DeleteFileTool,
)
from src.tools.terminal import (
    ExecuteCommandTool,
    GetProcessesTool,
    GetEnvironmentTool,
    KillProcessTool,
)
from src.tools.memory import (
    RememberFactTool,
    RecallMemoryTool,
    ForgetFactTool,
)
from src.tools.web import (
    SearchWebTool,
    ReadWebpageTool,
)
from src.tools.coding import (
    InspectProjectTool,
    SearchCodeTool,
    PatchFileTool,
    RunTestsTool,
)
from src.tools.git import (
    GitStatusTool,
    GitDiffTool,
    GitCommitTool,
)
from src.models.chain import build_provider_chain


logger = logging.getLogger(__name__)


class CoreContainer:
    """Contenedor de instancias singleton de Morgan Core."""

    def __init__(self):
        settings = get_settings()
        setup_logging(level=settings.log_level, log_dir=settings.log_dir)
        logger.info("Inicializando Morgan Core: %s", settings.redacted())
        self.audit_logger = AuditLogger()
        # Capa de repositorios: el Core habla con la interfaz, no con SQLite.
        self.repositories = SQLiteRepositoryFactory()
        # La memoria va sobre ESE repositorio, igual que en la nube. Antes el
        # Morgan local tenía su propia implementación con su propio SQL, y
        # `forget('1')` borraba el recuerdo de la fila 1 aunque ninguna clave
        # se llamara así. Ver `tests/test_memoria_unificada.py`.
        from src.memory.memoria_sobre_repositorio import MemoriaSobreRepositorio

        self.memory_manager = MemoryManager(
            MemoriaSobreRepositorio(self.repositories.memories, salud=self.repositories)
        )

        # --- Nube (V1.3) ---
        # SQLite sigue siendo la fuente de verdad en local; Supabase es el
        # destino de la sincronizacion. Solo en el entorno cloud pasa a ser el
        # almacen principal, porque alli no hay disco local persistente.
        self.remote_repositories = self._build_remote(settings)
        self.sync_queue = (
            SyncQueue(self.repositories.db)
            if settings.cloud_enabled and self.remote_repositories
            else None
        )
        self.sync_worker = (
            SyncWorker(self.sync_queue, self.remote_repositories)
            if self.sync_queue
            else None
        )

        if settings.is_cloud and self.remote_repositories:
            # En la nube no hay fichero local que sobreviva: el almacen es remoto.
            self.repositories = self.remote_repositories
            self.sync_queue = None
            self.sync_worker = None

            # La memoria persistente TAMBIEN. Sin esta linea seguia escribiendo
            # en el SQLite local, que en Render se borra en cada reinicio: todo
            # lo que Morgan recordaba se perdia a los quince minutos de no
            # usarlo, y sin dar ningun error. `POST /memory` respondia 200, la
            # lista los mostraba, y al dia siguiente no habia ninguno.
            self.memory_manager = MemoryManager(
                MemoriaSobreRepositorio(
                    self.repositories.memories, salud=self.remote_repositories
                )
            )
            logger.info("Entorno cloud: el almacenamiento principal es Supabase")

        self.conversation_history = (
            ConversationHistory(
                self.repositories,
                window=settings.history_window,
                sync_queue=self.sync_queue,
            )
            if settings.persist_history
            else None
        )
        # La API no tiene consola: las herramientas que requieren confirmación se
        # deniegan de forma explícita en lugar de bloquear el hilo HTTP.
        self.permission_manager = PermissionManager(
            audit_logger=self.audit_logger,
            interactive=False,
        )
        # Antes del registro: las herramientas de la V1.4 lo reciben.
        self.uploads = self._build_upload_store(settings)
        # Cupo diario por usuario. Morgan usa las claves de su dueno: con una
        # sola persona da igual, con varias una sola podria agotarlas en una
        # tarde. Se construye sobre los repositorios en uso, que en la nube
        # son Supabase.
        from src.identidad.cuotas import Cuotas

        self.uso = ControlDeUso(self.repositories, Cuotas.desde_configuracion())
        self.tasks = TaskManager(self.repositories.tasks)
        # El planificador necesita el registro de herramientas para saber el
        # riesgo real de cada paso, asi que se arma DESPUES de el.
        self.planificador = None
        # El conocimiento vive en la misma base pero NO es memoria: la
        # memoria viaja en cada prompt, esto se consulta cuando viene a
        # cuento. Ver docs/datos.md.
        self.conocimiento = self._build_conocimiento()
        self.tool_registry = self._build_tool_registry()
        self.planificador = Planificador(
            self.repositories.planes, self.tool_registry, self.audit_logger
        )
        for herramienta in plan_tools(self.planificador):
            self.tool_registry.register(herramienta)
        self.agent: Optional[Agent] = None
        self.mode: str = "normal"
        self.llm_status: str = "ok"
        self.llm_error: Optional[str] = None
        self._init_agent()

        # El reloj de las automatizaciones (4.14). Se arranca en el ciclo de vida de la app,
        # no aquí: un contenedor de pruebas no debe ponerse a ejecutar nada solo.
        from src.automatizacion.ejecutor import Reloj

        self.reloj = Reloj(self)

        # Estado por servicio (V1.3 §8). Se registra despues del agente para que
        # la comprobacion del LLM vea el proveedor ya construido.
        self.health = ServiceHealth()
        register_default_checks(self.health, self)

        # El propietario se establece al arrancar, una sola vez y de forma
        # idempotente. Va al final porque necesita los repositorios y el auditor
        # ya montados, y no puede impedir que Morgan arranque: una instalacion
        # sin propietario funciona igual, simplemente no tiene administracion.
        self._asegurar_propietario()

    def _asegurar_propietario(self) -> None:
        try:
            from src.identidad.propietario import asegurar_propietario
            from src.identidad.repositorio import repositorio_de_cuentas

            asegurar_propietario(
                repositorio_de_cuentas(self.repositories), self.audit_logger
            )
        except Exception:
            logger.warning(
                "No se pudo comprobar quién es el propietario", exc_info=True
            )

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        # System
        registry.register(SystemInfoTool())
        # Filesystem
        registry.register(ListFilesTool())
        registry.register(ReadFileTool())
        registry.register(SearchFilesTool())
        registry.register(CreateFileTool())
        registry.register(CopyFileTool())
        registry.register(MoveFileTool())
        registry.register(RenameFileTool())
        registry.register(DeleteFileTool())
        # Terminal
        registry.register(ExecuteCommandTool())
        registry.register(GetProcessesTool())
        registry.register(GetEnvironmentTool())
        registry.register(KillProcessTool())
        # Memory
        registry.register(RememberFactTool(memory_manager=self.memory_manager))
        registry.register(RecallMemoryTool(memory_manager=self.memory_manager))
        registry.register(ForgetFactTool(memory_manager=self.memory_manager))
        # Web
        ajustes = get_settings()
        registry.register(SearchWebTool(
            tavily_api_key=ajustes.tavily_api_key, serper_api_key=ajustes.serper_api_key,
            brave_api_key=ajustes.brave_search_api_key, orden=ajustes.buscadores))
        registry.register(ReadWebpageTool())
        # Coding
        registry.register(InspectProjectTool())
        registry.register(SearchCodeTool())
        registry.register(PatchFileTool())
        registry.register(RunTestsTool())
        # Git
        registry.register(GitStatusTool())
        registry.register(GitDiffTool())
        registry.register(GitCommitTool())

        # V1.4 — Archivos subidos por la web. No son locales: trabajan sobre lo
        # que llega por la interfaz, no sobre el disco, asi que siguen existiendo
        # en el entorno cloud.
        registry.register(ListUploadsTool(self.uploads))
        registry.register(ReadUploadTool(self.uploads))
        registry.register(AnalyzeImageTool(self.uploads, provider=None, uso=self.uso))
        registry.register(TranscribeAudioTool(self.uploads, uso=self.uso))

        # V1.5 — Tareas. Operan sobre la base de datos, no sobre el disco, asi
        # que existen igual en la nube.
        for herramienta in task_tools(self.tasks):
            registry.register(herramienta)

        # V1.8 — Conocimiento. Solo si hay donde guardarlo: ofrecer
        # herramientas que van a fallar es peor que no ofrecerlas.
        if self.conocimiento is not None:
            for herramienta in knowledge_tools(self.conocimiento, self.uploads):
                registry.register(herramienta)

        # V1.9 — GitHub. Al reves que el conocimiento: se registran SIEMPRE,
        # conectado o no. Si solo aparecieran con GitHub ya conectado, el modelo
        # no sabria que existen y nunca sugeriria conectarlo; sin conexion
        # responden diciendo donde se conecta, que es mas util que no estar.
        from src.tools.github import github_tools

        for herramienta in github_tools(self.repositories):
            registry.register(herramienta)

        # 4.14 — Las automatizaciones. En la base, como las tareas: existen igual en la nube.
        from src.automatizacion.repositorio import repositorio_de_automatizaciones
        from src.tools.automatizaciones import automation_tools

        # El registro, para validar los pasos fijos (4.15) con el catálogo real del turno.
        for herramienta in automation_tools(repositorio_de_automatizaciones(self.repositories), registry):
            registry.register(herramienta)

        # Google Calendar (V2.0.17) está APARCADO desde la 2.0.18, por decisión
        # mía: sus herramientas no se registran y el servicio no está en el
        # catálogo. Aparcado en la 2.0.18 y lo **descarté** el 2026-09-19.

        # En cloud, las herramientas que tocan la máquina se EXCLUYEN del
        # catálogo, no se dejan bloqueadas por permisos: lo que no está
        # registrado no puede invocarse ni aparece en el esquema que ve el
        # modelo. Cloud Morgan != acceso automático al ordenador (§15).
        if get_settings().is_cloud:
            excluidas = [t.name for t in registry.list_tools() if t.requires_local]
            for nombre in excluidas:
                registry.unregister(nombre)
            logger.info(
                "Entorno cloud: %d herramientas locales excluidas del catálogo (%s)",
                len(excluidas),
                ", ".join(excluidas),
            )
            # Y en su lugar, las del PC de cada persona a través de su agente local
            # (3.0-E): solo aparecen en el turno de quien lo tiene conectado. Lo que
            # tocan es SU PC, con SU política; este servidor sigue sin tocar nada suyo.
            from src.canal.herramientas import herramientas_del_equipo

            for herramienta in herramientas_del_equipo(self.uploads):
                registry.register(herramienta)

        return registry

    def _build_conocimiento(self):
        """El almacén de conocimiento, sobre el almacén que toque.

        Hasta la V1.9 solo existía la versión de SQLite, así que en la nube no
        había biblioteca y las herramientas de conocimiento **ni se
        registraban** — mejor que ofrecerlas y que fallaran, pero significaba
        que lo que Morgan aprendía en el escritorio no existía en la web.

        Las dos implementaciones comparten la **puntuación**, palabra por
        palabra. Lo que cambia es el filtro grueso, que es SQL y hay que decirlo
        en cada dialecto: en Supabase vive en una función de Postgres. Tener dos
        rankings distintos haría que la misma búsqueda ordenara distinto según
        dónde corriera Morgan, y eso es peor que no buscar.
        """
        client = getattr(self.repositories, "client", None)
        if client is not None:
            from src.conocimiento.almacen_supabase import AlmacenDeConocimientoSupabase

            logger.info("Conocimiento: Supabase")
            return AlmacenDeConocimientoSupabase(client)

        db = getattr(self.repositories, "db", None)
        if db is None:
            logger.info(
                "Sin almacén con el que hablar: el conocimiento no está disponible"
            )
            return None

        from src.conocimiento import AlmacenDeConocimiento

        return AlmacenDeConocimiento(db)

    def _build_upload_store(self, settings) -> UploadStore:
        """Elige donde viven los archivos subidos segun el entorno.

        En la nube el disco es efimero y el servicio se duerme, asi que guardar
        ahi significaba perder los archivos al primer rato de inactividad. Los
        bytes van a Supabase Storage y el indice a la misma base que el resto.
        """
        if settings.is_cloud and settings.has_supabase:
            try:
                almacen = AlmacenSupabase(
                    settings.supabase_url,
                    settings.supabase_secret_key or settings.supabase_key,
                )
                logger.info("Archivos subidos: Supabase Storage")
                return UploadStore(repositorio=self.repositories.uploads, almacen=almacen)
            except Exception as exc:
                # Sin almacenamiento remoto se sigue en disco: se perderan al
                # reiniciar, pero subir seguira funcionando dentro de la sesion.
                logger.warning(
                    "No se pudo usar Supabase Storage (%s); los archivos subidos "
                    "iran a disco y no sobreviviran a un reinicio", exc,
                )

        return UploadStore(repositorio=self.repositories.uploads)

    def _build_remote(self, settings):
        """Construye el proveedor remoto si hay credenciales y esta habilitado."""
        if not settings.has_supabase:
            return None
        if not (settings.cloud_enabled or settings.is_cloud):
            return None

        try:
            return SupabaseRepositoryFactory(
                settings.supabase_url,
                settings.supabase_secret_key or settings.supabase_key,
                device=settings.device_name,
            )
        except Exception as exc:
            # Sin nube Morgan funciona igual: es una mejora, no un requisito.
            logger.warning("No se pudo inicializar Supabase: %s", exc)
            return None


    def _init_agent(self) -> None:
        """Inicializa el modelo LLM con resiliencia y soporte de modo degradado."""
        try:
            settings = get_settings()
            model, activos = build_provider_chain(settings)

            if model is None:
                self.mode = "degraded"
                self.llm_status = "unavailable"
                self.llm_error = "Sin proveedor LLM configurado"
                logger.warning("Sin proveedor LLM configurado: Morgan arranca en modo degradado")
                return

            logger.info("Cadena de proveedores LLM activa: %s", ", ".join(activos))

            self.agent = Agent(
                model=model,
                tool_registry=self.tool_registry,
                permission_manager=self.permission_manager,
                conversation_history=self.conversation_history,
                tasks=self.tasks,
                # Sin esto el recorte del catálogo no ve los planes y quita
                # get_plan tras crear uno. Ver Agent._ATADAS_A_LA_CONVERSACION.
                planes=self.planificador,
            )

            # analyze_image necesita la cadena para poder pedir vision. Se inyecta
            # aqui y no en el constructor porque el registro se arma antes que el
            # modelo, y invertir ese orden obligaria a reordenar medio contenedor.
            analiza = self.tool_registry.get("analyze_image")
            if analiza is not None:
                analiza.provider = model

            # La memoria, por turno y de quien pregunta (4.0). NUNCA en el prompt compartido:
            # así se filtraba la de una persona a los turnos de otra.
            self.agent.memoria = self.memory_manager.get_context_summary
            # Los títulos de su conocimiento, por turno y de quien pregunta (4.1).
            if self.conocimiento is not None:
                from src.tools.conocimiento import indice_para_el_turno

                almacen = self.conocimiento
                self.agent.indice_conocimiento = lambda: indice_para_el_turno(almacen)
            # En la nube, si tiene un PC emparejado que ahora no está conectado (4.3).
            if settings.is_cloud:
                from src.identidad.agentes import ServicioDeAgentes, aviso_si_esta_desconectado
                from src.identidad.repositorio import repositorio_de_cuentas

                servicio_agentes = ServicioDeAgentes(repositorio_de_cuentas(self.repositories))
                self.agent.aviso_del_equipo = lambda: aviso_si_esta_desconectado(servicio_agentes)
            # El permiso automático de quien pregunta (4.6): lo verde y amarillo, sin esperar.
            from src.identidad import permiso_automatico
            from src.identidad.repositorio import repositorio_de_cuentas as _cuentas

            cuentas = _cuentas(self.repositories)
            self.agent.permiso_automatico = lambda: permiso_automatico.para_este_turno(cuentas)

            self.mode = "normal"
            self.llm_status = "ok"

        except Exception as e:
            self.mode = "degraded"
            self.llm_status = "error"
            self.llm_error = str(e)
            logger.exception("Fallo al inicializar el agente; Morgan queda en modo degradado")


# Instancia global reutilizable
_container: Optional[CoreContainer] = None
#: Uno solo, aunque lo pidan dos hilos a la vez. Medido en producción (2026-10-02, 03:18):
#: desde la 4.14 el reloj interno lo pide al arrancar a la vez que la primera petición; se
#: construían dos, los dos creaban la base SQLite («UNIQUE constraint failed:
#: schema_version») y la petición que caía ahí recibía 503 «no hay capa de cuentas».
_cerrojo_container = threading.Lock()


def get_container() -> CoreContainer:
    global _container
    if _container is None:
        with _cerrojo_container:
            if _container is None:
                _container = CoreContainer()
    return _container


def reset_container() -> None:
    """Permite reiniciar el contenedor (útil para pruebas)."""
    global _container
    _container = None

