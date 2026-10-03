/**
 * Los enlaces en las respuestas de Morgan (3.1-E).
 *
 * El chat **no** dibuja enlaces: el texto lo escribe el modelo, que puede haber leído
 * una página o un archivo con instrucciones escondidas. La única excepción es la
 * descarga de una copia del PC, que es una ruta propia de este servidor.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Markdown } from './Markdown';

describe('Enlaces en una respuesta', () => {
  it('la descarga de una copia del PC es un botón', () => {
    render(<Markdown text="Aquí lo tienes: [contrato.pdf](/api/uploads/up-1/contenido)" />);

    const enlace = screen.getByText('contrato.pdf');
    expect(enlace.tagName).toBe('A');
    expect(enlace.getAttribute('href')).toBe('/api/uploads/up-1/contenido');
    expect(enlace.hasAttribute('download')).toBe(true);
  });

  it('cualquier otra dirección se queda en texto', () => {
    const { container } = render(
      <Markdown text="Mira [aquí](https://sitio-falso.example/entra) para continuar" />);

    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).toContain('aquí (https://sitio-falso.example/entra)');
  });

  it('ni una descarga inventada de otro sitio', () => {
    const { container } = render(
      <Markdown text="[archivo](https://otro.example/api/uploads/x/contenido)" />);

    expect(container.querySelector('a')).toBeNull();
  });

  it('el resto del formato sigue igual', () => {
    const { container } = render(<Markdown text="Esto es **importante** y `codigo`" />);

    expect(container.querySelector('strong')?.textContent).toBe('importante');
    expect(container.querySelector('code')?.textContent).toBe('codigo');
  });
});

describe('La dirección completa de esta misma web', () => {
  it('también es un botón', () => {
    render(<Markdown text={`[bitacora.pdf](${window.location.origin}/api/uploads/up-9/contenido)`} />);

    const enlace = screen.getByText('bitacora.pdf');
    expect(enlace.tagName).toBe('A');
    expect(enlace.hasAttribute('download')).toBe(true);
  });
});
