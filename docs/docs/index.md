<div class="luckyd-hero" markdown>

# LuckyD Browser

<p class="tagline">The AI browser that runs <em>your</em> models — local, unlimited, offline — plus a full coding agent and real terminals in one window.</p>

<div class="cta-row" markdown>
[⬇ Download v6.0.0](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v6.0.0/LuckyDBrowserSetup-6.0.0.exe){ .luckyd-btn }
[Getting started](getting-started.md){ .luckyd-btn-ghost }
[What's new in 6.0](release-notes.md){ .luckyd-btn-ghost }
</div>

<div class="luckyd-badges" markdown>
![CI](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml/badge.svg)
![Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=7c5cff&label=release)
![Downloads](https://img.shields.io/github/downloads/Dylanchess0320/LuckyD-Browser/total?color=34d399)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)
![Windows](https://img.shields.io/badge/Windows-10%2F11%20x64-0078D4?logo=windows&logoColor=white)
</div>

</div>

## One window, everything

<div class="grid cards" markdown>

-   :material-robot:{ .lg .middle } __AI Sidebar__ <span class="card-kbd">Ctrl+Shift+A</span>

    ---

    Markdown chat, model picker, visual Q&A, and an autonomous agent that drives your **real visible tab**.

-   :material-code-braces:{ .lg .middle } __Coding Agent HQ__ <span class="card-kbd">Ctrl+Shift+H</span>

    ---

    A full coding-agent workspace in a tab — 70+ tools, memory, sessions.

-   :material-console:{ .lg .middle } __Real Terminals__ <span class="card-kbd">Ctrl+`</span>

    ---

    Genuine Windows ConPTY via xterm.js — agent CLI, PowerShell, CMD, Agent Mesh.

-   :material-view-dashboard:{ .lg .middle } __Dashboard__ <span class="card-kbd">New tab</span>

    ---

    Live new-tab hub: status pills, Ask LuckyD, one-tap tiles, local speed dial.

-   :material-magnify-expand:{ .lg .middle } __Deep Research__ <span class="card-kbd">Ctrl+Shift+R</span>

    ---

    A planner → parallel workers → critic → verifier swarm that returns citation-backed reports.

-   :material-view-grid-plus:{ .lg .middle } __Agent Mesh__ <span class="card-kbd">Ctrl+Alt+M</span>

    ---

    Four terminal panes, one dock — run agents side by side.

-   :material-workflow:{ .lg .middle } __Workflows__

    ---

    Record Control-API actions, replay them with self-healing element matching. Schedule them on autopilot.

-   :material-shield-lock:{ .lg .middle } __Private by design__

    ---

    Local-first Ollama, loopback-only services, HTTPS-Only mode, per-site permissions. No telemetry.

</div>

## Install in 10 seconds

1. Get [**LuckyDBrowserSetup-6.0.0.exe**](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v6.0.0/LuckyDBrowserSetup-6.0.0.exe)
2. Run it — per-user, **no admin**, installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
3. Leave **"Set up free unlimited local AI"** checked → Ollama + `llama3.2:3b` (~2 GB, one time)
4. `Ctrl+Shift+A` → chat offline. Or bring your own keys (Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, Cline, OpenCode)

Windows 10/11 x64 only. Silent install: `LuckyDBrowserSetup-6.0.0.exe /VERYSILENT /NORESTART`

## Why LuckyD

Most "AI browsers" rent you a chatbot behind an account. LuckyD is a **real Chromium daily driver** with a **local assistant**, a **coding-agent HQ**, and **ConPTY terminals** living in your tabs.

|  | LuckyD 6.0 | Comet / Dia | Edge Copilot | Chrome Gemini |
|---|:---:|:---:|:---:|:---:|
| Free AI with **no account / no key** | ✅ local Ollama | ❌ | ❌ | ❌ |
| Works **fully offline** | ✅ | ❌ | ❌ | ❌ |
| Agent **drives your live tabs** | ✅ | ✅ | limited | limited |
| **Coding agent + terminals in tabs** | ✅ | ❌ | ❌ | ❌ |
| **Workspaces · HTTPS-Only · Memory Saver** | ✅ | mixed | mixed | mixed |
| Open source MIT · no admin to install | ✅ | ❌ | ❌ | ❌ |

> The others rent you their AI. **LuckyD runs yours.**

## New in 6.0.0 — Agentic

The agentic frontier, shipped: an AI browser you can trust with real agency. [Read the release notes →](release-notes.md)

- **🛡 Trust dashboard** — `/trust`: permission scopes, risk levels, an approval queue, secret redaction, and an append-only audit log ("agentic with receipts")
- **🔒 Trust hardening** — legacy `auto_approve_all` bypasses removed (inert with a warning); schedule-dashboard routes gated by approval; a shared run lock so scheduled and interactive runs can never collide
- **🔌 WebMCP** — websites can expose typed agent tools to LuckyD; server-side schema validation, 30-second timeouts, and origin-bound rotating binding tokens keep hostile pages out
- **⏰ Scheduled agents** — background agents on cron schedules with a **morning digest** (`/schedules`)
- **🧩 Skills marketplace** — open skill registries with hash-verified install, update, remove, and publish
- **Official Windows installer** — `LuckyDBrowserSetup-6.0.0.exe`, built on a real Windows runner with Inno Setup 6; per-user, no admin, silent-install flags

*Verified: 428 tests passed, `ruff` clean.*

<details>
<summary>Previously: 5.0.0 — Final</summary>

The maxed-out release: the official Windows installer, version unified in 5.0, and a fully Ruff-formatted codebase — guarded by a regression test.

</details>

<details>
<summary>New in 4.0.0 — Frontier</summary>

A hardened foundation: every local service now authenticates with HttpOnly session cookies — no tokens in URLs or page source, ever.

- **Cookie-based auth** for the Control API, Coding Agent HQ, and terminal WebSocket
- **Deep Research XSS hardening** — escaped markdown, allowlisted link schemes
- **Cline bridge auth** — bearer token required, fails closed
- **Reliability fixes** — checkpoint undo can't loop forever, IPv6 loopback handled, updater exits cleanly off-Windows

</details>

---

<p align="center">
  <b>MIT © LuckyD</b> · Chromium · Ollama · Qt · xterm.js · pywinpty · PyInstaller · Inno Setup<br>
  <a href="https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v6.0.0/LuckyDBrowserSetup-6.0.0.exe">Download v6.0.0</a>
  · <a href="https://www.youtube.com/@LuckyDYoutube">YouTube</a>
</p>
