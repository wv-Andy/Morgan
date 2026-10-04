# What Morgan can do: capabilities and tools

**English** · [Español](capacidades.es.md)

> Brings together what used to be three documents: the capabilities in plain language, the
> tool catalog and the multimodal input. Last review: 2026-09-25, V3.5.0. **49 tools in
> `local` and 43 in `cloud`** (14 of them on the person's PC through their agent), in 8
> domains, counted from the real registry (`tests/test_readme_al_dia.py` checks it against
> the README).

## 1. In one sentence

An agent that talks, decides which tools to use and chains them until it solves what you ask.
On your computer it acts on your files, your terminal and your repositories; on the web
([morgan-ia.vercel.app](https://morgan-ia.vercel.app)), with an account, it does everything
that doesn't touch a machine, and since 3.0, with the local agent (in
Spanish) on your PC, it **reads** the folders you allow there; since 3.1 it can also **bring you
a copy of a file to download** on your phone; since 3.3, if you switch it on, **create, edit,
move and delete** in the folders you choose, with a plan you approve; and since 3.5, **run
programs from a closed catalog** (git, ping, ipconfig…) and see or end your processes.

| Way in | How | What for |
|---|---|---|
| **Web in the cloud** | `https://morgan-ia.vercel.app` | With an account, always available, 43 tools (14 of them act on your PC with the agent connected: four for reading, the copy of a file to download, five for writing and four for terminal and processes, these nine off until you switch them on) |
| **Morgan for Windows** (5.0) | The installer: «Descargar Morgan para Windows» (Download Morgan for Windows) in the web's side menu (from Windows) or in Settings → Your computer | Carries the agent inside: pairs it from a window, without a console, and leaves it starting with Windows. The recommended way to connect a PC. Since 5.1 it lives in the tray (status, pause, what it did last) and since 5.2 it brings **the conversation in its own window** (Ctrl+Alt+M) and notices as notifications if you switch on «Send you notices on this PC» |
| **CLI** | `.\venv\Scripts\python -m src.main` | Day to day on your computer, with all 49 |
| **Local web** | `.\venv\Scripts\python -m src.api.server` → `http://127.0.0.1:8000` | The interface over your computer |
| **REST API** | The same one the web uses | [api.md](api.md) |

## 2. What you can ask it

| What | Examples | Where |
|---|---|---|
| **Files** | "What's in Downloads?", "Create a `notas.md` with the summary" | Local. From the web, with the local agent: read in the folders you allow and, if you switch it on, write in the ones you choose for that |
| **Your PC** | "What's eating the CPU?", "Run `npm run build`" | Local. From the web, with the agent: a closed catalog (`git status`, `git pull`, `ipconfig`, `ping`…) and seeing or ending your processes |
| **Programming** | "Inspect this project", "Change the timeout and run the tests" | Local |
| **Git** | "What haven't I committed?", "Make a commit with this diff". **It doesn't push**, on purpose | Local |
| **GitHub** | Repositories, issues, pull requests and files, **read-only** | Both |
| **Internet** | "Search how to configure…", "Read this URL" | Both |
| **Remembering** | "Remember I prefer short answers", "What do you know about me?" | Both |
| **Documents** | Storing them and looking them up when they're relevant | Both |
| **Files, images and voice** | "Look at this screenshot", "Summarize this PDF", recording audio | Both |
| **Long jobs** | Tasks with progress, plans you approve before they run | Both |
| **Projects** | Workspaces with their own conversations, files and instructions | Both |

**It checks what it does and admits when it can't**: after creating, deleting or changing
something, it looks at whether it really happened, and says "I couldn't" instead of "done"
([agente.md](agente.md)).

## 3. What it does NOT do

- **It doesn't push or deploy anything.** The tool doesn't exist.
- **From the web, on your PC it does nothing you don't allow there.** It reads the folders you
  allow (none at first). Writing comes **switched off**: it's switched on on the PC, in
  separate folders, every change needs **a plan you approve** and deleting is also confirmed
  **on the PC** (to the Recycle Bin). **It doesn't create programs or scripts.** Running, only
  programs from a **closed catalog without a shell** that you switch on one by one (3.5): no
  PowerShell and no scripts. The rest of the local tools aren't even registered in the cloud.
- **It doesn't work without internet** except for what doesn't need a model. A local model is
  **ruled out**.
- **It doesn't speak** (text to speech). **It doesn't learn on its own.**
- **It doesn't connect to Google**: Calendar is parked (ruled out on 2026-09-19).

## 4. The catalog

🟢 `safe` · 🟡 `moderate` (asks for confirmation) · 🔴 `critical` (always asks for
confirmation). **L** = local only · **L+C** = local and cloud.

### Computer (local only)

| Tool | Level | What it does |
|---|---|---|
| `system_info` | 🟢 | CPU, RAM, disk and system in real time |
| `list_files`, `read_file`, `search_files` | 🟢 | List, read (paged) and search by name or pattern |
| `create_file`, `copy_file`, `move_file`, `rename_file` | 🟡 | Create (with intermediate folders), copy, move, rename |
| `delete_file` | 🔴 | Permanent deletion with system paths protected |
| `execute_command` | 🔴 | PowerShell with a timeout and captured output |
| `get_processes`, `get_environment` | 🟢 | Processes by CPU or RAM; variables **with secrets masked** |
| `kill_process` | 🔴 | End processes, with the critical ones protected |
| `inspect_project`, `search_code` | 🟢 | Stack and dependencies; definitions and patterns |
| `patch_file`, `run_tests` | 🟡 | Exact replacement; run the suite and read the traceback |
| `git_status`, `git_diff` | 🟢 | Status and differences (the path goes after `--`) |
| `git_commit` | 🟡 | Create a commit |

### GitHub (L+C, read-only)

`github_listar_repos`, `github_listar_issues` (without the PRs GitHub mixes in),
`github_listar_prs`, `github_leer_archivo` → [integraciones.md](integraciones.md).

### Memory, knowledge and web (L+C)

| Tool | Level | What it does |
|---|---|---|
| `remember_fact`, `recall_memory` | 🟢 | Store (by key) and look up facts about you |
| `forget_fact` | 🟡 | Forget one |
| `search_knowledge`, `add_knowledge`, `list_knowledge_sources`, `index_document` | 🟢 | Searchable documents → [datos.md](datos.md#4-knowledge-v18) |
| `remove_knowledge` | 🟡 | Delete a document |
| `search_web`, `read_webpage` | 🟢 | Search with **Google** (through Serper, `SERPER_API_KEY`, 4.8), Tavily, Brave and DuckDuckGo, in that order and each one only with its key; you can ask for one (`motor`) and each result says which one answered. **Several searches in one call** (`consultas`, up to 4, in parallel; 4.21): a question with several parts is no longer one round trip to the model per part. A search engine that doesn't answer rests for 10 min and, with none available, it's said straight away (4.5). And reading pages, isolated in `<untrusted_web_data>`, without reaching the internal network |
| `list_uploads`, `read_upload`, `analyze_image`, `transcribe_audio` | 🟢 | Files uploaded through the web (below) |
| `copy_file` | 🟢 | **Only with your PC connected** (3.1-E): brings a copy of one of your files and gives you a link to download it on your phone. Any format, up to 20 MB; the copy deletes itself after 24 h |
| `delete_file` | 🔴 | The same, and it's also **confirmed on the PC** with a notification. Goes to the Recycle Bin; only empty folders |
| `open_app` | 🟢 | **Only with your PC connected and switched on there** (4.8): open an installed app (never consoles or system tools), a file with its program, a page in the browser or a folder. No plan |
| `close_app` | 🔴 | Close one of your programs like its X button, with a plan and «Allow» on the PC |
| `pc_context` | 🟢 | **Only with your PC connected** (4.13; on by default): your code projects inside the allowed folders (type, git branch) and the installed editors. For "open project X" without giving the path |
| `ui_read` | 🟢 | **Only with your PC connected and switched on there** (4.12): the buttons, fields and links of a window, by name |
| `ui_control` | 🔴 | The same: click, type or press keys in a window, with a plan and «Allow» when it starts. Never passwords, consoles, File Explorer or the Windows key |
| `windows` | 🟢 | **Only with your PC connected and switched on there** (4.11): see your windows, focus, minimize, maximize, move or put two side by side. No plan |
| `screenshot` | 🟢 | The same (4.11), with **«Allow» on the PC every time**: a screenshot of the screen, a window or an area; it stays 24 h in your files and Morgan looks at it with `analyze_image` |
| `clipboard`, `notify` | 🟢 | **Only with your PC connected and switched on there** (4.10): write to the clipboard or read it (with «Allow» on the PC every time, because it may be a password); and a notification on the PC. No plan. Since 5.2, the notices of your automations also arrive this way, if `notify` is on |
| `pc_diagnostics` | 🟢 | **Only with your PC connected and switched on there** (4.7): performance ("why is it slow?"), the network and IP, which program listens on which port and the Windows services. `system_info` also gives disks and space, GPU and battery |
| `copy_path`, `compress` | 🟡 | Like the ones above (4.6): copy a file or folder to a new place on the PC; compress into a `.zip` or extract. Never credentials. Programs and scripts: compressing **does** include them (4.16, my decision: otherwise the copy of a project came out without its code); copying and extracting, never. A dangerous `.zip` isn't extracted |
| `file_info` | 🟢 | **Only with your PC connected** (4.6): type, size, dates and attributes of a file; for a folder, how much it takes up. `search_files` also by extension, date, size and text inside |
| `service_control` | 🔴 | **Only with your PC connected and switched on there** (4.9): start, stop or restart a Windows service, with a plan and «Allow»; never the ones that hold Windows up. Many require administrator rights, and then it can't |
| `run_change_command`, `kill_process` | 🔴 | What changes things (`git pull`, `git commit`) and ending **your** process: a plan and «Allow» on the PC |

### Tasks, plans and verification (L+C)

`create_task`, `get_task`, `list_tasks`, `complete_task`, `fail_task`, `cancel_task`,
`retry_task`, `create_plan`, `get_plan`, `list_plans`. All 🟢: planning doesn't run anything →
[agente.md](agente.md). `verify_step` was retired in 4.1.5: verification is already automatic
after every change, and it reported as failed what had been done on the PC.

### Automations (4.14)

| Tool | Risk | What it does |
|---|---|---|
| `create_automation` | 🔴 | Schedules an order for Morgan to do on its own (daily, on certain days or every N hours, at most every hour) and leave the result in the tray. **Always with a plan you approve**, also with the automatic permission. Two kinds: a **query** (4.14), or **fixed steps** that change something (4.15): exact tool and arguments, only green and yellow, the same every time except for `{fecha}` (date) and `{hora}` (time). At most 10 active |
| `list_automations` | 🟢 | The ones you have, when they run and how the last one went |

Pausing, resuming and deleting are in the web's **Automations** view. What an automation can
use while it runs: searching and reading the internet, your knowledge, your memory and, from
the PC, reading only (files, searching, system, projects, diagnostics and the query commands)
→ [seguridad.md](seguridad.md#5-bis-automations-nobody-in-front-414).

### Count

| Domain | Local | Cloud |
|---|---|---|
| filesystem | 8 | 0 |
| terminal | 4 | 0 |
| coding | 4 | 0 |
| system | 1 | 0 |
| git | 7 | 4 |
| memory | 8 | 8 |
| web | 6 | 6 |
| general | 10 | 10 |
| **Total** | **48** | **28** |

The tools that can't be used at that moment are trimmed from each turn's prompt: the ones
for an open task when there is none, the PC's if it isn't connected and, since 4.1.5, the
GitHub ones if the person hasn't connected it; since 4.2, the attachment ones if nothing was
uploaded and the ones to search, list or delete documents if there are none.

### Adding a tool

1. Inherit from `Tool` (`src/tools/base.py`): `name`, `description`, `parameters` (JSON
   Schema), `permission_level`, `category`, `execute()` returning
   `{"success", "data", "error"}`.
2. Declare `requires_local` (`True` by default: out of the cloud unless said otherwise).
3. Register it in `CoreContainer._build_tool_registry()`: **the only place**.
4. If it receives a list, give it `items`: **Gemini rejects the whole catalog** without it.
5. If it acts outside Morgan on someone's behalf, `exige_plan = True`.
6. Update this catalog and the README counts (both languages).

## 5. Files, images and voice

**Capabilities per provider.** Sending an image to a model without vision doesn't give a
worse answer: it gives an error. The chain filters by capability **before** failing over, and
if nobody can serve it, it fails right away with `CapacidadNoDisponible`. By default a
provider only does text. Today vision is served by Gemini; audio, by Whisper through Groq's
SDK with the same key.

**An uploaded file is untrusted data:**
- **The user's file name never touches the disk**: it's stored with a generated id. The
  defense against *path traversal* is not building the path.
- **The type is deduced from the bytes**, and if the extension promises a format with a
  signature and the signature isn't there, it's rejected (an executable renamed to `.png` used
  to be accepted).
- **What is extracted goes inside `<untrusted_file_data>`**, like web pages.
- **No preview**: rendering uploaded HTML or SVG is attack surface. SVG comes in as text.
- Uploads, deletions **and rejections** are audited, never the content.

| Limit | Value | Variable |
|---|---|---|
| Size per file | 20 MB | `MORGAN_UPLOAD_MAX_MB` |
| Files per person | 50 | `MORGAN_UPLOAD_MAX_ARCHIVOS` |
| Space per person | 200 MB | `MORGAN_UPLOAD_MAX_TOTAL_MB` |
| Lifetime | 24 h | `MORGAN_UPLOAD_TTL_HOURS` |
| Extraction | 30 s, 30,000 characters, 100 pages | — |

**Formats** (73 extensions): PNG, JPEG, GIF and WebP images; PDF; text and data (TXT,
Markdown, CSV, JSON, XML, YAML, TOML, INI, LOG); code in some twenty languages; MP3, WAV,
WebM, M4A, OGG, FLAC, Opus and AAC audio. A scanned PDF is detected and said so.

**Where they're stored**: the index in the database (SQLite or Supabase); the bytes on disk
locally and in Supabase Storage's **private** `morgan-uploads` bucket in the cloud. The index
used to live in memory and in the cloud it was lost when Render fell asleep.

**The attachment is tied to the message** in its metadata, not pasted into the text; one that
no longer exists is rejected with 422 before spending a turn. Up to 10 per message. With an
attachment, a message with no text is legitimate. Voice is transcribed **so you can review it
before sending** ([web.md](web.md#3-control-over-the-turn)).

Verified end to end: an `.md` read, and a PNG described by Gemini with the main model without
vision.

**Out**: OCR of scanned PDFs, DOCX and text to speech.
