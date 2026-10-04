# Authentication and accounts

**English** · [Español](autenticacion.es.md)

> How people get into Morgan, how passwords are stored and what keeps one person's data apart
> from another's. **Identity, brought forward from V2.0.**

Morgan has its **own** access system: username or email, and a password. There is no external
identity provider.

## Why its own and not "sign in with Google"

Supabase Auth with Google and GitHub was evaluated and ruled out for complexity: it forces you
to register applications in two outside dashboards, to keep secrets in three places, to deal
with return URLs that fail with messages that explain nothing, and to have the project's startup
depend on Google's verification screens. For a personal assistant that wants to open up to a
few users, it's a lot of machinery.

**This doesn't close the door to Google or GitHub.** On the contrary, and it's worth insisting
because it's the most expensive confusion in this area:

| | What it means | Status |
|---|---|---|
| **Sign in with GitHub** | GitHub confirms who you are, and that's how you get in | Ruled out |
| **Connecting GitHub to Morgan** | Morgan reads your repos, your *issues* and your PRs | ✅ Done in V1.9, and in use: [integraciones.md](integraciones.md) |

They're different things and they're designed separately. The second is an **integration**:
its own table, its own permissions, hanging from your Morgan account. Mixing them —your
identity *being* your GitHub account— is what later forces you to redo everything when someone
wants to sign in with an email, or connect two services, or disconnect one without losing
access.

## The pieces

```
Browser                       API                        Storage
───────                       ───                        ───────
morgan_sesion cookie  ──►  identidad_middleware  ──►  auth_sessions
(HttpOnly)                        │                    (id = hash of the token)
                                  ▼
morgan_csrf cookie    ──►  fijar_usuario(id)      ──►  ContextVar
(readable)                        │
                                  ▼
X-Morgan-CSRF         ──►  every query filters by user
```

| File | What it does |
|---|---|
| [`src/identidad/password.py`](../src/identidad/password.py) | Hashing and validation of passwords and identifiers |
| [`src/identidad/cuentas.py`](../src/identidad/cuentas.py) | The policy: sign-up, sign-in, sessions, recovery |
| [`src/identidad/repositorio.py`](../src/identidad/repositorio.py) | Where it's stored: SQLite or Supabase |
| [`src/identidad/correo.py`](../src/identidad/correo.py) | Really sending the recovery link |
| [`src/api/sesion_web.py`](../src/api/sesion_web.py) | Cookies, CSRF, the request's origin |
| [`src/api/identidad_middleware.py`](../src/api/identidad_middleware.py) | Who makes each request, and what happens if nobody |
| [`src/api/routes/cuentas.py`](../src/api/routes/cuentas.py) | The `/auth/*` routes |
| [`src/identidad/tokens.py`](../src/identidad/tokens.py) | Personal API tokens: creating, resolving, scopes and what a token can never do |
| [`web/src/components/Cuenta.tsx`](../web/src/components/Cuenta.tsx) | Sign-in screen and session state |
| [`web/src/components/PanelCuenta.tsx`](../web/src/components/PanelCuenta.tsx) | Account, password and sessions, inside Settings |

## Passwords

It uses **`scrypt`**, from `hashlib`, standardized in RFC 7914. It's an algorithm designed for
passwords: expensive in memory as well as in time, which is what makes attacks with graphics
cards costly.

It was chosen over `bcrypt` and `argon2-cffi` so as not to add a dependency with a C extension.
All three are defensible; this one doesn't force compiling anything on Windows or in Render's
container.

The format stores the parameters next to the hash:

```
scrypt$16384$8$1$<salt in base64>$<hash in base64>
```

That way, if in two years the cost has to go up, old passwords are still verified with their
original parameters and **rehash themselves when signing in**, taking advantage of the only
moment the password is in plain text.

Measured on the development computer: **44 ms per hash**. Enough to make a brute-force attack
expensive without signing in feeling slow.

**At most 4 hashes at a time** (V2.0.27). Each one reserves 16 MB, and the load test measured
498 MB with 25 simultaneous sign-ups: Render's free plan has 512. A peak of sign-ins —failed
ones too— would have brought the service down, and the throttles don't prevent it because they
count attempts, not concurrency. With the semaphore the ceiling for hashes is 64 MB, and the
server's measured peak with 60 sign-ups at once, 176 MB. It costs no time: the computation is
CPU-bound, and more in parallel just shared the same CPU
(mediciones.md, in Spanish).

Requirements: at least 8 characters, at most 200. **Uppercase, numbers or symbols aren't
required**: those rules push people towards short, predictable passwords like `Passw0rd!`.
Length is what matters.

## Sessions

- They live in an **`HttpOnly`** cookie. Storing them in `localStorage` would put them within
  reach of any injected script, and in an application that renders a model's text that isn't a
  remote hypothesis.
- The database stores the **hash** of the identifier, not the value. Whoever reads the database
  mustn't be able to impersonate anyone, just like with passwords.
- They last **30 days**. Asking for the password every week brings no real security and pushes
  people to pick simpler passwords.
- They're invalidated **on the server** when signing out. Just deleting the cookie would leave
  the session alive for thirty days for whoever had a copy of the token.
- Changing or recovering the password **closes the other sessions**. If someone had stolen it,
  changing it would be useless while their session stayed alive.

## Where the API lives, and why it matters for the cookie

**The browser has to see the API on the same origin as the page.** It isn't an architectural
preference: it's the condition for the session to exist.

The frontend is on Vercel and the API on Render, which are different domains. With the web
calling Render directly, the session cookie is a **third-party cookie**, and Safari on iPhone
discards them without exception. The effect was exact: you signed up and the web sent you back
to the sign-in screen forever, without any error anywhere. The whole story is in
[web.md](web.md).

That's why `vercel.json` forwards `/api/*` to Render **from the same origin**. The page calls
`morgan-ia.vercel.app/api/auth/yo`, and the cookie that comes back belongs to
`morgan-ia.vercel.app`: first party, and no browser argues with it.

What you need to know if you touch this:

- The proxy goes **before** the SPA's catch-all rule in `vercel.json`. After it, it's never
  applied.
- The edge **doesn't cache** `/api` (`x-vercel-enable-rewrite-caching: 0`). A cached session
  response served to another person would be a leak between accounts.
- The frontend decides its URL **at build time**, not in Vercel's dashboard.
- [`test_proxy_mismo_origen.py`](../tests/test_proxy_mismo_origen.py) pins it.

## CSRF

In the cloud the cookie is still marked `SameSite=None; Secure`. It's no longer needed to cross
domains —the proxy unified them— but it's kept: the API can also be reached directly, and a
cookie that only works through Vercel ties the session to a specific deployment.

`SameSite=None` means the cookie also travels in requests caused by another website, so **the
protection against CSRF has to be somewhere else**.

The defense is the **double submit**: besides the session cookie there is a CSRF token that the
client repeats in the `X-Morgan-CSRF` header. Someone else's website can cause the request, but
it can't **get** the token to fill in the header, nor set it without triggering a *preflight*
that CORS rejects.

Locally, `SameSite=Lax` is used and the cookies aren't marked `Secure`: `http://localhost` isn't
HTTPS and the browser would discard them, with the effect of not being able to sign in during
development.

### Where the client gets the token from

From two places, and the second exists because of a bug that left **the whole web read-only
during all of V1.8**:

1. **The `morgan_csrf` cookie**, if it can read it. It's the direct source and always up to date.
2. **The JSON of `/auth/login`, `/auth/registro` and `/auth/yo`**, which return it in the `csrf`
   field. The client stores it in `localStorage`.

The first path **only works when the web and the API share a domain**. Today they do, thanks
to the proxy; during all of V1.8 they didn't, and that's where the bug was: the cookie belonged
to `morgan-api.onrender.com` and the JavaScript ran on `morgan-ia.vercel.app`. The browser *sent*
the cookie on every request —so from the backend everything looked right— but `document.cookie`
**couldn't read it from another domain**. The header was never set, and **every** POST was
rejected with 403.

The second path stays even though the first already works. It costs little, and it's what holds
the session up when a browser clears the readable cookie on its own — which is exactly the case
that left the web read-only.

The symptom didn't look like the cause. The session was open, `/auth/yo` answered fine, the GETs
worked and the screen showed your name. Only *doing* things failed: sending a message, creating
a conversation, uploading a file, renaming. From the outside it read as "Morgan thinks I haven't
signed in".

**Returning the token through `/auth/yo` doesn't weaken the defense.** The double submit protects
because someone else's website can't get the token, and it still can't: `/auth/yo` only answers
the origins in the CORS list —checked live: to any other it doesn't return the
`Access-Control-Allow-Origin` header, and without it the browser stops it from reading the
response—. It's the same same-origin policy that made the cookie unreadable, applied one step
higher.

`/auth/yo` **issues a new token** if there is a live session and the cookie is missing. It
really happens: browsers that clear third-party cookies take this one and leave the session one,
and without replacing it the session stayed read-only until signing in again. Whoever has no
session gets none: if asking for it were enough, the double submit wouldn't protect against
anything.

### What this design doesn't cover

The entry routes —`/auth/login`, `/auth/registro`, `/auth/recuperar`, `/auth/restablecer`,
`/auth/logout`— are **exempt** from the CSRF check. They have to be: requiring a token that can
only be obtained with a session would make signing in impossible, and it trapped whoever came
back with an expired cookie.

The price is the **"login CSRF"**: someone else's website can force you to sign into *the
attacker's account* without you noticing. It's accepted knowingly. Avoiding it requires issuing
a token before a session exists, with its own storage and its own expiry, and the harm —working
unknowingly in someone else's account— is visible as soon as you look at the name on the screen,
unlike data theft.

## Brute force

Two caps in the same fifteen-minute window, and the lockouts lift themselves:

| Cap | Counted | What for |
|---|---|---|
| **5 failures** | per account **and origin** | So someone who gets it wrong doesn't lock out everyone else from another place |
| **20 failures** | per account, **from any origin** (V2.0.22) | So the one above can't be dodged |

The origin is taken from `X-Forwarded-For` when it exists, because behind Render or Vercel
`request.client.host` is the proxy and would be the same for everyone — counting by it would lock
out every user at once.

> **This used to say that faking that header "only serves to not accumulate attempts, which is
> the worst case of this protection, not a new flaw".** Not accumulating attempts is having no
> brake. The 2.3 audit measured it: with a different `X-Forwarded-For` on each attempt, 20 wrong
> passwords in a row gave 401 —not one 429— and the right one got in afterwards. That's why the
> second cap exists, which doesn't depend on any header.

**Failures are recorded against the account, not against what was typed.** With the username
and the email as separate keys, alternating them gave twice the attempts.

**The price, accepted knowingly:** whoever attacks an account can leave it locked for fifteen
minutes, also for its owner. It's a temporary lockout and not a theft, and **resetting the
password lifts it** on the spot.

Getting it right clears the previous attempts from that origin. Without that, whoever fails four
times and gets it right would stay one failure away from the lockout forever.

> **A defect that only appeared by exercising it.** The first version recorded the failed attempt
> and raised the exception inside the same `with` block, and `Database.connect` only commits the
> transaction if the block ends well: each record was rolled back with its own failure, the
> counter never went past zero and **the brake braked nothing**. Reading the code it looked
> correct. It's pinned in `TestElFrenoALaFuerzaBruta`.

## Sign-ups: registration is open, and throttled (V2.0.22)

I decided that anyone with the link can create an account. The 2.3 audit measured what a script
could do with that: **30 accounts in 4 seconds** from the same origin, each with its daily message
quota. Enough to use up everyone's free quota in minutes and push the rest to the paid provider.

| Cap, in 15 minutes | Why that one |
|---|---|
| **3 sign-ups per origin** | A family or a class behind the same IP can create their accounts |
| **20 sign-ups in total** | Doesn't depend on any header: it's the one that bounds a script that changes origin |

Only successful sign-ups count: a badly filled form doesn't spend anyone's quota. They're recorded
in `login_intentos` with the key `#registro`, which can be neither a username nor an email, and
sign-in rejects it without recording anything: otherwise, twenty failures typing it would close
registration for everyone. (The first version used `__registro__`, which is a valid username.)

If it's reached, the web receives `429 DEMASIADOS_REGISTROS` with a message that says how long to
wait.

## You can't find out who has an account

User enumeration slips in easily because the system "works" just as well with it inside.

- **When signing in**, "that account doesn't exist" and "the password is wrong" give the same
  error and the same message.
- **When recovering**, the answer is identical whether the account exists or not, and the token
  never travels in it: it only reaches the email. Not even a sending failure changes the answer,
  because saying "it couldn't be sent" would also reveal that it exists.
- **When signing up a duplicate username or email are told apart.** It's unavoidable: the person
  needs to know which of the two to change, and the leak is the same that any sign-up form
  produces.

## Password recovery

The token is 256 random bits, stored **hashed** (plain SHA-256: it isn't a password chosen by a
person, there is nothing to guess by brute force), **expires in 30 minutes**, works **only once**,
and asking for a new one invalidates the previous one — which may be in an old email.

### Email is a real dependency

**There is no simulated email system.** Either there is a configured server and the email really
goes out, or it doesn't go out and that's said clearly. An `enviar()` that returns `True` without
sending anything turns recovery into a broken feature that looks like it works, and the failure is
discovered the day someone loses their password.

| Variable | What for |
|---|---|
| `MORGAN_EMAIL_API` | `brevo` or `resend`. **What works in the cloud** |
| `MORGAN_EMAIL_API_KEY` | The provider's key |
| `MORGAN_EMAIL_FROM` | Sender; in Brevo, a verified address |
| `MORGAN_SMTP_HOST/PORT/USER/PASSWORD/FROM` | SMTP, for local |
| `MORGAN_WEB_URL` | The web's base, to build the link |

There are two transports: **HTTP API** (Brevo, Resend) and **SMTP**. The API wins when both are
there.

> **SMTP doesn't work in the cloud.** Render and most free plans block outgoing SMTP ports, and
> sending dies with `Network is unreachable` **even if the credentials are correct**. It was
> discovered in production, with Gmail correctly configured. The HTTP API goes through 443 and no
> hosting blocks it.

And another restriction decides the provider: **Brevo writes to anyone verifying only your own
address**; Resend, without its own domain, only writes to yours. For Morgan, where the email has
to reach other people, that's the difference that matters.

`/status` includes a `correo` (email) service that says which transport it uses and what happened
on the last send. It exists because the failure is invisible from outside: asking for a link
answers the same whether the email goes out or not.

**Without configuration** nothing is sent and an error is logged: recovery doesn't work until it's
configured, which is exactly what should be noticed.

The link is **not** written in the log, not even locally. It was tried "to be able to test in
development" and it didn't work: the log's secret filter masks anything that looks like `token=`,
so what was left was `?token=***`. Getting around that filter would be publishing a secret in the
log on purpose. To test without a provider, `solicitar_recuperacion()` returns the token directly.

## Email verification

**Verifying isn't a gate.** The account works from the first moment without confirming anything,
and that's a decision, not a shortcoming: forcing people to open their email before letting them
try Morgan is the fastest way to lose someone.

What you lose without confirming is concrete and bounded, and the notice says it as it is instead
of threatening in the abstract:

> You can keep using Morgan, but **you won't be able to recover your password** if you forget it
> until you open the link we sent you.

It's literally true: the recovery link goes to that address. If it was mistyped, there is nowhere
to send it.

### The two kinds of token share a table, but aren't worth the same

A verification token and a recovery token are the same thing from the storage's point of view —a
single-use secret, tied to a user, with an expiry—, so **they share a table**. Duplicating it would
also have duplicated its cleanup, its expiry check and its security properties, which are exactly
the things you don't want twice.

What they **don't** share is what they grant, and that's why every query filters by `tipo`:

| Type | What it opens | Lasts |
|---|---|---|
| `reset` | The account: it allows changing the password | 30 minutes |
| `verificacion` | Only confirms an address | 24 hours |

If the type weren't filtered, a "confirm your email" link would work to get into the account. And
that link is sent to an address that **may not belong to whoever signed up** — which is precisely
what verification exists to find out. There are tests in both directions.

The different duration also comes from there: whoever intercepts a recovery link takes the
account; whoever intercepts a verification one only manages to mark as verified an address they
already control.

### Confirming is public; resending requires a session

| Route | Session | Why |
|---|---|---|
| `POST /auth/verificar` | **No** | Whoever opens the link may be in another browser or on their phone. Asking them to sign in would turn a click into a chore |
| `POST /auth/verificar/reenviar` | **Yes** | An email is sent here. An open route that fires emails from an address is a tool to pester third parties |

### An email failure doesn't stop you signing up

Sending happens in the background and wrapped: if the provider doesn't answer, the account is
created anyway and the token stays stored to retry from Settings. Sending is the accessory part.

Changing the password invalidates pending recovery links —which is right— but **not** the
verification one, which has nothing to do with it and would force asking for it again for no
reason.

## Taking your data with you, or deleting it

Both operations exist, and **together** they're what turns "your data" into something real.
Separately each is half a promise: if you could only delete, the only way to keep a conversation
would be copying it by hand; if you could only export, leaving would mean leaving everything there.

| | Route | What it does |
|---|---|---|
| Download | `GET /auth/datos` | A JSON with everything you've generated |
| Delete | `DELETE /auth/cuenta` | The account and its data. No undo |

### What the export does NOT carry

An export file ends up in the downloads folder, is shared by email and uploaded to places. Putting
a credential there is handing out material to attack it at leisure and without anyone noticing.

| Out | Why |
|---|---|
| The password hash | It isn't data of yours that's useful for anything: it's a credential |
| The tokens of connected services | Worse: they open accounts **outside Morgan** |
| The session identifiers | Each one is a live key |

It does say **what** you have connected and with which account —knowing that your Morgan has
access to your GitHub is data of yours, and of the kind that matters—. What has no place there is
the key.

There are four tests devoted just to this, because it's what would be expensive to get wrong.

### A partial failure is said

Each block is read separately and a failure in one doesn't stop exporting the others. What couldn't
be read is marked in `incompleto`: an incomplete export that **looks** complete is worse than none,
because whoever saves it believes they have their conversations and doesn't.

### In the interface, downloading comes before deleting

It isn't a coincidence of the sections' order. Whoever reaches that part of the settings thinking
of leaving should stumble first on the option to take their things; the other way round, only
someone who had already decided to stay would find the export.

## Personal API tokens (V2.0.40)

What a client **that isn't the browser** —a script, an editor extension, the 3.0 local agent—
uses to talk to Morgan. It's phase 1 of the API plan (in Spanish),
approved on 2026-09-17.

### The gap, measured (phase 0)

Before building anything, how a script got in was measured, **isolated**: a temporary SQLite
database, without Supabase and without a shared token (the 2026-09-16 attempt was invalidated by
both things). Real output, before and after:

```
BEFORE (session only): a script without a browser
  GET /sessions with nothing                            -> 401 SIN_SESION
  POST /auth/login with the PASSWORD                    -> 200
    cookies that have to be kept: ['morgan_csrf', 'morgan_sesion']
  GET /sessions with the cookie                         -> 200
  POST /sessions with the cookie, without copying CSRF  -> 403 CSRF
  POST /sessions with cookie + x-morgan-csrf header     -> 200

AFTER (personal token): created once from the web with a session
  POST /auth/tokens (with a session)                    -> 200
    value: mgn_7VXm… (47 characters, shown once)
  GET /sessions with Authorization: Bearer mgn_…        -> 200
  POST /sessions with the token, without CSRF           -> 200
  POST /auth/tokens with the token                      -> 403 TOKEN_NO_PERMITIDO
  DELETE /auth/cuenta with the token                    -> 403 TOKEN_NO_PERMITIDO
```

That is: until then, a script had to **store the password**, sign in, keep two cookies and copy
one of them into a header on every request that changed something. And the session expires after
30 days. With a token, it's one header.

### What a token looks like

| What | How | Why |
|---|---|---|
| Shape | `mgn_` + 32 random bytes in base64url (47 characters) | The prefix makes it recognizable in a log or a secret scanner, and tells it apart from the shared token `MORGAN_API_TOKEN`, which belongs to the installation and not to a person |
| Stored | Only the **SHA-256**, like sessions | Whoever reads the database gets nothing usable |
| Shown | **Once**, in the response of `POST /auth/tokens` | Morgan can't show it again. If lost, it's revoked and another is created |
| Presented | `Authorization: Bearer mgn_...` and **only that way** | `X-Morgan-Token` belongs to the shared token: a single way of presenting a personal token is one thing less to audit |
| Expires | **Always**: 90 days by default, one year at most | A token forgotten on an old laptop can't be valid forever |
| How many | 20 alive per account | One client per token; without a cap, a broken loop would fill the table |
| CSRF | **Not required** | CSRF protects what the browser sends on its own (cookies). A header doesn't travel on its own |
| Audit | Every entry made with a token carries `"token": "tok-…"`, its id | To know which client did what, and revoke that one |

### Scopes, and what a token can never do

Each token carries one or more scopes, and each request asks for one:

| Scope | What it allows |
|---|---|
| `chat` | `POST /chat` and `POST /chat/stream`. It's separate because it's what almost every client asks for, and giving `escritura` to chat would be giving too much |
| `lectura` (read) | Any `GET` or `HEAD`: listing conversations, reading messages, memory, tasks… |
| `escritura` (write) | Everything else: creating, changing and deleting conversations, memories, files, workspaces… |

**What no token can do**, whatever its scopes: all of `/auth` except `GET /auth/yo` —creating or
revoking tokens, changing the password, deleting the account, downloading all the data, seeing or
closing sessions—, `/admin`, `/integraciones` and `/diagnostico`. **Stealing a token can't turn
into stealing the account.** The comparison is by segment: `/authors` isn't `/auth`.

`GET /auth/yo` is allowed, because knowing whose token it is is the first thing a client does.
With a token it answers `csrf: ""`: there is no cookie to protect.

### Where it comes in, and why it opens nothing to the web

In `identidad_middleware`, **before** anything else: if `Bearer mgn_...` arrives and the request
**carries no session cookie**, the token is resolved, the scope is checked and the user and role
are set in the context, just like with a session. From there on everything that filters by user
—conversations, memory, files, workspaces— stays isolated without touching a route.

- **With a cookie, the usual path is followed, with its CSRF**, whatever header it carries. If the
  header were enough to skip CSRF, someone else's website could send the victim's cookie with any
  `Bearer mgn_`. There is a test for that.
- **An invalid `mgn_` is always rejected**, also on the Morgan on your computer, where without a
  session you're the local user (who is the owner). That's why it goes before checking whether
  there is an account layer: a bad token can't inherit that.
- **The shared token lets it through** (`src/api/auth.py`): the identity goes inside and checks
  it. Stopping it there would make it impossible to use personal tokens on a deployment with a
  shared token. Whatever doesn't start with `mgn_` still needs the shared one.

| Response | Code | What it means for whoever integrates |
|---|---|---|
| 401 + `WWW-Authenticate: Bearer` | `TOKEN_INVALIDO` | It doesn't exist, expired, was revoked or the account is suspended: create another |
| 401 | `TOKENS_DESACTIVADOS` | This Morgan has tokens switched off |
| 403 | `TOKEN_NO_PERMITIDO` | This isn't done with a token, only from the web |
| 403 | `ALCANCE_INSUFICIENTE` | The token is valid, but it doesn't have the scope the route asks for |

### From the web

In **Settings → API access** (V2.0.41): create with a name, what it can do and an expiry; the value
is shown once; revoke one or all. Described in [web.md](web.md). By default it proposes **chatting
and reading, 90 days**: what almost any program asks for and nothing that can delete.

### Revoking

- **One**: `DELETE /auth/tokens/{id}`. It stops working **on the next request**: there is no cache.
  Someone else's answers 404, just like one that doesn't exist.
- **All**: `POST /auth/tokens/revocar-todos`.
- **On their own**: when **changing** or **resetting** the password all of them fall, which is what
  whoever changes it because they suspect something expects. And when deleting the account. **And
  the connected PC** (3.1): those three things also close their local agent's connection at once;
  when it comes back, the cloud checks its credential again.
- **The switch**: `MORGAN_TOKENS_API=false` turns them all off at once, without deleting them, and
  doesn't let more be created. It's the emergency brake; by default they're on (approved for every
  account).

The periodic cleanup deletes the revoked and expired ones.

### Checked against a real Supabase

The suite's tests exercise the Supabase implementation with a fake client, which checks the shape
of the queries but not that PostgREST understands them (the user travels embedded in the same query
as the token). That's why the walkthrough was repeated against the `morgan-carga` test deployment,
with its own Supabase and the simulated model (2026-09-18): sign-up, create a token,
`GET /auth/yo`, chat, list the conversation, `403 ALCANCE_INSUFICIENTE` without `escritura`,
`403 TOKEN_NO_PERMITIDO` when creating tokens or deleting the account, revoke and
`401 TOKEN_INVALIDO` on the next request. **Everything the same as locally.** In production, a
made-up token answers 401.

### What it doesn't do yet

- **A per-minute throttle per token** (phase 5). Today a token spends from its owner's daily quota
  like any of their messages, and the quota is the limit.
- **A notice before expiring** by email: the web marks it in color at 7 days, but nobody warns
  whoever doesn't come in.
- Local agents **don't use API tokens**: since 3.0 they have their own credential (`mga_`, distinct
  from `mgn_`), obtained by pairing the PC from the web (agente-local.md, in
  Spanish).

## When an account is required

`MORGAN_REQUIRE_AUTH` decides, and its default value **follows the environment**: in the cloud yes,
locally no.

It isn't a whim. The desktop Morgan runs on your computer with your keys, and forcing you to invent
a password to talk to your own computer protects against nothing. Without required accounts,
everything belongs to the implicit user `local`, who owns everything that existed before there were
accounts — and that's why `local` is a **reserved** username: registering it would give access to
that data.

`MORGAN_REGISTRO_ABIERTO` decides whether anyone can create an account. Open by default —a Morgan on
the web that nobody can sign up to is useless—, and closing it returns **403** to new sign-ups
without throwing out whoever was already in.

In the cloud there are **two valid ways to close the deployment**, and one is enough:
`MORGAN_API_TOKEN`, a shared secret for a private Morgan, or `MORGAN_REQUIRE_AUTH`, for one with
accounts. Startup aborts if there is neither.

> The token used to be required **always** in the cloud, and that made it impossible to open
> Morgan to other people: to invite someone you had to give them the token, with which that person
> got access to the whole API regardless of their account. Accounts aren't weaker protection than
> the token: they're stronger, because they also keep the data apart.

Routes open without a session: `/health`, `/status`, and the `/auth` ones used to get one.

> **`/status` is open on purpose.** It's what the interface checks to know whether the backend is
> alive, and what wakes Render up when it has been asleep for a while. Protecting it makes **the
> sign-in screen itself** say "API disconnected": you can't get in because you haven't got in. It
> publishes nobody's data, and if `MORGAN_API_TOKEN` is configured, that one still covers it. There
> is a test that pins it, so nobody "fixes" it by putting it among the protected ones.

## How the data is kept apart

Two barriers, and the second exists because the first can fail through carelessness.

**1. The filter by user, in the middleware.** Each request sets the user in a `ContextVar` and
**every** repository query filters by it. Having it there and not in each query is what makes a new
route born isolated by default: forgetting the filter stops being possible, because there is no
filter to write.

It's a `ContextVar` and not a global variable on purpose: FastAPI serves synchronous routes in a
thread *pool*, and a global would mix the data of two people asking at the same time. It's the kind
of bug that doesn't show up testing by hand and does as soon as there are two users.

**2. Row Level Security in Supabase.** Per-row policies that hold even if a query forgets the
filter. `auth_sessions`, `password_reset_tokens` and `login_intentos` have RLS on and **no policy**:
they store material to impersonate someone and are never queried from the browser, so the intended
effect is that nobody except the backend —which uses the service key— can read them.

## Quota per user

Morgan uses its owner's keys. With one person it doesn't matter; with several, **one alone could use
up everyone's quota** in an afternoon. By default: 50 messages, 20 transcriptions and 20 image
analyses a day, counted per calendar day. The local user is **exempt**: it's your computer and your
keys.

It's recorded **at the start, not at the end**. If it were counted at the end, a turn that fails
halfway would be free and causing failures would be enough to skip it. Checking and recording are a
single operation, because separating them would leave a window through which two simultaneous
requests would both get through: in SQLite that's an `UPDATE ... WHERE contador < límite` looking at
`rowcount`, and in Supabase a function in the private schema `morgan_priv`, because PostgREST can't
write `column = column + 1`.

When the limit is reached, `/chat` answers **429**, and the tools return a `success: false` with the
reason — not an exception, which would abort the whole turn with a generic failure instead of
letting Morgan explain it.

> **It was written and tested without anyone calling it.** The module existed, had its tests green
> and was a dead piece: no route invoked it. Now there are tests that check it's really applied on
> the paths that cost money, which is different from checking that the module works.

### The global quota (V2.0.24)

With open registration, **the per-person quota doesn't bound the total**: ten accounts are ten
quotas, and with paid OpenAI at the end of the chain that's money. The 2.3 audit raised it and I
delegated the number. Besides each person's quota there is a daily cap for **the sum of every
account subject to quota**:

| Concept | Cap across everyone | Why that number |
|---|---|---|
| Messages | **150** | The free model capacity is around 210 turns a day (three Groq accounts and the model relief, estimated). About 60 are left for the owner before touching the paid provider |
| Images | **15** | Only Gemini sees images, and its free account gives 20 requests a day |
| Transcriptions | **60** | Ample for normal use; it bounds a loop |

- **The owner and the local user neither count nor are throttled**: they're the ones who pay. An
  administrator does count: administering isn't paying.
- **It's checked before the person's quota**, so a global rejection spends nothing from anybody.
- **The message says it isn't the reader's fault**: "Morgan has reached its message limit for all
  accounts today. It isn't because of your use: it renews tomorrow". The route answers the same
  429 `CUOTA_AGOTADA`, which the web already knows how to show.
- **Configurable** with `MORGAN_CUPO_GLOBAL_MENSAJES`, `_TRANSCRIPCIONES` and `_IMAGENES`; 0 means no
  cap.
- **It isn't atomic across people**: two simultaneous turns from two accounts can both get through
  in the last gap. The possible excess is a few calls; making it atomic would require a lock shared
  by every turn on the server.
- In the cloud it's summed by reading the day's rows (one per active account) without calling the
  Postgres function. A failure when summing lets it through: the per-person quota still holds.

**And a defect that appeared while building it.** The image and audio tools recorded usage
**without the role**, so in the cloud the owner spent image and transcription quota like any
account. The test that pins it fails with the previous code. `tests/test_cupo_global.py`: 21
tests, 11 of 11 mutations.

See [`src/identidad/cuotas.py`](../src/identidad/cuotas.py).

### What costs no quota but fills the database (4.22)

What spends money already had a cap (messages, images and transcriptions, the global quota,
files, API tokens, PCs, automations). The review of per-account limits looked for what does
**not** spend quota but takes up the database, and that the model or a script can create in a
loop. Now it has a cap, a generous one, and on reaching it you're told what to do:

| What | Cap per account | On reaching it |
|---|---|---|
| Memories (`remember_fact`, `POST /memory`) | **500**, and **2000 characters** each | 409 `MEMORIA_LLENA`: «forget one that's no longer useful». Updating one that already exists is always possible |
| Workspaces | **50** | 409 `ESPACIOS_DEMASIADOS`: «delete one you don't use» |
| Knowledge documents, across all workspaces | **300** | The tool says so («delete one with `remove_knowledge`»). Replacing one (same title and collection) is always possible |

In the cloud only the account's rows are counted (with a `limit`, without fetching the table).
`tests/test_limites_por_cuenta.py`: 10 tests, 11 of 11 mutations.

## Schema

Migration **v11**, mirrored in Supabase.

| Table | What for |
|---|---|
| `morgan_users` | Now with `username`, `password_hash`, `status`, `email_verificado` |
| `auth_sessions` | Sessions. The `id` is the **hash** of the token |
| `password_reset_tokens` | Recovery. The token also **hashed** |
| `login_intentos` | Failed attempts, by identifier and origin |
| `api_tokens` | Personal API tokens (SQLite **v19**, Supabase **v23**). Also the **hash**, never the value. RLS on and no policy, like `auth_sessions` |

> It's called `auth_sessions` and **not** `sessions` on purpose: that table already exists and
> it's the **conversations**. Reusing the name would mix two things that have nothing to do with
> each other and guarantee confusion in every query anyone writes from now on.

## Routes

| Method | Route | Session | What it does |
|---|---|---|---|
| `POST` | `/auth/registro` | no | Creates the account and leaves the session signed in |
| `POST` | `/auth/login` | no | Signs in. 401 if it fails, **429** if there is a lockout |
| `POST` | `/auth/logout` | no | Closes the session on the server and deletes cookies |
| `GET` | `/auth/yo` | no | Who you are. **Never gives 401** |
| `POST` | `/auth/recuperar` | no | Sends the link, if that address has an account |
| `POST` | `/auth/restablecer` | no | Changes the password with the token from the email |
| `POST` | `/auth/password` | yes | Changes the password knowing the current one |
| `GET` | `/auth/sesiones` | yes | Where you have open sessions |
| `POST` | `/auth/sesiones/cerrar-otras` | yes | Closes the rest |
| `GET` | `/auth/tokens` | yes | Your live API tokens, without their value |
| `POST` | `/auth/tokens` | yes | Creates one (`nombre`, `alcances`, `dias`). Returns the value **once** |
| `DELETE` | `/auth/tokens/{id}` | yes | Revokes one. 404 if it isn't yours |
| `POST` | `/auth/tokens/revocar-todos` | yes | Revokes all |

`/auth/yo` doesn't give 401 on purpose: opening the web without having signed in is the most common
case of all, and treating it as an error would fill the console with failures that aren't.

Sign-in returns **429** and not 401 when there is a lockout because the client has to be able to
tell "wait" from "try another password": they're opposite pieces of advice.

## Tests

| File | What it covers |
|---|---|
| [`tests/test_cuentas.py`](../tests/test_cuentas.py) | The logic: hashing, sign-up, sessions, brute force, recovery, enumeration |
| [`tests/test_api_cuentas.py`](../tests/test_api_cuentas.py) | The seam with HTTP: cookies, CSRF, route protection, isolation between users |
| [`tests/test_cuotas.py`](../tests/test_cuotas.py) | The daily quota |
| [`tests/test_frenos_de_acceso.py`](../tests/test_frenos_de_acceso.py) | The two attacks from the 2.3 audit: faked origin and mass sign-ups. 10 of 10 mutations |
| [`tests/test_aislamiento.py`](../tests/test_aislamiento.py) | That each one sees only their own |
| [`tests/test_tokens_api.py`](../tests/test_tokens_api.py) | API tokens: the plan's acceptance (a script chats and lists, doesn't delete the account or create tokens, A's sees nothing of B's, revoking cuts at once), scopes, CSRF with a cookie, password, switch and Supabase. 31 of 32 mutations; the surviving one is equivalent (the table's cascade already deletes the tokens) |

**None disabled.** The suite's total isn't written here: it goes out of date the same day and says
nothing about authentication.

The HTTP ones exist because there are failures you don't see by reading the service: a cookie
without `HttpOnly`, a 401 without CORS headers, a route that forgot to require a session. And the
isolation ones check two things, not one: that Bruno doesn't read Ana's conversation **and** that
the answer is identical to the one for a conversation that doesn't exist. If they were told apart,
the endpoint would serve to find out which conversations exist even if it didn't let you read them.

## Putting this in place

For the cloud deployment, on Render (full step-by-step guide in despliegue.md, in
Spanish). In short:

```
MORGAN_ENVIRONMENT=cloud        # already turns on MORGAN_REQUIRE_AUTH
MORGAN_EMAIL_API=brevo          # without email there is no recovery; SMTP doesn't leave Render
MORGAN_EMAIL_API_KEY=...
MORGAN_EMAIL_FROM=...           # sender verified in Brevo
MORGAN_WEB_URL=https://morgan-ia.vercel.app
MORGAN_CORS_ORIGINS=https://morgan-ia.vercel.app
```

`MORGAN_CORS_ORIGINS` has to be exact: with `allow_credentials`, the browser **doesn't accept** the
`*` wildcard and the cookies wouldn't travel.

## What's missing

- **The "login CSRF"**, accepted knowingly: see
  [What this design doesn't cover](#what-this-design-doesnt-cover).
- **Integrations with Google** (email, calendar): **parked** since 2.0.18 and **ruled out** on
  2026-09-19. The Calendar code is still in the repository, without registering its tools.
