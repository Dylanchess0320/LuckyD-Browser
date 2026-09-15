# LuckyD Browser v9.2.0 — Release Notes

> **The AI browser that doesn't need an API key.** Free, unlimited, offline AI built in —
> plus a full coding agent and developer terminal living in your tabs.

**[⬇ Download `LuckyDBrowserSetup-9.2.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.2.0/LuckyDBrowserSetup-9.2.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

---

## What's new in 9.2.0 — AI provider list

- **AI provider list** — `lucky-code providers` in the terminal, `/providers` in the
  REPL, and `GET /api/providers` on the Harness HQ server. All 13 providers with
  live status (ready / needs key), cost tier (free / paid), and the active provider
  marked. No more guessing which keys are set.
- **Offline-proof model resolver** — the DeepSeek model cache now falls back to its
  last-known-good models when the catalog fetch fails (no key, offline, revoked key)
  instead of silently dropping to hardcoded defaults.
- **Honest cost tracking** — `deepseek-v4-flash`/`pro` now bill at real rates and every
  free-tier model (Nemotron, Grok, Cline gateways, Gemma preview) correctly reports $0.
- **Agent Mesh 1.0.1** — Gemini CLI retired from the mesh (it migrated to Antigravity);
  use the `agy` Antigravity agent for plan/build stages.

### Verify

| File | Size | SHA-256 |
|---|---|---|
| `LuckyDBrowserSetup-9.2.0.exe` | 332,455,396 bytes | `70D3783DF10A9590B9C193F97DB8FBBA450D8797E468995E8BB8C1DC92294166` |

On top of everything from 9.1.0 below.

---

# LuckyD Browser v9.1.0 — Release Notes (archive)

## What's new in 9.1.0 — Agent terminals upgrade

- **Gemini CLI first-class** — `gemini` / `mesh-gemini` shells in both Agent 1 (v3.6) and Agent 2 (v2.2) terminals.
- **Cline CLI first-class** — `cline` shell alongside `mesh-cline`.
- **Every free model in /model** — Gemini free tier, Cline free tier (12 models), new ClinePass models (glm-5.3, glm-5.2, qwen3.8-max).

On top of everything from 9.0.0 below.

---

# LuckyD Browser v9.0.0 — Release Notes (archive)

> **The AI browser that doesn't need an API key.** Free, unlimited, offline AI built in —
> plus a full coding agent and developer terminal living in your tabs.

**[⬇ Download `LuckyDBrowserSetup-9.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.0.0/LuckyDBrowserSetup-9.0.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

---

## The pitch in 10 seconds

1. Run the installer.
2. Leave **"Set up free unlimited local AI"** checked.
3. Open the sidebar and chat — **no account, no key, no cost, ever.**

The installer sets up [Ollama](https://ollama.com) and a fast local model (`llama3.2:3b`) for you.
Your prompts never leave your machine. Prefer the cloud? Bring your own keys for
Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, Cline, or OpenCode (via `OPENCODE_API_KEY`).

## What's new in 9.0.0 — Reliability release

- **Proxy-proof local AI** — Ollama/LM Studio are found even with a proxy/VPN set (localhost bypasses the proxy on both the dashboard check and the chat path).
- **Honest AI status** — the dashboard pill says exactly what's wrong: `Ollama not running`, `no models pulled`, or the working providers.
- **OpenCode Zen keyed** — Zen's old keyless $0 tier is gone; it registers with `OPENCODE_API_KEY` and serves its current platform catalog.
- **DeepSeek Harness boots** — Agent Mesh spawns `dsh web` (the old `--profile web --no-open` invocation is no longer valid).
- **Ctrl+K palette fixed** — the command palette takes keyboard focus again (Tab trap, fuzzy ranking, Home/End nav).
- **Readable home page** — shortcut labels get brighter type with text shadow.
- **Correct video color** — `--force-color-profile=srgb` on the software video path.
- **1,855 tests passing**, coverage **52% → 81%**, **24 production bugs fixed**.

## What's inside

🤖 **AI Sidebar** (`Ctrl+Shift+A`) — Markdown chat, per-provider model picker, page-aware Q&A,
📷 visual Q&A, and an autonomous agent that drives your **real, visible tab** while you watch.

⚡ **Coding Agent HQ** (`Ctrl+Shift+H`) — a full coding-agent workspace in a browser tab:
70+ tools, memory graph, sessions, background tasks. Auto-starts with the browser and
**mirrors the sidebar's AI provider**.

💻 **In-browser Terminal** — the complete `luckyd-code` CLI on a real Windows ConPTY
(xterm.js), one click from the dashboard.

🌐 **A real daily-driver browser** — tabs, bookmarks (import/export), history, downloads,
incognito, ad/tracker blocker, find-in-page, themes, command palette, print/save,
AI right-click actions (Explain / Summarize / Translate), Copy-as-Markdown.

🔒 **Private by default** — local-first AI, loopback-only control APIs, no telemetry,
no keys shipped in the bundle.

## Install notes

- Installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser` with Start Menu + optional desktop shortcut
  and a Settings > Apps uninstall entry.
- The AI bootstrap runs after setup in a small console window (one-time ~2 GB model download).
  Uncheck it if you only want cloud providers — you can run it later from the install folder.
- Silent install: `LuckyDBrowserSetup-9.0.0.exe /VERYSILENT /NORESTART`

## Upgrade tips

- **Better answers on GPU machines:** `ollama pull qwen3:8b`
- **Vision (screenshots/tab-driving):** `ollama pull gemma3:4b`
- The sidebar model picker finds anything you install — restart the browser after pulling.

## Checksums

| File | Size | SHA-256 |
|---|---|---|
| `LuckyDBrowserSetup-9.0.0.exe` | 303,971,333 bytes | `4F5503801D6F41EB39F0454A4E71E2C747725F79DFA229E108C8FF894095CD9B` |

> Hash verified 2026-09-14: local `browser/installer/output/LuckyDBrowserSetup-9.0.0.exe` matches the `v9.0.0` GitHub Release asset digest byte-for-byte. Recompute after any rebuild: `Get-FileHash browser\installer\output\LuckyDBrowserSetup-9.0.0.exe -Algorithm SHA256`.

---

**Full source + docs:** [github.com/Dylanchess0320/LuckyD-Browser](https://github.com/Dylanchess0320/LuckyD-Browser) · MIT © DylanChess03
