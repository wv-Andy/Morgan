/* Morgan Web — Quién eres, arriba a la derecha */

import { useEffect, useRef, useState } from 'react';
import type { EstadoCuenta } from './Cuenta';

/**
 * El control de cuenta de la esquina superior derecha.
 *
 * Antes no había ninguno: para saber con qué cuenta estabas dentro había que
 * abrir Ajustes y bajar hasta el panel, y para salir, lo mismo. En un Morgan de
 * una sola persona eso pasaba desapercibido; en cuanto lo usa más gente —o la
 * misma en dos navegadores— saber a nombre de quién estás escribiendo deja de
 * ser un detalle.
 *
 * **Va en posición absoluta, como el botón del menú lateral.** Es deliberado:
 * así no se mete en el flujo de ninguna vista y no hay que tocar el encabezado
 * de las siete que ya existen.
 */

interface Props {
  cuenta: EstadoCuenta;
  /** Llevar a Ajustes, donde está el panel completo de la cuenta. */
  alAbrirAjustes: () => void;
  /** Abrir la pantalla de acceso en uno u otro modo (solo sin sesión). */
  alPedirAcceso: (modo: 'entrar' | 'crear') => void;
}

function inicial(nombre: string): string {
  return (nombre.trim()[0] ?? '?').toUpperCase();
}

export function BarraCuenta({ cuenta, alAbrirAjustes, alPedirAcceso }: Props) {
  const [abierto, setAbierto] = useState(false);
  const contenedor = useRef<HTMLDivElement>(null);

  // Un menú que no se cierra al pulsar fuera se queda abierto tapando la
  // interfaz, y es de las cosas que más rápido se leen como "esto está roto".
  useEffect(() => {
    if (!abierto) return;

    function fuera(evento: MouseEvent) {
      if (!contenedor.current?.contains(evento.target as Node)) setAbierto(false);
    }
    function escape(evento: KeyboardEvent) {
      if (evento.key === 'Escape') setAbierto(false);
    }

    document.addEventListener('mousedown', fuera);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', fuera);
      document.removeEventListener('keydown', escape);
    };
  }, [abierto]);

  // Sin sesión: las dos puertas de entrada. Esto solo se ve en el Morgan de tu
  // equipo, que no exige cuenta —en la nube no se llega hasta aquí sin entrar—,
  // y es lo que permite crearse una cuenta desde un Morgan local sin tener que
  // saber que existe una URL para ello.
  if (!cuenta.autenticado) {
    return (
      <div className="barra-cuenta">
        <button
          type="button"
          className="barra-cuenta__enlace"
          onClick={() => alPedirAcceso('entrar')}
        >
          Iniciar sesión
        </button>
        <button
          type="button"
          className="barra-cuenta__principal"
          onClick={() => alPedirAcceso('crear')}
        >
          Registrarse
        </button>
      </div>
    );
  }

  const usuario = cuenta.usuario;
  const nombre = usuario?.display_name || usuario?.email || 'Mi cuenta';

  return (
    <div className="barra-cuenta" ref={contenedor}>
      <button
        type="button"
        className="barra-cuenta__chip"
        onClick={() => setAbierto(a => !a)}
        aria-haspopup="menu"
        aria-expanded={abierto}
        aria-label={`Cuenta de ${nombre}`}
      >
        {usuario?.avatar_url ? (
          <img className="barra-cuenta__avatar" src={usuario.avatar_url} alt="" />
        ) : (
          <span className="barra-cuenta__avatar" aria-hidden="true">{inicial(nombre)}</span>
        )}
        <span className="barra-cuenta__nombre">{nombre}</span>
      </button>

      {abierto && (
        <div className="barra-cuenta__menu" role="menu">
          <div className="barra-cuenta__quien">
            <strong>{nombre}</strong>
            {usuario?.email && <span>{usuario.email}</span>}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => { setAbierto(false); alAbrirAjustes(); }}
          >
            Ajustes de la cuenta
          </button>
          <button
            type="button"
            role="menuitem"
            className="danger"
            onClick={() => { setAbierto(false); void cuenta.salir(); }}
          >
            Cerrar sesión
          </button>
        </div>
      )}
    </div>
  );
}
