import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { morganAPI, MorganAPIError } from '../lib/api';
import type { UserSettings } from '../lib/api';
import { type Tema, TEMAS } from '../lib/tema';
import { PanelCuenta } from './PanelCuenta';
import { PanelEquipos } from './PanelEquipos';
import { PanelPermisos } from './PanelPermisos';
import { PanelTokens } from './PanelTokens';
import type { EstadoCuenta } from './Cuenta';
import {
  IconAudit, IconClose, IconCore, IconDatabase, IconExternal, IconInfo, IconMemory, IconPlug, IconSearch, IconShield, IconSparkle, IconUser,
} from './Icons';

/**
 * Ajustes de Morgan, como un panel encima de todo (V2.0.29).
 *
 * El diseño es el de la captura que di: secciones a la izquierda con un
 * buscador, y a la derecha filas con su nombre, una línea que explica qué hace y
 * el control. En el móvil ocupa la pantalla y las secciones pasan a una fila que
 * se desplaza de lado.
 *
 * La regla de siempre sigue mandando: **no hay ajustes que Morgan no pueda
 * respetar**. Del diseño se quedaron fuera el color de acento, la voz y los
 * enlaces a Discord: aquí no harían nada. Lo que se guarda en el servidor (perfil
 * y preferencias) acaba en el prompt de Morgan a través de su memoria; la
 * apariencia vive en el navegador, porque es de quien mira la pantalla.
 */

const VACIO: UserSettings = {
  nombre: '', ocupacion: '', sobre_mi: '', idioma: '', estilo_respuesta: '',
};

type Campo = {
  id: keyof UserSettings;
  etiqueta: string;
  ayuda: string;
  placeholder: string;
  largo?: boolean;
};

const PERFIL: Campo[] = [
  // Los textos de fondo describen QUÉ va en cada campo, no dan un ejemplo
  // concreto. Los primeros sí lo daban y estaban calcados de quien montó
  // Morgan: a cualquier otra persona le llegaba un formulario que parecía ya
  // relleno con la vida de un desconocido.
  { id: 'nombre', etiqueta: 'Nombre', ayuda: 'Cómo quieres que Morgan te llame.', placeholder: 'Tu nombre' },
  { id: 'ocupacion', etiqueta: 'Ocupación', ayuda: 'Le ayuda a ajustar el nivel de detalle.', placeholder: 'A qué te dedicas o qué estudias' },
  { id: 'sobre_mi', etiqueta: 'Sobre ti', ayuda: 'Lo que quieras que tenga siempre presente.', placeholder: 'Con qué trabajas, qué herramientas usas, qué te interesa…', largo: true },
  // Este sí lleva ejemplo: hay un formato que adivinar y no habla de nadie.
  { id: 'estilo_respuesta', etiqueta: 'Estilo de respuesta', ayuda: 'Por ejemplo: conciso, o con ejemplos de código.', placeholder: 'Conciso y directo' },
];

/**
 * El idioma se elige de una lista, como en el diseño, pero se guarda como texto:
 * es lo que Morgan lee en su prompt. Si alguien escribió otro idioma antes de
 * que existiera la lista, se conserva como una opción más en lugar de perderlo.
 */
const IDIOMAS = ['', 'Español', 'English', 'Português', 'Français', 'Deutsch', 'Italiano'];

export type SeccionAjustes = 'general' | 'personalizacion' | 'memoria' | 'datos' | 'cuenta' | 'equipos' | 'permisos' | 'api' | 'acerca';

const SECCIONES: { id: SeccionAjustes; titulo: string; icono: React.ReactNode; claves: string }[] = [
  { id: 'general', titulo: 'General', icono: <IconCore size={17} />, claves: 'apariencia tema claro oscuro azul sistema idioma lengua' },
  { id: 'personalizacion', titulo: 'Personalización', icono: <IconSparkle size={17} />, claves: 'perfil nombre ocupación sobre ti estilo respuesta' },
  { id: 'memoria', titulo: 'Memoria', icono: <IconMemory size={17} />, claves: 'recuerdos olvidar chat temporal historial' },
  { id: 'datos', titulo: 'Datos y privacidad', icono: <IconAudit size={17} />, claves: 'descargar exportar eliminar borrar cuenta auditoría registro privacidad claves' },
  { id: 'cuenta', titulo: 'Cuenta', icono: <IconUser size={17} />, claves: 'contraseña sesión sesiones cerrar correo email' },
  // El agente local (3.0-C): emparejar el PC con la cuenta.
  { id: 'equipos', titulo: 'Tu equipo', icono: <IconDatabase size={17} />, claves: 'pc ordenador equipo agente local emparejar código archivos carpetas' },
  // El permiso automático (4.6, pedido por mí): lo verde y amarillo sin esperar.
  { id: 'permisos', titulo: 'Permisos', icono: <IconShield size={17} />, claves: 'permiso permisos automático aprobar planes verde amarillo rojo preguntar' },
  // Tokens de API (V2.0.41). Aparte de la cuenta: es para quien conecta un
  // programa, y en «Cuenta» se perdería entre la contraseña y las sesiones.
  { id: 'api', titulo: 'Acceso por API', icono: <IconPlug size={17} />, claves: 'api token tokens programa script editor extensión desarrollador integrar revocar' },
  { id: 'acerca', titulo: 'Acerca de', icono: <IconInfo size={17} />, claves: 'versión código fuente github nube equipo' },
];

const REPOSITORIO = 'https://github.com/wv-Andy/Morgan';

function normalizar(texto: string): string {
  return texto.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

/** Una fila: nombre y explicación a la izquierda, el control a la derecha. */
function Fila({ titulo, ayuda, children }: { titulo: string; ayuda?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="ajustes-fila">
      <div className="ajustes-fila__texto">
        <span className="ajustes-fila__titulo">{titulo}</span>
        {ayuda && <span className="ajustes-fila__ayuda">{ayuda}</span>}
      </div>
      {children && <div className="ajustes-fila__control">{children}</div>}
    </div>
  );
}

export function SettingsView({
  apiOnline, tema, onTema, cuenta, entorno, seccionInicial = 'general',
  onCerrar, onIrA, onChatTemporal,
}: {
  apiOnline: boolean;
  tema: Tema;
  onTema: (t: Tema) => void;
  cuenta: EstadoCuenta;
  /** `cloud` o `local`, de /status. */
  entorno: string;
  seccionInicial?: SeccionAjustes;
  onCerrar: () => void;
  /** Llevar a otra vista de Morgan (memoria, auditoría) cerrando el panel. */
  onIrA: (vista: 'memory' | 'audit') => void;
  onChatTemporal: () => void;
}) {
  const [seccion, setSeccion] = useState<SeccionAjustes>(seccionInicial);
  const [busqueda, setBusqueda] = useState('');
  const [valores, setValores] = useState<UserSettings>(VACIO);
  const [guardados, setGuardados] = useState<UserSettings>(VACIO);
  // Se inicializa con el estado de la conexión: sin API no hay nada que cargar.
  const [cargando, setCargando] = useState(apiOnline);
  const [guardando, setGuardando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const caja = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!apiOnline) return;
    morganAPI.getSettings()
      .then(r => {
        const leidos = { ...VACIO, ...r.settings };
        setValores(leidos);
        setGuardados(leidos);
      })
      .catch(err => setError(err instanceof MorganAPIError ? err.message : 'No se pudieron cargar los ajustes.'))
      .finally(() => setCargando(false));
    morganAPI.health()
      .then(h => setVersion(h.version))
      .catch(() => { /* la versión es accesoria */ });
  }, [apiOnline]);

  // Escape cierra, como cualquier ventana. El foco entra en el panel al abrir:
  // si se quedara detrás, el teclado seguiría escribiendo en el chat tapado.
  useEffect(() => {
    caja.current?.focus();
    const porTecla = (e: KeyboardEvent) => { if (e.key === 'Escape') onCerrar(); };
    window.addEventListener('keydown', porTecla);
    return () => window.removeEventListener('keydown', porTecla);
  }, [onCerrar]);

  /**
   * Guarda en el servidor. `campos` dice qué se actualiza en pantalla: al
   * cambiar el idioma no se pisa lo que se esté escribiendo en el perfil.
   */
  const guardar = useCallback(async (nuevos: UserSettings, mensaje: string, campos?: (keyof UserSettings)[]) => {
    setGuardando(true);
    setError(null);
    setAviso(null);
    try {
      const r = await morganAPI.saveSettings(nuevos);
      const leidos = { ...VACIO, ...r.settings };
      setValores(v => (campos ? { ...v, ...Object.fromEntries(campos.map(k => [k, leidos[k]])) } : leidos));
      setGuardados(leidos);
      setAviso(mensaje);
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudieron guardar los ajustes.');
    } finally {
      setGuardando(false);
    }
  }, []);

  const visibles = useMemo(() => {
    const q = normalizar(busqueda.trim());
    if (!q) return SECCIONES;
    return SECCIONES.filter(s => normalizar(`${s.titulo} ${s.claves}`).includes(q));
  }, [busqueda]);

  // Si la búsqueda deja fuera la sección abierta, se enseña la primera que
  // coincide: un panel que dice «General» a la derecha mientras la lista de la
  // izquierda no lo contiene se lee como un fallo.
  const actual = visibles.some(s => s.id === seccion) ? seccion : visibles[0]?.id;
  const tituloActual = SECCIONES.find(s => s.id === actual)?.titulo ?? 'Sin resultados';

  const perfilCambiado = PERFIL.some(c => valores[c.id] !== guardados[c.id]);
  const idiomas = IDIOMAS.includes(valores.idioma) ? IDIOMAS : [...IDIOMAS, valores.idioma];
  const sinConexion = !apiOnline && (actual === 'general' || actual === 'personalizacion');

  const cambiarSeccion = (id: SeccionAjustes) => {
    setSeccion(id);
    setAviso(null);
    setError(null);
  };

  return (
    <div className="ajustes-velo" onMouseDown={e => { if (e.target === e.currentTarget) onCerrar(); }}>
      <div
        className="ajustes-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Ajustes"
        tabIndex={-1}
        ref={caja}
      >
        <aside className="ajustes-lado">
          <button type="button" className="ajustes-cerrar" onClick={onCerrar} aria-label="Cerrar ajustes">
            <IconClose />
          </button>

          <label className="ajustes-buscar">
            <IconSearch size={15} />
            <input
              type="search"
              value={busqueda}
              onChange={e => setBusqueda(e.target.value)}
              placeholder="Buscar ajustes"
              aria-label="Buscar ajustes"
            />
          </label>

          <nav className="ajustes-nav" aria-label="Secciones de ajustes">
            {visibles.map(s => (
              <button
                key={s.id}
                type="button"
                className={`ajustes-nav__item ${actual === s.id ? 'activa' : ''}`}
                onClick={() => cambiarSeccion(s.id)}
                aria-current={actual === s.id ? 'page' : undefined}
              >
                {s.icono}
                <span>{s.titulo}</span>
              </button>
            ))}
            {visibles.length === 0 && <p className="ajustes-nav__vacio">Nada coincide con «{busqueda.trim()}».</p>}
          </nav>
        </aside>

        <section className="ajustes-contenido" aria-label={tituloActual}>
          <h2 className="ajustes-contenido__titulo">{tituloActual}</h2>

          {sinConexion && (
            <div className="error-banner">
              Sin conexión con Morgan: el perfil y las preferencias no se pueden leer ni guardar.
              La apariencia sí, porque se guarda en este navegador.
            </div>
          )}
          {error && <div className="error-banner" role="alert">{error}</div>}
          {aviso && <div className="ajustes-aviso" role="status">{aviso}</div>}

          {actual === 'general' && (
            <>
              <Fila titulo="Apariencia" ayuda="«Del sistema» sigue la configuración de este dispositivo. Se guarda en este navegador.">
                <select
                  className="ajustes-select"
                  value={tema}
                  onChange={e => onTema(e.target.value as Tema)}
                  aria-label="Apariencia"
                >
                  {TEMAS.map(t => <option key={t.id} value={t.id}>{t.etiqueta}</option>)}
                </select>
              </Fila>
              <Fila titulo="Idioma de las respuestas" ayuda="En automático, Morgan responde en el idioma en que le escribes.">
                <select
                  className="ajustes-select"
                  value={valores.idioma}
                  disabled={!apiOnline || cargando || guardando}
                  onChange={e => void guardar({ ...guardados, idioma: e.target.value }, 'Idioma guardado.', ['idioma'])}
                  aria-label="Idioma de las respuestas"
                >
                  {idiomas.map(i => <option key={i || 'auto'} value={i}>{i || 'Automático'}</option>)}
                </select>
              </Fila>
            </>
          )}

          {actual === 'personalizacion' && (
            <>
              <p className="ajustes-intro">
                Se guarda en la memoria de Morgan y viaja en cada conversación: lo tiene
                presente sin que se lo repitas.
              </p>
              {cargando ? <p className="ajustes-intro">Cargando…</p> : PERFIL.map(c => (
                <label key={c.id} className="ajustes-fila ajustes-fila--campo">
                  <span className="ajustes-fila__texto">
                    <span className="ajustes-fila__titulo">{c.etiqueta}</span>
                    <span className="ajustes-fila__ayuda">{c.ayuda}</span>
                  </span>
                  {c.largo ? (
                    <textarea
                      className="ajustes-entrada"
                      value={valores[c.id]}
                      onChange={e => setValores(v => ({ ...v, [c.id]: e.target.value }))}
                      placeholder={c.placeholder}
                      rows={3}
                      maxLength={1000}
                      disabled={!apiOnline}
                    />
                  ) : (
                    <input
                      className="ajustes-entrada"
                      type="text"
                      value={valores[c.id]}
                      onChange={e => setValores(v => ({ ...v, [c.id]: e.target.value }))}
                      placeholder={c.placeholder}
                      maxLength={500}
                      disabled={!apiOnline}
                    />
                  )}
                </label>
              ))}
              <div className="ajustes-pie">
                {perfilCambiado && (
                  <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => setValores(guardados)}>
                    Descartar
                  </button>
                )}
                <button
                  type="button"
                  className="ajustes-boton"
                  onClick={() => void guardar(valores, 'Guardado. Morgan ya lo tiene en cuenta.')}
                  disabled={!perfilCambiado || guardando || !apiOnline}
                >
                  {guardando ? 'Guardando…' : 'Guardar cambios'}
                </button>
              </div>
            </>
          )}

          {actual === 'memoria' && (
            <>
              <Fila titulo="Lo que Morgan recuerda de ti" ayuda="Puedes revisarlo y borrar cualquier dato.">
                <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => onIrA('memory')}>
                  Abrir
                </button>
              </Fila>
              <Fila titulo="Chat temporal" ayuda="Una conversación que no se guarda ni en el historial ni en la memoria.">
                <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={onChatTemporal}>
                  Empezar uno
                </button>
              </Fila>
              <Fila
                titulo="Tus conversaciones"
                ayuda="Se renombran, fijan, archivan o eliminan desde la barra lateral, con el botón ⋯ de cada una."
              />
            </>
          )}

          {actual === 'datos' && (
            <>
              <Fila titulo="Registro de acciones" ayuda="Cada herramienta que usa Morgan queda anotada: qué hizo y si se autorizó.">
                <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => onIrA('audit')}>
                  Ver
                </button>
              </Fila>
              <Fila
                titulo="Tus claves y contraseñas"
                ayuda="Las claves de los servicios nunca llegan al navegador, y las protecciones de Morgan no se pueden desactivar desde aquí."
              />
              <PanelCuenta cuenta={cuenta} parte="datos" />
            </>
          )}

          {actual === 'cuenta' && <PanelCuenta cuenta={cuenta} parte="cuenta" />}

          {actual === 'equipos' && <PanelEquipos cuenta={cuenta} />}

          {actual === 'permisos' && <PanelPermisos apiOnline={apiOnline} />}

          {actual === 'api' && <PanelTokens cuenta={cuenta} />}

          {actual === 'acerca' && (
            <>
              <Fila titulo="Versión" ayuda="La de Morgan y la de esta web.">
                <span className="ajustes-valor">
                  {version ?? '—'} · web {__MORGAN_COMMIT__}
                </span>
              </Fila>
              <Fila titulo="Dónde corre" ayuda={entorno === 'cloud'
                ? 'En la nube: no tiene acceso a tu equipo.'
                : 'En tu equipo: puede trabajar con tus archivos, bajo el sistema de permisos.'}
              >
                <span className="ajustes-valor">{entorno === 'cloud' ? 'Nube' : entorno ? 'Tu equipo' : '—'}</span>
              </Fila>
              <Fila titulo="Código fuente" ayuda="Morgan es un proyecto abierto.">
                <a className="ajustes-enlace" href={REPOSITORIO} target="_blank" rel="noreferrer">
                  GitHub <IconExternal />
                </a>
              </Fila>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
