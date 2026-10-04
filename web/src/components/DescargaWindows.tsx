/* Morgan Web — «Descargar Morgan para Windows», a la vista (5.1)
 *
 * Antes el programa solo se ofrecía dentro de Ajustes → Tu equipo: quien no sabía que
 * existía no lo encontraba (lo pedí el 2026-10-04). Va en el pie del menú lateral,
 * siempre visible, y **solo desde Windows**: en un móvil o un Mac sería un botón inútil.
 */

import { DESCARGA_WINDOWS } from './PanelEquipos';
import { IconDescargar } from './Icons';

/** Si este navegador corre en Windows. Exportada para las pruebas. */
export function esWindows(nav: Pick<Navigator, 'userAgent'> & { userAgentData?: { platform?: string } } = navigator): boolean {
  const plataforma = nav.userAgentData?.platform;
  if (plataforma) return plataforma === 'Windows';
  return /Windows/i.test(nav.userAgent);
}

export function DescargaWindows() {
  if (!esWindows()) return null;
  return (
    <a
      className="descarga-windows"
      href={DESCARGA_WINDOWS}
      target="_blank"
      rel="noreferrer"
      title="El programa que conecta este PC con Morgan: un instalador de doble clic"
    >
      <IconDescargar />
      Descargar Morgan para Windows
    </a>
  );
}
