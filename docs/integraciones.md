# External services (V1.9)

**English** · [Español](integraciones.es.md)

> Connecting GitHub —and, later, other services— to each person's account.

## First things first: this isn't Morgan's sign-in

They're two different systems, and mixing them up is the most expensive mistake you can make
here:

| | What it means |
|---|---|
| **Morgan's sign-in** | Its own accounts: username, password, session. It says **who you are** |
| **Integration** | *You* authorize Morgan to use **your account on another service** |

There is an irony worth naming: OAuth was ruled out for *signing in* because of complexity, and it
shows up again here for *connecting services*. It isn't a contradiction. In the first case GitHub
would say who you are, and Morgan would depend on its service being up to let you in; in the second
it's already known who you are, and all you get is permission to act on your behalf in a specific
place.

The practical consequence: **an integration always hangs from a Morgan account**. Without a session
there is nobody to attribute the authorization to, and in a shared Morgan one person would connect
it and everyone would use it.

## The exchange, and where each protection is

```
1. The web asks to connect       ──►  Morgan issues a `state` and stores it
2. The browser goes to GitHub    ──►  the person authorizes (or not)
3. GitHub returns to the backend ──►  with `code` and the same `state`
4. Morgan validates the `state`  ──►  and only then exchanges the `code`
5. The token is stored encrypted ──►  and the browser goes back to the web
```

### Step 4 is the one that matters

Without checking that the `state` coming back is one that was issued, **someone else's website could
complete the flow and leave their GitHub account connected to another person's session**. Morgan
would then act on the attacker's repositories believing they're yours, and everything asked "about my
repo" would go through someone else's repository.

The state expires in ten minutes —that's the time to authorize on a screen, not a session's— and
**it's deleted when used**. A reusable one stops protecting anything.

### Whose authorization it is is said by the `state`, not the cookie

The return from GitHub is a **browser navigation**, not a request from the web. Trusting the session
cookie would be asking the very channel you're trying to verify. The `state` was issued with a valid
session and stored with its `user_id`; the callback consumes it and acts as that user, who may not be
the one in that request's cookie.

That's why `/integraciones/{servicio}/callback` is **the only public route** under that prefix.
Requiring a session there broke the flow right on the way back, after authorizing — the worst
possible moment to fail.

### The return URL points to the backend

To Render, not to Vercel. The application's secret lives on the server and that's where the code is
exchanged: **the frontend never sees either the secret or the token**. Putting the web's URL gives a
`redirect_uri_mismatch` that doesn't explain much.

## The token

### It's stored encrypted

With a GitHub token you can read private code and —depending on the permissions— write. In plain
text, a lost backup stops being a privacy problem and becomes an access one.

It uses **Fernet**, from `cryptography`: AES-128-CBC with HMAC-SHA256. There is no home-made
cryptography; [`secretos.py`](../src/integraciones/secretos.py) only resolves where the key comes
from.

The key is `MORGAN_SECRET_KEY`. **It isn't derived from another variable or generated at startup**,
and both things are deliberate:

- Deriving it from the Supabase key would tie together two secrets that must be rotatable
  separately: rotating one would make every token unreadable.
- Generating it at startup would make each Render restart render the integrations useless
  **without any error**: they would just stop decrypting.

**Without a key, integrations aren't offered.** Tokens aren't stored in plain text "in the
meantime", which is the solution that looks pragmatic and is the one that ends up in production: an
unencrypted token can't be told apart from an encrypted one by looking at the table, so the day
someone notices it'll have been there for months.

If the key changes, decrypting **raises** instead of returning an empty string. An empty token would
be used as valid and would give a 401 from GitHub — a symptom that doesn't point to its cause.

### It never leaves the backend

Not in the list, not in the detail, not in the audit. The interface doesn't need it: the server is
the one that talks to GitHub. Returning it would put it within reach of any script injected into the
page, and the harm wouldn't be Morgan's but that person's repositories'.

`Integracion.to_dict()` —what is publishable— has exactly seven fields, and there is a test that pins
the set so that adding one is a decision and not an oversight.

## The permissions

Minimal and explicit, which is what the specification asks for:

| Permission | What for |
|---|---|
| `read:user` | Knowing whose account is connected |
| `repo` | Seeing private repositories |

`repo` includes writing, and it's worth saying why: **GitHub doesn't offer a read-only permission on
private repositories** in classic OAuth Apps. It's the minimum that lets you see them. That's why the
interface shows **what it enables in plain language before connecting**, instead of taking the
permission for granted.

The token allowing writes doesn't mean Morgan writes. The actions still go through the usual
permission and confirmation system: **connecting GitHub isn't a blank authorization**.

## Disconnecting

GitHub is asked to invalidate the token, and **the local deletion happens no matter what**. If it
were only deleted here, a leaked copy of the token would keep working and the person would believe
they had revoked access. And if the call to GitHub fails, staying connected would be the worst
outcome: they've said they don't want to stay connected.

The answer says whether the remote revocation was confirmed, and the interface warns when it wasn't,
so it can be checked on the service's website.

## The four states

The interface tells them apart because they mean different things:

| State | What happens |
|---|---|
| **Not configured** | The server lacks credentials. **There is no button** |
| **Not connected** | It can be connected |
| **Connected** | With the account's name and the granted permissions |
| **Having problems** | There is an integration, but the last operation failed |

The last two are very different and easily confused. "Connected and failing" isn't fixed by
connecting again if the problem is that GitHub is down.

`disponible` (available) belongs to the **server** (does it have credentials?) and `conectado`
(connected) to the **person** (have they authorized?).

## The rule that governs the interface

**If something can't work, it's said; it isn't offered.** Without credentials, the section explains
what's missing and paints no button. A button that always gives an error wastes time and looks like a
Morgan breakdown when it's missing configuration.

### The local Morgan can't connect services, and since V2.0 it explains it

An integration hangs from an account: it's *someone* authorizing Morgan to use their account
somewhere else. In the local Morgan, with `MORGAN_REQUIRE_AUTH=false`, there are no accounts and so
nobody to attribute the authorization to. Connecting and disconnecting answer 401, and that doesn't
change: in a shared Morgan without accounts, one person would connect it and everyone would use it.

What did change is that **asking which services exist no longer requires an account**. Before,
`GET /integraciones` also answered 401, and the result was that the whole panel was painted as a red
error —the case this very rule says mustn't happen—. Now it answers with GitHub switched off and its
reason:

> To connect GitHub you need a Morgan account. This Morgan doesn't require accounts, so there would
> be nobody to attribute the authorization to: one person would connect it and everyone would use
> it.

And the account reason wins over the variables the server is missing. With the credentials set and
without accounts, saying "`GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` are missing" would send
whoever administers it to fix the wrong thing.

**How it showed up.** Not by reading the code: by removing things to see what broke.
`_exigir_cuenta()` in `desconectar` looked redundant —without it no test failed, because with
accounts required the middleware already returns 401 before getting there— and looking for where it
*does* matter, the local Morgan showed up, where that line is the only defense. Looking there, it was
seen that `listar` required it too. The story is in auditorias.md (in Spanish).

## Configuration

Done and in use: there is a GitHub account connected. It's written down for the day the deployment
has to be redone.

An OAuth application on GitHub (*Settings → Developer settings → OAuth Apps → New*, about ten
minutes). On Render, three variables:

```
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
MORGAN_SECRET_KEY=...        # any long, random string
```

And the return URL registered on GitHub:

```
https://morgan-ia-2-0.onrender.com/integraciones/github/callback
```

It has to match **exactly**, scheme included and without a trailing slash. If the Render service
changes URL —it happened on 2026-09-12, when it was recreated in Virginia—, this URL is changed in
GitHub's dashboard or connecting again fails with a `redirect_uri_mismatch` that explains nothing.
What's already connected keeps working, because it uses the stored token and not the return.

Morgan builds its side of that URL with `MORGAN_API_URL` and, if it doesn't exist, with
`RENDER_EXTERNAL_URL`, which Render sets on its own. **Better not to set the first one**: copied from
an old service, it points to a domain that no longer exists.

While any is missing, the section works the same: it shows GitHub switched off and says exactly
what's missing, with **all** the missing variables and not just the first. It's the same thing it
does today in the local Morgan, where what's missing isn't a variable but an account.

## The tools

Four, and **all read-only**:

| Tool | What it does |
|---|---|
| `github_listar_repos` | The repositories, from the most recent to the oldest |
| `github_listar_issues` | The issues, **without** the pull requests GitHub mixes in |
| `github_listar_prs` | The pull requests, with their source and target branch |
| `github_leer_archivo` | A file or a directory's index |

### They only read, and it's a decision

The token allows writing. These don't write. The specification asked for it in these words: "don't
automatically implement every GitHub capability just because OAuth is connected".

Adding "create an issue" or "make a commit" is easy from here, and that's why it's worth saying what
would be needed first: **going through the plan system**, so the person sees what's going to be
written and where before it happens. Writing to someone's repository without that step is exactly
what Morgan's permission system exists to prevent.

### The user comes from the context, never from the argument

None receives a user identifier. The token is looked up in the integrations repository, which
filters by the request's user. If it were a parameter, the model could make it up — and a model that
can name another user in a tool call is a model that can read their repos.

### They're always registered, connected or not

Unlike the knowledge ones. If they only appeared with GitHub already connected, the model wouldn't
know they exist and **would never suggest connecting it**. Without a connection they answer saying
where to connect it, which is more useful than not being there.

### What the model writes can't change what gets called

This started as a precaution and turned out to be an open hole.

`github_leer_archivo` put the file's path into the API URL as is. `httpx` normalizes the `..` when
building the URL, so:

```
/repos/duenyo/nombre/contents/../../../../user/emails   →   /user/emails
```

The person's private addresses, from a tool that says it reads a file from a repository. Along the
same path `/notifications`, `/user/keys` and `/gists` could be reached.

**And checking the repository didn't cover it.** `_repo_valido` looked at the *shape*: `'../..'` has
two pieces and no extra slash, so it passed, and `/repos/../../issues` is normalized to `/issues`.

It matters here more than anywhere else because of what the next section says: the value is composed
by the model from what it's told, and what it's told can come from a file it **has just read**. A
README with the right path was enough.

Three things close it, and all three are needed:

1. **The `.` and `..` pieces are rejected by name.** Encoding didn't help: `quote()` doesn't touch
   dots, so `..` survives intact.
2. **Everything else is encoded**, so a `?` or a `#` can't split the URL into something else.
3. **`_pedir` checks that the outgoing URL is the one that was asked for**, letter by letter. It's
   the safety net: it doesn't depend on a new function remembering to validate, which is exactly how
   the bug appeared.

[`test_github_rutas.py`](../tests/test_github_rutas.py) pins it, 17 cases.

### What comes from a repository is data, not instruction

A file's content arrives marked as `untrusted_file_data`, just like a web page or an audio. Someone
wrote it, and a code comment that says "ignore the above" can't be treated as an order.

## Tests

[`test_integraciones.py`](../tests/test_integraciones.py), 37 cases, plus
[`test_github_rutas.py`](../tests/test_github_rutas.py), 17. What they pin, in order of what a
mistake would cost:

0. That what the model writes can't change which endpoint is called. It was wrong and the person's
   private addresses could be reached.
1. The `state` decides whose authorization it is, not the cookie — including the check that a state
   issued by Ana returns `usr-ana` even when Bea looks it up.
2. The token doesn't appear in any response.
3. It's stored encrypted, in both implementations.
4. Isolation between users, in SQLite **and in Supabase**. The Supabase one was written and tested
   at the same time, which is exactly what wasn't done with conversations — and it cost
   [eight defects](web.md).
5. Without the server's credentials there is no connect button.
