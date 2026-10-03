/**
 * Qué está haciendo Morgan, dicho en una frase (V2.0.14).
 *
 * Traduce los eventos de `/chat/stream` a lo que se enseña bajo el indicador de
 * escritura. Nombres técnicos como `search_web` no le dicen nada a quien espera;
 * «Buscando en internet…» sí.
 *
 * Aparte del componente para poder probarlo sin pintar nada.
 */

import type { EventoDelTurno } from './api';

const POR_HERRAMIENTA: Record<string, string> = {
  search_web: 'Buscando en internet…',
  read_webpage: 'Leyendo la página…',
  recall_memory: 'Consultando lo que recuerda de ti…',
  remember_fact: 'Guardándolo en la memoria…',
  forget_fact: 'Olvidando lo que le pediste…',
  search_knowledge: 'Buscando en tus documentos…',
  add_knowledge: 'Guardando el documento…',
  index_document: 'Preparando el documento para consultarlo…',
  list_knowledge_sources: 'Mirando tus documentos…',
  list_uploads: 'Mirando tus archivos…',
  copy_file: 'Trayendo el archivo de tu PC…',
  create_file: 'Creando el archivo en tu PC…',
  edit_file: 'Editando el archivo en tu PC…',
  create_folder: 'Creando la carpeta en tu PC…',
  move_file: 'Moviendo en tu PC…',
  delete_file: 'Esperando a que lo confirmes en tu PC…',
  run_command: 'Consultando en tu PC…',
  run_change_command: 'Esperando a que lo confirmes en tu PC…',
  get_processes: 'Mirando los procesos de tu PC…',
  kill_process: 'Esperando a que lo confirmes en tu PC…',
  read_upload: 'Leyendo el archivo…',
  analyze_image: 'Mirando la imagen…',
  transcribe_audio: 'Escuchando el audio…',
  create_plan: 'Preparando un plan…',
  get_plan: 'Consultando el plan…',
  create_task: 'Organizando la tarea…',
  verify_step: 'Comprobando que salió bien…',
};

/**
 * La frase para un evento, o `null` si no cambia lo que se está enseñando.
 *
 * El latido devuelve `null` a propósito: dice que Morgan sigue vivo, no que haga
 * algo nuevo. Cambiar el texto cada 10 s sin que pase nada sería inventarse
 * progreso.
 */
export function fraseDelEvento(evento: EventoDelTurno): string | null {
  switch (evento.tipo) {
    case 'inicio':
      return 'Pensando…';
    case 'pensando':
      return (evento.vuelta ?? 1) > 1 ? 'Pensando con lo que ha encontrado…' : 'Pensando…';
    case 'herramienta': {
      if (evento.estado !== 'empieza') return null;
      const nombre = evento.nombre ?? '';
      if (POR_HERRAMIENTA[nombre]) return POR_HERRAMIENTA[nombre];
      if (nombre.startsWith('github_')) return 'Consultando GitHub…';
      if (nombre.endsWith('_task')) return 'Actualizando la tarea…';
      return 'Trabajando en ello…';
    }
    case 'archivo_listo':
      // El botón lo pinta el chat; aquí no hay nada nuevo que contar.
      return null;
    case 'equipo':
      // Cómo va la orden en el PC (3.4). «En marcha» no cambia nada: ya se enseña
      // la frase de la herramienta.
      if (evento.estado === 'PENDING') {
        const delante = (evento.posicion ?? 1) - 1;
        return delante > 0 ? `En cola en tu PC (${delante} delante)…` : 'En cola en tu PC…';
      }
      if (evento.estado === 'CANCEL_REQUESTED') return 'Cancelando en tu PC…';
      if (evento.estado === 'SIN_VUELTA') return 'Ya estaba hecho en tu PC: no se pudo parar.';
      return null;
    case 'respaldo':
      // Con el principal agotado un turno tarda más, y sin decirlo parece que
      // Morgan se ha atascado.
      return 'Contesta el modelo de respaldo…';
    default:
      return null;
  }
}
