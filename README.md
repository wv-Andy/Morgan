# Morgan — Personal AI Agent for Windows

**English** · [Español](README.es.md)

A personal AI assistant that talks in plain language and takes controlled actions on your
computer: files, terminal, git, the web and persistent memory, all under a permission system
based on risk levels and with a complete audit trail.

**Status (5.3)**: **Morgan for Windows**, a double-click program that lives in the system
tray, opens Morgan in a single window (Ctrl+Alt+M), connects your PC without a console and
updates itself, signed and going back on its own if something fails ([download](https://github.com/wv-Andy/Morgan/releases/latest)) · CLI + REST API + web ·
accounts with each person's data kept apart · workspaces · automations · deployed in the
cloud · **local agent**: Morgan reads the folders you allow on your PC and, if you switch it
on, **creates, edits, moves and deletes** in the folders you choose, always with a plan you
approve, and runs programs from a closed catalog, with no PowerShell · more than 4,000 Python
tests and more than 100 for the web, all green and none disabled.

> The interface and part of the documentation are in Spanish, the language Morgan was built
> in. Every public document has an English version (this one) and a Spanish one (`*.es.md`).

## Quick start

### Requirements
- Python 3.14 (the version it is tested with, here and in continuous integration; `.python-version`)
- Node.js 20+ (only for the web interface)
- An API key from [Groq](https://console.groq.com) or [Google Gemini](https://aistudio.google.com),
  both free. One is enough; with several, Morgan switches provider on its own when one fails.
  [OpenAI](https://platform.openai.com) is optional and **paid**: it goes last.

### Installation

```bash
git clone https://github.com/wv-Andy/Morgan.git morgan
cd morgan

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt        # pinned versions: the same as in production
pip install -r requirements-dev.txt    # plus pytest, for the tests

copy .env.example .env
# Edit .env with your API key
```

> **Don't move or copy the `venv` folder.** A virtual environment stores the path where it
> was created: once moved, `pytest.exe` and the other launchers fail silently and the
> tracebacks point to a folder that no longer exists. If you move the project, delete it and
> create it again. It really happened to me (2.0.12).

### Running

```bash
# Interactive CLI
python src/main.py

# REST API (http://127.0.0.1:8000, docs at /docs)
python -m src.api.server

# Web interface (needs the API running)
cd web && npm install && npm run dev
```

### Tests

```bash
pytest -q                 # more than 4,000, none disabled
cd web && npm test        # more than 100, the browser ones. From web/: outside it, it doesn't read its config
```

**Continuous integration** (`.github/workflows/pruebas.yml`, since 4.19): on every push, the
Python suite on Windows with coverage and a floor (if it drops, it fails), the web (types,
tests and build) and the known vulnerabilities in the dependencies (`pip-audit` and
`npm audit`). There is no desktop in front of it there: whatever drives real windows is
skipped with `MORGAN_SIN_ESCRITORIO=1`, saying so, and runs locally. **Morgan for Windows** is
built there too (`.github/workflows/escritorio.yml`) and tested on a clean machine: install,
pause, the shortcut, upgrading over an older version, uninstall.

The frontend tests exist because three bugs that broke the web in production lived exactly
there: in what the browser does, which you don't see by talking to the API from a script. The
story is in [docs/web.md](docs/web.md).

## CLI commands

| Command    | Description                                        |
|------------|----------------------------------------------------|
| `/help`    | Shows the help                                      |
| `/tools`   | Lists the tools with their domain and risk level    |
| `/domains` | Groups the tools by domain                          |
| `/memory`  | Shows the memories stored in SQLite                 |
| `/audit`   | Shows recent audit events                           |
| `/clear`   | Clears the conversation history in memory           |
| `/exit`    | Exit                                                |

## Architecture

```
  CLI (Rich)            Web UI (React/Vite)
       |                        |
       |                 REST API (FastAPI)
       +----------+-------------+
                  v
             Agent (core)          <- multi-turn agent loop
                  |
     +------------+------------+--------------+
     v            v            v              v
 LLMProvider  ToolRegistry  Permission-   MemoryManager
 Groq -> Gemini  8 domains   Manager      SQLite (local)
      -> OpenAI              + Validator  Supabase (cloud)
 + Fallback                  + AuditLogger
```

The agent receives a message, asks the LLM with the tool schemas and, for each requested call,
validates the arguments, checks permissions, executes and feeds the result back until it
produces a final answer. Full detail in [docs/arquitectura.md](docs/arquitectura.md).

## LLM models

A chain with automatic failover: **Groq** first (free and the fastest, with several accounts
adding up quota), **Gemini** next (free, and the only one that understands images) and
**OpenAI** last, which is **paid** and only answers when the free ones run out. NVIDIA NIM is
still in the code, switched off: it is enabled with `MORGAN_LLM_ORDER`. Configuring one
provider is enough. Detail in [docs/modelos.md](docs/modelos.md).

## Permission system

| Level | Behavior |
|---|---|
| 🟢 `safe` / `low_risk` | Runs automatically |
| 🟡 `moderate` | Needs confirmation (`MODERATE_PERMISSION_MODE=auto` skips it) |
| 🟠 `high_risk` | Always needs authorization |
| 🔴 `critical` | Always needs authorization |

Also: proactive blocking of destructive commands, protection of system paths and critical
processes, masking of secrets and an immutable log in `logs/audit.log`.

## Tools

**50 on your computer**, spread across eight domains: `system`, `filesystem`, `terminal`,
`memory`, `web`, `coding`, `git` and `general`.

**59 in the cloud.** The ones that touch the machine —files, terminal, processes, local git—
**aren't even registered** when Morgan runs on a server: it isn't that they are forbidden,
they just don't exist there. The GitHub ones are, because they talk to an API and not to the
disk. And the ones **for your PC** go through the local agent: they don't touch the server,
they only appear while your PC is connected and they only reach the folders you allow there:

- Since 3.0, four for **reading** (`list_files`, `read_file`, `search_files`, `system_info`),
  and since 3.1 `copy_file`, which brings you **a copy of a file to download** on your phone.
- Since 3.3, five for **writing** (`create_file`, `edit_file`, `create_folder`, `move_file`,
  `delete_file`), since 4.1 a sixth one, `append_file` (add to the end), and since 4.6 copying
  (`copy_path`) and compressing or extracting (`compress`). They come **switched off**; you
  turn them on in your PC, one by one, and choose in which folders. Every change needs **a plan
  you approve** in the web, and deleting is also confirmed **on the PC** with a notification.
  What is deleted goes to the Recycle Bin, and Morgan doesn't create or change programs or
  scripts.
- Since 3.5, the **terminal** and **processes** (`run_command`, `run_change_command`,
  `get_processes`, `kill_process`). **There is no PowerShell**: only programs from a closed
  catalog (git, ping, ipconfig…), without a shell, which you switch on one by one in your PC.
  Whatever changes things (`git pull`, `git commit`, ending a process) asks for a plan and
  «Allow» on the PC, and only your own processes can be ended.
- Since 4.6-4.13, the rest of the desktop: file metadata, opening and closing apps, the PC's
  performance and network, the clipboard, notifications, windows and screenshots, and driving
  an app's interface — each one off by default, and the risky ones with «Allow» on the PC.

Full catalog in [docs/capacidades.md](docs/capacidades.md).

## Structure

```
Morgan/
├── src/
│   ├── agent/         # Central agent, prompt and events
│   ├── models/        # LLM providers (Groq, Gemini, OpenAI, NVIDIA, Fallback, Mock)
│   ├── tools/         # Tools by domain + registry
│   ├── security/      # Permissions, validators and audit
│   ├── memory/        # Persistent memory (SQLite and Supabase)
│   ├── identidad/     # Accounts, sessions, roles, quotas and email
│   ├── integraciones/ # External services through OAuth (GitHub; Google, parked)
│   ├── conocimiento/  # Indexed documents and search
│   ├── espacios/      # Workspaces: projects with their own context
│   ├── uploads/       # Uploaded files, copies from the PC and text extraction
│   ├── automatizacion/# Automations: scheduled orders and the notice tray
│   ├── agente/        # The local agent that runs on YOUR PC (3.0): policy and capabilities
│   ├── canal/         # In the cloud: agent connections, dispatch and the PC's tools
│   ├── tasks/         # Tasks and plans
│   ├── api/           # FastAPI REST API
│   ├── config.py      # Central, validated configuration
│   └── main.py        # CLI
├── web/               # React + TypeScript + Vite interface
├── escritorio/        # Morgan for Windows (Tauri): tray, windows and installer
├── empaquetado/       # The frozen agent (PyInstaller) and the installer tests
├── migraciones/       # Supabase schema, versioned
├── tests/             # more than 4,000 tests (pytest)
├── docs/              # Documentation
├── data/              # SQLite database
└── logs/              # Audit log
```

The module and folder names are in Spanish, like the code: `identidad` is identity,
`integraciones` integrations, `conocimiento` knowledge, `espacios` workspaces, `agente` agent,
`canal` channel, `escritorio` desktop, `empaquetado` packaging, `migraciones` migrations.

## Documentation

| | |
|---|---|
| [Architecture](docs/arquitectura.md) | Layers, module map and the flow of a request |
| [What Morgan can do](docs/capacidades.md) | Real capabilities and limits, in plain language |
| [Authentication and accounts](docs/autenticacion.md) | How people get in and what keeps each one's data apart |
| [Security](docs/seguridad.md) | Permissions, validation, audit, roles and known limits |
| [Technical decisions](docs/decisions.md) | Why it is built this way, and what was ruled out |
| [The local agent](docs/agente.md) | How Morgan reaches your PC |
| [Data](docs/datos.md) | What is stored where, and the backups |
| [Models](docs/modelos.md) | The provider chain, quotas and failover |
| [Web](docs/web.md) | The web interface |
| [Integrations](docs/integraciones.md) | GitHub and the services connected through OAuth |
| [API](docs/api.md) | The REST API |
| [Privacy](docs/privacidad.md) | What Morgan stores, who it shares it with and for how long |

## Roadmap

- [x] V0.1 — Hello Agent
- [x] V0.2 — Filesystem
- [x] V0.3 — Terminal and processes
- [x] V0.4 — Multi-turn agent loop and dual LLM engine
- [x] V0.5 — Persistent memory (SQLite)
- [x] V0.6 — Internet and a safe web
- [x] V0.7 — Coding agent and Git
- [x] V0.8 — Tools organized by domain
- [x] V0.9 — Advanced security and audit
- [x] V1.0 — REST API (FastAPI)
- [x] V1.1 — Web interface (React + TypeScript)
- [x] V1.2 — Persistence, sessions, memory and a redesign
- [x] V1.3 — Hybrid online/offline architecture
- [x] V1.4 — Multimodal: images, audio and attachments
- [x] V1.5 — Task system
- [x] V1.6 — Planning and user accounts
- [x] V1.7 — Verified steps and recovery
- [x] V1.8 — Indexed knowledge and advanced memory
- [x] V1.9 — Integrations with external services (GitHub)
- [x] V2.0 — Platform: identity, observability, region and workspaces
- [x] V2.1–V2.3 — Measured optimization: agent, consolidation and getting ready for 3.0
- [x] V3.0 — Local agent: your PC connected to Morgan, read-only
- [x] V3.1 — The data boundary, and a copy of your files to download
- [x] V3.2 — The local policy engine (blocked folders, capabilities, limits)
- [x] V3.3 — Writing on your PC, with an approved plan and confirmation on the PC to delete
- [x] V3.4 — Execution engine: states, a journal on the PC, a «Stop» that really stops
- [x] V3.5 — Terminal and processes: a closed catalog, no PowerShell
- [x] V3.6 — Resilience: no ghost orders, atomic writes, health and a watchdog
- [x] V3.7 — Several agents per person, an order history and cross-checking both ends
- [x] V3.8 — The installable agent: signed package, updates with rollback, rotated credential
- [x] V3.9 — Audit of 3.x and the real-world test (each 3.x version with its own evaluation, .5)
- [x] V4.0 — The closed loop: the approved plan runs on its own, checks the PC, corrects with a cap and uses context
- [x] V4.1–V4.5 — Loose ends, `append_file`, efficiency (−22 % and −77 % tokens per turn) and what came out of my tests
- [x] V4.6–V4.13 — The desktop: files, the PC from the inside, apps, the extended terminal, clipboard and notifications, windows and screenshots, driving interfaces, the PC's context
- [x] V4.14–V4.15 — Automations: Morgan runs queries on its own at their time, and pre-approved changes
- [x] V4.16–V4.18 — Compressing with the code inside, connecting your PC for anyone, a cold start without 503
- [x] V4.19 — The foundation: continuous integration, pinned and audited dependencies, coverage with a floor, and the public code in [wv-Andy/Morgan](https://github.com/wv-Andy/Morgan)
- [x] V4.20 — Holding up in production and precise when searching: bounded requests, a dead provider doesn't cost its timeout, a backup really restored, errors reach the tray
- [x] V4.21 — Several searches in one round: the three-part question, from 29.7 s to 4.9 s
- [x] V4.22–4.24 — The rest of the hardening for production, **in parallel with 5.x** (the gate to start 5.0 was met with 4.21). Done: the web's security headers and no Supabase warning at WARN level. Also done: reviewing per-account limits (memories, workspaces and documents), rotating the clock's secret, closing the «login CSRF», basic accessibility (axe-core, 0 issues in the three themes) and errors in plain words. Also measured: a newcomer's walkthrough with the real model. The week of real use was dropped (I'll note anything that fails as I use it); someone new using it without help is 5.5
- [x] **V5.0 — Morgan for Windows**: a double-click installer, pairing without a console from a window ([download](https://github.com/wv-Andy/Morgan/releases/latest))
- [x] V5.1 — Morgan in the tray: status, pause and resume instantly, what it did last, and upgrading without unpairing
- [x] V5.2 — The conversation in its own window, Ctrl+Alt+M, and notices as Windows notifications
- [x] V5.3 — One window with the web (the program's things in Settings → This PC, and the web only asks for the harmless) and signed updates with automatic rollback
- [ ] V5.4–5.5 — Windows signing, the Microsoft Store and Morgan for anyone

## License

MIT
