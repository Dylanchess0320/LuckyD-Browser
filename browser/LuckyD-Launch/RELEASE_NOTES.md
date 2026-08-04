# LuckyD Browser v1.3.0 â€” Release Notes

> **The AI browser that doesn't need an API key.** Free, unlimited, offline AI built in â€”
> plus a full coding agent and developer terminal living in your tabs.

**[â¬‡ Download `LuckyDBrowserSetup-1.3.0.exe`](../../releases)** â€” Windows 10/11 x64 Â· per-user install Â· no admin needed

---

## The pitch in 10 seconds

1. Run the installer.
2. Leave **"Set up free unlimited local AI"** checked.
3. Open the sidebar and chat â€” **no account, no key, no cost, ever.**

The installer sets up [Ollama](https://ollama.com) and a fast local model (`llama3.2:3b`) for you.
Your prompts never leave your machine. Prefer the cloud? Bring your own keys for
Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, or log in with Cline.

## What's inside

ðŸ¤– **AI Sidebar** (`Ctrl+Shift+A`) â€” Markdown chat, per-provider model picker, page-aware Q&A,
ðŸ“· visual Q&A, and an autonomous agent that drives your **real, visible tab** while you watch.

âš¡ **Coding Agent HQ** (`Ctrl+Shift+H`) â€” a full coding-agent workspace in a browser tab:
70+ tools, memory graph, sessions, background tasks. Auto-starts with the browser and
**mirrors the sidebar's AI provider**.

ðŸ’» **In-browser Terminal** â€” the complete `luckyd-code` CLI on a real Windows ConPTY
(xterm.js), one click from the dashboard.

ðŸŒ **A real daily-driver browser** â€” tabs, bookmarks (import/export), history, downloads,
incognito, ad/tracker blocker, find-in-page, themes, command palette, print/save,
AI right-click actions (Explain / Summarize / Translate), Copy-as-Markdown.

ðŸ”’ **Private by default** â€” local-first AI, loopback-only control APIs, no telemetry,
no keys shipped in the bundle.

## Install notes

- Installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser` with Start Menu + optional desktop shortcut
  and a Settings > Apps uninstall entry.
- The AI bootstrap runs after setup in a small console window (one-time ~2 GB model download).
  Uncheck it if you only want cloud providers â€” you can run it later from the install folder.
- Silent install: `LuckyDBrowserSetup-1.3.0.exe /VERYSILENT /NORESTART`

## Upgrade tips

- **Better answers on GPU machines:** `ollama pull qwen3:8b`
- **Vision (screenshots/tab-driving):** `ollama pull gemma3:4b`
- The sidebar model picker finds anything you install â€” restart the browser after pulling.

## Checksums

| File | Size |
|---|---|
| `LuckyDBrowserSetup-1.3.0.exe` | 164.7 MB |

*SHA-256: `02B756A6EE90619B80E615DA8545CA8DAE4D3522C0FD31B50ED64ED7EC86CA3F`*

---

**Full source + docs:** [github.com/Dylanchess0320/LuckyD-Browser](https://github.com/Dylanchess0320/LuckyD-Browser) Â· MIT Â© DylanChess03

