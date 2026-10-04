# Models: providers, chain, quota and router

**English** · [Español](modelos.es.md)

> Brings together what used to be the LLM providers document and the model router one. Last
> review: 2026-09-25, V3.5.0. The measurements behind it are summarized in
> mediciones.md (in Spanish).

## 1. The chain

The Core talks to the `LLMProvider` interface (`src/models/base.py`); each provider translates
at its boundary. `FallbackProvider` tries the links in order, **filters by capability** (an
image never goes to a model without vision) and **postpones the one known to be exhausted**.

| Link | Role | Model | Variable |
|---|---|---|---|
| **Groq** | Main, free | `openai/gpt-oss-120b` | `GROQ_API_KEY`, `_2`, `_3`… |
| **Groq, relief** | If the main one runs out of quota (V2.0.20) | `openai/gpt-oss-20b` | `GROQ_MODELOS_RELEVO` |
| **Gemini** | Free fallback (20 requests/day **per project**). The only one with vision (Groq no longer serves vision models, measured 2026-09-27) | `gemini-3.6-flash` | `GEMINI_API_KEY`, `_2`, `_3`… (4.1.5): each one from another project adds 20 |
| **OpenAI** | **Last, and paid** | `gpt-5.6-luna` | `OPENAI_API_KEY` |
| NVIDIA | Exists, switched off | `deepseek-v4-pro-0813` | Add `nvidia` to `MORGAN_LLM_ORDER` |

Default order `MORGAN_LLM_ORDER=groq,gemini,openai`, defined in a single place
(`Settings.llm_order`). **The rule: the fastest free one first and the paid one last.** OpenAI
isn't last for being worse —it's the most reliable, 1.0 s for text and 1.7 s with tools— but
because it charges: what it buys is that there are no turns without an answer when the free
ones run out.

Without any key, Morgan starts in **degraded mode**: `/chat` gives 503 and everything that
doesn't depend on the model keeps working. **A local model is ruled out.**

`etapas` (stages) says **who really answered** (the `model` field used to return the whole
chain and claimed "Groq" while the fallback answered). The web **doesn't show the model** since
2.0.29 (my decision): if the backup answered, only the answer's time explains it when you hover,
without names ([web.md](web.md#2-structure)).

## 2. Time limits

Before V1.3 the clients inherited the SDK's timeouts and a turn could hang **for more than 15
minutes**. **Capping iterations doesn't cap time**: the cap that guarantees it is the turn's,
by clock.

| Variable | Default | Caps |
|---|---|---|
| `MORGAN_LLM_TIMEOUT` | 30 s | One call (Groq's normal one is 0.42 s) |
| `MORGAN_LLM_MAX_RETRIES` | 1 | SDK retries. **Groq goes with 0** (below) |
| `MORGAN_TURN_TIMEOUT` | 180 s local, 85 s cloud | The turn of `/chat` |
| `MORGAN_HTTP_DEADLINE` | no cap local, 100 s cloud | When the request answers; the turn continues |
| `MORGAN_STREAM_TURN_TIMEOUT` | 180 s local, 170 s cloud | The turn of `/chat/stream` (the web) |
| `MORGAN_MAX_ITERATIONS` | 6 | Rounds to the model. If they run out, a last call **without tools** answers with what was found (4.20) |

Why 85 and 170 in the cloud: [web.md](web.md#the-proxys-120-seconds) and
[agente.md](agente.md#1-the-turn).

## 3. Quota: counting it and not paying twice to know it

`src/models/cuota.py`. It counts the tokens that already came in each response, **warns at
80 %** of Groq's known cap (200,000/day) and **postpones the exhausted one** by reading the
"try again in" of the 429 itself. It doesn't warn for OpenAI: its owner sets its limit in the
dashboard.

**It reorders, it doesn't filter.** The exhaustion window is an estimate, and the mistakes
don't cost the same: calling an exhausted one costs 0.02 s; skipping one that worked leaves
Morgan without an answer. The exhausted one goes last, never out.

**Groq has two limits, and mixing them up cost money.** Measured: a turn cost 93 s and OpenAI
money for leaving Groq over a wait of tenths of a second.

| Limit | What the 429 says | What is done |
|---|---|---|
| 8,000 tokens per **minute** | "try again in 112ms" … "9.25s" | **Wait**, up to a 10 s clock budget |
| 200,000 tokens per **day** | "try again in 10m53s" | Move to the next link |

The order, from cheapest to most expensive: **another key** (free and instant), **waiting** (if
it fits in 10 s), **giving up**. The threshold comes from measuring the paths: waiting 0.1–9.3 s
for free; the SDK retrying, 13–26 s; Gemini, 29–36 s and a 504; OpenAI, 1–1.7 s and paying.

- **Groq's SDK runs with `max_retries=0`**: retrying on its own, 6 calls took 83.4 s instead of
  3.7, and it also **swallowed the 429** and the second key was barely used.
- **A 401 isn't exhaustion**: an invalid key isn't fixed by waiting.
- **A minimal request says nothing about the daily quota.** The headers are for the per-minute
  limit; 73 tokens fit when a 3,100-token turn no longer did.
- **It lives in memory on purpose**: persisting it would cost 45 ms per call to save 20.
- **A provider that is down rests** (4.20): a timeout or a connection error marks it as down
  for 30 s, doubling up to 10 minutes, and it goes last meanwhile. Before, a dead provider paid
  its 30 s on every request.

## 4. Several keys: the keyring (V2.0.4)

`src/models/llavero.py`. **Groq's quota belongs to the account**: two keys from two accounts are
two quotas. It was checked with the daily **request** counter (the token one refills in 570 ms
and tells nothing apart): the new key read 999, not 996. There are several accounts; each key
in `.env` (`GROQ_API_KEY`, `_2`, `_3`…) is one more quota, and `/status` says how many are
loaded.

- **They're used in series**, not spread out: that way the second one is a real reserve and
  the 80 % warning arrives in time.
- **It only rotates because of quota.** A 400 or 401 would happen to all of them alike.
- **Rotation lives in the provider, not in the chain**: changing key isn't changing model and
  mustn't be announced as a fallback.
- It's recorded per key (`Groq#1`, `Groq#2`), **never with the key inside**.
- `/status` says **how many** keys are active, so a badly copied one is noticed.
- Audio transcription uses only the first key (Whisper quota, not measured).

## 5. The keys are checked at startup

`src/models/claves.py`. When production was recreated, 18 secrets were typed by hand and the
Gemini key had **a pound sign inside**. Symptom: everything worked… paying OpenAI on every turn,
without anything saying so.

What **can't** work is rejected, without going out to the network: non-ASCII characters, spaces
or line breaks inside, control characters, fewer than 16 or more than 512 characters. Each bad
key falls on its own, with the name of **its** variable and level ERROR, and the provider is
built with the good ones. **It doesn't promise the key works**: only the provider knows that,
on the first turn.

## 6. The model router (V2.X of the master roadmap)

```
Morgan → LLM Router → fast | reasoner | vision | audio | cheap
```

| Branch | Status |
|---|---|
| **vision / audio** | ✅ The chain filters by capability |
| **cheap** | ✅ The chain's order |
| **Relief by capability** | ✅ **V2.0.20**, measured |
| **fast / reasoner** (choosing per task) | ⏸ Waiting for the evaluation cases |

### Why there is a relief

The roadmap said not to bring the router forward until there were **really different
profiles**. It was measured and there are: **Groq's quota is also per model** (with the same
key, 120b went from 996 to 995 while 20b spent from its own). So `gpt-oss-20b` is another daily
and per-minute quota, for free.

| Candidate | Result |
|---|---|
| `gpt-oss-20b` | ✅ 0.44 s, and **5 out of 5 real Morgan turns with tools** |
| `qwen3.8-27b` | ❌ The free account limits its output to 1,000 tokens/min and Morgan asks for 2,048: it always fails |
| `allam-2-7b` | ❌ Got a multiplication wrong |
| `groq/compound` | Only 250 requests/day |

### How it works

```
groq (120b) ──quota──▶ groq@20b ──quota──▶ gemini ──▶ openai (paid)
     └──down or another error─────────▶ gemini
```

- **Only after a quota rejection.** With Groq down, another of its models would fail the same
  way. With the main one already known to be exhausted, it starts with the relief.
- **The relief's keys are recorded with the model** (`Groq@openai/gpt-oss-20b#1`): the key
  exhausted for 120b isn't for 20b.
- **It's announced as a fallback.** That uncovered an earlier defect: with the main one
  exhausted the list was reordered and the fallback answered without notice.
- An empty `GROQ_MODELOS_RELEVO=` turns it off.
- **A rejection for size isn't tried on another model of the same provider** (3.1,
  `es_rechazo_por_tamano`). Groq says "you went over the quota" the same way as "this request
  doesn't fit", but waiting doesn't fix a request that is too big and Groq's other model has
  the same 8,000 tokens per minute cap. Measured on 2026-09-19 with my PC connected: a call of
  8,459 tokens failed on 120b and on 20b before reaching Gemini. Now it goes straight to
  another provider.

**An estimate, not a measurement at exhaustion:** the free ceiling goes from ~105 to ~210 turns
a day with three accounts, and the per-minute limit doubles too. 20b is only a relief because
its quality on long tasks hasn't been measured.

### What's missing to choose per task

Evaluation cases (my decision), measuring 120b and 20b on them, and a rule **only if the
measurement justifies it**. Guessing which turns "are easy" would degrade answers for a quota
that doesn't run out today.

## 7. Details of each provider

**Groq.** Tool results correlated by `tool_call_id`. Without tools you have to **omit** `tools`
and `tool_choice`, not send them as `null` (400).

**Gemini.** `raw_parts` is preserved so as not to lose the `thought_signature`; **every** tool
call is collected; a blocked answer is translated into text. A tool call without its signature
is sent back as a note to the person, not as a model turn (4.20).

**OpenAI (V2.0.3).** `gpt-5.6-luna` rejects `max_tokens` (it uses `max_completion_tokens`),
`temperature` other than 1, and tools without `reasoning_effort: "none"`: without it, **tools
don't work**. The three rejections are 400s and cost nothing to find out. Spending brakes:
`MORGAN_OPENAI_MAX_SALIDA` (2,048 per call), `MORGAN_OPENAI_TOPE_TOKENS` (200,000 per window,
in memory: it's a brake against loops, not a spending limit) and **a timeout isn't retried**,
because the answer may have been billed. The real limit is the one in OpenAI's dashboard.
**It's never tested against OpenAI.**

**NVIDIA (off since V2.0.3).** In production, **15 calls, 15 timeouts**, even though from home it
answers in 3 s: the effect was measured, not the cause. Unpredictable latency, so if it comes
back, it goes last. With `thinking: false` it's usable; with `true`, `deepseek-v4-flash` timed
out five times out of five. If `content` arrives empty and there is reasoning, the reasoning is
used.

## Security and known limits

- Keys only in `.env` and Render. `Settings.redacted()` says whether they're there, never their
  value. The log redacts `gsk_`, `AIza`, `sk-`, `api_key=`, `token=`, `password=`.
- The model's status isn't checked with a real call: `(3 claves)` says they're well formed, not
  that they have quota left.

## Tests

`test_llm_chain.py`, `test_fallback_provider.py`, `test_cuota_de_proveedores.py`,
`test_cuotas.py`, `test_llavero_de_claves.py`, `test_claves_de_proveedor.py`,
`test_router_de_modelos.py` (18, 8 of 8 mutations), `test_llm_errors.py`,
`test_llm_resilience.py`.
