import { useCallback, useEffect, useState } from 'react';
import { morganAPI, MorganAPIError } from '../lib/api';
import type { UploadItem } from '../lib/api';

/**
 * Gestión de los archivos subidos (§14 del backlog).
 *
 * Antes se podía subir pero no había dónde ver qué había, cuánto ocupaba ni
 * borrarlo. La API ya estaba entera; solo faltaba esto.
 *
 * **No hay previsualización**, y es deliberado: renderizar contenido subido por
 * el usuario —HTML o SVG, sobre todo— es una superficie de ataque que no
 * compensa. Para leer un archivo ya está Morgan.
 *
 * **Descargar sí** (3.1-E): aquí aparecen también las copias que Morgan trae del PC
 * de la persona cuando se las pide, y el botón las guarda en el movil o en el
 * ordenador. La ruta las sirve siempre como adjunto, nunca como pagina.
 */

const FAMILIAS: Record<UploadItem['familia'], string> = {
  imagen: 'Imagen',
  documento: 'Documento',
  texto: 'Texto',
  audio: 'Audio',
  descarga: 'Copia de tu PC',
};

function tamano(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function caduca(creadoEn: number, horas: number): string {
  // `creado_en` viene en segundos desde epoch, como lo guarda el backend.
  const restanMs = (creadoEn + horas * 3600) * 1000 - Date.now();
  if (restanMs <= 0) return 'caducado';

  const restanHoras = Math.floor(restanMs / 3_600_000);
  if (restanHoras >= 1) return `caduca en ${restanHoras} h`;
  return `caduca en ${Math.max(1, Math.round(restanMs / 60_000))} min`;
}

export function ArchivosView({ apiOnline }: { apiOnline: boolean }) {
  const [archivos, setArchivos] = useState<UploadItem[]>([]);
  const [limites, setLimites] = useState({ max_mb: 0, ttl_hours: 24 });
  const [cargando, setCargando] = useState(apiOnline);
  const [error, setError] = useState<string | null>(null);

  const cargar = useCallback(() => {
    if (!apiOnline) return;
    morganAPI.uploads()
      .then(r => {
        setArchivos(r.uploads);
        setLimites({ max_mb: r.max_mb, ttl_hours: r.ttl_hours });
        setError(null);
      })
      .catch(err => setError(err instanceof MorganAPIError ? err.message : 'No se pudieron cargar los archivos.'))
      .finally(() => setCargando(false));
  }, [apiOnline]);

  useEffect(() => { cargar(); }, [cargar]);

  const borrar = useCallback(async (archivo: UploadItem) => {
    // Confirmación porque es irreversible: no hay papelera de archivos subidos.
    if (!window.confirm(`¿Eliminar «${archivo.nombre}»?\n\nEsta acción no se puede deshacer.`)) {
      return;
    }
    try {
      await morganAPI.deleteUpload(archivo.id);
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo eliminar el archivo.');
    } finally {
      cargar();
    }
  }, [cargar]);

  const ocupado = archivos.reduce((total, a) => total + a.tamano, 0);

  return (
    <>
      <div className="view-header">
        <h2>Archivos</h2>
        <span className="counter">{archivos.length}</span>
        <button className="header-action" onClick={() => cargar()}>Actualizar</button>
      </div>

      <div className="view-body">
        {!apiOnline && <div className="error-banner">Sin conexión con Morgan.</div>}
        {error && <div className="error-banner">{error}</div>}

        <p className="ajustes-nota">
          Lo que subes por el chat vive aquí. Se borra solo pasadas{' '}
          {limites.ttl_hours} horas, y ocupa {tamano(ocupado)} de los{' '}
          {limites.max_mb} MB por archivo permitidos.
        </p>

        {cargando ? (
          <p className="ajustes-nota">Cargando…</p>
        ) : archivos.length === 0 ? (
          <p className="ajustes-nota">
            No hay archivos subidos. Usa el clip del chat para adjuntar imágenes,
            documentos, código o audio.
          </p>
        ) : (
          <div className="archivo-lista">
            {archivos.map(a => (
              <div key={a.id} className="archivo">
                <div className="archivo-datos">
                  <span className="archivo-nombre" title={a.nombre}>{a.nombre}</span>
                  <span className="archivo-meta">
                    {FAMILIAS[a.familia] ?? a.familia} · {a.mime} · {tamano(a.tamano)} ·{' '}
                    {caduca(a.creado_en, limites.ttl_hours)}
                  </span>
                </div>
                <a
                  className="header-action"
                  href={`/api/uploads/${a.id}/contenido`}
                  download={a.nombre}
                  aria-label={`Descargar ${a.nombre}`}
                >
                  Descargar
                </a>
                <button
                  className="header-action danger"
                  onClick={() => borrar(a)}
                  aria-label={`Eliminar ${a.nombre}`}
                >
                  Eliminar
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
