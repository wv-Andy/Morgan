# How the agent works: the turn, tasks, plans and verification

**English** · [Español](agente.es.md)

> Brings together what used to be four documents: tasks (V1.5), planning (V1.6),
> verification (V1.7) and the turn's streaming (V2.0.14). Last review: 2026-09-25, V3.5.0.

```
Request → (plan, if it needs approving) → tools → verification → answer
                                  └─ everything is recorded in a task ─┘
```

## 1. The turn

The agent doesn't run one tool per message: **it iterates**. It asks for a tool, reads the
result, decides whether it needs another one and chains them until it has the answer. It stops
when it's done, when it repeats the same call with the same arguments three times, or when the
turn's time runs out.

**What already failed isn't repeated** (3.1). A call with the same tool and the same arguments
that has just failed **isn't run again**: the model gets back the error from the first time and
is told to try another way. Other arguments, or another tool, are tried; and what works can be
repeated as many times as needed. Measured on my PC on 2026-09-19: `copy_file` failed with an
internal error and the model repeated it **four times** with the same arguments until it ran
out of rounds and answered "I have reached the maximum number of steps". Repeating what has
just failed gives the same error; telling it so leaves it rounds to look for another way out.

| Time limit | Local | Cloud, `/chat` | Cloud, `/chat/stream` (the web) |
|---|---|---|---|
| Turn cap | 180 s | 85 s | **170 s** |
| Why | — | A silent answer has to fit in the 120 s that Vercel's proxy tolerates | With heartbeats there is no silence, and what was measured reaches 180 s |

**There is a single agent for every user.** That's why everything that belongs to "this
turn" —the user, the time measurement, the event channel— lives in a `ContextVar`, and the
turn's thread receives it with `copy_context()`. Attaching it to the shared agent would make
Ana's events show up in Bea's answer.

### «Stop» (3.4)

The turn **doesn't stop** because nobody is listening: it finishes in its thread and is saved
(decision B of the streaming). But the web's «Stop» button does stop **what the turn is doing
on the PC**: the `inicio` event brings a `turno`, and `POST /chat/parar` with it cancels the
running order at its next safe point and lets no new orders from that turn go out to the PC
(`src/canal/paradas.py`). Only the button: a connection that drops (switching apps on your
phone) stops nothing.

**One turn at a time per conversation (4.5).** Since the turn continues even if nobody is
listening, a retry after a drop launched another turn of the same thing with the first one
running (measured in my test: one question processed 3 times). `/chat` and `/chat/stream`
mark the conversation as busy **before** counting the message against the quota, and free it
when the turn really ends (not when someone stops waiting for it); meanwhile, another turn in
it gets `409 TURNO_EN_CURSO`. The web waits for the answer instead of retrying (web.md §3).

### Streaming: `POST /chat/stream`

I approved it with three decisions: a 170 s cap in the cloud, «Stop» stops watching but the
turn finishes and is saved, and **no word-by-word text** (the long time is in the tools, not
in writing the answer).

One JSON line per event (NDJSON). SSE (it only allows `GET`) and WebSocket (it isn't measured
that Vercel's proxy forwards them) were ruled out.

| Event | When |
|---|---|
| `inicio` | Right at the start, **before any wait**: if it doesn't arrive straight away, there is a buffer in the middle |
| `pensando` | Every round to the model |
| `herramienta` | When each one starts and ends. **Only the name**: an argument might carry a secret |
| `respaldo` | When a model other than the main one answers |
| `latido` | After 10 s without another event |
| `fin` / `error` | The result, or `code` and `message` **without** the exception's text |

Measured with a real turn: each event arrives ~50 ms after being emitted and the heartbeats go
out every 10 s exactly. That measurement uncovered another defect: a gzip-compressed page was
read as text and weighed 25,789 tokens (fixed in 2.0.15; the same turn went from 34.5 s and a
failure to 5.1 s).

**Whoever listens doesn't hold a thread** (V2.0.27). The stream's generator is asynchronous: it
waits in the event loop and the turn's thread wakes it up when it enqueues
(`CanalDelTurno.al_poner`). When it was synchronous, Starlette iterated it in its pool of 40
threads —the one for every synchronous route— and each open turn held one. The load test
measured it: with 60 long turns, `/health` took 9.7 s and the `inicio` of a new turn 11.5 s;
afterwards, 0.2 and 3.3 s (mediciones.md, in Spanish).

The web uses **a single `fetch`** for everything, and translates the tool's name into a sentence
("Searching the internet…"). If the stream fails, it shows the error: it doesn't secretly retry
with `/chat`, because it would pay for the model twice.

## 2. Tasks

A long job leaves a record: what was tried, with which tool, what it returned and what failed.
That's what lets Morgan say "I couldn't" instead of "done".

```
pending ──► running ──► completed
             ├──► waiting ──► running
             ├──► failed ────► retry ──► running
             └──► cancelled ─► retry ──► running
```

- **Transitions are validated.** Without that, retrying a completed task would reopen it.
  At most **three attempts**: a task that always fails has to end up saying so.
- **Steps record themselves.** The model used to ask for them with `advance_task`, and each
  step cost a round trip to the provider: a three-step job took 126 s. The agent already knows
  what it ran; removing it brought it down to 106 s.
- **Progress is computed, not stored.** A completed task is at 100 % even if it had planned
  steps left over.
- **Orphans close themselves**: a live task with no news in ten minutes (the turn died) is
  closed as failed when listing.
- **There is no generic `update_task`** and tasks aren't created through the API: they would
  allow writing states that don't match anything that ran. A state conflict returns 409.

Tools: `create_task`, `get_task`, `list_tasks`, `complete_task`, `fail_task`, `cancel_task`,
`retry_task`. The **Tasks** view refreshes itself while any is alive.

## 3. Plans: saying what will be done before doing it

**Approving is the order** (4.0-A, my decision). When you approve in the web, the turn is sent
on its own with `ejecutar_plan`, and **the agent runs the steps**, not the model: in order, with
the approved arguments, along the same path as any call (`_ejecutar_una`: permissions, the
plan's authorization, verification, recording). **It stops at the first step that doesn't
work** (4.0.5). If everything worked, the summary is written by the core ("Done: create the
notes (checked).") and the model isn't called (4.1.5, my decision); if something failed or
there is something to explain or ask, the model receives the report ("1. create the notes
(create_file): done, and checked") and tells the person. Only a plan from this conversation
and approved. **When proposing it** the same happens (4.2): if `create_plan` leaves a pending
plan with no warnings, the core closes the turn ("I propose this plan…"), because the web
already shows it in full above the chat.

**The risk is set by the tool registry, not by the model.** If it came in the proposal,
writing `"riesgo": "safe"` next to a `delete_file` would be enough. An unknown tool counts as
**critical**.

| Plan | What happens |
|---|---|
| All reading | Born **approved** and runs right away. Making people approve "I'm going to read three files" teaches them to approve without looking |
| Some step moderate or above | **Pending**: it shows up above the chat until the person decides |

```
draft ──► pending ──► approved ──► running ──► completed / failed
              └──────────────┴──► rejected
```

- **It's approved in the interface** because in the web there is no console to ask. And the
  decision is about the whole job, not about loose questions.
- **The decider is shown masked arguments** (secrets as `********`, cut at 120 characters),
  but they're stored whole: running with truncated arguments would be a silent failure.
- **The plan's conversation is set by the agent**, not the model, which doesn't know it.
  Asking the model for it left the plan orphaned and the next turn no longer offered
  `get_plan`.
- **A rule in the prompt is a suggestion; an answer at the moment of failure is a path.** The
  V1.6 gate saw a model go straight to deleting and ignore the prompt's rule. What works is
  that, when the tool is denied, the system tells it "use `create_plan` with these steps".
- When rejected, the model is told **not to propose an equivalent one**.
- **A step may use no tool** ("ask the person"): the step's optional fields accept `null`
  (2.0.32). With the schema as `string` only, Groq rejected the whole call with a 400 and the
  turn fell back: 24-35 s per plan, measured
  (mediciones.md, in Spanish).
- **If a step uses a tool that doesn't exist here**, it's stored anyway (it counts as
  critical), but `create_plan` tells the model which ones exist so it redoes the plan or
  explains it. In the cloud it proposed `move_file` and `mkdir`.
- **In the cloud the prompt says there is no computer** (2.0.33): when the catalog has no tools
  for the computer, "Where you work: in the cloud" is added. Without it, it promised a new
  person commands "with your authorization" and processing CSVs with pandas. It's decided from
  the catalog, like the rest of the prompt.
- **The prompt asks to propose the plan without exploring first**: measured, the model looked
  at files, knowledge, memory and repositories "just in case" and chained up to seven calls.

### What acts outside Morgan requires a plan (V2.0.16)

A tool with `exige_plan = True` runs **only** if in this conversation there is an approved
plan with a step for that tool, **with the same arguments** and **not run yet**. It applies to
everyone, the owner included. The approval is only used up if the tool works, and a tool that
requires a plan never counts as safe (otherwise its plan would approve itself). It's used by
**the ones that change things on the person's PC**: the five for writing (3.3),
`run_change_command` and `kill_process` (3.5). Calendar, which introduced it, is parked.
Tested in `tests/test_exige_plan.py` (10 of 10 mutations). The web shows each step's arguments
as they are, with their line breaks: what is going to be written is approved by seeing it.

Routes: `GET /planes`, `GET /planes/{id}`, `POST /planes/{id}/aprobar`,
`POST /planes/{id}/rechazar`. Someone else's gives **404**, not 403: saying "it exists but it
isn't yours" would reveal that it exists.

## 4. Verification: checking that it really happened

`success: True` means that **the call didn't fail**, not that the goal was met. So after each
tool the agent looks at the effect **against the real world**, without asking the model:

| Tool | What is checked |
|---|---|
| `create_file` | Does it exist? Does it have content? |
| `delete_file` | Did it disappear? |
| `patch_file` | Is the new text there? |
| `run_tests` | Does the output say "failed"? |

| Verdict | Does it block completing the task? |
|---|---|
| `correcto` (correct) | No |
| `incorrecto` (incorrect) | **Yes**, and the message says which step and why |
| `no_verificable` (not verifiable) | No. **And it isn't "correct"**: a system that calls verified what it didn't check is worse than one that doesn't verify |

**If the tool says success and the effect says no, the effect wins**: the result becomes
`success: False` and the model can react. In the interface, only what was checked gets a seal.

**Copying, moving and renaming** are verified too (4.0-B): the copy exists and has the same
size as the original; what was moved or renamed is no longer at the source and is at the
destination.

**What is done on the person's PC is checked there** (4.0.0-dev). The local agent's tools carry
`en_el_pc`, and their verdict comes from what the agent checked on its disk: the sha256
fingerprint of what was written, or the Recycle Bin. **Never from the cloud's disk**: until
then it was checked with `os.path.exists` on the server, and in production (Render, Linux) a
file correctly created on the PC was reported as not created. Since 4.0-B, the agent also says
what it checked (`comprobado`) when creating a folder, moving, running a command that changes
something or ending a process.

Verified with the real model: given a drive that doesn't exist, Morgan answered "I couldn't
create the file. Drive `Z:\` doesn't exist", instead of "done".

### Correcting, with a cap (4.0-C)

I decided that, if something that changes things doesn't work —the tool fails or the
verification says the effect isn't there—, the model gets **one** attempt with another way,
and it's told so at that moment. If that attempt doesn't work either, **no other change runs in
the turn** and it's asked to tell the person what it tried. Querying is still possible, and an
attempt that works closes the matter. "Changing" means moderate risk or above and whatever
requires a plan (which still doesn't run without approval). Identical calls were already
blocked since 3.1. Tested in `tests/test_corregir.py`.

### Context in the plan (4.0-D)

An approved plan runs as it is, so **what's needed to write it is read beforehand**, without a
plan: an attachment, a file, a document from its knowledge. `create_plan` rejects a plan with a
query before a change (`CONSULTAS` in `src/tools/planificacion.py`): the change would be
waiting for data that never arrives, and the model filled the gap with a placeholder that
ended up on the disk. A query after the changes, or a plan of queries only, are left alone.
And in the turn that runs an approved plan that went entirely well, another one can't be
proposed: it repeated what was already done. Tested in `tests/test_contexto_en_el_plan.py`;
measured in `mediciones.md`.

## What it doesn't do yet

- **It doesn't learn from rejections** from one conversation to another.

## Tests

| File | What it pins down |
|---|---|
| `tests/test_tareas.py` | States, transitions, automatic recording |
| `tests/test_planificacion.py` | That the risk written by the model is ignored, states, approval |
| `tests/test_planes_de_la_conversacion.py` | That the agent ties the plan to its conversation, through the real loop |
| `tests/test_exige_plan.py` | Same arguments, once, this conversation |
| `tests/test_verificacion.py` | That not verifiable is neither correct nor failed |
| `tests/test_ejecutar_plan.py` | The approved plan runs on its own, with its arguments (4.0-A) |
| `tests/test_corregir.py` | One attempt with another way, and not one more (4.0-C) |
| `tests/test_contexto_en_el_plan.py` | Query before planning; no repeated plan after running (4.0-D) |
| `tests/test_memoria_por_persona.py` | One account's memory never goes into another's turns (4.0) |
| `tests/test_chat_stream.py` | Event order, heartbeat, isolation between two users, error without the exception |
