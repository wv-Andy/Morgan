/* Morgan Web — Cuentas: acceso, registro y recuperación (identidad, V2.0 adelantada) */

import { useCallback, useEffect, useState } from 'react';
import {
  API_ES_REMOTA,
  EVENTO_SIN_SESION,
  MorganAPIError,
  morganAPI,
  type QuienSoyResponse,
  type UsuarioMorgan,
} from '../lib/api';
import { LogoMorgan } from './Logo';

/**
 * Quién está usando Morgan.
 *
 * Los tres estados que importan son distintos y conviene no confundirlos:
 *
 * - `cargando`: todavía no se sabe. Enseñar la pantalla de acceso aquí haría
 *   parpadear el formulario a quien ya tiene sesión, en cada recarga.
 * - `local`: Morgan corre en tu equipo y no pide cuenta a nadie.
 * - autenticado o no: solo tiene sentido cuando el despliegue exige cuenta.
 */
export interface EstadoCuenta {
  cargando: boolean;
  autenticado: boolean;
  local: boolean;
  usuario: UsuarioMorgan | null;
  refrescar: () => Promise<void>;
  salir: () => Promise<void>;
}

// Este fichero exporta el hook ademas de la pantalla: los dos son la misma
// pieza —quien eres y como entras— y separarlos por una regla de recarga en
// caliente dejaria dos ficheros que solo se entienden juntos.
// oxlint-disable-next-line react/only-export-components
export function useCuenta(): EstadoCuenta {
  const [estado, setEstado] = useState<QuienSoyResponse | null>(null);
  const [cargando, setCargando] = useState(true);

  const refrescar = useCallback(async () => {
    // UN SOLO `finally`, y envolviéndolo todo.
    //
    // La versión anterior tenía dos bloques seguidos y un `return` en el
    // camino de éxito del primero. Ese `return` saltaba por encima del
    // `finally`, que pertenecía al SEGUNDO bloque, así que `cargando` se
    // quedaba en `true` para siempre y la aplicación no pasaba nunca de la
    // pantalla de arranque.
    //
    // Lo cruel del fallo: solo ocurría cuando `/auth/yo` FUNCIONABA. Todos los
    // caminos de error apagaban la bandera correctamente, así que probar con el
    // backend caído no lo reproducía. Y las comprobaciones contra producción
    // hablaban con la API directamente, sin pasar por React.
    try {
      try {
        setEstado(await morganAPI.yo());
      } catch {
        // Un fallo aquí suele ser el servicio despertando, no una avería. Se
        // reintenta una vez antes de decidir nada.
        setEstado(await morganAPI.yo());
      }
    } catch {
      // NO se asume `local: true`.
      //
      // Era lo que hacía antes, y producía el estado más confuso posible: con
      // `local` puesto, App.tsx da por hecho que este Morgan no pide cuentas y
      // enseña la aplicación entera. Pero el backend de la nube SÍ las pide, así
      // que cada petición respondía «Necesitas iniciar sesión para usar Morgan».
      // Parecía que Morgan hubiera olvidado quién eras estando dentro.
      //
      // Si la API vive en otro sitio, este despliegue exige cuenta: lo honesto
      // es enseñar el acceso. Solo cuando la API es la de tu propio equipo tiene
      // sentido seguir sin ella.
      setEstado({
        success: false,
        autenticado: false,
        local: !API_ES_REMOTA,
        usuario: null,
      });
    } finally {
      // Pase lo que pase. Es lo único que saca a la interfaz de la pantalla de
      // arranque, y no puede depender de por qué camino se llegó hasta aquí.
      setCargando(false);
    }
  }, []);

  // Preguntar al backend quien eres es justo lo que un efecto debe hacer:
  // sincronizar con un sistema externo. El aviso del linter da por hecho que
  // el setState es sincrono, y aqui llega despues de una peticion de red.
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void refrescar();
  }, [refrescar]);

  // Si en cualquier momento el backend dice que la sesion ya no vale, se vuelve
  // a preguntar quien eres. El resultado sera «no autenticado», y App.tsx
  // ensenara el acceso: una salida clara en lugar de una aplicacion abierta
  // donde ya nada funciona.
  useEffect(() => {
    const alPerderla = () => { void refrescar(); };

    window.addEventListener(EVENTO_SIN_SESION, alPerderla);
    return () => window.removeEventListener(EVENTO_SIN_SESION, alPerderla);
  }, [refrescar]);

  const salir = useCallback(async () => {
    try {
      await morganAPI.logout();
    } finally {
      // Se recarga entera a proposito: es la forma segura de tirar todo el
      // estado en memoria —conversaciones, archivos, ajustes— de quien acaba de
      // salir. Limpiarlo a mano deja restos, y los restos aqui son datos de otra
      // persona en la pantalla de la siguiente.
      window.location.reload();
    }
  }, []);

  return {
    cargando,
    autenticado: estado?.autenticado ?? false,
    local: estado?.local ?? true,
    usuario: estado?.usuario ?? null,
    refrescar,
    salir,
  };
}

type Modo = 'entrar' | 'crear' | 'recuperar' | 'restablecer';

/** Qué guarda Morgan, con quién lo comparte y las condiciones (4.20). */
export const PRIVACIDAD = 'https://github.com/wv-Andy/Morgan/blob/main/docs/privacidad.es.md';

/** Lee el token del enlace de recuperación, si se llegó desde un correo. */
function tokenDeLaUrl(): string {
  try {
    return new URLSearchParams(window.location.search).get('token') ?? '';
  } catch {
    return '';
  }
}

/**
 * Si esta visita viene del enlace de un correo de recuperación.
 *
 * Se consulta antes que nada: restablecer la contraseña no necesita saber quién
 * eres, y hacerlo depender de preguntárselo al backend dejaba el enlace inservible
 * cuando esa consulta tardaba o fallaba.
 */
// oxlint-disable-next-line react/only-export-components
export function hayTokenDeRecuperacion(): boolean {
  return tokenDeLaUrl() !== '';
}

/**
 * Lo que se ve mientras se averigua quién eres: la espiral girando en el centro.
 *
 * Nada más, por decisión de diseño (V2.0.28). Es idéntica a la que pinta
 * `index.html` mientras se descarga la aplicación, así que de una a otra no hay
 * salto: la espiral empieza a girar al abrir la página y deja de hacerlo cuando
 * Morgan está listo.
 *
 * Que el giro siga es lo que dice «pasa algo» con el backend dormido, que puede
 * tardar hasta un minuto en despertar; antes esto era un `div` vacío,
 * indistinguible de una web rota. Para los lectores de pantalla va el texto en
 * `aria-label`.
 */
export function PantallaCargando() {
  return (
    <div className="carga" role="status" aria-label="Cargando Morgan">
      <LogoMorgan size={56} className="carga__logo" />
    </div>
  );
}

function mensajeDeError(error: unknown): string {
  if (error instanceof MorganAPIError) return error.message;
  return 'No se pudo completar la operación. Inténtalo de nuevo.';
}

interface Props {
  alEntrar: () => void;
  /**
   * Con cuál de los dos formularios abrir. Lo usan los botones de la esquina
   * superior derecha: pulsar «Registrarse» y aterrizar en «Entrar» obliga a
   * buscar el enlace correcto, que es justo lo que el botón evitaba.
   */
  modoInicial?: 'entrar' | 'crear';
  /**
   * Volver sin entrar. Solo tiene sentido en el Morgan local, donde se llega
   * aquí por elección y no porque haga falta una cuenta; en la nube no hay
   * ningún sitio al que volver.
   */
  alCancelar?: () => void;
  /**
   * Un aviso que viene de fuera, por ejemplo el resultado de confirmar el
   * correo desde un enlace. Se enseña aquí porque quien abre ese enlace sin
   * sesión aterriza en esta pantalla, y sin esto no vería nada: parecería que
   * el enlace no hizo nada.
   */
  avisoExterno?: { texto: string; error: boolean } | null;
}

export function PantallaAcceso({ alEntrar, modoInicial, alCancelar, avisoExterno }: Props) {
  const tokenInicial = tokenDeLaUrl();
  const [modo, setModo] = useState<Modo>(
    tokenInicial ? 'restablecer' : (modoInicial ?? 'entrar'),
  );

  const [identificador, setIdentificador] = useState('');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [token] = useState(tokenInicial);

  const [error, setError] = useState('');
  const [aviso, setAviso] = useState('');
  const [enviando, setEnviando] = useState(false);

  function cambiarA(nuevo: Modo) {
    // Salir del modo restablecer significa abandonar el enlace del correo: hay
    // que quitarlo de la URL o la puerta de entrada seguiria viendolo y
    // devolveria aqui en cuanto se recargue.
    if (modo === 'restablecer' && nuevo !== 'restablecer') {
      window.history.replaceState({}, '', '/');
    }
    setModo(nuevo);
    setError('');
    setAviso('');
  }

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setError('');
    setAviso('');
    setEnviando(true);

    try {
      if (modo === 'entrar') {
        await morganAPI.login(identificador, password);
        alEntrar();
      } else if (modo === 'crear') {
        await morganAPI.registro({ username, email, password });
        alEntrar();
      } else if (modo === 'recuperar') {
        const res = await morganAPI.recuperar(email);
        // El mensaje viene del backend y es deliberadamente vago: decir "esa
        // cuenta no existe" convertiria este formulario en una forma de
        // averiguar quien esta registrado.
        setAviso(res.message);
      } else {
        await morganAPI.restablecer(token, password);
        // El token sale de la barra de direcciones ANTES de cambiar de pantalla:
        // ya no vale, dejarlo ahi solo sirve para que acabe en un historial o en
        // un enlace copiado, y ademas la puerta de entrada lo mira para decidir
        // que ensenar — con el puesto, se quedaria en este mismo formulario.
        window.history.replaceState({}, '', '/');
        setAviso('Contraseña cambiada. Ya puedes entrar con la nueva.');
        setPassword('');
        setIdentificador('');
        setModo('entrar');
      }
    } catch (err) {
      setError(mensajeDeError(err));
    } finally {
      setEnviando(false);
    }
  }

  const titulos: Record<Modo, string> = {
    entrar: 'Entrar en Morgan',
    crear: 'Crear tu cuenta',
    recuperar: 'Recuperar el acceso',
    restablecer: 'Elige una contraseña nueva',
  };

  const acciones: Record<Modo, string> = {
    entrar: 'Entrar',
    crear: 'Crear cuenta',
    recuperar: 'Enviarme el enlace',
    restablecer: 'Guardar la contraseña',
  };

  return (
    // Con `alCancelar` esto se abre ENCIMA de Morgan, no en su lugar: quien
    // pulsa «Registrarse» desde dentro no ha perdido nada, y desmontar la
    // aplicacion le tiraria la conversacion a medias.
    <div className={`acceso ${alCancelar ? 'acceso--superpuesto' : ''}`}>
      {/* Arriba a la derecha, donde se buscan: las dos puertas de entrada
          visibles a la vez, sin tener que leer hasta el final del formulario
          para descubrir que la otra existe. */}
      {modo !== 'restablecer' && (
        <div className="acceso__barra">
          <button
            type="button"
            className={`acceso__barra-boton ${modo === 'entrar' ? 'activo' : ''}`}
            onClick={() => cambiarA('entrar')}
          >
            Iniciar sesión
          </button>
          <button
            type="button"
            className={`acceso__barra-boton principal ${modo === 'crear' ? 'activo' : ''}`}
            onClick={() => cambiarA('crear')}
          >
            Registrarse
          </button>
          {alCancelar && (
            <button type="button" className="acceso__barra-boton" onClick={alCancelar}>
              Volver
            </button>
          )}
        </div>
      )}

      <div className="acceso__caja">
        <LogoMorgan size={56} className="acceso__logo" />
        <h1 className="acceso__marca">Morgan</h1>
        <h2 className="acceso__titulo">{titulos[modo]}</h2>

        {modo === 'recuperar' && (
          <p className="acceso__ayuda">
            Escribe tu correo y te llegará un enlace para elegir una contraseña
            nueva. Caduca en 30 minutos.
          </p>
        )}

        <form onSubmit={enviar} className="acceso__form">
          {modo === 'entrar' && (
            <label className="acceso__campo">
              <span>Usuario o correo</span>
              <input
                value={identificador}
                onChange={(e) => setIdentificador(e.target.value)}
                autoComplete="username"
                autoFocus
                required
              />
            </label>
          )}

          {modo === 'crear' && (
            <>
              <label className="acceso__campo">
                <span>Nombre de usuario</span>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  minLength={3}
                  maxLength={32}
                  autoFocus
                  required
                />
              </label>
              <label className="acceso__campo">
                <span>Correo electrónico</span>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                />
              </label>
            </>
          )}

          {modo === 'recuperar' && (
            <label className="acceso__campo">
              <span>Correo electrónico</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                autoFocus
                required
              />
            </label>
          )}

          {modo !== 'recuperar' && (
            <label className="acceso__campo">
              <span>Contraseña</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={modo === 'entrar' ? 'current-password' : 'new-password'}
                minLength={modo === 'entrar' ? undefined : 8}
                autoFocus={modo === 'restablecer'}
                required
              />
              {modo !== 'entrar' && (
                <small className="acceso__pista">
                  Mínimo 8 caracteres. Una frase que recuerdes es mejor que algo
                  corto y retorcido.
                </small>
              )}
            </label>
          )}

          {error && <p className="acceso__error" role="alert">{error}</p>}
          {(aviso || avisoExterno) && (
            <p
              className={avisoExterno?.error ? 'acceso__error' : 'acceso__aviso'}
              role="status"
            >
              {aviso || avisoExterno?.texto}
            </p>
          )}

          <button type="submit" className="acceso__boton" disabled={enviando}>
            {enviando ? 'Un momento…' : acciones[modo]}
          </button>
          {modo === 'crear' && (
            <p className="acceso__legal">
              Al crear la cuenta aceptas{' '}
              <a href={PRIVACIDAD} target="_blank" rel="noreferrer">
                las condiciones y la política de privacidad
              </a>
              .
            </p>
          )}
        </form>

        <div className="acceso__enlaces">
          {modo === 'entrar' && (
            <>
              <button type="button" onClick={() => cambiarA('crear')}>
                Crear una cuenta
              </button>
              <button type="button" onClick={() => cambiarA('recuperar')}>
                He olvidado mi contraseña
              </button>
            </>
          )}
          {modo !== 'entrar' && (
            <button type="button" onClick={() => cambiarA('entrar')}>
              Volver a entrar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
