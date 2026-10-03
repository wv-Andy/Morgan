import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

/**
 * Barrera de errores de render.
 *
 * Sin ella, cualquier excepción durante el render desmonta todo el árbol y el
 * usuario se queda con una página en blanco, sin ninguna pista de qué pasó ni
 * forma de recuperarse. Con ella, el fallo queda acotado: se muestra qué ocurrió
 * y se ofrece continuar sin recargar.
 */

interface Props {
  children: ReactNode;
  /** Nombre de la zona, para que el aviso diga qué parte falló. */
  area?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Queda en la consola para poder diagnosticarlo.
    console.error('Fallo de render en Morgan:', error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="render-error">
        <h2>Algo falló al mostrar {this.props.area ?? 'esta vista'}</h2>
        <p>
          El resto de Morgan sigue funcionando. Puedes reintentar sin perder la sesión.
        </p>
        <code className="render-error-detail">{error.message}</code>
        <div className="render-error-actions">
          <button className="btn-primary" onClick={this.reset}>Reintentar</button>
          <button className="header-action" onClick={() => window.location.reload()}>
            Recargar la página
          </button>
        </div>
      </div>
    );
  }
}
