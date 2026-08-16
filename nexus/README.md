# Nexus

A mobile AI chat app (React + Vite + Capacitor, Android) that talks to
OpenRouter and/or Gemini directly, with optional Supabase sync.

## Stack

- **UI**: React 18, Tailwind, `react-markdown` + `highlight.js` for rendering
- **Shell**: Capacitor 8 → Android (no iOS platform yet)
- **Providers**: OpenRouter (`src/lib/openrouter.js`) and Gemini direct
  (`src/lib/gemini.js`), with automatic fallback between them mid-request
- **Storage**: `src/lib/db.js` — Supabase when `VITE_SUPABASE_URL` /
  `VITE_SUPABASE_ANON_KEY` are set, otherwise `localStorage`
- **Images**: generation via `src/lib/imagegen.js`, vision input via the
  provider APIs

## Setup

```bash
cd nexus
npm install
cp .env.example .env   # fill in keys — see "Environment variables" below
npm run dev
```

## Environment variables

| Var | Required | Notes |
|---|---|---|
| `VITE_OPENROUTER_API_KEY` | one of these two | Or leave unset and paste a key in-app (Settings) |
| `VITE_GEMINI_API_KEY` | one of these two | Same — in-app key entry works too |
| `VITE_SUPABASE_URL` | optional | Enables cross-device sync |
| `VITE_SUPABASE_ANON_KEY` | optional | Public anon key — safe to expose, RLS does the gating |

**Important:** this is a Vite app, so any `VITE_`-prefixed variable gets
compiled straight into the JS bundle — including the built Android APK.
Anyone who unzips the APK can read these values back out. Treat committing
real provider keys to `.env` as equivalent to shipping them publicly once
you build. Prefer pasting keys in-app (stored in `localStorage` on-device
only) over baking them into the build, or route provider calls through a
server-side proxy if you want to ship an APK to other people.

## Android build

```bash
npm run build
npx cap sync android
# then open nexus/android in Android Studio, or:
./build-and-install.bat   # builds + installs on a connected/emulated device
```

Build/install logs land in `gradle-build.log` and
`build-and-install-log.txt`; a crash log (if the app dies on launch) is
written to `crash-log.txt`.

## Supabase (optional sync + magic-link auth)

Schema lives in `supabase/schema.sql`. To enable sync:

1. Create a Supabase project, run `supabase/schema.sql` in the SQL editor
   (fresh project) — or, if you already have the old anon-mode schema, run
   `supabase/migrations/001_auth_and_rls.sql` instead to tighten it in place.
2. Set `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`.
3. Authentication → Providers → enable **Email**.
4. Authentication → URL Configuration → add your app's origin (e.g.
   `http://localhost:5173` for dev, or your deployed URL) as a **Redirect
   URL** — the magic-link email won't complete sign-in without this.

Users sign in from Settings → Sync with just an email address (magic link,
no password). Sign-in is optional: signed-out, the app works fully offline
against `localStorage`; signing in later migrates any local chats up to
the account automatically and switches to synced storage per row-level
security (`user_id = auth.uid()` — a signed-in user can only ever read or
write their own rows).

Without Supabase configured at all, the app runs fully offline against
`localStorage` — chats just won't follow you to another device.

## Project layout

```
nexus/
├── src/
│   ├── App.jsx           ← top-level state, streaming orchestration
│   ├── components/       ← Sidebar, ChatView, Composer, Settings, VoiceMode, …
│   └── lib/
│       ├── db.js          ← persistence (Supabase / localStorage)
│       ├── openrouter.js  ← OpenRouter client (catalog + streaming)
│       ├── gemini.js      ← Gemini direct client
│       ├── auto.js        ← "Auto" model routing (task detection → model pick)
│       ├── imagegen.js    ← image generation backends
│       ├── settings.js    ← user settings store
│       ├── speech.js      ← TTS (native on Android)
│       └── util.js        ← shared helpers, context-window trimming, export
├── supabase/schema.sql
├── android/               ← Capacitor Android platform
└── capacitor.config.json
```

## Known limitations

- No conversation branching — editing or regenerating a reply overwrites
  it in place.
- No non-image file attachments (PDF, text, code files).
- Android only; no iOS platform folder.
- Context trimming (`trimMessagesToBudget` in `lib/util.js`) uses a rough
  chars/4 token estimate, not the real tokenizer — conservative by design,
  but not exact.
