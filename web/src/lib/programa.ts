/* Morgan Web — hablar con Morgan para Windows, cuando la web corre dentro (5.3)
 *
 * Desde la 5.3 el programa es una sola ventana que carga esta web, y lo suyo (el estado del PC,
 * pausar, lo último que hizo, la versión) está en Ajustes → Este PC. La web le pide cosas por
 * el puente de Tauri (`window.__TAURI_INTERNALS__.invoke`), pero **solo las inofensivas**: el
 * programa solo le deja esas (escritorio/src-tauri/capabilities/web.json). Emparejar no: eso se
 * hace en una pantalla del propio programa.
 */

/** El estado del PC como lo pinta la bandeja. `clave`: Conectado, EnPausa, SinEmparejar… */
export type ResumenDelPC = {
  clave: string;
  texto: string;
  /** Si el botón dice «Pausar» (si no, «Reanudar»), y si se puede pulsar. */
  pausar: boolean;
  se_puede: boolean;
};

export type VersionDelPrograma = { version: string; nueva: string | null };

/** Lo que pidió Morgan a este PC en las últimas 24 horas (el diario del agente). */
export type AccionEnElPC = { cuando: number; que: string; detalle?: string; estado: string };

type Puente = { invoke: (orden: string, args?: Record<string, unknown>) => Promise<unknown> };
type ConPrograma = { __MORGAN_PROGRAMA__?: string; __TAURI_INTERNALS__?: Puente; __MORGAN_IR__?: string };

/** Si esta web corre dentro de Morgan para Windows (5.2): el programa lo dice al cargarla. */
export function dentroDelPrograma(w: ConPrograma = window as never): boolean {
  return typeof w.__MORGAN_PROGRAMA__ === 'string';
}

function puente(w: ConPrograma): Puente {
  const p = w.__TAURI_INTERNALS__;
  if (!dentroDelPrograma(w) || typeof p?.invoke !== 'function') {
    throw new Error('Esto solo funciona dentro de Morgan para Windows.');
  }
  return p;
}

/** Las órdenes que la web puede pedir al programa. Ninguna más. */
export function programa(w: ConPrograma = window as never) {
  const pedir = async (orden: string) => puente(w).invoke(orden);
  return {
    resumen: () => pedir('resumen') as Promise<ResumenDelPC>,
    pausar: () => pedir('pausar') as Promise<string>,
    reanudar: () => pedir('reanudar') as Promise<string>,
    ultimas: async (): Promise<AccionEnElPC[]> => {
      try {
        const lista = JSON.parse(String(await pedir('ultimas')));
        return Array.isArray(lista) ? lista : [];
      } catch {
        return [];
      }
    },
    /** «Qué puede hacer y qué carpetas ve»: la ventana de ajustes del agente. */
    abrirAjustes: () => pedir('abrir_ajustes') as Promise<void>,
    /** Lleva la ventana a la pantalla del programa para emparejar. */
    emparejar: () => pedir('pantalla_emparejar') as Promise<void>,
    version: () => pedir('programa') as Promise<VersionDelPrograma>,
    actualizar: () => pedir('actualizar_ahora') as Promise<void>,
  };
}

/** El aviso del programa para abrir una parte de la web (`{ ir: 'este-pc' }`), desde la bandeja. */
export const EVENTO_DEL_PROGRAMA = 'morgan-programa';

/**
 * A qué parte llevar, si el programa lo pidió antes de que la web escuchara (al abrir la
 * ventana desde «Lo último que hizo en este PC»). Se lee una vez.
 */
export function pendienteDelPrograma(w: ConPrograma = window as never): string | null {
  const ir = w.__MORGAN_IR__;
  delete w.__MORGAN_IR__;
  return typeof ir === 'string' ? ir : null;
}
