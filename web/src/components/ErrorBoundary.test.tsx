/**
 * Cuando una vista falla al pintarse (4.23): qué pasó y qué hacer, en palabras; el detalle
 * técnico, plegado. Antes «Cannot read properties of undefined» salía a la vista.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from './ErrorBoundary';

function Rota(): never {
  throw new Error("Cannot read properties of undefined (reading 'map')");
}

describe('ErrorBoundary', () => {
  it('dice qué hacer y pliega el detalle técnico', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ErrorBoundary area="Memoria"><Rota /></ErrorBoundary>);
    expect(screen.getByText(/Algo falló al mostrar Memoria/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeTruthy();
    const detalle = screen.getByText(/Cannot read properties/);
    const plegado = detalle.closest('details');
    expect(plegado).not.toBeNull();
    expect(plegado?.open).toBe(false);
    expect(screen.getByText('Detalles técnicos')).toBeTruthy();
  });
});
