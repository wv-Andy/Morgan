# The web interface

**English** · [Español](web.es.md)

> Brings together what used to be four documents: the interface's design, control over the
> turn, the frontend tests and the ten defects that broke the web in production. Last review:
> 2026-09-25, V3.5.0 (the local agent: downloads, plans and «Stop»).

React 19 + TypeScript + Vite, published on Vercel (`morgan-ia.vercel.app`). It talks to the
Render backend through a same-origin proxy, `/api/*` (despliegue.md, in
Spanish). The interface is in Spanish.

## 1. Design principles

- **A button that does nothing is worse than not having it.** Controls without a function
  aren't copied from a visual reference: no "deep think" pills, no `+` that was disabled on
  the home screen (removed in 2.0.9). From the 2.0.28 design, sharing, notifications, a model
  picker and usage data were left out: in Morgan they wouldn't do anything.
- **No component writes a color by hand**; all of them read variables from
  `web/src/index.css`, and `tests/test_tema_web.py` pins the WCAG contrast of the three themes,
  also for the accent and the two tones of the send button. Status color (risk, health) is
  still information; the accent marks what can be pressed.
- **Typography**: Outfit for the whole interface and the answers; Instrument Serif (a Times-like
  face) in italics for the highlighted word in the greeting; JetBrains Mono for code.
- **The home screen's suggestions write into the composer, they don't send.** With an agent
  that takes real actions, whoever presses has to be able to read what they're asking.
- **SVG icons, not emoji**: emoji change between systems and don't inherit color.

### Themes

Since 2.0.28 they come from my design screenshots:

| Theme | `data-tema` | What it is |
|---|---|---|
| **Night blue** (default) | `oscuro` | Very dark navy blue, light blue accent, buttons with a blue gradient |
| **Dark** | `negro` | Monochrome: neutral grays, white accent, a white send button with black text |
| **Light** | `claro` | The night blue by day: bluish gray background, white cards with a soft shadow |
| From the system | — | Picks between light and night blue |

The identifiers didn't change (`oscuro` was the lime one until 2.0.27, `negro` the monochrome
of V1.2): renaming them would send everyone who already has one saved back to the default
theme.

### The logo (V2.0.19)

My spiral, vectorized and compared with the original. It's a component, `LogoMorgan`
(`web/src/components/Logo.tsx`), with `fill="currentColor"`: it works in the three themes
without a network request. It's in the favicon, the side bar, the top bar, the sign-in screen
and the home screen's badge, which **spins while the server is being checked**. PNGs of 180
and 512 px for iOS and for sharing the link.

**The tab icon** (2.0.29) is the spiral in blue on a night blue square, in SVG, a 96 px PNG and
a real `favicon.ico`. They're linked with `?v=`: the purple lightning bolt I saw in the tab was
the Vite template's icon, **cached by the browser** since before the logo existed (production
was already serving the spiral). Browsers cache the icon by its address for a long time, and
`/favicon.ico` returned the page. The template's leftovers (`vite.svg`, `react.svg`,
`hero.png`, `icons.svg`), which nothing used, were deleted too.

**The loading screen is just the spiral spinning in the center** (2.0.28). It's there twice on
purpose: inline in `web/index.html`, to be seen from the first instant while the app downloads
(with a script that applies the saved theme before painting), and in `PantallaCargando`,
identical, while the session is being figured out. There is no jump from one to the other.
With the backend asleep the wait can go past a minute: the spiral still spinning is what says
something is happening. With `prefers-reduced-motion` it stays still.

### How the 2.0.28 design was checked

With screenshots of the built web, served by an **isolated** backend (temporary data, no model
or Supabase keys):

- The three themes on desktop, against the reference screenshots.
- 360 and 390 px inside an `iframe`, with the menu open and with the composer in view. It found
  three bugs, fixed before publishing: the cards didn't switch to one column, the 40 px touch
  rule squashed «Adjuntar» (Attach) and the top bar piled up without a session.
- The loading screen without JavaScript, and that the spiral **spins**: two screenshots at
  different moments give different images.
- A conversation with Markdown and code, and the sign-in screen in cloud mode.
- It also found an inconsistency: the bar said «Modo degradado» (degraded mode) and the home
  screen «Operativo» (operational). Now both read the same data.

## 2. Structure

A side bar and a canvas with a bar on top (2.0.28):

- **Side bar**: the brand with the environment (`Nube` or `Local`, from `/status`), the new
  conversation button, the views, the workspace and the history. At the bottom, the **user
  card**: name, Morgan's status and the Settings gear. Since 5.0.2, from Windows, above the
  card, **«Descargar Morgan para Windows»** (download Morgan for Windows), which isn't shown on
  a phone or inside the program itself.
- **Settings is a panel, not a view** (2.0.29), as in the reference screenshot: sections on the
  left with a search box (General, Personalization, Memory, Data and privacy, Account, API
  access, About) and rows with a name, a line of help and the control. It opens from the gear
  or from the account menu and closes with Escape or by clicking outside, without losing where
  you were. At 640 px or less it takes the whole screen and the sections become pills that
  scroll sideways. Accent color, voice and Discord were left out of the design: here they
  wouldn't do anything. The language is picked from a list and saved at once **without
  overwriting what's being typed in the profile**; the profile is saved with its button, which
  is only enabled if something changed.
- **Your computer, step by step** (4.17): the program to download (5.0) and, as an
  alternative, the install line with its **Copy** button, how to open PowerShell and paste it,
  and **a notice only when the PC connects** (it checks every 3 s while there is a code; the
  one that was already there doesn't count as new). On each connected computer, **«Abrir los
  ajustes en mi PC»** (open the settings on my PC): it opens «Morgan en tu PC» there and
  changes nothing from here. And in «API access», when creating a token, the API's address and
  examples that work as they are (PowerShell, curl, Python) with their Copy button
  (`ParaCopiar.tsx`).
- **Your computer** (3.0-C): pairing the PC with the account for the
  local agent (in Spanish) (`PanelEquipos.tsx`). It asks for a single-use
  code and shows it big, with its countdown, because it's typed by hand on the PC; an expired
  one isn't shown as if it were valid. It warns about what threat H cuts: *"if the account you
  see isn't yours, say no"*. It lists the computers with their system, version and last
  connection, and revoking asks for confirmation. It says that once installed the agent **can't
  do anything** until the person, on their PC, gives it folders or switches on capabilities.
  Since 3.8, with the code it shows **the PowerShell line that installs the agent** on a new PC
  (and how to pair one that already has it); since 3.7, for each computer, whether it's
  connected, what it offers and its history (below).
- **API access** (2.0.41, phase 2 of the API plan, in Spanish):
  creating, seeing and revoking the [personal tokens](autenticacion.md#personal-api-tokens-v2040)
  (`PanelTokens.tsx`). It's written for someone who has never seen a token: it explains what
  it's for and says "if you don't know what it is, you don't need it". What makes it safe to
  use:
  - **By default, chatting and reading, 90 days**: nothing that can delete unless ticked.
  - **The value is shown once**, in a text area where it's seen whole (on the phone, a one-line
    field cut it), with «Copy» and «I've saved it». The box **doesn't close on its own**:
    losing it by switching section would force you to revoke it. If the browser doesn't allow
    the clipboard, it leaves it selected.
  - **Revoking asks for confirmation** ("Yes, revoke"), one by one or all of them.
  - It warns in color about the ones that **expire in 7 days or less**.
  - On the Morgan on your computer it explains that no token is needed: without an account,
    the API is already yours.

  **Checked on the phone** with the built web and an isolated backend, at 360 and 390 px:
  creating, seeing, saving and revoking without anything going off the screen, and the token
  created from the phone **works from a script** (200) and stops working when revoked from the
  web (401). The first screenshot showed two things, fixed: the title came out twice and the
  value was cut.
- **Which model answers isn't shown** (2.0.29, my decision): neither on each message nor under
  the composer. A single hint is left, without names: if the backup answered, the answer's time
  explains it when you hover. Under the composer, a notice for anyone ("Morgan can be wrong…").
- **The status view speaks in the words of whoever uses Morgan** (2.0.29): «Conversar y
  razonar» (talking and reasoning), «Internet», «Tus datos» (your data), «Correo» (email), with
  «Funciona / Con problemas / No disponible» (works / has problems / not available), and what
  Morgan can do where it runs. No model providers, database names, email APIs or subsystems.
  The two databases are shown as "your data" with the **worst** of their states. The detail is
  still in the `/status` response for diagnosing: this changes what is painted, not what the
  server knows.
- **Top bar**: where you are (Morgan · workspace), the real status with the number of tools,
  and the avatar with the account menu. The phone's menu button goes here, **inside the
  flow**: it used to float and overlap the first thing in each view.
- **What Morgan can do, depending on where it runs** (2.0.33): the home screen's text comes
  from the environment `/status` reports, not from how the web was built. And while Morgan
  answers, the field says «Morgan está respondiendo…» (Morgan is answering); it used to say
  «Morgan API desconectada…» (disconnected), because busy and down shared the same variable.
- **Home screen**: a badge (where Morgan runs, or «Chat temporal»), the greeting «¿Qué
  *hacemos* hoy?» (what shall we do today?) with the highlighted word, what Morgan can do here
  and **three cards that depend on the environment**: in the cloud, searching, reading a page
  and remembering; on your computer, reviewing a project, planning and searching. Offering to
  review a project in the cloud would be promising something it can't do there.
- **Composer**: the field on top and a row below with attach, dictate and send (just the
  arrow). The cards are only an icon, a title and a description: pressing them writes. With
  messages it anchors at the bottom with a maximum width of 760 px.

- **Workspaces**: a native `<select>` at the top of the side bar. Changing workspace starts a
  new conversation ([datos.md](datos.md#5-workspaces-v2010)).
- **The account, top right**: with a session, avatar and menu (sign out); without a session
  (only locally), «Iniciar sesión» and «Registrarse» (sign in, sign up), which open the access
  screen **on top of** Morgan so as not to throw away a half-written conversation.
- **What Morgan is doing**: under the typing indicator, a sentence taken from the turn's events
  ("Searching the internet…"). With no event there is no sentence, and the heartbeat doesn't
  change it: that would be making up progress
  ([agente.md](agente.md#streaming-post-chatstream)).
- **Automations** (4.14): a view with the **tray** on top (what each run reported, with what it
  used) and the **scheduled** ones below, with pause, resume and delete. Not creating them:
  they're asked for in the chat. **Seeing it is reading it**: no "mark as read" button. In the
  navigation, the number of unread notices, checked every minute with a route that doesn't
  bring the texts. Each request sends the browser's time zone (`X-Morgan-Zona`), so that "at 9"
  means 9 for whoever creates it.
- **Pending plans above the chat**, not in a tab: they block the work. **«Aprobar y ejecutar»
  runs** (4.0): the chat sends on its own the turn «✅ Plan aprobado: …» with `ejecutar_plan`,
  without having to type "go ahead".

## 3. Control over the turn

| Action | How, and why this way |
|---|---|
| **Stop** | Aborts the request with `AbortController` and its own exception, so it isn't confused with a timeout. The text goes back to the composer. The turn finishes and is saved on the server. **Since 3.4, also**, it tells the cloud (`POST /chat/parar` with the `turno` from the `inicio` event) to **stop what is being done on the PC**. Only the button: switching apps on the phone cancels nothing |
| **Retry** | Keeps the text and files of the last send. **Since 4.5, it doesn't appear if the connection dropped with the turn already started**: that turn continues on the server, so the web checks the conversation every 4 s (up to 200 s) and paints the answer when it's saved. Measured in my test from the phone: the retry processed the same question again with the first one still running. If the server says `TURNO_EN_CURSO`, the message goes back to the composer and the previous one is awaited |
| **Regenerate** | Cuts the history **before** the discarded answer: if Morgan saw it, it would tend to repeat it |
| **Edit and continue** | Inline, only your last message; what came after is discarded |
| **Drag and drop** | Through the same route as the paperclip, one by one, with the file's name in each error |
| **Voice** | `POST /uploads/{id}/transcripcion` returns the text to the composer **so it can be reviewed before sending**. An injection attempt through audio stops being invisible. It spends quota the same |
| **Download a copy from the PC** (3.1-E) | When Morgan brings a file from the PC, the turn emits `archivo_listo` and the chat paints **a button** under the answer, with name and size. **The model's text isn't read**: the first version depended on it writing the link as is, and it wrote the address its own way, so I got "a sort of link" and no button. When reloading an old conversation the button isn't there (the history keeps the text, not the events); the file is still in Settings → Your files |

**Your computers** (Settings, 3.7): for each PC, whether it's **connected now** and what it
offers (reading, writing, terminal, processes), and its 7-day order history, loaded when you
open it. Without arguments or content: which capability, when, how it ended and how long it
took. Since 3.8, **whether it has an old version** ("There is a new version: …; the PC will ask
you whether to install it") and when its credential changed. When asking for a code, the web
gives **the PowerShell line that installs the agent** with it on a new PC.

**What is done on the PC, told.** The turn's progress says which PC tool is running ("Creating
the file on your PC…", "Waiting for you to confirm it on your PC…") and, since 3.4, how the
order is going there (the `equipo` event): "Queued on your PC", "Cancelling on your PC…". And a
plan shows each step's arguments with their line breaks: what Morgan is going to write is
approved by seeing it as it is (3.3).

**Links in the chat.** The Markdown renderer doesn't draw links, on purpose: the text is written
by the model, which may have read a page with hidden instructions, and turning any address into
a button would hand out a place to put a fake link with Morgan's face on it. **The only
exception** is this same server's download route (`/api/uploads/{id}/contenido`, also written as
the web's own full address). Any other address is still shown as text.

The first four share a single send function. The buttons appear on hover and only if they
apply; the retry one after an error is always visible.

**Deleting the account** asks for the password even with a session, deletes the data first and
the account afterwards (a failure halfway leaves an empty account, not orphan rows), accepts no
identifier and doesn't delete the audit.

## 4. The phone

On 2026-09-10, opening the web on simulated phones with the session open, **there was no
navigation**: a mobile rule placed before the base one hid the menu button at every width. On a
narrow desktop it didn't reproduce. The same walkthrough (eleven views on three phones) found
the composer off screen, iOS zooming the page on fields under 16 px, suggestions overflowing at
320 px and `100vh` lying about the height. In production, the confirm-your-email notice pushed
the whole chat out.

Rules that stayed:
- **Everything mobile lives at the end of the CSS**; before it, any later rule covers it
  without warning. `tests/test_movil_web.py` pins it.
- Nothing floats over the content: the menu button and the account go in `.barra-superior`, in
  the flow (a 52 px gap used to be reserved). At 620 px or less the cards switch to one column
  and send stays as the icon; at 480 px the workspace pill disappears (it's in the side bar).
- 44 px touch targets, `100dvh` and `env(safe-area-inset-*)`.

> **To measure the phone, a screenshot with a narrow window isn't enough**: headless Edge
> doesn't go below 492 px and crops the image. You have to measure `innerWidth` or put the web
> in an iframe. That's how the 2.3 audit checked that at 360 and 400 px nothing goes off.

## 5. What broke the web in production

On 2026-09-07, the web's chat **had never worked in the cloud**. Ten defects that lived
together and covered for each other; none was visible locally or caught by the suite. They were
found by reproducing against the real deployment what the browser does.

| # | Defect | Fix |
|---|---|---|
| 1 | The CSRF token couldn't be read from another domain: the whole web was read-only | The token also travels in the JSON of `/auth/*` |
| 2 | **Supabase didn't filter by user in almost any query**: 500 when creating conversations and, where it didn't fail, each user saw everyone else's | `_mio()` in the 32 operations of the 6 repositories; the user is never passed as a parameter |
| 3 | If `/auth/yo` failed, the interface assumed "this Morgan doesn't require accounts" | It retries, and if it keeps failing the sign-in screen is shown |
| 4 | Buttons that failed silently | The error is shown |
| 5 | The expired cookie wasn't deleted across domains | It's deleted with the same attributes it was set with |
| 6 | The quota function was out of PostgREST's reach: 500 **only for whoever wasn't the owner** | A wrapper in `public` only for `service_role` |
| 7 | And that function had never worked (`text` against `date`) | Conversion inside the function |
| 8 | **Memory didn't persist in the cloud**: it answered 200 and the table was empty | Memory uses the container's repository |
| 9 | **On the iPhone the session always dropped**: Safari discards third-party cookies | A `/api/*` proxy on Vercel: the API becomes same-origin |
| 10 | Uploading a file arrived without a session: it had its own `fetch`, copied and outdated | **A single `fetch` in the whole frontend**, pinned by a test |

What they taught:

- **An audit by reading only finds what it occurs to you to look for.** The V1.8 one looked for
  unfiltered SQL; the Supabase queries are PostgREST and didn't show up.
- **Reproducing what the browser does isn't using a browser.** `httpx` keeps any cookie; #9 only
  existed in Safari.
- **A configuration file that isn't applied doesn't warn.** The proxy was first written in a
  `web/vercel.json` that Vercel doesn't read. Now a test requires a single `vercel.json`.
- **Count rows, don't trust the 200.** #8 complied in appearance.

### The proxy's 120 seconds

Vercel's edge cuts a **silent** response at 120.1 s (measured twice in a row). That's why
`/chat` in the cloud gives up at 85 s and **the request answers at 100 s** even though the turn
continues: the turn isn't killed, it finishes and is saved, and the notice says "I'm still
working on it", not "error", so nobody repeats it and pays twice. Since 2.0.14 the web uses
`/chat/stream`, with heartbeats, and the cut stops affecting it: the ceiling is on silence, not
duration.

## 6. Frontend tests

They came because of a bug that only happened **when everything worked**: a `return` inside the
first `try` skipped the second one's `finally`, and the web stayed forever on "the server is
starting" with the server answering in 0.3 s. Neither the Python tests, nor `tsc`, nor the HTTP
checks could see it.

| Layer | What it checks |
|---|---|
| **Vitest** (`npm test` in `web/`) | Behavior: `Cuenta.test.tsx` (startup always finishes), `api.test.ts` (CSRF, cancelling, lost session, uploads, HTML error pages), `progreso.test.ts`, `Tokens.test.tsx` (the value is seen once, revoking confirms, what is asked for by default; 12 of 12 mutations), `DescargaWindows.test.tsx` (only from Windows and not inside the program) |
| **Python**: `tests/test_frontend_estabilidad.py` | Invariants of all the code: **every "busy" flag is turned off in a `finally`** and **a single `fetch`** |
| **Python**: `test_contrato_web.py`, `test_movil_web.py`, `test_tema_web.py`, `test_proxy_mismo_origen.py`, `test_cabeceras_web.py` | That every route the web calls exists, the mobile rules, the contrast, the proxy and the security headers |

```bash
cd web
npm run build   # what Vercel runs; it starts with tsc -b, which is NOT tsc --noEmit
npm test
npm run lint
```

The Vitest configuration lives in `vitest.config.ts` and not in `vite.config.ts`: put there,
`tsc -b` failed and **Vercel stopped deploying** without warning.

**Still to cover**: the chat's logic (stop, regenerate, edit) with behavior tests.
