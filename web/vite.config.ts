// Morgan Web UI — Vite Config (V1.1)
//
// SOLO del build. La configuracion de las pruebas vive en vitest.config.ts, y
// no aqui, por un motivo concreto: vitest arrastra su propia copia de Vite, y
// mezclar las dos en este fichero hacia que `tsc -b` fallara por tipos
// incompatibles. Eso no rompe las pruebas: rompe `npm run build`, que es lo que
// ejecuta Vercel al desplegar.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Que version esta desplegada, dicho por el propio build.
  //
  // Existe porque durante toda una tarde la pregunta «¿esta ya desplegado el
  // arreglo?» se respondio comparando huellas de ficheros y adivinando. Vercel
  // compila con sus propias variables, asi que el nombre del bundle no coincide
  // con el de un build local y no sirve para comparar.
  //
  // VERCEL_GIT_COMMIT_SHA lo define Vercel al construir. En local no existe y
  // vale 'local', que es la respuesta correcta ahi.
  define: {
    __MORGAN_COMMIT__: JSON.stringify(
      (process.env.VERCEL_GIT_COMMIT_SHA ?? '').slice(0, 7) || 'local',
    ),
    // En Vercel, la API se alcanza SIEMPRE por el mismo origen, a traves del
    // proxy de vercel.json. El valor que haya en el panel se ignora a
    // proposito, y conviene entender por que antes de «arreglarlo».
    //
    // Con la API en otro dominio, la cookie de sesion es una cookie DE
    // TERCEROS. Safari en iPhone la descarta sin excepcion —ITP lleva anos
    // asi— y Firefox movil la aisla por sitio, con el mismo efecto. El sintoma
    // es exacto: te registras, el servidor manda la cookie, el navegador la
    // tira, y la web te devuelve al login para siempre.
    //
    // No es configuracion: por muy bien puesta que este la URL de Render, el
    // navegador no va a guardar esa cookie. Lo unico que lo arregla es que la
    // API responda desde el mismo origen que la pagina.
    //
    // Se ignora el panel en lugar de pedir que se cambie ahi porque olvidarlo
    // deja el fallo intacto y sin ninguna senal de por que.
    'import.meta.env.VITE_MORGAN_API_URL': JSON.stringify(
      process.env.VERCEL ? '/api' : process.env.VITE_MORGAN_API_URL,
    ),
  },
  server: {
    port: 5173,
    open: true,
    proxy: {
      // En desarrollo, redirige /api/* a Morgan API en puerto 8000
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
