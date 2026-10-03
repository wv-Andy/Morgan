/**
 * Lo que ve alguien nuevo al entrar (auditoría 2.3, V2.0.33).
 *
 * Salió de recorrer la web como una persona que acaba de crearse la cuenta, con
 * el modelo real:
 *
 * - **La portada decía lo que Morgan no puede hacer.** En la nube anunciaba «leer
 *   y escribir archivos, ejecutar comandos…» porque el texto dependía de cómo se
 *   compiló la web y no de dónde corre Morgan.
 * - **Mientras contestaba, el campo decía «Morgan API desconectada…».** Ocupado y
 *   caído compartían la misma variable, y quien escribía por primera vez creía
 *   haberlo roto.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { morganAPI } from '../lib/api';
import { ChatView } from './Chat';

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(morganAPI, 'sessionMessages').mockResolvedValue({ success: true, messages: [] } as never);
  // jsdom no implementa el desplazamiento suave que usa la conversación.
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = '';
});

function portada(entorno: string) {
  render(
    <ChatView
      estadoApi="online"
      sessionId="s1"
      sistema={{ herramientas: 29, entorno }}
      onConversationSaved={vi.fn()}
    />,
  );
}

describe('La portada', () => {
  it('en la nube no promete archivos ni comandos', () => {
    portada('cloud');
    const texto = document.body.textContent ?? '';

    expect(texto).toContain('buscar en internet');
    expect(texto).not.toContain('ejecutar comandos');
  });

  it('en tu equipo sí los ofrece', () => {
    portada('local');

    expect(document.body.textContent).toContain('ejecutar comandos');
  });
});

describe('Mientras Morgan contesta', () => {
  it('el campo dice que está respondiendo, no que está desconectado', async () => {
    // Un turno que no termina: lo que se mira es el estado de «ocupado».
    vi.spyOn(morganAPI, 'chatStream').mockImplementation(() => new Promise(() => {}));
    portada('cloud');

    const campo = screen.getByLabelText('Mensaje para Morgan') as HTMLTextAreaElement;
    fireEvent.change(campo, { target: { value: 'hola' } });
    await act(async () => {
      fireEvent.click(screen.getByLabelText('Enviar mensaje'));
    });

    const ahora = screen.getByLabelText('Mensaje para Morgan') as HTMLTextAreaElement;
    expect(ahora.placeholder).toBe('Morgan está respondiendo…');
    expect(ahora.placeholder).not.toContain('desconectada');
  });
});
