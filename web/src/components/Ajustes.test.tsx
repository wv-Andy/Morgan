/**
 * Los ajustes como panel y la vista de estado en palabras de quien usa Morgan
 * (V2.0.29).
 *
 * Lo que se fija:
 *
 * - **El panel se cierra con Escape** y el buscador deja solo las secciones que
 *   coinciden, enseñando una de ellas: un título a la derecha que no está en la
 *   lista de la izquierda se lee como un fallo.
 * - **Cambiar el idioma no pisa lo que se esté escribiendo en el perfil.** Se
 *   guarda al momento, y el resto del formulario sigue a medias.
 * - **El estado no enseña nombres internos**: ni proveedores de modelos, ni
 *   bases de datos, ni subsistemas. Es lo que pedí, y es fácil que vuelva
 *   a colarse al añadir un campo en el servidor.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { morganAPI } from '../lib/api';
import type { EstadoCuenta } from './Cuenta';
import { SettingsView } from './SettingsView';
import { StatusView } from './VistasDeConsulta';

const CUENTA_LOCAL: EstadoCuenta = {
  cargando: false,
  autenticado: false,
  local: true,
  usuario: null,
  refrescar: async () => {},
  salir: async () => {},
};

const AJUSTES = {
  nombre: 'Ana', ocupacion: '', sobre_mi: '', idioma: '', estilo_respuesta: '',
};

function abrir(props: Partial<Parameters<typeof SettingsView>[0]> = {}) {
  const onCerrar = vi.fn();
  const onTema = vi.fn();
  render(
    <SettingsView
      apiOnline
      tema="oscuro"
      onTema={onTema}
      cuenta={CUENTA_LOCAL}
      entorno="cloud"
      onCerrar={onCerrar}
      onIrA={vi.fn()}
      onChatTemporal={vi.fn()}
      {...props}
    />,
  );
  return { onCerrar, onTema };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(morganAPI, 'getSettings').mockResolvedValue({ success: true, settings: { ...AJUSTES } } as never);
  vi.spyOn(morganAPI, 'health').mockResolvedValue({ status: 'ok', version: '2.0.29-dev', timestamp: '' });
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('El panel de ajustes', () => {
  it('se cierra con Escape', () => {
    const { onCerrar } = abrir();

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onCerrar).toHaveBeenCalledTimes(1);
  });

  it('el buscador deja solo lo que coincide y enseña esa sección', () => {
    abrir();

    fireEvent.change(screen.getByLabelText('Buscar ajustes'), { target: { value: 'contraseña' } });

    const nav = screen.getByRole('navigation', { name: 'Secciones de ajustes' });
    expect(nav.textContent).toBe('Cuenta');
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Cuenta');
  });

  it('cambia el tema desde General', () => {
    const { onTema } = abrir();

    fireEvent.change(screen.getByLabelText('Apariencia'), { target: { value: 'claro' } });

    expect(onTema).toHaveBeenCalledWith('claro');
  });

  it('guardar el idioma no pisa lo que se está escribiendo en el perfil', async () => {
    const guardar = vi.spyOn(morganAPI, 'saveSettings').mockImplementation(
      async (s) => ({ success: true, settings: s }) as never,
    );
    abrir({ seccionInicial: 'personalizacion' });
    const nombre = await screen.findByDisplayValue('Ana');

    // A medias: sin guardar.
    fireEvent.change(nombre, { target: { value: 'Ana María' } });

    fireEvent.click(screen.getByRole('button', { name: 'General' }));
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Idioma de las respuestas'), { target: { value: 'English' } });
    });

    // Se guarda el idioma con lo que HABÍA guardado, no con el borrador…
    expect(guardar).toHaveBeenCalledWith({ ...AJUSTES, idioma: 'English' });

    // …y el borrador sigue ahí.
    fireEvent.click(screen.getByRole('button', { name: 'Personalización' }));
    expect(screen.getByDisplayValue('Ana María')).toBeTruthy();
  });

  it('el perfil solo se puede guardar si ha cambiado algo', async () => {
    abrir({ seccionInicial: 'personalizacion' });
    const nombre = await screen.findByDisplayValue('Ana');
    const boton = screen.getByRole('button', { name: 'Guardar cambios' }) as HTMLButtonElement;

    expect(boton.disabled).toBe(true);
    fireEvent.change(nombre, { target: { value: 'Ana María' } });
    expect(boton.disabled).toBe(false);
  });
});

describe('La vista de estado', () => {
  it('no enseña nombres internos: proveedores, bases de datos ni subsistemas', async () => {
    vi.spyOn(morganAPI, 'status').mockResolvedValue({
      status: 'ok',
      mode: 'normal',
      tools_count: 29,
      domains_count: 4,
      environment: 'cloud',
      components: {
        llm: { status: 'ok', details: 'Modelo: Groq:openai/gpt-oss-120b [Respaldo: gemini-3.6-flash]' },
        database: { status: 'ok', details: 'Supabase conectado' },
      },
      services: [
        { name: 'llm', state: 'available', detail: 'Groq:openai/gpt-oss-120b [Respaldo: OpenAI:gpt-5.6-luna]', required_for: [] },
        { name: 'database.local', state: 'available', detail: 'SQLite operativo', required_for: [] },
        { name: 'database.remote', state: 'degraded', detail: 'Supabase operativo (62 ms)', required_for: [] },
        { name: 'correo', state: 'available', detail: 'Enviando por api:brevo', required_for: [] },
      ],
      capabilities: [
        { name: 'Conversación (LLM)', available: true },
        { name: 'Archivos del equipo', available: false, reason: 'No disponible en el entorno cloud' },
      ],
      sync: { enabled: false, pending: 0, failed: 0 },
    });

    render(<StatusView />);
    await waitFor(() => expect(screen.getByText('Tus datos')).toBeTruthy());
    const texto = document.body.textContent ?? '';

    for (const interno of ['Groq', 'gpt', 'gemini', 'OpenAI', 'Supabase', 'SQLite', 'brevo', 'database', 'llm', 'LLM', 'dominios', 'Subsistemas', 'cloud']) {
      expect(texto, `se ve «${interno}»`).not.toContain(interno);
    }
    // Las dos bases son «tus datos», con el peor de sus estados.
    expect(screen.getByText('Tus datos').parentElement?.textContent).toContain('Con problemas');
    expect(texto).toContain('Solo en el Morgan de tu equipo');
  });
});
