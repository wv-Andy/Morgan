/**
 * Aprobar un plan es la orden (4.0, decisión mía).
 *
 * Antes, tras aprobar un plan había que escribirle a Morgan «adelante»: visto en la
 * prueba real de la 3.9. Ahora el panel avisa al aprobar y el chat manda solo el turno
 * que lo ejecuta, con `ejecutar_plan`; los pasos los hace Morgan con lo aprobado.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { morganAPI, type PlanItem } from '../lib/api';
import { ChatView } from './Chat';
import { PlanesPendientes } from './Planes';

const PLAN: PlanItem = {
  id: 'plan-1', objetivo: 'cambiar mis notas', estado: 'pendiente', riesgo: 'moderate',
  necesita_aprobacion: true, ejecutable: false, session_id: 's1', task_id: null,
  creado_en: 0, decidido_en: null, decidido_por: null, motivo_rechazo: null,
  pasos: [{ orden: 1, descripcion: 'crear las notas', herramienta: 'create_file',
            argumentos: { path: 'C:/notas.txt' }, riesgo: 'moderate', depende_de: [], motivo: null,
            necesita_confirmacion: true }],
} as unknown as PlanItem;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(morganAPI, 'sessionMessages').mockResolvedValue({ success: true, messages: [] } as never);
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('Aprobar un plan', () => {
  it('el panel avisa con el plan aprobado', async () => {
    vi.spyOn(morganAPI, 'planes').mockResolvedValue({ success: true, count: 1, planes: [PLAN] });
    const aprobar = vi.spyOn(morganAPI, 'aprobarPlan').mockResolvedValue({} as never);
    const onAprobado = vi.fn();
    await act(async () => { render(<PlanesPendientes apiOnline sessionId="s1" onAprobado={onAprobado} />); });
    await act(async () => { fireEvent.click(await screen.findByText('Aprobar y ejecutar')); });
    expect(aprobar).toHaveBeenCalledWith('plan-1');
    expect(onAprobado).toHaveBeenCalledWith(expect.objectContaining({ id: 'plan-1' }));
  });

  it('rechazar no manda nada', async () => {
    vi.spyOn(morganAPI, 'planes').mockResolvedValue({ success: true, count: 1, planes: [PLAN] });
    vi.spyOn(morganAPI, 'rechazarPlan').mockResolvedValue({} as never);
    const onAprobado = vi.fn();
    await act(async () => { render(<PlanesPendientes apiOnline sessionId="s1" onAprobado={onAprobado} />); });
    await act(async () => { fireEvent.click(await screen.findByText('Rechazar')); });
    expect(onAprobado).not.toHaveBeenCalled();
  });

  it('el chat manda solo el turno que lo ejecuta', async () => {
    const turno = vi.spyOn(morganAPI, 'chatStream').mockResolvedValue(
      { success: true, response: 'Listo.', model: 'x', elapsed_seconds: 1 } as never);
    const onPlanEnviado = vi.fn();
    await act(async () => {
      render(
        <ChatView estadoApi="online" sessionId="s1" sistema={{ herramientas: 1, entorno: 'cloud' }}
                  onConversationSaved={vi.fn()} planAprobado={{ id: 'plan-1', objetivo: 'cambiar mis notas' }}
                  onPlanEnviado={onPlanEnviado} />,
      );
    });
    expect(onPlanEnviado).toHaveBeenCalled();
    expect(turno).toHaveBeenCalledTimes(1);
    const args = turno.mock.calls[0];
    expect(args[0]).toBe('✅ Plan aprobado: cambiar mis notas');
    expect(args[6]).toBe('plan-1');
  });

  it('sin plan aprobado, el chat no manda nada', async () => {
    const turno = vi.spyOn(morganAPI, 'chatStream');
    await act(async () => {
      render(<ChatView estadoApi="online" sessionId="s1" sistema={{ herramientas: 1, entorno: 'cloud' }}
                       onConversationSaved={vi.fn()} />);
    });
    expect(turno).not.toHaveBeenCalled();
  });
});
