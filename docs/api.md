# Morgan API (REST)

**English** · [Español](api.es.md)

A FastAPI service that exposes Morgan's Core. Interactive documentation at `/docs` (Swagger) and
`/redoc`; OpenAPI schema at `/openapi.json`. Route names and error codes are in Spanish, like
the code.

## Starting it

```bash
python -m src.api.server
# or
uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Configurable with `MORGAN_API_HOST`, `MORGAN_API_PORT`, `MORGAN_API_RELOAD` and
`MORGAN_CORS_ORIGINS` (by default only Vite's development server, not `*`).

## Endpoints

| Method | Route | Description |
|---|---|---|
| `GET` | `/health` | Basic health check (status, version, timestamp). |
| `GET` | `/status` | Diagnosis per subsystem: core, database, tools, permissions, llm. Says whether the mode is `normal` or `degraded`. |
| `POST` | `/chat` | Sends a message to the agent and runs the reasoning loop. |
| `POST` | `/chat/stream` | The same, reporting progress as it happens: one JSON line per event (NDJSON). It's what the web uses. |
| `POST` | `/chat/parar` | The «Stop» button (3.4): with the `turno` from the `inicio` event, cancels what that turn is doing on the person's PC, at its next safe point, and lets no new orders go out to the PC. Only its owner can stop it. The turn itself finishes and is saved. |
| `GET` | `/tools` | Tool catalog; optional filter `?category=`. |
| `GET` | `/tools/{name}` | Metadata and JSON Schema of a tool. |
| `POST` | `/tools/{name}` | Runs a tool directly (goes through permissions and audit). |
| `GET` | `/memory` | Lists memories; filters `?query=` and `?category=`. |
| `POST` | `/memory` | Saves or updates a memory. |
| `DELETE` | `/memory/{key}` | Deletes a memory by key. |
| `GET` | `/audit` | Audit records; `?limit=` between 1 and 500 (50 by default). |
| `GET` | `/sessions` | Lists conversations by recency; `?limit=` and `?offset=`. |
| `POST` | `/sessions` | Creates a conversation; generates the id if none is given. |
| `GET` | `/sessions/{id}` | Detail of a conversation, with its number of messages. |
| `GET` | `/sessions/{id}/messages` | The most recent messages, in chronological order. |
| `DELETE` | `/sessions/{id}` | Deletes the conversation and all its messages. |
| `PATCH` | `/sessions/{id}` | Renames, archives, pins or moves it to another workspace (`espacio_id`; `""` is General). What isn't sent isn't touched. `404 ESPACIO_DESTINO_NO_ENCONTRADO` if the workspace isn't yours. |
| `GET` | `/espacios` | The workspaces of whoever asks, with `max_instrucciones`. |
| `POST` | `/espacios` | Creates one (`nombre`, `instrucciones`). `409 ESPACIO_DUPLICADO` if the name already exists. |
| `GET` | `/espacios/{id}` | One workspace. |
| `PATCH` | `/espacios/{id}` | Changes name or instructions. |
| `DELETE` | `/espacios/{id}` | Deletes it; its conversations, files and documents go back to General. |
| `GET` | `/settings` | The user's profile and preferences. |
| `PUT` | `/settings` | Saves them. An empty field **deletes** that data. |
| `POST` | `/uploads` | Uploads a file. `422` with the reason if it's rejected. |
| `GET` | `/uploads` | Lists what was uploaded, with the limits in force. |
| `GET` | `/uploads/{id}/contenido` | Downloads the file as it is (3.1-E): always as an attachment and with `nosniff`, only your own. |
| `DELETE` | `/uploads/{id}` | Deletes an uploaded file, index and content. |
| `GET` | `/tasks` | Lists the tasks; `?session_id=` and `?solo_activas=`. |
| `GET` | `/tasks/{id}` | Detail with steps, progress and result. |
| `POST` | `/tasks/{id}/cancel` | Cancels a task. `409` if it had already finished. |
| `POST` | `/tasks/{id}/retry` | Retries a failed or cancelled one. `409` if it doesn't apply. |
| `DELETE` | `/tasks/{id}` | Deletes a task. |
| `POST` | `/auth/registro` | Creates an account and leaves the session signed in. |
| `POST` | `/auth/login` | Signs in. `401` if it fails; **`429`** if there is a lockout because of attempts. |
| `POST` | `/auth/logout` | Closes the session on the server and deletes the cookies. |
| `GET` | `/auth/yo` | Who is making the request. **It never returns 401.** |
| `POST` | `/auth/recuperar` | Asks for the recovery link. Answers the same whether the account exists or not. |
| `POST` | `/auth/restablecer` | Changes the password with the token from the email. |
| `POST` | `/auth/password` | Changes the password knowing the current one. |
| `GET` | `/auth/sesiones` | Where there are open sessions. |
| `POST` | `/auth/sesiones/cerrar-otras` | Closes the other sessions. |
| `POST` | `/auth/verificar` | Confirms the email with the link's token. |
| `POST` | `/auth/verificar/reenviar` | Sends the verification link again. |
| `GET` | `/auth/datos` | Exports everything Morgan stores about you. |
| `DELETE` | `/auth/cuenta` | Deletes the account and its data. Requires the password. |
| `GET` | `/auth/tokens` | Your live API tokens, without their value. Only with a session. |
| `POST` | `/auth/tokens` | Creates a token (`nombre`, `alcances`, `dias`) and returns its value **only once**. Only with a session. |
| `DELETE` | `/auth/tokens/{id}` | Revokes a token: it stops working on the next request. |
| `POST` | `/auth/tokens/revocar-todos` | Revokes all of yours. |
| `GET` | `/auth/permiso-automatico` | Whether green and yellow are approved on their own (4.6). Only with the web's session. |
| `PUT` | `/auth/permiso-automatico` | Turns it on or off (`{"encendido": true}`); it stays in the audit. |
| `POST` | `/auth/agentes/codigo` | A code to pair a PC (local agent, 3.0): 10 minutes, single use. Only with a session. |
| `GET` | `/auth/agentes` | Your paired computers, without their credential. Since 3.7, for each one whether it's **connected now** and which capabilities it offers |
| `DELETE` | `/auth/agentes/{id}` | Revokes a computer: it can't connect again. |
| `POST` | `/auth/agentes/{id}/abrir-ajustes` | Opens «Morgan en tu PC» on that computer (4.17). **It changes nothing**: the policy is changed on the PC. `409 NO_CONECTADO` if it isn't connected (or belongs to another person) and `409 AGENTE_ANTIGUO` if its agent is older than 4.17 |
| `GET` | `/auth/agentes/{id}/ordenes` | The order history of one of your computers (3.7): what was asked, when, how it ended and how long it took. **Never arguments or content.** `?dias=` up to 30. Someone else's, 404 |
| `POST` | `/agente/emparejar/consultar` | Called by the agent: which account a code would pair with. Doesn't use it up. |
| `POST` | `/agente/emparejar/confirmar` | Called by the agent: exchanges the code for its `agent_id` and its credential (`mga_…`, only once). |
| `POST` | `/agente/desemparejar` | The agent revokes itself, with its credential in `Authorization`. |
| `GET` | `/agente/historial` | The agent asks for **its** history (3.7), with its credential, to cross-check it with its journal (`python -m src.agente cruzar`). `?horas=` |
| `POST` | `/agente/rotar` | The agent asks for a new credential with the current one (3.8). It stays **pending**: the old one is valid until it connects with the new one |
| `GET` | `/agente/actualizacion` | The latest published version of the agent: the manifest **exactly as it was signed** and my signature (3.8). With the agent's credential or `X-Morgan-Codigo` (a live pairing code, which isn't used up) |
| `GET` | `/agente/paquete` | That version's package (zip). Same credentials. The cloud only hosts it: the agent checks it |
| `GET` | `/agente/instalar.ps1` | The installer for Windows (3.8). Public: it carries no secrets and does nothing without a live code |
| `GET` | `/admin/yo/permisos` | Which permissions whoever asks has. |
| `GET` | `/admin/usuarios` | Lists the accounts. Requires `users.read`. |
| `POST` | `/admin/usuarios/{user_id}/rol` | Changes the role. Nobody elevates themselves. |
| `POST` | `/admin/usuarios/{user_id}/estado` | Activates or suspends an account. |
| `GET` | `/integraciones` | Connectable services and their status. **Doesn't require an account**, so the panel can explain itself. |
| `POST` | `/integraciones/{servicio}/conectar` | Issues the `state` and returns where to go. Requires an account. |
| `GET` | `/integraciones/{servicio}/callback` | The way back from the external service. Open, and the `state` says whose it is. |
| `DELETE` | `/integraciones/{servicio}` | Disconnects and asks the service to invalidate the token. |
| `GET` | `/planes` | The conversation's plans; `?session_id=`. |
| `GET` | `/planes/{plan_id}` | Detail of a plan with its steps. |
| `POST` | `/planes/{plan_id}/aprobar` | Approves a plan so it runs. |
| `POST` | `/planes/{plan_id}/rechazar` | Rejects it, with an optional reason. |
| `GET` | `/automatizaciones` | Your automations (4.14): what they do, when the next run is and how the last one went. **There is no route to create them**: they're asked for in the chat, with a red plan you approve. |
| `POST` | `/automatizaciones/{automatizacion_id}/pausar` | Pauses it. |
| `POST` | `/automatizaciones/{automatizacion_id}/reanudar` | Resumes it **from now on** (what was due while it was paused isn't recovered); `409 DEMASIADAS` with 10 active. |
| `DELETE` | `/automatizaciones/{automatizacion_id}` | Deletes it. |
| `GET` | `/avisos` | The tray: what each run reported (done, didn't work or skipped) and how many are unread. |
| `GET` | `/avisos/sin-leer` | Just the unread number, for the navigation (the web checks it every minute). |
| `POST` | `/avisos/leidos` | Marks the ones in `ids` as read, or all of them without `ids`. |
| `POST` | `/automatizaciones/reloj` | **Called by Supabase's clock** (`pg_cron`), not by the web: without a session, with the `X-Morgan-Reloj` header (`MORGAN_RELOJ_SECRETO`); without that secret configured, `404`. Answers right away and runs whatever is due in another thread. |
| `POST` | `/uploads/{upload_id}/transcripcion` | Transcribes an uploaded audio so it can be reviewed before sending. |
| `GET` | `/diagnostico/goteo` | Emits one line per second. **A measuring instrument**, requires `system.manage`. |
| `GET` | `/diagnostico/origen` | How Morgan sees the origin of your request (your IP and the proxy chain). **Public and without writes**: it measures the case without a session (2.3 audit). |

**Workspaces (V2.2).** The `X-Morgan-Espacio: <id>` header says which workspace you're working
in; without it, "General". It affects `/sessions` (list and creation), `/uploads` and knowledge.
The middleware checks that the workspace belongs to whoever asks: a made-up or someone else's one
answers `404 ESPACIO_ACTUAL_NO_ENCONTRADO`, it never silently falls back to General.
`GET /sessions?espacio=todos` lists those of every workspace. In `/chat` the workspace **of the
saved conversation** rules, not the header; the header only decides where a new conversation is
born. Detail in [datos.md](datos.md).

**`ejecutar_plan`** (4.0), in the body of `/chat` and `/chat/stream`: the id of a plan of this
conversation that the person has just approved. Morgan runs its steps, with the approved
arguments, before the model speaks, and reports the result. A plan from another conversation,
not approved or already run does nothing.

**`/chat/stream` (V2.0.14)** accepts the same body as `/chat` and answers
`application/x-ndjson`, one object per line:

| `tipo` | When | Fields |
|---|---|---|
| `inicio` | Right at the start, before any wait | `t` |
| `pensando` | When each round to the model starts | `vuelta`, `t` |
| `herramienta` | When each one starts and ends | `nombre`, `estado` (`empieza`, `ok`, `fallo`), `t`. **No arguments** |
| `respaldo` | A provider other than the main one answered | `proveedor`, `t` |
| `latido` | 10 s without any other event | `t` |
| `fin` | At the end | The ones from `/chat`: `success`, `response`, `model`, `elapsed_seconds`, `etapas` |
| `error` | If the turn fails | `code`, `message`. Never the exception's text |

What fails **before** starting —degraded mode, an attachment that doesn't exist, the quota—
arrives as a normal error with its HTTP code, not inside the stream. The turn has a 170 s cap in
the cloud (85 in `/chat`) and there is no `http_deadline`: Vercel's proxy only cuts silence. If
the connection closes, the turn **finishes and is saved**. Design in [agente.md](agente.md).

**In `/sessions`**, the `archived` parameter takes three values: `false` (only active ones, the
default), `true` (only archived) and `all`. A boolean wouldn't do: three states are needed and
HTTP doesn't carry `None`.

**The routes that change something require the `X-Morgan-CSRF` header** with the value of the
`morgan_csrf` cookie, except the `/auth` ones used to get in. See
[autenticacion.md](autenticacion.md#csrf).

**`/health` and `/status` stay open without a session.** `/status` is what the interface checks
to know whether the backend is alive: protecting it makes the sign-in screen itself say "API
disconnected".

**Tasks can't be created through the API.** Creating them and moving them forward is the
agent's job; exposing it here would allow writing states that don't match anything that ran.

**`/diagnostico/goteo` isn't functionality, it's an instrument.** It holds the connection for
up to three minutes emitting one line per second, and it exists to measure where the proxy cuts
(roadmap-2.0.md, 2.0-D, in Spanish). It requires a system permission because,
open to the public, a request that holds a connection for three minutes is a cheap way to use up
the free plan's ones.

**This table is complete, and there is a test that keeps it so.** It used to document 20 of the
45 routes —the administration ones, the integration ones, the plan ones and four account ones
were missing— and an incomplete table is worse than none: whoever reads it concludes that what
doesn't appear doesn't exist. `test_api_documentada.py` fails if a route is added and not added
here (in both languages).

**And a WebSocket**, out of the table because `/openapi.json` only describes HTTP:
`/agente/canal`, the local agent's channel (3.0-D), with the `mga_…` credential in
`Authorization`. **Protocol 3** since 3.4: besides `orden`, `resultado` and `fragmento`, the
agent reports each `estado` of an order and the cloud can `cancelar` (cancel) one and
`consultar` (ask about) it after a drop. Protocol and close codes in
agente-local.md and
§15bis (in Spanish).

## Error format

Every exception is normalized:

```json
{
  "success": false,
  "error": {
    "code": "TOOL_NOT_FOUND",
    "message": "La herramienta 'foo' no existe en el registro.",
    "details": null
  }
}
```

Common codes: `VALIDATION_ERROR` (422), `PERMISSION_DENIED` (403), `TOOL_NOT_FOUND` /
`MEMORY_KEY_NOT_FOUND` (404), `INVALID_ARGUMENTS` (400), `DEGRADED_MODE` (503),
`AGENT_EXECUTION_ERROR` / `TOOL_EXECUTION_ERROR` / `STORAGE_ERROR` / `INTERNAL_SERVER_ERROR` (500),
`UNAUTHORIZED` (401), `SESSION_NOT_FOUND` (404).

With an API token: `TOKEN_INVALIDO` and `TOKENS_DESACTIVADOS` (401), `TOKEN_NO_PERMITIDO` and
`ALCANCE_INSUFICIENTE` (403). What to do with each one, in
[autenticacion.md](autenticacion.md#personal-api-tokens-v2040).

## Degraded mode

If there is no valid LLM API key, the `CoreContainer` starts without an agent: `/chat` answers
**503 DEGRADED_MODE**, while `/health`, `/status`, `/tools`, `/memory`, `/audit`, `/sessions`,
`/settings`, `/uploads` and `/tasks` keep working. `/status` reflects it in `mode: "degraded"`.

## Security notes

- **A local agent's credential (`mga_…`) isn't valid in the API**, and a personal token isn't
  valid in the agent's routes. See agente-local.md (in Spanish).
- **A client that isn't the browser gets in with a personal token**:
  `Authorization: Bearer mgn_...`, created in `POST /auth/tokens`. Without CSRF, with scopes
  (`chat`, `lectura` — read, `escritura` — write) and never with access to `/auth` (except
  `GET /auth/yo`), `/admin`, `/integraciones` or `/diagnostico`. See
  [autenticacion.md](autenticacion.md#personal-api-tokens-v2040).
- **In the cloud the deployment has to be closed in one of two ways**: accounts
  (`MORGAN_REQUIRE_AUTH`, what production uses) or a shared token (`MORGAN_API_TOKEN`, for a
  private Morgan). Without either, startup aborts. `/health`, `/status` and the root stay open.
  See [autenticacion.md](autenticacion.md#when-an-account-is-required).
- **CORS is registered last on purpose.** Starlette applies the last middleware on the outside,
  and CORS has to wrap authentication too: a 401 without CORS headers is blocked by the browser,
  the client never gets to read it and concludes that there is no backend instead of asking for
  the token. Pinned by `test_el_cors_es_el_middleware_mas_externo`.
- **Permissions without a console**: the API builds its `PermissionManager` with
  `interactive=False`, so the `moderate`, `high_risk` and `critical` tools are **denied right
  away** over HTTP instead of blocking the thread waiting for a confirmation nobody can give
  (see ADR-007). To allow them you have to use `PermissionManager.allow_tool()` or
  `MODERATE_PERMISSION_MODE=auto` (the latter only raises the `moderate` level).
- **Argument validation**: `POST /tools/{nombre}` validates the arguments against the tool's
  JSON Schema before evaluating permissions, just like the agent's loop.
- **Sessions**: `session_id` in `/chat` is used. Each session has its own history and its own
  lock, so two concurrent clients don't mix conversations. If it's omitted, the `default`
  session is used.
