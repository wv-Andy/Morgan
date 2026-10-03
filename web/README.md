# Morgan — interfaz web

React 19 + TypeScript + Vite. Se publica en Vercel y habla con el backend por `/api`.

```bash
npm ci
npm run dev      # desarrollo, contra la API local en 127.0.0.1:8000
npm run build    # lo que ejecuta Vercel (empieza por tsc -b)
npm test         # Vitest
npm run lint     # oxlint
```

Diseño, decisiones y pruebas: [docs/web.md](../docs/web.md).
