/* Morgan Web — Servicios externos conectados (V1.9) */

import { useCallback, useEffect, useState } from 'react';
import { MorganAPIError, morganAPI } from '../lib/api';
import type { ServicioIntegrable } from '../lib/api';

/**
 * Conectar y desconectar servicios externos.
 *
 * **Esto no es el login de Morgan.** Son dos sistemas distintos: uno dice quién
 * eres, el otro autoriza a Morgan a usar tu cuenta de otro sitio. La vista lo
 * dice en voz alta porque confundirlos lleva a esperar que «conectar GitHub»
 * sirva para entrar.
 *
 * **El botón de conectar solo aparece cuando puede funcionar.** Si al servidor
 * le faltan las credenciales de la aplicación OAuth, se explica qué falta y no
 * hay nada que pulsar: un botón que siempre da error hace perder el tiempo y
 * parece una avería de Morgan cuando es configuración que falta.
 */

/** El resultado que el backend deja en la URL al volver de autorizar. */
const AVISOS: Record<string, { texto: string; error: boolean }> = {
  conectado: { texto: 'Servicio conectado.', error: false },
  cancelado: { texto: 'No se completó la autorización.', error: false },
  fallo: { texto: 'No se pudo completar la conexión. Inténtalo de nuevo.', error: true },
  estado_invalido: {
    texto: 'La autorización caducó o no era válida. Vuelve a empezar.',
    error: true,
  },
  invalido: { texto: 'La respuesta del servicio no era válida.', error: true },
};

/**
 * Lee el resultado que el backend dejó en la URL al volver de autorizar.
 *
 * Solo LEE. Limpiar la barra de direcciones es otra cosa y va aparte
 * (limpiarLaUrl): mezclarlas obligaba a hacer el efecto secundario durante el
 * render, que es donde React no garantiza cuántas veces se ejecuta.
 */
function resultadoDeLaUrl(): { texto: string; error: boolean } | null {
  try {
    const params = new URLSearchParams(window.location.search);
    if (!params.get('integracion') || !params.get('resultado')) return null;

    return AVISOS[params.get('resultado') as string] ?? AVISOS.fallo;
  } catch {
    return null;
  }
}

/**
 * Quita los parámetros de la vuelta de la barra de direcciones.
 *
 * Si se quedaran, recargar la página volvería a enseñar «servicio conectado»
 * sobre algo que ocurrió hace media hora.
 */
function limpiarLaUrl(): void {
  try {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('integracion') && !params.has('resultado')) return;

    params.delete('integracion');
    params.delete('resultado');
    const resto = params.toString();
    window.history.replaceState({}, '', resto ? `/?${resto}` : '/');
  } catch {
    // Sin historial manipulable el aviso se repetirá al recargar. Molesto,
    // no roto.
  }
}

export function IntegracionesView({ apiOnline }: { apiOnline: boolean }) {
  const [servicios, setServicios] = useState<ServicioIntegrable[]>([]);
  const [cargando, setCargando] = useState(apiOnline);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  // Se lee en la inicialización, no en un efecto: el valor existe desde el
  // primer render y no hace falta un segundo para pintarlo.
  const [aviso, setAviso] = useState<{ texto: string; error: boolean } | null>(
    resultadoDeLaUrl,
  );

  const cargar = useCallback(() => {
    if (!apiOnline) return;
    morganAPI.integraciones()
      .then(r => { setServicios(r.servicios); setError(null); })
      .catch(err => setError(
        err instanceof MorganAPIError ? err.message : 'No se pudieron cargar los servicios.',
      ))
      .finally(() => setCargando(false));
  }, [apiOnline]);

  useEffect(() => { cargar(); }, [cargar]);

  // Lo único que queda para el efecto es tocar el historial del navegador, que
  // sí es un sistema externo. No hay setState aquí.
  useEffect(() => { limpiarLaUrl(); }, []);

  const conectar = useCallback(async (servicio: string) => {
    setOcupado(servicio);
    setError(null);
    try {
      const { url } = await morganAPI.conectarIntegracion(servicio);
      // La redirección la hace el cliente: si el backend redirigiera, la
      // seguiría el propio `fetch` y el navegador nunca vería la pantalla de
      // GitHub.
      window.location.href = url;
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo iniciar la conexión.');
      setOcupado(null);
    }
  }, []);

  const desconectar = useCallback(async (servicio: string, nombre: string) => {
    if (!window.confirm(
      `¿Desconectar ${nombre}?\n\nMorgan dejará de tener acceso. Puedes volver a conectarlo cuando quieras.`,
    )) return;

    setOcupado(servicio);
    setError(null);
    try {
      const res = await morganAPI.desconectarIntegracion(servicio);
      // Se avisa cuando el servicio remoto no confirmó la revocación: la fila
      // local ya no está, pero conviene revisarlo en su web.
      setAviso(res.revocado_en_origen
        ? { texto: `${nombre} desconectado.`, error: false }
        : {
            texto: `${nombre} desconectado de Morgan, pero no se pudo confirmar la `
                 + `revocación en su web. Revísalo en los ajustes de ${nombre}.`,
            error: true,
          });
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo desconectar.');
    } finally {
      setOcupado(null);
      cargar();
    }
  }, [cargar]);

  return (
    <>
      <div className="view-header">
        <h2>Servicios</h2>
        <span className="counter">{servicios.filter(s => s.conectado).length}</span>
        <button className="header-action" onClick={() => cargar()}>Actualizar</button>
      </div>

      <div className="view-body">
        <p className="ajustes-nota">
          Aquí autorizas a Morgan a usar tus cuentas de otros servicios.
          <strong> No es lo mismo que entrar en Morgan</strong>: tu sesión aquí no
          cambia, y desconectar un servicio no cierra tu cuenta.
        </p>

        {aviso && (
          <div
            className={aviso.error ? 'error-banner' : 'ajustes-aviso'}
            role="status"
            onClick={() => setAviso(null)}
          >
            {aviso.texto}
          </div>
        )}
        {error && <div className="error-banner">{error}</div>}

        {cargando ? (
          <p className="ajustes-nota">Cargando…</p>
        ) : (
          <div className="servicios">
            {servicios.map(s => (
              <article key={s.servicio} className={`servicio ${s.conectado ? 'conectado' : ''}`}>
                <div className="servicio-cabecera">
                  <h3>{s.nombre}</h3>
                  <span className={`servicio-estado ${estadoDe(s)}`}>
                    {etiquetaDe(s)}
                  </span>
                </div>

                <p className="servicio-descripcion">{s.descripcion}</p>

                {s.conectado && s.cuenta && (
                  <p className="servicio-cuenta">
                    Conectado como <strong>{s.cuenta}</strong>
                  </p>
                )}

                {s.conectado && s.error && (
                  <p className="servicio-fallo">
                    Última operación fallida: {s.error}
                  </p>
                )}

                {/* Qué habilita, en lenguaje llano y ANTES de conectar. Dar por
                    supuesto lo que concede un permiso es lo que hace que la
                    gente autorice sin saber qué. */}
                <ul className="servicio-permisos">
                  {(s.conectado ? s.scopes ?? [] : s.permite).map(item => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>

                {!s.disponible ? (
                  <p className="servicio-no-disponible">{s.motivo_no_disponible}</p>
                ) : s.conectado ? (
                  <button
                    className="ajustes-boton peligro"
                    onClick={() => void desconectar(s.servicio, s.nombre)}
                    disabled={ocupado === s.servicio}
                  >
                    {ocupado === s.servicio ? 'Desconectando…' : 'Desconectar'}
                  </button>
                ) : (
                  <button
                    className="ajustes-boton"
                    onClick={() => void conectar(s.servicio)}
                    disabled={ocupado === s.servicio || !apiOnline}
                  >
                    {ocupado === s.servicio ? 'Abriendo…' : `Conectar ${s.nombre}`}
                  </button>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function estadoDe(s: ServicioIntegrable): string {
  if (!s.disponible) return 'apagado';
  if (!s.conectado) return 'libre';
  return s.error ? 'fallando' : 'activo';
}

function etiquetaDe(s: ServicioIntegrable): string {
  if (!s.disponible) return 'No configurado';
  if (!s.conectado) return 'Sin conectar';
  // «Conectado y fallando» es muy distinto de «no conectado», y hay que poder
  // distinguirlos de un vistazo.
  return s.error ? 'Con problemas' : 'Conectado';
}
