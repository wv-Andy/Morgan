import { useCallback, useEffect, useState } from 'react';
import { morganAPI, MorganAPIError } from '../lib/api';
import type { EstadoTarea, TaskItem } from '../lib/api';

/**
 * Progreso de las tareas (V1.5).
 *
 * Hasta ahora, un encargo de ocho llamadas a herramientas era un indicador
 * girando durante minuto y medio. Aquí se ve qué se está haciendo, qué salió y
 * qué falló.
 *
 * El progreso y el paso actual **los calcula el servidor**: repetir esa regla en
 * TypeScript garantizaría que las dos versiones acabaran discrepando.
 */

const ETIQUETAS: Record<EstadoTarea, string> = {
  pending: 'Pendiente',
  running: 'En marcha',
  waiting: 'Esperando confirmación',
  failed: 'No se pudo',
  completed: 'Completada',
  cancelled: 'Cancelada',
};

const TERMINALES: EstadoTarea[] = ['completed', 'failed', 'cancelled'];

function Barra({ valor, estado }: { valor: number; estado: EstadoTarea }) {
  const porcentaje = Math.round(Math.min(1, Math.max(0, valor)) * 100);
  return (
    <div
      className={`tarea-barra ${estado}`}
      role="progressbar"
      aria-valuenow={porcentaje}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="tarea-barra-relleno" style={{ width: `${porcentaje}%` }} />
      <span className="tarea-barra-cifra">{porcentaje}%</span>
    </div>
  );
}

function Paso({ paso }: { paso: TaskItem['pasos'][number] }) {
  // Un simbolo por estado. El del fallo no es decorativo: es lo que permite ver
  // de un vistazo donde se torcio algo.
  const marca = paso.estado === 'completed' ? '✓'
    : paso.estado === 'failed' ? '✕'
      : paso.estado === 'running' ? '→' : '○';

  return (
    <li className={`tarea-paso ${paso.estado}`}>
      <span className="tarea-paso-marca" aria-hidden="true">{marca}</span>
      <span className="tarea-paso-texto">
        {paso.descripcion}
        {paso.herramienta && <span className="tarea-paso-tool">{paso.herramienta}</span>}
        {/* Solo se marca lo COMPROBADO. Un paso sin verificar no lleva sello:
            poner uno que dijera "sin comprobar" en la mayoria de los pasos
            seria ruido, y poner uno de "correcto" seria mentir. */}
        {paso.verificacion === 'correcto' && (
          <span className="tarea-paso-verificado" title="Se comprobó el efecto">
            comprobado
          </span>
        )}
        {paso.verificacion === 'incorrecto' && (
          <span className="tarea-paso-nover" title={paso.verificacion_motivo ?? ''}>
            no salió
          </span>
        )}
        {paso.error && <span className="tarea-paso-error">{paso.error}</span>}
      </span>
    </li>
  );
}

export function TareasView({ apiOnline }: { apiOnline: boolean }) {
  const [tareas, setTareas] = useState<TaskItem[]>([]);
  const [cargando, setCargando] = useState(apiOnline);
  const [error, setError] = useState<string | null>(null);
  const [abierta, setAbierta] = useState<string | null>(null);

  const cargar = useCallback(() => {
    if (!apiOnline) return;
    morganAPI.tasks()
      .then(r => setTareas(r.tasks))
      .catch(err => setError(err instanceof MorganAPIError ? err.message : 'No se pudieron cargar las tareas.'))
      .finally(() => setCargando(false));
  }, [apiOnline]);

  useEffect(() => { cargar(); }, [cargar]);

  // Mientras haya alguna viva se refresca solo: una tarea en marcha cambia sin
  // que el usuario toque nada, y obligarle a pulsar "actualizar" seria absurdo.
  //
  // La dependencia es si HAY alguna viva, no la lista entera. Con la lista, cada
  // carga cambiaba el estado, el efecto se volvia a ejecutar y el intervalo se
  // recreaba: el reloj empezaba de cero en cada vuelta y, con respuestas lentas,
  // podia no llegar a disparar nunca.
  const hayAlgunaViva = tareas.some(t => !TERMINALES.includes(t.estado));

  useEffect(() => {
    if (!hayAlgunaViva) return;
    const id = setInterval(cargar, 3_000);
    return () => clearInterval(id);
  }, [hayAlgunaViva, cargar]);

  const actuar = useCallback(async (id: string, accion: 'cancel' | 'retry') => {
    try {
      await (accion === 'cancel' ? morganAPI.cancelTask(id) : morganAPI.retryTask(id));
      setError(null);
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo completar la acción.');
    } finally {
      cargar();
    }
  }, [cargar]);

  return (
    <>
      <div className="view-header">
        <h2>Tareas</h2>
        <span className="counter">{tareas.length}</span>
        <button className="header-action" onClick={() => cargar()}>Actualizar</button>
      </div>

      <div className="view-body">
        {error && <div className="error-banner">{error}</div>}

        {cargando ? (
          <p className="ajustes-nota">Cargando…</p>
        ) : tareas.length === 0 ? (
          <p className="ajustes-nota">
            Todavía no hay tareas. Morgan crea una cuando le pides algo que lleva
            varios pasos, para que puedas seguir el progreso.
          </p>
        ) : (
          <div className="tarea-lista">
            {tareas.map(t => (
              <div key={t.id} className={`tarea ${t.estado}`}>
                <button
                  className="tarea-cabecera"
                  onClick={() => setAbierta(a => (a === t.id ? null : t.id))}
                  aria-expanded={abierta === t.id}
                >
                  <span className="tarea-objetivo">{t.objetivo}</span>
                  <span className={`tarea-estado ${t.estado}`}>{ETIQUETAS[t.estado]}</span>
                </button>

                <Barra valor={t.progreso} estado={t.estado} />

                {t.paso_actual && !TERMINALES.includes(t.estado) && (
                  <p className="tarea-actual">→ {t.paso_actual}</p>
                )}

                {abierta === t.id && (
                  <>
                    {t.pasos.length > 0 && (
                      <ul className="tarea-pasos">
                        {t.pasos.map(p => <Paso key={p.orden} paso={p} />)}
                      </ul>
                    )}

                    {t.resultado && <p className="tarea-resultado">{t.resultado}</p>}
                    {t.error && <p className="tarea-error">{t.error}</p>}
                    {t.intentos > 0 && (
                      <p className="ajustes-nota">Intentos: {t.intentos}</p>
                    )}

                    <div className="tarea-acciones">
                      {!TERMINALES.includes(t.estado) && (
                        <button className="header-action" onClick={() => actuar(t.id, 'cancel')}>
                          Cancelar
                        </button>
                      )}
                      {(t.estado === 'failed' || t.estado === 'cancelled') && (
                        <button className="header-action" onClick={() => actuar(t.id, 'retry')}>
                          Reintentar
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
