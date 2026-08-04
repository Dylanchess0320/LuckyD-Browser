# LuckyD Browser v1.3.0 — Release Notes

> **The AI browser that doesn't need an API key.** Free, unlimited, offline AI built in —
> plus a full coding agent and developer terminal living in your tabs.

**[⬇ Download `LuckyDBrowserSetup-1.3.0.exe`](../../releases)** — Windows 10/11 x64 · per-user install · no admin needed

---

## The pitch in 10 seconds

1. Run the installer.
2. Leave **"Set up free unlimited local AI"** checked.
3. Open the sidebar and chat — **no account, no key, no cost, ever.**

The installer sets up [Ollama](https://ollama.com) and a fast local model (`llama3.2:3b`) for you.
Your prompts never leave your machine. Prefer the cloud? Bring your own keys for
Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, or log in with Cline.

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
- Silent install: `LuckyDBrowserSetup-1.3.0.exe /VERYSILENT /NORESTART`

## Upgrade tips

- **Better answers on GPU machines:** `ollama pull qwen3:8b`
- **Vision (screenshots/tab-driving):** `ollama pull gemma3:4b`
- The sidebar model picker finds anything you install — restart the browser after pulling.

## Checksums

| File | Size |
|---|---|
| `LuckyDBrowserSetup-1.3.0.exe` | 164.7 MB |

*(add SHA-256 here when uploading)*

---

**Full source + docs:** [github.com/Dylanchess0320/LuckyD-Browser](https://github.com/Dylanchess0320/LuckyD-Browser) · MIT © DylanChess03
