/* Morgan Web — Ajustes → Este PC, dentro de Morgan para Windows (5.3)
 *
 * Lo que antes era la ventana pequeña del programa, ahora en Ajustes con el mismo diseño que el
 * resto (lo pedí el 2026-10-04: «no me gusta que haya dos ventanas»). Solo sale dentro del
 * programa, y solo pide lo inofensivo (lib/programa.ts).
 */

import { useCallback, useEffect, useState } from 'react';
import { programa, type AccionEnElPC, type ResumenDelPC, type VersionDelPrograma } from '../lib/programa';

/** Cada cuánto se mira el estado con el panel abierto: leerlo no lanza nada (tres ficheros). */
const CADA_MS = 3000;

function hora(segundos: number): string {
  return new Date(segundos * 1000).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });
}

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

export function PanelEstePC({ pc: dado }: { pc?: ReturnType<typeof programa> }) {
  // Uno para toda la vida del panel: si cambiara en cada pintada, el reloj se rehacía sin fin.
  const [pc] = useState(() => dado ?? programa());
  const [resumen, setResumen] = useState<ResumenDelPC | null>(null);
  const [version, setVersion] = useState<VersionDelPrograma | null>(null);
  const [ultimas, setUltimas] = useState<AccionEnElPC[] | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [error, setError] = useState('');

  const mirar = useCallback(() => {
    pc.resumen().then(setResumen).catch(() => setResumen(null));
  }, [pc]);

  useEffect(() => {
    mirar();
    pc.version().then(setVersion).catch(() => setVersion(null));
    pc.ultimas().then(setUltimas);
    const reloj = window.setInterval(mirar, CADA_MS);
    return () => window.clearInterval(reloj);
  }, [pc, mirar]);

  async function hacer(que: string, orden: () => Promise<unknown>, siFalla: string) {
    setOcupado(que);
    setError('');
    try {
      await orden();
    } catch (err) {
      setError(String(err).trim() || siFalla);
    } finally {
      setOcupado(null);
      mirar();
    }
  }

  const sinEmparejar = resumen?.clave === 'SinEmparejar';

  return (
    <>
      <p className="ajustes-intro">
        Morgan para Windows: lo que puede hacer en este PC y cómo está. Sigue en la bandeja, junto
        al reloj, aunque cierres la ventana.
      </p>
      {error && <div className="error-banner" role="alert">{error}</div>}

      <Fila titulo="Estado" ayuda={resumen?.texto ?? 'Mirando…'}>
        {resumen && !sinEmparejar && (
          <button
            type="button"
            className="ajustes-boton ajustes-boton--suave"
            disabled={!resumen.se_puede || ocupado !== null}
            onClick={() => void hacer(
              'pausa',
              resumen.pausar ? pc.pausar : pc.reanudar,
              resumen.pausar ? 'No se pudo pausar.' : 'No se pudo reanudar.',
            )}
          >
            {ocupado === 'pausa'
              ? (resumen.pausar ? 'Pausando…' : 'Reanudando…')
              : (resumen.pausar ? 'Pausar Morgan en este PC' : 'Reanudar Morgan')}
          </button>
        )}
      </Fila>

      {sinEmparejar ? (
        <Fila
          titulo="Conectar este PC"
          ayuda="Pide un código en Ajustes → Tu equipo y escríbelo en la pantalla del programa: allí te dice de qué cuenta es antes de usarlo."
        >
          <button type="button" className="ajustes-boton" onClick={() => void hacer('emparejar', pc.emparejar, 'No se pudo abrir.')}>
            Conectar
          </button>
        </Fila>
      ) : (
        <Fila titulo="Qué puede hacer y qué carpetas ve" ayuda="Las carpetas que ve Morgan y lo que le dejas hacer aquí.">
          <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => void hacer('ajustes', pc.abrirAjustes, 'No se pudo abrir.')}>
            Abrir
          </button>
        </Fila>
      )}

      <Fila titulo="Lo último que hizo en este PC" ayuda="Lo que le pidió Morgan a este PC en las últimas 24 horas." />
      {ultimas === null ? (
        <p className="ajustes-intro">Cargando…</p>
      ) : ultimas.length === 0 ? (
        <p className="ajustes-intro">Nada en las últimas 24 horas.</p>
      ) : (
        <ul className="este-pc__lista">
          {ultimas.map((u, i) => (
            <li key={`${u.cuando}-${i}`}>
              <span className="este-pc__estado">{u.estado}</span>
              <span className="este-pc__cuando">{hora(u.cuando)}</span>
              {u.que}
              {u.detalle && <span className="este-pc__detalle">{u.detalle}</span>}
            </li>
          ))}
        </ul>
      )}

      <Fila
        titulo="Versión del programa"
        ayuda={version?.nueva
          ? `Hay una versión nueva, la ${version.nueva}. Se descarga, se comprueba su firma y se instala sin más ventanas; si no conecta el PC, vuelve sola a la de ahora.`
          : version ? 'Al día. Se mira una vez al día.' : 'Mirando…'}
      >
        <span className="ajustes-valor">{version?.version ?? '—'}</span>
        {version?.nueva && (
          <button
            type="button"
            className="ajustes-boton"
            disabled={ocupado !== null}
            onClick={() => void hacer('actualizar', pc.actualizar, 'No se pudo actualizar.')}
          >
            {ocupado === 'actualizar' ? 'Actualizando…' : `Actualizar a la ${version.nueva}`}
          </button>
        )}
      </Fila>

      {!sinEmparejar && (
        <Fila titulo="Conectar con otra cuenta" ayuda="Se hace en la pantalla del programa, que pregunta antes de cambiar nada.">
          <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => void hacer('emparejar', pc.emparejar, 'No se pudo abrir.')}>
            Abrir
          </button>
        </Fila>
      )}
    </>
  );
}
