/**
 * El espacio de trabajo en el cliente HTTP (V2.2).
 *
 * La cabecera se pone en `request` para que ninguna llamada nueva pueda olvidarse
 * de ella. Aquí se fija eso, y lo que pasa cuando el servidor dice que el espacio
 * seleccionado ya no existe.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  EVENTO_ESPACIO_PERDIDO,
  MorganAPIError,
  espacioActual,
  fijarEspacioActual,
  morganAPI,
} from './api';

function respuesta(cuerpo: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => cuerpo } as Response;
}

function cabecerasDe(spy: { mock: { calls: unknown[][] } }): Record<string, string> {
  return ((spy.mock.calls[0][1] as RequestInit).headers ?? {}) as Record<string, string>;
}

beforeEach(() => {
  fijarEspacioActual(null);
  vi.restoreAllMocks();
});

afterEach(() => {
  fijarEspacioActual(null);
  vi.restoreAllMocks();
});

describe('la cabecera del espacio', () => {
  it('va en cada petición con el espacio seleccionado', async () => {
    fijarEspacioActual('esp-tesis');
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta({ success: true, uploads: [], count: 0 }),
    );

    await morganAPI.uploads();

    expect(cabecerasDe(spy)['X-Morgan-Espacio']).toBe('esp-tesis');
  });

  it('no va en General', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta({ success: true, sessions: [] }),
    );

    await morganAPI.sessions();

    expect(cabecerasDe(spy)).not.toHaveProperty('X-Morgan-Espacio');
  });

  it('se recuerda entre recargas', () => {
    fijarEspacioActual('esp-tesis');
    expect(localStorage.getItem('morgan_espacio')).toBe('esp-tesis');

    fijarEspacioActual(null);
    expect(localStorage.getItem('morgan_espacio')).toBeNull();
  });
});

describe('un espacio que ya no existe', () => {
  it('vuelve a General y avisa a la aplicación', async () => {
    fijarEspacioActual('esp-borrado');
    const avisos = vi.fn();
    window.addEventListener(EVENTO_ESPACIO_PERDIDO, avisos);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respuesta(
      { success: false, error: { code: 'ESPACIO_ACTUAL_NO_ENCONTRADO', message: 'No existe.' } },
      false, 404,
    ));

    await expect(morganAPI.uploads()).rejects.toBeInstanceOf(MorganAPIError);

    expect(espacioActual()).toBeNull();
    expect(avisos).toHaveBeenCalledTimes(1);
    window.removeEventListener(EVENTO_ESPACIO_PERDIDO, avisos);
  });

  it('un 404 de mover una conversación NO pierde el espacio seleccionado', async () => {
    /**
     * Mover a un espacio que no existe es un error de esa operación, no del
     * espacio en el que se está. Con el mismo código, la web te sacaría de tu
     * proyecto por intentar mover una conversación.
     */
    fijarEspacioActual('esp-tesis');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respuesta(
      { success: false, error: { code: 'ESPACIO_DESTINO_NO_ENCONTRADO', message: 'No existe.' } },
      false, 404,
    ));

    await expect(morganAPI.updateSession('s1', { espacio_id: 'esp-otro' })).rejects.toThrow();

    expect(espacioActual()).toBe('esp-tesis');
  });
});
