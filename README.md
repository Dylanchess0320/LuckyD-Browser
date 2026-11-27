<div align="center">

# LuckyD Browser

**The Chromium browser with a real AI assistant, a full coding agent, and a multi-agent terminal — in one window.**

[![Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=7c5cff&label=release)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078D4?logo=windows&logoColor=white)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![Built with](https://img.shields.io/badge/built%20with-PySide6%20%2F%20Qt%20WebEngine-green)](https://www.qt.io/)
[![Tools](https://img.shields.io/badge/agent%20tools-122-7c5cff)](#the-one-window-platform)
[![Tests](https://img.shields.io/badge/tests-3%2C500%2B-brightgreen)](#build-from-source)

### [⬇ Download LuckyD Browser for Windows](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)

**[📝 Changelog](CHANGELOG.md)** · **[📖 Documentation](https://dylanchess0320.github.io/LuckyD-Browser/)** · **[💬 Discord](https://discord.gg/CmvErK8bkM)**

</div>

---

## Why LuckyD?

Most AI browsers rent you a chatbot. LuckyD runs the AI where you do — on your machine with Ollama, on free gateways when they're available, or on your own keys. No account required to start. No telemetry. MIT licensed.

**By the numbers (v10.9.5):** **122 tools** · **17 provider entries** · **3,500+ tests** · **90% coverage** · **11 permission scopes** · **16 mesh backends**

- 🆓 **Free AI, no catch** — no account, no API key, no subscription. The first-run setup installs Ollama and a local model, so the assistant works without an account or key.
- 🌐 **A real browser** — Chromium engine, tabs, bookmarks, workspaces, built-in ad blocking, private mode.
- 🛠 **A real coding agent** — `luckyd-code` ships in the box: 122 tools, memory, sessions, orchestration, and a trust layer.
- 🔒 **Private by default** — no telemetry, no bundled keys. With local models, prompts and page content stay on your machine.
- 🎨 **Yours, visually** — hand-built themes, animated homepages you can switch off, and a setting to rename the browser itself.

> Other AI browsers rent you their chatbot. **LuckyD finds you a free one — or runs one on your machine.**

---

## 🥊 Why people switch

| | **LuckyD** | Comet | Dia | Edge + Copilot | Chrome + Gemini |
|---|---|---|---|---|---|
| Free AI **with no account/key** | ✅ unlimited, local | ❌ | ❌ | ❌ | ❌ |
| **Local models** (Ollama built in) | ✅ | ❌ | ❌ | ❌ | ❌ |
| AI works **fully offline** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Autonomous agent drives your tabs | ✅ | ✅ | ✅ | limited | limited |
| **Coding agent + terminal in a tab** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Open source | ✅ MIT | ❌ | ❌ | ❌ | ❌ |
| No admin rights to install | ✅ | ✅ | ✅ | ✅ | ✅ |

LuckyD is Windows 10/11 x64 today. The coding-agent CLI is plain Python and runs anywhere Python 3.10–3.12 runs.

---

## 🖥 The one-window platform

| Surface | What it is | Open it |
|---|---|---|
| **AI Sidebar** | Chat with Markdown bubbles, per-provider model picker, page-aware Q&A, visual Q&A, and an autonomous agent that drives your real tab — with a pre-run task review, step narration, and Stop button | `Ctrl+Shift+A` |
| **Coding Agent Terminal** | The full `luckyd-code` CLI in a real tab: **122 tools**, memory graph, sessions, orchestration, and background tasks | `` Ctrl+` `` |
| **Multi-Agent Terminal** | Four parallel agent sessions in one workspace, plus independent PowerShell/CMD terminal tabs on Windows ConPTY via xterm.js | `Ctrl+Alt+M` (workspace), `` Ctrl+Shift+` `` (PowerShell) |
| **Trust Dashboard** | Permission scopes, pending approvals, and an append-only audit log — "agentic with receipts" | `http://127.0.0.1:8000/trust` (the backend auto-starts) |
| **Live Dashboard** | New-tab hub: web search, one-tap app tiles (AI assistant, terminals, chat, network, bookmarks), speed-dial shortcuts, and a time-aware greeting | New tab |

**Harness mode (default ON):** sidebar agent tasks run on the coding-agent backend, which can drive your **live, visible tabs** through the Control API — exe brain, browser hands.

---

## 🤖 AI that tells the truth

The assistant auto-detects providers at launch, in priority order:

| Priority | Provider | Key needed? | Notes |
|---|---|---|---|
| 1 — **Local** | **Ollama** | ❌ | Free, unlimited, offline — **auto-installed by first-run setup** |
| 1 — Local | LM Studio | ❌ | Auto-detected on `127.0.0.1:1234` |
| 2 | Cline free tier | Cline login | Free-tier models, rate-limited |
| 3 | **OpenCode Zen** | `OPENCODE_API_KEY` | Keyed gateway serving the current platform catalog |
| 4 | ClinePass | Cline login | Flat-subscription gateway |
| 4 | Google Gemini | ✔ | Free tier available |
| 4 | Groq | ✔ | Free tier available |
| 4 | Z.ai / OpenRouter | ✔ | |
| 4 | DeepSeek / OpenAI / Anthropic | ✔ | |
| 4 | MiniMax / xAI / Moonshot / Meta / Mistral | ✔ | |

- **17 provider entries total.** Some are aliases or billing routes that share a key; the model picker lists whatever your providers actually have installed or available.
- **Status that tells the truth:** the sidebar's setup hint names the exact problem (Ollama not running, Ollama up but model-less, no provider), and `/key` lists which providers have a saved key, masked. Cline credit exhaustion is tracked separately.
- **Rotation that works:** when a model dies mid-request, the run rotates to the next working one instead of ending in an empty reply.
- **Local by default:** the first-run setup pulls `llama3.2:3b` for the browser sidebar; the coding agent defaults to `luckyd:8b` when that model is installed. Any Ollama model you pull is picked up automatically.
- **Vision is automatic:** screenshots stream to the model when it supports images and are skipped for text-only models.
- **One switch, everywhere:** the terminal and coding agent mirror the sidebar's provider.

---

## 🛡 Agentic with receipts

The trust layer is not a settings page bolted on at the end — it is the same code path every entry point uses.

- **11 permission scopes** — read, network, browser, files, shell, desktop, git, memory, agents, MCP, system.
- **Approval gates** at the CLI, the local web dashboard, and ACP server. `Write`, `Edit`, `Bash`, `PowerShell`, browser control, git push, and schedule mutations require approval by default.
- **Deny-by-default ACP** — editors without an interactive approval UI cannot silently run dangerous tools.
- **Append-only audit log** — every tool call records tool, redacted args, scope, risk, decision, duration, and outcome to `~/.luckyd/audit/audit.jsonl`.
- **Secret redaction** — password/token/key arguments never land in the log.
- **Circuit breaker** — four consecutive all-failure turns stop a runaway agent instead of burning to the turn ceiling.
- **Sandboxed code execution** — timeouts, output caps, temp-dir isolation, AST checks, and forbidden-import/call blocking.
- **Reusable kit** — [`kit/`](kit/) ships the scopes, approval engine, audit log, and circuit breaker as a stdlib-only package for Claude Code, OpenCode, and Codex CLI.

---

## 🌐 It's also just a really good browser

**HTTPS-Only Mode** · **per-site camera/mic/location permissions** · **named Workspaces** · **Memory Saver** (sleeps idle tabs) · **Read Aloud** (`Ctrl+Shift+L`) · **Summarize This Page** (`Ctrl+Shift+U`) · **Session restore** (windows, tabs, pinned state) · **tab groups** with an **AI organizer** · **Reader Mode** · tabs (pin, drag, hover previews, recently-closed) · omnibox with history completion · bookmarks (import/export Chrome/Edge HTML) with a **toggleable bookmarks bar** (`Ctrl+Shift+B`) · searchable history · downloads dock with cancel · **incognito** (🕶 badge, off-the-record profile) · in-page find · built-in **ad/tracker blocker** · **per-site zoom memory** · one-key **page screenshots** (`Ctrl+Shift+S`) · **multi-terminal tabs** · **AI data extraction to Markdown** · print / save-page · view-source + DevTools · auto-retry on network errors · four themes plus a hidden **Synthwave**, with glass toasts · command palette (`Ctrl+K`) · AI right-click actions (**Explain / Summarize / Translate**) · **copy chat as Markdown** · mouse back/forward buttons · full keyboard-shortcut reference (`Ctrl+/`).

Userscripts live under **Tools → Extensions**. LuckyD does not load Chrome Web Store extensions.

---

## 🧩 Architecture

```
┌──────────────────────────── LuckyD Browser (PySide6 / Qt WebEngine) ───────────────────────────┐
│                                                                                                │
│   Tabs (Chromium)      AI Sidebar ──────────┐      Dashboard / Agent terminal                  │
│        │                                  │              (served by Control API)               │
│        │ CDP                              │                                                    │
│        ▼                                  ▼                                                    │
│   Browser Control API (127.0.0.1:9777)   AI Bridge ──► Ollama (local, keyless)                 │
│   /status /tabs /snapshot /act …         multi-provider ──► Cline / Gemini / Groq / DeepSeek … │
│        ▲                                  │                                                    │
│        │ drives live tabs                 │ mirrors provider                                   │
│   luckyd-code backend (port 8000) ◄───────┘      In-browser terminal (ConPTY ↔ WebSocket,      │
│   122 tools · memory · orchestration             port 9881 — full luckyd-code CLI)              │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Browser Control API

Localhost HTTP control of the real browser — used by the coding agent, the terminal agent, and your own scripts. **Binds to `127.0.0.1` only**; token and HttpOnly session-cookie auth; cross-origin requests rejected. Toggle in **Tools → Browser Control API**.

```
GET  /status /tabs /screenshot /help     POST /navigate /tab/new /act /snapshot /eval /ask
```

Element indices in `/snapshot` and `/act` are the same ones the sidebar agent uses — anything that can read a snapshot can drive the page.

---

## 🔒 Privacy & security

- **Local-first AI**: with Ollama, prompts and page content never leave your machine.
- **No accounts, no telemetry, no sign-in** required to use the assistant.
- **All control surfaces bind loopback only**: Control API `9777`, web dashboard `8000`, terminal `9881`, CDP `9222`.
- **Token auth by default** on the Control API and local web server, plus HttpOnly cookies for in-browser tabs and Origin checks. The web server also enforces a 10 MB body cap and denies path-traversal reads of `.env`/`.git`.
- **Approval + audit** on every dangerous tool call; secrets are redacted before logging.
- Browsing data stays in `%LOCALAPPDATA%\LuckyDBrowser`; incognito uses an off-the-record profile that clears cookies and cache when the window closes.
- The shipped bundle contains **no API keys** — cloud providers activate only with *your* keys.

---

## 📦 Install in 3 steps

1. **[Download the installer](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)** (Windows 10/11, 64-bit)
2. Run it — installs for just you, **no admin needed**
3. Open LuckyD and press `Ctrl+Shift+A` — the AI works right away on free providers

- Installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
- Start Menu shortcut, optional desktop icon, **Settings > Apps** uninstall entry
- Portable ZIP available on the releases page
- Silent install: `LuckyDBrowserSetup-10.9.5.exe /VERYSILENT /NORESTART`
- Data folder: `%LOCALAPPDATA%\LuckyDBrowser` (settings, history/bookmarks DB, userscripts)

---

## 🆕 What's new in 10.9.5

**Agent task review safety gate and interactive `/apikey` provider setup.**

- **AI Sidebar Task Review** — autonomous browser runs now show a pre-execution review of the plan before driving open tabs.
- **Interactive `/apikey`** — paste, save, and activate provider keys with masked input, aliases, and atomic `.env` management.
- **Unified 10.9.5 runtime** — browser, LuckyD Code REPL, and Multi-Agent Terminal backend all report the same version.
- **10.9.3 hardening** — approval gates wired once at every entry point, `Edit` gated like `Write`/`Bash`, retries capped at two with a 120s limit, circuit breaker for stuck tool loops.
- **10.9.2** — `/key` saves to the shared `.env`, so OpenRouter and every provider connect without manual edits.

Older changes: [full changelog](CHANGELOG.md) · [all releases](https://github.com/Dylanchess0320/LuckyD-Browser/releases).

---

## 🧱 Build from source

Requirements: Windows 10/11 x64, Python 3.10–3.12, and (only for the installer) Inno Setup 6.

```powershell
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
pip install -r browser\requirements.txt

# Run from source
browser\run_browser.bat

# Build the app bundle (PyInstaller -> browser\dist\LuckyDBrowser)
cd browser
python -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
cd ..

# Build the shareable installer (Inno Setup -> browser\installer\output\)
powershell -NoProfile -ExecutionPolicy Bypass -File browser\installer\build_installer.ps1
```

**Tests** (headless; Qt is mocked on CI):

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

**3,500+ tests · 90% measured coverage · mypy clean on 204 files · ruff check + format clean.**

The installer script (`browser\installer\LuckyDBrowser.iss`) and the AI bootstrap (`browser\installer\ollama_setup.ps1`) are plain text — tweak away.

---

## ⚠ Current limitations (v10.9.5)

- **Windows 10/11 x64 only** for the browser. The coding-agent CLI is cross-platform Python.
- **No Chrome Web Store extensions.** Built-in userscripts are supported under Tools → Extensions.
- **No H.264/AAC decoders** in the bundled Qt WebEngine build — some video and game-audio sites won't play.
- **Installer is not code-signed yet**, so Windows SmartScreen may warn. There is no in-app auto-updater; **Help → Check for Updates** opens the GitHub releases page, and upgrades are manual.
- **Chromium 140** is pinned via PySide6 6.11.3 for rendering stability; security patches follow Qt's release cadence.

---

## ⚙ Configuration

- **Settings UI**: search engine, homepage, ad/tracker blocking, HTTPS-Only, download folder, default + per-site zoom, startup/session restore, memory saver, homepage animation, themes (four plus a hidden Synthwave), browser display name, harness auto-start, CDP debugging.
- **Provider keys** (optional): set `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, … in `%LOCALAPPDATA%\LuckyDBrowser\.env` (or the repo `.env` in a source checkout), or paste them with `/apikey` in the assistant. No keys = local Ollama.
- **Local model**: the bootstrap installs `llama3.2:3b` (fast, tool-capable, CPU-friendly). Pull others any time, e.g. `ollama pull gemma3:4b` (adds vision) or `ollama pull qwen3:8b` (better quality on GPU machines) — the picker finds them automatically.

---

## 🧹 Uninstall

**Settings > Apps > LuckyD Browser**, or the **Uninstall LuckyD Browser** shortcut in the Start Menu.
Browsing data is kept in `%LOCALAPPDATA%\LuckyDBrowser` — delete it manually for a full wipe.
Ollama is a separate product and is left installed (remove via Settings > Apps > Ollama).

---

## 🧱 Tech stack

**PySide6 / Qt WebEngine** (Chromium 140) · Python 3.10+ · httpx (multi-provider AI bridge) · Chrome DevTools Protocol · pywinpty + WebSockets + xterm.js (terminal) · PyInstaller · Inno Setup · Ollama (local models).

## 📄 License

MIT © 2026 [DylanChess03](https://github.com/Dylanchess0320) — see [LICENSE](LICENSE).

---

*If LuckyD Browser saves you an API bill, a ⭐ on the repo is appreciated.*
