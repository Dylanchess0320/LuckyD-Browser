# LuckyD Browser

**A Chromium-based AI browser for Windows â€” with a free, unlimited, offline AI assistant built in.**
No accounts. No API keys. No subscriptions. The installer sets everything up for you.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-blue)](https://github.com/Dylanchess0320/LuckyD-Browser/releases)
[![Built with](https://img.shields.io/badge/built%20with-PySide6%20%2F%20Qt%20WebEngine-green)](https://www.qt.io/)
[![AI](https://img.shields.io/badge/AI-local%20Ollama%20%2B%209%20cloud%20providers-purple)](#ai-providers)

LuckyD Browser is more than a browser â€” it's a **one-window AI platform**:

> **Web browser + AI assistant + coding agent HQ + full developer terminal â€” all in a single window, all able to drive your real tabs.**

<p align="center">
  <img src="docs/screenshots/sidebar.png" alt="LuckyD Browser â€” the local AI assistant (Ollama, llama3.2) answering in the sidebar. No API key, no account." width="920"><br>
  <em>The AI sidebar answering on a 100% local model â€” no key, no login, no cost.</em>
</p>

---

## ðŸ¥Š Why people switch

| | **LuckyD** | Comet | Dia | Edge + Copilot | Chrome + Gemini |
|---|---|---|---|---|---|
| Free AI **with no account/key** | âœ… unlimited, local | âŒ | âŒ | âŒ | âŒ |
| **Local models** (Ollama built in) | âœ… | âŒ | âŒ | âŒ | âŒ |
| Works **fully offline** | âœ… | âŒ | âŒ | âŒ | âŒ |
| Autonomous agent drives your tabs | âœ… | âœ… | âœ… | limited | limited |
| **Coding agent + terminal in a tab** | âœ… | âŒ | âŒ | âŒ | âŒ |
| Open source | âœ… MIT | âŒ | âŒ | âŒ | âŒ |
| No admin rights to install | âœ… | âœ… | âœ… | âœ… | âœ… |

The others rent you their AI. **LuckyD runs yours.**

---

## âœ¨ Headline: free AI, out of the box

Most "AI browsers" need an API key, an account, or a subscription before the assistant says a word.
LuckyD doesn't:

1. Run the installer.
2. Leave **"Set up free unlimited local AI"** checked (default).
3. Open the sidebar and chat â€” that's it.

The setup automatically installs **[Ollama](https://ollama.com)** (per-user, no admin) and pulls a fast,
tool-capable local model (`llama3.2:3b`, ~2 GB one-time download). From then on the assistant runs
**100% locally** â€” unlimited, offline, and private. Your prompts never leave your machine.

Prefer a cloud model instead? The sidebar supports 9 more providers â€” see
[AI Providers](#ai-providers). Your own keys, your choice.

---

## ðŸ“¦ Download & install

**[â¬‡ Download v3.8.0](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.8.0)** (`LuckyDBrowserSetup-3.8.0.exe`)

- Windows 10/11 x64 Â· per-user install Â· **no admin rights needed**
- Installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
- Start Menu shortcut, optional desktop icon, **Settings > Apps** uninstall entry
- Everything is bundled (Chromium runtime + coding-agent backend) â€” nothing else required
- Silent install for scripting: `LuckyDBrowserSetup-3.8.0.exe /VERYSILENT /NORESTART`

---

## ðŸ–¥ The one-window platform

| Surface | What it is | Open it |
|---|---|---|
| **AI Sidebar** | Chat (Markdown bubbles), per-provider model picker, page-aware Q&A, ðŸ“· visual Q&A, and an **autonomous agent that drives your real tab** â€” orange highlight ring, step narration, Stop button | `Ctrl+Shift+A` |
| **Coding Agent HQ** | The full `luckyd-code` workspace as a browser tab: 70+ tools, memory graph, sessions, orchestration, background tasks â€” auto-starts with the browser | âš¡ button or `Ctrl+Shift+H` |
| **In-browser Terminal** | Real terminals on Windows ConPTY via xterm.js â€” the `luckyd-code` agent CLI **and** plain PowerShell/CMD, each tab its own independent session | `Ctrl+`` ` / `Ctrl+Shift+`` ` |
| **Workflows** | Record Control-API actions into named automations and replay them with self-healing element matching | Tools â†’ Workflowsâ€¦ |
| **Live Dashboard** | New-tab hub: status pills, **Ask LuckyD** box, one-tap tiles, speed dial, time-aware greetings | New tab |

**Harness mode (default ON):** sidebar agent tasks run on the coding-agent backend, which can drive
your **live, visible tabs** through the Control API â€” exe brain, browser hands.

<p align="center">
  <img src="docs/screenshots/hq.png" alt="LuckyD Code HQ â€” the full coding-agent workspace running as a browser tab" width="920"><br>
  <em>The coding-agent HQ lives in a browser tab â€” 70+ tools, mirroring the sidebar's provider.</em>
</p>

---

## ðŸŒ It's also just a really good browser

**Session restore** (continue where you left off â€” windows, tabs, pinned state) Â· **tab groups**
(colors, collapse, restored with your session) with an **AI organizer** that sorts them for you Â·
**Reader Mode** Â· tabs (pin, drag, hover previews, recently-closed) Â· omnibox with history completion Â· bookmarks (import/export
Chrome/Edge HTML) with a **toggleable bookmarks bar** (`Ctrl+Shift+B`) Â· searchable history Â·
downloads dock with cancel Â· **incognito (ðŸ•¶ badge, nothing touches disk)** Â· in-page find Â·
built-in **ad/tracker blocker** Â· **per-site zoom memory** with Ctrl+scroll zoom Â· **one-key
page screenshots** (`Ctrl+Shift+S`) Â· **multi-terminal tabs** (agent CLI + PowerShell/CMD,
`Ctrl+`` / `Ctrl+Shift+``) Â· **workflow record & replay** with self-healing element matching Â·
**AI data extraction to JSON** Â· print / save-page Â· view-source + DevTools Â· HTTPS lock icon Â·
auto-retry on network errors Â· 4 futuristic themes with glass toasts Â· command palette (`Ctrl+K`) Â·
AI right-click actions (**Explain / Summarize / Translate**) Â· **Copy as Markdown** Â·
mouse back/forward buttons Â· full keyboard-shortcut reference (`Ctrl+/`).

---

## ðŸ¤– AI providers

The assistant auto-detects providers at launch, in this priority order:

| Priority | Provider | Key needed? | Notes |
|---|---|---|---|
| 1 â€” **Local** | **Ollama** | âŒ | Free, unlimited, offline â€” **auto-installed by setup** |
| 1 â€” Local | LM Studio | âŒ | Auto-detected on `127.0.0.1:1234` |
| 2 | Cline free tier | Cline login | Free-tier models, rate-limited |
| 3 | ClinePass | Cline login | Flat-subscription gateway |
| 3 | Google Gemini | âœ” | Free tier available |
| 3 | Groq | âœ” | Free tier available |
| 3 | Z.ai / OpenRouter | âœ” | |
| 3 | DeepSeek / OpenAI / Anthropic | âœ” | |

- The **model picker** lists whatever your providers actually have installed/available.
- **Vision is automatic**: screenshots stream to the model when it supports images
  (gemma3, gpt-4o, Gemini, Claudeâ€¦) and are skipped for text-only models.
- HQ and the terminal **mirror the sidebar's provider** â€” switch once, everywhere follows.

---

## ðŸ§© Architecture

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ LuckyD Browser (PySide6 / Qt WebEngine) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                                                                                                â”‚
â”‚   Tabs (Chromium)      AI Sidebar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”      Dashboard / HQ shell / Terminal tab         â”‚
â”‚        â”‚                                  â”‚              (served by Control API)               â”‚
â”‚        â”‚ CDP                              â”‚                                                    â”‚
â”‚        â–¼                                  â–¼                                                    â”‚
â”‚   Browser Control API (127.0.0.1:9777)   AI Bridge â”€â”€â–º Ollama (local, keyless)                 â”‚
â”‚   /status /tabs /snapshot /act â€¦         multi-provider â”€â”€â–º Cline / Gemini / Groq / DeepSeek â€¦ â”‚
â”‚        â–²                                  â”‚                                                    â”‚
â”‚        â”‚ drives live tabs                 â”‚ mirrors provider                                   â”‚
â”‚   luckyd-code backend (port 8000) â—„â”€â”€â”€â”€â”€â”€â”€â”˜      In-browser terminal (ConPTY â†” WebSocket,      â”‚
â”‚   70+ tools Â· memory Â· orchestration             port 9881 â€” full luckyd-code CLI)             â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Browser Control API

Localhost HTTP control of the real browser â€” used by the HQ agent, the terminal agent, and your
own scripts. **Binds to `127.0.0.1` only**; optional `Authorization: Bearer` token; toggle in
**Tools â†’ Browser Control API**.

```
GET  /status /tabs /screenshot /help     POST /navigate /tab/new /act /snapshot /eval /ask
```

Element indices in `/snapshot` and `/act` are the same ones the sidebar agent uses â€” anything
that can read a snapshot can drive the page.

---

## ðŸ›  Build from source

Requirements: Windows 10/11 x64, Python 3.10â€“3.12, and (only for the installer) Inno Setup 6.

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

The installer script (`browser\installer\LuckyDBrowser.iss`) and the AI bootstrap
(`browser\installer\ollama_setup.ps1`) are plain text â€” tweak away.

---

## âš™ Configuration

- **Settings UI**: search engine, homepage, ad blocker, download folder, zoom, themes
  (4 + a secret Synthwave one), startup/session restore, harness auto-start, Control
  API port/token.
- **Data folder**: `%LOCALAPPDATA%\LuckyDBrowser` (settings, history/bookmarks DB, userscripts).
- **Provider keys** (optional): set `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, â€¦
  in a `.env` next to the app, or just use the sidebar picker. No keys = local Ollama.
- **Local model**: the bootstrap installs `llama3.2:3b` (fastest tool-capable model for CPU).
  Pull others any time, e.g. `ollama pull gemma3:4b` (adds vision) or `ollama pull qwen3:8b`
  (better quality on GPU machines) â€” the picker finds them automatically.

---

## ðŸ”’ Privacy & security

- **Local-first AI**: with Ollama, prompts and page content never leave your machine.
- **No accounts, no telemetry accounts, no sign-in** to use the assistant.
- All control surfaces (Control API `9777`, HQ `8000`, terminal `9881`, CDP `9222`)
  bind to **loopback only**.
- Browsing data stays in `%LOCALAPPDATA%\LuckyDBrowser`; incognito writes nothing to disk.
- The shipped bundle contains **no API keys** â€” cloud providers activate only with *your* keys.

## ðŸ§¹ Uninstall

**Settings > Apps > LuckyD Browser** (or `uninstall.ps1` in the install folder).
Browsing data is kept in `%LOCALAPPDATA%\LuckyDBrowser` â€” delete it manually for a full wipe.
Ollama is a separate product and is left installed (remove via Settings > Apps > Ollama).

---

## ðŸ§± Tech stack

**PySide6 / Qt WebEngine** (Chromium) Â· Python 3.10+ Â· httpx (multi-provider AI bridge) Â·
Chrome DevTools Protocol Â· pywinpty + WebSockets + xterm.js (terminal) Â· PyInstaller Â· Inno Setup Â·
Ollama (local models)

## ðŸ“„ License

MIT Â© 2026 [DylanChess03](https://github.com/Dylanchess0320) â€” see [LICENSE](LICENSE).

---

*If LuckyD Browser saves you an API bill, a â­ on the repo is appreciated.*

