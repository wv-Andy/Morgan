/**
 * El streaming en el cliente: leer líneas y traducirlas (V2.0.14).
 *
 * `flujo()` se prueba con respuestas cuyo cuerpo llega **en trozos**, porque así
 * llega de verdad: una línea puede partirse entre dos lecturas.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MorganAPIError, OperacionCancelada, guardarCsrf, morganAPI } from './api';
import type { EventoDelTurno } from './api';
import { fraseDelEvento } from './progreso';

/** Una respuesta NDJSON que entrega `trozos` uno a uno. */
function stream(trozos: string[], init: { status?: number; tipo?: string } = {}) {
  const codificador = new TextEncoder();
  const cuerpo = new ReadableStream<Uint8Array>({
    start(control) {
      for (const trozo of trozos) control.enqueue(codificador.encode(trozo));
      control.close();
    },
  });
  return new Response(cuerpo, {
    status: init.status ?? 200,
    headers: { 'content-type': init.tipo ?? 'application/x-ndjson' },
  });
}

beforeEach(() => {
  guardarCsrf('');
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('chatStream', () => {
  it('pasa el progreso y devuelve el fin', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream([
      '{"tipo":"inicio","t":0}\n{"tipo":"pensa',              // partida a mitad
      'ndo","vuelta":1,"t":0.1}\n',
      '{"tipo":"herramienta","nombre":"search_web","estado":"empieza","t":0.5}\n',
      '{"tipo":"fin","success":true,"response":"listo","model":"groq","elapsed_seconds":1.2,"t":1.2}\n',
    ]));
    const vistos: EventoDelTurno[] = [];

    const fin = await morganAPI.chatStream('hola', e => vistos.push(e), 'ses');

    expect(vistos.map(e => e.tipo)).toEqual(['inicio', 'pensando', 'herramienta']);
    expect(fin.response).toBe('listo');
    expect(fin.model).toBe('groq');
  });

  it('va por el mismo fetch, con cookie y CSRF', async () => {
    guardarCsrf('token-del-stream');
    const espia = vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream([
      '{"tipo":"fin","success":true,"response":"","model":"m","elapsed_seconds":0,"t":0}\n',
    ]));

    await morganAPI.chatStream('hola', () => {});

    const [url, init] = espia.mock.calls[0];
    expect(String(url)).toContain('/chat/stream');
    expect((init as RequestInit).credentials).toBe('include');
    expect(((init as RequestInit).headers as Record<string, string>)['X-Morgan-CSRF']).toBe('token-del-stream');
  });

  it('un evento de error se lanza con su código', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream([
      '{"tipo":"inicio","t":0}\n',
      '{"tipo":"error","code":"AGENT_EXECUTION_ERROR","message":"Error durante la ejecución del agente.","t":1}\n',
    ]));

    await expect(morganAPI.chatStream('hola', () => {})).rejects.toMatchObject({
      code: 'AGENT_EXECUTION_ERROR',
    });
  });

  it('si se corta sin fin lo dice, sin culpar al servidor', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream(['{"tipo":"inicio","t":0}\n']));

    const error = await morganAPI.chatStream('hola', () => {}).catch(e => e);

    expect(error).toBeInstanceOf(MorganAPIError);
    expect(error.code).toBe('STREAM_CORTADO');
    expect(error.message).not.toMatch(/en ejecución/);
  });

  it('un error antes de empezar llega como error normal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ success: false, error: { code: 'CUOTA_AGOTADA', message: 'Has llegado al límite de hoy.' } }),
      { status: 429, headers: { 'content-type': 'application/json' } },
    ));

    await expect(morganAPI.chatStream('hola', () => {})).rejects.toMatchObject({
      code: 'CUOTA_AGOTADA',
      message: 'Has llegado al límite de hoy.',
    });
  });

  it('una página HTML del proxy no se toma por «servidor apagado»', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      '<html>An error occurred</html>', { status: 502, headers: { 'content-type': 'text/html' } },
    ));

    const error = await morganAPI.chatStream('hola', () => {}).catch(e => e);

    expect(error.code).toBe('HTTP_502');
    expect(error.message).toMatch(/La red cortó la espera/);
  });

  it('detener es cancelar, no un error', async () => {
    const control = new AbortController();
    control.abort();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(stream([]));

    await expect(
      morganAPI.chatStream('hola', () => {}, 'ses', false, [], control.signal),
    ).rejects.toBeInstanceOf(OperacionCancelada);
  });
});

describe('fraseDelEvento', () => {
  it('traduce las herramientas a lo que están haciendo', () => {
    expect(fraseDelEvento({ tipo: 'herramienta', nombre: 'search_web', estado: 'empieza', t: 1 }))
      .toBe('Buscando en internet…');
    expect(fraseDelEvento({ tipo: 'herramienta', nombre: 'github_listar_prs', estado: 'empieza', t: 1 }))
      .toBe('Consultando GitHub…');
    expect(fraseDelEvento({ tipo: 'herramienta', nombre: 'una_nueva', estado: 'empieza', t: 1 }))
      .toBe('Trabajando en ello…');
  });

  it('borrar en el PC dice que espera tu confirmación allí (3.3)', () => {
    expect(fraseDelEvento({ tipo: 'herramienta', nombre: 'delete_file', estado: 'empieza', t: 1 }))
      .toMatch(/confirmes en tu PC/);
    expect(fraseDelEvento({ tipo: 'herramienta', nombre: 'edit_file', estado: 'empieza', t: 1 }))
      .toBe('Editando el archivo en tu PC…');
  });

  it('cuenta cómo va la orden en el PC (3.4)', () => {
    expect(fraseDelEvento({ tipo: 'equipo', estado: 'PENDING', posicion: 1, t: 1 })).toBe('En cola en tu PC…');
    expect(fraseDelEvento({ tipo: 'equipo', estado: 'PENDING', posicion: 3, t: 1 }))
      .toBe('En cola en tu PC (2 delante)…');
    expect(fraseDelEvento({ tipo: 'equipo', estado: 'CANCEL_REQUESTED', t: 1 })).toBe('Cancelando en tu PC…');
    expect(fraseDelEvento({ tipo: 'equipo', estado: 'SIN_VUELTA', t: 1 })).toMatch(/no se pudo parar/);
    // En marcha no cambia la frase: ya se enseña la de la herramienta.
    expect(fraseDelEvento({ tipo: 'equipo', estado: 'RUNNING', t: 1 })).toBeNull();
  });

  it('«Detener» avisa a la nube con el turno (3.4)', async () => {
    const pedido = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ success: true, parado: true }), {
        status: 200, headers: { 'content-type': 'application/json' },
      }));
    await morganAPI.pararTurno('t-123');
    const [url, init] = pedido.mock.calls[0];
    expect(String(url)).toMatch(/\/chat\/parar$/);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ turno: 't-123' });
  });

  it('el latido y el final de una herramienta no cambian la frase', () => {
    expect(fraseDelEvento({ tipo: 'latido', t: 10 })).toBeNull();
    expect(fraseDelEvento({ tipo: 'herramienta', nombre: 'search_web', estado: 'ok', t: 2 })).toBeNull();
  });

  it('dice cuando contesta un respaldo', () => {
    expect(fraseDelEvento({ tipo: 'respaldo', proveedor: 'gemini', t: 3 })).toMatch(/respaldo/);
  });
});
