/**
 * Automatizaciones (4.14): la bandeja y las programadas.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { morganAPI, zonaHeader } from '../lib/api';
import type { Automatizacion, Aviso } from '../lib/api';
import { AutomatizacionesView } from './Automatizaciones';

const AVISO: Aviso = {
  id: 'av1', automatizacion_id: 'a1', titulo: 'Noticias de IA', texto: 'Hoy **tres** novedades.',
  estado: 'hecha', herramientas: ['search_web'], leido: false, creado_en: 1_790_000_000,
};
const AUTO: Automatizacion = {
  id: 'a1', nombre: 'Noticias de IA', instruccion: 'Resume las noticias de IA', cuando: 'cada día a las 09:00',
  zona: 'Europe/Madrid', necesita_pc: false, activa: true, proxima: 1_790_100_000,
  proxima_texto: 'jueves 01/10 a las 09:00', esperando_pc: false, ultima: null, ultimo_estado: null,
  fallos_seguidos: 0,
  pasos: null,
};

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { document.body.innerHTML = ''; });

function montar(avisos: Aviso[] = [AVISO], autos: Automatizacion[] = [AUTO]) {
  vi.spyOn(morganAPI, 'avisos').mockResolvedValue({ avisos, sin_leer: avisos.filter(a => !a.leido).length });
  vi.spyOn(morganAPI, 'automatizaciones').mockResolvedValue({ automatizaciones: autos, max_activas: 10 });
  return vi.spyOn(morganAPI, 'marcarAvisosLeidos').mockResolvedValue({ sin_leer: 0 });
}

describe('La bandeja', () => {
  it('enseña lo que contó cada ejecución y verla es leerla', async () => {
    const leidos = montar();
    const onLeidos = vi.fn();
    await act(async () => { render(<AutomatizacionesView apiOnline onLeidos={onLeidos} />); });
    expect(screen.getByText('Hecha')).toBeTruthy();
    expect(screen.getByText('tres').tagName).toBe('STRONG');
    expect(screen.getByText('Usó: search_web')).toBeTruthy();
    expect(leidos).toHaveBeenCalledWith();
    expect(onLeidos).toHaveBeenCalled();
  });

  it('sin nada sin leer no marca nada', async () => {
    const leidos = montar([{ ...AVISO, leido: true }]);
    await act(async () => { render(<AutomatizacionesView apiOnline />); });
    expect(leidos).not.toHaveBeenCalled();
  });
});

describe('Las programadas', () => {
  it('dice cuándo toca y se pausa', async () => {
    montar();
    const pausar = vi.spyOn(morganAPI, 'pausarAutomatizacion').mockResolvedValue({ ...AUTO, activa: false });
    await act(async () => { render(<AutomatizacionesView apiOnline />); });
    expect(screen.getByText(/cada día a las 09:00 · próxima: jueves 01\/10 a las 09:00/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Pausar' })); });
    expect(pausar).toHaveBeenCalledWith('a1');
  });

  it('borrar pregunta antes, y con un no no borra', async () => {
    montar();
    const borrar = vi.spyOn(morganAPI, 'borrarAutomatizacion').mockResolvedValue({ borrada: 'a1' });
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    await act(async () => { render(<AutomatizacionesView apiOnline />); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Borrar' })); });
    expect(borrar).not.toHaveBeenCalled();
  });

  it('sin ninguna, dice cómo se crean (en el chat, no aquí)', async () => {
    montar([], []);
    await act(async () => { render(<AutomatizacionesView apiOnline />); });
    expect(screen.getByText(/Pídesela a Morgan en el chat/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /crear/i })).toBeNull();
  });

  it('una de pasos fijos enseña exactamente lo que hace', async () => {
    montar([], [{ ...AUTO, pasos: [{ herramienta: 'compress', descripcion: 'Comprimir Proyectos',
      argumentos: { accion: 'comprimir', dst: 'C:/Copias/proyectos-{fecha}.zip' } }] }]);
    await act(async () => { render(<AutomatizacionesView apiOnline />); });
    const lista = screen.getByRole('list', { name: 'Hace exactamente' });
    expect(lista.textContent).toContain('Comprimir Proyectos');
    expect(lista.textContent).toContain('dst: C:/Copias/proyectos-{fecha}.zip');
  });

  it('esperando al PC lo dice', async () => {
    montar([], [{ ...AUTO, necesita_pc: true, esperando_pc: true }]);
    await act(async () => { render(<AutomatizacionesView apiOnline />); });
    expect(screen.getByText('Esperando a tu PC')).toBeTruthy();
  });
});

it('cada petición lleva la zona horaria del navegador', () => {
  expect(zonaHeader()['X-Morgan-Zona']).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
});
