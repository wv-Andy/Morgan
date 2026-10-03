/* Morgan Web — Cómo se enseñan fechas, horas e identificadores */

/**
 * Sale de `App.tsx` al partirlo. Son tres funciones de tres líneas, pero las
 * usaban cuatro vistas distintas: dejarlas dentro habría obligado a exportarlas
 * desde el fichero principal, que es justo lo que convierte un fichero grande
 * en un fichero del que todo depende.
 */

export function formatTime(d: Date): string {
  return d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
}

export function formatDate(s: string): string {
  try { return new Date(s).toLocaleString('es-MX', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch { return s; }
}

export function uid(): string {
  return Math.random().toString(36).slice(2);
}
