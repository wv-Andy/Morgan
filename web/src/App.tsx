import { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';
import { morganAPI, MorganAPIError, setApiToken, getApiToken, defaultSessionId, newSessionId, API_ES_REMOTA, API_BASE_URL, TIMEOUT_DESPERTAR, espacioActual, fijarEspacioActual, EVENTO_ESPACIO_PERDIDO } from './lib/api';
import type { SessionItem, SessionChanges, EspacioItem } from './lib/api';
import { IconPlus, IconChat, IconTools, IconMemory, IconStatus, IconAudit, IconMenu, IconPlug, IconSearch, IconTemporal, IconCore, IconTasks, IconClip, IconClock } from './components/Icons';
import {
  PantallaAcceso,
  PantallaCargando,
  hayTokenDeRecuperacion,
  useCuenta,
  type EstadoCuenta,
} from './components/Cuenta';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SettingsView, type SeccionAjustes } from './components/SettingsView';
import { TareasView } from './components/Tareas';
import { AutomatizacionesView } from './components/Automatizaciones';
import { PlanesPendientes } from './components/Planes';
import { ArchivosView } from './components/Archivos';
import { SelectorDeEspacio } from './components/Espacios';
import { BarraCuenta } from './components/BarraCuenta';
import { DescargaWindows } from './components/DescargaWindows';
import { IntegracionesView } from './components/Integraciones';
import { AuditView, MemoryView, StatusView, ToolsView } from './components/VistasDeConsulta';
import { ChatView } from './components/Chat';
import { LogoMorgan } from './components/Logo';
import { AvisoSinVerificar, useVerificacionPorEnlace } from './components/VerificarCorreo';
import { aplicarTema, guardarTema, leerTema } from './lib/tema';
import type { Tema } from './lib/tema';

// ─── Tipos ───────────────────────────────────────────────────────────────────

type View = 'chat' | 'tasks' | 'automatizaciones' | 'files' | 'tools' | 'memory' | 'status' | 'audit' | 'servicios';

function TokenOverlay({ onSubmit }: { onSubmit: (token: string) => void }) {
  const [value, setValue] = useState(getApiToken());

  return (
    <div className="connection-overlay">
      <div className="connection-card">
        <h2>Morgan pide autenticación</h2>
        <p>
          Este backend tiene <code className="inline-code">MORGAN_API_TOKEN</code> definido.
          Introduce el token para continuar; se guardará en este navegador.
        </p>
        <input
          id="token-input"
          className="token-input"
          type="password"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onSubmit(value.trim()); }}
          placeholder="Token de la API"
          aria-label="Token de la API"
          autoFocus
        />
        <button id="token-submit" className="btn-primary" onClick={() => onSubmit(value.trim())}>
          Guardar y conectar
        </button>
      </div>
    </div>
  );
}

function ConnectionOverlay({ onRetry, despertando }: { onRetry: () => void; despertando?: boolean }) {
  return (
    <div className="connection-overlay">
      <div className="connection-card">
        <h2>Morgan API desconectada</h2>
        {API_ES_REMOTA ? (
          <>
            <p>No se pudo conectar con el backend de Morgan en:</p>
            <code>{API_BASE_URL}</code>
            <p className="overlay-nota">
              Si acabas de abrir la página, puede estar despertando: los servicios
              gratuitos se duermen tras un rato sin uso.
            </p>
          </>
        ) : (
          <>
            <p>No se pudo conectar con el backend. Asegúrate de que el servidor esté en ejecución:</p>
            <code>python -m src.api.server</code>
          </>
        )}
        <button
          id="retry-connection-btn"
          className="btn-primary"
          onClick={onRetry}
          disabled={despertando}
        >
          {despertando ? 'Despertando el servicio…' : 'Reintentar conexión'}
        </button>
      </div>
    </div>
  );
}

// ─── Raíz ────────────────────────────────────────────────────────────────────

function inicialDe(nombre: string): string {
  return (nombre.trim()[0] ?? '?').toUpperCase();
}

const NAV_ITEMS: { id: View; icon: React.ReactNode; label: string }[] = [
  { id: 'chat', icon: <IconChat />, label: 'Chat' },
  { id: 'tasks', icon: <IconTasks />, label: 'Tareas' },
  { id: 'automatizaciones', icon: <IconClock />, label: 'Automatizaciones' },
  { id: 'files', icon: <IconClip size={16} />, label: 'Archivos' },
  { id: 'tools', icon: <IconTools />, label: 'Herramientas' },
  { id: 'memory', icon: <IconMemory />, label: 'Memoria' },
  { id: 'status', icon: <IconStatus />, label: 'Estado' },
  { id: 'audit', icon: <IconAudit />, label: 'Auditoría' },
  { id: 'servicios', icon: <IconPlug size={16} />, label: 'Servicios' },
];

/**
 * Morgan por dentro. Solo se monta cuando ya se sabe quien eres: o hay sesion, o
 * es el Morgan de tu equipo, que no pide ninguna.
 *
 * Montarlo antes no era una molestia estetica: sus efectos piden conversaciones,
 * archivos y ajustes nada mas aparecer, y esas peticiones saldrian sin sesion
 * para volver en 401 y dejar la pantalla a medio pintar.
 */
function AppAutenticada({
  cuenta, resultadoVerificacion,
}: {
  cuenta: EstadoCuenta;
  /** Lo que pasó al abrir un enlace de confirmación, si se abrió uno. */
  resultadoVerificacion?: { texto: string; error: boolean } | null;
}) {
  const [view, setView] = useState<View>('chat');
  const [apiStatus, setApiStatus] = useState<'loading' | 'online' | 'offline'>('loading');
  //: El mismo valor, para leerlo sin que la lectura genere una dependencia. Ver
  //: `loadSessions`: tenerlo en las dependencias la pedia dos veces al arrancar.
  const estadoApi = useRef(apiStatus);
  const [systemMode, setSystemMode] = useState<string>('loading');
  const [toolsCount, setToolsCount] = useState<number>(0);
  //: Los avisos de las automatizaciones sin leer (4.14), en la navegación.
  const [sinLeer, setSinLeer] = useState<number>(0);

  // Se mira al conectar y cada minuto: una automatización termina sin que la web haga nada.
  // Solo el número, sin los textos. Un fallo no dice nada: el número se queda como estaba.
  useEffect(() => {
    if (apiStatus !== 'online') return;
    const mirar = () => {
      morganAPI.avisosSinLeer().then(r => setSinLeer(r.sin_leer)).catch(() => undefined);
    };
    mirar();
    const id = setInterval(mirar, 60_000);
    return () => clearInterval(id);
  }, [apiStatus]);
  const [environment, setEnvironment] = useState<string>('');
  const [showOverlay, setShowOverlay] = useState(false);
  const [needsToken, setNeedsToken] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Con que formulario abrir la pantalla de acceso desde los botones de la
  // esquina. Solo llega a usarse en el Morgan local: en la nube no se entra
  // aqui sin sesion, asi que nunca hay nada que abrir.
  const [acceso, setAcceso] = useState<'entrar' | 'crear' | null>(null);
  // Lo que salio mal en la ultima operacion sobre una conversacion.
  //
  // Antes no existia: renombrar, fijar, archivar y eliminar se tragaban su
  // error «y la lista se queda como estaba». En produccion, donde todos los
  // POST se rechazaban, eso significaba pulsar Eliminar, confirmar, y que no
  // ocurriera absolutamente nada. Un boton que falla en silencio es
  // indistinguible de un boton roto.
  const [avisoSesiones, setAvisoSesiones] = useState('');
  const [tema, setTema] = useState<Tema>(() => leerTema());
  // El panel de ajustes abierto, y en qué sección. `null` es cerrado.
  const [ajustes, setAjustes] = useState<SeccionAjustes | null>(null);
  const cerrarAjustes = useCallback(() => setAjustes(null), []);
  const [sessionId, setSessionId] = useState<string>(defaultSessionId());
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  // El espacio de trabajo seleccionado (V2.2). `null` es «General». Se lee de lo
  // que recuerda el navegador, para volver a aparecer donde se dejó.
  const [espacio, setEspacio] = useState<string | null>(() => espacioActual());
  const [espacios, setEspacios] = useState<EspacioItem[]>([]);
  const [maxInstrucciones, setMaxInstrucciones] = useState(2000);
  const [busqueda, setBusqueda] = useState('');
  const [verArchivadas, setVerArchivadas] = useState(false);
  // El menu se pinta con posicion fija y coordenadas tomadas del boton: la lista
  // de conversaciones tiene overflow para poder desplazarse, y ese recorte se
  // comia el desplegable. Sacarlo del flujo es lo unico que lo evita de verdad.
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [renombrando, setRenombrando] = useState<string | null>(null);
  // Un chat temporal no se guarda: ni en la base de datos ni en la memoria
  // permanente. Se marca en la interfaz para que nunca sea una sorpresa.
  const [esTemporal, setEsTemporal] = useState(false);
  // Un contador que sube al acabar cada turno. El panel de planes lo mira para
  // volver a consultar: sin esto, un plan propuesto durante la conversacion no
  // aparecia hasta recargar la pagina, y Morgan se quedaba esperando una
  // aprobacion que la persona no tenia forma de dar.
  const [turnosCompletados, setTurnosCompletados] = useState(0);
  /** 4.0: el plan que la persona acaba de aprobar, hasta que el chat manda el turno que lo ejecuta. */
  const [planAprobado, setPlanAprobado] = useState<{ id: string; objetivo: string } | null>(null);
  const anotarTurno = useCallback(() => setTurnosCompletados(n => n + 1), []);
  const failedChecks = useRef(0);
  // Con un backend remoto dormido, la espera puede pasar del minuto. Sin decirlo,
  // la pantalla parece colgada.
  const [despertando, setDespertando] = useState(false);

  // Una sola peticion: /status ya implica que la API responde, asi que la llamada
  // previa a /health era redundante.
  const checkHealth = useCallback(async (showLoading = true) => {
    if (showLoading) setApiStatus('loading');
    try {
      // Con backend remoto se concede el tiempo de un arranque en frio: con los
      // 15 s de una consulta normal, la comprobacion se cancelaba antes de que el
      // servicio llegara a despertar, y el reintento repetia el mismo error.
      const espera = setTimeout(() => setDespertando(true), 6_000);
      let status;
      try {
        status = await morganAPI.status(API_ES_REMOTA ? TIMEOUT_DESPERTAR : undefined);
      } finally {
        clearTimeout(espera);
        setDespertando(false);
      }
      setApiStatus('online');
      setSystemMode(status.mode);
      setToolsCount(status.tools_count);
      setEnvironment(status.environment ?? '');
      setShowOverlay(false);
      setNeedsToken(false);
      failedChecks.current = 0;
    } catch (err) {
      // Un 401 no es una caida del backend: falta el token.
      if (err instanceof MorganAPIError && err.code === 'UNAUTHORIZED') {
        setApiStatus('offline');
        setSystemMode('offline');
        setNeedsToken(true);
        setShowOverlay(false);
        return;
      }

      // Un fallo aislado no significa que Morgan se haya caido: puede estar
      // ocupado atendiendo un turno largo. Solo se marca desconectado tras
      // varios intentos seguidos, para no parpadear.
      failedChecks.current += 1;
      if (failedChecks.current < 2) {
        setSystemMode('degraded');
        return;
      }

      setApiStatus('offline');
      setSystemMode('offline');
      setShowOverlay(true);
    }
  }, []);

  // La regla avisa de que 'load' fija estado de forma sincrona (setLoading,
  // setError) al montar. Aqui es justo el caso que la propia regla admite:
  // sincronizar con un sistema externo, la API de Morgan. No hay valor que
  // derivar en el render porque los datos aun no existen.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { checkHealth(); }, [checkHealth]);

  const loadSessions = useCallback(() => {
    // Se pide en cuanto se puede, sin esperar a la comprobacion de estado.
    //
    // Antes la condicion era `!== 'online'`, o sea que la lista esperaba a que
    // respondiera `/status` —el endpoint mas pesado, que comprueba todos los
    // componentes—. Con el backend dormido eso son 35,7 segundos medidos con la
    // aplicacion mostrando nada, y solo entonces empezaba a pedir datos.
    //
    // La lista no necesita saber si el sistema esta sano para poder pedirse: si
    // falla, no se muestra, que es lo que ya hacia el `catch` de abajo. Lo unico
    // que se evita es insistir cuando ya se sabe que no hay nadie al otro lado.
    //
    // Se lee de una referencia y NO de la variable de estado, y eso es el
    // arreglo de un fallo que introdujo el cambio anterior: con `apiStatus` en
    // las dependencias, pasar de 'loading' a 'online' cambiaba la identidad de
    // esta funcion, el efecto de abajo se repetia y la lista se pedia DOS VECES
    // al arrancar. Medido en produccion: /sessions a los 4.705 ms y otra vez a
    // los 6.584. Un viaje de 1,2 s tirado.
    if (estadoApi.current === 'offline') return;
    morganAPI.sessions({
      archived: verArchivadas ? 'true' : 'false',
      q: busqueda,
    })
      // Las de cero mensajes se ocultan salvo que se este buscando: una sesion
      // recien creada y sin usar no aporta nada al historial, pero si el usuario
      // la busca por su nombre, esconderla resultaria desconcertante.
      .then(res => setSessions(
        busqueda.trim() ? res.sessions : res.sessions.filter(s => s.message_count > 0),
      ))
      .catch(() => { /* la lista es accesoria: si falla, no se muestra */ });
  }, [verArchivadas, busqueda]);

  useEffect(() => {
    // La busqueda espera a que el usuario deje de teclear: sin esto cada pulsacion
    // seria una peticion.
    const id = setTimeout(loadSessions, busqueda ? 250 : 0);
    return () => clearTimeout(id);
  }, [loadSessions, busqueda]);

  // Reintentar la lista SOLO al volver de una caida, no en cualquier cambio de
  // estado. Sin esta distincion habia que elegir entre pedirla dos veces al
  // arrancar o no reintentarla nunca cuando el backend volviera.
  useEffect(() => {
    const volvio = estadoApi.current === 'offline' && apiStatus === 'online';
    estadoApi.current = apiStatus;
    if (volvio) loadSessions();
  }, [apiStatus, loadSessions]);

  // Con posicion fija, el menu no acompana a la lista al desplazarse: quedaria
  // flotando en un sitio que ya no corresponde. Se cierra, que es lo esperable.
  useEffect(() => {
    if (!menu) return;

    const cerrar = () => setMenu(null);
    const porTecla = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenu(null); };

    // 'click' en lugar de 'mousedown': asi el clic sobre una opcion del menu llega
    // a ejecutarse antes de que este desaparezca.
    window.addEventListener('click', cerrar);
    window.addEventListener('resize', cerrar);
    window.addEventListener('scroll', cerrar, true);
    window.addEventListener('keydown', porTecla);
    return () => {
      window.removeEventListener('click', cerrar);
      window.removeEventListener('resize', cerrar);
      window.removeEventListener('scroll', cerrar, true);
      window.removeEventListener('keydown', porTecla);
    };
  }, [menu]);

  // 'sistema' obliga a seguir escuchando: la preferencia del sistema operativo
  // puede cambiar con la pagina abierta.
  useEffect(() => aplicarTema(tema), [tema]);

  const cambiarTema = useCallback((t: Tema) => {
    setTema(t);
    guardarTema(t);
  }, []);

  const startNewChat = useCallback((temporal = false) => {
    setSessionId(newSessionId());
    setEsTemporal(temporal);
    setView('chat');
    setSidebarOpen(false);
  }, []);

  const cargarEspacios = useCallback(() => {
    if (estadoApi.current === 'offline') return;
    morganAPI.espacios()
      .then(r => {
        setEspacios(r.espacios);
        setMaxInstrucciones(r.max_instrucciones);
      })
      // Sin la lista se sigue pudiendo usar Morgan en General.
      .catch(() => { /* accesoria, como la de conversaciones */ });
  }, []);

  useEffect(() => { cargarEspacios(); }, [cargarEspacios]);

  const cambiarEspacio = useCallback((id: string | null) => {
    fijarEspacioActual(id);
    setEspacio(id);
    // Se empieza una conversación nueva. Una conversación pertenece a un
    // espacio: seguir en la de antes mezclaría la conversación de un proyecto
    // con los archivos de otro.
    startNewChat(false);
    // La lista se pide aquí y no poniendo `espacio` en las dependencias de
    // `loadSessions`: la función no lo usa —el espacio viaja en la cabecera, que
    // sale de lo que `fijarEspacioActual` acaba de guardar—, y una dependencia
    // que no se usa es justo lo que avisa el lint.
    loadSessions();
  }, [startNewChat, loadSessions]);

  // El servidor dice que el espacio seleccionado ya no existe: se borró desde
  // otro dispositivo, o era de otra cuenta usada en este navegador.
  useEffect(() => {
    const alPerderlo = () => {
      setEspacio(null);
      cargarEspacios();
      loadSessions();
      setAvisoSesiones('El espacio de trabajo que tenías seleccionado ya no existe. Estás en General.');
    };
    window.addEventListener(EVENTO_ESPACIO_PERDIDO, alPerderlo);
    return () => window.removeEventListener(EVENTO_ESPACIO_PERDIDO, alPerderlo);
  }, [cargarEspacios, loadSessions]);

  // Las cuatro operaciones son un PATCH; se refresca la lista al terminar porque
  // el orden puede cambiar (fijar) o la conversacion desaparecer (archivar).
  const cambiarSesion = useCallback(async (id: string, cambios: SessionChanges) => {
    setAvisoSesiones('');
    try {
      await morganAPI.updateSession(id, cambios);
      loadSessions();
    } catch (err) {
      setAvisoSesiones(
        err instanceof MorganAPIError
          ? err.message
          : 'No se pudo cambiar la conversación.',
      );
    } finally {
      setMenu(null);
      setRenombrando(null);
    }
  }, [loadSessions]);

  const borrarSesion = useCallback(async (id: string, titulo: string) => {
    // Confirmacion antes de una eliminacion permanente, que es lo que pide la
    // especificacion: archivar es reversible, esto no.
    if (!window.confirm(`¿Eliminar «${titulo}» y todos sus mensajes?

Esta acción no se puede deshacer. Si solo quieres quitarla de en medio, archívala.`)) {
      return;
    }
    setAvisoSesiones('');
    try {
      await morganAPI.deleteSession(id);
      if (id === sessionId) startNewChat();
      loadSessions();
    } catch (err) {
      setAvisoSesiones(
        err instanceof MorganAPIError
          ? err.message
          : 'No se pudo eliminar la conversación.',
      );
    } finally {
      setMenu(null);
    }
  }, [loadSessions, sessionId, startNewChat]);

  const openSession = useCallback((id: string) => {
    setSessionId(id);
    setEsTemporal(false);
    setView('chat');
    setSidebarOpen(false);
  }, []);

  // Revalidacion periodica: antes el estado solo se comprobaba al montar, de modo
  // que si la API caia el usuario seguia viendo "Operativo".
  useEffect(() => {
    const id = setInterval(() => checkHealth(false), 30_000);
    return () => clearInterval(id);
  }, [checkHealth]);

  const statusLabel = apiStatus === 'loading'
    ? 'Conectando…'
    : apiStatus === 'online'
      ? (systemMode === 'degraded' ? 'Modo degradado' : 'Operativo')
      : 'Desconectado';

  const statusClass = apiStatus === 'online'
    ? (systemMode === 'degraded' ? 'degraded' : 'normal')
    : apiStatus === 'loading' ? 'loading' : 'offline';

  const go = (v: View) => { setView(v); setSidebarOpen(false); };

  const nombreVisible = cuenta.usuario?.display_name
    || cuenta.usuario?.email
    || (cuenta.local ? 'Este equipo' : 'Mi cuenta');
  const nombreDelEspacio = espacios.find(e => e.id === espacio)?.nombre ?? 'General';

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} id="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <span className="brand-mark"><LogoMorgan size={20} /></span>
            <span className="brand-texto">
              <span className="brand-nombre">Morgan</span>
              <span className="brand-lema">Tu agente personal</span>
            </span>
            {/* Dónde corre este Morgan. Dato real de /status, no un adorno: en
                la nube no puede tocar tu equipo, y conviene saberlo de un vistazo. */}
            {environment && (
              <span className="env-tag" title={environment === 'cloud' ? 'Morgan en la nube: sin acceso a tu equipo' : 'Morgan en tu equipo'}>
                {environment === 'cloud' ? 'Nube' : 'Local'}
              </span>
            )}
          </div>

          <button id="nav-new-chat" className="boton-nueva" onClick={() => startNewChat(false)}>
            <IconPlus size={16} />
            Nueva conversación
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="Navegación principal">
          <button
            id="nav-temp-chat"
            className="nav-item"
            onClick={() => startNewChat(true)}
            title="No se guarda en el historial ni en la memoria permanente"
          >
            <span className="nav-icon"><IconTemporal /></span>
            Chat temporal
          </button>

          {/* Ajustes no va en la lista: es un panel que se abre desde el
              engranaje del pie, junto a tu cuenta, como en el diseño. */}
          {NAV_ITEMS.map(item => (
            <button
              key={item.id}
              id={`nav-${item.id}`}
              className={`nav-item ${view === item.id ? 'active' : ''}`}
              onClick={() => go(item.id)}
              aria-current={view === item.id ? 'page' : undefined}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
              {item.id === 'tools' && toolsCount > 0 && <span className="nav-count">{toolsCount}</span>}
              {item.id === 'automatizaciones' && sinLeer > 0 && (
                <span className="nav-count nav-count--aviso" aria-label={`${sinLeer} avisos sin leer`}>{sinLeer}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-section">
          <SelectorDeEspacio
            espacios={espacios}
            actual={espacio}
            maxInstrucciones={maxInstrucciones}
            deshabilitado={apiStatus !== 'online'}
            onCambiar={cambiarEspacio}
            onCambiado={cargarEspacios}
          />
        </div>

        <div className="sidebar-section">
          <div className="sidebar-section-label">
            {verArchivadas ? 'Archivadas' : 'Conversaciones'}
            <button
              className="section-action"
              onClick={() => setVerArchivadas(v => !v)}
              title={verArchivadas ? 'Volver al historial' : 'Ver archivadas'}
            >
              {verArchivadas ? 'Historial' : 'Archivadas'}
            </button>
          </div>

          <div className="session-search">
            <IconSearch />
            <input
              type="search"
              value={busqueda}
              onChange={e => setBusqueda(e.target.value)}
              placeholder="Buscar conversación"
              aria-label="Buscar conversación por título"
            />
          </div>

          {avisoSesiones && (
            <p className="sidebar-note sidebar-note--error" role="alert">
              {avisoSesiones}
            </p>
          )}

          {sessions.length === 0 ? (
            <p className="sidebar-note">
              {busqueda.trim()
                ? `Ninguna conversación coincide con «${busqueda.trim()}».`
                : verArchivadas
                  ? 'No hay conversaciones archivadas.'
                  : 'Todavía no hay conversaciones guardadas.'}
            </p>
          ) : (
            <div className="session-list">
              {sessions.map(s => (
                <div key={s.id} className={`session-row ${s.id === sessionId ? 'active' : ''}`}>
                  {renombrando === s.id ? (
                    <input
                      className="session-rename"
                      defaultValue={s.title ?? ''}
                      autoFocus
                      aria-label="Nuevo título"
                      onKeyDown={e => {
                        if (e.key === 'Enter') cambiarSesion(s.id, { title: e.currentTarget.value });
                        if (e.key === 'Escape') setRenombrando(null);
                      }}
                      onBlur={e => cambiarSesion(s.id, { title: e.currentTarget.value })}
                    />
                  ) : (
                    <button
                      className="session-item"
                      onClick={() => openSession(s.id)}
                      title={s.title ?? s.id}
                    >
                      {s.pinned && <span className="session-pin" aria-label="Fijada">•</span>}
                      <span className="session-title">{s.title ?? 'Sin título'}</span>
                      <span className="session-count">{s.message_count}</span>
                    </button>
                  )}

                  <button
                    className="session-menu-btn"
                    onClick={e => {
                      e.stopPropagation();
                      if (menu?.id === s.id) { setMenu(null); return; }
                      const r = e.currentTarget.getBoundingClientRect();
                      // Si no cabe hacia abajo, se abre hacia arriba.
                      // Crece con los espacios: hay una opción «Mover a…» por cada uno.
                      const alto = 160 + 30 * (espacios.length + 1);
                      const haciaArriba = r.bottom + alto > window.innerHeight;
                      setMenu({
                        id: s.id,
                        x: r.right,
                        y: haciaArriba ? r.top - alto : r.bottom + 4,
                      });
                    }}
                    aria-label={`Opciones de ${s.title ?? 'la conversación'}`}
                    aria-expanded={menu?.id === s.id}
                  >
                    ⋯
                  </button>

                  {menu?.id === s.id && (
                    <div
                      className="session-menu"
                      role="menu"
                      style={{ left: menu.x, top: menu.y }}
                    >
                      <button role="menuitem" onClick={() => { setRenombrando(s.id); setMenu(null); }}>
                        Renombrar
                      </button>
                      <button role="menuitem" onClick={() => cambiarSesion(s.id, { pinned: !s.pinned })}>
                        {s.pinned ? 'No fijar' : 'Fijar'}
                      </button>
                      <button role="menuitem" onClick={() => cambiarSesion(s.id, { archived: !s.archived })}>
                        {s.archived ? 'Desarchivar' : 'Archivar'}
                      </button>
                      {/* Sustituye a «Agrupar»: los grupos eran texto libre sin
                          nada que los reuniera, y se convirtieron en espacios. */}
                      {[{ id: '', nombre: 'General' }, ...espacios]
                        .filter(e => (e.id || null) !== (s.espacio_id ?? null))
                        .map(e => (
                          <button
                            key={e.id || 'general'}
                            role="menuitem"
                            onClick={() => cambiarSesion(s.id, { espacio_id: e.id })}
                          >
                            Mover a {e.nombre}
                          </button>
                        ))}
                      <button
                        role="menuitem"
                        className="danger"
                        onClick={() => borrarSesion(s.id, s.title ?? 'esta conversación')}
                      >
                        Eliminar
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="sidebar-footer">
          {/* Quién eres y cómo está Morgan, abajo del todo. El engranaje lleva a
              Ajustes; cerrar sesión está en el menú del avatar de arriba. */}
          <DescargaWindows />
          <div className="tarjeta-usuario">
            <span className="tarjeta-usuario__avatar" aria-hidden="true">
              {cuenta.usuario?.avatar_url
                ? <img src={cuenta.usuario.avatar_url} alt="" />
                : inicialDe(nombreVisible)}
            </span>
            <span className="tarjeta-usuario__datos">
              <span className="tarjeta-usuario__nombre">{nombreVisible}</span>
              <span className="sidebar-status">
                <span className={`status-dot ${statusClass}`} />
                {statusLabel}
              </span>
            </span>
            <button
              id="nav-settings"
              className={`tarjeta-usuario__ajustes ${ajustes ? 'active' : ''}`}
              onClick={() => { setAjustes('general'); setSidebarOpen(false); }}
              aria-label="Ajustes"
              aria-haspopup="dialog"
              title="Ajustes"
            >
              <IconCore size={18} />
            </button>
          </div>
          {/* Que version se esta usando. Responde «¿esta ya desplegado el
              arreglo?» sin tener que comparar huellas de ficheros: Vercel
              compila con sus propias variables, asi que el nombre del bundle
              nunca coincide con el de un build local. */}
          <div className="sidebar-version" title="Version desplegada">
            {__MORGAN_COMMIT__}
          </div>
        </div>
      </aside>

      <div
        className={`sidebar-backdrop ${sidebarOpen ? 'visible' : ''}`}
        onClick={() => setSidebarOpen(false)}
        aria-hidden="true"
      />

      <main className="main-content" id="main-content">
        {/* El resultado de haber abierto un enlace de confirmación. Va antes
            que el aviso: si acaba de confirmarse, lo que importa es decirlo. */}
        {resultadoVerificacion && (
          <div
            className={`aviso-verificar ${resultadoVerificacion.error ? 'aviso-verificar--error' : ''}`}
            role="status"
          >
            <div className="aviso-verificar__texto">{resultadoVerificacion.texto}</div>
          </div>
        )}

        {/* Dentro de la aplicacion, no delante: se puede seguir usando Morgan
            con el en pantalla. Eso es lo que separa un aviso de una puerta. */}
        {cuenta.autenticado && cuenta.usuario?.email_verificado === false && (
          <AvisoSinVerificar correo={cuenta.usuario?.email} />
        )}

        {/* La barra de arriba. Todo lo que hay en ella es un dato o un control
            real: dónde estás, si Morgan responde y tu cuenta. Del diseño se
            quitaron los botones que aquí no harían nada (compartir, avisos). */}
        <header className="barra-superior">
          {/* Solo visible en pantallas estrechas, donde la barra lateral se oculta. */}
          <button className="sidebar-toggle" onClick={() => setSidebarOpen(o => !o)} aria-label="Abrir menú">
            <IconMenu />
          </button>

          <div className="pastilla pastilla--contexto" title="Espacio de trabajo actual">
            <LogoMorgan size={15} className="pastilla__logo" />
            <span className="pastilla__fuerte">Morgan</span>
            <span className="pastilla__sep" aria-hidden="true">·</span>
            <span className="pastilla__suave">{nombreDelEspacio}</span>
          </div>

          <div className="barra-superior__derecha">
            <div className={`pastilla pastilla--estado ${statusClass}`} role="status">
              <span className={`status-dot ${statusClass}`} />
              <span className="pastilla__texto-estado">
                {statusLabel}
                {apiStatus === 'online' && toolsCount > 0 && (
                  <span className="pastilla__suave"> · {toolsCount} herramientas</span>
                )}
              </span>
            </div>

            <BarraCuenta
              cuenta={cuenta}
              alAbrirAjustes={() => setAjustes('cuenta')}
              alPedirAcceso={setAcceso}
            />
          </div>
        </header>

        <ErrorBoundary area={NAV_ITEMS.find(i => i.id === view)?.label ?? 'esta vista'} key={view}>
          {view === 'chat' && (
            <>
              {/* Va ARRIBA del chat a proposito: un plan pendiente bloquea el
                  trabajo, y esconderlo en otra pestana significaria que Morgan
                  se queda esperando una decision que nadie sabe que debe tomar.
                  Cuando no hay ninguno, no pinta nada. */}
              <PlanesPendientes
                apiOnline={apiStatus === 'online'}
                sessionId={sessionId}
                refrescoPedido={turnosCompletados}
                onAprobado={plan => setPlanAprobado({ id: plan.id, objetivo: plan.objetivo })}
              />
              <ChatView
                key={sessionId}
                estadoApi={apiStatus}
                sessionId={sessionId}
                sistema={{ herramientas: toolsCount, entorno: environment }}
                onConversationSaved={loadSessions}
                onTurnoTerminado={anotarTurno}
                temporal={esTemporal}
                planAprobado={planAprobado}
                onPlanEnviado={() => setPlanAprobado(null)}
              />
            </>
          )}
          {view === 'tools' && <ToolsView />}
          {view === 'memory' && <MemoryView />}
          {view === 'status' && <StatusView />}
          {view === 'audit' && <AuditView />}
          {view === 'tasks' && <TareasView apiOnline={apiStatus === 'online'} />}
          {view === 'automatizaciones' && (
            <AutomatizacionesView apiOnline={apiStatus === 'online'} onLeidos={() => setSinLeer(0)} />
          )}
          {/* Con el espacio en la clave: al cambiar de espacio se vuelve a montar
              y pide la lista del nuevo, en lugar de enseñar la del anterior. */}
          {view === 'files' && <ArchivosView key={espacio ?? 'general'} apiOnline={apiStatus === 'online'} />}
          {view === 'servicios' && <IntegracionesView apiOnline={apiStatus === 'online'} />}
        </ErrorBoundary>
      </main>

      {/* Ajustes es un panel por encima de todo, no una vista: se abre y se
          cierra sin perder dónde estabas (V2.0.29). */}
      {ajustes && (
        <ErrorBoundary area="Ajustes">
          <SettingsView
            apiOnline={apiStatus === 'online'}
            tema={tema}
            onTema={cambiarTema}
            cuenta={cuenta}
            entorno={environment}
            seccionInicial={ajustes}
            onCerrar={cerrarAjustes}
            onIrA={v => { setAjustes(null); go(v); }}
            onChatTemporal={() => { setAjustes(null); startNewChat(true); }}
          />
        </ErrorBoundary>
      )}

      {/* Se pinta por encima de todo, no en lugar de Morgan: quien entra aqui
          desde el boton no ha perdido la sesion, solo quiere crearse una cuenta,
          y desmontar la aplicacion tiraria su conversacion a medias. */}
      {acceso && (
        <PantallaAcceso
          modoInicial={acceso}
          alCancelar={() => setAcceso(null)}
          alEntrar={() => window.location.reload()}
        />
      )}

      {needsToken && (
        <TokenOverlay
          onSubmit={token => { setApiToken(token); checkHealth(); }}
        />
      )}
      {showOverlay && !needsToken && <ConnectionOverlay onRetry={() => checkHealth()} despertando={despertando} />}
    </div>
  );
}

/**
 * La puerta.
 *
 * Tres estados, y el orden importa:
 *
 * 1. Mientras no se sabe quien eres, no se pinta nada. Ensenar el formulario
 *    aqui lo haria parpadear en cada recarga a quien ya tiene sesion.
 * 2. Si el despliegue exige cuenta y no hay sesion, la pantalla de acceso.
 * 3. Si no, Morgan. En local eso es siempre: es tu equipo y tus claves.
 */
export default function App() {
  const cuenta = useCuenta();

  // Confirmar el correo se hace ANTES de saber quién eres, y sin exigir sesión:
  // quien abre el enlace puede estar en otro navegador, o en el móvil. Pedirle
  // que inicie sesión primero convertiría un clic en un trámite.
  //
  // El hook consume el token una sola vez y limpia la barra de direcciones.
  const verificacion = useVerificacionPorEnlace(cuenta.refrescar);

  // El enlace del correo manda sobre todo lo demas, y va PRIMERO a proposito.
  //
  // Restablecer la contrasena no necesita saber quien eres: es justo para
  // cuando no puedes entrar. Supeditarlo a preguntarselo al backend hacia que,
  // si esa consulta tardaba o fallaba —el servicio dormido tarda hasta un
  // minuto—, el formulario no llegara a aparecer y el enlace del correo se
  // perdiera. Quien viene de un correo ve su formulario al instante, aunque el
  // backend todavia este despertando.
  if (hayTokenDeRecuperacion()) {
    return <PantallaAcceso alEntrar={() => window.location.reload()} />;
  }

  if (cuenta.cargando) {
    return <PantallaCargando />;
  }

  if (!cuenta.local && !cuenta.autenticado) {
    return (
      <PantallaAcceso
        alEntrar={() => window.location.reload()}
        avisoExterno={verificacion.resultado}
      />
    );
  }

  return <AppAutenticada cuenta={cuenta} resultadoVerificacion={verificacion.resultado} />;
}
