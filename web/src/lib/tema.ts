/**
 * Tema de la interfaz: azul noche, oscuro, claro o el del sistema.
 *
 * Vive solo en el navegador y no viaja al backend: es una preferencia de quien
 * mira la pantalla, no del agente. Guardarla en Morgan obligaría a una petición
 * antes de poder pintar nada, y provocaría un parpadeo en cada carga.
 *
 * Desde la V2.0.28, `oscuro` es el azul noche del diseño nuevo (antes, lima) y
 * `negro` el oscuro monocromo. Los identificadores se quedan como estaban a
 * propósito: son los que hay guardados en el navegador de quien ya usa Morgan,
 * y renombrarlos devolvería a todo el mundo al tema por defecto sin motivo.
 */

export type Tema = 'oscuro' | 'negro' | 'claro' | 'sistema';

const VALIDOS: readonly Tema[] = ['oscuro', 'negro', 'claro', 'sistema'];

export const TEMAS: { id: Tema; etiqueta: string; descripcion: string }[] = [
  { id: 'oscuro', etiqueta: 'Azul noche', descripcion: 'El de siempre de Morgan' },
  { id: 'negro', etiqueta: 'Oscuro', descripcion: 'Negro y blanco, sin color' },
  { id: 'claro', etiqueta: 'Claro', descripcion: 'Fondo claro' },
  { id: 'sistema', etiqueta: 'Del sistema', descripcion: 'Sigue a tu equipo' },
];

const CLAVE = 'morgan_tema';

export function leerTema(): Tema {
  try {
    const guardado = localStorage.getItem(CLAVE);
    // Se comprueba contra la lista y no con una cadena de comparaciones: al
    // añadir el tema negro, esa cadena habría seguido compilando y devuelto el
    // tema por defecto a quien lo eligiera, sin que nada fallara.
    if (VALIDOS.includes(guardado as Tema)) {
      return guardado as Tema;
    }
  } catch {
    // Modo privado o almacenamiento bloqueado: se usa el valor por defecto.
  }
  return 'oscuro';
}

export function guardarTema(tema: Tema): void {
  try {
    localStorage.setItem(CLAVE, tema);
  } catch {
    // Sin almacenamiento, el tema vale solo para esta carga.
  }
}

/**
 * Escribe el tema efectivo en el elemento raíz.
 *
 * Devuelve una funcion de limpieza: con 'sistema' hay que seguir escuchando los
 * cambios de preferencia del sistema operativo, no basta con mirarla una vez.
 */
export function aplicarTema(tema: Tema): () => void {
  const raiz = document.documentElement;

  if (tema !== 'sistema') {
    raiz.dataset.tema = tema;
    return () => {};
  }

  // 'sistema' elige entre claro y azul noche. El oscuro no entra aquí porque el
  // sistema operativo solo dice «claro u oscuro», y cuál de los dos oscuros
  // prefieres es precisamente lo que no puede saber.
  const consulta = window.matchMedia('(prefers-color-scheme: light)');
  const sincronizar = () => { raiz.dataset.tema = consulta.matches ? 'claro' : 'oscuro'; };

  sincronizar();
  consulta.addEventListener('change', sincronizar);
  return () => consulta.removeEventListener('change', sincronizar);
}
