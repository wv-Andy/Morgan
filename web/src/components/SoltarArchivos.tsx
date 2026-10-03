/* Morgan Web — Arrastrar y soltar archivos en la conversación */

import { useCallback, useRef, useState } from 'react';
import { MorganAPIError, morganAPI } from '../lib/api';
import type { UploadItem } from '../lib/api';

/**
 * Soltar archivos sobre la conversación para adjuntarlos.
 *
 * Sube por la **misma ruta** que el clip del compositor —`morganAPI.uploadFile`—
 * y no por un camino paralelo. Eso importa más de lo que parece: los límites de
 * tamaño, los tipos admitidos y el cupo diario viven en el backend, así que un
 * segundo camino de subida no los saltaría, pero sí podría informar distinto del
 * mismo rechazo.
 *
 * **Varios archivos a la vez, pero de uno en uno.** Subirlos en paralelo agota
 * el cupo más rápido cuando alguno va a ser rechazado, y deja los mensajes de
 * error mezclados sin saber cuál es de cuál.
 *
 * El contador de `dragenter`/`dragleave` no es una manía: esos eventos se
 * disparan también al pasar por CADA hijo del contenedor, así que sin contarlos
 * el aviso parpadea mientras mueves el ratón por encima.
 */

interface Resultado {
  /** Se pega al contenedor que acepta los archivos. */
  props: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
  };
  /** Hay algo encima esperando a soltarse. */
  encima: boolean;
  subiendo: boolean;
  error: string | null;
  limpiarError: () => void;
}

// El hook y su aviso son la misma funcion partida en dos: separarlos por una
// regla de recarga en caliente dejaria dos ficheros que solo se entienden juntos.
// oxlint-disable-next-line react/only-export-components
export function useSoltarArchivos(
  onAdjuntado: (archivo: UploadItem) => void,
  desactivado = false,
): Resultado {
  const [encima, setEncima] = useState(false);
  const [subiendo, setSubiendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const profundidad = useRef(0);

  const traeArchivos = (e: React.DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files');

  const onDragEnter = useCallback((e: React.DragEvent) => {
    if (desactivado || !traeArchivos(e)) return;
    e.preventDefault();
    profundidad.current += 1;
    setEncima(true);
  }, [desactivado]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    if (desactivado || !traeArchivos(e)) return;
    // Sin esto el navegador abre el archivo en la pestaña, que es su
    // comportamiento por defecto y el más desconcertante posible aquí.
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }, [desactivado]);

  const onDragLeave = useCallback((e: React.DragEvent) => {
    if (desactivado || !traeArchivos(e)) return;
    e.preventDefault();
    profundidad.current = Math.max(0, profundidad.current - 1);
    if (profundidad.current === 0) setEncima(false);
  }, [desactivado]);

  const onDrop = useCallback(async (e: React.DragEvent) => {
    if (desactivado || !traeArchivos(e)) return;
    e.preventDefault();
    profundidad.current = 0;
    setEncima(false);

    const archivos = Array.from(e.dataTransfer.files);
    if (archivos.length === 0) return;

    setSubiendo(true);
    setError(null);
    try {
      for (const archivo of archivos) {
        try {
          onAdjuntado(await morganAPI.uploadFile(archivo));
        } catch (err) {
          // Se nombra el archivo: con varios a la vez, «no se pudo subir» a
          // secas no dice cuál de ellos.
          setError(
            `${archivo.name}: ${err instanceof MorganAPIError ? err.message : 'no se pudo subir'}`,
          );
          break;
        }
      }
    } finally {
      setSubiendo(false);
    }
  }, [desactivado, onAdjuntado]);

  return {
    props: { onDragEnter, onDragOver, onDragLeave, onDrop },
    encima,
    subiendo,
    error,
    limpiarError: () => setError(null),
  };
}

/** El aviso que se ve mientras hay algo encima. */
export function AvisoSoltar({ subiendo }: { subiendo: boolean }) {
  return (
    <div className="soltar-aviso" role="status">
      <div className="soltar-aviso__caja">
        {subiendo ? 'Subiendo…' : 'Suelta para adjuntar'}
      </div>
    </div>
  );
}
