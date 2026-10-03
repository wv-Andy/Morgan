/* Morgan Web — La insignia de nivel de riesgo */

/**
 * El color aquí es información, no decoración: dice cuánto puede romper una
 * herramienta. Por eso vive en su propio fichero y lo usan tanto el catálogo de
 * herramientas como los planes: dos insignias distintas para la misma escala
 * serían dos escalas.
 */

export function RiskBadge({ level }: { level: string }) {
  const labels: Record<string, string> = {
    safe: 'SAFE', low_risk: 'LOW', moderate: 'MOD', high_risk: 'HIGH', critical: 'CRIT', sensitive: 'CRIT',
  };
  // Un nivel ausente o inesperado no debe tumbar el render de toda la lista.
  const safe = level || 'unknown';
  return <span className={`risk-badge ${safe}`}>{labels[safe] ?? safe.toUpperCase()}</span>;
}
