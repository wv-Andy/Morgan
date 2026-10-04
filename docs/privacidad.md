# Morgan's privacy policy and terms of use

**English** · [Español](privacidad.es.md)

> In force since 2026-10-03.

Morgan is a personal project that I maintain myself, a single person, and that anyone can use
for free by creating an account. Here I explain what data it stores, where, who it is shared
with so that Morgan works, for how long, and what you can do with it. If any of this changes,
I change it here first.

## What Morgan stores

| What | What for |
|---|---|
| Your account: username, email and your password **hashed** (never in plain text) | So you can sign in, and recover your password by email |
| Your conversations, what Morgan remembers about you (memory), your plans, tasks, automations and the notices in the tray | So Morgan has them the next time you come in |
| The files you upload | So Morgan can read them in the conversation |
| How much you use Morgan each day (number of messages) | To share the free quota among everyone |
| Your open sessions (the browser you sign in from) and sign-in attempts | So you can close them, and to stop anyone trying to guess passwords |
| If you connect your PC: the computer's name, its version and the history of orders Morgan sent it (30 days) | So you can see what was done on your PC and revoke it |

**What it doesn't store**: the contents of your PC. Morgan only sees on your PC what you allow
in «Morgan on your PC» (the folders and capabilities you switch on), at the moment it asks
for it, and what it reads goes through the conversation like any other message. The PC's
access keys stay encrypted on your computer.

## Who it is shared with

I don't sell or hand over your data to anyone. To work, Morgan uses these services, which
process it on my behalf:

| Service | What it receives | Where |
|---|---|---|
| **Supabase** | The database and the uploaded files | United States (us-east-1) |
| **Render** | Morgan's server (what goes through it; it doesn't store it) | United States (Virginia) |
| **Vercel** | The web page | Global |
| **Groq**, **Google (Gemini)** and, as a last resort, **OpenAI** | The text of each conversation, so the model can answer | United States |
| **Serper (Google)** | What Morgan searches the internet for on your behalf | United States |
| **Brevo** | Your email, only to send you the verification or the password recovery | European Union |
| **GitHub** | If you use Morgan for Windows: one request a day to its releases page, to tell you about a new version (like any visit, it sees your PC's IP; it carries nothing of yours) | United States |

Each model provider handles what it receives under its own terms. **One to watch out for**:
the free tier of the Gemini API may use what is sent to it to improve Google's products. Morgan
only uses it as a fallback when Groq doesn't answer, but if one of your conversations goes
through Gemini, those terms apply to it. Don't write anything in Morgan that you wouldn't want
a model provider to read.

## For how long

Your data is kept while you have the account. What expires on its own: the notices in the
tray and the history of orders to the PC (30 days), the sessions and the recovery links (when
they expire). I make backups of the database, which I keep myself, on my computer.

## What you can do

- **Download all your data** (Settings → Export my data).
- **Delete your account** (Settings → Delete the account): everything of yours is deleted from
  the database and your PCs are disconnected. Whatever could remain in a backup disappears
  when that backup is replaced.
- Revoke the access of a PC or an API token at any moment.
- Ask me anything about your data, or ask me to delete something: open an issue in
  [Morgan's repository](https://github.com/wv-Andy/Morgan/issues) (without putting anything
  private there; I'll answer and we'll continue wherever you prefer).

## Terms of use

- Morgan is free, provided **as is** and without warranties: it may fail, be down or change.
  Don't use it for anything where a mistake would cause you serious harm.
- Morgan can be wrong. What it does on your PC it proposes first and you approve; check what
  you approve.
- There is a daily usage quota per person, so there is enough for everyone.
- Don't use it for anything illegal, to attack others or to overload the service. If you do,
  I may close the account.
- Morgan's code is public ([wv-Andy/Morgan](https://github.com/wv-Andy/Morgan)).
