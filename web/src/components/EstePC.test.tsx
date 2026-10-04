/**
 * Ajustes → Este PC, dentro de Morgan para Windows (5.3).
 *
 * Lo que se fija:
 *
 * - **Solo sale dentro del programa.** En un navegador la sección no existe.
 * - **La web solo pide lo inofensivo**: las órdenes que salen de `lib/programa.ts` son
 *   exactamente las que deja `capabilities/web.json` (tests/test_programa_una_ventana.py mira
 *   el otro lado). Emparejar, nunca: «Conectar» solo lleva a la pantalla del programa.
 * - **Pausar y reanudar** según diga el estado, y el aviso de versión nueva con su botón.
 * - **La bandeja abre esta sección** aunque lo pida antes de que la web escuche.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { morganAPI } from '../lib/api';
import { EVENTO_DEL_PROGRAMA, dentroDelPrograma, pendienteDelPrograma, programa } from '../lib/programa';
import type { EstadoCuenta } from './Cuenta';
import { PanelEstePC } from './PanelEstePC';
import { SettingsView } from './SettingsView';

const CUENTA: EstadoCuenta = {
  cargando: false, autenticado: false, local: true, usuario: null,
  refrescar: async () => {}, salir: async () => {},
};

type Ventana = Record<string, unknown>;

function conPrograma(respuestas: Record<string, unknown> = {}) {
  const pedidas: string[] = [];
  const invoke = vi.fn(async (orden: string) => {
    pedidas.push(orden);
    if (orden in respuestas) return respuestas[orden];
    return null;
  });
  const w = window as unknown as Ventana;
  w.__MORGAN_PROGRAMA__ = '5.3.0';
  w.__TAURI_INTERNALS__ = { invoke };
  return { invoke, pedidas };
}

const CONECTADO = { clave: 'Conectado', texto: 'Conectado', pausar: true, se_puede: true };

beforeEach(() => {
  vi.spyOn(morganAPI, 'getSettings').mockResolvedValue({ success: true, settings: {} } as never);
  vi.spyOn(morganAPI, 'health').mockResolvedValue({ status: 'ok', version: '5.3.0', timestamp: '' });
});

afterEach(() => {
  const w = window as unknown as Ventana;
  delete w.__MORGAN_PROGRAMA__;
  delete w.__TAURI_INTERNALS__;
  delete w.__MORGAN_IR__;
  vi.restoreAllMocks();
});

function ajustes() {
  render(
    <SettingsView
      apiOnline tema="oscuro" onTema={vi.fn()} cuenta={CUENTA} entorno="cloud"
      onCerrar={vi.fn()} onIrA={vi.fn()} onChatTemporal={vi.fn()}
    />,
  );
}

describe('la sección', () => {
  it('no existe en un navegador', () => {
    ajustes();
    expect(screen.queryByRole('button', { name: /Este PC/ })).toBeNull();
    expect(dentroDelPrograma()).toBe(false);
  });

  it('sale dentro del programa', async () => {
    conPrograma({ resumen: CONECTADO, programa: { version: '5.3.0', nueva: null }, ultimas: '[]' });
    ajustes();
    fireEvent.click(screen.getByRole('button', { name: /Este PC/ }));
    expect(await screen.findByText('Conectado')).toBeTruthy();
  });
});

describe('lo que pide al programa', () => {
  it('solo lo inofensivo, y nunca emparejar', async () => {
    const { pedidas } = conPrograma({ resumen: CONECTADO, programa: { version: '5.3.0', nueva: '5.4.0' }, ultimas: '[]' });
    const pc = programa();
    await pc.resumen();
    await pc.pausar();
    await pc.reanudar();
    await pc.ultimas();
    await pc.abrirAjustes();
    await pc.emparejar();
    await pc.version();
    await pc.actualizar();
    expect(new Set(pedidas)).toEqual(new Set([
      'resumen', 'pausar', 'reanudar', 'ultimas', 'abrir_ajustes', 'pantalla_emparejar', 'programa', 'actualizar_ahora',
    ]));
    expect(pedidas).not.toContain('emparejar');
    expect(pedidas).not.toContain('consultar');
  });

  it('fuera del programa no pide nada', async () => {
    const w = window as unknown as Ventana;
    w.__TAURI_INTERNALS__ = { invoke: vi.fn() };
    await expect(programa().resumen()).rejects.toThrow(/Morgan para Windows/);
  });
});

describe('el panel', () => {
  it('pausa y, en pausa, reanuda', async () => {
    const { pedidas } = conPrograma({ resumen: CONECTADO, programa: { version: '5.3.0', nueva: null }, ultimas: '[]' });
    render(<PanelEstePC />);
    fireEvent.click(await screen.findByRole('button', { name: 'Pausar Morgan en este PC' }));
    await waitFor(() => expect(pedidas).toContain('pausar'));
    expect(pedidas).not.toContain('reanudar');
  });

  it('en pausa el botón reanuda', async () => {
    const { pedidas } = conPrograma({
      resumen: { clave: 'EnPausa', texto: 'En pausa', pausar: false, se_puede: true },
      programa: { version: '5.3.0', nueva: null }, ultimas: '[]',
    });
    render(<PanelEstePC />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reanudar Morgan' }));
    await waitFor(() => expect(pedidas).toContain('reanudar'));
    expect(pedidas).not.toContain('pausar');
  });

  it('sin emparejar ofrece conectar en la pantalla del programa, sin pausa', async () => {
    const { pedidas } = conPrograma({
      resumen: { clave: 'SinEmparejar', texto: 'Sin emparejar', pausar: true, se_puede: false },
      programa: { version: '5.3.0', nueva: null }, ultimas: '[]',
    });
    render(<PanelEstePC />);
    fireEvent.click(await screen.findByRole('button', { name: 'Conectar' }));
    await waitFor(() => expect(pedidas).toContain('pantalla_emparejar'));
    expect(screen.queryByRole('button', { name: /Pausar/ })).toBeNull();
  });

  it('con versión nueva, la ofrece y la instala al pulsar', async () => {
    const { pedidas } = conPrograma({ resumen: CONECTADO, programa: { version: '5.3.0', nueva: '5.4.0' }, ultimas: '[]' });
    render(<PanelEstePC />);
    expect(screen.queryByText('Al día. Se mira una vez al día.')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Actualizar a la 5.4.0' }));
    await waitFor(() => expect(pedidas).toContain('actualizar_ahora'));
  });

  it('al día, sin botón', async () => {
    conPrograma({ resumen: CONECTADO, programa: { version: '5.3.0', nueva: null }, ultimas: '[]' });
    render(<PanelEstePC />);
    expect(await screen.findByText('Al día. Se mira una vez al día.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Actualizar a/ })).toBeNull();
  });

  it('enseña lo último que hizo', async () => {
    conPrograma({
      resumen: CONECTADO, programa: { version: '5.3.0', nueva: null },
      ultimas: JSON.stringify([{ cuando: 1_790_000_000, que: 'Leer un archivo', detalle: 'notas.txt', estado: 'hecho' }]),
    });
    render(<PanelEstePC />);
    expect(await screen.findByText('Leer un archivo')).toBeTruthy();
    expect(screen.getByText('notas.txt')).toBeTruthy();
  });
});

describe('la bandeja abre Este PC', () => {
  it('lo pendiente se lee una vez', () => {
    (window as unknown as Ventana).__MORGAN_IR__ = 'este-pc';
    expect(pendienteDelPrograma()).toBe('este-pc');
    expect(pendienteDelPrograma()).toBeNull();
  });

  it('el aviso lleva el nombre que manda el programa', () => {
    // escritorio/src-tauri/src/main.rs (fn ir) lo despacha con este nombre.
    expect(EVENTO_DEL_PROGRAMA).toBe('morgan-programa');
    const oido = vi.fn();
    window.addEventListener(EVENTO_DEL_PROGRAMA, oido);
    act(() => { window.dispatchEvent(new CustomEvent(EVENTO_DEL_PROGRAMA, { detail: { ir: 'este-pc' } })); });
    window.removeEventListener(EVENTO_DEL_PROGRAMA, oido);
    expect(oido).toHaveBeenCalledTimes(1);
  });
});
