# LuckyD Browser

A full-featured, Chromium-based web browser for Windows, built with
**PySide6 / Qt WebEngine** — and the front end of the **LuckyD One Platform**:
browser + AI assistant + coding agent (`luckyd-code.exe`), all inside one window.

## The One-Window Platform (v1.3)

Open the browser and everything else comes with it:

- **Live Dashboard** (new-tab page, served by the built-in Control API):
  status pills for the harness/AI/API, an **Ask LuckyD** answer box,
  one-tap tiles for every part of the platform, web search and your
  speed-dial shortcuts.
- **Coding Agent HQ** — the `luckyd-code.exe` workspace (98 tools, memory
  graph, sessions, orchestration) opens **as a browser tab** via the ⚡
  toolbar button, `Ctrl+Shift+H`, the dashboard tile, or the sidebar's HQ
  button. The exe **auto-starts when the browser launches** (Settings toggle);
  the `/hq` gateway redirects instantly when it's up and shows an
  auto-refreshing boot splash when it isn't.
- **AI sidebar** (`Ctrl+Shift+A`): chat bubbles with Markdown rendering
  (code blocks, bold, lists), live harness status line, per-provider model
  picker, quick actions, visual Q&A, FMHY tool search, and the autonomous
  agent — with **Harness mode** routing tasks to the exe backend, which can
  drive your live tabs through the Control API.
- Links inside the assistant/dashboard always open in LuckyD tabs — you
  never get kicked out to another browser.

## Run it

**As an installed app** (Start Menu + Desktop shortcut, Settings > Apps entry):
```
powershell -NoProfile -ExecutionPolicy Bypass -File browser\install.ps1
```

**Portable exe** (no install, no console window):
```
browser\dist\LuckyDBrowser\LuckyDBrowser.exe
```

**From source** (no console window):
```bat
browser\run_browser.bat
```

**Test it** (launches real window, 44 checks):
```bat
python browser\selftest.py
```

## Rebuild / reinstall after changes

```
cd browser
python -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File browser\install.ps1
```

> The spec excludes `PIL` on purpose: Pillow 12.3's `Image.py` crashes
> Python 3.10.0's `dis` during PyInstaller's modulegraph scan
> (`IndexError: tuple index out of range`), and the browser never imports
> PIL at runtime (only the `make_icon.py` dev tool does).

## Shareable installer (give this to other people)

Build a single `setup.exe` that anyone can run — **no Python, no admin
rights, Windows 10/11 x64 only**:

```
powershell -NoProfile -ExecutionPolicy Bypass -File browser\installer\build_installer.ps1
```

Output: `browser\installer\output\LuckyDBrowserSetup-2.0.0.exe`

- Installs per-user to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
- Start Menu shortcut, optional desktop icon, Settings > Apps uninstall entry
- Includes everything (Qt WebEngine runtime **and** the `luckyd-code.exe`
  backend) — the target machine needs nothing else
- Only the *build* machine needs Inno Setup 6: https://jrsoftware.org/isdl.php
- Silent install for scripting: `LuckyDBrowserSetup-2.0.0.exe /VERYSILENT /NORESTART`
- The raw script is `browser\installer\LuckyDBrowser.iss` if you want to tweak
  it (then recompile with `iscc browser\installer\LuckyDBrowser.iss`)

## Features

| Area | What you get |
|------|--------------|
| Platform | **Live dashboard** new-tab page, **Coding Agent HQ in a tab** (⚡ / Ctrl+Shift+H), harness **auto-start on launch**, shared harness status everywhere |
| Tabs | New/close/pin-style reorder (drag), Ctrl+T / Ctrl+W / Ctrl+Tab / Ctrl+1-9, `+` button, popups open as tabs, middle-click to close, hover preview cards, wheel cycling, recently-closed stack (Ctrl+Shift+T), **session restore — "continue where you left off"** (Settings → On startup), **tab groups with colors + collapse** (right-click → Tab Group), **AI tab organizer** (Tools) |
| Reader | **Reader Mode** (Ctrl+Alt+R): text-density extraction into a themed serif view; **Copy Link to Highlighted Text** (`#:~:text=` fragment links) |
| Layout | **Vertical tabs** (View menu) with group colors; **Focus Mode** (Ctrl+Shift+F) strips all chrome; **Side Pane** (right-click a link) docks a second web view |
| DevTools+ | **Network Monitor** (Tools): live request table over CDP with filter + **HAR export** (`/network`); Omnibox **`?` prefix asks the AI** |
| Omnibox | URLs and searches in one bar, history-based completions, configurable search engine, Ctrl+L to focus |
| Bookmarks | Ctrl+D star toggle with toast feedback, **bookmarks bar (Ctrl+Shift+B, right-click to manage)**, Bookmarks menu + manager, **import from Chrome/Edge HTML export**, **export to HTML**, per-bookmark delete |
| History | Ctrl+H, searchable, clear-all, **delete individual entries** (right-click), auto-recorded to SQLite |
| Downloads | Ctrl+J dock with live progress, **speed + ETA**, **pause/resume + cancel** per download, right-click context menu, Clear Completed, custom folder, double-click to open |
| Incognito | Ctrl+Shift+N — off-the-record profile, nothing touches disk, **visual 🕶 badge** in toolbar |
| Find | Ctrl+F in-page find with next/prev |
| Vision | **📷 Visual Q&A** button: screenshots the tab via raw CDP (works with GPU rendering — `QWidget.grab()` can't) and asks a vision model |
| Context menu | Right-click links: **Open in New Window, Save Link As, Copy as Markdown**; Media: **Save Media As**; Text: **AI ▸ Explain / Summarize / Translate**; **Back/Forward mouse buttons** (XButton1/XButton2) |
| View | Zoom (Ctrl +/-/0, **Ctrl+Scroll**, **zoom percentage indicator**), **per-site zoom memory + configurable default zoom**, F11 fullscreen, Ctrl+U view-source, F12 DevTools, **🔒 HTTPS lock icon** in status bar |
| Privacy | Built-in ad/tracker blocklist (Tools menu), clear cache/cookies in Settings |
| Reliability | Transient network errors auto-retry up to 3× behind a friendly "Connecting…" page |
| Print/Save | **Ctrl+P print**, **Ctrl+S save page as HTML**, **Ctrl+Shift+S save page screenshot (GPU-safe CDP capture)**, **full-page screenshots** (entire scrollable document), Ctrl+O open local file |
| Terminal | **Multi-terminal tabs**: every terminal tab is its own ConPTY session — **agent CLI** (Ctrl+\`), **PowerShell** (Ctrl+Shift+\`), or **CMD**, switchable live in the tab's shell bar |
| Automation | **Workflow recorder/replayer** (Tools → Workflows…): records Control-API actions into named JSON workflows, replays them with **self-healing element re-resolution** (fingerprint scoring when indices drift), manager page with per-step replay log. **Scheduled workflows** auto-replay every 15m–daily. **POST /extract**: schema-guided AI extraction of structured JSON from the active page |
| Help | **Ctrl+/ keyboard shortcuts reference**, About dialog |
| AI | Sidebar (Ctrl+Shift+A): **Markdown chat bubbles**, live **harness status line**, chat with page context, FMHY tool search, autonomous agent that drives your **real visible tab** — watch every step live: orange highlight ring + 🤖 badge on the element it's about to click, step narration in the sidebar, Stop button always available. Popup windows open as normal tabs the agent can read, and JavaScript alert/confirm/prompt dialogs are auto-dismissed mid-task. **Ultra-fast + smart mode:** plan-first reasoning, stuck detection, no API key needed. **Vision steps are AUTO** — screenshots stream to the model every step when the selected model accepts images (gpt-4o, gemini, claude-sonnet, gemma3…), auto-disabled for text-only models, and image payloads now work on Gemini and Anthropic endpoints too |
| Themes | 4 futuristic themes (Neon Night, Cyber Grove, Solar Dusk, Arctic Light) + a **secret Synthwave Sunset** (Konami code on the new-tab page) — live switch in Settings or `POST /theme`, **themed tab bar + toolbar**, glass toasts, command palette (Ctrl+K) |
| Fun | **Offline runner arcade** on the error page, time-aware dashboard greetings, confetti for flawless workflow replays |
| Updates | **One-click in-app updates**: silent startup check, release-notes preview, background download, restart-to-apply (installer runs silently; session restore brings your tabs back) |
| Harness | **🔌 Harness mode — DEFAULT ON** (AI sidebar): agent tasks run on the `luckyd-code.exe` backend — 98 tools, memory graph, orchestration — auto-started when needed, live progress, results in chat. Tasks auto-include Browser Control API instructions so the exe can drive your live tabs. Uncheck to use the in-browser agent |
| Control API | **Browser Control API** (`127.0.0.1:9777`, Tools menu): localhost HTTP control of the real browser — status/tabs/navigate/snapshot/act/screenshot/eval/ask — for the harness, the terminal agent, and scripts |

## Browser Control API

Localhost HTTP control of the REAL browser (default `http://127.0.0.1:9777`).
This is what lets the `luckyd-code.exe` harness (98 tools), the terminal
agent, and any script drive the tabs you are looking at. Toggle it in
**Tools → Browser Control API**; change the port with the
`browser_api_port` setting; set `browser_api_token` to require
`Authorization: Bearer <token>` on every request. Binds to 127.0.0.1 only —
never expose it on a network interface.

```
GET  /status                 browser state + harness reachability
GET  /tabs                   open tabs (index, url, title, active)
POST /navigate               {"url": "https://…", "new_tab": false}
POST /tab/new | /tab/activate | /tab/close
POST /snapshot               URL, title, numbered interactive elements, text
POST /act                    {"action": "click|type|press|select|scroll|navigate|back|wait", "index": N, "text": "…"}
GET  /screenshot             base64 JPEG of the visible tab (via CDP)
POST /eval                   {"js": "…"} — run JavaScript in the active tab
POST /ask                    {"question": "…"} — AI answer grounded in the page
GET  /help                   machine-readable route list
```

The element indices in `/snapshot` and `/act` are the same ones the AI
sidebar agent uses, so anything that can read a snapshot can drive the page.

## Harness mode (luckyd-code.exe backend)

The AI sidebar's **🔌 Harness mode** checkbox routes agent tasks to the
`luckyd-code.exe` web server (`--web --port 8000`): 98 tools, memory graph,
LSP, orchestration pipeline. The exe is auto-started when missing, progress
polls via `/api/background/*`, and results render in the chat. When the
Control API is live, tasks automatically carry usage instructions for it —
so the exe's agent can click and read your real tabs (exe brain, browser
hands). `python start_platform.py` launches both together.

## Layout

```
browser/
├── main.py                   ← entry point
├── browser_app.py            ← QApplication + shared services + windows
├── selftest.py               ← automated functional smoke test (30 checks)
├── __init__.py               ← package marker (version = "1.2.0")
│
├── browser_core/             ← backend services
│   ├── __init__.py
│   ├── adblock.py            ← domain + URL pattern ad blocker
│   ├── agent.py              ← autonomous browsing agent loop
│   ├── ai_bridge.py          ← LLM multi-provider bridge (keyless local + cloud)
│   ├── cdp_driver.py         ← raw Chrome DevTools Protocol driver
│   ├── cline_session.py      ← Cline CLI auth session reader
│   ├── control_server.py     ← Browser Control API (localhost HTTP control)
│   ├── harness_bridge.py     ← luckyd-code.exe harness client (98 tools)
│   ├── fmhy.py               ← FMHY free-tools search index
│   ├── profile.py            ← WebEngine profiles (normal + incognito)
│   ├── screenshot.py         ← CDP-based screenshot capture
│   ├── scripts.py            ← userscript engine (Greasemonkey)
│   ├── settings.py           ← JSON key/value settings store
│   └── storage.py            ← SQLite history + bookmarks (+ import/export)
│
├── browser_ui/               ← Qt UI components
│   ├── __init__.py
│   ├── main_window.py        ← MainWindow: toolbar, menus, docks, shortcuts
│   ├── tab_widget.py         ← custom tab bar + tab lifecycle
│   ├── web_view.py           ← QWebEngineView subclass
│   ├── omnibox.py            ← address bar (URL + search + completions)
│   ├── ai_sidebar.py         ← AI assistant sidebar + agent UI
│   ├── dialogs.py            ← History, Bookmarks, Settings, Scripts dialogs
│   ├── downloads.py          ← downloads dock (progress + cancel + clear)
│   ├── palette.py            ← Ctrl+K command palette
│   ├── theme.py              ← design system (4 themes, QSS generator)
│   └── toasts.py             ← glass toast notifications
│
├── assets/                   ← bundled resources
│   ├── icon.png / icon.ico   ← app icons
│   ├── newtab.html           ← new-tab page (live clock, shortcuts, search)
│   ├── adblock.txt           ← domain blocklist (1.2k+ domains)
│   └── userscripts/          ← built-in userscripts (Dark Mode, Video Speed)
│
├── data/                     ← runtime user data (git-ignored)
│   ├── settings.json         ← persistent settings
│   ├── browser.db            ← SQLite history + bookmarks
│   └── userscripts/          ← user-added scripts
│
├── build/ / dist/            ← PyInstaller build artifacts (git-ignored)
│
├── requirements.txt          ← PySide6 + httpx + websockets
├── run_browser.bat           ← launch from source (no console)
├── install_browser.bat       ← Start Menu entry + dependency install
├── uninstall_browser.bat     ← remove registry entry
├── LuckyDBrowser.spec        ← PyInstaller spec file
├── make_icon.py              ← icon generator utility
└── version_info.txt          ← build metadata
```

## Free AI, no API key

The sidebar and agent run fully keyless on a local model server:

```
winget install Ollama.Ollama
ollama pull qwen3:4b          # daily driver — best JSON/agent behavior per GB
ollama pull llama3.2:3b       # fastest option (~2 GB)
ollama pull gemma3:4b         # multimodal: sees screenshots (vision agent)
ollama pull nomic-embed-text  # embeddings for semantic history (~274 MB)
```

No GPU needed — all of the above run on CPU (tested target: Ryzen 5-class,
16 GB RAM). Expect ~8–15 tok/s from 3–4B models: chat feels responsive,
agent steps take a few seconds each. Avoid 8B+ models on CPU (2–4 tok/s).

Restart the browser — `ollama` appears first in the provider dropdown and
becomes the default of the fallback chain. LM Studio (localhost:1234) is
detected the same way. Set `OLLAMA_MODEL=` in the repo `.env` to pin a model.
Page context sent to local models is auto-shrunk (4k vs 12k chars) so long
pages don't stall CPU inference. Optional free-tier cloud keys
(`GOOGLE_API_KEY`, `GROQ_API_KEY`, …) are picked up as boosters behind the
local default — pick one in the dropdown when you want cloud speed.

## ClinePass (Kimi K3 — uses your Cline login)

If you're logged into the Cline CLI, the browser auto-detects the session
(`~/.cline`) and adds a **clinepass** provider running `cline-pass/kimi-k3`
— no key copying needed, and **the Cline terminal does not need to stay
open**. Session tokens live ~1 hour, so the browser renews them itself via
the stored WorkOS refresh token (writing the result back for the CLI) —
it works regardless of which model the CLI is currently set to, and only
disappears if you fully log out (`cline auth` signs back in). Permanent
option: create a key at app.cline.bot → Settings → API Keys and set
`CLINEPASS_API_KEY` in the repo `.env`.

**Model picker:** select `clinepass` in the provider dropdown, then use the
**Model** dropdown underneath to switch models (your pick is remembered per
provider, and works for Ollama/DeepSeek/etc. too):

- **Included in your flat subscription** — work regardless of credit balance:
  `cline-pass/` kimi-k3, glm-5.2, kimi-k2.7-code, kimi-k2.6,
  deepseek-v4-pro, deepseek-v4-flash (fast — good agent model), mimo-v2.5,
  mimo-v2.5-pro, minimax-m3, qwen3.7-max, qwen3.7-plus.
- **Credit-billed** (Cline Credits): the gateway's free-tier
  `minimax/minimax-m2.5` and the regular paid models
  (`deepseek/deepseek-chat`, `google/gemini-2.5-pro`,
  `anthropic/claude-sonnet-4-6`, `openai/gpt-4o`) — these need a
  non-negative credit balance at app.cline.bot/credits. With the provider
  set to "auto", a rate-limited/credit-blocked model falls through to the
  next provider automatically.

**Billing rules** (verified against Cline docs): credits are only ever
consumed by *paid* credit-billed models — billed from the first token.
Free models never bill: exceeding the free allowance returns HTTP 429
(wait + retry), not charges. ClinePass subscription models are flat-rate:
exceeding a quota window (5h / weekly / monthly) pauses until reset, never
bills. A 402 means "balance negative / credits insufficient" — a block,
not a charge. The model picker labels each entry `· flat subscription` or
`· credit-billed ⚠` so the boundary is always visible.

## Roadmap (not built yet)

- **Vision agent** — the screenshot pipeline is DONE (`browser_core/
  screenshot.py`, raw CDP `Page.captureScreenshot`); next step is feeding
  frames into the agent loop (local `gemma3:4b` / `moondream` on CPU, or a
  vision model on ClinePass)
- **Raw-CDP driver** — NB: Playwright's `connect_over_cdp` CANNOT attach to
  Qt WebEngine ("Browser context management is not supported"), but talking
  to page targets directly works (proven by the screenshot module) —
  `Input.dispatchMouseEvent`/`dispatchKeyEvent` via the same channel gives
  trusted input events vs JS injection
- **Userscript "extensions"** — Qt WebEngine can't run Chrome extensions, but
  `QWebEngineScript` gives Greasemonkey-style injection (DarkReader, reader
  mode, …) plus AI-generated scripts from a text description
- Semantic history search (local `nomic-embed-text` embeddings in SQLite),
  context-menu AI actions (explain/translate selection), background +
  scheduled agents, read-aloud answers (edge-tts — speakers only, no mic
  needed), session restore, per-site permissions

## Dev

```
python browser\selftest.py     # functional test (launches real window, 30 checks)
```
