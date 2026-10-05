/**
 * El cliente HTTP: CSRF, cancelación y sesión perdida.
 *
 * Se prueba aquí y no contra producción porque lo que importa es **lo que hace
 * el navegador**, y eso no se ve hablando con la API desde un script: los tres
 * fallos que rompieron la web en la nube vivían justo en esa diferencia.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  EVENTO_SIN_SESION,
  MorganAPIError,
  OperacionCancelada,
  guardarCsrf,
  morganAPI,
} from './api';

function respuesta(cuerpo: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => cuerpo,
  } as Response;
}

beforeEach(() => {
  guardarCsrf('');
  vi.restoreAllMocks();
});

afterEach(() => {
  guardarCsrf('');
  vi.restoreAllMocks();
});

describe('el token CSRF', () => {
  it('se recoge de cualquier respuesta que lo traiga', async () => {
    /**
     * Se recoge en `request` y no en cada función de `/auth/*` para que una
     * ruta nueva no pueda olvidarse de hacerlo. Es lo que arregló los 403 en
     * todos los POST: la cookie pertenece al dominio de la API y el JavaScript
     * de otro dominio no puede leerla.
     */
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta({ success: true, autenticado: true, csrf: 'token-nuevo' }),
    );

    await morganAPI.yo();

    // La siguiente petición ya lo lleva en la cabecera.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta({ success: true, sessions: [] }),
    );
    await morganAPI.sessions();

    const cabeceras = (fetchSpy.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(cabeceras['X-Morgan-CSRF']).toBe('token-nuevo');
  });

  it('se olvida al cerrar sesión', async () => {
    // Sin esto, el token de la sesión cerrada viajaría en las peticiones de la
    // siguiente. No es un agujero —el backend lo compara con su cookie— pero sí
    // un 403 desconcertante.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta({ success: true, csrf: 'token-viejo' }),
    );
    await morganAPI.yo();

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respuesta({ success: true }));
    await morganAPI.logout();

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta({ success: true, sessions: [] }),
    );
    await morganAPI.sessions();

    const cabeceras = (fetchSpy.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(cabeceras['X-Morgan-CSRF']).toBeUndefined();
  });
});

describe('cancelar un turno', () => {
  it('no es lo mismo que un tiempo agotado', async () => {
    /**
     * Se distinguen con una excepción propia. Mezclarlas hacía que parar una
     * respuesta enseñara un error de tiempo excedido, cuando pararla es
     * justamente lo que se acababa de pedir.
     */
    const control = new AbortController();
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      control.abort();
      return Promise.reject(Object.assign(new Error('abortada'), { name: 'AbortError' }));
    });

    await expect(
      morganAPI.chat('hola', 's1', false, [], control.signal),
    ).rejects.toBeInstanceOf(OperacionCancelada);
  });

  it('una señal ya abortada ni siquiera sale a la red', async () => {
    const control = new AbortController();
    control.abort();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    await expect(
      morganAPI.chat('hola', 's1', false, [], control.signal),
    ).rejects.toBeInstanceOf(OperacionCancelada);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe('cuando la sesión deja de valer', () => {
  it('se avisa para que la interfaz enseñe el acceso', async () => {
    /**
     * Sin esto, la aplicación se quedaba abierta con la sesión muerta y cada
     * cosa que se tocara daba un error distinto, sin ninguna forma evidente de
     * volver a entrar que no fuera recargar a mano.
     */
    const avisos: Event[] = [];
    const oyente = (e: Event) => avisos.push(e);
    window.addEventListener(EVENTO_SIN_SESION, oyente);

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta(
        { success: false, error: { code: 'SIN_SESION', message: 'Necesitas iniciar sesión' } },
        false,
        401,
      ),
    );

    await expect(morganAPI.sessions()).rejects.toBeInstanceOf(MorganAPIError);
    expect(avisos).toHaveLength(1);

    window.removeEventListener(EVENTO_SIN_SESION, oyente);
  });

  it('pero un 401 de otra cosa no dispara el aviso', async () => {
    // Un token de API que falta no es una sesión caducada, y tratarlos igual
    // devolvería a la pantalla de acceso a quien no la necesita.
    const avisos: Event[] = [];
    const oyente = (e: Event) => avisos.push(e);
    window.addEventListener(EVENTO_SIN_SESION, oyente);

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta(
        { success: false, error: { code: 'UNAUTHORIZED', message: 'Falta el token' } },
        false,
        401,
      ),
    );

    await expect(morganAPI.sessions()).rejects.toBeInstanceOf(MorganAPIError);
    expect(avisos).toHaveLength(0);

    window.removeEventListener(EVENTO_SIN_SESION, oyente);
  });
});

describe('los errores llegan con su mensaje', () => {
  it('se conserva el código y el texto del backend', async () => {
    // Enseñar «Error 500» cuando el backend explicó qué pasó es tirar la única
    // información útil que había.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      respuesta(
        { success: false, error: { code: 'CUOTA_AGOTADA', message: 'Vuelve mañana.' } },
        false,
        429,
      ),
    );

    await expect(morganAPI.sessions()).rejects.toMatchObject({
      code: 'CUOTA_AGOTADA',
      message: 'Vuelve mañana.',
    });
  });

  it('un fallo de red se distingue de una respuesta con error', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('sin red'));

    await expect(morganAPI.sessions()).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
    });
  });
});

describe('cuando el que responde no es Morgan', () => {
  /**
   * El borde de Vercel corta los turnos a los 120 s y devuelve una página HTML
   * de error suya. Medido en producción: dos cortes a 120.1 s.
   *
   * Antes, `res.json()` lanzaba con ese HTML, el fallo caía hasta el final y
   * salía «¿Está el servidor en ejecución?» — falso, porque el servidor sigue
   * trabajando, y además manda a buscar el problema donde no está.
   */
  function paginaDeError(status: number) {
    return {
      ok: false,
      status,
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON');
      },
    } as unknown as Response;
  }

  it('un 502 del borde no se culpa al servidor', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(paginaDeError(502));

    await expect(morganAPI.status()).rejects.toMatchObject({
      code: 'HTTP_502',
    });

    try {
      await morganAPI.status();
    } catch (err) {
      const mensaje = (err as MorganAPIError).message;
      expect(mensaje).not.toContain('servidor en ejecución');
      expect(mensaje).toContain('por partes');
    }
  });

  it('y cualquier otro cuerpo ilegible dice justo eso', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(paginaDeError(500));

    try {
      await morganAPI.status();
      expect.unreachable('tenía que fallar');
    } catch (err) {
      expect((err as MorganAPIError).code).toBe('HTTP_500');
      expect((err as MorganAPIError).message).toContain('no pudo contestar');
    }
  });
});

describe('subir un archivo', () => {
  /**
   * Tuvo su propio `fetch` durante cinco versiones, copiado del de `request`.
   * En la copia se quedaron fuera `credentials: 'include'` y la cabecera CSRF,
   * así que la subida llegaba **sin sesión**: el servidor contestaba
   * «Necesitas iniciar sesión para usar Morgan» a quien la tenía abierta.
   *
   * Reproducido contra producción: sin cookie da 401, con cookie y sin CSRF da
   * 403, y con las dos cosas da 200.
   */
  function archivo() {
    return new File(['hola'], 'nota.txt', { type: 'text/plain' });
  }

  it('manda la cookie de sesión', async () => {
    const espia = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(respuesta({ id: 'a1', nombre: 'nota.txt' }));

    await morganAPI.uploadFile(archivo());

    const opciones = espia.mock.calls[0][1] as RequestInit;
    expect(opciones.credentials).toBe('include');
  });

  it('y la comprobación CSRF, o el servidor la rechaza con un 403', async () => {
    guardarCsrf('el-token');
    const espia = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(respuesta({ id: 'a1', nombre: 'nota.txt' }));

    await morganAPI.uploadFile(archivo());

    const cabeceras = (espia.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(cabeceras['X-Morgan-CSRF']).toBe('el-token');
  });

  it('sin fijar el Content-Type, que lo pone el navegador', async () => {
    /**
     * En multipart la cabecera lleva un `boundary` que genera el navegador.
     * Fijarla a mano deja la subida rota en silencio: el servidor no encuentra
     * el archivo en el cuerpo. Era la razón de tener un `fetch` aparte, y es lo
     * que ahora `request` sabe hacer.
     */
    const espia = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(respuesta({ id: 'a1', nombre: 'nota.txt' }));

    await morganAPI.uploadFile(archivo());

    const cabeceras = (espia.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(cabeceras['Content-Type']).toBeUndefined();
  });

  it('pero una petición normal sí lo lleva', async () => {
    const espia = vi.spyOn(globalThis, 'fetch').mockResolvedValue(respuesta({ success: true }));

    await morganAPI.status();

    const cabeceras = (espia.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(cabeceras['Content-Type']).toBe('application/json');
  });
});

describe('los errores, en palabras que entiende cualquiera (4.23)', () => {
  const JERGA = ['api', 'servidor en ejecución', 'ilegible', 'error 4', 'error 5', 'http'];
  const limpio = (m: string) => !JERGA.some(j => m.toLowerCase().includes(j));

  it('sin conexión dice qué mirar', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    const err = (await morganAPI.status().then(() => null, e => e)) as MorganAPIError;
    expect(err.code).toBe('NETWORK_ERROR');
    expect(limpio(err.message)).toBe(true);
    expect(err.message).toContain('conexión a internet');
  });

  it('un error sin mensaje no se queda en «Error 422»', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(respuesta({ detail: 'algo' }, false, 422));
    const err = (await morganAPI.status().then(() => null, e => e)) as MorganAPIError;
    expect(err.code).toBe('HTTP_422');
    expect(limpio(err.message)).toBe(true);
    expect(err.message).toContain('Vuelve a intentarlo');
  });
});
