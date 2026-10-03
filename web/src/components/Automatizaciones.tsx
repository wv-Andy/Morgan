import { useCallback, useEffect, useState } from 'react';
import { morganAPI, MorganAPIError } from '../lib/api';
import type { Automatizacion, Aviso } from '../lib/api';
import { Markdown } from './Markdown';

/**
 * Las automatizaciones y su bandeja (4.14).
 *
 * Arriba, la bandeja: lo que contó cada ejecución (mi decisión 3: los avisos van
 * solo aquí, ni correo ni notificaciones). Abajo, las programadas, con lo único que se
 * hace con ellas desde aquí: pausar, reanudar y borrar. **Crearlas no**: se piden a
 * Morgan en el chat, que las propone como un plan que apruebas.
 */

const ESTADOS: Record<Aviso['estado'], string> = {
  hecha: 'Hecha',
  fallo: 'No salió',
  saltada: 'Saltada',
};

// Las clases de las tarjetas de tareas: hecha en verde, fallo en rojo, saltada en ámbar.
const CLASE: Record<Aviso['estado'], string> = {
  hecha: 'completed',
  fallo: 'failed',
  saltada: 'waiting',
};

function fecha(segundos: number): string {
  return new Date(segundos * 1000).toLocaleString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

export function AutomatizacionesView({ apiOnline, onLeidos }: { apiOnline: boolean; onLeidos?: () => void }) {
  const [avisos, setAvisos] = useState<Aviso[]>([]);
  const [autos, setAutos] = useState<Automatizacion[]>([]);
  const [cargando, setCargando] = useState(apiOnline);
  const [error, setError] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    if (!apiOnline) return;
    try {
      const [b, a] = await Promise.all([morganAPI.avisos(), morganAPI.automatizaciones()]);
      setAvisos(b.avisos);
      setAutos(a.automatizaciones);
      setError(null);
      // Verlos es leerlos: sin un botón de «marcar como leído» que nadie pulsaría.
      if (b.sin_leer > 0) {
        await morganAPI.marcarAvisosLeidos();
        onLeidos?.();
      }
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudieron cargar las automatizaciones.');
    } finally {
      setCargando(false);
    }
  }, [apiOnline, onLeidos]);

  useEffect(() => { void cargar(); }, [cargar]);

  const actuar = useCallback(async (a: Automatizacion, accion: 'pausar' | 'reanudar' | 'borrar') => {
    if (accion === 'borrar' && !window.confirm(`¿Borrar «${a.nombre}»? No se puede deshacer.`)) return;
    try {
      if (accion === 'pausar') await morganAPI.pausarAutomatizacion(a.id);
      else if (accion === 'reanudar') await morganAPI.reanudarAutomatizacion(a.id);
      else await morganAPI.borrarAutomatizacion(a.id);
      setError(null);
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo completar la acción.');
    } finally {
      void cargar();
    }
  }, [cargar]);

  return (
    <>
      <div className="view-header">
        <h2>Automatizaciones</h2>
        <span className="counter">{autos.filter(a => a.activa).length}</span>
      </div>

      <div className="view-body">
        {error && <div className="error-banner">{error}</div>}

        {cargando ? (
          <p className="ajustes-nota">Cargando…</p>
        ) : (
          <div className="tarea-lista">
            <h3 className="auto-titulo">Bandeja</h3>
            {avisos.length === 0 ? (
              <p className="ajustes-nota">Aquí aparecerá lo que cuente cada ejecución.</p>
            ) : avisos.map(av => (
              <div key={av.id} className={`tarea auto-aviso${av.leido ? '' : ' sin-leer'}`}>
                <div className="tarea-cabecera">
                  <span className="tarea-objetivo">{av.titulo}</span>
                  <span className={`tarea-estado ${CLASE[av.estado]}`}>{ESTADOS[av.estado]}</span>
                </div>
                <p className="ajustes-nota">{fecha(av.creado_en)}</p>
                <div className="auto-texto"><Markdown text={av.texto} /></div>
                {av.herramientas.length > 0 && (
                  <p className="ajustes-nota">Usó: {av.herramientas.join(', ')}</p>
                )}
              </div>
            ))}

            <h3 className="auto-titulo">Programadas</h3>
            {autos.length === 0 ? (
              <p className="ajustes-nota">
                Todavía no hay ninguna. Pídesela a Morgan en el chat, por ejemplo: «cada lunes a las 9,
                mira si tengo cambios sin subir en mi proyecto y avísame». Te la propondrá como un plan
                para que la apruebes.
              </p>
            ) : autos.map(a => (
              <div key={a.id} className="tarea">
                <div className="tarea-cabecera">
                  <span className="tarea-objetivo">{a.nombre}</span>
                  <span className={`tarea-estado ${a.activa ? 'running' : ''}`}>
                    {a.activa ? (a.esperando_pc ? 'Esperando a tu PC' : 'Activa') : 'Pausada'}
                  </span>
                </div>
                {a.pasos ? (
                  // Pasos fijos (4.15): se enseña lo que hace exactamente, como se aprobó.
                  <ol className="auto-pasos" aria-label="Hace exactamente">
                    {a.pasos.map((p, i) => (
                      <li key={i}>
                        {p.descripcion || p.herramienta} <code>{p.herramienta}</code>
                        {Object.keys(p.argumentos).length > 0 && (
                          <span className="ajustes-nota">
                            {' '}{Object.entries(p.argumentos).map(([k, v]) => `${k}: ${String(v)}`).join(' · ')}
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="tarea-actual">{a.instruccion}</p>
                )}
                <p className="ajustes-nota">
                  {a.cuando}{a.necesita_pc ? ' · usa tu PC' : ''}
                  {a.activa && a.proxima_texto ? ` · próxima: ${a.proxima_texto}` : ''}
                  {a.ultimo_estado ? ` · la última: ${ESTADOS[a.ultimo_estado].toLowerCase()}` : ''}
                </p>
                <div className="tarea-acciones">
                  <button className="header-action" onClick={() => actuar(a, a.activa ? 'pausar' : 'reanudar')}>
                    {a.activa ? 'Pausar' : 'Reanudar'}
                  </button>
                  <button className="header-action" onClick={() => actuar(a, 'borrar')}>Borrar</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
