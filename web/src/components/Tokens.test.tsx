/**
 * Los tokens de API en Ajustes (plan de la API, fase 2, V2.0.41).
 *
 * Lo que se fija, que es la prueba de aceptación de la fase —«crear, ver una vez,
 * revocar»— y lo que la hace segura de usar para alguien que no ha visto un token
 * nunca:
 *
 * - **El valor se ve una sola vez** y la caja no se cierra sola: se cierra cuando
 *   la persona dice que lo ha guardado, y entonces desaparece de la página.
 * - **Por defecto no se pide nada que pueda borrar**: chatear y leer, 90 días.
 * - **Revocar pide confirmación**: el primer clic no toca nada.
 * - Sin cuenta (el Morgan de tu equipo) no se ofrece nada que no funcionaría.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MorganAPIError, morganAPI, type TokenDeApi } from '../lib/api';
import type { EstadoCuenta } from './Cuenta';
import { PanelTokens } from './PanelTokens';
import { SettingsView } from './SettingsView';

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
const VALOR = 'mgn_ValorSecretoDeEjemploQueSoloSeEnseñaUnaVez12345';

function token(parcial: Partial<TokenDeApi> = {}): TokenDeApi {
  return {
    id: 'tok-1', nombre: 'script de copias', alcances: ['chat', 'lectura'],
    creado_en: AHORA - 86400, caduca_en: AHORA + 60 * 86400, ultimo_uso: null,
    ...parcial,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('Los tokens de API en Ajustes', () => {
  it('sin cuenta lo explica y no pide nada al servidor', () => {
    const lista = vi.spyOn(morganAPI, 'tokens');

    render(<PanelTokens cuenta={LOCAL} />);

    expect(screen.getByText(/no pide cuenta/)).toBeTruthy();
    expect(screen.queryByText('Crear un token')).toBeNull();
    expect(lista).not.toHaveBeenCalled();
  });

  it('lista los tokens con lo que pueden hacer, y avisa del que caduca pronto', async () => {
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({
      success: true,
      tokens: [
        token(),
        token({ id: 'tok-2', nombre: 'VS Code', alcances: ['escritura'], caduca_en: AHORA + 3 * 86400 }),
      ],
    });

    render(<PanelTokens cuenta={CON_CUENTA} />);

    expect(await screen.findByText('script de copias')).toBeTruthy();
    expect(screen.getByText('Puede: chatear, leer')).toBeTruthy();
    expect(screen.getByText('Puede: cambiar')).toBeTruthy();
    const pronto = screen.getByText(/Caduca en 3 días/);
    expect(pronto.className).toContain('ajustes-token__pronto');
    expect(screen.getByText(/Caduca el/).className).not.toContain('pronto');
  });

  it('crea con chatear y leer por defecto y enseña el valor una sola vez', async () => {
    vi.spyOn(morganAPI, 'tokens')
      .mockResolvedValueOnce({ success: true, tokens: [] })
      .mockResolvedValue({ success: true, tokens: [token()] });
    const crear = vi.spyOn(morganAPI, 'crearToken').mockResolvedValue({
      success: true, token: token(), valor: VALOR,
    });

    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByText('Crear un token'));
    fireEvent.change(screen.getByPlaceholderText(/script de copias/), { target: { value: '  script de copias ' } });
    await act(async () => { fireEvent.click(screen.getByText('Crear')); });

    expect(crear).toHaveBeenCalledWith('script de copias', ['chat', 'lectura'], 90);
    const campo = screen.getByLabelText('Valor del token') as HTMLTextAreaElement;
    expect(campo.value).toBe(VALOR);
    expect(campo.readOnly).toBe(true);
    expect(screen.getByText(/No se volverá a/)).toBeTruthy();

    fireEvent.click(screen.getByText('Ya lo he guardado'));

    expect(screen.queryByLabelText('Valor del token')).toBeNull();
    expect(document.body.innerHTML).not.toContain(VALOR);
    expect(screen.getByText('Crear un token')).toBeTruthy();
  });

  it('no deja crear sin nombre o sin nada que pueda hacer', async () => {
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({ success: true, tokens: [] });

    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByText('Crear un token'));
    const boton = screen.getByText('Crear') as HTMLButtonElement;
    expect(boton.disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/script de copias/), { target: { value: 'x' } });
    expect(boton.disabled).toBe(false);

    fireEvent.click(screen.getByLabelText(/Chatear/));
    fireEvent.click(screen.getByLabelText(/Leer/));
    expect(boton.disabled).toBe(true);
    expect(screen.getByText(/al menos una cosa/)).toBeTruthy();
  });

  it('revocar pide confirmación: el primer clic no toca nada', async () => {
    vi.spyOn(morganAPI, 'tokens')
      .mockResolvedValueOnce({ success: true, tokens: [token()] })
      .mockResolvedValue({ success: true, tokens: [] });
    const revocar = vi.spyOn(morganAPI, 'revocarToken').mockResolvedValue({ success: true });

    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByLabelText('Revocar script de copias'));

    expect(revocar).not.toHaveBeenCalled();

    await act(async () => { fireEvent.click(screen.getByText('Sí, revocar')); });

    expect(revocar).toHaveBeenCalledWith('tok-1');
    expect(screen.getByText(/ya no vale/)).toBeTruthy();
    expect(await screen.findByText('No tienes ninguno.')).toBeTruthy();
  });

  it('enseña el motivo del servidor si no se puede crear', async () => {
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({ success: true, tokens: [] });
    vi.spyOn(morganAPI, 'crearToken').mockRejectedValue(
      new MorganAPIError('TOKENS_DESACTIVADOS', 'Este Morgan no tiene activados los tokens de API.'),
    );

    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByText('Crear un token'));
    fireEvent.change(screen.getByPlaceholderText(/script de copias/), { target: { value: 'x' } });
    await act(async () => { fireEvent.click(screen.getByText('Crear')); });

    expect(screen.getByText(/no tiene activados/)).toBeTruthy();
    expect(screen.queryByLabelText('Valor del token')).toBeNull();
  });

  it('sin permiso para el portapapeles deja el valor seleccionado para copiarlo a mano', async () => {
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({ success: true, tokens: [] });
    vi.spyOn(morganAPI, 'crearToken').mockResolvedValue({ success: true, token: token(), valor: VALOR });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('sin permiso')) },
    });

    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByText('Crear un token'));
    fireEvent.change(screen.getByPlaceholderText(/script de copias/), { target: { value: 'x' } });
    await act(async () => { fireEvent.click(screen.getByText('Crear')); });
    await act(async () => { fireEvent.click(screen.getByText('Copiar')); });

    const campo = screen.getByLabelText('Valor del token') as HTMLTextAreaElement;
    expect(campo.selectionEnd! - campo.selectionStart!).toBe(VALOR.length);
    expect(screen.getByText(/cópialo con Ctrl\+C/)).toBeTruthy();
  });

  it('con la dirección de la API enseña ejemplos que funcionan tal cual (4.17)', async () => {
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({ success: true, tokens: [] });
    vi.spyOn(morganAPI, 'crearToken').mockResolvedValue({
      success: true, token: token(), valor: VALOR, api_url: 'https://api.morgan.ejemplo' });
    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByText('Crear un token'));
    fireEvent.change(screen.getByPlaceholderText(/script de copias/), { target: { value: 'x' } });
    await act(async () => { fireEvent.click(screen.getByText('Crear')); });
    const ejemplo = () => screen.getByLabelText(/Copiar el ejemplo de/).previousElementSibling!.textContent!;
    expect(ejemplo()).toContain('Invoke-RestMethod -Method Post -Uri "https://api.morgan.ejemplo/chat"');
    expect(ejemplo()).toContain(`Bearer ${VALOR}`);
    fireEvent.click(screen.getByRole('tab', { name: 'Python' }));
    expect(ejemplo()).toContain('requests.post("https://api.morgan.ejemplo/chat"');
    expect(screen.getByText('https://api.morgan.ejemplo/docs')).toBeTruthy();
  });

  it('sin «chatear», el ejemplo lee tus conversaciones (4.17)', async () => {
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({ success: true, tokens: [] });
    vi.spyOn(morganAPI, 'crearToken').mockResolvedValue({
      success: true, token: token({ alcances: ['lectura'] }), valor: VALOR, api_url: 'https://api' });
    render(<PanelTokens cuenta={CON_CUENTA} />);
    fireEvent.click(await screen.findByText('Crear un token'));
    fireEvent.change(screen.getByPlaceholderText(/script de copias/), { target: { value: 'x' } });
    await act(async () => { fireEvent.click(screen.getByText('Crear')); });
    const ejemplo = screen.getByLabelText(/Copiar el ejemplo de/).previousElementSibling!.textContent!;
    expect(ejemplo).toContain('https://api/sessions') ;
    expect(ejemplo).not.toContain('/chat');
  });

  it('el buscador de Ajustes lo encuentra por «token»', async () => {
    vi.spyOn(morganAPI, 'getSettings').mockResolvedValue({
      success: true, settings: { nombre: '', ocupacion: '', sobre_mi: '', idioma: '', estilo_respuesta: '' },
    } as never);
    vi.spyOn(morganAPI, 'health').mockResolvedValue({ status: 'ok', version: '2.0.41-dev', timestamp: '' });
    vi.spyOn(morganAPI, 'tokens').mockResolvedValue({ success: true, tokens: [] });

    render(
      <SettingsView
        apiOnline tema="oscuro" onTema={vi.fn()} cuenta={CON_CUENTA} entorno="cloud"
        onCerrar={vi.fn()} onIrA={vi.fn()} onChatTemporal={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Buscar ajustes'), { target: { value: 'token' } });

    await waitFor(() => expect(screen.getByText('Crear un token')).toBeTruthy());
    expect(screen.getAllByText('Acceso por API').length).toBeGreaterThan(0);
  });
});
