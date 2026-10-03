import { useState } from 'react';
import { morganAPI, MorganAPIError } from '../lib/api';
import type { EspacioItem } from '../lib/api';

/**
 * El selector de espacio de trabajo de la barra lateral (V2.2).
 *
 * Un espacio agrupa conversaciones, archivos y documentos, y tiene instrucciones
 * propias que Morgan tiene en cuenta en cada turno. «General» es lo que no está
 * en ningún espacio, y es donde queda todo lo que ya existía.
 *
 * Es un `<select>` nativo y no un desplegable hecho a mano: funciona con teclado y
 * con lector de pantalla sin escribir nada, y en el móvil abre el selector del
 * sistema, que es el que la gente ya sabe usar.
 */
export function SelectorDeEspacio({
  espacios,
  actual,
  maxInstrucciones,
  deshabilitado,
  onCambiar,
  onCambiado,
}: {
  espacios: EspacioItem[];
  /** El identificador del espacio seleccionado, o `null` para «General». */
  actual: string | null;
  maxInstrucciones: number;
  deshabilitado: boolean;
  onCambiar: (id: string | null) => void;
  /** Se llama cuando se ha creado, editado o borrado uno: hay que recargar la lista. */
  onCambiado: () => void;
}) {
  const [editando, setEditando] = useState<'nuevo' | 'actual' | null>(null);
  const seleccionado = espacios.find(e => e.id === actual) ?? null;

  return (
    <div className="espacio-selector">
      <div className="sidebar-section-label">
        Espacio de trabajo
        <span>
          {seleccionado && (
            <button
              className="section-action"
              onClick={() => setEditando(editando === 'actual' ? null : 'actual')}
              disabled={deshabilitado}
              aria-expanded={editando === 'actual'}
            >
              Editar
            </button>
          )}
          <button
            className="section-action"
            onClick={() => setEditando(editando === 'nuevo' ? null : 'nuevo')}
            disabled={deshabilitado}
            aria-expanded={editando === 'nuevo'}
          >
            Nuevo
          </button>
        </span>
      </div>

      <select
        className="espacio-select"
        value={actual ?? ''}
        onChange={e => { setEditando(null); onCambiar(e.target.value || null); }}
        disabled={deshabilitado}
        aria-label="Espacio de trabajo"
      >
        <option value="">General</option>
        {espacios.map(e => (
          <option key={e.id} value={e.id}>{e.nombre}</option>
        ))}
      </select>

      {editando && (
        <EditorDeEspacio
          key={editando === 'nuevo' ? 'nuevo' : seleccionado?.id}
          espacio={editando === 'actual' ? seleccionado : null}
          maxInstrucciones={maxInstrucciones}
          onCerrar={() => setEditando(null)}
          onGuardado={(guardado) => {
            setEditando(null);
            onCambiado();
            if (guardado) onCambiar(guardado.id);
          }}
          onBorrado={() => {
            setEditando(null);
            onCambiar(null);
            onCambiado();
          }}
        />
      )}
    </div>
  );
}

function EditorDeEspacio({
  espacio,
  maxInstrucciones,
  onCerrar,
  onGuardado,
  onBorrado,
}: {
  /** `null` para crear uno nuevo. */
  espacio: EspacioItem | null;
  maxInstrucciones: number;
  onCerrar: () => void;
  onGuardado: (espacio: EspacioItem | null) => void;
  onBorrado: () => void;
}) {
  const [nombre, setNombre] = useState(espacio?.nombre ?? '');
  const [instrucciones, setInstrucciones] = useState(espacio?.instrucciones ?? '');
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState('');

  const guardar = async () => {
    setGuardando(true);
    setError('');
    try {
      const guardado = espacio
        ? await morganAPI.actualizarEspacio(espacio.id, { nombre, instrucciones })
        : await morganAPI.crearEspacio(nombre, instrucciones);
      onGuardado(guardado);
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo guardar el espacio.');
    } finally {
      setGuardando(false);
    }
  };

  const borrar = async () => {
    if (!espacio) return;
    // Se dice lo que NO pasa, porque es lo que da miedo: no se pierde nada.
    if (!window.confirm(`¿Eliminar el espacio «${espacio.nombre}»?

Sus conversaciones, archivos y documentos no se borran: vuelven a General.`)) {
      return;
    }
    setGuardando(true);
    setError('');
    try {
      await morganAPI.eliminarEspacio(espacio.id);
      onBorrado();
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo eliminar el espacio.');
    } finally {
      // Si se borró, el editor ya se ha cerrado y esto no hace nada; si falló,
      // es lo que vuelve a dejar pulsar los botones.
      setGuardando(false);
    }
  };

  return (
    <form
      className="espacio-editor"
      onSubmit={e => { e.preventDefault(); void guardar(); }}
    >
      <label>
        Nombre
        <input
          value={nombre}
          onChange={e => setNombre(e.target.value)}
          maxLength={60}
          required
          autoFocus
        />
      </label>

      <label>
        Instrucciones para Morgan
        <textarea
          value={instrucciones}
          onChange={e => setInstrucciones(e.target.value)}
          maxLength={maxInstrucciones}
          rows={4}
          placeholder="Por ejemplo: es un proyecto en Python; responde con ejemplos de código."
        />
        {/* El contador explica el límite antes de chocar con él. Las instrucciones
            van en cada mensaje a Morgan, y cuanto más largas, más cuota gastan. */}
        <span className="espacio-editor__cuenta">
          {instrucciones.length} / {maxInstrucciones}
        </span>
      </label>

      {error && <p className="sidebar-note sidebar-note--error" role="alert">{error}</p>}

      <div className="espacio-editor__acciones">
        <button type="submit" className="section-action" disabled={guardando || !nombre.trim()}>
          {espacio ? 'Guardar' : 'Crear'}
        </button>
        <button type="button" className="section-action" onClick={onCerrar} disabled={guardando}>
          Cancelar
        </button>
        {espacio && (
          <button type="button" className="section-action danger" onClick={() => void borrar()} disabled={guardando}>
            Eliminar
          </button>
        )}
      </div>
    </form>
  );
}
