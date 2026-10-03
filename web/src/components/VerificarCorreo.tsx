/* Morgan Web — Confirmar el correo (V1.9) */

import { useCallback, useEffect, useState } from 'react';
import { MorganAPIError, morganAPI } from '../lib/api';

/**
 * Confirmar la dirección de correo.
 *
 * **No es una puerta.** La cuenta funciona desde el primer momento sin
 * confirmar nada, y eso es una decisión: obligar a abrir el correo antes de
 * dejar probar Morgan es la forma más rápida de perder a alguien.
 *
 * Lo que se pierde sin confirmar es concreto y acotado, y por eso el aviso lo
 * dice tal cual en vez de amenazar en abstracto: **no se puede recuperar la
 * contraseña**, porque el enlace de recuperación va justo a esa dirección.
 */

// Este fichero exporta dos ayudantes ademas del componente. Son la misma pieza
// —confirmar el correo— partida en lo que lee el enlace, lo que lo consume y lo
// que avisa; separarlos por una regla de recarga en caliente dejaria tres
// ficheros que solo se entienden juntos.
// oxlint-disable react/only-export-components

/** Lee el token del enlace, si esta visita viene de un correo. */
export function tokenDeVerificacion(): string {
  try {
    return new URLSearchParams(window.location.search).get('verificar') ?? '';
  } catch {
    return '';
  }
}

function limpiarLaUrl(): void {
  try {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('verificar')) return;

    params.delete('verificar');
    const resto = params.toString();
    window.history.replaceState({}, '', resto ? `/?${resto}` : '/');
  } catch {
    // Sin historial manipulable el token se queda en la barra. Molesto, no
    // roto: ya se ha consumido y no vale para nada.
  }
}

/**
 * Consume el token del enlace, si lo hay.
 *
 * Se hace **antes** de saber quién eres y sin exigir sesión: quien abre el
 * enlace puede estar en otro navegador, o en el móvil. Pedirle que inicie
 * sesión primero convertiría un clic en un trámite.
 */
export function useVerificacionPorEnlace(alVerificar: () => void) {
  const [resultado, setResultado] = useState<{ texto: string; error: boolean } | null>(null);

  useEffect(() => {
    const token = tokenDeVerificacion();
    if (!token) return;

    let cancelado = false;
    limpiarLaUrl();

    morganAPI.verificarEmail(token)
      .then(() => {
        if (cancelado) return;
        setResultado({ texto: 'Correo confirmado. Gracias.', error: false });
        // Se vuelve a preguntar quién eres para que el aviso desaparezca sin
        // recargar: el dato que lo controla acaba de cambiar en el servidor.
        alVerificar();
      })
      .catch((err) => {
        if (cancelado) return;
        setResultado({
          texto: err instanceof MorganAPIError
            ? err.message
            : 'No se pudo confirmar el correo.',
          error: true,
        });
      });

    return () => { cancelado = true; };
  }, [alVerificar]);

  return { resultado, descartar: () => setResultado(null) };
}

/**
 * El aviso de que falta confirmar.
 *
 * Aparece dentro de la aplicación, no delante. Se puede seguir usando Morgan
 * con él en pantalla, que es justamente lo que separa un aviso de una puerta.
 */
export function AvisoSinVerificar({ correo }: { correo: string | null | undefined }) {
  const [estado, setEstado] = useState<'listo' | 'enviando' | 'enviado' | 'fallo'>('listo');
  const [mensaje, setMensaje] = useState('');
  const [oculto, setOculto] = useState(false);

  const reenviar = useCallback(async () => {
    setEstado('enviando');
    try {
      const res = await morganAPI.reenviarVerificacion();
      setMensaje(res.message);
      setEstado('enviado');
    } catch (err) {
      setMensaje(err instanceof MorganAPIError ? err.message : 'No se pudo enviar.');
      setEstado('fallo');
    }
  }, []);

  if (oculto) return null;

  return (
    <div className="aviso-verificar" role="status">
      <div className="aviso-verificar__texto">
        <strong>Falta confirmar tu correo{correo ? ` (${correo})` : ''}.</strong>{' '}
        Puedes seguir usando Morgan, pero <strong>no podrás recuperar tu
        contraseña</strong> si la olvidas hasta que abras el enlace que te
        enviamos.
      </div>

      <div className="aviso-verificar__acciones">
        {estado === 'enviado' || estado === 'fallo' ? (
          <span className={estado === 'fallo' ? 'aviso-verificar__fallo' : ''}>
            {mensaje}
          </span>
        ) : (
          <button type="button" onClick={() => void reenviar()} disabled={estado === 'enviando'}>
            {estado === 'enviando' ? 'Enviando…' : 'Reenviar el correo'}
          </button>
        )}
        {/* Se puede quitar de en medio. Un aviso que no se puede cerrar deja de
            leerse a los dos días y estorba para siempre. */}
        <button type="button" onClick={() => setOculto(true)} aria-label="Ocultar el aviso">
          Ahora no
        </button>
      </div>
    </div>
  );
}
