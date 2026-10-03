import { useRef, useState } from 'react';

/**
 * Un texto para pegar en otro sitio (una orden de PowerShell, un ejemplo de la API), con su
 * botón de copiar (4.17). Sin permiso para el portapapeles, lo deja seleccionado para
 * copiarlo a mano.
 */
export function ParaCopiar({ texto, etiqueta }: { texto: string; etiqueta: string }) {
  const [estado, setEstado] = useState<'' | 'si' | 'a-mano'>('');
  const campo = useRef<HTMLElement>(null);

  async function copiar() {
    try {
      await navigator.clipboard.writeText(texto);
      setEstado('si');
    } catch {
      const rango = document.createRange();
      if (campo.current) {
        rango.selectNodeContents(campo.current);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(rango);
      }
      setEstado('a-mano');
    }
  }

  return (
    <div className="para-copiar">
      <code className="ajustes-orden" ref={campo}>{texto}</code>
      <button type="button" className="ajustes-boton" onClick={() => void copiar()} aria-label={etiqueta}>
        {estado === 'si' ? 'Copiado' : 'Copiar'}
      </button>
      {estado === 'a-mano' && <small>Ya está seleccionado: cópialo con Ctrl+C.</small>}
    </div>
  );
}
