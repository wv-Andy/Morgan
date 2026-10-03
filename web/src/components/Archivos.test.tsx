/**
 * Tus archivos: descargar una copia traída del PC (3.1-E).
 *
 * Lo que se fija: cada archivo tiene su enlace de descarga a la ruta que lo sirve como
 * adjunto, y una copia del PC que Morgan no sabe leer se enseña como lo que es, no como
 * una familia rara.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { morganAPI, type UploadItem } from '../lib/api';
import { ArchivosView } from './Archivos';

const AHORA = Date.now() / 1000;

function archivo(parcial: Partial<UploadItem> = {}): UploadItem {
  return {
    id: 'up-1',
    nombre: 'contrato.pdf',
    mime: 'application/pdf',
    familia: 'documento',
    tamano: 1024 * 1024,
    creado_en: AHORA,
    ...parcial,
  } as UploadItem;
}

afterEach(() => vi.restoreAllMocks());

describe('Tus archivos', () => {
  it('cada archivo se puede descargar', async () => {
    vi.spyOn(morganAPI, 'uploads').mockResolvedValue({
      success: true, count: 1, uploads: [archivo()], max_mb: 20, ttl_hours: 24,
    } as Awaited<ReturnType<typeof morganAPI.uploads>>);

    render(<ArchivosView apiOnline />);

    const enlace = await screen.findByLabelText('Descargar contrato.pdf');
    expect(enlace.getAttribute('href')).toBe('/api/uploads/up-1/contenido');
    expect(enlace.getAttribute('download')).toBe('contrato.pdf');
  });

  it('una copia del PC se enseña como tal', async () => {
    vi.spyOn(morganAPI, 'uploads').mockResolvedValue({
      success: true, count: 1, ttl_hours: 24, max_mb: 20,
      uploads: [archivo({ id: 'up-2', nombre: 'informe.docx', familia: 'descarga', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })],
    } as Awaited<ReturnType<typeof morganAPI.uploads>>);

    render(<ArchivosView apiOnline />);

    await waitFor(() => expect(screen.getByText('informe.docx')).toBeTruthy());
    expect(screen.getByText(/Copia de tu PC/)).toBeTruthy();
    expect(screen.getByLabelText('Descargar informe.docx').getAttribute('href'))
      .toBe('/api/uploads/up-2/contenido');
  });
});
