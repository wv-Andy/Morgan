# Morgan's architecture

**English** · [Español](arquitectura.es.md)

> The real architecture, checked against the code. It brings together what used to be the
> architecture, the online/local/degraded mode and the architectural review of 2.2. Last
> review: 2026-09-25, V3.5.0.

A personal AI agent that talks in natural language and takes real actions under permissions
and audit. It runs in two environments with different capabilities and degrades in a
controlled way.

> **Online when it can, local when it must, and degraded when it can't do something.**

## 1. Overview

```
   CLI (Rich)                Web interface (React, Vercel)
        │                            │  /api/* (same-origin proxy)
        │                     REST API (FastAPI, Render)
        │                            │
        │                   Identity and workspace (ContextVar)
        └─────────────┬──────────────┘
                      ▼
                 Agent (Core) ── a single agent for every user
                      │
   ┌──────────┬───────┴───────┬──────────────┬──────────────┐
   ▼          ▼               ▼              ▼              ▼
LLMProvider  ToolRegistry  Permission-   Repositories   ServiceHealth
+ Fallback   (per env.)     Manager       (interface)    (status)
   │              │         + Validator        │
Groq · Gemini ·  49 / 29    + AuditLogger ┌────┴────┐
OpenAI           tools                    SQLite  Supabase
```

**No arrow goes from the Core to a concrete technology: always to an interface.** The CLI and
the API are built on the **same** container (`CoreContainer` in `src/api/dependencies.py`).

## 2. Layers

| Layer | Where | Detail |
|---|---|---|
| **Core** | `src/agent/core.py` | Multi-turn loop: asks the model, validates arguments, checks permissions, executes, verifies and feeds back. Caps by iterations **and by clock** → [agente.md](agente.md) |
| **Models** | `src/models/` | `LLMProvider`, a chain with capabilities, quota, keyring and failover → [modelos.md](modelos.md) |
| **Tools** | `src/tools/` | `Tool` + `ToolRegistry`, 8 domains. Each declares its risk, `requires_local` and, if it acts outside Morgan, `exige_plan` (requires a plan) → [capacidades.md](capacidades.md) |
| **Security** | `src/security/` | 5 risk levels, validation of paths and commands, audit of **every attempt** → [seguridad.md](seguridad.md) |
| **Identity** | `src/identidad/`, `src/api/identidad_middleware.py` | The request's user and role in a `ContextVar`, set in the middleware → [autenticacion.md](autenticacion.md) |
| **Data** | `src/memory/`, `src/conocimiento/`, `src/espacios/` | SQLite and Supabase repositories; no reference to `sqlite3` or the HTTP client outside them → [datos.md](datos.md) |
| **Tasks and files** | `src/tasks/`, `src/uploads/` | Tasks, plans, verification; uploaded files → [agente.md](agente.md), [capacidades.md](capacidades.md#5-files-images-and-voice) |
| **Integrations** | `src/integraciones/` | OAuth with an encrypted token → [integraciones.md](integraciones.md) |
| **Status** | `src/health.py`, `src/api/health_checks.py` | One entry per dependency, never blocks (below) |
| **API** | `src/api/` | Factory, routes, CORS, uniform errors, degraded mode → [api.md](api.md) |
| **Web** | `web/` | → [web.md](web.md) |
| **Configuration** | `src/config.py`, `.env.example` | Loaded and validated once; an invalid value falls back to the default with a warning. Every variable, in [`.env.example`](../.env.example) |

### What belongs to "this turn" lives in a `ContextVar`

There is **a single agent** and a single registry for every user. That's why the user, the
workspace, the measurement and the turn's event channel live in context variables, and the
turn's thread receives them with `copy_context()`. Passing them as parameters would let a new
function forget them; storing them in the agent would make Ana's things appear in Bea's turn.
A workspace's instructions go in the prompt **of the turn**, never in the agent's.

## 3. The two environments

`MORGAN_ENVIRONMENT`:

| | `local` (default) | `cloud` |
|---|---|---|
| Where it runs | Your computer | Render |
| Tools | All 49 | The ones that don't touch the machine, **plus the ones of the person's PC** while their local agent is connected |
| Storage | SQLite | Supabase |
| Accounts | Not required | Yes |

**In `cloud`, the tools with `requires_local=True` aren't registered**, rather than "blocked by
permissions": what isn't registered can't be invoked and doesn't appear in the schema the
model sees ([ADR-013](decisions.md)). `requires_local` is `True` by default: forgetting to
classify a tool leaves it out of the cloud.

**Morgan in the cloud doesn't touch your PC on its own.** Since V3.0 it can **ask the local
agent** that the person installs on their computer, and it's the agent that decides: it has
its own credential, its folder policy and its audit, and it treats the cloud as untrusted
(agente-local.md, in Spanish). In V3.1 those capabilities were reading,
searching and **bringing a copy of a file to download**; writing arrived in 3.3, and the rest
of the desktop in 4.6-4.13.

## 4. Status per service and degradation

There is no `internet = yes/no`: there is one entry per dependency (`internet`,
`database.local`, `database.remote`, `llm`, `correo` — email), with the states `AVAILABLE`,
`DEGRADED`, `UNAVAILABLE` and `UNKNOWN`.

- **Checking the status never blocks**: the last value is read, with a 30 s TTL.
- **A service that is down isn't hammered**: exponential backoff up to 5 minutes.
- **The LLM isn't checked with a real call** (it would cost money): what was observed in use
  is reported, and if there were failovers, it shows `DEGRADED`.

| Situation | Behavior |
|---|---|
| Slow or dead provider | Cuts at 30 s and fails over to the next one |
| Every provider down | Controlled error; whatever doesn't need a model keeps working |
| No internet | The local part works; whatever goes out to the network gives a controlled error |
| Database down | Morgan starts; `/status` says so |
| Turn too long | Stops with a clear message; in the cloud the request answers at 100 s and the turn continues |

The web's **Status** view translates this into capabilities in plain language, with the
reason when something isn't available in the environment.

## 5. The flow of a request

```
Request → middleware: token, user, CSRF, workspace, measurement
  → Agent: recent window of the history (filtered by user)
  → model (turn prompt + memory + date) → tools?
       yes → validate → permissions / plan → execute → verify → repeat
       no  → answer
  → persist the turn → answer (or events on /chat/stream)
```

## 6. Principles

1. **The Core depends on interfaces, never on providers.**
2. **The internet improves Morgan, it doesn't condition it.**
3. **Local writes never wait for the remote.**
4. **Fail-safe by default.** An unclassified tool stays out of the cloud; without a console,
   permissions deny; an unknown risk is critical; a model without declared capabilities gets
   text only.
5. **What hasn't been checked isn't taken as good.**
6. **An external failure doesn't bring Morgan down.**
7. **Isolation between users is put in one place, not in every query.**
8. **A single path for each thing**: one tool catalog, one `fetch` in the web, one scoring
   function for knowledge. Two copies drift apart on their own.

## 7. The architectural review of 2.2 (V2.0.11)

It was done by measuring, not by reading in search of something to improve: size per module,
repeated names and blocks (with a home-made detector that normalizes whitespace), `vulture`,
`ruff` and coverage (**91.4 %**).

- **The console had a copied catalog with 12 fewer tools** (knowledge, plans and GitHub), and
  the test that should have seen it compared in the wrong direction. Now the console is built
  on the same container (`tests/test_consola.py`).
- **The PostgREST filters were copied** in the cloud knowledge store; `_esc` is what prevents
  slipping in someone else's filter, and two copies could drift apart.
- **Code nobody called**, checked by hand one by one: **285 fewer lines** in `src/`, without
  changing what Morgan does.
- **The two SQLite memories** weren't unified then because they didn't behave the same; it was
  done in 2.0.13 with the test first, and a defect showed up
  ([datos.md](datos.md#3-memory-the-five-types)).

Left alone on purpose: the repeated properties of each `Tool` (that's the shape of the
interface) and the similarity between repositories (two dialects of the same thing).

## Tests

`pytest`, **none disabled**. How many there are is what `pytest -q` says; writing it here would
only guarantee that one day it's wrong. Every defect found leaves a test that pins it, and
every new test is verified by breaking the code on purpose (mutations).
