/**
 * Si la conexión se corta a mitad de un turno, se espera su respuesta (4.5).
 *
 * Medido en mi prueba desde el móvil (2026-09-30): una respuesta de 60-75 s
 * perdió la conexión, la web ofreció «Reintentar», y el reintento lanzó otro turno con el
 * primero aún en marcha. La misma pregunta se procesó tres veces. Ahora la web mira la
 * conversación hasta que la respuesta está guardada, y el servidor rechaza un segundo
 * turno en la misma conversación (`TURNO_EN_CURSO`).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MorganAPIError, morganAPI, type StoredMessage } from '../lib/api';
import { ChatView } from './Chat';

function guardado(id: number, role: string, content: string, hace = 0): StoredMessage {
  return {
    id, session_id: 's1', role, content, tool_name: null, tool_call_id: null,
    created_at: new Date(Date.now() - hace).toISOString(),
  };
}

function lista(mensajes: StoredMessage[]) {
  return { success: true, session_id: 's1', count: mensajes.length, total: mensajes.length, messages: mensajes } as never;
}

async function montarYEnviar(texto: string) {
  await act(async () => {
    render(<ChatView estadoApi="online" sessionId="s1" sistema={{ herramientas: 1, entorno: 'cloud' }}
                     onConversationSaved={vi.fn()} />);
  });
  fireEvent.change(screen.getByRole('textbox'), { target: { value: texto } });
  await act(async () => { fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' }); });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('La conexión se corta a mitad del turno', () => {
  it('espera la respuesta guardada en vez de ofrecer Reintentar', async () => {
    const mensajes = vi.spyOn(morganAPI, 'sessionMessages')
      .mockResolvedValueOnce(lista([]))                                   // al abrir
      .mockResolvedValueOnce(lista([]))                                   // primera mirada: aún no
      .mockResolvedValue(lista([guardado(1, 'user', 'El mínimo?'),        // ya está
                                guardado(2, 'assistant', 'Son 365.000 colones.')]));
    const turno = vi.spyOn(morganAPI, 'chatStream').mockImplementation(async (_t, alEvento) => {
      alEvento({ tipo: 'inicio', t: 0, turno: 't1' } as never);
      throw new MorganAPIError('STREAM_CORTADO', 'La conexión se cortó.');
    });

    await montarYEnviar('El mínimo?');
    expect(await screen.findByText(/Morgan sigue con tu mensaje/)).toBeTruthy();
    expect(screen.queryByText('Reintentar')).toBeNull();

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(await screen.findByText('Son 365.000 colones.')).toBeTruthy();
    expect(screen.queryByText(/Morgan sigue con tu mensaje/)).toBeNull();
    expect(turno).toHaveBeenCalledTimes(1);
    expect(mensajes.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it('con el reloj del móvil adelantado, la reconoce porque hay mensajes nuevos', async () => {
    vi.spyOn(morganAPI, 'sessionMessages')
      .mockResolvedValueOnce(lista([]))
      .mockResolvedValueOnce(lista([]))
      // El servidor la fecha dos minutos «antes» de que el móvil la enviara.
      .mockResolvedValue(lista([guardado(1, 'user', 'hola', 120_000), guardado(2, 'assistant', 'Hola.', 120_000)]));
    vi.spyOn(morganAPI, 'chatStream').mockImplementation(async (_t, alEvento) => {
      alEvento({ tipo: 'inicio', t: 0 } as never);
      throw new MorganAPIError('STREAM_CORTADO', 'Cortada.');
    });

    await montarYEnviar('hola');
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(await screen.findByText('Hola.')).toBeTruthy();
  });

  it('si terminó antes de la primera mirada, también la reconoce', async () => {
    vi.spyOn(morganAPI, 'sessionMessages')
      .mockResolvedValueOnce(lista([]))
      .mockResolvedValue(lista([guardado(1, 'user', 'hola'), guardado(2, 'assistant', 'Hola, Andy.')]));
    vi.spyOn(morganAPI, 'chatStream').mockImplementation(async (_t, alEvento) => {
      alEvento({ tipo: 'inicio', t: 0 } as never);
      throw new MorganAPIError('NETWORK_ERROR', 'Sin red.');
    });

    await montarYEnviar('hola');
    expect(await screen.findByText('Hola, Andy.')).toBeTruthy();
  });

  it('una respuesta vieja a la misma pregunta no cuenta', async () => {
    vi.spyOn(morganAPI, 'sessionMessages').mockResolvedValue(
      lista([guardado(1, 'user', 'Intenta de nuevo', 600_000), guardado(2, 'assistant', 'La de antes.', 600_000)]));
    vi.spyOn(morganAPI, 'chatStream').mockImplementation(async (_t, alEvento) => {
      alEvento({ tipo: 'inicio', t: 0 } as never);
      throw new MorganAPIError('STREAM_CORTADO', 'Cortada.');
    });

    await montarYEnviar('Intenta de nuevo');
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(screen.getByText(/Morgan sigue con tu mensaje/)).toBeTruthy();
  });

  it('si el turno no llegó a empezar, se puede reintentar como siempre', async () => {
    vi.spyOn(morganAPI, 'sessionMessages').mockResolvedValue(lista([]));
    vi.spyOn(morganAPI, 'chatStream').mockRejectedValue(new MorganAPIError('NETWORK_ERROR', 'Sin red.'));

    await montarYEnviar('hola');
    expect(await screen.findByText('Reintentar')).toBeTruthy();
  });

  it('si ya hay un turno en marcha, el mensaje vuelve al compositor y se espera aquel', async () => {
    vi.spyOn(morganAPI, 'sessionMessages').mockResolvedValue(lista([]));
    vi.spyOn(morganAPI, 'chatStream').mockRejectedValue(
      new MorganAPIError('TURNO_EN_CURSO', 'Morgan sigue con tu mensaje anterior en esta conversación.'));

    await montarYEnviar('Como? Intenta de nuevo');
    expect(await screen.findByText(/sigue con tu mensaje anterior/)).toBeTruthy();
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Como? Intenta de nuevo');
    expect(screen.queryByText('Reintentar')).toBeNull();
  });
});
