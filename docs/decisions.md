# Technical Decision Record

**English** · [Español](decisions.es.md)

## ADR-001: Python as the main language
- **Date**: 2026-09-04
- **Status**: Accepted
- **Context**: We need a language for the agent's backend.
- **Decision**: Python, for its AI/ML ecosystem, ease of scripting and wide availability of
  libraries.
- **Consequences**: Excellent support for LLM APIs, psutil and system automation.

## ADR-002: Google Gemini as the initial model
- **Date**: 2026-09-04
- **Status**: Accepted
- **Context**: We need an LLM with function calling support.
- **Decision**: Google Gemini through its API (free with generous limits).
- **Alternatives**: OpenAI GPT-4, Anthropic Claude, local models.
- **Consequences**: A free API key for development. Native function calling. The modular design
  allows changing provider.

## ADR-003: A permission system by levels
- **Date**: 2026-09-04
- **Status**: Accepted
- **Context**: The AI mustn't have unlimited access to the computer.
- **Decision**: Three permission levels (🟢 safe, 🟡 moderate, 🔴 sensitive) with independent
  control per tool.
- **Consequences**: Controlled security from the start. Scalable for new tools.

## ADR-004: A CLI with Rich before a GUI
- **Date**: 2026-09-04
- **Status**: Accepted
- **Context**: We need an interface for V0.1.
- **Decision**: An interactive CLI using Rich for formatting and colors.
- **Alternatives**: An immediate GUI (React), Textual (TUI).
- **Consequences**: Fast development. Functional from day one. The GUI will come in V0.7.

## ADR-005: The "functionality first" principle
- **Date**: 2026-09-04
- **Status**: Accepted
- **Context**: Risk of over-engineering with complex architectures.
- **Decision**: Every version must add a real capability. Don't build infrastructure without
  functionality that justifies it.
- **Consequences**: Measurable progress. A single agent at first, several agents only if the need
  is demonstrated.

## ADR-006: SQLite stays; the access layer is rewritten
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: The technical audit found connection leaks, no error handling, no migrations and no
  concurrency configuration. The question was whether the engine was the problem.
- **Decision**: Keep SQLite and rewrite the access layer (`src/memory/db.py`). Morgan is a
  single-user personal agent running locally: PostgreSQL would add a service to administer without
  any real benefit.
- **Consequences**: Connections that get closed, WAL for the API's multithreaded access, errors
  translated into `MemoryStorageError` and incremental migrations with `schema_version`. Adding
  tasks, configuration or history means adding a migration and a repository, without touching
  what exists.

## ADR-007: Permissions deny, never block, in a non-interactive context
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: `Confirm.ask()` read from the server process's stdin. When uvicorn was launched from
  a terminal, `isatty()` was true and an HTTP request to a moderate-level tool left the worker
  blocked waiting for someone to type in the server's console.
- **Decision**: `PermissionManager` receives an explicit `interactive` flag. Without a console the
  policy is to deny right away. The API's container builds it with `interactive=False`.
- **Alternatives**: Auto-approving over HTTP (unacceptable), or a timeout on the prompt (it still
  blocks the thread for the length of the timeout).
- **Consequences**: The API never hangs. In return, the `moderate`, `high_risk` and `critical`
  tools can't run over HTTP until there is an approval flow in the interface. It's a conscious,
  documented limitation. (That flow arrived later: plans approved in the web, V1.6 and V2.0.16.)

## ADR-008: No C++ is introduced in this phase
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: Whether any part of Morgan would benefit from native code was evaluated.
- **Decision**: Don't introduce C++. There is no justified candidate today.
- **Rationale**: Morgan's profile is network waits and I/O, not computation. Response time is
  dominated by the LLM call (seconds), followed by the PowerShell and git subprocesses. The
  `os.walk` traversals are disk I/O, where C++ brings no significant advantage, and `psutil` is
  already native underneath. Measured during the audit: `get_schemas()` costs 0.013 ms per call and
  `search_files` went from 79 ms to 1 ms just by pruning directories, without leaving Python.
- **Consequences**: A build toolchain, packaging complexity and a new failure surface are avoided in
  exchange for zero measurable improvement.
- **Re-evaluation criteria**: it will be reconsidered if large-scale code indexing, mass hashing or
  diffing of files, or deep integration with the Win32 API appear. In that case the module must stay
  isolated behind a clean interface, with its tests and its documentation. (When the desktop
  program arrived in 5.0, Rust came in through Tauri, isolated in `escritorio/`, under exactly
  these conditions.)

## ADR-009: The audit log doesn't rotate
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: `logs/audit.log` grows without limit and reading it in full degraded `/audit` and
  `/status` linearly.
- **Decision**: Keep the log without rotation (it's a security record: rotating would be discarding
  evidence) and solve performance by reading only the tail of the file.
- **Consequences**: 50 entries from a 3.34 MB log are read in 8.4 ms. The application log
  (`logs/morgan.log`), which is dispensable, rotates at 2 MB with 3 copies.

## ADR-010: A repository layer; SQLite by default, replaceable provider
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: V1.2 asked to evaluate Supabase/PostgreSQL and, above all, that the Core didn't stay
  coupled to a specific provider. Before V1.2 the `MemoryManager` talked directly to storage and
  there was no abstraction per entity.
- **Decision**: Introduce `SessionRepository`, `MessageRepository` and `MemoryRepository` as
  abstract interfaces, plus a `RepositoryFactory` that brings them together. A single
  implementation is shipped, SQLite's. The Core works with dataclasses (`Session`, `Message`,
  `MemoryRecord`) and never sees SQL.
- **Alternatives considered**:
  1. *Implement Supabase now*: it would sync across devices, but personal memory would move to the
     cloud, Morgan would stop working offline and every read would add network latency. For an
     agent that acts on the local machine, the benefit doesn't make up for it.
  2. *Both backends selectable by configuration*: more surface to maintain and test without a
     demonstrated need.
  3. *Carry on without an abstraction*: it fails the replaceable-provider requirement.
- **Consequences**: Adding PostgreSQL or Supabase consists of implementing three classes and
  changing which factory is built; nothing above changes. There is no call to `sqlite3` outside
  `src/memory/`. In exchange, there is one more layer of indirection.

## ADR-011: The history is persisted whole, but retrieved by window
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: When persisting conversations, the question arises of how much to feed back when
  resuming. Loading everything gives continuity, but inflates the context of every request to the
  LLM.
- **Decision**: Save every message, and when resuming load only the `MORGAN_HISTORY_WINDOW` most
  recent ones (20 by default), and only those with the `user` and `assistant` roles.
- **Reason for excluding tool messages**: a tool result without the call that produced it is an
  orphan message, and providers reject the history.
- **Consequences**: The cost per request stays bounded and the growing-prompt problem fixed in V1.0
  doesn't come back. In return, in very long conversations Morgan doesn't remember what was said at
  the beginning; the future solution is summarizing or semantic search, not widening the window.

## ADR-012: Authentication by shared token, not by users
- **Date**: 2026-09-05
- **Status**: Accepted (superseded by own accounts in V2.0; the shared token remains for a private
  Morgan, see [autenticacion.md](autenticacion.md))
- **Context**: Morgan runs commands on the machine where it runs. Without authentication it can
  only safely listen on `127.0.0.1`, which prevents using it from another device.
- **Decision**: An optional shared token (`MORGAN_API_TOKEN`). Without it, behavior doesn't change
  and a warning is logged. With it, the whole API requires it.
- **Alternatives**: a system of users and sessions would be right for several users, but Morgan is
  a personal agent: it's too much complexity for a single owner.
- **Consequences**: It allows exposing Morgan behind HTTPS with a secret. It doesn't replace real
  access control: whoever has the token has the computer. `/health` stays open so availability can
  be checked without handing out the token.

## ADR-013: Separating capabilities by execution environment
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: Morgan must be able to work as a web assistant reachable from a browser and, also, as
  a local agent with access to the operating system. Without an explicit separation, a cloud
  deployment would expose `execute_command`, `delete_file` and `kill_process` on the server, and
  anyone who reached the API would have a console.
- **Decision**: Each tool declares `requires_local`. With `MORGAN_ENVIRONMENT=cloud`, the ones that
  have it as `True` **are excluded from the registry**, not limited by permissions.
- **Why exclude instead of block**: what isn't registered can't be invoked and doesn't appear in the
  schema the model sees. Relying only on the permission layer leaves the door closed but still in
  place: a configuration change or a bug would open it.
- **Why `True` by default**: it's fail-safe. Forgetting to classify a new tool leaves it out of the
  cloud, not in. Erring on the side of excess costs a capability without exposing anything; erring
  the other way would expose the user's computer.
- **Consequences**: in the cloud 5 tools out of 25 remained (memory and web). Access to the machine
  from a remote Morgan would require in the future an explicit local agent with its own
  authentication; it stays as a direction, not implemented. (It was implemented in 3.0: the local
  agent, with its own credential and policy.)

## ADR-014: Explicit time limits on reasoning
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: The LLM clients were built without their own timeout and inherited the SDK's
  (`connect 5 s`, `read 60 s`, 2 retries). With `max_iterations=6` and a `FallbackProvider` that then
  tries Gemini with its own retries, a single turn could hang for more than 15 minutes with a
  degraded network.
- **Decision**: three combined limits: 30 s per model call, 1 SDK retry and a **120 s clock cap for
  the whole turn**.
- **How the values were chosen**: by measuring. Groq's normal latency averages 0.42 s (5 calls:
  0.37–0.48 s); under 6 concurrent requests up to 44 s was observed. 30 s leaves ~70× the normal
  case without letting a hung call eat minutes. A single retry because, with a fallback provider
  behind, insisting more only lengthens the wait before failing over.
- **Why the turn cap is needed**: capping iterations doesn't cap time. Six iterations of unbounded
  duration are still unbounded.
- **Consequences**: a request can't block Morgan for more than ~2 minutes. If the cap is reached,
  the user gets an explanation and a suggestion, not a hang.

## ADR-015: Status per service, computed and non-blocking
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: A single `internet = yes/no` indicator is misleading: there can be a connection and
  Supabase down, or Supabase answering and the LLM provider down.
- **Decision**: a registry with one entry per dependency, each with its own check, its TTL and its
  exponential backoff. The global status is **computed** from the individual ones instead of being
  stored.
- **Why computed**: a global status stored separately drifts from the real one as soon as a
  dependency changes.
- **Why non-blocking**: whoever checks reads the last known value. If a user's request had to wait
  for a probe, the health check would become the cause of the slowness it's meant to detect.
- **Consequences**: the status can be up to 30 s old, which is exposed in `age_seconds`. It's a
  deliberate trade-off: slightly stale information in exchange for not penalizing any request.

## ADR-016: SQLite and Supabase coexist; which one rules depends on the environment
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: V1.3 asks that Morgan works online through the web and also locally with access to
  the system, without one mode destroying the other.
- **Decision**: don't replace SQLite with Supabase. In the `local` environment, SQLite is the source
  of truth and Supabase the sync target. In the `cloud` environment, Supabase becomes the main store.
- **Why it changes with the environment**: in the cloud there is no local disk that survives a
  redeploy, so a SQLite there would be volatile memory disguised as persistence. On the user's
  computer, instead, depending on the network to read your own memory contradicts the principle that
  the internet improves Morgan but doesn't condition it.
- **Consequences**: both implementations satisfy the same interfaces, so the Core doesn't tell which
  one is underneath. In the cloud there is no sync queue, because there is nothing local to upload.

## ADR-017: Its own PostgREST client instead of the Supabase SDK
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: Supabase has to be talked to from Python.
- **Decision**: implement a minimal client on top of `httpx` against the PostgREST API, instead of
  adding `supabase-py`.
- **Reason**: only four operations (select, insert, upsert, delete) on three tables are needed here.
  The SDK would drag in `gotrue`, `realtime` and `storage3`, which Morgan doesn't use, plus their
  transitive dependencies. The specification explicitly asks not to add unnecessary dependencies.
- **Cost taken on**: error handling and headers have to be maintained by hand. It's mitigated by
  translating every failure into `MemoryStorageError`, the same error the local layer uses, so the
  caller doesn't need to know who it talked to.

## ADR-018: Sync uploads, but doesn't download yet
- **Date**: 2026-09-05
- **Status**: Accepted
- **Context**: §11 of the specification limits V1.3 to preparing the infrastructure, and §12 warns
  against turning the version into a sync project.
- **Decision**: implement the outbox queue and the local → cloud upload. Leave the download and the
  merge for V1.4.
- **Reason**: downloading remote data and mixing it with the local data is where data corruption
  lives: it requires resolving identity across computers, event ordering, propagated deletions and
  real conflicts. Doing it in a hurry in a version whose declared priority is stability would be
  contradicting itself.
- **Groundwork left in place**: `updated_at` in the three tables, `origin_device` to know which
  computer wrote each row, and `client_id` in `messages` as a stable identity across computers (the
  local id is autoincremental and different on each machine, so it doesn't work for deduplicating).
- **Note from 2026-09-10 (V2.0)**: V1.4 came and went, and the download wasn't done. It isn't in the
  2.0 roadmap either. The text above is left as it was —an ADR records what was decided then, not
  what happened afterwards— but the date it promised no longer holds and saying so matters: the real
  status is **postponed with no date**. What changed meanwhile is that the cloud became the main
  place, so the download is only needed for whoever uses both Morgans at once. Removing it from the
  plan or scheduling it is the creator's decision; it's explained in [datos.md](datos.md).
- **Removed on 2026-09-18 (V2.0.34)**, by my decision. The download isn't going to be done: the
  cloud is the source of truth, and with the 3.0 local agent it will also be so for the person's
  computer, which will work against it and not against a copy that has to be merged. It's taken out
  of the plans and the pending lists; the schema keeps `updated_at`, `origin_device` and `client_id`
  because the upload uses them.
- **Conflict strategy chosen for V1.4**: last-write-wins by `updated_at`, recording the discarded
  value. With a single user on several devices, real conflicts are rare and an automatic merge would
  add more risk than value.
