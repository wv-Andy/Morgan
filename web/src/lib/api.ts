/* Morgan Web UI — API Client (V1.1) */

// Origen de la API:
//  - VITE_MORGAN_API_URL manda, si esta definida. En los builds de Vercel vale
//    '/api' y no se puede cambiar desde el panel: lo fija vite.config.ts, que
//    explica por que. Resumen: con la API en otro dominio la cookie de sesion
//    es de terceros y Safari en iPhone la tira, asi que no se entra nunca.
//  - En desarrollo (Vite en :5173) hay que apuntar al backend en :8000.
//  - En el build de produccion la sirve la propia API, asi que se usa el mismo
//    origen: funciona igual por 127.0.0.1 que por localhost y evita el CORS.
const API_BASE =
  import.meta.env.VITE_MORGAN_API_URL ??
  (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');

// Con VITE_MORGAN_API_URL el backend vive en otra maquina (Render, un tunel...).
// Ahi no hay nada que arrancar en el equipo de quien mira, asi que decirle
// "inicia el servidor" es un consejo inutil y confuso.
export const API_ES_REMOTA = Boolean(import.meta.env.VITE_MORGAN_API_URL);
export const API_BASE_URL = API_BASE;

// Identificador de sesion estable por navegador. Sin el, todos los clientes
// compartirian la sesion 'default' del backend y sus conversaciones se mezclarian.
const SESSION_KEY = 'morgan_session_id';

function getSessionId(): string {
  try {
    let id = localStorage.getItem(SESSION_KEY);
    if (!id) {
      id = `web-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
      localStorage.setItem(SESSION_KEY, id);
    }
    return id;
  } catch {
    // Modo privado o almacenamiento bloqueado: sesion efimera para esta carga.
    return `web-efimera-${Math.random().toString(36).slice(2)}`;
  }
}

const SESSION_ID = getSessionId();

export function defaultSessionId(): string {
  return SESSION_ID;
}

export function newSessionId(): string {
  return `web-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

// --- Token de la API (opcional) ---
// Solo hace falta si el backend define MORGAN_API_TOKEN. Se guarda por navegador
// para no tener que reescribirlo en cada visita.
const TOKEN_KEY = 'morgan_api_token';

function readStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

let memoryToken = readStoredToken();

export function getApiToken(): string {
  return memoryToken;
}

export function setApiToken(token: string): void {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Almacenamiento bloqueado: el token valdra solo para esta carga.
  }
}

export interface ChatResponse {
  success: boolean;
  response: string;
  model: string;
  elapsed_seconds: number;
  /**
   * En que se fue el tiempo, en milisegundos: `modelo_ms`, `bd_ms`, `resto_ms`,
   * `herramienta.<nombre>_ms`. Y dos datos que no son tiempos: `proveedor`, que
   * dice quien contesto, y `respaldo`, que esta puesto si no fue el principal.
   *
   * Sirve para explicar una respuesta lenta en lugar de dejar que parezca una
   * averia. Medido: con el principal agotado, un turno pasa de segundos a un
   * minuto.
   */
  etapas?: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: string;
  version: string;
  timestamp: string;
}

export interface ComponentStatus {
  status: string;
  details?: string;
}

export interface ServiceStatusItem {
  name: string;
  state: 'available' | 'degraded' | 'unavailable' | 'unknown';
  detail?: string | null;
  age_seconds?: number | null;
  required_for: string[];
}

export interface CapabilityItem {
  name: string;
  available: boolean;
  reason?: string | null;
}

export interface SyncStatus {
  enabled: boolean;
  pending: number;
  failed: number;
  remote_available?: boolean | null;
}

export interface StatusResponse {
  status: string;
  mode: string;
  tools_count: number;
  domains_count: number;
  components: Record<string, ComponentStatus>;
  environment: string;
  services: ServiceStatusItem[];
  capabilities: CapabilityItem[];
  sync: SyncStatus;
}

export interface ToolSchema {
  name: string;
  category: string;
  risk_level: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface ToolListResponse {
  success: boolean;
  total: number;
  categories: string[];
  tools: ToolSchema[];
}

export interface MemoryItem {
  category: string;
  key: string;
  value: string;
  updated_at: string;
}

export interface MemoryListResponse {
  success: boolean;
  count: number;
  memories: MemoryItem[];
}

export interface AuditItem {
  timestamp: string;
  tool: string;
  risk_level: string;
  authorized: boolean;
  arguments: Record<string, unknown>;
  success: boolean;
  error?: string;
}

export interface AuditListResponse {
  success: boolean;
  count: number;
  records: AuditItem[];
}

export interface SessionItem {
  id: string;
  title: string | null;
  created_at: string | null;
  updated_at: string | null;
  metadata: Record<string, unknown>;
  message_count: number;
  archived: boolean;
  pinned: boolean;
  group_name: string | null;
  /** El espacio de trabajo. `null` es «General». */
  espacio_id: string | null;
}

export type EstadoTarea =
  | 'pending' | 'running' | 'waiting' | 'failed' | 'completed' | 'cancelled';

export type Veredicto = 'correcto' | 'incorrecto' | 'no_verificable';

export interface TaskStepItem {
  orden: number;
  descripcion: string;
  estado: EstadoTarea;
  herramienta: string | null;
  resultado: string | null;
  error: string | null;
  /** Si se comprobo el efecto (V1.7). `null` significa que NO se comprobo, que
   *  es distinto de que saliera bien. */
  verificacion: Veredicto | null;
  verificacion_motivo: string | null;
}

/** Una automatización (4.14): una orden guardada con un horario. */
export interface Automatizacion {
  id: string;
  nombre: string;
  instruccion: string;
  cuando: string;
  zona: string;
  necesita_pc: boolean;
  activa: boolean;
  proxima: number | null;
  proxima_texto: string | null;
  esperando_pc: boolean;
  ultima: number | null;
  ultimo_estado: 'hecha' | 'fallo' | 'saltada' | null;
  fallos_seguidos: number;
  /** Los pasos fijos (4.15), si cambia algo: exactamente lo que hace cada vez. */
  pasos: { herramienta: string; argumentos: Record<string, unknown>; descripcion: string }[] | null;
}

/** Lo que contó una ejecución, en la bandeja. */
export interface Aviso {
  id: string;
  automatizacion_id: string | null;
  titulo: string;
  texto: string;
  estado: 'hecha' | 'fallo' | 'saltada';
  herramientas: string[];
  leido: boolean;
  creado_en: number;
}

export interface TaskItem {
  id: string;
  objetivo: string;
  estado: EstadoTarea;
  session_id: string | null;
  pasos: TaskStepItem[];
  resultado: string | null;
  error: string | null;
  intentos: number;
  creado_en: number;
  actualizado_en: number;
  // Los calcula el servidor: repetir la regla aqui acabaria en discrepancias.
  progreso: number;
  paso_actual: string | null;
}

export interface TaskListResponse {
  success: boolean;
  count: number;
  tasks: TaskItem[];
}

export interface UploadItem {
  id: string;
  nombre: string;
  mime: string;
  // `descarga`: una copia traida del PC que Morgan no sabe leer, solo descargar (3.1-E).
  familia: 'imagen' | 'documento' | 'texto' | 'audio' | 'descarga';
  tamano: number;
  creado_en: number;
  espacio_id: string | null;
}

export interface UploadListResponse {
  success: boolean;
  count: number;
  uploads: UploadItem[];
  max_mb: number;
  ttl_hours: number;
}

export interface UserSettings {
  nombre: string;
  ocupacion: string;
  sobre_mi: string;
  idioma: string;
  estilo_respuesta: string;
}

export interface SettingsResponse {
  success: boolean;
  settings: UserSettings;
}

export interface SessionFilters {
  limit?: number;
  /** 'false' solo activas · 'true' solo archivadas · 'all' ambas. */
  archived?: 'false' | 'true' | 'all';
  group?: string;
  /** Busca en el titulo. */
  q?: string;
}

export interface SessionChanges {
  title?: string;
  archived?: boolean;
  pinned?: boolean;
  /** Cadena vacia para sacarla del grupo. */
  group_name?: string;
  /** Mover a otro espacio. Cadena vacia para devolverla a «General». */
  espacio_id?: string;
}

export interface EspacioItem {
  id: string;
  nombre: string;
  instrucciones: string;
  creado_en: number;
  actualizado_en: number;
}

export interface EspacioListResponse {
  success: boolean;
  count: number;
  espacios: EspacioItem[];
  max_instrucciones: number;
}

export interface SessionListResponse {
  success: boolean;
  count: number;
  sessions: SessionItem[];
}

export interface StoredMessage {
  id: number | null;
  session_id: string;
  role: string;
  content: string;
  tool_name: string | null;
  tool_call_id: string | null;
  created_at: string | null;
}

export interface MessageListResponse {
  success: boolean;
  session_id: string;
  count: number;
  total: number;
  messages: StoredMessage[];
}

/**
 * Lo que se dice cuando no hay un mensaje de Morgan que enseñar (4.23: los errores, en palabras
 * que entiende cualquiera). El código (`HTTP_503`…) va en `code`, para quien lo necesite; en
 * pantalla, qué pasó y qué hacer.
 */
export const SIN_RESPUESTA_CLARA = (status: number) =>
  `Morgan no pudo contestar ahora mismo (código ${status}). Vuelve a intentarlo en un momento.`;
export const SIN_CONEXION = 'No se pudo conectar con Morgan. Mira tu conexión a internet y vuelve a intentarlo.';

export class MorganAPIError extends Error {
  constructor(public code: string, message: string, public details?: unknown) {
    super(message);
    this.name = 'MorganAPIError';
  }
}

// Un turno del agente puede encadenar varias llamadas al LLM y ejecutar
// herramientas: con carga o con un proveedor lento supera holgadamente los 30 s.
// Medido en una rafaga de 6 peticiones simultaneas: 23 s, 40 s y 44 s. Con un
// unico timeout corto, el backend respondia bien pero la interfaz lo daba por
// caido. Por eso cada endpoint lleva el suyo.
const TIMEOUT_RAPIDO = 15_000;   // consultas de solo lectura
// Por encima del limite del turno del servidor (180 s), para que llegue su
// aviso en lugar de que el cliente se rinda antes y no explique nada.
const TIMEOUT_AGENTE = 240_000;  // /chat y ejecucion de herramientas

// Un backend remoto en un plan gratuito se duerme tras un rato sin uso y tarda
// cerca de un minuto en levantarse. Con los 15 s de una consulta normal, la
// primera comprobacion se cancelaba SIEMPRE antes de que el servicio despertara:
// la web daba por caido un backend que solo estaba arrancando, y el reintento
// volvia a fallar por lo mismo.
export const TIMEOUT_DESPERTAR = 90_000;

// Entrar, registrarse o pedir un enlace de recuperacion son lo PRIMERO que se
// hace al abrir la web, asi que casi siempre le tocan al backend dormido. Con los
// 15 s de una consulta normal, la peticion se cancelaba antes de que el servicio
// terminara de despertar y la persona veia un error habiendo funcionado todo.
const TIMEOUT_CUENTAS = 60_000;


// --- Cuentas (identidad, V2.0 adelantada) ---

export type RolMorgan = 'user' | 'admin' | 'owner';

export interface UsuarioMorgan {
  id: string;
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
  /** Lo decide el servidor leyendo la fila del usuario. Aqui solo se muestra:
   *  ocultar un boton no es seguridad, y cada ruta comprueba el permiso. */
  rol?: RolMorgan;
  /**
   * Si esa dirección se confirmó abriendo el enlace del correo.
   *
   * **No es una puerta**: la cuenta funciona igual sin confirmar. Lo que cambia
   * es que sin confirmar no se puede recuperar la contraseña, porque el enlace
   * de recuperación va justo a esa dirección.
   */
  email_verificado?: boolean;
  creado_en?: number;
  ultima_actividad?: number | null;
}

export interface QuienSoyResponse {
  success: boolean;
  autenticado: boolean;
  /** Morgan corriendo en tu equipo: no hay cuenta, y no hace falta. */
  local: boolean;
  usuario: UsuarioMorgan | null;
  /**
   * El token CSRF de esta sesion. Viene por aqui porque su cookie pertenece al
   * dominio de la API y la interfaz vive en otro: el navegador la envia, pero
   * no puede leerla. Vacio cuando no hay sesion.
   */
  csrf?: string | null;
}

export interface CuentaResponse {
  success: boolean;
  usuario: UsuarioMorgan;
  csrf: string | null;
}

/**
 * Un servicio externo, con su estado real.
 *
 * `disponible` es del SERVIDOR (¿tiene credenciales configuradas?) y
 * `conectado` es de la PERSONA (¿ha autorizado?). Son cosas distintas y hay que
 * poder distinguirlas: la interfaz enseña botón de conectar solo cuando lo
 * primero es cierto.
 */
export interface ServicioIntegrable {
  servicio: string;
  nombre: string;
  descripcion: string;
  /** Qué habilita, en lenguaje llano, para poder leerlo antes de autorizar. */
  permite: string[];
  disponible: boolean;
  motivo_no_disponible: string | null;
  conectado: boolean;
  cuenta?: string | null;
  scopes?: string[];
  /** El último fallo al hablar con el servicio. Distingue «conectado y
   *  fallando» de «no conectado», que no son lo mismo. */
  error?: string | null;
  creado_en?: number;
  actualizado_en?: number;
}

export interface SesionActiva {
  creado_en: number;
  ultimo_uso: number | null;
  user_agent: string | null;
}

/** Lo que un token de API deja hacer (V2.0.40). Ver docs/autenticacion.md. */
export type AlcanceToken = 'chat' | 'lectura' | 'escritura';

/** Un equipo emparejado (agente local, 3.0-C). Nunca su credencial. */
export interface AgenteLocal {
  id: string;
  nombre: string;
  sistema: string | null;
  agent_version: string | null;
  protocol_version: number | null;
  creado_en: number;
  last_seen: number | null;
  /** Desde la 3.7: si está conectado **ahora**, y lo que ofrece. */
  conectado?: boolean;
  capacidades?: string[];
  /** Desde la 3.8: cuándo se cambió su credencial por última vez (rota sola cada 90 días). */
  credencial_rotada_en?: number | null;
}

/** Una orden a un equipo, del historial de la nube (3.7). Sin argumentos ni contenido. */
export interface OrdenDeAgente {
  command_id: string;
  capability: string;
  estado: string;
  ms: number | null;
  creado_en: number;
}

/** Un token de API tal como se enseña: nunca su valor, que solo viaja al crearlo. */
export interface TokenDeApi {
  id: string;
  nombre: string;
  alcances: AlcanceToken[];
  creado_en: number;
  caduca_en: number;
  ultimo_uso: number | null;
}


// --- Planes (V1.6) ---

export type EstadoPlan =
  | 'borrador' | 'pendiente' | 'aprobado'
  | 'ejecutando' | 'completado' | 'fallido' | 'rechazado';

export interface PasoPlaneado {
  orden: number;
  descripcion: string;
  herramienta: string | null;
  /** Recortados y sin secretos: quien aprueba necesita ver QUE archivo, no su
   *  contenido entero. Lo hace el servidor, no aqui. */
  argumentos: Record<string, string>;
  riesgo: string;
  depende_de: number[];
  motivo: string | null;
  necesita_confirmacion: boolean;
  /** Si ya se ejecutó con la autorización del plan (V2.0.16). Una aprobación vale una vez. */
  ejecutado: boolean;
}

export interface PlanItem {
  id: string;
  objetivo: string;
  pasos: PasoPlaneado[];
  estado: EstadoPlan;
  /** El del paso mas arriesgado, no un promedio. */
  riesgo: string;
  necesita_aprobacion: boolean;
  ejecutable: boolean;
  session_id: string | null;
  task_id: string | null;
  creado_en: number;
  decidido_en: number | null;
  decidido_por: string | null;
  motivo_rechazo: string | null;
}

export interface PlanListResponse {
  success: boolean;
  count: number;
  planes: PlanItem[];
}

// --- CSRF ---
// La sesion viaja en una cookie que el navegador adjunta sola, y eso es
// precisamente lo que hace posible el CSRF: otra web puede provocar la peticion
// y la cookie ira igual. La defensa es el doble envio: el backend espera el
// valor del token repetido en esta cabecera. Una web ajena puede provocar la
// peticion, pero no conseguir el token: CORS le impide leer la respuesta de
// donde sale.
//
// DE DONDE SALE EL TOKEN, y por que hubo que cambiarlo.
//
// La primera version lo leia de una cookie con `document.cookie`. Eso solo
// funciona cuando la web y la API comparten dominio —el `npm run dev` de
// siempre—. En el despliegue real NO lo comparten: la web esta en Vercel y la
// API en Render. El navegador ENVIA la cookie, pero el JavaScript de otro
// dominio no puede LEERLA, asi que la cabecera nunca se ponia y **todos** los
// POST se rechazaban con 403.
//
// El sintoma no se parecia a la causa: la sesion estaba abierta, `/auth/yo`
// respondia bien y los GET funcionaban, pero enviar un mensaje, crear una
// conversacion o subir un archivo fallaba. Parecia que Morgan no reconociera
// la sesion.
//
// Ahora el token llega en el JSON de `/auth/login`, `/auth/registro` y
// `/auth/yo`, y se guarda aqui. La cookie se sigue prefiriendo cuando se puede
// leer, porque en ese caso siempre esta al dia.
const COOKIE_CSRF = 'morgan_csrf';
const CABECERA_CSRF = 'X-Morgan-CSRF';
const CSRF_KEY = 'morgan_csrf_token';

// --- Espacio de trabajo (V2.2) ------------------------------------------------
//
// Viaja en una cabecera de TODAS las peticiones, igual que el CSRF: así ninguna
// llamada nueva puede olvidarse de decir en qué espacio está. El servidor
// comprueba que el espacio es de quien pide antes de usarlo.
//
// Se recuerda entre recargas. Volver a abrir Morgan y aparecer en otro proyecto
// del que se dejó sería desconcertante.
const CABECERA_ESPACIO = 'X-Morgan-Espacio';
const ESPACIO_KEY = 'morgan_espacio';

/** Se emite cuando el servidor dice que el espacio seleccionado ya no existe. */
export const EVENTO_ESPACIO_PERDIDO = 'morgan:espacio-perdido';

let espacioEnMemoria = ((): string => {
  try {
    return localStorage.getItem(ESPACIO_KEY) ?? '';
  } catch {
    return '';
  }
})();

/** El espacio seleccionado, o `null` si es «General». */
export function espacioActual(): string | null {
  return espacioEnMemoria || null;
}

export function fijarEspacioActual(id: string | null): void {
  espacioEnMemoria = id ?? '';
  try {
    if (espacioEnMemoria) localStorage.setItem(ESPACIO_KEY, espacioEnMemoria);
    else localStorage.removeItem(ESPACIO_KEY);
  } catch {
    // Almacenamiento bloqueado: vale para esta carga.
  }
}

function espacioHeader(): Record<string, string> {
  return espacioEnMemoria ? { [CABECERA_ESPACIO]: espacioEnMemoria } : {};
}

/**
 * La zona horaria del navegador (4.14). Con ella, «cada día a las 9» se guarda como las 9
 * de tu reloj, y no de la del servidor. Si el navegador no la da, ninguna: el servidor
 * usa UTC y lo dice al crear la automatización.
 */
export function zonaHeader(): Record<string, string> {
  try {
    const zona = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zona ? { 'X-Morgan-Zona': zona } : {};
  } catch {
    return {};
  }
}

/** Se emite cuando el backend responde que la sesion ya no vale. */
export const EVENTO_SIN_SESION = 'morgan:sin-sesion';

function leerCookie(nombre: string): string {
  try {
    const encontrada = document.cookie
      .split('; ')
      .find((c) => c.startsWith(`${nombre}=`));
    return encontrada ? decodeURIComponent(encontrada.slice(nombre.length + 1)) : '';
  } catch {
    return '';
  }
}

let csrfEnMemoria = ((): string => {
  try {
    return localStorage.getItem(CSRF_KEY) ?? '';
  } catch {
    return '';
  }
})();

/** Guarda el token que devolvio el backend. Vacio lo olvida (al cerrar sesion). */
export function guardarCsrf(token: string | null | undefined): void {
  csrfEnMemoria = token ?? '';
  try {
    if (csrfEnMemoria) localStorage.setItem(CSRF_KEY, csrfEnMemoria);
    else localStorage.removeItem(CSRF_KEY);
  } catch {
    // Almacenamiento bloqueado: el token vale para esta carga, que es
    // suficiente porque `/auth/yo` lo devuelve en cada arranque.
  }
}

function csrfHeader(): Record<string, string> {
  // La cookie manda cuando se puede leer: es la fuente de la verdad y no puede
  // quedarse desfasada. Fuera de ahi, lo guardado.
  const valor = leerCookie(COOKIE_CSRF) || csrfEnMemoria;
  return valor ? { [CABECERA_CSRF]: valor } : {};
}

/** Se lanza cuando quien pidió la operación decidió pararla. */
export class OperacionCancelada extends Error {
  constructor() {
    super('Operación cancelada');
    this.name = 'OperacionCancelada';
  }
}

/**
 * El cuerpo, o `undefined` si no era JSON.
 *
 * Se separa para que `request` no tenga un `try` dentro de otro: lo que se
 * quiere distinguir es «Morgan contesto algo raro» de «esto no lo contesto
 * Morgan», y eso no se ve si el fallo de parseo se mezcla con los demas.
 */
async function leerCuerpo(res: Response) {
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}

/**
 * El UNICO `fetch` del cliente.
 *
 * Lo usan `request()`, que lee JSON, y `flujo()`, que lee líneas. Todo lo que una
 * petición no puede olvidar vive aquí: la cookie, la cabecera CSRF, el token y el
 * espacio de trabajo. Que haya un solo sitio no es estilo: la subida de archivos
 * tuvo su propio `fetch` durante cinco versiones, se quedó sin cookie ni CSRF, y
 * contestaba «Necesitas iniciar sesión» a quien la tenía abierta. Lo fija
 * `test_frontend_estabilidad.py`.
 */
async function conectar(path: string, options: RequestInit | undefined, signal: AbortSignal) {
  // En multipart el Content-Type lo tiene que poner el navegador, porque lleva
  // el 'boundary' que el mismo genera. Fijarlo aqui deja la subida rota de
  // forma silenciosa: el servidor no encuentra el archivo en el cuerpo.
  const esFormulario = options?.body instanceof FormData;

  return fetch(`${API_BASE}${path}`, {
    ...options,
    signal,
    // Sin esto el navegador NO envia la cookie de sesion cuando la API vive en
    // otro dominio. Todas las peticiones llegarian sin identificar.
    credentials: 'include',
    headers: {
      ...(esFormulario ? {} : { 'Content-Type': 'application/json' }),
      ...(memoryToken ? { Authorization: `Bearer ${memoryToken}` } : {}),
      ...csrfHeader(),
      ...espacioHeader(),
      ...zonaHeader(),
      ...options?.headers,
    },
  });
}

/**
 * Lo que dice una respuesta que no es un éxito, con sus efectos: recoger el CSRF,
 * avisar de la sesión caducada y del espacio perdido.
 *
 * En una función porque `request()` y `flujo()` reciben los mismos errores —un
 * 401, un 422 de un adjunto, un 429 de cupo— y dos copias de esto divergirían.
 */
function interpretarRespuesta(res: Response, data: unknown): MorganAPIError | null {
  // Un cuerpo que no es JSON no lo ha escrito Morgan: lo ha escrito algo que
  // hay delante. El caso real es el borde de Vercel cortando un turno largo a
  // los 120 s con una pagina HTML de error suya.
  //
  // Sin esto, el fallo caia hasta el final y salia «No se pudo conectar con
  // Morgan API. ¿Esta el servidor en ejecucion?». Es falso —el servidor esta
  // perfectamente y sigue trabajando— y manda a buscar el problema justo donde
  // no esta.
  if (data === undefined) {
    return new MorganAPIError(
      `HTTP_${res.status}`,
      res.status === 502 || res.status === 504
        ? 'La red cortó la espera antes de que Morgan terminara. Suele pasar con encargos muy largos: pídemelo por partes.'
        : SIN_RESPUESTA_CLARA(res.status),
    );
  }

  // El token se recoge AQUI y no en cada funcion de `/auth/*`: asi ninguna
  // ruta nueva puede olvidarse de hacerlo. El backend lo manda en el login,
  // el registro y `/auth/yo`.
  if (data && typeof data === 'object' && 'csrf' in data) {
    guardarCsrf((data as { csrf?: string | null }).csrf);
  }

  if (res.ok) return null;

  const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;

  // La sesion ha dejado de valer: caduco, se cerro desde otro dispositivo o
  // se cambio la contrasena. Se avisa a quien pinta la pantalla para que
  // ensene el acceso.
  if (res.status === 401 && err?.code === 'SIN_SESION') {
    guardarCsrf('');
    window.dispatchEvent(new CustomEvent(EVENTO_SIN_SESION));
  }

  // El espacio seleccionado ya no existe: se borró desde otro dispositivo,
  // o era de otra cuenta que se usó en este navegador. Se vuelve a
  // «General» y se avisa, en lugar de fallar en cada petición.
  if (res.status === 404 && err?.code === 'ESPACIO_ACTUAL_NO_ENCONTRADO') {
    fijarEspacioActual(null);
    window.dispatchEvent(new CustomEvent(EVENTO_ESPACIO_PERDIDO));
  }

  return new MorganAPIError(
    err?.code || `HTTP_${res.status}`,
    err?.message || SIN_RESPUESTA_CLARA(res.status),
    err?.details as never,
  );
}

async function request<T>(
  path: string,
  options?: RequestInit,
  timeoutMs: number = TIMEOUT_RAPIDO,
  /**
   * Señal para poder parar la petición desde fuera.
   *
   * Va aparte del tiempo límite porque son dos cosas distintas y hay que
   * poder distinguirlas: una espera agotada es un problema del que informar,
   * y una cancelación es lo que el usuario acaba de pedir. Mezclarlas hacía
   * que parar una respuesta enseñara un error de tiempo excedido.
   */
  externa?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  const cancelar = () => controller.abort();
  externa?.addEventListener('abort', cancelar);

  try {
    if (externa?.aborted) throw new OperacionCancelada();

    const res = await conectar(path, options, controller.signal);
    const data = await leerCuerpo(res);

    const error = interpretarRespuesta(res, data);
    if (error) throw error;

    return data as T;
  } catch (err) {
    throw traducirFallo(err, path, timeoutMs, externa);
  } finally {
    clearTimeout(timeout);
    externa?.removeEventListener('abort', cancelar);
  }
}

/** Lo que se lanza cuando la petición no llegó a terminar bien. Común a `request` y `flujo`. */
function traducirFallo(err: unknown, path: string, timeoutMs: number, externa?: AbortSignal): Error {
  if (err instanceof MorganAPIError) return err;
  // Una cancelacion pasa TAL CUAL. Sin esta linea, la que se lanza arriba
  // cuando la senal ya venia abortada caia hasta el final y se convertia en
  // NETWORK_ERROR: «No se pudo conectar con Morgan API. ¿Esta el servidor en
  // ejecucion?». Culpar al servidor de algo que acaba de hacer el usuario es
  // la peor forma de equivocarse en un mensaje de error.
  if (err instanceof OperacionCancelada) return err;
  if ((err as Error)?.name === 'AbortError') {
    // Quien cancela ya sabe lo que ha hecho: no se le enseña un error.
    if (externa?.aborted) return new OperacionCancelada();

    return new MorganAPIError(
      'TIMEOUT',
      // El aviso depende de la ruta: en el chat, el turno puede seguir vivo y
      // acabar apareciendo; en un formulario de acceso, decir "revisa la
      // conversación" no significa nada y asusta sin motivo.
      path.startsWith('/auth/')
        ? `El servidor tardó más de ${Math.round(timeoutMs / 1000)} s en responder. Puede estar despertando: espera un momento y vuelve a intentarlo.`
        : `La solicitud superó los ${Math.round(timeoutMs / 1000)} s. Morgan puede seguir trabajando: revisa la conversación en unos segundos.`,
    );
  }
  return new MorganAPIError('NETWORK_ERROR', SIN_CONEXION);
}

/** Un evento de progreso de `/chat/stream`. `fin` y `error` no llegan aquí: los resuelve `flujo`. */
export interface EventoDelTurno {
  tipo: 'inicio' | 'pensando' | 'herramienta' | 'respaldo' | 'latido' | 'archivo_listo' | 'equipo';
  t: number;
  vuelta?: number;
  nombre?: string;
  /** De una herramienta: `empieza`, `ok`, `fallo`. De `equipo` (3.4): cómo va la orden en
   *  el PC — `PENDING`, `RUNNING`, `CANCEL_REQUESTED` o `SIN_VUELTA`. */
  estado?: string;
  /** `equipo` en cola: su puesto (1 = la siguiente). */
  posicion?: number;
  /** `inicio` (3.4): con él, «Detener» para también lo que se hace en el PC. */
  turno?: string;
  proveedor?: string;
  /** `archivo_listo` (3.1-E): una copia del PC, lista para descargar. */
  upload_id?: string;
  bytes?: number;
}

/** Una copia del PC lista para descargar, sacada de los eventos del turno (3.1-E). */
export interface Descarga {
  id: string;
  nombre: string;
  bytes: number;
}

/**
 * Una respuesta que va contando lo que pasa: una línea JSON por evento (V2.0.14).
 *
 * Diseño en docs/agente.md. Tres diferencias con `request()`, y las tres
 * importan:
 *
 * - **El plazo es de silencio, no total.** Se reinicia con cada trozo que llega.
 *   Un plazo total mataría un turno sano que lleva tres minutos hablando.
 * - **`fin` devuelve el resultado y `error` lo lanza.** Lo demás va a
 *   `alEvento`, que es lo que pinta el progreso.
 * - **Si se corta sin `fin`, se dice**, y sin reintentar con `/chat` a
 *   escondidas: el turno puede seguir vivo, y repetirlo pagaría el modelo dos
 *   veces.
 */
async function flujo<T>(
  path: string,
  options: RequestInit,
  alEvento: (evento: EventoDelTurno) => void,
  silencioMs: number,
  externa?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  let reloj = setTimeout(() => controller.abort(), silencioMs);
  const reiniciarReloj = () => {
    clearTimeout(reloj);
    reloj = setTimeout(() => controller.abort(), silencioMs);
  };

  const cancelar = () => controller.abort();
  externa?.addEventListener('abort', cancelar);

  try {
    if (externa?.aborted) throw new OperacionCancelada();

    const res = await conectar(path, options, controller.signal);
    const tipo = res.headers?.get('content-type') ?? '';

    // Lo que falla antes de empezar —un adjunto que no existe, el cupo, la sesión—
    // llega como una respuesta normal, no como stream. Y una página HTML del
    // proxy, también: se trata igual que en `request()`.
    if (!res.ok || !tipo.includes('ndjson') || !res.body) {
      const data = await leerCuerpo(res);
      throw interpretarRespuesta(res, data) ?? new MorganAPIError(
        `HTTP_${res.status}`, SIN_RESPUESTA_CLARA(res.status),
      );
    }

    const lector = res.body.getReader();
    const decodificador = new TextDecoder();
    let pendiente = '';

    for (;;) {
      const { value, done } = await lector.read();
      if (done) break;
      reiniciarReloj();
      pendiente += decodificador.decode(value, { stream: true });

      // Una línea puede llegar partida en dos trozos: solo se procesan las
      // completas, y lo que queda espera al siguiente.
      const lineas = pendiente.split('\n');
      pendiente = lineas.pop() ?? '';

      for (const linea of lineas) {
        if (!linea.trim()) continue;
        let evento: { tipo: string; code?: string; message?: string };
        try {
          evento = JSON.parse(linea);
        } catch {
          throw new MorganAPIError(`HTTP_${res.status}`, 'La respuesta se cortó a mitad. Vuelve a intentarlo; si era un encargo largo, pídelo por partes.');
        }
        if (evento.tipo === 'fin') return evento as unknown as T;
        if (evento.tipo === 'error') {
          throw new MorganAPIError(evento.code || 'AGENT_EXECUTION_ERROR', evento.message || 'Error durante el turno.');
        }
        alEvento(evento as EventoDelTurno);
      }
    }

    throw new MorganAPIError(
      'STREAM_CORTADO',
      'La conexión se cortó antes de que Morgan terminara. Puede seguir trabajando: revisa la conversación en unos segundos.',
    );
  } catch (err) {
    throw traducirFallo(err, path, silencioMs, externa);
  } finally {
    clearTimeout(reloj);
    externa?.removeEventListener('abort', cancelar);
  }
}

export const morganAPI = {
  health: () => request<HealthResponse>('/health'),
  status: (timeoutMs?: number) => request<StatusResponse>('/status', undefined, timeoutMs),

  chat: (
    message: string,
    sessionId?: string,
    temporary = false,
    attachments: string[] = [],
    señal?: AbortSignal,
  ) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({
        message,
        session_id: sessionId ?? SESSION_ID,
        temporary,
        // Van en su propio campo, no incrustados en el texto: si el usuario
        // edita el mensaje antes de enviarlo, el vinculo no se pierde.
        attachments,
      }),
    }, TIMEOUT_AGENTE, señal),

  /**
   * El chat que usa la web desde la V2.0.14: el mismo turno, contando el progreso.
   * `TIMEOUT_AGENTE` es aquí un plazo de **silencio**: con latidos cada 10 s, solo
   * salta si deja de llegar nada.
   */
  chatStream: (
    message: string,
    alEvento: (evento: EventoDelTurno) => void,
    sessionId?: string,
    temporary = false,
    attachments: string[] = [],
    señal?: AbortSignal,
    /** 4.0: un plan que la persona acaba de aprobar; Morgan ejecuta sus pasos. */
    ejecutarPlan?: string,
  ) =>
    flujo<ChatResponse>('/chat/stream', {
      method: 'POST',
      body: JSON.stringify({
        message,
        session_id: sessionId ?? SESSION_ID,
        temporary,
        attachments,
        ...(ejecutarPlan ? { ejecutar_plan: ejecutarPlan } : {}),
      }),
    }, alEvento, TIMEOUT_AGENTE, señal),

  /** «Detener» (3.4): para lo que el turno está haciendo en el PC de la persona. */
  pararTurno: (turno: string) =>
    request<{ success: boolean; parado: boolean }>('/chat/parar', {
      method: 'POST',
      body: JSON.stringify({ turno }),
    }),

  // --- Conversaciones persistidas (V1.2) ---
  sessions: (filtros: SessionFilters = {}) => {
    const params = new URLSearchParams({ limit: String(filtros.limit ?? 30) });
    if (filtros.archived) params.set('archived', filtros.archived);
    if (filtros.group) params.set('group_name', filtros.group);
    // Solo se manda 'q' con contenido: una cadena vacia haria que el backend
    // filtrase por nada y devolviese la lista completa igual, pero ensucia la URL.
    if (filtros.q?.trim()) params.set('q', filtros.q.trim());
    return request<SessionListResponse>(`/sessions?${params}`);
  },

  // --- Espacios de trabajo (V2.2) ---
  espacios: () => request<EspacioListResponse>('/espacios'),

  crearEspacio: (nombre: string, instrucciones = '') =>
    request<EspacioItem>('/espacios', {
      method: 'POST',
      body: JSON.stringify({ nombre, instrucciones }),
    }),

  actualizarEspacio: (id: string, cambios: { nombre?: string; instrucciones?: string }) =>
    request<EspacioItem>(`/espacios/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(cambios),
    }),

  eliminarEspacio: (id: string) =>
    request<{ success: boolean; deleted: string }>(
      `/espacios/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),

  // --- Tareas (V1.5) ---
  tasks: (opciones: { sessionId?: string; soloActivas?: boolean } = {}) => {
    const params = new URLSearchParams();
    if (opciones.sessionId) params.set('session_id', opciones.sessionId);
    if (opciones.soloActivas) params.set('solo_activas', 'true');
    return request<TaskListResponse>(`/tasks?${params}`);
  },

  cancelTask: (id: string) =>
    request<TaskItem>(`/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),

  retryTask: (id: string) =>
    request<TaskItem>(`/tasks/${encodeURIComponent(id)}/retry`, { method: 'POST' }),

  /**
   * Lo que dice un audio, para poder leerlo antes de enviarlo.
   *
   * Se pide aparte del turno a propósito: si Whisper entiende mal una palabra
   * —con nombres propios pasa— Morgan respondería a otra pregunta sin que se
   * pueda ver por qué.
   */
  transcribir: (uploadId: string) =>
    request<{ success: boolean; texto: string; idioma: string | null; nombre: string }>(
      `/uploads/${encodeURIComponent(uploadId)}/transcripcion`,
      { method: 'POST' },
      TIMEOUT_AGENTE,
    ),

  // --- Archivos subidos (V1.4) ---
  //
  // Pasa por request() como todo lo demas, y eso no es un detalle de estilo.
  // Tuvo su propio `fetch` durante cinco versiones, copiado del de request(), y
  // en la copia se quedaron fuera dos cosas: `credentials: 'include'` y la
  // cabecera CSRF. Efecto: subir un archivo llegaba SIN SESION, y el servidor
  // contestaba «Necesitas iniciar sesion para usar Morgan» a alguien que la
  // tenia abierta y perfectamente valida.
  //
  // El motivo de la copia era real —request() fijaba Content-Type a JSON y en
  // multipart lo tiene que poner el navegador— pero la solucion correcta era
  // ensenarle multipart a request(), no tener dos caminos que se van separando.
  uploadFile: async (archivo: File): Promise<UploadItem> => {
    const cuerpo = new FormData();
    cuerpo.append('file', archivo);

    try {
      return await request<UploadItem>(
        '/uploads',
        { method: 'POST', body: cuerpo },
        TIMEOUT_AGENTE,
      );
    } catch (err) {
      // El aviso generico de request() habla de revisar la conversacion, que en
      // una subida no significa nada.
      if (err instanceof MorganAPIError && err.code === 'TIMEOUT') {
        throw new MorganAPIError('TIMEOUT', 'La subida tardó demasiado.');
      }
      throw err;
    }
  },

  uploads: () => request<UploadListResponse>('/uploads'),

  deleteUpload: (id: string) =>
    request(`/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // --- Ajustes del usuario (Etapa B) ---
  // Se guardan como recuerdos, asi que acaban en el prompt: lo que se configura
  // aqui, Morgan lo cumple.
  getSettings: () => request<SettingsResponse>('/settings'),

  saveSettings: (cambios: Partial<UserSettings>) =>
    request<SettingsResponse>('/settings', {
      method: 'PUT',
      body: JSON.stringify(cambios),
    }),

  updateSession: (sessionId: string, cambios: SessionChanges) =>
    request<SessionItem>(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'PATCH',
      body: JSON.stringify(cambios),
    }),

  sessionMessages: (sessionId: string, limit = 100) =>
    request<MessageListResponse>(`/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}`),

  deleteSession: (sessionId: string) =>
    request(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),

  tools: (category?: string) =>
    request<ToolListResponse>(`/tools${category ? `?category=${category}` : ''}`),

  executeTool: (toolName: string, args: Record<string, unknown> = {}) =>
    request(`/tools/${toolName}`, {
      method: 'POST',
      body: JSON.stringify({ arguments: args }),
    }, TIMEOUT_AGENTE),

  memory: (query?: string) =>
    request<MemoryListResponse>(`/memory${query ? `?query=${encodeURIComponent(query)}` : ''}`),

  rememberFact: (category: string, key: string, value: string) =>
    request<MemoryItem>('/memory', {
      method: 'POST',
      body: JSON.stringify({ category, key, value }),
    }),

  forgetFact: (key: string) =>
    request(`/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),

  audit: (limit = 50) => request<AuditListResponse>(`/audit?limit=${limit}`),

  // --- Planes (V1.6) ---

  planes: (soloPendientes = false, sessionId?: string) =>
    request<PlanListResponse>(
      `/planes?solo_pendientes=${soloPendientes}` +
      (sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ''),
    ),

  aprobarPlan: (planId: string) =>
    request<{ success: boolean; plan: PlanItem }>(
      `/planes/${encodeURIComponent(planId)}/aprobar`, { method: 'POST' },
    ),

  rechazarPlan: (planId: string, motivo?: string) =>
    request<{ success: boolean; plan: PlanItem }>(
      `/planes/${encodeURIComponent(planId)}/rechazar`,
      { method: 'POST', body: JSON.stringify({ motivo: motivo ?? null }) },
    ),

  // --- Cuentas (identidad, V2.0 adelantada) ---
  //
  // Ninguna de estas funciones guarda la sesion: vive en una cookie HttpOnly que
  // el navegador maneja solo y que el JavaScript no puede leer. Es lo que impide
  // que un script inyectado se la lleve.

  yo: () => request<QuienSoyResponse>('/auth/yo', undefined, TIMEOUT_CUENTAS),

  registro: (datos: {
    username: string;
    email: string;
    password: string;
    display_name?: string;
  }) => request<CuentaResponse>('/auth/registro', {
    method: 'POST',
    body: JSON.stringify(datos),
  }, TIMEOUT_CUENTAS),

  login: (identificador: string, password: string) =>
    request<CuentaResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ identificador, password }),
    }, TIMEOUT_CUENTAS),

  logout: async () => {
    const salida = await request<{ success: boolean }>(
      '/auth/logout', { method: 'POST' }, TIMEOUT_CUENTAS,
    );
    // Sin esto, el token de la sesion cerrada se quedaria guardado y viajaria
    // en las peticiones de la siguiente. No es un fallo de seguridad —el
    // backend lo compara con su cookie— pero si deja un 403 desconcertante.
    guardarCsrf('');
    return salida;
  },

  cambiarPassword: (actual: string, nueva: string) =>
    request<{ success: boolean; sesiones_cerradas: number }>('/auth/password', {
      method: 'POST',
      body: JSON.stringify({ actual, nueva }),
    }, TIMEOUT_CUENTAS),

  recuperar: (email: string) =>
    request<{ success: boolean; message: string }>('/auth/recuperar', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }, TIMEOUT_CUENTAS),

  /** Confirma el correo con el token del enlace. No necesita sesión. */
  verificarEmail: (token: string) =>
    request<{ success: boolean; message: string }>('/auth/verificar', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }, TIMEOUT_CUENTAS),

  /** Vuelve a mandar el enlace. Esta sí necesita sesión: manda un correo. */
  reenviarVerificacion: () =>
    request<{ success: boolean; enviado: boolean; message: string }>(
      '/auth/verificar/reenviar',
      { method: 'POST' },
      TIMEOUT_CUENTAS,
    ),

  restablecer: (token: string, password: string) =>
    request<{ success: boolean; message: string }>('/auth/restablecer', {
      method: 'POST',
      body: JSON.stringify({ token, password }),
    }, TIMEOUT_CUENTAS),

  sesiones: () => request<{ success: boolean; sesiones: SesionActiva[] }>('/auth/sesiones'),

  cerrarOtrasSesiones: () =>
    request<{ success: boolean; cerradas: number }>('/auth/sesiones/cerrar-otras', {
      method: 'POST',
    }),

  // --- Tokens personales de API (V2.0.41) ---
  //
  // Para conectar un programa propio a Morgan. Se gestionan solo con la sesión
  // de la web: con un token no se pueden crear ni revocar tokens.

  tokens: () => request<{ success: boolean; tokens: TokenDeApi[] }>('/auth/tokens'),

  /** Devuelve el valor del token **esta única vez**: Morgan solo guarda su huella. */
  crearToken: (nombre: string, alcances: AlcanceToken[], dias: number) =>
    request<{ success: boolean; token: TokenDeApi; valor: string; api_url?: string }>('/auth/tokens', {
      method: 'POST',
      body: JSON.stringify({ nombre, alcances, dias }),
    }, TIMEOUT_CUENTAS),

  revocarToken: (id: string) =>
    request<{ success: boolean }>(`/auth/tokens/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }, TIMEOUT_CUENTAS),

  revocarTodosLosTokens: () =>
    request<{ success: boolean; revocados: number }>('/auth/tokens/revocar-todos', {
      method: 'POST',
    }, TIMEOUT_CUENTAS),

  // --- El permiso automático (4.6) ---
  //
  // Lo verde y amarillo se aprueba solo; lo rojo sigue esperando. Solo con la sesión de
  // la web: un token no puede verlo ni cambiarlo.

  // --- Automatizaciones y bandeja (4.14) ---

  automatizaciones: () =>
    request<{ automatizaciones: Automatizacion[]; max_activas: number }>('/automatizaciones'),

  pausarAutomatizacion: (id: string) =>
    request<Automatizacion>(`/automatizaciones/${encodeURIComponent(id)}/pausar`, { method: 'POST' }),

  reanudarAutomatizacion: (id: string) =>
    request<Automatizacion>(`/automatizaciones/${encodeURIComponent(id)}/reanudar`, { method: 'POST' }),

  borrarAutomatizacion: (id: string) =>
    request<{ borrada: string }>(`/automatizaciones/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  avisos: () => request<{ avisos: Aviso[]; sin_leer: number }>('/avisos'),

  avisosSinLeer: () => request<{ sin_leer: number }>('/avisos/sin-leer'),

  marcarAvisosLeidos: (ids?: string[]) =>
    request<{ sin_leer: number }>('/avisos/leidos', {
      method: 'POST',
      body: JSON.stringify(ids ? { ids } : {}),
    }),

  permisoAutomatico: () =>
    request<{ success: boolean; encendido: boolean }>('/auth/permiso-automatico'),

  cambiarPermisoAutomatico: (encendido: boolean) =>
    request<{ success: boolean; encendido: boolean }>('/auth/permiso-automatico', {
      method: 'PUT',
      body: JSON.stringify({ encendido }),
    }, TIMEOUT_CUENTAS),

  // --- Agentes locales (3.0-C) ---
  //
  // Emparejar un PC con la cuenta. Solo con la sesión de la web: un token personal
  // no puede pedir códigos ni revocar equipos.

  agentes: () =>
    request<{ success: boolean; agentes: AgenteLocal[]; ultima_version?: string | null }>('/auth/agentes'),

  /** Un código de un solo uso que dura diez minutos. Anula el anterior. */
  codigoAgente: () =>
    request<{ success: boolean; codigo: string; caduca_en: number; instalar?: string }>('/auth/agentes/codigo', {
      method: 'POST',
    }, TIMEOUT_CUENTAS),

  ordenesAgente: (id: string, dias = 7) =>
    request<{ success: boolean; ordenes: OrdenDeAgente[] }>(
      `/auth/agentes/${encodeURIComponent(id)}/ordenes?dias=${dias}`),

  /** Abre «Morgan en tu PC» en ese equipo (4.17). No cambia nada: se decide allí. */
  abrirAjustesAgente: (id: string) =>
    request<{ success: boolean }>(`/auth/agentes/${encodeURIComponent(id)}/abrir-ajustes`, {
      method: 'POST',
    }, TIMEOUT_CUENTAS),

  revocarAgente: (id: string) =>
    request<{ success: boolean }>(`/auth/agentes/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }, TIMEOUT_CUENTAS),

  // --- Servicios externos (V1.9) ---
  //
  // El token NUNCA viaja al frontend: quien habla con GitHub es el backend.
  // Aquí solo se ve de qué cuenta se trata y qué permisos tiene.

  integraciones: () =>
    request<{ success: boolean; servicios: ServicioIntegrable[] }>('/integraciones'),

  /** Devuelve a dónde mandar el navegador para autorizar. La redirección la
   *  hace el cliente: si redirigiera el backend, la seguiría el propio fetch. */
  conectarIntegracion: (servicio: string) =>
    request<{ success: boolean; url: string }>(
      `/integraciones/${encodeURIComponent(servicio)}/conectar`,
      { method: 'POST' },
      TIMEOUT_CUENTAS,
    ),

  desconectarIntegracion: (servicio: string) =>
    request<{ success: boolean; desconectado: boolean; revocado_en_origen: boolean }>(
      `/integraciones/${encodeURIComponent(servicio)}`,
      { method: 'DELETE' },
      TIMEOUT_CUENTAS,
    ),

  /**
   * Todo lo que Morgan guarda de ti, en un JSON.
   *
   * Sin credenciales dentro: ni el hash de la contraseña, ni los tokens de los
   * servicios conectados, ni los identificadores de sesión. Un fichero de
   * exportación acaba en la carpeta de descargas y se comparte por correo.
   */
  exportarDatos: () =>
    request<{ success: boolean; datos: Record<string, unknown> }>(
      '/auth/datos', undefined, TIMEOUT_CUENTAS,
    ),

  /**
   * Borra la cuenta y todo lo que ha generado. No tiene deshacer.
   *
   * La contraseña se pide aunque haya sesión abierta: una sesión olvidada en un
   * equipo prestado basta para que otra persona lo borre todo de un clic.
   */
  eliminarCuenta: (password: string) =>
    request<{ success: boolean; borrado: Record<string, number> }>('/auth/cuenta', {
      method: 'DELETE',
      body: JSON.stringify({ password }),
    }, TIMEOUT_CUENTAS),
};
