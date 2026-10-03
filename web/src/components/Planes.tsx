/* Morgan Web — Planes pendientes de aprobación (V1.6) */

import { useCallback, useEffect, useState } from 'react';
import { MorganAPIError, morganAPI, type PasoPlaneado, type PlanItem } from '../lib/api';

/**
 * Lo que Morgan piensa hacer, antes de hacerlo.
 *
 * Aparece **arriba del chat**, no en una pestaña aparte, y es deliberado: un plan
 * pendiente bloquea el trabajo, así que esconderlo en otra vista significaría que
 * Morgan se queda esperando una decisión que la persona no sabe que tiene que
 * tomar.
 *
 * Cuando no hay nada pendiente, este componente no pinta nada. Un panel vacío
 * permanente enseña a ignorar esa zona de la pantalla, y entonces deja de servir
 * el día que sí hay algo.
 */

const RIESGOS: Record<string, string> = {
  safe: 'sin riesgo',
  low_risk: 'riesgo bajo',
  moderate: 'riesgo medio',
  high_risk: 'riesgo alto',
  critical: 'riesgo crítico',
  sensitive: 'riesgo crítico',
};

function etiquetaDeRiesgo(riesgo: string): string {
  return RIESGOS[riesgo] ?? riesgo;
}

function Paso({ paso }: { paso: PasoPlaneado }) {
  const argumentos = Object.entries(paso.argumentos ?? {});

  return (
    <li className={`plan-paso ${paso.necesita_confirmacion ? 'delicado' : ''}`}>
      <div className="plan-paso-cabecera">
        <span className="plan-paso-orden">{paso.orden}</span>
        <span className="plan-paso-texto">{paso.descripcion}</span>
        {paso.herramienta && (
          <code className="plan-paso-herramienta">{paso.herramienta}</code>
        )}
      </div>

      {argumentos.length > 0 && (
        <dl className="plan-paso-args">
          {argumentos.map(([clave, valor]) => (
            <div key={clave}>
              <dt>{clave}</dt>
              <dd>{valor}</dd>
            </div>
          ))}
        </dl>
      )}

      {paso.motivo && <p className="plan-paso-motivo">{paso.motivo}</p>}
    </li>
  );
}

export function PlanesPendientes({
  apiOnline,
  sessionId,
  refrescoPedido = 0,
  onAprobado,
}: {
  apiOnline: boolean;
  sessionId?: string;
  /** Cambia cada vez que termina un turno de chat.
   *
   *  Sin esto, un plan creado durante la conversación NO aparecía hasta recargar
   *  la página: Morgan se quedaba esperando una aprobación que la persona no
   *  tenía forma de dar. Un contador que cambia es más simple que un canal de
   *  eventos y basta para lo que hace falta. */
  refrescoPedido?: number;
  /** 4.0 (decisión mía): aprobar es la orden. El chat manda el turno que lo ejecuta. */
  onAprobado?: (plan: PlanItem) => void;
}) {
  const [planes, setPlanes] = useState<PlanItem[]>([]);
  const [decidiendo, setDecidiendo] = useState<string | null>(null);
  const [error, setError] = useState('');

  const cargar = useCallback(async () => {
    if (!apiOnline) return;
    try {
      const respuesta = await morganAPI.planes(true, sessionId);
      setPlanes(respuesta.planes);
    } catch {
      // Un fallo al consultar no debe ensuciar la pantalla: si no se pueden
      // leer los planes, lo que hay que enseñar es nada, no un error rojo sobre
      // algo que la persona no pidió.
      setPlanes([]);
    }
  }, [apiOnline, sessionId]);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void cargar();
  }, [cargar, refrescoPedido]);

  async function decidir(plan: PlanItem, aprobar: boolean) {
    setDecidiendo(plan.id);
    setError('');

    try {
      if (aprobar) {
        await morganAPI.aprobarPlan(plan.id);
        onAprobado?.(plan);
      } else {
        await morganAPI.rechazarPlan(plan.id);
      }
      await cargar();
    } catch (err) {
      setError(
        err instanceof MorganAPIError
          ? err.message
          : 'No se pudo registrar tu decisión.',
      );
    } finally {
      setDecidiendo(null);
    }
  }

  if (planes.length === 0) return null;

  return (
    <div className="planes">
      {planes.map((plan) => (
        <article key={plan.id} className="plan">
          <header className="plan-cabecera">
            <div>
              <h4>Morgan quiere hacer esto</h4>
              <p className="plan-objetivo">{plan.objetivo}</p>
            </div>
            <span className={`plan-riesgo ${plan.riesgo}`}>
              {etiquetaDeRiesgo(plan.riesgo)}
            </span>
          </header>

          <ol className="plan-pasos">
            {plan.pasos.map((paso) => (
              <Paso key={paso.orden} paso={paso} />
            ))}
          </ol>

          <p className="plan-nota">
            Nada de esto se ha ejecutado todavía. Si lo rechazas, no queda nada a
            medias.
          </p>

          <div className="plan-acciones">
            <button
              type="button"
              className="plan-boton aprobar"
              onClick={() => void decidir(plan, true)}
              disabled={decidiendo === plan.id}
            >
              {decidiendo === plan.id ? 'Un momento…' : 'Aprobar y ejecutar'}
            </button>
            <button
              type="button"
              className="plan-boton rechazar"
              onClick={() => void decidir(plan, false)}
              disabled={decidiendo === plan.id}
            >
              Rechazar
            </button>
          </div>

          {error && <p className="plan-error">{error}</p>}
        </article>
      ))}
    </div>
  );
}
