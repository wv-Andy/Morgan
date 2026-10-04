# Security: permissions, validation, audit and roles

**English** · [Español](seguridad.es.md)

> Brings together what used to be the security document and the roles one. Accounts,
> sessions, CSRF and access throttling are in [autenticacion.md](autenticacion.md). Last
> review: 2026-09-25, V3.5.0 (the local agent, §5).

The AI **doesn't have unlimited access** to the computer. Every tool declares its risk, and
none runs without going through `PermissionManager`, which validates commands and paths and
leaves a record in the audit.

## 1. Risk levels

| Level | Behavior | Tools |
|---|---|---|
| 🟢 `safe` | Automatic | Reads: files, processes, memory, web, code, `git_status`, `git_diff`, tasks, plans |
| 🟢 `low_risk` | Automatic | Reserved |
| 🟡 `moderate` | Asks (or automatic with `MODERATE_PERMISSION_MODE=auto`) | Create, copy, move and rename files, `patch_file`, `run_tests`, `git_commit`, `forget_fact`, `remove_knowledge` |
| 🟠 `high_risk` | Always asks | Reserved |
| 🔴 `critical` | Always asks | `delete_file`, `execute_command`, `kill_process` |

### Evaluation order (the first one that decides, stops)

1. Unknown level → **denied** (fail-safe).
2. `CommandValidator` on `execute_command`: blocks `format`, `diskpart`, `bcdedit`,
   `reg delete hklm`, `shutdown`, recursive deletions from the root and fork bombs **before
   asking**.
3. `PathValidator` on everything that writes: denies drive roots, `C:\Windows` and
   `Program Files`, with their subfolders.
4. Explicitly blocked → denied. Explicitly allowed or allowed in the session → approved.
5. `safe` and `low_risk` → approved; `moderate` depending on the mode; `high_risk` and
   `critical` → always ask.

**Without a console, it's denied right away**, never waiting for an answer nobody is going to
give ([ADR-007](decisions.md)). In the web, whatever acts outside Morgan is authorized with
**an approved plan** ([agente.md](agente.md#3-plans-saying-what-will-be-done-before-doing-it)).

## 2. Other defenses

- **Memory belongs to whoever asks** (4.0): the summary of memories, name and preferences is
  read on every turn for the person of the turn and never written into the agent's prompt,
  which is one for every account. Until 4.0 it was written there, and everyone's turns
  carried the memory of whoever saved it last (`tests/test_memoria_por_persona.py`).
- **Protected processes**: `kill_process` refuses `lsass`, `csrss`, `winlogon`, `services`,
  `svchost`, `explorer` and the other critical ones.
- **Masked secrets** in `get_environment` and in the audit (`KEY`, `SECRET`, `PASSWORD`,
  `TOKEN`, `CREDENTIAL`, `AUTH`, `APIKEY`, `PRIVATE`), and by pattern in the application log
  (`gsk_`, `AIza`, `sk-`, `api_key=`, `token=`, `password=`).
- **Prompt injection**: what is downloaded goes inside `<untrusted_web_data>` and what comes
  from uploaded files inside `<untrusted_file_data>`; the prompt teaches the model to treat it
  as data. Downloads of up to 250 KB and 12,000 characters.
- **Compressed pages** (2.0.15): gzip and deflate with the same 250 KB cap; an unknown
  compression is rejected by name, and a body with more than 5 % replacement characters isn't
  delivered (python.org arrived as compressed garbage).
- **SSRF**: `read_webpage` runs without confirmation, so it refuses `localhost`, private
  networks, link-local (`169.254.169.254`), multicast, reserved ranges and anything that isn't
  http/https.
- **The web's headers** (`vercel.json`, completed in 5.2 from the 4.22 list): HSTS, `nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, and also a **CSP** that only allows its own
  scripts plus the theme one by its hash (no `unsafe-inline` or `unsafe-eval`), connections
  only to its own domain (the API goes through `/api`) and nobody putting the web in a frame;
  **Permissions-Policy** (no camera, location, payments or USB; the microphone, only the web
  itself) and **COOP**. Tested in a real browser before publishing them
  (`scripts/probar_cabeceras_web.py`: headless Edge, the built web served with those headers,
  zero blocks; and it catches a wrong hash, fonts not allowed and connections cut).
  `tests/test_cabeceras_web.py` checks that the hash follows the script.
- **Argument validation** against the JSON Schema before running, in the agent and in
  `POST /tools/{nombre}`.
- **Injected options**: `git_diff` puts the path after `--`. Without it,
  `file_path='--output=/wherever'` made a read tool **write a file**.
- **What the model writes doesn't change what gets called**: names with `.` or `..` are
  rejected, they're encoded, and GitHub checks that the outgoing URL is the requested one.
  With `ruta='../../../../user/emails'` a "read a repository file" tool returned the person's
  private emails ([integraciones.md](integraciones.md)).

## 3. Audit

`logs/audit.log` records **every attempt**, authorized or not, in JSON Lines: date, tool,
risk, sanitized arguments, authorization, result and error. Also administrative actions (who,
on whom, what changed), and file uploads and rejections. **It doesn't rotate**: it's evidence;
the tail of the file is read ([ADR-009](decisions.md)). **It isn't deleted when an account is
deleted**: it records what Morgan did, and deleting it would be a way of covering a trail.
It's read with `/audit` in the CLI, `GET /audit` or the Audit view.

**What `success` means** (clarified in 4.1, after reading the audit of the 4.0.5 evaluation):
in the entry for each **permission** it repeats the authorization (it's written before
running, when there is no result yet). The real **result** of what changes something goes in
a second entry, the one for the execution authorized by a plan (`autorizado_por_plan` in the
arguments). To know whether something worked, look at that one, or at the verification's
verdict.

## 4. Roles and owner

```
USER  →  ADMIN  →  OWNER
```

> **Authorization is decided by permissions, never by who you are.** No
> `if user.email == "..."`: the chain is user → role → permissions → authorization, and the
> role is resolved **always on the server**.

| Permission | Authorizes | USER | ADMIN | OWNER |
|---|---|:--:|:--:|:--:|
| `users.read` | See accounts | | ✅ | ✅ |
| `users.manage` | Change roles, suspend | | | ✅ |
| `audit.read` | Read the audit | | ✅ | ✅ |
| `system.manage`, `integrations.manage`, `settings.manage` | The installation | | | ✅ |

Each person's own things (conversations, memory, files) don't need a permission: they're
already isolated per user.

### What is lifted for the owner, and what isn't

| Lifted | Not lifted |
|---|---|
| Daily quota (they pay for the keys) | Validation of commands and paths: it protects the owner from what **the model** proposes |
| File limits | Audit |
| Confirmations in the web (without a console, asking would deny) | Tools blocked by hand |
| | The ones that don't exist in the cloud: that machine is a Render container, not their PC |
| | **Isolation**: being the owner gives no access to anyone's data |
| | **`exige_plan`**: the owner also needs the approved plan |

**The mode without accounts isn't an owner with privileges.** The Morgan on your computer has
the owner role, but there is a console and the confirmations work. The exemptions are only for
the owner **who signed in with their account** where there is nobody to ask, and they live in
a single function (`propietario_con_cuenta`).

### How it's set

`MORGAN_OWNER_EMAIL=you@email`. On startup: if there is already an Owner, nothing; if an
account with that email exists, it's promoted and audited; if it doesn't, **it isn't created**
(a password would have to be invented). You sign up, restart and you're the owner; after that
the variable is no longer needed. **There is no master password, ownership isn't transferred**
if there is already another owner, and a **partial unique index** in the database prevents two
owners even if the code forgets.

### Administrative routes

`GET /admin/usuarios` (`users.read`), `GET /admin/yo/permisos` (so the interface doesn't offer
buttons that will give 403: **it isn't security**), `POST /admin/usuarios/{id}/rol` and
`POST /admin/usuarios/{id}/estado` (`users.manage`).

Even for the owner: you can't change your own role or suspend yourself, create a second owner
or touch the owner's role. A made-up role is **rejected** (when reading from the database, an
unknown one falls back to `user`). **Suspending revokes the sessions at that moment.**

## 5. The local agent: another boundary

Since 3.0, Morgan in the cloud acts on the person's PC through a **local agent** that they
install. It's the most delicate part of the project, and **it doesn't trust the cloud**: even
if the cloud checked everything, the agent checks it again with its own rules, which can only
be changed on the PC. A summary of the layers; the detail, the threat model and the
evaluations are in agente-local.md (in Spanish).

| Layer | What it stops |
|---|---|
| Pairing and an `mga_` credential (encrypted with DPAPI) | Someone else's PC passing itself off as yours, or the cloud sending to the wrong one |
| Local policy (3.2): folders, blocked ones, capabilities, limits | Everything starts closed; blocked wins over allowed; limits only go down; the cloud can't change it |
| Output boundary (3.1) | Secrets covered, names as data, a cap on what goes out, and no website receiving what was read from the PC |
| Approved plan (`exige_plan`) | Writing, running or ending something without the person approving those exact arguments |
| Confirmation on the PC (3.3) | Deleting, running what changes things and ending processes ask for «Allow» in a Windows notification |
| Zones where it never writes | The agent's folder, Startup, the system, the apps' data, `.git`; neither programs nor scripts |
| Catalog terminal without a shell (3.5) | The cloud running anything: there is no PowerShell, and git runs without *hooks* or `fsmonitor` |
| Execution engine (3.4) | Doing something twice, or saying the opposite of what's on the disk |
| Orders with their send time (3.6) | A ghost order: held back in a half-open connection, arriving when the cloud already said it wasn't done |
| Updates signed by me (3.8) | Whoever takes over the cloud (or gets in the middle) making the PCs run code I didn't publish, or rolling them back to an old version with a bug |
| Two-step credential, every 90 days (3.8) | A stolen credential working forever; and rotating it leaving a PC out because of a drop halfway |
| Registry per PC and choosing the computer (3.7) | With several PCs, an order (or its recovery, or its cancellation) on the wrong one; and Morgan picking one without saying so, also while the other reconnects (3.7.5) |

**The terminal of the local Morgan isn't this one.** `execute_command` (§2) runs PowerShell
with a list of forbidden patterns: it works as a net on your computer, with your console in
front of you, but a list of what's forbidden can be bypassed. From the cloud only the agent's
exists.

**Morgan for Windows (5.x)** adds more:

- **One window, and the web only asks for the harmless** (5.3). The program's window loads the
  Morgan web, and the web can ask the program only for what does no harm: the status, pause
  and resume, what it did last, open «What it can do and which folders it sees», go to the
  pairing screen, and the version and its update (`capabilities/web.json`, only for
  `https://morgan-ia.vercel.app`). **Pairing, no**: it's the only thing that gives access to
  the PC, so a compromised web couldn't connect it to another account; it's done on a screen
  of the program itself, which asks whose account the code belongs to before using it. With
  the command list in `build.rs`, **no** command can be asked for without an explicit
  permission. Measured from inside the window, in GitHub: `emparejar`, `consultar`,
  `desemparejar` and `estado` are rejected from the web.
- **Only Morgan loads in the window**: the web over https and the program's screen; any other
  link opens in the browser, and `data:`, `javascript:` or `file:` don't open at all.
- **Signed updates** (5.3): a new version is installed only when you press «Update now», only if
  its signature matches the key the program carries, and the installer of the current version
  is kept (with its signature checked) to go back on its own if the new one doesn't connect
  the PC within 3 minutes.
- Its uninstaller and installer only unpair the agent if it was the program that paired it, and
  never when upgrading.

## 5 bis. Automations: nobody in front (4.14)

An automation acts at a time when nobody is watching: the biggest trust boundary since the
local agent. Design and my decisions in plan-4.x.md (in Spanish).

| Layer | What it stops |
|---|---|
| Creating it is a **red plan** (`create_automation`, `exige_plan`) | A file with hidden instructions leaving something scheduled: the person always approves it, also with the automatic permission |
| **Queries only** in 4.14 (`src/automatizacion/contexto.py`, allow list) | Changing something with nobody in front. The core checks it when offering the catalog **and** when running; nothing that asks for «Allow» on the PC |
| What changes something isn't even proposed (`prevalidar`) | The person approving "delete every night…" thinking it will be done |
| Runs as its owner, **with their role and their quota** | An automation spending without limit or with more privilege than whoever created it; a deleted or blocked account runs nothing |
| The clock calls with **its own secret** (`MORGAN_RELOJ_SECRETO`, compared in constant time; without it, the route doesn't exist) | Someone outside launching runs. Even with the secret, only what was already due runs |
| **Atomic claim** (a counter in the row) | Two crossed clocks running it twice |
| Temporary conversation, without the automatic permission | What it does getting mixed with the person's history, or something being approved "because it was on" |
| **Fixed steps** (4.15): only calls **identical** to an approved step (`contexto.permitida`) | Doing something different from what was approved: another path, another tool, or "another way" after a failure. The model is offered no tool |
| Only green and yellow steps; no queries, plans, tasks or memory | Scheduling something that asks for «Allow» (and wouldn't be done) or that plants something in Morgan |
| Only `{fecha}` and `{hora}`; arguments checked against each tool's schema | Approving a step that would never go out, or writing unreplaced placeholders |
| The notice of a failure is the real report, without a model | The tray telling what didn't happen (measured: the model made it up) |

## 6. Known limits

- **Without `MORGAN_API_TOKEN` or accounts, the API requires nothing.** Acceptable only when
  listening on `127.0.0.1`. In the cloud, startup **aborts** without one of the two.
- **The "login CSRF" isn't covered**, knowingly ([autenticacion.md](autenticacion.md)).
- **Sign-up is open** (my decision). The daily total is bounded by the **global quota**
  (2.0.24: 150 messages across every account that isn't the owner's), and the money by the cap
  in OpenAI's dashboard.
- `MODERATE_PERMISSION_MODE=auto` removes the confirmation of writes and commits; the
  validations remain.
- **The plan that authorizes writing on the PC is required by the cloud.** A compromised cloud
  could ask to create, edit or move without a plan; with my decision (only deleting, running
  and ending are confirmed on the PC), that isn't confirmed there. The PC's policy stops it;
  whoever wants more switches on `confirmar escribir si` (accepted by me in 3.3.5).
- **The policy file belongs to the Windows user**: a program already running with their
  permissions can edit it. Out of the threat model (a PC with malware running with your
  permissions is out of reach).

## Tests

`test_permissions.py`, `test_permiso_confirmacion.py`, `test_security_advanced.py`,
`test_regressions_audit.py`, `test_roles.py` (47), `test_privilegios_owner.py` (24),
`test_web_comprimida.py`, and the isolation and throttling ones listed in
[autenticacion.md](autenticacion.md). For the local agent: `test_agente_*.py`,
`test_motor_politica.py`, `test_motor_ejecucion.py`, `test_canal_agente.py`, `test_bandeja.py`
and the stress battery (`test_estres_ejecucion.py`, with `MORGAN_ESTRES=1`).
