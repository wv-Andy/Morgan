/**
 * `useCuenta`: quién está usando Morgan.
 *
 * **Estas pruebas existen por un fallo concreto que llegó a producción.** La
 * web se quedaba atascada en la pantalla de arranque —«El servidor estaba en
 * reposo y está arrancando»— para siempre, sin volver nunca.
 *
 * La causa: al añadir un reintento, el camino de éxito salía con un `return`
 * que saltaba por encima del `finally` donde se apagaba la bandera `cargando`.
 * Y lo cruel es que **solo fallaba cuando `/auth/yo` funcionaba**: todos los
 * caminos de error la apagaban bien.
 *
 * Ninguna de las 1358 pruebas de Python podía verlo —comprueban el backend— ni
 * las verificaciones contra producción, que hablan con la API sin pasar por
 * React. Ese hueco es lo que este fichero cierra.
 *
 * La regla que se fija aquí, y que vale para cualquier carga futura: **la
 * bandera de «cargando» se apaga pase lo que pase**. Es lo único que separa la
 * aplicación de una pantalla de espera perpetua.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCuenta } from './Cuenta';
import { morganAPI } from '../lib/api';

const DENTRO = {
  success: true,
  autenticado: true,
  local: false,
  usuario: { id: 'usr-ana', display_name: 'Ana' },
  csrf: 'un-token',
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('la pantalla de arranque siempre acaba', () => {
  it('cuando /auth/yo responde a la primera', async () => {
    // EL CASO DEL FALLO. Se pone el primero a propósito: era el único que no
    // funcionaba, y el que menos sospechas levanta.
    vi.spyOn(morganAPI, 'yo').mockResolvedValue(DENTRO as never);

    const { result } = renderHook(() => useCuenta());

    await waitFor(() => expect(result.current.cargando).toBe(false));
    expect(result.current.autenticado).toBe(true);
  });

  it('cuando falla la primera vez y responde a la segunda', async () => {
    const yo = vi.spyOn(morganAPI, 'yo')
      .mockRejectedValueOnce(new Error('dormido'))
      .mockResolvedValueOnce(DENTRO as never);

    const { result } = renderHook(() => useCuenta());

    await waitFor(() => expect(result.current.cargando).toBe(false));
    expect(yo).toHaveBeenCalledTimes(2);
    expect(result.current.autenticado).toBe(true);
  });

  it('cuando falla las dos veces', async () => {
    vi.spyOn(morganAPI, 'yo').mockRejectedValue(new Error('caído'));

    const { result } = renderHook(() => useCuenta());

    await waitFor(() => expect(result.current.cargando).toBe(false));
    expect(result.current.autenticado).toBe(false);
  });
});

describe('el reintento', () => {
  it('no se pide dos veces si la primera funciona', async () => {
    // Un reintento incondicional doblaría las peticiones de arranque contra un
    // servicio que justo acaba de despertar.
    const yo = vi.spyOn(morganAPI, 'yo').mockResolvedValue(DENTRO as never);

    const { result } = renderHook(() => useCuenta());

    await waitFor(() => expect(result.current.cargando).toBe(false));
    expect(yo).toHaveBeenCalledTimes(1);
  });
});

describe('qué se asume cuando no se puede preguntar', () => {
  it('NO se da por hecho que este Morgan no pide cuentas', async () => {
    /**
     * Con `local` puesto, App.tsx enseña la aplicación entera. Si el backend sí
     * exige cuenta —el despliegue de la nube—, cada petición respondería
     * «Necesitas iniciar sesión» y parecería que Morgan olvidó quién eres
     * estando dentro. Es el fallo que se corrigió antes de este.
     *
     * `API_ES_REMOTA` se calcula al importar el módulo desde una variable de
     * Vite, así que en las pruebas vale `false`: la API sería la del propio
     * equipo, y ahí `local` sí es lo correcto. Lo que se fija aquí es que la
     * decisión dependa de eso y no sea un `true` fijo.
     */
    vi.spyOn(morganAPI, 'yo').mockRejectedValue(new Error('caído'));

    const { result } = renderHook(() => useCuenta());

    await waitFor(() => expect(result.current.cargando).toBe(false));
    expect(result.current.usuario).toBeNull();
    expect(result.current.autenticado).toBe(false);
  });
});

describe('cuando el backend dice que la sesión ya no vale', () => {
  it('se vuelve a preguntar quién eres', async () => {
    // Sin esto, la aplicación se quedaba abierta con la sesión muerta y cada
    // cosa que se tocara daba un error distinto, sin forma evidente de volver
    // a entrar que no fuera recargar a mano.
    const yo = vi.spyOn(morganAPI, 'yo').mockResolvedValue(DENTRO as never);

    const { result } = renderHook(() => useCuenta());
    await waitFor(() => expect(result.current.cargando).toBe(false));

    const antes = yo.mock.calls.length;
    await act(async () => {
      window.dispatchEvent(new CustomEvent('morgan:sin-sesion'));
    });

    await waitFor(() => expect(yo.mock.calls.length).toBeGreaterThan(antes));
  });
});
