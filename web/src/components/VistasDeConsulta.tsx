/* Morgan Web — Vistas de consulta: herramientas, memoria, estado y auditoría */

import { useCallback, useEffect, useState } from 'react';

import { MorganAPIError, morganAPI } from '../lib/api';
import type {
  AuditItem,
  CapabilityItem,
  MemoryItem,
  ServiceStatusItem,
  StatusResponse,
  ToolSchema,
} from '../lib/api';
import {
  IconAudit,
  IconMemory,
  IconRefresh,
  IconTrash,
} from './Icons';
import { RiskBadge } from './RiskBadge';
import { formatDate } from '../lib/formato';

/**
 * Las cuatro vistas que solo consultan: herramientas, memoria, estado y
 * auditoría.
 *
 * Salieron de `App.tsx` cuando ese fichero pasó de 1800 líneas. Van juntas y no
 * en cuatro ficheros porque comparten forma —cargar, mostrar una lista, un
 * botón de actualizar— y separarlas del todo repartiría esa forma en cuatro
 * sitios donde diverge sola.
 */

export function ToolsView() {
  const [tools, setTools] = useState<ToolSchema[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [activeFilter, setActiveFilter] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    morganAPI.tools()
      .then(res => { setTools(res.tools); setCategories(res.categories); })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const filtered = activeFilter === 'all' ? tools : tools.filter(t => t.category === activeFilter);

  return (
    <>
      <div className="view-header">
        <h2>Herramientas</h2>
        <span className="counter">{filtered.length} de {tools.length}</span>
      </div>

      <div className="view-body">
        <div className="view-inner">
          <div className="filter-chips">
            <button id="filter-all" className={`chip ${activeFilter === 'all' ? 'active' : ''}`} onClick={() => setActiveFilter('all')}>Todas</button>
            {categories.map(cat => (
              <button key={cat} id={`filter-${cat}`} className={`chip ${activeFilter === cat ? 'active' : ''}`} onClick={() => setActiveFilter(cat)}>
                {cat}
              </button>
            ))}
          </div>

          {loading && <div className="loader"><div className="spinner" /><span>Cargando herramientas…</span></div>}
          {error && <div className="error-banner">{error}</div>}

          {!loading && !error && (
            <div className="tools-grid">
              {filtered.map(tool => (
                <div key={tool.name} className="tool-item" id={`tool-${tool.name}`}>
                  <div className="tool-item-header">
                    <span className="tool-item-name">{tool.name}</span>
                    <RiskBadge level={tool.risk_level} />
                  </div>
                  <p className="tool-item-desc">{tool.description}</p>
                  <div className="tool-item-footer">
                    <span className="domain-tag">{tool.category}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export function MemoryView() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    morganAPI.memory()
      .then(res => setMemories(res.memories))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  // La regla avisa de que 'load' fija estado de forma sincrona (setLoading,
  // setError) al montar. Aqui es justo el caso que la propia regla admite:
  // sincronizar con un sistema externo, la API de Morgan. No hay valor que
  // derivar en el render porque los datos aun no existen.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  const handleDelete = async (key: string) => {
    try {
      await morganAPI.forgetFact(key);
      setMemories(prev => prev.filter(m => m.key !== key));
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'Error al eliminar el recuerdo');
    }
  };

  return (
    <>
      <div className="view-header">
        <h2>Memoria</h2>
        <span className="counter">{memories.length} recuerdos</span>
        <button className="header-action" onClick={load}><IconRefresh /> Actualizar</button>
      </div>

      <div className="view-body">
        <div className="view-inner">
          {loading && <div className="loader"><div className="spinner" /><span>Cargando memoria…</span></div>}
          {error && <div className="error-banner">{error}</div>}

          {!loading && !error && memories.length === 0 && (
            <div className="empty-state">
              <IconMemory size={26} />
              <p>Morgan no tiene recuerdos guardados todavía. Cuéntale tu nombre o tus preferencias en el chat.</p>
            </div>
          )}

          {!loading && !error && memories.length > 0 && (
            <div className="memory-list">
              {memories.map(m => (
                <div key={m.key} className="memory-item" id={`memory-${m.key}`}>
                  <span className="memory-cat">{m.category}</span>
                  <span className="memory-key">{m.key}</span>
                  <span className="memory-value">{m.value}</span>
                  <span className="memory-date">{formatDate(m.updated_at)}</span>
                  <button
                    className="memory-delete-btn"
                    onClick={() => handleDelete(m.key)}
                    aria-label={`Olvidar ${m.key}`}
                    title="Olvidar este recuerdo"
                  >
                    <IconTrash />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * Lo que la vista de estado cuenta de cada servicio, en palabras de quien usa
 * Morgan (V2.0.29).
 *
 * El servidor manda nombres internos —`database.remote`, `llm`— y detalles como
 * qué proveedor de modelos contesta o por qué API sale el correo. Eso sirve para
 * diagnosticar, no a quien abre la web: aquí se traduce a qué puede hacer Morgan
 * y qué no. Las dos bases de datos se enseñan como una sola cosa, «tus datos»,
 * con el peor de sus dos estados: si una falla, tus datos están en riesgo.
 */
const SERVICIOS: { claves: string[]; nombre: string; para: string }[] = [
  { claves: ['llm'], nombre: 'Conversar y razonar', para: 'Que Morgan pueda contestarte' },
  { claves: ['internet'], nombre: 'Internet', para: 'Buscar en la web y leer páginas' },
  { claves: ['database.local', 'database.remote'], nombre: 'Tus datos', para: 'Guardar conversaciones, memoria y archivos' },
  { claves: ['correo'], nombre: 'Correo', para: 'Confirmar tu correo y recuperar la contraseña' },
];

const GRAVEDAD: Record<string, number> = { available: 0, unknown: 1, degraded: 2, unavailable: 3 };

const ESTADO_LEGIBLE: Record<string, string> = {
  available: 'Funciona',
  degraded: 'Con problemas',
  unavailable: 'No disponible',
  unknown: 'Sin comprobar',
};

/** Las capacidades, sin «LLM» ni «entorno cloud». */
const CAPACIDAD_LEGIBLE: Record<string, string> = {
  'Conversación (LLM)': 'Conversar',
  'Memoria y historial': 'Recordar y guardar el historial',
  'Búsqueda web': 'Buscar en internet',
  'Archivos del equipo': 'Trabajar con los archivos de tu equipo',
  'Terminal y procesos': 'Ejecutar programas en tu equipo',
};

export function StatusView() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    morganAPI.status()
      .then(s => setStatus(s))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  // La regla avisa de que 'load' fija estado de forma sincrona (setLoading,
  // setError) al montar. Aqui es justo el caso que la propia regla admite:
  // sincronizar con un sistema externo, la API de Morgan. No hay valor que
  // derivar en el render porque los datos aun no existen.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  const enLaNube = status?.environment === 'cloud';

  const servicios = (status?.services ?? []).length === 0 ? [] : SERVICIOS
    .map(def => {
      const suyos = (status?.services ?? []).filter((s: ServiceStatusItem) => def.claves.includes(s.name));
      if (suyos.length === 0) return null;
      const peor = suyos.reduce((a, b) => ((GRAVEDAD[b.state] ?? 1) > (GRAVEDAD[a.state] ?? 1) ? b : a));
      return { ...def, estado: peor.state };
    })
    .filter((s): s is { claves: string[]; nombre: string; para: string; estado: ServiceStatusItem['state'] } => s !== null);

  return (
    <>
      <div className="view-header">
        <h2>Estado</h2>
        <button className="header-action" onClick={load}><IconRefresh /> Actualizar</button>
      </div>

      <div className="view-body">
        <div className="view-inner">
          {loading && <div className="loader"><div className="spinner" /><span>Comprobando…</span></div>}
          {error && <div className="error-banner">{error}</div>}

          {!loading && !error && status && (
            <>
              <div className={`mode-banner ${status.mode}`}>
                <span className={`status-dot ${status.mode === 'normal' ? 'normal' : 'degraded'}`} />
                {status.mode === 'normal'
                  ? 'Morgan funciona con normalidad.'
                  : 'Morgan responde, pero ahora mismo no puede conversar. Vuelve a intentarlo en un rato.'}
                <span className="mode-meta">
                  {enLaNube ? 'En la nube' : 'En tu equipo'} · {status.tools_count} herramientas
                </span>
              </div>

              {servicios.length > 0 && (
                <>
                  <h3 className="section-title">Servicios</h3>
                  <div className="service-list">
                    {servicios.map(s => (
                      <div key={s.nombre} className="service-row">
                        <span className={`status-dot ${s.estado}`} />
                        <span className="service-name">{s.nombre}</span>
                        <span className="service-detail">{s.para}</span>
                        <span className={`service-state ${s.estado}`}>{ESTADO_LEGIBLE[s.estado] ?? 'Sin comprobar'}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {status.capabilities?.length > 0 && (
                <>
                  <h3 className="section-title">Qué puede hacer aquí</h3>
                  <div className="capability-list">
                    {status.capabilities.map((c: CapabilityItem) => (
                      <div key={c.name} className="capability-row">
                        <span className={`capability-mark ${c.available ? 'yes' : 'no'}`}>
                          {c.available ? '✓' : '✗'}
                        </span>
                        <span className="capability-name">{CAPACIDAD_LEGIBLE[c.name] ?? c.name}</span>
                        {!c.available && (
                          <span className="capability-reason">
                            {enLaNube ? 'Solo en el Morgan de tu equipo' : 'No disponible ahora'}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}

              {status.sync?.enabled && (
                <>
                  <h3 className="section-title">Copia en la nube</h3>
                  <div className="sync-row" id="sync-status">
                    <span className={`status-dot ${status.sync.remote_available ? 'available' : 'unavailable'}`} />
                    <span className="capability-name">
                      {status.sync.remote_available
                        ? (status.sync.pending > 0 ? 'Subiendo los últimos cambios' : 'Al día')
                        : 'Sin conexión: lo pendiente se subirá al recuperarse'}
                    </span>
                    {status.sync.pending > 0 && (
                      <span className="sync-counts">
                        {status.sync.pending} pendiente{status.sync.pending === 1 ? '' : 's'}
                      </span>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

export function AuditView() {
  const [logs, setLogs] = useState<AuditItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    morganAPI.audit(100)
      .then(res => setLogs([...res.records].reverse()))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  // La regla avisa de que 'load' fija estado de forma sincrona (setLoading,
  // setError) al montar. Aqui es justo el caso que la propia regla admite:
  // sincronizar con un sistema externo, la API de Morgan. No hay valor que
  // derivar en el render porque los datos aun no existen.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="view-header">
        <h2>Auditoría de seguridad</h2>
        <span className="counter">{logs.length} eventos</span>
        <button className="header-action" onClick={load}><IconRefresh /> Actualizar</button>
      </div>

      <div className="view-body">
        <div className="view-inner">
          {loading && <div className="loader"><div className="spinner" /><span>Cargando registros…</span></div>}
          {error && <div className="error-banner">{error}</div>}

          {!loading && !error && logs.length === 0 && (
            <div className="empty-state">
              <IconAudit size={26} />
              <p>No hay eventos de auditoría todavía. Aparecerán en cuanto Morgan use una herramienta.</p>
            </div>
          )}

          {!loading && !error && logs.length > 0 && (
            <div className="audit-list">
              {logs.map((item, i) => (
                <div key={i} className="audit-item" id={`audit-${i}`}>
                  <span className="audit-time">{item.timestamp}</span>
                  <RiskBadge level={item.risk_level} />
                  <span className="audit-tool">{item.tool}</span>
                  <span className={`audit-result ${item.authorized ? 'allowed' : 'denied'}`}>
                    {item.authorized ? 'PERMITIDO' : 'DENEGADO'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
