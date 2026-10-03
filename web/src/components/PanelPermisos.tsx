/* Morgan Web — Ajustes → Permisos: el permiso automático (4.6) */

import { useCallback, useEffect, useState } from 'react';

import { MorganAPIError, morganAPI } from '../lib/api';

/**
 * El permiso automático, pedido por mí (2026-09-30): «darme permiso automático de
 * ejecución al hacer la mayoría de acciones en verde y amarillo, pero en las rojas
 * preguntar».
 *
 * Encendido, un plan con solo pasos verdes o amarillos se aprueba y se ejecuta al
 * proponerlo; uno con algo rojo espera al botón, como siempre. Lo que el PC confirma con
 * «Permitir» lo sigue confirmando: eso es de su política y la web no lo toca.
 */
export function PanelPermisos({ apiOnline }: { apiOnline: boolean }) {
  const [encendido, setEncendido] = useState<boolean | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!apiOnline) return;
    let cancelado = false;
    morganAPI.permisoAutomatico()
      .then(r => { if (!cancelado) setEncendido(r.encendido); })
      .catch(err => {
        if (!cancelado) setError(err instanceof MorganAPIError ? err.message : 'No se pudo leer el permiso.');
      });
    return () => { cancelado = true; };
  }, [apiOnline]);

  const alternar = useCallback(async () => {
    if (encendido === null) return;
    setGuardando(true);
    setError(null);
    try {
      const r = await morganAPI.cambiarPermisoAutomatico(!encendido);
      setEncendido(r.encendido);
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo guardar el permiso.');
    } finally {
      setGuardando(false);
    }
  }, [encendido]);

  return (
    <>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="ajustes-fila">
        <div className="ajustes-fila__texto">
          <span className="ajustes-fila__titulo" id="permiso-automatico">Permiso automático</span>
          <span className="ajustes-fila__ayuda">
            Lo <strong className="permiso-color permiso-color--verde">verde</strong> y
            lo <strong className="permiso-color permiso-color--amarillo">amarillo</strong> se
            hace sin esperar a que lo apruebes: crear, editar, copiar, comprimir o mover archivos en las carpetas
            que permitiste. Lo <strong className="permiso-color permiso-color--rojo">rojo</strong> te
            lo sigue preguntando: borrar, comandos que cambian algo, cerrar programas, escribir en
            GitHub.
          </span>
        </div>
        <div className="ajustes-fila__control">
          <button
            type="button"
            role="switch"
            aria-checked={encendido === true}
            aria-labelledby="permiso-automatico"
            className="interruptor"
            disabled={!apiOnline || encendido === null || guardando}
            onClick={() => void alternar()}
          >
            <span className="interruptor__bola" />
          </button>
        </div>
      </div>
      <div className="ajustes-fila">
        <div className="ajustes-fila__texto">
          <span className="ajustes-fila__titulo">Tu PC sigue mandando</span>
          <span className="ajustes-fila__ayuda">
            Lo que tu PC confirma con «Permitir» lo sigue confirmando: eso se decide en el PC, no
            aquí. Y este permiso no vale para los tokens de API: lo que llegue por ellos sigue
            esperando tu aprobación.
          </span>
        </div>
      </div>
    </>
  );
}
