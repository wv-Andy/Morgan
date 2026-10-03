/**
 * Ajustes → Permisos: el permiso automático (4.6, pedido por mí).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { morganAPI } from '../lib/api';
import { PanelPermisos } from './PanelPermisos';

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { document.body.innerHTML = ''; });

describe('El permiso automático', () => {
  it('enseña cómo está y lo cambia', async () => {
    vi.spyOn(morganAPI, 'permisoAutomatico').mockResolvedValue({ success: true, encendido: false });
    const cambiar = vi.spyOn(morganAPI, 'cambiarPermisoAutomatico').mockResolvedValue({ success: true, encendido: true });
    await act(async () => { render(<PanelPermisos apiOnline />); });

    const interruptor = screen.getByRole('switch', { name: 'Permiso automático' });
    expect(interruptor.getAttribute('aria-checked')).toBe('false');
    await act(async () => { fireEvent.click(interruptor); });
    expect(cambiar).toHaveBeenCalledWith(true);
    expect(interruptor.getAttribute('aria-checked')).toBe('true');
  });

  it('dice qué sigue preguntando', async () => {
    vi.spyOn(morganAPI, 'permisoAutomatico').mockResolvedValue({ success: true, encendido: true });
    await act(async () => { render(<PanelPermisos apiOnline />); });
    expect(screen.getByText(/borrar, comandos que cambian algo/)).toBeTruthy();
    expect(screen.getByText(/Tu PC sigue mandando/)).toBeTruthy();
  });

  it('sin conexión no se puede tocar', async () => {
    const leer = vi.spyOn(morganAPI, 'permisoAutomatico');
    await act(async () => { render(<PanelPermisos apiOnline={false} />); });
    expect(leer).not.toHaveBeenCalled();
    expect((screen.getByRole('switch') as HTMLButtonElement).disabled).toBe(true);
  });

  it('si falla al guardar, lo dice y no cambia', async () => {
    const { MorganAPIError } = await import('../lib/api');
    vi.spyOn(morganAPI, 'permisoAutomatico').mockResolvedValue({ success: true, encendido: false });
    vi.spyOn(morganAPI, 'cambiarPermisoAutomatico').mockRejectedValue(new MorganAPIError('X', 'No se pudo.'));
    await act(async () => { render(<PanelPermisos apiOnline />); });
    await act(async () => { fireEvent.click(screen.getByRole('switch')); });
    expect(screen.getByRole('alert').textContent).toBe('No se pudo.');
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false');
  });
});
