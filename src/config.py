"""
Configuración central de Morgan.

Antes cada módulo leía `os.getenv` por su cuenta y `load_dotenv()` se invocaba en
cuatro sitios distintos. Eso hacía imposible saber qué configuración estaba activa y
provocaba que un valor mal escrito (`MORGAN_API_PORT=abc`) reventara con un
`ValueError` crudo en mitad del arranque.

Aquí la configuración se carga una vez, se valida, y los valores inválidos caen al
valor por defecto dejando un aviso en el log en lugar de abortar el proceso.
"""

import logging
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# Valores de marcador de posición del .env.example: se tratan como "no configurado".
PLACEHOLDER_KEYS = {
    "tu-groq-api-key-aqui",
    "tu-gemini-api-key-aqui",
    "tu-api-key-aqui",
    "",
}

VALID_PERMISSION_MODES = ("ask", "auto")
VALID_ENVIRONMENTS = ("local", "cloud")

#: Los proveedores de modelo que EXISTEN en el codigo, que no es lo mismo que
#: los que estan activos por defecto.
#:
#: La distincion aparecio al quitar NVIDIA del orden por defecto: la lista
#: blanca de `MORGAN_LLM_ORDER` era el propio `Settings.llm_order`, asi que un
#: proveedor fuera del orden por defecto pasaba a ser un nombre "desconocido" y
#: NO SE PODIA VOLVER A ACTIVAR. Su codigo seguia ahi, entero y con pruebas, e
#: inalcanzable.
#:
#: Lo cazo una prueba que comprobaba justo eso —que quitarlo del orden no es
#: borrarlo— y es la razon de que esta constante exista aparte.
PROVEEDORES_CONOCIDOS = ("groq", "gemini", "nvidia", "openai")


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s='%s' no es un entero válido; se usa %d", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("%s=%d es menor que el mínimo (%d); se usa %d", name, value, minimum, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("%s=%d supera el máximo (%d); se usa %d", name, value, maximum, maximum)
        return maximum
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _env_any_case(name: str) -> str:
    """Lee una variable probando mayúsculas y minúsculas.

    En Windows `os.environ` no distingue, pero en Linux sí. Las claves de
    Supabase suelen pegarse del panel en minúsculas, así que se aceptan ambas.
    """
    for variante in (name, name.upper(), name.lower()):
        valor = os.environ.get(variante)
        if valor:
            return valor.strip()
    return ""


def _env_key(name: str) -> str | None:
    """Devuelve la API key sólo si está realmente configurada (no es un marcador)."""
    raw = _env_any_case(name)
    return None if raw in PLACEHOLDER_KEYS else raw


#: Cuántas claves numeradas se buscan por proveedor, como máximo.
#:
#: El tope existe para que el arranque no recorra variables sin fin, y porque
#: pasado cierto punto la cuenta que hay que administrar es más trabajo que el
#: que ahorra. Cinco claves de Groq son un millón de tokens al día.
MAXIMO_CLAVES_POR_PROVEEDOR = 5


def _env_keys(name: str) -> tuple[str, ...]:
    """Todas las claves de un proveedor: `GROQ_API_KEY`, `GROQ_API_KEY_2`...

    **Variables numeradas y no una lista separada por comas**, y hay motivo
    medido: al recrear el servicio de producción hubo que teclear 18 secretos y
    tres salieron mal. Un campo por clave aísla el error —una mal copiada no
    arrastra a las demás— y `claves.py` puede decir *cuál* variable revisar,
    que es lo que necesita quien va a arreglarlo en el panel de Render.

    Se para en el primer hueco: si hay `_2` y `_4` pero no `_3`, se queda con
    dos y lo dice. Seguir adelante escondería una variable mal nombrada, que es
    justo el fallo que esto pretende hacer visible.
    """
    claves: list[str] = []
    vistas: set[str] = set()

    for n in range(1, MAXIMO_CLAVES_POR_PROVEEDOR + 1):
        variable = name if n == 1 else f"{name}_{n}"
        clave = _env_key(variable)
        if not clave:
            # Un hueco corta la serie, pero se avisa si había algo detrás: es la
            # señal de una variable mal nombrada.
            siguientes = [
                f"{name}_{m}"
                for m in range(n + 1, MAXIMO_CLAVES_POR_PROVEEDOR + 1)
                if _env_key(f"{name}_{m}")
            ]
            if siguientes:
                logger.warning(
                    "%s no está definida, así que %s NO se usa. Las claves se "
                    "numeran seguidas: %s, %s_2, %s_3...",
                    variable, ", ".join(siguientes), name, name, name,
                )
            break

        if clave in vistas:
            # Repetir la misma clave no da más cuota: es la misma cuenta. Y
            # parecería que sí, que es lo peligroso.
            logger.warning(
                "%s tiene el mismo valor que una clave anterior de %s. No se "
                "usa dos veces: la cuota es de la cuenta, no de la variable.",
                variable, name,
            )
            continue

        vistas.add(clave)
        claves.append(clave)

    return tuple(claves)


@dataclass(frozen=True)
class Settings:
    """Configuración efectiva de Morgan."""

    # --- Proveedores LLM ---
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    # Groq admite VARIAS claves, y no es un capricho: son cuentas distintas con
    # cuotas distintas, y eso es lo unico que sube el techo de capacidad.
    #
    # Medido antes de escribir una linea, porque la pregunta decidia si esto
    # servia de algo. Tres llamadas alternando las dos claves, mirando el
    # contador de peticiones que Groq devuelve en cada respuesta:
    #
    #     vieja: 999 -> 998 -> 997     nueva: 999 -> 998
    #
    # Si el cubo fuera compartido, la nueva habria leido 996. Leyo 999, asi que
    # son cubos separados: 400.000 tokens y 2.000 peticiones al dia en lugar de
    # 200.000 y 1.000. El contador de TOKENS no servia para decidirlo —se
    # rellena en 570 ms, asi que entre dos llamadas se disimula— y el de
    # peticiones si, porque su ventana es de un dia.
    #
    # `groq_api_key` sigue siendo la primera de la lista, para que nada de lo
    # que ya la leia tenga que cambiar.
    groq_api_keys: tuple[str, ...] = ()

    # Relevos por capacidad (V2.0.20, el router del roadmap maestro).
    #
    # **La cuota de Groq es por MODELO, no solo por cuenta.** Medido el
    # 2026-09-16 con los contadores de cada respuesta: 120b 996, luego 20b 998 y
    # 997, y 120b otra vez 995. Si el cupo fuera comun, 120b habria leido 993.
    # Asi que cada modelo es otro cubo de 200.000 tokens al dia y 8.000 por
    # minuto, con las mismas claves y sin pagar nada.
    #
    # Un relevo solo entra cuando el modelo principal rechaza **por cuota**. No
    # es un router por tipo de tarea: eso espera a la evaluacion de calidad
    # de los modelos. Por que gpt-oss-20b y no otro, medido en
    # docs/modelos.md: qwen3.8-27b falla siempre en la cuenta
    # gratuita (1.000 tokens de salida por minuto; Morgan pide 2.048).
    #
    # `GROQ_MODELOS_RELEVO=` vacio los desactiva.
    groq_modelos_relevo: tuple[str, ...] = ("openai/gpt-oss-20b",)
    gemini_api_key: str | None = None
    #: Varias claves de Gemini (4.1.5, decisión mía): `GEMINI_API_KEY`, `_2`… Cada una
    #: de un proyecto distinto es otro cupo: la capa gratuita da 20 peticiones al día por
    #: proyecto y modelo, y Gemini es el único con visión. Ver `src/models/llavero.py`.
    gemini_api_keys: tuple[str, ...] = ()
    gemini_model: str = "gemini-3.6-flash"
    #: El buscador de `search_web` (4.5, decisión mía). DuckDuckGo no contesta a los
    #: servidores de Render (medido en producción, 2026-09-30: cada búsqueda agotaba sus
    #: 12 s; desde un PC, 1,1 s). Tavily: 1.000 búsquedas al mes gratis, sin tarjeta. Sin
    #: clave, se sigue usando solo DuckDuckGo, que en local funciona.
    tavily_api_key: str | None = None
    #: Google (vía Serper) y Brave (4.8, pedido por mí: «que busque con Google y otros
    #: motores»). Cada uno, solo si tiene su clave. `buscadores` es el orden; DuckDuckGo no
    #: necesita clave y va el último. Ver `src/tools/web.py`.
    serper_api_key: str | None = None
    brave_search_api_key: str | None = None
    buscadores: tuple[str, ...] = ("google", "tavily", "brave", "duckduckgo")
    # Orden de la cadena de respaldo. Cuál conviene primero depende de las claves
    # que tengas, de su saldo y de su latencia; por eso es configurable.
    # NVIDIA NIM (V1.4): segundo eslabon. Admite herramientas, verificado contra
    # el servicio real, asi que es un respaldo de pleno derecho y no solo para
    # conversar.
    nvidia_api_key: str | None = None
    nvidia_model: str = "deepseek-ai/deepseek-v4-pro-0813"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # OpenAI (V2.0.3): el UNICO proveedor de pago, y el ultimo de la cadena.
    #
    # No va al final por ser peor —medido, es el mas fiable de los cuatro: 1,0 s
    # de texto y 1,7 s con herramientas, sin un solo fallo— sino porque mientras
    # quede cuota gratis no hay razon para pagar. Asi el dinero solo se gasta
    # cuando Groq y Gemini se agotan, que es exactamente cuando Morgan dejaba de
    # funcionar: la auditoria midio 20 turnos que tardaban 90 s en no contestar.
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_base_url: str = "https://api.openai.com/v1"

    # --- El freno de mano del gasto ---
    #
    # Tope de salida por llamada. Es un limite de FACTURA, no de calidad: una
    # respuesta cortada se ve y se puede subir, una factura no se puede bajar.
    openai_max_salida: int = 2048

    # Tope de tokens por ventana, contando entrada y salida. Al alcanzarlo el
    # proveedor se niega a llamar y la cadena sigue su camino.
    #
    # Vive EN MEMORIA, y conviene saber lo que eso significa: un reinicio lo
    # pone a cero, y el plan gratuito de Render reinicia a menudo. Asi que no es
    # un limite de gasto, es un freno contra lo que de verdad da miedo —un bucle
    # que se desboca y hace mil llamadas en diez minutos—. El limite que no se
    # puede saltar es el del panel de OpenAI, y ese solo lo pone su dueno.
    openai_tope_tokens: int = 200_000
    openai_ventana_segundos: int = 86_400

    # El orden importa y sale de medir, no de suponer. NVIDIA va ULTIMO porque
    # su latencia es impredecible: el mismo modelo y la misma peticion han dado
    # 2,8 s, 18 s, 23 s y mas de 180 s en la misma tarde. Con `llm_timeout` en
    # 30 s eso significa que unas veces contesta y otras se come el plazo
    # entero sin dar nada, y entonces el turno paga medio minuto de nada antes
    # de conmutar.
    #
    # Estaba en segundo lugar, y con la cuota de Groq agotada eso hacia que
    # cada turno costara 64 s: Groq 0,4 s (429) + NVIDIA 30 s (plazo agotado) +
    # Gemini ~34 s. Gemini contesta de forma estable, asi que va antes.
    llm_order: tuple[str, ...] = ("groq", "gemini", "openai")

    # --- Permisos ---
    moderate_permission_mode: str = "ask"

    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = False
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    # Los despliegues de vista previa de Vercel estrenan dominio cada vez, asi que
    # enumerarlos uno a uno no escala: una expresion regular los cubre todos.
    cors_origin_regex: str | None = None

    # --- Archivos subidos (V1.4) ---
    # Los limites son configurables porque dependen del entorno: en la nube, un
    # plan gratuito con poca memoria no aguanta lo mismo que un PC.
    upload_max_bytes: int = 20 * 1024 * 1024      # 20 MB
    upload_ttl_hours: int = 24                     # se borran solos al dia
    # Hasta ahora solo habia limite POR archivo: nada impedia subir mil de 19 MB
    # y llenar el disco, o la cuota de Supabase Storage, sin pasarse en ninguno.
    upload_max_archivos: int = 50
    upload_max_total_mb: int = 200
    # No hay limite de duracion de audio: medirla exigiria otra dependencia, y un
    # limite que no se aplica es peor que no tenerlo. El tamano ya lo acota.

    # --- Persistencia y logging ---
    db_timeout: int = 10
    log_level: str = "INFO"
    log_dir: Path = field(default_factory=lambda: BASE_DIR / "logs")
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")

    # --- Entorno de ejecución (V1.3 §3) ---
    # 'local': Morgan corre en el equipo del usuario y expone todas sus
    #          herramientas, incluidas archivos, terminal y procesos.
    # 'cloud': Morgan corre en un servidor. Las herramientas que tocan la máquina
    #          quedan FUERA del catálogo, no solo bloqueadas por permisos.
    environment: str = "local"

    # --- Nube (V1.3) ---
    # Desactivada por defecto: los datos personales no salen del equipo salvo
    # que el usuario lo pida explícitamente.
    cloud_enabled: bool = False
    supabase_url: str | None = None
    supabase_key: str | None = None          # publicable, para clientes
    supabase_secret_key: str | None = None   # de servicio, solo backend
    device_name: str = "local"

    # --- Interfaz web ---
    serve_web: bool = True

    # --- Autenticación de la API (opcional) ---
    api_token: str | None = None

    # --- Las automatizaciones (4.14) ---
    #: El secreto con el que llama el reloj de Supabase (`/automatizaciones/reloj`). Sin él,
    #: esa ruta no existe: nadie de fuera puede lanzar ejecuciones.
    reloj_secreto: str | None = None
    #: Además, un reloj dentro del proceso que mira cada minuto. En local es el único; en la
    #: nube cubre lo que toque mientras Render está despierto (dormido, no corre).
    reloj_interno: bool = True
    #: Que un error del servidor llegue a la bandeja del propietario (4.20), agrupado y
    #: con tope. Sin esto, un error solo se veía leyendo los registros de Render.
    avisar_errores: bool = True

    # --- Cuentas de usuario (identidad, V2.0 adelantada) ---
    # Si Morgan exige iniciar sesión. El valor por defecto depende del entorno y
    # se resuelve en `load_settings`: en la nube, sí; en local, no.
    #
    # No es un capricho: el Morgan de escritorio corre en TU equipo con TUS
    # claves, y obligarte a inventar una contraseña para hablar con tu propio
    # ordenador no protege de nada. En la nube es exactamente al revés.
    require_auth: bool = False

    # Si cualquiera puede crear una cuenta. Abierto por defecto: un Morgan en la
    # web al que nadie puede registrarse no sirve de nada, y la cuota diaria
    # limita el dano de que entre gente de mas.
    #
    # Cerrarlo es la opcion para un Morgan de circulo pequeno: te registras tu,
    # lo cierras, y a partir de ahi las cuentas se crean a mano.
    registro_abierto: bool = True

    # Tokens personales de API (plan de la API, fase 1): lo que usa un cliente
    # que no es el navegador. Activos por defecto (aprobado el 2026-09-17, para
    # todas las cuentas). Apagarlo es el freno de emergencia: los tokens que
    # existan dejan de valer al momento, sin borrarlos, y no se crean más.
    tokens_api: bool = True

    # Cupo global diario entre todas las cuentas sujetas a cupo (V2.0.24). El
    # propietario no cuenta. 0 = sin tope. Por qué estos números: docstring de
    # `Cuotas` en src/identidad/cuotas.py.
    cupo_global_mensajes: int = 150
    cupo_global_transcripciones: int = 60
    cupo_global_imagenes: int = 15

    # --- Historial persistente ---
    persist_history: bool = True
    history_window: int = 20

    # --- Proveedores LLM ---
    # Medido: la latencia normal de Groq es ~0,42 s; bajo peticiones concurrentes
    # se han observado hasta 44 s. 30 s deja margen de sobra sin permitir que una
    # llamada colgada consuma minutos. Con un proveedor de respaldo detrás, un
    # reintento basta: reintentar dos veces solo multiplica la espera.
    llm_timeout: int = 30
    llm_max_retries: int = 1

    # --- Agente ---
    # Tope de reloj para un turno completo. Sin él, seis iteraciones con reintentos
    # y conmutación de proveedor podían bloquear una petición más de 15 minutos.
    # Medido: un encargo de investigacion de tres pasos —buscar, leer una pagina y
    # resumir— tarda entre 126 y 135 s. Con 120 s se cortaba justo antes de
    # terminar y la tarea quedaba a medias. El cliente web espera 240 s, asi que
    # queda margen para que el aviso llegue en vez de agotarse tambien.
    turn_timeout: int = 180

    # En la nube manda otro reloj que no es nuestro. La web llega a la API por el
    # proxy del borde de Vercel —hace falta para que la cookie de sesion no sea
    # de terceros, ver docs/web.md (defecto 9 de producción)— y ese proxy corta a
    # los 120 s con un 502 suyo: «ROUTER_EXTERNAL_TARGET_ERROR». Medido en
    # produccion el 2026-09-09: dos cortes a 120.1 s y un exito a 104.7 s.
    #
    # Con 180 s aqui, un turno de entre 120 y 180 s no le llega nunca a nadie:
    # el borde ya respondio esa pagina de error, que no dice de que va ni sugiere
    # nada. Rindiendose antes, el que se acaba primero es Morgan, y entonces
    # contesta EL, explicando que ha parado por tiempo. **No se gana tiempo de
    # trabajo: se gana que el aviso sea de quien sabe lo que estaba haciendo.**
    #
    # De donde sale el numero: 85 + 30 = 115, por debajo de 120. El 30 es
    # `llm_timeout`, que es lo que puede durar el paso mas largo que empiece
    # justo antes de cumplirse el plazo. Porque el plazo se mira ENTRE pasos, no
    # dentro de uno: un paso que ya arranco se termina. Con 110 se probo, y un
    # turno murio igual en el proxy a los 120.1 s.
    #
    # No es una garantia absoluta y conviene no venderla como tal: con
    # reintentos y conmutacion de proveedor, una sola llamada al modelo puede
    # pasar de 30 s. Cubre el caso normal, no el patologico.
    #
    # El precio esta medido y hay que decirlo: el encargo de investigacion de
    # tres pasos, ese de 126-135 s, ya no cabe por la web. Por el proxy tampoco
    # cabia con 180 —moria en el 502— asi que lo que se pierde es la ilusion,
    # no la capacidad. Sigue cabiendo en el Morgan local, que no pasa por ahi.
    #
    # Se quita del todo con un dominio propio: con la web y la API bajo el mismo
    # dominio registrable la cookie ya no seria de terceros, no haria falta el
    # proxy, y este tope desapareceria. Esta en docs/roadmap-2.0.md.
    turn_timeout_nube: int = 85

    # El tope del turno en `/chat/stream` (V2.0.14). Todo lo de arriba sobre los
    # 85 s existe porque `/chat` no dice nada hasta el final, y el proxy corta
    # una respuesta callada a los 120. El streaming habla: emite un latido cada
    # 10 s, y la sonda `/diagnostico/goteo?segundos=180` demostró el 2026-09-11
    # que una respuesta que emite bytes llega entera a los 180 s por el proxy.
    #
    # 170 y no más porque **180 es lo medido**. Subirlo exige antes lanzar la
    # sonda con `segundos=300`; decidido así al aprobar
    # docs/agente.md (decisión A). Con 170 cabe el encargo de tres
    # pasos de 106-145 s, que por `/chat` no cabe.
    #
    # En local vale lo mismo que `turn_timeout`: no hay proxy.
    turn_timeout_stream_nube: int = 170

    max_iterations: int = 6

    # Tope duro de lo que puede tardar UNA respuesta HTTP. Cero: sin tope.
    #
    # `turn_timeout` no basta y hay medicion que lo demuestra. Ese plazo se mira
    # ENTRE pasos, asi que no acota lo que tarda un paso que ya arranco: con el
    # turno en 85 s, tres de cinco peticiones seguidas murieron igual en el
    # proxy a los 120.1 s. Una busqueda lenta o una llamada al modelo con
    # reintento y conmutacion de proveedor se comen el margen sin preguntar.
    #
    # Esto es de otra naturaleza: no le pide al agente que se dé prisa, deja de
    # esperarle. A los 100 s la peticion contesta, y el turno **sigue vivo en su
    # hilo**. Cuando acabe, `_persist` guarda la respuesta como siempre, y quien
    # recargue la conversacion la encuentra ahi. Se prefiere eso a matar el
    # trabajo: el turno ya esta pagado al proveedor.
    #
    # 100 y no 119 porque la respuesta tambien tiene que viajar. En local vale
    # cero: no hay proxy, y esperar a que Morgan termine es lo que se quiere.
    http_deadline: int = 0
    http_deadline_nube: int = 100
    turn_timeout_stream: int = 180

    @property
    def is_cloud(self) -> bool:
        return self.environment == "cloud"

    @property
    def has_supabase(self) -> bool:
        """Si hay credenciales suficientes para hablar con Supabase."""
        return bool(self.supabase_url and (self.supabase_secret_key or self.supabase_key))

    @property
    def has_llm(self) -> bool:
        return bool(self.groq_api_key or self.gemini_api_key)

    def redacted(self) -> dict:
        """Vista de la configuración apta para logs: nunca expone las claves."""
        return {
            # El NUMERO de claves, nunca las claves. Saber que hay dos es lo
            # que permite entender de donde sale la cuota; saber cuales no le
            # hace falta a nadie que lea un registro.
            "groq_api_key": (
                f"{len(self.groq_api_keys)} configurada(s)"
                if self.groq_api_keys else "ausente"
            ),
            "gemini_api_key": "configurada" if self.gemini_api_key else "ausente",
            "tavily_api_key": "configurada" if self.tavily_api_key else "ausente",
            "serper_api_key": "configurada" if self.serper_api_key else "ausente",
            "brave_search_api_key": "configurada" if self.brave_search_api_key else "ausente",
            "buscadores": list(self.buscadores),
            "nvidia_api_key": "configurada" if self.nvidia_api_key else "ausente",
            "nvidia_model": self.nvidia_model,
            "groq_model": self.groq_model,
            "gemini_model": self.gemini_model,
            "moderate_permission_mode": self.moderate_permission_mode,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "cors_origins": list(self.cors_origins),
            "cors_origin_regex": self.cors_origin_regex,
            "upload_max_mb": self.upload_max_bytes // (1024 * 1024),
            "upload_ttl_hours": self.upload_ttl_hours,
            "upload_max_archivos": self.upload_max_archivos,
            "upload_max_total_mb": self.upload_max_total_mb,
            "log_level": self.log_level,
            "api_token": "configurado" if self.api_token else "ausente",
            "reloj_secreto": "configurado" if self.reloj_secreto else "ausente",
            "reloj_interno": self.reloj_interno,
            "avisar_errores": self.avisar_errores,
            "require_auth": self.require_auth,
            "registro_abierto": self.registro_abierto,
            "tokens_api": self.tokens_api,
            "serve_web": self.serve_web,
            "environment": self.environment,
            "cloud_enabled": self.cloud_enabled,
            "supabase_url": "configurada" if self.supabase_url else "ausente",
            "supabase_secret_key": "configurada" if self.supabase_secret_key else "ausente",
            "device_name": self.device_name,
        }


def _normalizar_origen(bruto: str) -> str | None:
    """Limpia un origen tal y como se pega en el panel de un servicio de hosting.

    Un origen CORS tiene que casar **exactamente** con lo que envia el navegador:
    esquema, host y puerto, sin barra final. Tres descuidos habituales lo rompen y
    ninguno produce un error legible, solo un "API desconectada" sin pistas:

      "https://app.vercel.app"   comillas arrastradas al copiar
      https://app.vercel.app/    barra final
      app.vercel.app             sin esquema

    Los tres se corrigen aqui, dejando constancia en el log de lo que se cambio.
    """
    limpio = bruto.strip()
    if not limpio:
        return None

    original = limpio

    # Comillas que arrastra un copiar y pegar.
    for comilla in ('"', "'"):
        if len(limpio) >= 2 and limpio.startswith(comilla) and limpio.endswith(comilla):
            limpio = limpio[1:-1].strip()

    limpio = limpio.rstrip("/")

    if "://" not in limpio:
        # Un host pelado nunca casaria; la intencion es inequivoca.
        limpio = f"https://{limpio}"

    if limpio != original:
        logger.warning(
            "MORGAN_CORS_ORIGINS: se normalizo '%s' como '%s'. El origen debe "
            "coincidir exactamente con el que envia el navegador.",
            original,
            limpio,
        )

    return limpio or None


def load_settings() -> Settings:
    """Lee el entorno (y el .env) y construye la configuración validada."""
    load_dotenv()

    mode = (os.getenv("MODERATE_PERMISSION_MODE") or "ask").strip().lower()
    if mode not in VALID_PERMISSION_MODES:
        logger.warning(
            "MODERATE_PERMISSION_MODE='%s' no es válido (%s); se usa 'ask'",
            mode,
            " | ".join(VALID_PERMISSION_MODES),
        )
        mode = "ask"

    environment = (os.getenv("MORGAN_ENVIRONMENT") or "local").strip().lower()
    if environment not in VALID_ENVIRONMENTS:
        logger.warning(
            "MORGAN_ENVIRONMENT='%s' no es válido (%s); se usa 'local'",
            environment,
            " | ".join(VALID_ENVIRONMENTS),
        )
        environment = "local"

    raw_orden = _env_any_case("MORGAN_LLM_ORDER")
    if raw_orden:
        pedidos = [p.strip().lower() for p in raw_orden.split(",") if p.strip()]
        validos = [p for p in pedidos if p in PROVEEDORES_CONOCIDOS]
        if len(validos) != len(pedidos):
            logger.warning(
                "MORGAN_LLM_ORDER contiene proveedores desconocidos; se usan %s",
                ", ".join(validos) or "el orden por defecto",
            )
        llm_order = tuple(validos) if validos else Settings.llm_order
    else:
        llm_order = Settings.llm_order

    raw_origins = (os.getenv("MORGAN_CORS_ORIGINS") or "").strip()
    if raw_origins:
        origins = tuple(
            limpio
            for bruto in raw_origins.split(",")
            if (limpio := _normalizar_origen(bruto))
        )
        if not origins:
            logger.warning(
                "MORGAN_CORS_ORIGINS no contiene ningun origen utilizable; "
                "se usan los de desarrollo local"
            )
            origins = Settings.cors_origins
    else:
        origins = Settings.cors_origins

    origin_regex = (os.getenv("MORGAN_CORS_ORIGIN_REGEX") or "").strip() or None
    if origin_regex:
        try:
            re.compile(origin_regex)
        except re.error as exc:
            logger.warning(
                "MORGAN_CORS_ORIGIN_REGEX no es una expresion regular valida (%s); "
                "se ignora",
                exc,
            )
            origin_regex = None

    # Las dos salen de la misma lectura para que no puedan discrepar: la
    # primera de la lista ES `groq_api_key`.
    claves_groq = _env_keys("GROQ_API_KEY")
    claves_gemini = _env_keys("GEMINI_API_KEY")

    # Definida pero vacia significa «sin relevos»; sin definir, los de serie.
    raw_relevos = os.getenv("GROQ_MODELOS_RELEVO")
    if raw_relevos is None:
        relevos_groq = Settings.groq_modelos_relevo
    else:
        relevos_groq = tuple(dict.fromkeys(
            m.strip() for m in raw_relevos.split(",") if m.strip()
        ))

    return Settings(
        groq_api_key=claves_groq[0] if claves_groq else None,
        groq_api_keys=claves_groq,
        groq_modelos_relevo=relevos_groq,
        groq_model=(os.getenv("GROQ_MODEL_NAME") or Settings.groq_model).strip(),
        nvidia_api_key=_env_key("NVIDIA_API_KEY"),
        nvidia_model=(os.getenv("NVIDIA_MODEL_NAME") or Settings.nvidia_model).strip(),
        nvidia_base_url=(os.getenv("NVIDIA_BASE_URL") or Settings.nvidia_base_url).strip(),
        openai_api_key=_env_key("OPENAI_API_KEY"),
        openai_model=(os.getenv("OPENAI_MODEL_NAME") or Settings.openai_model).strip(),
        openai_base_url=(os.getenv("OPENAI_BASE_URL") or Settings.openai_base_url).strip(),
        openai_max_salida=_env_int(
            "MORGAN_OPENAI_MAX_SALIDA", Settings.openai_max_salida,
            minimum=64, maximum=32_768,
        ),
        openai_tope_tokens=_env_int(
            "MORGAN_OPENAI_TOPE_TOKENS", Settings.openai_tope_tokens,
            minimum=0, maximum=100_000_000,
        ),
        openai_ventana_segundos=_env_int(
            "MORGAN_OPENAI_VENTANA_SEGUNDOS", Settings.openai_ventana_segundos,
            minimum=60, maximum=2_592_000,
        ),
        gemini_api_key=claves_gemini[0] if claves_gemini else None,
        gemini_api_keys=claves_gemini,
        gemini_model=(os.getenv("MODEL_NAME") or Settings.gemini_model).strip(),
        tavily_api_key=_env_key("TAVILY_API_KEY"),
        serper_api_key=_env_key("SERPER_API_KEY"),
        brave_search_api_key=_env_key("BRAVE_SEARCH_API_KEY"),
        buscadores=tuple(b.strip().lower() for b in (os.getenv("MORGAN_BUSCADORES") or "").split(",")
                         if b.strip()) or Settings.buscadores,
        llm_order=llm_order,
        moderate_permission_mode=mode,
        api_host=(os.getenv("MORGAN_API_HOST") or Settings.api_host).strip(),
        # Render, Railway y Fly imponen el puerto con la variable PORT. Se respeta
        # MORGAN_API_PORT si esta definida, para no cambiar el arranque en local.
        api_port=_env_int(
            "MORGAN_API_PORT" if os.getenv("MORGAN_API_PORT") else "PORT",
            Settings.api_port,
            minimum=1,
            maximum=65535,
        ),
        api_reload=_env_bool("MORGAN_API_RELOAD", False),
        cors_origins=origins,
        cors_origin_regex=origin_regex,
        upload_max_bytes=_env_int(
            "MORGAN_UPLOAD_MAX_MB", Settings.upload_max_bytes // (1024 * 1024),
            minimum=1, maximum=200,
        ) * 1024 * 1024,
        upload_ttl_hours=_env_int(
            "MORGAN_UPLOAD_TTL_HOURS", Settings.upload_ttl_hours, minimum=1, maximum=720
        ),
        upload_max_archivos=_env_int(
            "MORGAN_UPLOAD_MAX_ARCHIVOS", Settings.upload_max_archivos,
            minimum=1, maximum=1000,
        ),
        upload_max_total_mb=_env_int(
            "MORGAN_UPLOAD_MAX_TOTAL_MB", Settings.upload_max_total_mb,
            minimum=1, maximum=5000,
        ),
        db_timeout=_env_int("MORGAN_DB_TIMEOUT", Settings.db_timeout, minimum=1, maximum=120),
        log_level=(os.getenv("MORGAN_LOG_LEVEL") or Settings.log_level).strip().upper(),
        log_dir=Path(os.getenv("MORGAN_LOG_DIR") or (BASE_DIR / "logs")),
        data_dir=Path(os.getenv("MORGAN_DATA_DIR") or (BASE_DIR / "data")),
        environment=environment,
        cloud_enabled=_env_bool("MORGAN_CLOUD_ENABLED", False),
        supabase_url=_env_key("SUPABASE_URL"),
        supabase_key=_env_key("SUPABASE_KEY"),
        supabase_secret_key=_env_key("SUPABASE_SECRET_KEY"),
        device_name=(_env_any_case("MORGAN_DEVICE_NAME") or socket.gethostname() or "local"),
        serve_web=_env_bool("MORGAN_SERVE_WEB", True),
        api_token=_env_key("MORGAN_API_TOKEN"),
        reloj_secreto=_env_key("MORGAN_RELOJ_SECRETO"),
        reloj_interno=_env_bool("MORGAN_RELOJ_INTERNO", True),
        avisar_errores=_env_bool("MORGAN_AVISAR_ERRORES", True),
        # El valor por defecto sigue al entorno: la nube es publica y local no.
        require_auth=_env_bool("MORGAN_REQUIRE_AUTH", environment == "cloud"),
        registro_abierto=_env_bool("MORGAN_REGISTRO_ABIERTO", True),
        tokens_api=_env_bool("MORGAN_TOKENS_API", True),
        cupo_global_mensajes=max(0, _env_int("MORGAN_CUPO_GLOBAL_MENSAJES", Settings.cupo_global_mensajes)),
        cupo_global_transcripciones=max(0, _env_int("MORGAN_CUPO_GLOBAL_TRANSCRIPCIONES", Settings.cupo_global_transcripciones)),
        cupo_global_imagenes=max(0, _env_int("MORGAN_CUPO_GLOBAL_IMAGENES", Settings.cupo_global_imagenes)),
        persist_history=_env_bool("MORGAN_PERSIST_HISTORY", True),
        history_window=_env_int("MORGAN_HISTORY_WINDOW", Settings.history_window, minimum=0, maximum=200),
        llm_timeout=_env_int("MORGAN_LLM_TIMEOUT", Settings.llm_timeout, minimum=5, maximum=300),
        llm_max_retries=_env_int("MORGAN_LLM_MAX_RETRIES", Settings.llm_max_retries, minimum=0, maximum=5),
        # El valor por defecto sigue al entorno, como `require_auth`: en la nube
        # hay un proxy delante que corta a los 120 s y en local no hay nada.
        turn_timeout=_env_int(
            "MORGAN_TURN_TIMEOUT",
            Settings.turn_timeout_nube if environment == "cloud" else Settings.turn_timeout,
            minimum=10, maximum=900,
        ),
        # Sin variable, en la nube 170 y en local el mismo tope que `/chat`.
        turn_timeout_stream=_env_int(
            "MORGAN_STREAM_TURN_TIMEOUT",
            Settings.turn_timeout_stream_nube if environment == "cloud" else Settings.turn_timeout,
            minimum=10, maximum=900,
        ),
        max_iterations=_env_int("MORGAN_MAX_ITERATIONS", Settings.max_iterations, minimum=1, maximum=20),
        http_deadline=_env_int(
            "MORGAN_HTTP_DEADLINE",
            Settings.http_deadline_nube if environment == "cloud" else Settings.http_deadline,
            minimum=0, maximum=900,
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Configuración compartida del proceso (se carga una sola vez)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings() -> None:
    """Fuerza una recarga de la configuración (útil en pruebas)."""
    global _settings
    _settings = None
