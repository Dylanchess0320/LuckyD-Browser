<div class="luckyd-hero" markdown>

# LuckyD Browser

<p class="tagline">The AI browser that runs <em>your</em> models — local, unlimited, offline — plus a full coding agent and real terminals in one window.</p>

<div class="cta-row" markdown>
[⬇ Download v10.4.0](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.4.0/LuckyDBrowserSetup-10.4.0.exe){ .luckyd-btn }
[Getting started](getting-started.md){ .luckyd-btn-ghost }
[What's new](release-notes.md){ .luckyd-btn-ghost }
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

1. Get [**LuckyDBrowserSetup-10.4.0.exe**](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.4.0/LuckyDBrowserSetup-10.4.0.exe)
2. Run it — per-user, **no admin**, installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
3. Optional: run `ollama_setup.ps1` from the install folder → Ollama + `llama3.2:3b` (~2 GB, one time)
4. `Ctrl+Shift+A` → chat offline. Or bring your own keys (Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, Cline, OpenCode)

Windows 10/11 x64 only. Silent install: `LuckyDBrowserSetup-10.4.0.exe /VERYSILENT /NORESTART`

## Why LuckyD

Most "AI browsers" rent you a chatbot behind an account. LuckyD is a **real Chromium daily driver** with a **local assistant**, a **coding-agent HQ**, and **ConPTY terminals** living in your tabs.

|  | LuckyD 9.0 | Comet / Dia | Edge Copilot | Chrome Gemini |
|---|:---:|:---:|:---:|:---:|
| Free AI with **no account / no key** | ✅ local Ollama | ❌ | ❌ | ❌ |
| Works **fully offline** | ✅ | ❌ | ❌ | ❌ |
| Agent **drives your live tabs** | ✅ | ✅ | limited | limited |
| **Coding agent + terminals in tabs** | ✅ | ❌ | ❌ | ❌ |
| **Workspaces · HTTPS-Only · Memory Saver** | ✅ | mixed | mixed | mixed |
| Open source MIT · no admin to install | ✅ | ❌ | ❌ | ❌ |

> The others rent you their AI. **LuckyD runs yours.**

## New in 10.4.0 — Provider-credit honesty

A Cline HTTP 402 (insufficient credits) records a 24-hour marker in `~/.luckyd/cline_credit_state.json`: provider auto-selection skips Cline while valid, the provider list shows an exhausted indicator, and `lucky-code providers --clear-credit-state` clears it after topping up. Agent 1 and Agent 2 both run LuckyD Code v10.4.0.

## New in 10.2.0 — Performance + hardening edition

Agent hot-path tools (grep, file read/edit, CSV, LSP rename, secrets, file watching, memory compression) no longer block the event loop; `ProcessTool` is hardened against command injection and runs Windows builtins correctly; POSIX/desktop-optional tests skip cleanly so the suite is green everywhere; and Agent 1 and Agent 2 both run LuckyD Code v10.2.0.

## New in 10.1.0 — Google AI Pro edition

OpenCode is back in the Agent Mesh (keyed via `OPENCODE_API_KEY`, alongside MiniMax and Cline), Gemini models are refreshed (2.5 Pro/Flash, 3 previews), Nano Banana image generation and Veo 3.1 video generation are wired through the Gemini API, and one command backs up your files to the 2TB Google Drive.

## New in 9.9.0 — Anti-block hardening + portable ZIP

The installer no longer uses `-ExecutionPolicy Bypass` (the biggest behavioral red flag for endpoint tools like Halcyon anti-ransomware). Every release now also ships a portable ZIP (`LuckyDBrowser-Portable-9.9.0.zip`) — unzip and run, no installer, for locked-down work PCs. Code signing is wired into the build pipeline (binaries + installer sign automatically when a certificate is configured; unsigned until then — free for open source via SignPath Foundation). Also fixed: the 3 mypy errors that kept 9.8's CI red, and a real agent crash when the MiniMax CLI tools were imported.

## New in 9.8.0 — Zen retired, Cline default + goals, plugins, ACP

OpenCode Zen is retired (keyless `$0` tier dead) — Cline is now the free default when logged in (`cline auth`). Plus goals with token budgets (`/goal`), mid-run steering (`/steer`) and follow-up queue (`/btw`), plugin management (`lucky-code plugin`), user-defined providers (`lucky-code custom-provider`), ACP stdio server (`lucky-code --acp`), MiniMax Code (`mcode`) + media (`mmx`) in the Agent Mesh and as agent tools, deep research on the assistant's connected providers via the bridge, and Google-CAPTCHA-proof browsing (DDG default, adblock allowlist, desktop UA hardening).

## New in 9.7.0 — Flagship Code CLI + MiniMax provider

The LuckyD Code CLI is now the flagship terminal agent: permission modes (`--permission-mode`), background subagent delegation (`delegate_task`), slash commands from Markdown files (`/compact`, `/review`, `/init`), automatic context compaction, classified retry with backoff, and session autosave. MiniMax is a first-class provider (`--provider minimax`), and Agent 1 and Agent 2 in the mesh both run LuckyD Code v9.7.

## New in 9.6.0 — Honest research + opt-in contributor tier

Deep Research shows its resolved engine (provider + model + backend), keyed backends fail fast with `no API key configured`, evidence loads from `evidence.json`, and cancel never overlaps the next run. `muse-spark-1.3` vs `-contributor` stays hidden unless `/contributor on`, with honest cached-token costs.

## New in 9.5.0 — Terminal crash fix

A Bash tool call with a missing/null `command` used to raise `AttributeError` out of the tool and kill the agent's whole terminal session. The shell tools now validate their input and return an error result instead — a bad tool call can never crash the agent again — and the timeout kill path is covered by regression tests.

## New in 9.4.0 — Antigravity replaces Gemini

`agy` / `mesh-agy` covers the agent-mesh plan/build stages (PATH plus `%LOCALAPPDATA%\agy\bin` fallback), `gemini` / `mesh-gemini` is removed, and Cline CLI stays first-class — shipped with the 9.4.0 installer built from this source.

- **🛸 Antigravity mesh agent** — plan/build stages via `agy`, no more dead Gemini panes
- **🧹 Gemini removed** — shells, dock chips, and fallback probing are gone
- **🖥 Cline stays first-class** — `cline` alongside `mesh-cline` in both agent terminals

## New in 9.3.0 — AI provider list + reliability

`lucky-code providers` (or `/providers`, or `GET /api/providers`) shows all 13 AI providers with live status and cost tier, the model resolver now survives offline or revoked keys via its stale cache, cost tracking is honest (deepseek-v4 at real rates, free-tier models at $0), and the dashboard no longer duplicates the Deep Research tile — shipped with the 9.3.0 installer built from this source.

- **📋 Provider list** — every provider, its default model, cost tier (free/paid), and whether it's usable right now
- **🛡️ Offline-proof resolver** — failed catalog fetches fall back to last-known-good models
- **💲 Honest costs** — real deepseek-v4 rates, $0 for all free-tier models
- **🟡 Cline CLI** — still first-class in both agent terminals, every free model in `/model`

## New in 9.0.0 — Reliability release

Proxy-proof local AI, honest AI status, keyed OpenCode Zen, working DeepSeek Harness boot, focused Ctrl+K, readable shortcuts, and correct video color — shipped with the 9.0.0 installer built from this source.

- **🔌 Proxy-proof local AI** — Ollama/LM Studio stay reachable with a proxy/VPN set; localhost bypasses the proxy
- **🤖 Honest AI status** — the dashboard pill says `Ollama not running` / `no models pulled` instead of a dead `not set up`
- **🔑 OpenCode Zen keyed** — Zen's old keyless $0 tier is gone; it registers with `OPENCODE_API_KEY` and serves its current platform catalog
- **🖥 DeepSeek Harness boots** — Agent Mesh spawns `dsh web`
- **⌨ Ctrl+K overhaul** — Tab focus trap, Enter activates the top result, fuzzy ranking, Home/End navigation, plus keyboard-operable dashboard with ARIA
- **Official Windows installer** — built on a real Windows runner with Inno Setup 6; per-user, no admin, silent-install flags

*Verified: 1,855 tests passed, coverage 81%, `ruff` clean.*

## New in 8.0.0 — The Cleanup, phase one

Contextual skills move into the AI sidebar, the model router gets smart, and the home page gets the Neon Night treatment. Ships with the 8.0.0 installer built from this source.

- **✨ Contextual skill chips** — chess, movie-picker, news brief, graphify, and top-picks surface as chips above the sidebar input as you type; one tap attaches the skill as chat context
- **🧠 Smart model routing** — the `core/router.py` ModelRouter now picks the provider per question in auto mode (explicit picks always win; non-viable picks fall back safely)
- **🌃 Neon Night home** — the real `/dashboard` and the `newtab` fallback share one refined typographic system: tabular clock, small-caps labels, system fonts, fully offline-safe
- **Honest AI labels + $0 fallback** — provider chips say `· free tier` / `· credit-billed ⚠`; with no Ollama and no keys, chat falls back to the free OpenCode Zen gateway
- **Antigravity decluttered** — redundant Tools-menu/palette launchers removed (it's already in the terminal); full copy-freshness pass across README, docs, and in-app text

## New in 7.0.0 — Agentic

The agentic frontier, shipped: an AI browser you can trust with real agency. [Read the release notes →](release-notes.md)

- **🛡 Trust dashboard** — `/trust`: permission scopes, risk levels, an approval queue, secret redaction, and an append-only audit log ("agentic with receipts")
- **🔒 Trust hardening** — legacy `auto_approve_all` bypasses removed (inert with a warning); schedule-dashboard routes gated by approval; a shared run lock so scheduled and interactive runs can never collide
- **🔌 WebMCP** — websites can expose typed agent tools to LuckyD; server-side schema validation, 30-second timeouts, and origin-bound rotating binding tokens keep hostile pages out
- **⏰ Scheduled agents** — background agents on cron schedules with a **morning digest** (`/schedules`)
- **🧩 Skills marketplace** — open skill registries with hash-verified install, update, remove, and publish
- **Official Windows installer** — built on a real Windows runner with Inno Setup 6; per-user, no admin, silent-install flags

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
  <a href="https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.4.0/LuckyDBrowserSetup-10.4.0.exe">Download v10.4.0</a>
  · <a href="https://www.youtube.com/@LuckyDYoutube">YouTube</a>
</p>
