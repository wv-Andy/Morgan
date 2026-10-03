import type { ReactNode } from 'react';

/**
 * Renderizador de Markdown para las respuestas de Morgan.
 *
 * Construye nodos de React, **nunca** HTML: el texto viene del modelo y puede
 * arrastrar contenido web no confiable leído por `read_webpage`. Usar
 * `dangerouslySetInnerHTML` aquí sería reintroducir el XSS corregido en la V1.0.
 *
 * Cubre lo que Morgan usa de verdad en sus respuestas: tablas, listas, bloques
 * de código, encabezados, negrita y código en línea. No pretende ser un parser
 * completo de Markdown; traer una librería entera para esto sería desproporcionado.
 */

// --- Formato dentro de una línea ---------------------------------------------

/**
 * Un enlace de descarga de Morgan, y **solo** eso (3.1-E).
 *
 * El chat no dibuja enlaces, a propósito: el texto lo escribe el modelo, que puede
 * haber leído una página o un archivo con instrucciones escondidas, y convertir en
 * botón cualquier dirección que escriba sería regalar un sitio donde poner un enlace
 * falso con la cara de Morgan.
 *
 * La excepción es lo que sirve este mismo servidor como descarga —la copia de un
 * archivo del PC—, que es una ruta fija y propia. Cualquier otra dirección se sigue
 * enseñando como texto.
 */
const DESCARGA = /^\/api\/uploads\/[A-Za-z0-9_-]{1,64}\/contenido$/;

/** La ruta de descarga, escrita como ruta o como dirección completa de ESTA web. */
function esDescarga(url: string): boolean {
  if (DESCARGA.test(url)) return true;
  try {
    const dir = new URL(url, window.location.origin);
    return dir.origin === window.location.origin && DESCARGA.test(dir.pathname);
  } catch {
    return false;
  }
}

function inline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|\[[^\]\n]+\]\([^)\s]+\))/g);

  return parts.map((part, i) => {
    const key = `${keyPrefix}-${i}`;

    const enlace = /^\[([^\]\n]+)\]\(([^)\s]+)\)$/.exec(part);
    if (enlace) {
      const [, etiqueta, url] = enlace;
      if (esDescarga(url)) {
        return (
          <a key={key} className="md-descarga" href={url} download>
            {etiqueta}
          </a>
        );
      }
      return <span key={key}>{`${etiqueta} (${url})`}</span>;
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={key} className="inline-code">{part.slice(1, -1)}</code>;
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    return <span key={key}>{part}</span>;
  });
}

// --- Tablas -------------------------------------------------------------------

function isTableSeparator(line: string): boolean {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes('-');
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map(c => c.trim());
}

function Table({ rows, id }: { rows: string[][]; id: string }) {
  const [head, ...body] = rows;

  return (
    // El contenedor permite desplazar una tabla ancha sin romper el ancho del chat.
    <div className="md-table-wrap">
      <table className="md-table">
        <thead>
          <tr>{head.map((c, i) => <th key={`${id}-h-${i}`}>{inline(c, `${id}-h-${i}`)}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((fila, r) => (
            <tr key={`${id}-r-${r}`}>
              {fila.map((c, i) => <td key={`${id}-r-${r}-${i}`}>{inline(c, `${id}-c-${r}-${i}`)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Documento ----------------------------------------------------------------

export function Markdown({ text }: { text: string }) {
  const lines = (text ?? '').split('\n');
  const bloques: ReactNode[] = [];

  let i = 0;
  let n = 0;

  const parrafo: string[] = [];
  const volcarParrafo = () => {
    if (parrafo.length === 0) return;
    const contenido = parrafo.join('\n');
    bloques.push(
      <p key={`p-${n++}`} className="md-p">{inline(contenido, `p-${n}`)}</p>,
    );
    parrafo.length = 0;
  };

  while (i < lines.length) {
    const linea = lines[i];

    // Bloque de código
    if (linea.trim().startsWith('```')) {
      volcarParrafo();
      const lenguaje = linea.trim().slice(3).trim();
      const codigo: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codigo.push(lines[i]);
        i++;
      }
      i++; // cierre
      bloques.push(
        <pre key={`code-${n++}`} className="md-code">
          {lenguaje && <span className="md-code-lang">{lenguaje}</span>}
          <code>{codigo.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    // Tabla: una fila con barras seguida de un separador
    if (linea.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      volcarParrafo();
      const filas: string[][] = [splitRow(linea)];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        filas.push(splitRow(lines[i]));
        i++;
      }
      bloques.push(<Table key={`t-${n++}`} rows={filas} id={`t-${n}`} />);
      continue;
    }

    // Encabezado
    const encabezado = linea.match(/^(#{1,4})\s+(.*)$/);
    if (encabezado) {
      volcarParrafo();
      const nivel = encabezado[1].length;
      bloques.push(
        <div key={`h-${n++}`} className={`md-h md-h${nivel}`}>{inline(encabezado[2], `h-${n}`)}</div>,
      );
      i++;
      continue;
    }

    // Lista (con viñetas o numerada)
    if (/^\s*([-*+]|\d+\.)\s+/.test(linea)) {
      volcarParrafo();
      const numerada = /^\s*\d+\./.test(linea);
      const elementos: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        elementos.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''));
        i++;
      }
      const Etiqueta = numerada ? 'ol' : 'ul';
      bloques.push(
        <Etiqueta key={`l-${n++}`} className="md-list">
          {elementos.map((el, k) => <li key={`l-${n}-${k}`}>{inline(el, `l-${n}-${k}`)}</li>)}
        </Etiqueta>,
      );
      continue;
    }

    // Línea en blanco: cierra el párrafo
    if (!linea.trim()) {
      volcarParrafo();
      i++;
      continue;
    }

    parrafo.push(linea);
    i++;
  }

  volcarParrafo();

  return <div className="md">{bloques}</div>;
}
