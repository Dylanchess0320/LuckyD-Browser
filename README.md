<div align="center">

<img src="docs/screenshots/hero.svg" alt="LuckyD Browser" width="100%">

# LuckyD Browser

**The AI browser that runs *your* models — local, unlimited, offline — plus a full coding agent and real terminals in one window.**

[![CI](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml/badge.svg)](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=7c5cff&label=release)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Dylanchess0320/LuckyD-Browser/total?color=34d399)](https://github.com/Dylanchess0320/LuckyD-Browser/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%2F11%20x64-0078D4?logo=windows&logoColor=white)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10--3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

**[⬇ Download Latest Release](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)**
&nbsp;·&nbsp;
**[Browser guide](README-LuckyD-Browser.md)**
&nbsp;·&nbsp;
**[Changelog](CHANGELOG.md)**
&nbsp;·&nbsp;
**[YouTube](https://www.youtube.com/@LuckyDYoutube)**

</div>

<p align="center">
  <img src="docs/screenshots/sidebar.png" alt="LuckyD dashboard, Agent Mesh tabs, and local AI sidebar" width="920">
</p>

---

## Why LuckyD

Most “AI browsers” rent you a chatbot behind an account. LuckyD is a **real Chromium daily driver** with a **local assistant**, a **coding-agent HQ**, and **ConPTY terminals** living in your tabs.

| | LuckyD | Comet / Dia | Edge Copilot | Chrome Gemini |
|---|:---:|:---:|:---:|:---:|
| Free AI with **no account / no key** | ✅ local Ollama | ❌ | ❌ | ❌ |
| Works **fully offline** | ✅ | ❌ | ❌ | ❌ |
| Agent **drives your live tabs** | ✅ | ✅ | limited | limited |
| **Coding agent + terminals in tabs** | ✅ | ❌ | ❌ | ❌ |
| **Workspaces · HTTPS-Only · Memory Saver** | ✅ | mixed | mixed | mixed |
| Open source MIT · no admin to install | ✅ | ❌ | ❌ | ❌ |

> The others rent you their AI. **LuckyD runs yours.**

---

## Latest release

**v8.0.0 — The Cleanup, phase one**: skills in the sidebar, smart model routing, and a Neon Night home — on top of the agentic stack, shipped with an official installer:

| | |
|---|---|
| **⬇ Windows installer** | [`LuckyDBrowserSetup-8.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases) — built on a real Windows runner with Inno Setup 6. Per-user, no admin, silent-install flags. |
| **🧩 Skills in sidebar** | The 5 bundled skills (`ai-news-brief`, `chess`, `graphify`, `movie-picker`, `top-picks`) surface as ✨ chips above the AI sidebar input as you type; one tap attaches the skill as chat context. |
| **🧠 Smart model routing** | Auto mode picks the provider per question — explicit picks always win, non-viable picks fall back gracefully. |
| **🌙 Neon Night home** | `/dashboard` and the new-tab fallback share one refined typographic system: tabular clock, small-caps labels, system-only font stack, offline-safe, `prefers-reduced-motion` support. |
| **🏷 Honest provider labels** | Sidebar provider chips say `· free tier` / `· credit-billed ⚠` — no more guessing what a request costs. |
| **💸 $0 OpenCode Zen fallback** | With no Ollama and no keys, chat falls back to the free Zen gateway instead of an empty-token provider. |
| **🔔 Quiet update badge** | A silent check runs at startup — when a new release exists, a small toolbar badge lights up. No modal, no interruption. |

> **9.0 in development** — `main` now carries an overnight hardening pass: **1,855 tests passing** (1,389 added overnight), coverage **52% → 81%** with the 60% CI gate enforced, **24 production bugs fixed**, and a UX overhaul (Ctrl+K palette: Tab focus trap, Enter activates the top result, fuzzy ranking, Home/End nav; keyboard-operable dashboard with ARIA; AI sidebar honest provider/error states; clearer error copy), plus Windows CI fixes. No 9.0 installer yet — grab v8.0.0 above, or run from source.

<details>
<summary>New in 8.0.0 — The Cleanup, phase one</summary>

Skills in the sidebar, smart routing, and a Neon Night home.

| | |
|---|---|
| **Contextual skill chips** | The 5 bundled skills (`ai-news-brief`, `chess`, `graphify`, `movie-picker`, `top-picks`) now surface as `✨` chips above the AI sidebar input as you type; one tap attaches the skill as chat context. |
| **Smart model routing** | `ModelRouter` is wired into `AIBridge.chat()` auto mode: it picks the provider per question (explicit picks always win; non-viable picks fall back to the existing chain). |
| **Neon Night home** | The real `/dashboard` and the `newtab.html` fallback share one refined typographic system: tabular clock, small-caps labels, system-only font stack, offline-safe, `prefers-reduced-motion` support. |
| **$0 OpenCode Zen fallback** | With no Ollama and no keys, chat falls back to the free Zen gateway instead of an empty-token provider. |
| **Honest provider labels** | Sidebar provider chips say `· free tier` / `· credit-billed ⚠`. |
| **Antigravity decluttered** | Redundant Tools-menu and command-palette launchers removed; it's already in the terminal. |
| **Copy freshness** | README, docs, `.github`, and in-app text brought current; About dialog and window titles now say "LuckyD". |

*Verified: 465 tests passed, `ruff` clean.*

</details>

<details>
<summary>New in 7.0.0 — Lucky polish</summary>

Lucky polish — slim chrome, corrected colors, soul — on top of everything in 6.0.0.

</details>

<details>
<summary>New in 6.0.0 — Trust foundation</summary>

Agentic with receipts: permission scopes, risk levels, secret redaction, an append-only audit log, and an approval queue at `/trust` — plus WebMCP, scheduled/background agents, and the open Skills marketplace.

| | |
|---|---|
| **🛡 Trust dashboard** | "Agentic with receipts": permission scopes, risk levels, secret redaction, an append-only audit log, and an approval queue — review everything the agents did at `/trust`. |
| **🔒 Trust hardening** | Legacy `auto_approve_all` bypasses removed (now inert with a warning); explicit audited `auto_approve_low_risk` per-run mode; cross-process run locks so scheduled and interactive runs can't collide; mutating schedule-dashboard routes gated by approval; zero-valued schedule fields (`max_retries=0`) honored instead of dropped. |
| **🔌 WebMCP** | Websites can expose typed agent tools to LuckyD (JS shim + native support), permission-gated so sites can't reach beyond their grant. Server-side schema validation, 30s dispatch timeouts, and origin binding via rotating discovery tokens — a page can't call tools on another origin's behalf, and bindings are keyed per tab. |
| **⏰ Scheduled agents** | Background agents on cron schedules with a **morning digest** (`/schedules`) — it works while you rest. |
| **🧩 Skills marketplace** | Open skill registries with hash-verified install, update, remove, and publish for agent skills. |
| **🛡 Agent circuit breaker** | The agent loop stops after 4 consecutive all-tool-failure turns with an actionable message instead of burning turns and budget. |

*Verified: 428 tests passed, `ruff` clean.*

</details>

<details>
<summary>New in 5.0.0 — Final</summary>

The maxed-out release: everything from 4.0, shipped like a finished product.

| | |
|---|---|
| **Official Windows installer** | `LuckyDBrowserSetup-5.0.0.exe` — built on a real Windows runner with Inno Setup 6. Per-user, no admin, silent-install flags. |
| **Version unified** | 5.0.0 everywhere — package, installers, user-agent — guarded by a regression test so it can't drift. |
| **Fully formatted & linted** | Entire tree `ruff`-clean, CI green. |

*Verified: 365 tests passed, `ruff` clean.*

</details>

<details>
<summary>New in 4.0.0 — Frontier</summary>

A hardened foundation: every local service now authenticates with HttpOnly session cookies — no tokens in URLs or page source, ever.

| | |
|---|---|
| **Cookie-based auth** | Control API, Coding Agent HQ, and terminal WebSocket use `luckyd_ctl` / `luckyd_hq` / `luckyd_term` session cookies. Zero credentials embedded in served HTML. |
| **Deep Research XSS hardening** | Report markdown is HTML-escaped before rendering; links allowlisted to `http:`, `https:`, `mailto:` with `rel="noopener"`. |
| **Cline bridge auth** | `/v1/models` and `/v1/chat/completions` require a bearer token (`CLINE_BRIDGE_TOKEN`); fails closed when unconfigured. |
| **SSRF protection** | Agent web tools validate DNS against private/loopback/metadata IPs, re-check redirects, pin connections to validated IPs (anti DNS-rebinding), ignore env proxies. |
| **Single command blocklist** | One consolidated blocklist gates every shell in the system — agent Bash tool and background processes alike. |
| **Reliability fixes** | Checkpoint undo can't loop forever on deleted files; IPv6 loopback (`[::1]`) handled; Windows updater exits cleanly off-Windows; race-safe terminal server; session saves survive concurrent windows; tile processes reaped on shutdown. |

*Verified: 189 tests passed, `ruff` clean.*

</details>

<details>
<summary>New in 3.9.0 — Daily Driver</summary>

A browser you can actually live in, not just demo.

| | |
|---|---|
| **HTTPS-Only Mode** | Public `http://` navigations upgrade to `https://`. Localhost and private LAN addresses are never rewritten. |
| **Site permissions** | Camera, mic, location, notifications, pointer lock, and screen capture prompt **once** and remember Allow/Block. Click the lock icon. Incognito never persists. |
| **Workspaces** | Named tab collections — File → Workspaces, or the command palette (`Ctrl+K`). Switching saves the current window first. |
| **Memory Saver** | Idle background tabs freeze at 5 minutes and discard at 15. Pinned, audible, and local HQ/terminal tabs stay awake. Sleeping tabs show 💤. |
| **Read Aloud** | `Ctrl+Shift+L` or toolbar 🔊 — speaks the selection or the page via Windows SAPI. Click again to stop. |
| **Summarize this page** | `Ctrl+Shift+U` or toolbar ✨ — opens the AI sidebar and summarizes the current tab. |

Also in this lineage: Deep Research swarm (`Ctrl+Shift+R`), Agent Mesh, self-healing workflows, and one-click in-app updates.
</details>

---

## The one-window platform

| Surface | What it is | Open it |
|---|---|---|
| **AI Sidebar** | Chat-first: Markdown chat, contextual skill chips, honest cost labels per model (`· free tier`, `· credit-billed ⚠`), visual Q&A, autonomous agent on your **real visible tab** | `Ctrl+Shift+A` |
| **Coding Agent HQ** | Full `luckyd-code` workspace in a tab — 70+ tools, memory, sessions | `Ctrl+Shift+H` |
| **Terminals** | Real Windows ConPTY via xterm.js — agent CLI, PowerShell, CMD, Agent Mesh | `` Ctrl+` `` |
| **Dashboard** | Live new-tab hub: status pills, Ask LuckyD, one-tap tiles, speed dial | New tab |
| **Workflows** | Record Control-API actions, replay with self-healing element matching | Tools → Workflows |

**Harness mode is on by default:** sidebar tasks run on the coding-agent backend, which can drive the tabs you are looking at.

---

## Install in 10 seconds

1. Get **[LuckyDBrowserSetup-8.0.0.exe](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)**
2. Run it — per-user, **no admin**, installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
3. Leave **“Set up free unlimited local AI”** checked → Ollama + `llama3.2:3b` (~2 GB, one time)
4. `Ctrl+Shift+A` → chat offline. Or bring your own keys (Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, Cline, OpenCode)

No local model yet? The **$0 OpenCode Zen gateway** is the built-in fallback — no local server, no key, the sidebar just works.

Silent: `LuckyDBrowserSetup-8.0.0.exe /VERYSILENT /NORESTART`

Windows 10/11 x64 only.

---

## Keyboard

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+A` | AI Sidebar |
| `Ctrl+Shift+H` | Coding Agent HQ |
| `Ctrl+Shift+U` | Summarize this page |
| `Ctrl+Shift+L` | Read Aloud |
| `Ctrl+Shift+R` | Deep Research |
| `` Ctrl+` `` / `Ctrl+Shift+`` ` | Terminal (Agent / PowerShell) |
| `Ctrl+Alt+M` | Agent Mesh (4 panes) |
| `Ctrl+K` | Command palette |
| `Ctrl+Alt+R` / `Ctrl+Shift+F` | Reader / Focus |
| `Ctrl+,` | Settings |

---

## Privacy

- Local-first Ollama — prompts never leave the machine unless you pick a cloud provider
- Control API and terminal bridge bind **127.0.0.1 only**, with per-profile tokens
- No telemetry, no bundled API keys, incognito writes nothing to disk

---

## Build from source

```powershell
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
pip install -r browser\requirements.txt
browser\run_browser.bat

# Shareable installer (needs Inno Setup 6)
powershell -NoProfile -ExecutionPolicy Bypass -File browser\installer\build_installer.ps1
# → browser\installer\output\LuckyDBrowserSetup-8.0.0.exe
```

---

## Structure

```
browser/   PySide6 / Qt WebEngine · Control API :9777 · Terminal :9881
core/      agent loop, LLM client, checkpoints
tools/     70+ tools including orchestration and Deep Research
tests/     headless pytest (Qt mocked on Linux CI)
```

---

<p align="center">
  <b>MIT © LuckyD</b> · Chromium · Ollama · Qt · xterm.js · pywinpty · PyInstaller · Inno Setup<br>
  <a href="https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest">Download Latest Release</a>
  · <a href="https://www.youtube.com/watch?v=La6bxaa7icY">Watch the showcase</a>
</p>
