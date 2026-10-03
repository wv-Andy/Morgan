/* Morgan Web — Tu cuenta, dentro de Ajustes (identidad, V2.0 adelantada) */

import { useEffect, useState } from 'react';
import { MorganAPIError, morganAPI, type SesionActiva } from '../lib/api';
import type { EstadoCuenta } from './Cuenta';

function cuando(marca: number | null): string {
  if (!marca) return 'nunca';
  return new Date(marca * 1000).toLocaleString('es', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/** Del `user-agent` entero, lo único que le dice algo a una persona. */
function navegador(agente: string | null): string {
  if (!agente) return 'Dispositivo desconocido';
  const cual = ['Firefox', 'Edg', 'Chrome', 'Safari'].find((n) => agente.includes(n));
  const donde = ['Windows', 'Android', 'iPhone', 'Mac', 'Linux'].find((n) => agente.includes(n));
  const nombre = cual === 'Edg' ? 'Edge' : cual;
  return [nombre, donde].filter(Boolean).join(' · ') || 'Dispositivo desconocido';
}

/**
 * Cómo se nombra el rol, cuando merece nombrarse.
 *
 * A un usuario normal no se le dice que es «usuario»: no le aporta nada y sugiere
 * que hay una escala en la que está abajo. Solo se menciona lo que da capacidades.
 */
function etiquetaDeRol(rol: string | undefined): string {
  if (rol === 'owner') return ', propietario de este Morgan';
  if (rol === 'admin') return ', con permisos de administración';
  return '';
}

/**
 * La cuenta dentro de Ajustes. Desde la V2.0.29 se enseña en dos secciones del
 * panel: `cuenta` (quién eres, contraseña, sesiones) y `datos` (descargar y
 * eliminar), como en el diseño de referencia.
 */
export function PanelCuenta({ cuenta, parte = 'cuenta' }: { cuenta: EstadoCuenta; parte?: 'cuenta' | 'datos' }) {
  const [actual, setActual] = useState('');
  const [nueva, setNueva] = useState('');
  const [error, setError] = useState('');
  const [aviso, setAviso] = useState('');
  const [guardando, setGuardando] = useState(false);
  const [sesiones, setSesiones] = useState<SesionActiva[]>([]);

  const conCuenta = cuenta.autenticado && !cuenta.local;

  useEffect(() => {
    if (!conCuenta || parte !== 'cuenta') return;
    morganAPI.sesiones()
      .then((r) => setSesiones(r.sesiones))
      .catch(() => setSesiones([]));
  }, [conCuenta, parte]);

  if (parte === 'datos') {
    // Sin cuenta no hay datos «tuyos» que separar: en tu equipo todo es tuyo.
    if (!conCuenta) return null;
    return (
      <>
        <DescargarDatos />
        <BorrarCuenta esPropietario={cuenta.usuario?.rol === 'owner'} />
      </>
    );
  }

  // En tu propio equipo no hay cuenta que gestionar, y decirlo es mas util que
  // ensenar un formulario que no haria nada.
  if (!conCuenta) {
    return (
      <section className="ajustes-grupo">
        <h3>Cuenta</h3>
        <p className="ajustes-nota">
          Este Morgan corre en tu equipo con tus claves, así que no pide cuenta:
          no habría de quién separar tus datos. Las cuentas son para el Morgan de
          la web, donde entra más gente.
        </p>
      </section>
    );
  }

  async function cambiar(evento: React.FormEvent) {
    evento.preventDefault();
    setError('');
    setAviso('');
    setGuardando(true);

    try {
      const res = await morganAPI.cambiarPassword(actual, nueva);
      setActual('');
      setNueva('');
      setAviso(
        res.sesiones_cerradas > 0
          ? `Contraseña cambiada. Se han cerrado ${res.sesiones_cerradas} sesión(es) en otros dispositivos.`
          : 'Contraseña cambiada.',
      );
      setSesiones(await morganAPI.sesiones().then((r) => r.sesiones));
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo cambiar la contraseña.');
    } finally {
      setGuardando(false);
    }
  }

  async function cerrarOtras() {
    setError('');
    setAviso('');
    try {
      const res = await morganAPI.cerrarOtrasSesiones();
      setAviso(
        res.cerradas > 0
          ? `Cerradas ${res.cerradas} sesión(es). Esta sigue abierta.`
          : 'No había ninguna otra sesión abierta.',
      );
      setSesiones(await morganAPI.sesiones().then((r) => r.sesiones));
    } catch {
      setError('No se pudieron cerrar las otras sesiones.');
    }
  }

  return (
    <>
      <section className="ajustes-grupo">
        <h3>Cuenta</h3>
        <p className="ajustes-nota">
          Estás dentro como <strong>{cuenta.usuario?.display_name}</strong>
          {cuenta.usuario?.email ? ` (${cuenta.usuario.email})` : ''}
          {etiquetaDeRol(cuenta.usuario?.rol)}. Tus conversaciones, tu memoria y
          tus archivos son solo tuyos: nadie más los ve.
        </p>
        <button type="button" className="ajustes-boton" onClick={() => void cuenta.salir()}>
          Cerrar sesión
        </button>
      </section>

      <section className="ajustes-grupo">
        <h3>Cambiar la contraseña</h3>
        <p className="ajustes-nota">
          Al cambiarla se cierran las sesiones de los demás dispositivos. Es a
          propósito: si alguien te la había robado, cambiarla no serviría de nada
          mientras su sesión siguiera abierta.
        </p>

        <form onSubmit={cambiar} className="ajustes-form">
          <label className="ajustes-campo">
            <span>Contraseña actual</span>
            <input
              type="password"
              value={actual}
              onChange={(e) => setActual(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <label className="ajustes-campo">
            <span>Contraseña nueva</span>
            <input
              type="password"
              value={nueva}
              onChange={(e) => setNueva(e.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          <button type="submit" className="ajustes-boton" disabled={guardando}>
            {guardando ? 'Guardando…' : 'Cambiar contraseña'}
          </button>
        </form>

        {error && <div className="error-banner">{error}</div>}
        {aviso && <div className="ajustes-aviso">{aviso}</div>}
      </section>

      <section className="ajustes-grupo">
        <h3>Dónde tienes la sesión abierta</h3>
        <p className="ajustes-nota">
          Si ves algo que no reconoces, cierra las demás y cambia la contraseña.
        </p>

        <ul className="ajustes-sesiones">
          {sesiones.map((s, i) => (
            <li key={`${s.creado_en}-${i}`}>
              <span>{navegador(s.user_agent)}</span>
              <small>Último uso: {cuando(s.ultimo_uso)}</small>
            </li>
          ))}
          {sesiones.length === 0 && <li><small>No se pudieron leer las sesiones.</small></li>}
        </ul>

        {sesiones.length > 1 && (
          <button type="button" className="ajustes-boton" onClick={() => void cerrarOtras()}>
            Cerrar las demás sesiones
          </button>
        )}
      </section>
    </>
  );
}

/**
 * Descargar todo lo que Morgan guarda de ti.
 *
 * Va **antes** de eliminar la cuenta, y el orden importa: quien llega a esta
 * parte de los ajustes pensando en irse debería tropezarse primero con la
 * opción de llevarse sus cosas. Al revés, la exportación la encontraría solo
 * quien ya decidió quedarse.
 */
function DescargarDatos() {
  const [estado, setEstado] = useState<'listo' | 'preparando' | 'fallo'>('listo');
  const [error, setError] = useState('');

  async function descargar() {
    setEstado('preparando');
    setError('');
    try {
      const { datos } = await morganAPI.exportarDatos();

      // Se construye el fichero en el navegador y se descarga sin pasar por el
      // servidor otra vez. El JSON con sangría porque esto lo va a abrir una
      // persona tanto como un programa.
      const blob = new Blob([JSON.stringify(datos, null, 2)], {
        type: 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const enlace = document.createElement('a');
      enlace.href = url;
      enlace.download = `morgan-mis-datos-${new Date().toISOString().slice(0, 10)}.json`;
      enlace.click();
      // Sin esto el navegador se queda el blob en memoria hasta recargar.
      URL.revokeObjectURL(url);

      setEstado('listo');
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo preparar la descarga.');
      setEstado('fallo');
    }
  }

  return (
    <section className="ajustes-grupo">
      <h3>Descargar tus datos</h3>
      <p className="ajustes-nota">
        Un fichero con tus conversaciones, recuerdos, tareas y preferencias.
        No incluye contraseñas ni las llaves de los servicios que hayas
        conectado: eso no debe acabar en una carpeta de descargas.
      </p>

      <button
        type="button"
        className="ajustes-boton"
        onClick={() => void descargar()}
        disabled={estado === 'preparando'}
      >
        {estado === 'preparando' ? 'Preparando…' : 'Descargar mis datos'}
      </button>

      {error && <div className="error-banner">{error}</div>}
    </section>
  );
}

/**
 * Eliminar la cuenta.
 *
 * Va en su propia sección y **cerrada por defecto**. No es timidez de diseño:
 * un botón de borrado permanentemente visible junto a «cambiar contraseña»
 * acaba pulsado por error, y esto no tiene deshacer.
 *
 * Se pide la contraseña —no un «¿estás seguro?»— porque una sesión abierta en
 * un equipo prestado basta para que otra persona lo borre todo. La comprobación
 * de verdad la hace el backend; esto solo evita el clic accidental.
 */
function BorrarCuenta({ esPropietario }: { esPropietario: boolean }) {
  const [abierto, setAbierto] = useState(false);
  const [password, setPassword] = useState('');
  const [borrando, setBorrando] = useState(false);
  const [error, setError] = useState('');

  // El propietario no puede borrarse: dejaría este Morgan sin nadie que lo
  // administre. Se dice aquí en lugar de enseñar un botón que siempre falla.
  if (esPropietario) {
    return (
      <section className="ajustes-grupo">
        <h3>Eliminar la cuenta</h3>
        <p className="ajustes-nota">
          La cuenta del propietario no se puede eliminar: dejaría este Morgan sin
          nadie que administre roles ni cuentas.
        </p>
      </section>
    );
  }

  async function borrar(evento: React.FormEvent) {
    evento.preventDefault();
    setError('');
    setBorrando(true);
    try {
      await morganAPI.eliminarCuenta(password);
      // Recarga entera: es la forma segura de tirar todo el estado en memoria
      // de quien acaba de irse. Limpiarlo a mano deja restos.
      window.location.reload();
    } catch (err) {
      setError(err instanceof MorganAPIError ? err.message : 'No se pudo eliminar la cuenta.');
      setBorrando(false);
    }
    // Sin `finally` a proposito, y es el unico sitio del proyecto donde eso es
    // correcto: cuando sale bien, la pagina se recarga entera y este componente
    // deja de existir. Apagar ahi la bandera seria escribir en un componente
    // desmontado. Solo el camino de error la apaga, porque solo ahi se sigue
    // viendo el formulario.
  }

  return (
    <section className="ajustes-grupo">
      <h3>Eliminar la cuenta</h3>
      <p className="ajustes-nota">
        Se borran tus conversaciones, tus recuerdos, tus archivos y tus tareas.
        <strong> No se puede deshacer.</strong>
      </p>

      {!abierto ? (
        <button type="button" className="ajustes-boton peligro" onClick={() => setAbierto(true)}>
          Quiero eliminar mi cuenta
        </button>
      ) : (
        <form onSubmit={borrar} className="ajustes-form">
          <label className="ajustes-campo">
            <span>Escribe tu contraseña para confirmar</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              autoFocus
              required
            />
          </label>
          <div className="ajustes-acciones">
            <button
              type="button"
              className="ajustes-boton"
              onClick={() => { setAbierto(false); setPassword(''); setError(''); }}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="ajustes-boton peligro"
              disabled={borrando || !password}
            >
              {borrando ? 'Eliminando…' : 'Eliminar definitivamente'}
            </button>
          </div>
          {error && <div className="error-banner">{error}</div>}
        </form>
      )}
    </section>
  );
}
