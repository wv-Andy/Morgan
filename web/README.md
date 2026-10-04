# Morgan — web interface

**English** · [Español](README.es.md)

React 19 + TypeScript + Vite. It's published on Vercel and talks to the backend through `/api`. The
interface is in Spanish.

```bash
npm ci
npm run dev      # development, against the local API at 127.0.0.1:8000
npm run build    # what Vercel runs (it starts with tsc -b)
npm test         # Vitest
npm run lint     # oxlint
```

Design, decisions and tests: [docs/web.md](../docs/web.md).
