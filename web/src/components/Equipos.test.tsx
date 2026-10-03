/**
 * «Tu equipo» en Ajustes: emparejar el agente local (3.0-C).
 *
 * Lo que se fija: el código se pide solo con cuenta y se enseña con su cuenta atrás;
 * uno caducado no se enseña como si valiera; revocar un equipo pide confirmación; y
 * en el Morgan de tu equipo no se ofrece nada que no funcionaría.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MorganAPIError, morganAPI, type AgenteLocal } from '../lib/api';
import type { EstadoCuenta } from './Cuenta';
import { PanelEquipos } from './PanelEquipos';

const CON_CUENTA: EstadoCuenta = {
  cargando: false,
  autenticado: true,
  local: false,
  usuario: { id: 'usr-ana', display_name: 'Ana', email: 'ana@ejemplo.co' },
  refrescar: async () => {},
  salir: async () => {},
};
const LOCAL: EstadoCuenta = { ...CON_CUENTA, autenticado: false, local: true, usuario: null };

const AHORA = Date.now() / 1000;

function agente(parcial: Partial<AgenteLocal> = {}): AgenteLocal {
  return {
    id: 'agt-1', nombre: 'Portátil', sistema: 'Windows 11', agent_version: '3.0.0-dev',
    protocol_version: 1, creado_en: AHORA - 3600, last_seen: AHORA - 60, ...parcial,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('Tu equipo', () => {
  it('sin cuenta lo explica y no pide nada', () => {
    const lista = vi.spyOn(morganAPI, 'agentes');

    render(<PanelEquipos cuenta={LOCAL} />);

    expect(screen.getByText(/no hay nada que emparejar/)).toBeTruthy();
    expect(screen.queryByText('Emparejar un equipo')).toBeNull();
    expect(lista).not.toHaveBeenCalled();
  });

  it('pide un código y lo enseña con su cuenta atrás y la advertencia', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [] });
    const pedir = vi.spyOn(morganAPI, 'codigoAgente').mockResolvedValue({
      success: true, codigo: 'K7QF-2M9D', caduca_en: Date.now() / 1000 + 600,
    });

    render(<PanelEquipos cuenta={CON_CUENTA} />);
    await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });

    expect(pedir).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Código de emparejamiento').textContent).toBe('K7QF-2M9D');
    expect(screen.getByText(/10:00|9:5\d/)).toBeTruthy();
    expect(screen.getByText(/si la cuenta que ves no es la tuya, di que no/)).toBeTruthy();
  });

  it('un código caducado no se enseña como si valiera', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [] });
    vi.spyOn(morganAPI, 'codigoAgente').mockResolvedValue({
      success: true, codigo: 'K7QF-2M9D', caduca_en: Date.now() / 1000 - 1,
    });

    render(<PanelEquipos cuenta={CON_CUENTA} />);
    await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });

    expect(screen.queryByLabelText('Código de emparejamiento')).toBeNull();
    expect(screen.getByText('El código caducó sin usarse.')).toBeTruthy();
    expect(screen.getByText('Pedir otro código')).toBeTruthy();
  });

  it('lista los equipos y revocar pide confirmación', async () => {
    vi.spyOn(morganAPI, 'agentes')
      .mockResolvedValueOnce({ success: true, agentes: [agente()] })
      .mockResolvedValue({ success: true, agentes: [] });
    const revocar = vi.spyOn(morganAPI, 'revocarAgente').mockResolvedValue({ success: true });

    render(<PanelEquipos cuenta={CON_CUENTA} />);
    expect(await screen.findByText('Portátil')).toBeTruthy();
    expect(screen.getByText(/Windows 11 · agente 3.0.0-dev/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Revocar Portátil'));
    expect(revocar).not.toHaveBeenCalled();

    await act(async () => { fireEvent.click(screen.getByText('Sí, revocar')); });
    expect(revocar).toHaveBeenCalledWith('agt-1');
    expect(await screen.findByText('No tienes ninguno emparejado.')).toBeTruthy();
  });

  it('enseña el motivo del servidor si no se puede pedir el código', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [] });
    vi.spyOn(morganAPI, 'codigoAgente').mockRejectedValue(
      new MorganAPIError('SIN_CUENTA', 'Emparejar un equipo necesita una cuenta de Morgan.'),
    );

    render(<PanelEquipos cuenta={CON_CUENTA} />);
    await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });

    expect(screen.getByText(/necesita una cuenta/)).toBeTruthy();
  });

  it('dice si cada equipo está conectado ahora y qué ofrece (3.7)', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({
      success: true,
      agentes: [
        agente({ conectado: true, capacidades: ['read_file', 'list_files', 'create_file', 'run_command'] }),
        agente({ id: 'agt-2', nombre: 'Sobremesa', conectado: false, capacidades: [] }),
      ],
    });
    render(<PanelEquipos cuenta={CON_CUENTA} />);
    expect(await screen.findByText(/Conectado ahora · leer, escribir, terminal/)).toBeTruthy();
    expect(screen.getByText('○ No conectado')).toBeTruthy();
  });

  it('el historial se carga al abrirlo, sin argumentos ni contenido (3.7)', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [agente({ conectado: true })] });
    const historial = vi.spyOn(morganAPI, 'ordenesAgente').mockResolvedValue({
      success: true,
      ordenes: [{ command_id: 'c1', capability: 'read_file', estado: 'completed', ms: 184.2, creado_en: AHORA - 30 }],
    });
    render(<PanelEquipos cuenta={CON_CUENTA} />);
    const boton = await screen.findByLabelText('Historial de Portátil');
    expect(historial).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(boton); });
    expect(historial).toHaveBeenCalledWith('agt-1');
    expect(await screen.findByText(/read_file · completed · 184 ms/)).toBeTruthy();
  });

  it('con el código da la línea para instalarlo en un PC nuevo (3.8)', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [] });
    const linea = 'irm https://nube/agente/instalar.ps1 -OutFile $env:TEMP\\instalar-morgan.ps1; powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\\instalar-morgan.ps1 -Codigo K7QF-2M9D -Nube https://nube';
    vi.spyOn(morganAPI, 'codigoAgente').mockResolvedValue({
      success: true, codigo: 'K7QF-2M9D', caduca_en: Date.now() / 1000 + 600, instalar: linea,
    });
    render(<PanelEquipos cuenta={CON_CUENTA} />);
    await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });
    expect(screen.getByText(linea)).toBeTruthy();
    expect(screen.getByText(
      '& "$env:LOCALAPPDATA\\Morgan\\agente\\morgan-agente.cmd" emparejar --codigo K7QF-2M9D')).toBeTruthy();
  });

  it('dice si un equipo tiene una versión vieja y cuándo cambió su credencial (3.8)', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({
      success: true,
      ultima_version: '3.8.5',
      agentes: [
        agente({ agent_version: '3.8.0', credencial_rotada_en: AHORA - 86400 }),
        agente({ id: 'agt-2', nombre: 'Sobremesa', agent_version: '3.8.5' }),
      ],
    });
    render(<PanelEquipos cuenta={CON_CUENTA} />);
    expect(await screen.findAllByText(/Hay una versión nueva: 3.8.5/)).toHaveLength(1);
    expect(screen.getAllByText(/credencial cambiada el/)).toHaveLength(1);
  });

  it('la línea se copia con un botón (4.17)', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [] });
    vi.spyOn(morganAPI, 'codigoAgente').mockResolvedValue({
      success: true, codigo: 'K7QF-2M9D', caduca_en: Date.now() / 1000 + 600, instalar: 'irm linea',
    });
    const escribir = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: escribir } });
    render(<PanelEquipos cuenta={CON_CUENTA} />);
    await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });
    await act(async () => { fireEvent.click(screen.getByLabelText('Copiar la línea de instalación')); });
    expect(escribir).toHaveBeenCalledWith('irm linea');
    expect(screen.getByText('Copiado')).toBeTruthy();
  });

  it('avisa sola cuando el PC nuevo se conecta (4.17)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const lista = vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [agente()] });
      vi.spyOn(morganAPI, 'codigoAgente').mockResolvedValue({
        success: true, codigo: 'K7QF-2M9D', caduca_en: Date.now() / 1000 + 600, instalar: 'irm linea',
      });
      render(<PanelEquipos cuenta={CON_CUENTA} />);
      await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });
      lista.mockResolvedValue({ success: true, agentes: [
        agente(), agente({ id: 'agt-nuevo', nombre: 'Sobremesa', conectado: true })] });
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      expect(screen.getByText(/«Sobremesa» está conectado/)).toBeTruthy();
      expect(screen.queryByText('K7QF-2M9D')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('el que ya estaba no cuenta como nuevo (4.17)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [agente({ conectado: true })] });
      vi.spyOn(morganAPI, 'codigoAgente').mockResolvedValue({
        success: true, codigo: 'K7QF-2M9D', caduca_en: Date.now() / 1000 + 600, instalar: 'irm linea',
      });
      render(<PanelEquipos cuenta={CON_CUENTA} />);
      await act(async () => { fireEvent.click(await screen.findByText('Emparejar un equipo')); });
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      expect(screen.queryByText(/está conectado\./)).toBeNull();
      expect(screen.getByText('K7QF-2M9D')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('abre los ajustes en el PC conectado, y solo en él (4.17)', async () => {
    vi.spyOn(morganAPI, 'agentes').mockResolvedValue({ success: true, agentes: [
      agente({ conectado: true }), agente({ id: 'agt-2', nombre: 'Sobremesa', conectado: false })] });
    const abrir = vi.spyOn(morganAPI, 'abrirAjustesAgente').mockResolvedValue({ success: true });
    render(<PanelEquipos cuenta={CON_CUENTA} />);
    await screen.findByText('Sobremesa');
    await act(async () => { fireEvent.click(await screen.findByLabelText('Abrir los ajustes en Portátil')); });
    expect(abrir).toHaveBeenCalledWith('agt-1');
    expect(screen.queryByLabelText('Abrir los ajustes en Sobremesa')).toBeNull();
    expect(screen.getByText(/mira la pantalla de ese PC/)).toBeTruthy();
  });
});
