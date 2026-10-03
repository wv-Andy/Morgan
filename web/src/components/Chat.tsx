/* Morgan Web — La conversación: compositor, edición y control del turno */
//
// Este fichero exporta un tipo ademas del componente. Son la misma pieza —el
// estado de la conexion decide lo que la conversacion puede hacer— y separarlos
// por una regla de recarga en caliente dejaria dos ficheros que solo se
// entienden juntos.
// oxlint-disable react/only-export-components

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  API_ES_REMOTA,
  MorganAPIError,
  OperacionCancelada,
  morganAPI,
} from '../lib/api';
import type { Descarga, EventoDelTurno, StoredMessage, UploadItem } from '../lib/api';
import { fraseDelEvento } from '../lib/progreso';
import { Adjuntos } from './Adjuntos';
import { LogoMorgan } from './Logo';
import { IconChat, IconMemory, IconSearch, IconSend, IconTasks, IconTools } from './Icons';
import { Markdown } from './Markdown';
import { AvisoSoltar, useSoltarArchivos } from './SoltarArchivos';
import { formatTime, uid } from '../lib/formato';

/**
 * Todo lo que pasa dentro de una conversación.
 *
 * Salió de `App.tsx` cuando ese fichero llegó a 1800 líneas y siete vistas. Es
 * el bloque que más creció —las cuatro operaciones sobre el turno son de la
 * V1.9— y el que más se toca, así que era el primero que había que sacar.
 *
 * Se queda junto lo que se entiende junto: el compositor, el editor de un
 * mensaje enviado y la vista que los coordina comparten estado y decisiones
 * (quién puede escribir, qué se descarta al editar). Repartirlos en tres
 * ficheros habría convertido cada cambio en un recorrido.
 */

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  elapsed?: number;
  /** Puesto si contesto un respaldo y no el proveedor principal. */
  respaldo?: boolean;
  error?: boolean;
  /** El turno se cortó a petición del usuario. No es un fallo. */
  detenido?: boolean;
  /** Falló, pero el texto y los adjuntos siguen guardados: se puede reintentar. */
  reintentable?: boolean;
  /** Se cortó la conexión y el turno sigue en el servidor: se espera su respuesta (4.5). */
  esperando?: boolean;
  /**
   * Copias del PC que Morgan trajo en este turno (3.1-E): se pintan como botones.
   *
   * Vienen de los eventos del turno y **no de lo que escriba el modelo**: probé la
   * primera versión y le salió «una especie de enlace», porque el modelo escribió la
   * dirección a su manera. Al recargar la conversación no están —el historial guarda el
   * texto, no los eventos—, pero los archivos siguen en Ajustes → Tus archivos.
   */
  descargas?: Descarga[];
}

// ─── Utilidades ──────────────────────────────────────────────────────────────

/** «1,2 MB», para el botón de una copia del PC. */
function tamanoCorto(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Lo guardado de una conversación, como lo pinta el chat. */
function desdeLaBase(guardados: StoredMessage[]): Message[] {
  return guardados
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .map(m => ({
      id: `db-${m.id}`,
      role: m.role === 'assistant' ? 'assistant' : 'user',
      content: m.content,
      timestamp: m.created_at ? new Date(m.created_at.replace(' ', 'T')) : new Date(),
    }));
}

/** Milisegundos de una fecha de la base: sin zona es UTC, y «+00» vale como «+00:00». */
function instante(texto: string | null): number {
  if (!texto) return 0;
  let iso = texto.trim().replace(' ', 'T');
  if (/[+-]\d\d$/.test(iso)) iso += ':00';
  else if (!/(Z|[+-]\d\d:?\d\d)$/.test(iso)) iso += 'Z';
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? 0 : ms;
}

/**
 * Cuando la conexión se corta a mitad de un turno, el turno **sigue** en el servidor y
 * se guarda al terminar (decisión B del streaming). Se espera su respuesta mirando la
 * conversación, en vez de ofrecer «Reintentar» (4.5): medido en mi prueba desde
 * el móvil, el reintento lanzaba otro turno con el primero aún en marcha.
 */
export const ESPERA_CADA_MS = 4000;
export const ESPERA_MAX_MS = 200_000;
/** Margen para la diferencia de reloj entre el móvil y el servidor. */
const MARGEN_RELOJ_MS = 10_000;

/** Qué fallos de un turno dejan ese turno vivo en el servidor. */
const SIGUE_EN_EL_SERVIDOR = new Set(['STREAM_CORTADO', 'TIMEOUT', 'NETWORK_ERROR']);

const AVISO_ESPERANDO =
  '_Se cortó la conexión, pero Morgan sigue con tu mensaje: la respuesta aparecerá aquí en cuanto termine._';



// ─── Componentes compartidos ─────────────────────────────────────────────────

// El consejo util depende de donde viva el backend: en local hay algo que
// arrancar, con un backend remoto no.
/**
 * En qué punto está la conexión con Morgan.
 *
 * Son TRES estados y no dos. Aplastarlos a un booleano `apiOnline` hacía que
 * «todavía no lo sé» se enseñara como «está caído»: al abrir la web, mientras
 * la primera comprobación estaba en vuelo, el compositor decía «Morgan API
 * desconectada» sobre un servicio que estaba perfectamente. Con Render
 * despertando, ese cartel salía durante casi un minuto.
 *
 * Callar mientras se averigua es lo correcto: una alarma que salta antes de
 * saber si hay incendio enseña a ignorarla.
 */

export type EstadoApi = 'loading' | 'online' | 'offline';

const PISTA_DESCONECTADO = API_ES_REMOTA
  ? 'No se alcanza el backend de Morgan. Puede estar despertando; reintenta en unos segundos.'
  : 'Inicia el backend con python -m src.api.server para empezar a conversar.';

/**
 * Qué puede hacer Morgan, según **dónde corre** (dato de `/status`), no según
 * cómo se compiló la web (V2.0.33). Antes dependía de `API_ES_REMOTA`: una web
 * servida por el propio backend en modo nube decía «puede leer y escribir
 * archivos, ejecutar comandos…» con la insignia «En la nube» justo encima. Lo
 * encontró el recorrido de alguien nuevo de la auditoría 2.3.
 */
function pistaConectado(entorno: string): string {
  return entorno === 'cloud'
    ? 'Morgan puede buscar en internet, leer páginas y recordar lo que le cuentes.'
    : 'Morgan puede leer y escribir archivos, ejecutar comandos, consultar Git y recordar lo que le cuentes.';
}


// Morgan responde en Markdown: tablas, listas y bloques de codigo. Antes se
// mostraban como texto crudo con barras verticales. El renderizador construye
// nodos de React, nunca HTML (ver components/Markdown.tsx).
function MessageContent({ text }: { text: string }) {
  return <Markdown text={text} />;
}

function TypingIndicator({ progreso }: { progreso: string | null }) {
  return (
    <div className="message assistant fade-in">
      <div className="typing-indicator" role="status" aria-label={progreso ?? 'Morgan está escribiendo'}>
        <div className="typing-dot" />
        <div className="typing-dot" />
        <div className="typing-dot" />
        {/* Qué está haciendo, con los eventos de /chat/stream (V2.0.14). Sin
            frase no se pinta nada: un texto fijo de relleno enseña a ignorarlo. */}
        {progreso && <span className="typing-progreso">{progreso}</span>}
      </div>
    </div>
  );
}

/** Barra de entrada. Se usa centrada en el estado inicial y anclada abajo durante la conversación. */
function Composer({
  value, onChange, onSubmit, disabled, onAdjuntado,
  adjuntos = [], onQuitarAdjunto, onTranscrito, conectando = false, ocupado = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  onAdjuntado?: (archivo: UploadItem) => void;
  adjuntos?: UploadItem[];
  onQuitarAdjunto?: (id: string) => void;
  /** Lo que se entendio en una grabacion, para revisarlo antes de enviarlo. */
  onTranscrito?: (texto: string) => void;
  /** Todavía se está comprobando si Morgan responde. No es lo mismo que caído. */
  conectando?: boolean;
  /**
   * Morgan está respondiendo. Tampoco es lo mismo que caído (V2.0.33): con un
   * solo `disabled`, mientras contestaba el campo decía «Morgan API
   * desconectada…», y quien escribía por primera vez creía haberlo roto.
   */
  ocupado?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /**
   * El alto se recalcula cuando cambia el VALOR, no cuando se teclea.
   *
   * Antes se hacía en el manejador de tecleo, y eso dejaba fuera todo lo que
   * escribe en el compositor sin que nadie pulse una tecla: las sugerencias de
   * la portada y el texto de una transcripción. El resultado era un campo con
   * alto de una línea conteniendo tres, enseñando el final y recortando el
   * principio — parecía que Morgan se hubiera comido el texto.
   *
   * Al derivarlo del valor, da igual quién lo ponga: si el contenido cambia, el
   * alto se ajusta. Es la diferencia entre resolver el caso que se vio y
   * resolver la categoría.
   */
  useEffect(() => {
    const campo = textareaRef.current;
    if (!campo) return;

    // A 'auto' primero, si no `scrollHeight` devuelve el alto actual cuando el
    // texto ENCOGE y el campo no se volvería a hacer pequeño.
    campo.style.height = 'auto';
    campo.style.height = `${Math.min(campo.scrollHeight, 180)}px`;
  }, [value]);

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange(e.target.value);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  };

  const kb = (bytes: number) =>
    bytes < 1024 * 1024
      ? `${Math.max(1, Math.round(bytes / 1024))} KB`
      : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

  return (
    <div className="composer">
      {adjuntos.length > 0 && (
        <div className="adjuntos-pendientes">
          {adjuntos.map(a => (
            <span key={a.id} className={`adjunto-chip ${a.familia}`}>
              <span className="adjunto-nombre" title={`${a.nombre} · ${a.mime}`}>{a.nombre}</span>
              <span className="adjunto-peso">{kb(a.tamano)}</span>
              <button
                className="adjunto-quitar"
                onClick={() => onQuitarAdjunto?.(a.id)}
                aria-label={`Quitar ${a.nombre}`}
                title="Quitar"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <textarea
        ref={textareaRef}
        id="chat-input"
        className="composer-input"
        placeholder={
          conectando
            ? 'Conectando con Morgan…'
            : ocupado
              ? 'Morgan está respondiendo…'
              : disabled ? 'Morgan API desconectada…' : 'Pregunta lo que quieras'
        }
        value={value}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
        aria-label="Mensaje para Morgan"
      />

      {/* La fila de controles, debajo del campo como en el diseño: adjuntar a la
          izquierda, dictar y enviar a la derecha. Solo lo que hace algo. */}
      <div className="composer__barra">
        {onAdjuntado && (
          <Adjuntos
            disabled={disabled}
            onAdjuntado={onAdjuntado}
            onTranscrito={onTranscrito}
          />
        )}

        <button
          id="send-btn"
          className="send-button"
          onClick={onSubmit}
          disabled={disabled || (!value.trim() && adjuntos.length === 0)}
          aria-label="Enviar mensaje"
          title="Enviar (Enter)"
        >
          <IconSend size={17} />
        </button>
      </div>
    </div>
  );
}

/**
 * Lo que se ofrece en la portada.
 *
 * **Cada una escribe su texto en el compositor y ahí se queda.** No lanzan la
 * petición: quien pulsa puede leer lo que va a pedir, cambiarlo o borrarlo. Un
 * botón que pone a Morgan a trabajar sin enseñar antes qué le ha pedido hace
 * más de lo que aparenta, y con un agente que ejecuta acciones reales eso no es
 * un detalle de estilo.
 *
 * Los textos corresponden a cosas que Morgan sabe hacer de verdad con las
 * herramientas que tiene registradas. Poner aquí «genera una imagen» cuando no
 * hay con qué sería vender humo desde la primera pantalla.
 */
/** Lo que la portada enseña del sistema. Todo sale de `/status`. */
export interface InfoSistema {
  herramientas: number;
  entorno: string;
}

interface Sugerencia { icono: React.ReactNode; titulo: string; detalle: string; prompt: string }

const REVISAR_PROYECTO: Sugerencia = {
    icono: <IconTools size={16} />,
    titulo: 'Revisar un proyecto',
    detalle: 'Estructura, dependencias y puntos flojos',
    prompt: 'Inspecciona este proyecto y dime cómo está organizado, qué dependencias usa y qué partes te parecen más frágiles.',
};

const PLANEAR: Sugerencia = {
    icono: <IconTasks size={16} />,
    titulo: 'Planear antes de actuar',
    detalle: 'Morgan propone los pasos y tú los apruebas',
    prompt: 'Quiero hacer un cambio en varios archivos. Antes de tocar nada, crea un plan con los pasos que darías para que pueda aprobarlo.',
};

const BUSCAR: Sugerencia = {
    icono: <IconSearch size={16} />,
    titulo: 'Buscar y resumir',
    detalle: 'Consulta la web y te lo deja en claro',
    prompt: 'Busca información actualizada sobre ',
};

const LEER_PAGINA: Sugerencia = {
  icono: <IconChat size={16} />,
  titulo: 'Leer una página',
  detalle: 'Pega un enlace y te cuenta lo importante',
  prompt: 'Lee esta página y resúmeme lo importante: ',
};

const RECORDAR: Sugerencia = {
  icono: <IconMemory size={16} />,
  titulo: 'Recordar algo',
  detalle: 'Lo guarda entre conversaciones',
  prompt: 'Recuerda esto para próximas conversaciones: ',
};

/**
 * Tres, como en el diseño, y **según dónde corre Morgan**. En la nube no hay
 * proyecto que revisar ni archivos que cambiar: ofrecerlo sería prometer algo
 * que ahí no puede hacer. Lo de la nube es lo que el catálogo registra en ese
 * entorno (buscar en internet, leer páginas, recordar).
 */
function sugerenciasPara(entorno: string): Sugerencia[] {
  return entorno === 'cloud'
    ? [BUSCAR, LEER_PAGINA, RECORDAR]
    : [REVISAR_PROYECTO, PLANEAR, BUSCAR];
}

/**
 * Reescribir un mensaje ya enviado, en el sitio donde está.
 *
 * En línea y no en el compositor a propósito: el compositor puede tener otro
 * texto a medio escribir, y machacarlo para editar uno viejo sería perder
 * trabajo del usuario sin avisar.
 *
 * Enter envía, Shift+Enter hace salto de línea y Escape cancela — las mismas
 * teclas que el compositor, porque aprender dos convenios para lo mismo es
 * gratuito solo para quien escribe el código.
 */
function EditorDeMensaje({
  inicial, onGuardar, onCancelar,
}: {
  inicial: string;
  onGuardar: (texto: string) => void;
  onCancelar: () => void;
}) {
  const [valor, setValor] = useState(inicial);
  const campo = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = campo.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 260)}px`;
  }, []);

  return (
    <div className="editor-mensaje">
      <textarea
        ref={campo}
        className="editor-mensaje__campo"
        value={valor}
        onChange={e => {
          setValor(e.target.value);
          e.target.style.height = 'auto';
          e.target.style.height = `${Math.min(e.target.scrollHeight, 260)}px`;
        }}
        onKeyDown={e => {
          if (e.key === 'Escape') { e.preventDefault(); onCancelar(); }
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onGuardar(valor); }
        }}
        aria-label="Editar el mensaje"
      />
      <div className="editor-mensaje__acciones">
        <span className="editor-mensaje__aviso">
          Se descarta lo que venga después
        </span>
        <button type="button" onClick={onCancelar}>Cancelar</button>
        <button
          type="button"
          className="destacada"
          onClick={() => onGuardar(valor)}
          disabled={!valor.trim()}
        >
          Enviar de nuevo
        </button>
      </div>
    </div>
  );
}

// ─── Vistas ──────────────────────────────────────────────────────────────────

export function ChatView({
  estadoApi, sessionId, onConversationSaved, onTurnoTerminado, temporal = false,
  sistema, planAprobado = null, onPlanEnviado,
}: {
  /** Los tres estados, no un booleano: ver `EstadoApi`. */
  estadoApi: EstadoApi;
  sessionId: string;
  /** Lo que se enseña en la portada. Datos reales, no constantes. */
  sistema: InfoSistema;
  onConversationSaved: () => void;
  /** Se llama al acabar cada turno. Lo usa el panel de planes para enterarse
   *  de que Morgan quiza acaba de proponer uno. */
  onTurnoTerminado?: () => void;
  temporal?: boolean;
  /** 4.0: un plan que la persona acaba de aprobar. Aprobar es la orden: se manda solo
   *  el turno que lo ejecuta, sin tener que escribir «adelante». */
  planAprobado?: { id: string; objetivo: string } | null;
  onPlanEnviado?: () => void;
}) {
  // `apiOnline` sigue existiendo para decidir qué se puede HACER: con la
  // comprobación en vuelo tampoco se puede enviar nada. Lo que cambia es lo que
  // se DICE, y para eso hacen falta los tres estados.
  const apiOnline = estadoApi === 'online';
  const conectando = estadoApi === 'loading';

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  // La frase del progreso del turno en vuelo. Solo cambia cuando pasa algo:
  // el latido no la toca (ver `fraseDelEvento`).
  const [progreso, setProgreso] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  // Al abrir una conversación se recupera su historial persistido, UNA sola vez.
  // Antes dependía también de apiOnline: cualquier parpadeo de conexión volvía a
  // cargar desde la base y machacaba los mensajes locales del usuario.
  const historyLoaded = useRef(false);

  useEffect(() => {
    let cancelled = false;

    if (historyLoaded.current) return;
    historyLoaded.current = true;

    morganAPI.sessionMessages(sessionId)
      .then(res => {
        if (cancelled) return;
        setMessages(desdeLaBase(res.messages));
      })
      .catch(() => { /* sesión sin historial */ });

    return () => { cancelled = true; };
  }, [sessionId]);

  // Archivos adjuntos a este mensaje, todavia sin enviar. Van aparte del texto:
  // asi el usuario puede editar lo que escribio sin romper el vinculo, y ve
  // exactamente que va a mandar.
  const [adjuntos, setAdjuntos] = useState<UploadItem[]>([]);

  // Qué mensaje se está reescribiendo, si alguno.
  const [editando, setEditando] = useState<string | null>(null);

  // El turno en vuelo, para poder pararlo. En una referencia y no en el estado:
  // cambiarlo no tiene que repintar nada, y el manejador necesita leer el valor
  // de AHORA, no el de cuando se creo la funcion.
  const enVuelo = useRef<AbortController | null>(null);
  /** El turno en curso, para que «Detener» pare también lo que hace en el PC (3.4). */
  const turnoEnVuelo = useRef<string | null>(null);

  // Lo ultimo que se intento enviar. Es lo que permite reintentar sin que el
  // usuario tenga que reescribirlo: un fallo de red no deberia costarle el
  // mensaje ni los archivos que ya habia subido.
  const ultimoIntento = useRef<{ texto: string; adjuntos: UploadItem[] } | null>(null);

  // La espera de una respuesta cuyo stream se cortó (4.5): una a la vez, y se abandona
  // al cambiar de conversación.
  const esperaViva = useRef(0);
  useEffect(() => () => { esperaViva.current += 1; }, [sessionId]);

  /**
   * Mira la conversación hasta que llegue la respuesta del turno que sigue en el
   * servidor, y entonces la pinta como está guardada. Llegó si hay mensajes nuevos que
   * acaban en una respuesta, o si lo último guardado es la respuesta a `texto` y se
   * guardó después de enviarlo (por si terminó antes de la primera mirada).
   */
  const esperarRespuesta = useCallback(async (texto: string | null, enviadoEn: number, aviso: string) => {
    const mia = ++esperaViva.current;
    setMessages(prev => [...prev.filter(m => !m.esperando), {
      id: uid(), role: 'assistant', content: aviso, timestamp: new Date(), esperando: true,
    }]);
    let total0: number | null = null;
    const limite = Date.now() + ESPERA_MAX_MS;
    while (Date.now() < limite) {
      if (esperaViva.current !== mia) return;
      try {
        const res = await morganAPI.sessionMessages(sessionId);
        if (esperaViva.current !== mia) return;
        const vistos = res.messages.filter(m => m.role === 'user' || m.role === 'assistant');
        const ultimo = vistos[vistos.length - 1];
        const anterior = vistos[vistos.length - 2];
        const total = res.total ?? vistos.length;
        if (total0 === null) total0 = total;
        const reciente = instante(ultimo?.created_at ?? null) >= enviadoEn - MARGEN_RELOJ_MS;
        const llego = ultimo?.role === 'assistant' && (
          total > total0
          || (reciente && (texto === null || (anterior?.role === 'user' && anterior.content === texto))));
        if (llego) {
          setMessages(desdeLaBase(res.messages));
          return;
        }
      } catch {
        // Sin red todavía (el móvil volviendo de otra aplicación): se vuelve a mirar.
      }
      await new Promise(resolver => setTimeout(resolver, ESPERA_CADA_MS));
    }
    if (esperaViva.current !== mia) return;
    setMessages(prev => prev.map(m => (m.esperando ? {
      ...m, esperando: false, error: true, reintentable: true,
      content: '**No llegó la respuesta.** Puede que el turno fallara: vuelve a intentarlo.',
    } : m)));
  }, [sessionId]);

  /**
   * Envía un turno.
   *
   * `historial` permite reescribir la conversación antes de enviar, que es lo
   * que necesitan regenerar (quitar la última respuesta) y editar (cortar por
   * el mensaje editado). Sin eso habría tres copias de esta función.
   */
  const enviarTurno = useCallback(async (
    texto: string,
    conAdjuntos: UploadItem[],
    historial?: Message[],
    ejecutarPlan?: string,
  ) => {
    if (!apiOnline) return;

    const visible = conAdjuntos.length
      ? `${texto}${texto ? '\n\n' : ''}${conAdjuntos.map(a => `📎 ${a.nombre}`).join('\n')}`
      : texto;

    ultimoIntento.current = { texto, adjuntos: conAdjuntos };
    const enviado = texto || 'Mira los archivos adjuntos.';
    const enviadoEn = Date.now();
    const idPregunta = uid();

    const base = historial ?? null;
    setMessages(prev => [
      ...(base ?? prev),
      { id: idPregunta, role: 'user', content: visible, timestamp: new Date() },
    ]);
    setLoading(true);

    const control = new AbortController();
    enVuelo.current = control;
    turnoEnVuelo.current = null;
    setProgreso(null);
    // Si el servidor llegó a empezar el turno: entonces un corte no lo para (4.5).
    let empezo = false;

    const descargas: Descarga[] = [];
    const alEvento = (evento: EventoDelTurno) => {
      // Un turno detenido puede seguir emitiendo un momento: no se pinta nada
      // de uno que ya no es el que está en vuelo.
      if (enVuelo.current !== control) return;
      empezo = true;
      if (evento.tipo === 'inicio' && evento.turno) turnoEnVuelo.current = evento.turno;
      if (evento.tipo === 'archivo_listo' && evento.upload_id) {
        descargas.push({ id: evento.upload_id, nombre: evento.nombre ?? 'archivo', bytes: evento.bytes ?? 0 });
      }
      const frase = fraseDelEvento(evento);
      if (frase) setProgreso(frase);
    };

    try {
      // `/chat/stream` y no `/chat` desde la V2.0.14: cuenta el progreso y, como
      // habla mientras trabaja, el proxy de Vercel no lo corta a los 120 s.
      const res = await morganAPI.chatStream(
        enviado,
        alEvento,
        sessionId,
        temporal,
        conAdjuntos.map(a => a.id),
        control.signal,
        ejecutarPlan,
      );
      ultimoIntento.current = null;
      setMessages(prev => [...prev, {
        id: uid(),
        role: 'assistant',
        content: res.response,
        timestamp: new Date(),
        elapsed: res.elapsed_seconds,
        respaldo: Boolean(res.etapas?.respaldo),
        descargas: descargas.length ? descargas : undefined,
      }]);
    } catch (err) {
      if (err instanceof OperacionCancelada) {
        // Parar no es fallar. Se deja constancia de que el turno se cortó y se
        // devuelve el texto al compositor, porque lo normal después de parar es
        // querer reformular la pregunta.
        setMessages(prev => [...prev, {
          id: uid(),
          role: 'assistant',
          content: '_Respuesta detenida._',
          timestamp: new Date(),
          detenido: true,
        }]);
        setInput(texto);
        setAdjuntos(conAdjuntos);
        ultimoIntento.current = null;
        return;
      }

      // Ya hay un turno en marcha en esta conversación (el que perdió la conexión): este
      // mensaje no se procesó. Vuelve al compositor y se espera la respuesta de aquel.
      if (err instanceof MorganAPIError && err.code === 'TURNO_EN_CURSO') {
        setMessages(prev => prev.filter(m => m.id !== idPregunta));
        setInput(texto);
        setAdjuntos(conAdjuntos);
        void esperarRespuesta(null, enviadoEn, `_${err.message}_`);
        return;
      }
      // La conexión se cortó con el turno ya empezado: sigue en el servidor.
      if (empezo && err instanceof MorganAPIError && SIGUE_EN_EL_SERVIDOR.has(err.code)) {
        void esperarRespuesta(enviado, enviadoEn, AVISO_ESPERANDO);
        return;
      }

      setMessages(prev => [...prev, {
        id: uid(),
        role: 'assistant',
        content: err instanceof MorganAPIError ? `**Error:** ${err.message}` : 'Ocurrió un error inesperado.',
        timestamp: new Date(),
        error: true,
        // Marca el mensaje como reintentable: el texto y los adjuntos siguen
        // guardados, así que reintentar no le cuesta nada al usuario.
        reintentable: true,
      }]);
    } finally {
      enVuelo.current = null;
      setProgreso(null);
      setLoading(false);
      onConversationSaved();
      onTurnoTerminado?.();
    }
  }, [apiOnline, sessionId, temporal, onConversationSaved, onTurnoTerminado, esperarRespuesta]);

  // 4.0 (decisión mía): aprobar un plan es la orden. En cuanto no hay otro turno en
  // vuelo, se manda el que lo ejecuta; los pasos los hace Morgan con lo aprobado.
  // Cada plan, una vez: aunque quien lo pasa no lo borre, no se vuelve a mandar al acabar
  // el turno (visto en su prueba: sin esto, el mismo plan se mandaba en bucle).
  const planesEnviados = useRef(new Set<string>());
  useEffect(() => {
    if (!planAprobado || loading || !apiOnline || planesEnviados.current.has(planAprobado.id)) return;
    planesEnviados.current.add(planAprobado.id);
    onPlanEnviado?.();
    void enviarTurno(`✅ Plan aprobado: ${planAprobado.objetivo}`, [], undefined, planAprobado.id);
  }, [planAprobado, loading, apiOnline, enviarTurno, onPlanEnviado]);

  const send = useCallback(async () => {
    const text = input.trim();
    // Con adjuntos, un mensaje sin texto es legitimo: «mira esto».
    if ((!text && adjuntos.length === 0) || loading || !apiOnline) return;

    const enviados = adjuntos;
    setInput('');
    setAdjuntos([]);
    await enviarTurno(text, enviados);
  }, [input, adjuntos, loading, apiOnline, enviarTurno]);

  /**
   * Corta el turno en curso. La petición se aborta de verdad, no se ignora.
   *
   * Y antes se avisa a la nube (3.4, decisión mía): lo que el turno esté haciendo
   * en el PC se cancela en su siguiente punto seguro. Solo el botón lo hace: cerrar la
   * pestaña o cambiar de aplicación en el móvil no para nada.
   */
  const detener = useCallback(() => {
    const turno = turnoEnVuelo.current;
    if (turno) morganAPI.pararTurno(turno).catch(() => undefined);
    turnoEnVuelo.current = null;
    enVuelo.current?.abort();
  }, []);

  /**
   * Vuelve a intentar lo último que falló, con su texto y sus archivos.
   *
   * Se quita el mensaje de error antes de reintentar: dejarlo ahí convertiría
   * la conversación en un registro de incidencias.
   */
  const reintentar = useCallback(() => {
    const intento = ultimoIntento.current;
    if (!intento || loading) return;

    // El historial se corta por el mensaje del usuario que provocó el error:
    // `enviarTurno` lo vuelve a añadir, así que dejarlo duplicaría la pregunta.
    const limpio = messages.filter(m => !m.error);
    void enviarTurno(intento.texto, intento.adjuntos, limpio.slice(0, -1));
  }, [loading, enviarTurno, messages]);

  /**
   * Pide otra respuesta a la última pregunta.
   *
   * Se corta el historial ANTES de la respuesta que se descarta, de modo que
   * Morgan no la vea en su contexto: si la viera, tendería a repetirla, que es
   * justo lo contrario de lo que se le está pidiendo.
   */
  const regenerar = useCallback(() => {
    if (loading || !apiOnline) return;

    const indice = messages.map(m => m.role).lastIndexOf('user');
    if (indice < 0) return;

    void enviarTurno(messages[indice].content, [], messages.slice(0, indice));
  }, [loading, apiOnline, messages, enviarTurno]);

  /**
   * Reescribe un mensaje anterior y continúa desde ahí.
   *
   * Todo lo que venía después se descarta, incluida la respuesta que ya no
   * corresponde. Conservarlo dejaría en pantalla una conversación que no
   * ocurrió.
   */
  const editarMensaje = useCallback((id: string, nuevo: string) => {
    if (loading || !apiOnline || !nuevo.trim()) return;

    const indice = messages.findIndex(m => m.id === id);
    if (indice < 0) return;

    void enviarTurno(nuevo.trim(), [], messages.slice(0, indice));
  }, [loading, apiOnline, messages, enviarTurno]);

  // El archivo se guarda como adjunto pendiente, no se escribe en el texto:
  // antes se pegaba el identificador en el mensaje y bastaba con que el usuario
  // borrara esa linea para que Morgan perdiera el vinculo.
  const adjuntar = useCallback((archivo: UploadItem) => {
    setAdjuntos(actuales =>
      actuales.some(a => a.id === archivo.id) ? actuales : [...actuales, archivo],
    );
  }, []);

  const quitarAdjunto = useCallback((id: string) => {
    setAdjuntos(actuales => actuales.filter(a => a.id !== id));
  }, []);

  // Soltar archivos sobre la conversacion. Sube por la misma ruta que el clip
  // del compositor, asi que los limites y los rechazos son los mismos.
  const soltar = useSoltarArchivos(adjuntar, !apiOnline || loading);

  /**
   * Pone en el compositor lo que se entendio en la grabacion.
   *
   * Se ANADE a lo que ya hubiera escrito en lugar de sustituirlo: dictar una
   * frase en mitad de un mensaje a medias es un uso normal, y machacar el texto
   * seria perder trabajo del usuario sin avisar.
   */
  const usarTranscripcion = useCallback((texto: string) => {
    if (!texto.trim()) return;
    setInput(actual => (actual.trim() ? `${actual.trimEnd()} ${texto}` : texto));
    document.getElementById('chat-input')?.focus();
  }, []);

  /** Escribe la sugerencia en el compositor y deja el cursor al final. */
  const usarSugerencia = useCallback((prompt: string) => {
    setInput(prompt);
    // El foco va al compositor para poder seguir escribiendo sin pulsar otra
    // vez: varias sugerencias acaban en frase abierta a proposito.
    const campo = document.getElementById('chat-input') as HTMLTextAreaElement | null;
    campo?.focus();
    campo?.setSelectionRange(prompt.length, prompt.length);
  }, []);

  const composer = (
    <Composer
      value={input}
      onChange={setInput}
      onSubmit={send}
      disabled={loading || !apiOnline}
      conectando={conectando}
      ocupado={loading}
      onAdjuntado={adjuntar}
      adjuntos={adjuntos}
      onQuitarAdjunto={quitarAdjunto}
      onTranscrito={usarTranscripcion}
    />
  );

  // Estado inicial: saludo centrado, como en la referencia de diseño.
  if (messages.length === 0) {
    return (
      <div className="chat-view" {...soltar.props}>
        {soltar.encima && <AvisoSoltar subiendo={soltar.subiendo} />}
        <div className="chat-hero">
          {soltar.error && (
            <p className="error-banner" role="alert" onClick={soltar.limpiarError}>
              {soltar.error}
            </p>
          )}

          {/* La insignia de arriba: qué conversación es y dónde corre Morgan. */}
          <div className={`insignia ${temporal ? 'insignia--temporal' : ''}`} role={temporal ? 'status' : undefined}>
            <LogoMorgan
              size={13}
              className={`insignia__logo ${conectando ? 'chat-hero__logo--girando' : ''}`}
            />
            <span className="insignia__fuerte">{temporal ? 'Chat temporal' : 'Morgan'}</span>
            <span className="insignia__sep" aria-hidden="true">·</span>
            <span>
              {temporal
                ? 'No se guarda en el historial ni en la memoria'
                : sistema.entorno === 'cloud' ? 'En la nube' : 'En tu equipo'}
            </span>
          </div>

          <h1 className="saludo">
            {temporal
              ? <>Una conversación <em>de paso</em></>
              : <>¿Qué <em>hacemos</em> hoy?</>}
          </h1>

          <p className="saludo__sub">
            {conectando
              ? 'Un momento, comprobando que Morgan responde…'
              : apiOnline ? pistaConectado(sistema.entorno) : PISTA_DESCONECTADO}
          </p>

          {/* Rellenan el compositor con un texto que se puede editar antes de
              enviarlo. No lanzan nada por su cuenta: pulsar una sugerencia y que
              Morgan empiece a trabajar sin poder mirar lo que va a pedir sería
              un botón que hace más de lo que enseña. */}
          {!temporal && (
            <div className="sugerencias">
              {sugerenciasPara(sistema.entorno).map(s => (
                <button
                  key={s.titulo}
                  className="sugerencia"
                  onClick={() => usarSugerencia(s.prompt)}
                  disabled={!apiOnline}
                  type="button"
                >
                  <span className="sugerencia__cabecera">
                    <span className="sugerencia__icono" aria-hidden="true">{s.icono}</span>
                    <span className="sugerencia__titulo">{s.titulo}</span>
                  </span>
                  <span className="sugerencia__detalle">{s.detalle}</span>
                </button>
              ))}
            </div>
          )}

          <div className="composer-wrapper">
            {composer}
            <p className="composer-footnote">
              Enter para enviar · Shift + Enter, salto de línea · Puedes soltar archivos aquí
            </p>
          </div>
        </div>
      </div>
    );
  }

  // Solo el último mensaje propio se puede editar. Editar uno de más arriba
  // significaría descartar media conversación, y eso pide una confirmación que
  // no compensa: para eso está reformular y volver a preguntar.
  const ultimoDelUsuario = messages.map(m => m.role).lastIndexOf('user');

  return (
    <div className="chat-view" {...soltar.props}>
      {soltar.encima && <AvisoSoltar subiendo={soltar.subiendo} />}
      {temporal && (
        <div className="temporal-bar" role="status">
          Chat temporal · no se guarda en el historial ni en la memoria
        </div>
      )}
      <div className="messages-container">
        <div className="messages-inner">
          {messages.map((msg, indice) => (
            <div
              key={msg.id}
              className={`message ${msg.role}${msg.error ? ' error' : ''}${msg.detenido ? ' detenido' : ''}`}
            >
              {editando === msg.id ? (
                <EditorDeMensaje
                  inicial={msg.content}
                  onGuardar={nuevo => { setEditando(null); editarMensaje(msg.id, nuevo); }}
                  onCancelar={() => setEditando(null)}
                />
              ) : (
                <>
                  <div className="message-content">
                    <MessageContent text={msg.content} />
                  </div>
                  {msg.descargas?.map(d => (
                    <a
                      key={d.id}
                      className="md-descarga"
                      href={`/api/uploads/${d.id}/contenido`}
                      download={d.nombre}
                      aria-label={`Descargar ${d.nombre}`}
                    >
                      {d.nombre}{d.bytes ? ` · ${tamanoCorto(d.bytes)}` : ''}
                    </a>
                  ))}
                  <div className="message-meta">
                    <span>{formatTime(msg.timestamp)}</span>
                    {/* Qué modelo contestó NO se enseña (V2.0.29, decisión
                        mía): a quien usa Morgan le da igual, y es un detalle
                        interno. Queda una sola pista, sin nombres: si contestó
                        el de reserva, el tiempo lo explica al pasar por encima.
                        Sin ella, una respuesta de un minuto porque el principal
                        está agotado se lee como que Morgan se ha roto. */}
                    {msg.elapsed !== undefined && (
                      <span title={msg.respaldo ? 'Tardó más de lo normal: el servicio principal estaba ocupado y contestó el de reserva.' : undefined}>
                        · {msg.elapsed.toFixed(2)} s
                      </span>
                    )}
                  </div>

                  {/* Solo aparecen cuando pueden hacer algo: editar el último
                      mensaje propio, regenerar la última respuesta, reintentar
                      lo que falló. Un botón permanentemente deshabilitado
                      ocupa sitio y no dice nada. */}
                  <div className="acciones-mensaje">
                    {msg.role === 'user' && !loading && indice === ultimoDelUsuario && (
                      <button onClick={() => setEditando(msg.id)} type="button">
                        Editar y reenviar
                      </button>
                    )}
                    {msg.role === 'assistant' && !msg.error && !msg.esperando && !loading
                      && indice === messages.length - 1 && (
                      <button onClick={regenerar} type="button">
                        Regenerar
                      </button>
                    )}
                    {msg.reintentable && !loading && (
                      <button onClick={reintentar} type="button" className="destacada">
                        Reintentar
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          ))}
          {loading && (
            <>
              <TypingIndicator progreso={progreso} />
              <div className="acciones-turno">
                <button onClick={detener} type="button" className="boton-detener">
                  Detener respuesta
                </button>
              </div>
            </>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="composer-area">
        <div className="composer-wrapper">
          {/* Solo cuando se SABE que está caída. Mientras se comprueba no se
              dice nada: un aviso de avería sobre algo que funciona enseña a
              ignorar los avisos. */}
          {estadoApi === 'offline' && (
            <div className="error-banner">Morgan API desconectada. {PISTA_DESCONECTADO}</div>
          )}
          {soltar.error && (
            <div className="error-banner" role="alert" onClick={soltar.limpiarError}>
              {soltar.error}
            </div>
          )}
          {composer}
          <p className="composer-footnote">
            {/* Sin el nombre del modelo (V2.0.29): un aviso útil para
                cualquiera, distinto en la nube y en tu equipo. */}
            {sistema.entorno === 'cloud'
              ? 'Morgan puede equivocarse: comprueba lo importante.'
              : 'Morgan actúa en tu equipo bajo el sistema de permisos. Puede equivocarse: comprueba lo importante.'}
          </p>
        </div>
      </div>
    </div>
  );
}

// ─── Superposición de conexión ───────────────────────────────────────────────
