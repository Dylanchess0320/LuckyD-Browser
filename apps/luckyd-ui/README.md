# LuckyD UI — chat-first cross-platform app (Claude/Codex-style)

Fancy face, same brain. This app talks to the **existing Python backend**
(`web_server.py` on `127.0.0.1:8000`) — no backend changes, no data migration.
Memory (`data/memory_store`), schedules (`~/.luckyd/schedules.db`), trust policy
and audit log (`~/.luckyd/`) are untouched.

## Run in 60 seconds (dev)

```powershell
# 1. Backend (repo root) — leave running
python web_server.py --web --port 8000 --host 127.0.0.1

# 2. UI
cd apps\luckyd-ui
npm install
npm run dev        # → http://127.0.0.1:5173/ (proxies /api + /health to :8000)
```

Get the token from `.luckyd-code\hq_token` (auto-created on first backend boot)
and paste it once in **Settings → Token**. It stays in `localStorage`.

## v6.2 highlights

- **Home dashboard** — live token spend (`/api/cost`), memory count, tool count,
  scheduled-agent count, pending approvals; one-click navigation + try-asking.
- **Slim desktop installer** — NSIS Setup exe now ships without `node_modules`,
  cutting the installer from ~279 MB to ~125 MB. Backend sidecar
  (`luckyd-code.exe`) and app icon bundled via `extraResources`.
- **File://-safe assets** — `vite.config.ts` sets `base: './'` so the built
  HTML references `./assets/*` instead of `/assets/*`. Without this the
  packaged window was blank (absolute paths resolve to the drive root under
  Electron's `file://` protocol).
- **Packaged backend** — Electron no longer spawns Python in production; it runs
  the bundled `luckyd-code.exe` from `resources/backend`.
- **Security hardening** — single-instance lock, token injected via preload
  `additionalArguments` (renderer never touches the filesystem), splash window
  while the backend boots, external links open in the default browser.
  `electron-main.cjs` / `preload.cjs` use `.cjs` so `require()` works alongside
  `"type": "module"` (Vite requirement); all main-process errors go to
  `%APPDATA%\luckyd\luckyd.log`. Headless check: `electron scripts/verify-html.cjs`.

## Build the installer

```powershell
cd apps\luckyd-ui
npm run installer        # tsc + vite build → electron-builder NSIS → installers\
# artifact: C:\Users\dylan\LuckyD-Browser\installers\LuckyD-Setup-6.2.0.exe
```

First run on Windows needs one manual step: seed the electron-builder
`winCodeSign` cache if 7-Zip can't create symlinks (no admin). Only needed once:

```powershell
& apps\luckyd-ui\node_modules\7zip-bin\win\x64\7za.exe x -y `
  "$env:LOCALAPPDATA\electron-builder\Cache\winCodeSign\<hash>.7z" `
  "-o$env:LOCALAPPDATA\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0"
```

## v6.1 highlights

- **Non-blocking chat** — messages run on the server's background worker
  (`POST /api/background/start` + poll `/api/background/status/:id`), so long
  agent runs never freeze the tab. Live `Working… · 42s` label, ■ Stop detaches
  cleanly (server keeps its audit trail). Old blocking `/api/chat` retired.
- **Markdown answers** — GFM tables/lists/code with copy buttons (react-markdown).
- **Session rail** — up to 50 local chats in `localStorage`, Claude-style.
  `Ctrl+K` jump-to-tab palette, `Ctrl+Shift+O` new chat.
- **Trust badge** — pending approvals surface on the 🛡 rail icon.

## Desktop shell (Electron, optional)

```powershell
cd apps\luckyd-ui
npm install
$env:LUCKYD_DEV_URL="http://localhost:5173"; npm run electron:dev
```

`electron-main.js` spawns `web_server.py` (dev) or `luckyd-code.exe` (packaged)
as a sidecar and waits for `/health` before showing the window.
`npm run electron:build` produces Win/Mac/Linux installers via electron-builder.

## What maps to what

| UI tab | Backend route (already exists) |
|---|---|
| Home 🍀 | `GET /api/cost`, `/api/brain/stats`, `/api/tools`, `/api/schedules`, `/api/approvals/pending` |
| Chat | `POST /api/chat`, `POST /api/clear`, `GET /api/settings` |
| Trust 🛡 | `GET /api/trust/scopes`, `/api/audit`, `/api/approvals/pending`, `POST /api/trust/policy`, `POST /api/approvals/resolve` |
| Schedules ⏰ | `GET /api/schedules`, `/api/schedules/runs`, `POST /api/schedules`, `POST /api/schedules/:id/:enable\|disable\|run\|delete\|update` |
| Memory 🧠 | `GET /api/brain/stats`, `GET /api/brain/search?q=` |
| Settings ⚙ | `GET /api/tools`, `GET /api/cost`, `GET /api/models` |

Design tokens mirror `browser/browser_core/brand.py` `PALETTES["neon"]`.
