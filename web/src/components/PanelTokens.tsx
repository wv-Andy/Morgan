/* Morgan Web — Tokens de API, dentro de Ajustes (plan de la API, fase 2, V2.0.41) */

import { useEffect, useRef, useState } from 'react';
import { MorganAPIError, morganAPI, type AlcanceToken, type TokenDeApi } from '../lib/api';
import type { EstadoCuenta } from './Cuenta';
import { ParaCopiar } from './ParaCopiar';

/**
 * Qué deja hacer cada alcance, en palabras de quien no ha visto nunca uno.
 * El orden es el de la lista: de lo que pide casi cualquier programa a lo que
 * más cuidado merece.
 */
const ALCANCES: { id: AlcanceToken; titulo: string; ayuda: string }[] = [
  { id: 'chat', titulo: 'Chatear', ayuda: 'Mandar mensajes a Morgan y recibir sus respuestas.' },
  { id: 'lectura', titulo: 'Leer', ayuda: 'Ver tus conversaciones, tu memoria, tus tareas y tus archivos.' },
  { id: 'escritura', titulo: 'Cambiar', ayuda: 'Crear, cambiar y borrar conversaciones, recuerdos, archivos y espacios.' },
];

/** Las caducidades que se ofrecen. El servidor acepta de 1 a 365 días. */
const CADUCIDADES = [
  { dias: 30, texto: '30 días' },
  { dias: 90, texto: '90 días' },
  { dias: 180, texto: '6 meses' },
  { dias: 365, texto: '1 año' },
];

/** A partir de cuántos días antes de caducar se avisa. */
const DIAS_DE_AVISO = 7;

function fecha(marca: number): string {
  return new Date(marca * 1000).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' });
}

function ultimoUso(marca: number | null): string {
  if (!marca) return 'Sin usar todavía';
  return 'Último uso: ' + new Date(marca * 1000).toLocaleString('es', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/** «Caduca el 17 dic 2026», o un aviso si le queda poco. */
function caducidad(caduca_en: number, ahora = Date.now() / 1000): { texto: string; pronto: boolean } {
  const dias = Math.ceil((caduca_en - ahora) / 86400);
  if (dias <= DIAS_DE_AVISO) {
    return { texto: dias <= 1 ? 'Caduca hoy o mañana' : `Caduca en ${dias} días`, pronto: true };
  }
  return { texto: `Caduca el ${fecha(caduca_en)}`, pronto: false };
}

function mensaje(err: unknown, porDefecto: string): string {
  return err instanceof MorganAPIError ? err.message : porDefecto;
}

/**
 * Los tokens de API de la cuenta: crearlos, verlos y revocarlos.
 *
 * **El valor se enseña una sola vez**, justo al crearlo, y la caja no se cierra
 * sola: se cierra cuando la persona dice que ya lo ha guardado. Si se cerrara al
 * cambiar de sección o a los pocos segundos, perderlo sería lo normal, y la única
 * salida es revocarlo y crear otro.
 */
export function PanelTokens({ cuenta }: { cuenta: EstadoCuenta }) {
  const conCuenta = cuenta.autenticado && !cuenta.local;

  const [tokens, setTokens] = useState<TokenDeApi[] | null>(null);
  const [error, setError] = useState('');
  const [aviso, setAviso] = useState('');
  const [creando, setCreando] = useState(false);
  const [nuevo, setNuevo] = useState<{ nombre: string; valor: string; apiUrl?: string;
    alcances: AlcanceToken[] } | null>(null);
  const [confirmar, setConfirmar] = useState<string | null>(null);

  function cargar(): Promise<void> {
    return morganAPI.tokens()
      .then((r) => setTokens(r.tokens))
      .catch((err: unknown) => {
        setTokens([]);
        setError(mensaje(err, 'No se pudieron leer tus tokens.'));
      });
  }

  useEffect(() => {
    if (!conCuenta) return;
    morganAPI.tokens()
      .then((r) => setTokens(r.tokens))
      .catch((err: unknown) => {
        setTokens([]);
        setError(mensaje(err, 'No se pudieron leer tus tokens.'));
      });
  }, [conCuenta]);

  // En tu equipo no hay cuenta, y la API ya es tuya sin nada más: está en tu
  // ordenador y solo escucha en él.
  if (!conCuenta) {
    return (
      <section className="ajustes-grupo">
        <h3>Acceso por API</h3>
        <p className="ajustes-nota">
          Este Morgan corre en tu equipo y no pide cuenta, así que tus programas
          pueden hablar con su API directamente, sin token. Los tokens son para el
          Morgan de la web.
        </p>
      </section>
    );
  }

  async function revocar(token: TokenDeApi) {
    setError('');
    setAviso('');
    try {
      await morganAPI.revocarToken(token.id);
      setConfirmar(null);
      setAviso(`«${token.nombre}» ya no vale. El programa que lo usaba dejará de poder entrar.`);
      await cargar();
    } catch (err) {
      setError(mensaje(err, 'No se pudo revocar el token.'));
    }
  }

  async function revocarTodos() {
    setError('');
    setAviso('');
    try {
      const { revocados } = await morganAPI.revocarTodosLosTokens();
      setConfirmar(null);
      setAviso(`Revocados ${revocados}. Ningún programa puede entrar ya con un token.`);
      await cargar();
    } catch (err) {
      setError(mensaje(err, 'No se pudieron revocar los tokens.'));
    }
  }

  return (
    <>
      <section className="ajustes-grupo">
        <h3>Conectar un programa</h3>
        <p className="ajustes-nota">
          Un token deja que un programa tuyo —un script, una extensión de tu editor—
          use Morgan en tu nombre, sin tu contraseña. Tú decides qué puede hacer y
          lo puedes revocar cuando quieras. <strong>Si no sabes qué es, no lo
          necesitas.</strong>
        </p>
        <p className="ajustes-nota">
          Ningún token puede cambiar tu contraseña, borrar tu cuenta ni crear otros
          tokens. Y al cambiar la contraseña, todos dejan de valer.
        </p>

        {nuevo && <TokenRecienCreado {...nuevo} onGuardado={() => setNuevo(null)} />}

        {!nuevo && (creando ? (
          <FormularioToken
            onCancelar={() => setCreando(false)}
            onCreado={async (nombre, valor, apiUrl, alcances) => {
              setCreando(false);
              setAviso('');
              setNuevo({ nombre, valor, apiUrl, alcances });
              await cargar();
            }}
          />
        ) : (
          <button type="button" className="ajustes-boton" onClick={() => { setCreando(true); setAviso(''); setError(''); }}>
            Crear un token
          </button>
        ))}
      </section>

      <section className="ajustes-grupo">
        <h3>Tus tokens</h3>

        {tokens === null && <p className="ajustes-nota">Cargando…</p>}
        {tokens?.length === 0 && <p className="ajustes-nota">No tienes ninguno.</p>}

        {tokens && tokens.length > 0 && (
          <ul className="ajustes-sesiones ajustes-tokens">
            {tokens.map((t) => {
              const cad = caducidad(t.caduca_en);
              return (
                <li key={t.id}>
                  <div className="ajustes-token__cabecera">
                    <span>{t.nombre}</span>
                    {confirmar === t.id ? (
                      <span className="ajustes-acciones">
                        <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => setConfirmar(null)}>
                          No
                        </button>
                        <button type="button" className="ajustes-boton peligro" onClick={() => void revocar(t)}>
                          Sí, revocar
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="ajustes-boton ajustes-boton--suave"
                        onClick={() => setConfirmar(t.id)}
                        aria-label={`Revocar ${t.nombre}`}
                      >
                        Revocar
                      </button>
                    )}
                  </div>
                  <small>
                    Puede: {ALCANCES.filter((a) => t.alcances.includes(a.id)).map((a) => a.titulo.toLowerCase()).join(', ')}
                  </small>
                  <small className={cad.pronto ? 'ajustes-token__pronto' : undefined}>
                    {cad.texto} · {ultimoUso(t.ultimo_uso)}
                  </small>
                </li>
              );
            })}
          </ul>
        )}

        {tokens && tokens.length > 1 && (confirmar === 'todos' ? (
          <div className="ajustes-acciones">
            <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={() => setConfirmar(null)}>
              No
            </button>
            <button type="button" className="ajustes-boton peligro" onClick={() => void revocarTodos()}>
              Sí, revocar todos
            </button>
          </div>
        ) : (
          <button type="button" className="ajustes-boton" onClick={() => setConfirmar('todos')}>
            Revocar todos
          </button>
        ))}

        {error && <div className="error-banner">{error}</div>}
        {aviso && <div className="ajustes-aviso">{aviso}</div>}
      </section>
    </>
  );
}

function FormularioToken({ onCancelar, onCreado }: {
  onCancelar: () => void;
  onCreado: (nombre: string, valor: string, apiUrl: string | undefined, alcances: AlcanceToken[]) => Promise<void>;
}) {
  const [nombre, setNombre] = useState('');
  // Por defecto, lo que pide casi cualquier programa y nada que pueda borrar.
  const [alcances, setAlcances] = useState<AlcanceToken[]>(['chat', 'lectura']);
  const [dias, setDias] = useState(90);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState('');

  function alternar(id: AlcanceToken) {
    setAlcances((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id]));
  }

  async function crear(evento: React.FormEvent) {
    evento.preventDefault();
    setError('');
    setEnviando(true);
    try {
      const res = await morganAPI.crearToken(nombre.trim(), alcances, dias);
      await onCreado(res.token.nombre, res.valor, res.api_url, res.token.alcances ?? alcances);
    } catch (err) {
      setError(mensaje(err, 'No se pudo crear el token.'));
    } finally {
      // Si salió bien, el formulario ya no se ve; apagarlo igual no cuesta nada
      // y es la regla del proyecto: ninguna bandera de «ocupado» sin su finally.
      setEnviando(false);
    }
  }

  return (
    <form onSubmit={crear} className="ajustes-form">
      <label className="ajustes-campo">
        <span>Para qué es</span>
        <input
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          placeholder="Por ejemplo: script de copias, VS Code"
          maxLength={60}
          autoFocus
          required
        />
      </label>

      <fieldset className="ajustes-alcances">
        <legend>Qué puede hacer</legend>
        {ALCANCES.map((a) => (
          <label key={a.id} className="ajustes-alcance">
            <input type="checkbox" checked={alcances.includes(a.id)} onChange={() => alternar(a.id)} />
            <span>
              <strong>{a.titulo}</strong>
              <small>{a.ayuda}</small>
            </span>
          </label>
        ))}
      </fieldset>

      <label className="ajustes-campo">
        <span>Caduca en</span>
        <select className="ajustes-select" value={dias} onChange={(e) => setDias(Number(e.target.value))}>
          {CADUCIDADES.map((c) => <option key={c.dias} value={c.dias}>{c.texto}</option>)}
        </select>
      </label>

      <div className="ajustes-acciones">
        <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={onCancelar}>
          Cancelar
        </button>
        <button type="submit" className="ajustes-boton" disabled={enviando || !nombre.trim() || alcances.length === 0}>
          {enviando ? 'Creando…' : 'Crear'}
        </button>
      </div>
      {alcances.length === 0 && <small className="ajustes-nota">Elige al menos una cosa que pueda hacer.</small>}
      {error && <div className="error-banner">{error}</div>}
    </form>
  );
}

/** Ejemplos que funcionan tal cual, con el token y la dirección de verdad (4.17). Con
 * «Chatear», una pregunta; si no, leer tus conversaciones. Sin tildes en el mensaje: el
 * PowerShell de Windows (5.1) manda el cuerpo en otra codificación. */
export function ejemplos(apiUrl: string, valor: string, alcances: AlcanceToken[]) {
  const chat = alcances.includes('chat');
  const ruta = chat ? '/chat' : '/sessions';
  const cuerpo = '{"message": "Hola Morgan, dime en una frase que puedes hacer", "session_id": "api-prueba"}';
  return {
    PowerShell: chat
      ? `Invoke-RestMethod -Method Post -Uri "${apiUrl}${ruta}" -Headers @{ Authorization = "Bearer ${valor}" } `
        + `-ContentType "application/json" -Body '${cuerpo}'`
      : `Invoke-RestMethod -Uri "${apiUrl}${ruta}" -Headers @{ Authorization = "Bearer ${valor}" }`,
    curl: chat
      ? `curl -X POST "${apiUrl}${ruta}" -H "Authorization: Bearer ${valor}" -H "Content-Type: application/json" `
        + `-d '${cuerpo}'`
      : `curl "${apiUrl}${ruta}" -H "Authorization: Bearer ${valor}"`,
    Python: chat
      ? `import requests\n\nr = requests.post("${apiUrl}${ruta}", timeout=180,\n    headers={"Authorization": "Bearer ${valor}"},\n`
        + `    json={"message": "Hola Morgan, dime en una frase que puedes hacer", "session_id": "api-prueba"})\nprint(r.json()["response"])`
      : `import requests\n\nr = requests.get("${apiUrl}${ruta}", timeout=30,\n    headers={"Authorization": "Bearer ${valor}"})\nprint(r.json())`,
  };
}

function TokenRecienCreado({ nombre, valor, apiUrl, alcances, onGuardado }: {
  nombre: string;
  valor: string;
  apiUrl?: string;
  alcances: AlcanceToken[];
  onGuardado: () => void;
}) {
  const [lenguaje, setLenguaje] = useState<'PowerShell' | 'curl' | 'Python'>('PowerShell');
  const campo = useRef<HTMLTextAreaElement>(null);
  const [copiado, setCopiado] = useState<'no' | 'si' | 'a-mano'>('no');

  async function copiar() {
    try {
      await navigator.clipboard.writeText(valor);
      setCopiado('si');
    } catch {
      // Sin permiso para el portapapeles (o fuera de HTTPS): se deja
      // seleccionado para copiarlo a mano.
      campo.current?.select();
      setCopiado('a-mano');
    }
  }

  return (
    <div className="ajustes-token-nuevo" role="status">
      <strong>Tu token «{nombre}»</strong>
      <p>
        Cópialo ahora y guárdalo en un sitio seguro. <strong>No se volverá a
        enseñar</strong>: Morgan solo guarda una huella que no sirve para
        recuperarlo. Si lo pierdes, revócalo y crea otro.
      </p>
      {/* Un área de texto y no un campo de una línea: el valor se parte y se ve
          entero, que en el móvil es lo que permite comprobarlo o copiarlo a mano. */}
      <textarea
        ref={campo}
        className="ajustes-token-nuevo__valor"
        value={valor}
        readOnly
        rows={2}
        spellCheck={false}
        aria-label="Valor del token"
        onFocus={(e) => e.currentTarget.select()}
      />
      <div className="ajustes-acciones">
        <button type="button" className="ajustes-boton" onClick={() => void copiar()}>
          {copiado === 'si' ? 'Copiado' : 'Copiar'}
        </button>
        <button type="button" className="ajustes-boton ajustes-boton--suave" onClick={onGuardado}>
          Ya lo he guardado
        </button>
      </div>
      {copiado === 'a-mano' && (
        <small>No se pudo copiar solo: ya está seleccionado, cópialo con Ctrl+C (o mantén pulsado en el móvil).</small>
      )}
      <small>
        Se usa en la cabecera <code>Authorization: Bearer …</code> de cada petición.
      </small>
      {apiUrl && (
        <div className="token-ejemplos">
          <strong>Pruébalo</strong>
          <div className="ajustes-acciones" role="tablist" aria-label="Lenguaje del ejemplo">
            {(['PowerShell', 'curl', 'Python'] as const).map((l) => (
              <button key={l} type="button" role="tab" aria-selected={lenguaje === l}
                className={`ajustes-boton ${lenguaje === l ? '' : 'ajustes-boton--suave'}`}
                onClick={() => setLenguaje(l)}>{l}</button>
            ))}
          </div>
          <ParaCopiar texto={ejemplos(apiUrl, valor, alcances)[lenguaje]} etiqueta={`Copiar el ejemplo de ${lenguaje}`} />
          <small>
            Todas las rutas, con lo que piden y devuelven: <a href={`${apiUrl}/docs`} target="_blank"
              rel="noreferrer">{apiUrl}/docs</a>.
          </small>
        </div>
      )}
    </div>
  );
}
