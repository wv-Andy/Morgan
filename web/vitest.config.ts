// Morgan Web UI — Configuración de las pruebas
//
// **Fichero aparte de `vite.config.ts` a propósito.**
//
// Lo natural sería añadir un bloque `test` a la configuración de Vite, y es lo
// que se intentó primero. No funciona aquí: vitest trae su propia copia de Vite
// anidada, distinta de la del proyecto —Vite 8, sobre rolldown—, y al mezclar
// las dos en el mismo fichero `tsc -b` falla por tipos incompatibles.
//
// Lo grave de ese fallo no es que no corrieran las pruebas: es que `tsc -b` es
// el primer paso de `npm run build`, que es lo que ejecuta Vercel. Un error de
// tipos en la configuración de las pruebas dejaba la web sin desplegar.
//
// Con dos ficheros, cada herramienta lee el suyo y no se pisan. Este no entra
// en ningún `tsconfig`, así que no participa en el build; vitest lo interpreta
// por su cuenta.

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  // Lo que en el build define `vite.config.ts`. Sin esto, cualquier componente
  // que enseñe la versión desplegada (Ajustes → Acerca de) revienta en pruebas.
  define: { __MORGAN_COMMIT__: JSON.stringify('pruebas') },
  test: {
    // jsdom porque lo que se prueba son hooks y lógica de cliente: hay
    // `window`, `document` y eventos, pero no se pinta nada en una pantalla.
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
