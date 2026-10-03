import { useCallback, useRef, useState } from 'react';
import { morganAPI, MorganAPIError } from '../lib/api';
import type { UploadItem } from '../lib/api';
import { IconClip, IconMic } from './Icons';

/**
 * Adjuntar archivos y grabar audio desde el compositor (V1.4).
 *
 * Lo que se sube **no se procesa aquí**: el archivo queda guardado y Morgan lo usa
 * cuando se lo pides. El adjunto queda como estado pendiente del compositor y
 * viaja en su propio campo de la petición, no incrustado en el texto: antes se
 * pegaba el identificador en el mensaje y bastaba con que el usuario borrara esa
 * línea para que Morgan perdiera el vínculo.
 *
 * La grabación usa MediaRecorder, que existe en todos los navegadores actuales
 * pero **exige HTTPS o localhost**. En Vercel y con el túnel se cumple; en una IP
 * local por HTTP, no, y por eso el fallo se explica en lugar de quedar mudo.
 */

/**
 * Estados de un adjunto, de los que pide el backlog (§12 y §18).
 *
 * `transcribiendo` no lo sabe esta capa con certeza: la transcripcion la hace
 * Morgan al procesar el turno. Se muestra igualmente porque lo alternativo es
 * dejar al usuario sin ninguna senal justo en la espera mas larga.
 */
type Estado = 'inactivo' | 'grabando' | 'subiendo' | 'transcribiendo' | 'listo' | 'fallido';

const ETIQUETAS: Record<Estado, string> = {
  inactivo: '',
  grabando: 'Grabando… pulsa para parar',
  subiendo: 'Subiendo…',
  transcribiendo: 'Transcribiendo…',
  listo: 'Listo',
  fallido: 'Falló',
};

export function Adjuntos({
  disabled, onAdjuntado, onTranscrito,
}: {
  disabled: boolean;
  onAdjuntado: (archivo: UploadItem) => void;
  /**
   * Lo que se entendió en la grabación, para revisarlo.
   *
   * Sin esto, el audio se adjuntaba y Morgan lo transcribía dentro del turno:
   * lo primero que veías de tu propia voz era la respuesta a algo que no habías
   * podido leer. Ahora el texto entra en el compositor y se puede corregir.
   */
  onTranscrito?: (texto: string) => void;
}) {
  const entrada = useRef<HTMLInputElement>(null);
  const grabadora = useRef<MediaRecorder | null>(null);
  const trozos = useRef<Blob[]>([]);

  // El usuario debe saber en que punto esta. Antes solo habia «Subiendo…», y
  // entre que el archivo llegaba y Morgan respondia no habia ninguna senal.
  const [estado, setEstado] = useState<Estado>('inactivo');
  const [error, setError] = useState<string | null>(null);

  const grabando = estado === 'grabando';
  const ocupado = estado === 'subiendo' || estado === 'transcribiendo';

  const subir = useCallback(async (archivo: File, esAudio = false) => {
    setEstado('subiendo');
    setError(null);

    let subido: UploadItem;
    try {
      subido = await morganAPI.uploadFile(archivo);
    } catch (err) {
      setEstado('fallido');
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo subir el archivo.');
      return;
    }

    // Un audio grabado aquí se transcribe y el texto va al compositor, para
    // poder leerlo y corregirlo. Solo se adjunta como archivo cuando no hay a
    // quién entregar el texto —o cuando la transcripción falla—, porque
    // entonces Morgan puede intentarlo dentro del turno y no se pierde nada.
    if (esAudio && onTranscrito) {
      setEstado('transcribiendo');
      try {
        const { texto } = await morganAPI.transcribir(subido.id);
        onTranscrito(texto);
        setEstado('listo');
        setTimeout(() => setEstado('inactivo'), 2000);
        return;
      } catch (err) {
        setError(
          err instanceof MorganAPIError
            ? `${err.message} Se adjunta el audio para que Morgan lo intente.`
            : 'No se pudo transcribir. Se adjunta el audio.',
        );
        onAdjuntado(subido);
        setEstado('fallido');
        setTimeout(() => { setEstado('inactivo'); setError(null); }, 6000);
        return;
      }
    }

    onAdjuntado(subido);
    setEstado('listo');
    // El estado «listo» es informativo: se limpia solo para no dejar un cartel
    // permanente en el compositor.
    setTimeout(() => setEstado('inactivo'), 2000);
  }, [onAdjuntado, onTranscrito]);

  const elegir = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const archivo = e.target.files?.[0];
    // Se limpia el valor para que elegir dos veces el mismo archivo dispare el
    // evento la segunda vez: si no, el navegador lo considera "sin cambios".
    e.target.value = '';
    if (archivo) subir(archivo);
  }, [subir]);

  const alternarGrabacion = useCallback(async () => {
    if (grabando) {
      grabadora.current?.stop();
      return;
    }

    setError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setEstado('fallido');
      setError('Este navegador no permite grabar, o la página no se sirve por HTTPS.');
      return;
    }

    try {
      const flujo = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(flujo);
      trozos.current = [];

      rec.ondataavailable = e => { if (e.data.size > 0) trozos.current.push(e.data); };
      rec.onstop = () => {
        // Se sueltan las pistas o el navegador deja el indicador de micrófono
        // encendido, que además es inquietante.
        flujo.getTracks().forEach(t => t.stop());

        const audio = new Blob(trozos.current, { type: rec.mimeType || 'audio/webm' });
        if (audio.size > 0) {
          subir(new File([audio], `grabacion-${Date.now()}.webm`, { type: audio.type }), true);
        }
      };

      rec.start();
      grabadora.current = rec;
      setEstado('grabando');
    } catch {
      setEstado('fallido');
      setError('No se pudo acceder al micrófono. Revisa los permisos del navegador.');
    }
  }, [grabando, subir]);

  return (
    <>
      <input
        ref={entrada}
        type="file"
        hidden
        onChange={elegir}
        // La lista de verdad la valida el backend por el contenido; esto solo
        // ayuda al selector del sistema a filtrar.
        accept="image/*,audio/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.toml,.ini,.log,.py,.js,.ts,.tsx,.jsx,.html,.css,.c,.cpp,.h,.java,.cs,.sql,.php,.go,.rs,.kt,.swift,.rb,.sh"
      />

      <button
        className="composer-btn composer-btn--chip"
        onClick={() => entrada.current?.click()}
        disabled={disabled || ocupado}
        title="Adjuntar archivo o imagen"
        aria-label="Adjuntar archivo o imagen"
      >
        <IconClip size={15} />
        <span className="composer-btn__texto" aria-hidden="true">Adjuntar</span>
      </button>

      <button
        className={`composer-btn composer-btn--mic ${grabando ? 'grabando' : ''}`}
        onClick={alternarGrabacion}
        disabled={disabled || ocupado}
        title={grabando ? 'Detener y transcribir' : 'Grabar audio'}
        aria-label={grabando ? 'Detener grabación' : 'Grabar audio'}
      >
        <IconMic size={16} />
      </button>

      {(estado !== 'inactivo' || error) && (
        <span className={`adjunto-estado ${error ? 'error' : estado}`} role="status">
          {error ?? ETIQUETAS[estado]}
        </span>
      )}
    </>
  );
}
