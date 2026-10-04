/* Morgan Web — Tu equipo: emparejar el agente local, dentro de Ajustes (3.0-C) */

import { useEffect, useState } from 'react';
import { MorganAPIError, morganAPI, type AgenteLocal, type OrdenDeAgente } from '../lib/api';
import type { EstadoCuenta } from './Cuenta';
import { ParaCopiar } from './ParaCopiar';

/** Morgan para Windows (5.0): la última versión publicada del programa. */
export const DESCARGA_WINDOWS = 'https://github.com/wv-Andy/Morgan/releases/latest';

function fecha(marca: number | null): string {
  if (!marca) return 'nunca';
  return new Date(marca * 1000).toLocaleString('es', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function mensaje(err: unknown, porDefecto: string): string {
  return err instanceof MorganAPIError ? err.message : porDefecto;
}

/** Lo que queda de un código, como «9:41». Cero cuando ya no vale. */
function restante(caduca_en: number, ahora: number): string {
  const s = Math.max(0, Math.round(caduca_en - ahora));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * Emparejar un PC con la cuenta, y ver y revocar los emparejados.
 *
 * **El código se enseña aquí, pero la decisión se toma en el PC**: el agente enseña
 * con qué cuenta se va a emparejar y pregunta allí. Es lo que corta a quien genere
 * un código en su cuenta y convenza a otra persona de teclearlo
 * (docs/agente-local.md, amenaza H).
 */
export function PanelEquipos({ cuenta }: { cuenta: EstadoCuenta }) {
  const conCuenta = cuenta.autenticado && !cuenta.local;

  const [agentes, setAgentes] = useState<AgenteLocal[] | null>(null);
  const [codigo, setCodigo] = useState<{ codigo: string; caduca_en: number; instalar?: string } | null>(null);
  const [ultima, setUltima] = useState<string | null>(null);
  const [ahora, setAhora] = useState(() => Date.now() / 1000);
  const [pidiendo, setPidiendo] = useState(false);
  const [confirmar, setConfirmar] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [aviso, setAviso] = useState('');
  //: Los equipos que había al pedir el código: el que aparezca después es el nuevo (4.17).
  const [antes, setAntes] = useState<Set<string> | null>(null);

  function cargar(): Promise<void> {
    return morganAPI.agentes()
      .then((r) => { setAgentes(r.agentes); setUltima(r.ultima_version ?? null); })
      .catch((err: unknown) => {
        setAgentes([]);
        setError(mensaje(err, 'No se pudieron leer tus equipos.'));
      });
  }

  useEffect(() => {
    if (!conCuenta) return;
    morganAPI.agentes()
      .then((r) => { setAgentes(r.agentes); setUltima(r.ultima_version ?? null); })
      .catch((err: unknown) => {
        setAgentes([]);
        setError(mensaje(err, 'No se pudieron leer tus equipos.'));
      });
  }, [conCuenta]);

  // La cuenta atrás del código. Solo mientras hay uno a la vista.
  useEffect(() => {
    if (!codigo) return;
    const reloj = window.setInterval(() => setAhora(Date.now() / 1000), 1000);
    return () => window.clearInterval(reloj);
  }, [codigo]);

  // Mientras hay un código a la vista, se mira cada 3 s si ya se conectó el PC nuevo
  // (4.17): quien acaba de pegar la línea ve aquí «conectado», sin recargar nada.
  useEffect(() => {
    if (!codigo || !antes) return;
    const mirar = window.setInterval(() => {
      morganAPI.agentes().then((r) => {
        setAgentes(r.agentes);
        const nuevo = r.agentes.find((a) => !antes.has(a.id) && a.conectado);
        if (nuevo) {
          setCodigo(null);
          setAntes(null);
          setAviso(`«${nuevo.nombre}» está conectado. En tu PC se ha abierto «Morgan en tu PC»: `
            + 'elige allí qué carpetas puede ver Morgan y qué puede hacer.');
        }
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(mirar);
  }, [codigo, antes]);

  if (!conCuenta) {
    return (
      <section className="ajustes-grupo">
        <h3>Tu equipo</h3>
        <p className="ajustes-nota">
          Este Morgan ya corre en tu equipo y trabaja con tus archivos directamente:
          no hay nada que emparejar. Emparejar es para el Morgan de la web.
        </p>
      </section>
    );
  }

  async function pedirCodigo() {
    setError('');
    setAviso('');
    setPidiendo(true);
    try {
      // Los que hay AHORA, pedidos de nuevo: si se pulsa antes de que cargue la lista, uno
      // que ya estaba contaría como nuevo y saldría un «conectado» falso (lo cazó su prueba).
      const yaEstaban = await morganAPI.agentes().then((l) => l.agentes).catch(() => agentes ?? []);
      const r = await morganAPI.codigoAgente();
      setAntes(new Set(yaEstaban.map((a) => a.id)));
      setAhora(Date.now() / 1000);
      setCodigo({ codigo: r.codigo, caduca_en: r.caduca_en, instalar: r.instalar });
    } catch (err) {
      setError(mensaje(err, 'No se pudo pedir un código.'));
    } finally {
      setPidiendo(false);
    }
  }

  async function abrirAjustes(agente: AgenteLocal) {
    setError('');
    setAviso('');
    try {
      await morganAPI.abrirAjustesAgente(agente.id);
      setAviso(`Abierto en «${agente.nombre}»: mira la pantalla de ese PC.`);
    } catch (err) {
      setError(mensaje(err, 'No se pudo abrir en el PC.'));
    }
  }

  async function revocar(agente: AgenteLocal) {
    setError('');
    setAviso('');
    try {
      await morganAPI.revocarAgente(agente.id);
      setConfirmar(null);
      setAviso(`«${agente.nombre}» ya no está emparejado: ese PC no puede volver a conectarse.`);
      await cargar();
    } catch (err) {
      setError(mensaje(err, 'No se pudo revocar el equipo.'));
    }
  }

  const caducado = codigo !== null && ahora >= codigo.caduca_en;

  return (
    <>
      <section className="ajustes-grupo">
        <h3>Conectar tu PC</h3>
        <p className="ajustes-nota">
          El agente local es un programa que instalas en tu PC para que Morgan, desde
          la web o el móvil, pueda trabajar con las carpetas que <strong>tú</strong>{' '}
          elijas. Decide siempre con sus propias reglas, y lo delicado se confirma en
          el PC, nunca aquí.
        </p>
        <p className="ajustes-nota">
          <strong>Al instalarlo no puede hacer nada</strong> hasta que tú, en tu PC, le
          das carpetas para leer (y, si quieres, para escribir) o enciendes la terminal.
          Se actualiza solo cuando tú lo aceptas, y solo con versiones firmadas.
        </p>

        {codigo && !caducado && (
          <div className="ajustes-token-nuevo" role="status">
            <strong>Tu código</strong>
            <span className="ajustes-codigo-agente" aria-label="Código de emparejamiento">{codigo.codigo}</span>
            <p>
              Caduca en <strong>{restante(codigo.caduca_en, ahora)}</strong> y sirve una sola vez.
              El agente te enseñará tu cuenta y te preguntará si quieres emparejarlo: si la
              cuenta que ves no es la tuya, di que no.
            </p>
            {codigo.instalar ? (
              <>
                {/* 5.0: el programa de Windows es lo recomendado; la línea, la alternativa. */}
                <ol className="pasos-equipo">
                  <li>
                    <a href={DESCARGA_WINDOWS} target="_blank" rel="noreferrer">
                      <strong>Descarga Morgan para Windows</strong>
                    </a>{' '}
                    e instálalo (no pide administrador).
                    <span className="ajustes-nota">
                      {' '}Aún no está firmado: si Windows dice «Windows protegió tu PC», pulsa{' '}
                      <strong>Más información</strong> y después <strong>Ejecutar de todas formas</strong>.
                    </span>
                  </li>
                  <li>
                    Ábrelo, escribe este código y pulsa <strong>Conectar</strong>. Te enseñará de qué
                    cuenta es: si es la tuya, confirma.
                  </li>
                  <li>
                    Después elige en <strong>«Qué puede hacer y qué carpetas ve»</strong> lo que
                    Morgan puede usar de tu PC. Esta página te avisará cuando se conecte.
                  </li>
                </ol>
                <details>
                  <summary>Sin el programa: con una línea de PowerShell</summary>
                  <ol className="pasos-equipo">
                    <li>
                      <strong>Copia esta línea</strong>:
                      <ParaCopiar texto={codigo.instalar} etiqueta="Copiar la línea de instalación" />
                    </li>
                    <li>
                      En tu PC, pulsa <kbd>Inicio</kbd>, escribe <strong>PowerShell</strong> y ábrelo.
                      Pega con <strong>clic derecho</strong> y pulsa <kbd>Intro</kbd>.
                    </li>
                    <li>
                      Di que sí cuando te enseñe tu cuenta. Si no tienes Python, te pregunta si lo
                      instala (sin administrador).
                    </li>
                  </ol>
                </details>
                <details>
                  <summary>¿Ya tenías el agente instalado?</summary>
                  <ParaCopiar
                    texto={`& "$env:LOCALAPPDATA\\Morgan\\agente\\morgan-agente.cmd" emparejar --codigo ${codigo.codigo}`}
                    etiqueta="Copiar la orden de emparejar" />
                </details>
                <p className="ajustes-nota" aria-live="polite">Esperando a que tu PC se conecte…</p>
              </>
            ) : (
              <code className="ajustes-orden">python -m src.agente emparejar</code>
            )}
            <div className="ajustes-acciones">
              <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => setCodigo(null)}>
                Cerrar
              </button>
            </div>
          </div>
        )}

        {(!codigo || caducado) && (
          <>
            {caducado && <p className="ajustes-nota">El código caducó sin usarse.</p>}
            <button type="button" className="ajustes-boton" onClick={() => void pedirCodigo()} disabled={pidiendo}>
              {pidiendo ? 'Pidiendo…' : caducado ? 'Pedir otro código' : 'Emparejar un equipo'}
            </button>
          </>
        )}
      </section>

      <section className="ajustes-grupo">
        <h3>Tus equipos</h3>

        {agentes === null && <p className="ajustes-nota">Cargando…</p>}
        {agentes?.length === 0 && <p className="ajustes-nota">No tienes ninguno emparejado.</p>}

        {agentes && agentes.length > 0 && (
          <ul className="ajustes-sesiones ajustes-tokens">
            {agentes.map((a) => (
              <li key={a.id}>
                <div className="ajustes-token__cabecera">
                  <span>{a.nombre}</span>
                  {confirmar === a.id ? (
                    <span className="ajustes-acciones">
                      <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => setConfirmar(null)}>
                        No
                      </button>
                      <button type="button" className="ajustes-boton peligro" onClick={() => void revocar(a)}>
                        Sí, revocar
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="ajustes-boton ajustes-boton--suave"
                      onClick={() => setConfirmar(a.id)}
                      aria-label={`Revocar ${a.nombre}`}
                    >
                      Revocar
                    </button>
                  )}
                </div>
                {a.conectado && (
                  <button type="button" className="ajustes-boton ajustes-boton--suave"
                    onClick={() => void abrirAjustes(a)} aria-label={`Abrir los ajustes en ${a.nombre}`}>
                    Abrir los ajustes en mi PC
                  </button>
                )}
                <small className={a.conectado ? 'equipo-conectado' : 'equipo-desconectado'}>
                  {a.conectado ? '● Conectado ahora' : '○ No conectado'}
                  {a.conectado && a.capacidades && a.capacidades.length > 0 && ` · ${loQueOfrece(a.capacidades)}`}
                </small>
                <small>{[a.sistema, a.agent_version && `agente ${a.agent_version}`].filter(Boolean).join(' · ') || 'Sistema desconocido'}</small>
                {ultima && a.agent_version && a.agent_version !== ultima && (
                  <small className="equipo-desactualizado">
                    Hay una versión nueva: {ultima}. El PC te preguntará si instalarla.
                  </small>
                )}
                <small>
                  Emparejado el {fecha(a.creado_en)} · última conexión: {fecha(a.last_seen)}
                  {a.credencial_rotada_en ? ` · credencial cambiada el ${fecha(a.credencial_rotada_en)}` : ''}
                </small>
                <HistorialDelEquipo agente={a} />
              </li>
            ))}
          </ul>
        )}

        {error && <div className="error-banner">{error}</div>}
        {aviso && <div className="ajustes-aviso">{aviso}</div>}
      </section>
    </>
  );
}

/** Lo que ofrece un equipo, en palabras (3.7). */
const QUE_ES: Record<string, string> = {
  list_files: 'leer', read_file: 'leer', search_files: 'leer', file_info: 'leer', pc_context: 'leer',
  copy_file: 'copiar',
  create_file: 'escribir', edit_file: 'escribir', append_file: 'escribir', create_folder: 'escribir',
  move_file: 'escribir', copy_path: 'escribir', compress: 'escribir',
  delete_file: 'borrar', run_command: 'terminal', run_change_command: 'terminal',
  get_processes: 'procesos', kill_process: 'procesos', pc_diagnostics: 'diagnóstico',
  service_control: 'servicios', open_app: 'abrir apps', close_app: 'cerrar apps',
  clipboard: 'portapapeles', notify: 'avisos', windows: 'ventanas', screenshot: 'capturas',
  ui_read: 'interfaz', ui_control: 'interfaz',
};

export function loQueOfrece(capacidades: string[]): string {
  const partes = [...new Set(capacidades.map((c) => QUE_ES[c]).filter(Boolean))];
  return partes.length ? partes.join(', ') : 'solo su estado';
}

/**
 * El historial de órdenes de un equipo (3.7): qué se le pidió, cuándo, cómo acabó y cuánto
 * tardó. **Nunca los argumentos ni el contenido.** Se carga al abrirlo, no antes.
 */
function HistorialDelEquipo({ agente }: { agente: AgenteLocal }) {
  const [ordenes, setOrdenes] = useState<OrdenDeAgente[] | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function abrir() {
    setAbierto(!abierto);
    if (abierto || ordenes !== null) return;
    try {
      setOrdenes((await morganAPI.ordenesAgente(agente.id)).ordenes);
    } catch {
      setError('No se pudo cargar el historial.');
    }
  }

  return (
    <div className="equipo-historial">
      <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => void abrir()}
        aria-expanded={abierto} aria-label={`Historial de ${agente.nombre}`}>
        {abierto ? 'Ocultar historial' : 'Historial (7 días)'}
      </button>
      {abierto && error && <small>{error}</small>}
      {abierto && ordenes === null && !error && <small>Cargando…</small>}
      {abierto && ordenes?.length === 0 && <small>Ninguna orden en estos días.</small>}
      {abierto && ordenes && ordenes.length > 0 && (
        <ul className="equipo-historial__lista">
          {ordenes.map((o) => (
            <li key={o.command_id}>
              <small>
                {fecha(o.creado_en)} · {o.capability} · {o.estado}
                {o.ms !== null && ` · ${Math.round(o.ms)} ms`}
              </small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
