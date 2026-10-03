/// <reference types="vite/client" />

/**
 * El commit con el que se construyo esta version, inyectado por Vite.
 *
 * Lo pone `define` en vite.config.ts desde VERCEL_GIT_COMMIT_SHA. En un build
 * local vale 'local'. Sirve para responder «¿que hay desplegado?» sin comparar
 * huellas de ficheros a ojo.
 */
declare const __MORGAN_COMMIT__: string;
