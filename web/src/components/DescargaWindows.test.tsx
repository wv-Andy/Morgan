/**
 * «Descargar Morgan para Windows» en el menú lateral (5.1): a la vista para quien entra
 * desde Windows, y en ningún otro sitio (en un móvil sería un botón inútil).
 */

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DescargaWindows, esWindows } from './DescargaWindows';
import { DESCARGA_WINDOWS } from './PanelEquipos';

const WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36';
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15';

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('esWindows', () => {
  it('por el agente de usuario', () => {
    expect(esWindows({ userAgent: WINDOWS })).toBe(true);
    expect(esWindows({ userAgent: ANDROID })).toBe(false);
    expect(esWindows({ userAgent: MAC })).toBe(false);
  });

  it('manda la plataforma, si el navegador la da', () => {
    expect(esWindows({ userAgent: WINDOWS, userAgentData: { platform: 'macOS' } })).toBe(false);
    expect(esWindows({ userAgent: MAC, userAgentData: { platform: 'Windows' } })).toBe(true);
  });
});

describe('DescargaWindows', () => {
  it('desde Windows, enlaza con la última versión del programa', () => {
    vi.stubGlobal('navigator', { userAgent: WINDOWS });
    render(<DescargaWindows />);
    const enlace = screen.getByRole('link', { name: /Descargar Morgan para Windows/ });
    expect(enlace.getAttribute('href')).toBe(DESCARGA_WINDOWS);
    expect(enlace.getAttribute('target')).toBe('_blank');
  });

  it('desde un móvil, no aparece', () => {
    vi.stubGlobal('navigator', { userAgent: ANDROID });
    render(<DescargaWindows />);
    expect(screen.queryByRole('link', { name: /Descargar Morgan para Windows/ })).toBeNull();
  });
});
