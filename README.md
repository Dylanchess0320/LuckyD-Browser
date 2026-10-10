<div align="center">



# LuckyD Browser

**A web browser with a built-in AI assistant that works for free — no account, no API key, no subscription.**

[![Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=7c5cff&label=release)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%2F11%20x64-0078D4?logo=windows&logoColor=white)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)

### [⬇ Download LuckyD Browser for Windows](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)

**[🎬 Watch it in action](https://www.youtube.com/@LuckyDYoutube)** · **[📝 Changelog](CHANGELOG.md)**

</div>

---

## What is this?

LuckyD is a normal, fast web browser — with an AI assistant living inside it. Press `Ctrl+Shift+A` on any page and ask it to summarize the article, explain something, or write code. It uses free AI providers and local models, so there's **no account, no API key, no subscription**.

- 🆓 **Free AI, no catch** — free providers and local models built in. No account, no key, no monthly bill.
- 🌐 **A real browser** — tabs, bookmarks, ad blocking, workspaces, private mode. Use it as your daily driver.
- 🤖 **A coding agent in a tab** — press `Ctrl+Shift+H` and tell it what to build. It writes code, runs terminals, and gets things done.
- 🔒 **Private by default** — no telemetry, no tracking. Incognito writes nothing to disk. Local-only mode keeps every prompt on your machine.

> Other AI browsers rent you their chatbot. **LuckyD finds you a free one — or runs one on your machine.**

---

## LuckyD AI

The browser's AI isn't a single chatbot — it's a routing layer that finds you something that actually works, plus an agent terminal that runs real coding CLIs.

**Provider handling that tells the truth.** Settings → Models shows every provider's live status — Ready, Needs key, or Exhausted (with retry countdown) — read from the same health snapshot the backend uses. The "Switch to best working" button moves you to the provider with evidence it answered recently, in one click. No more picking a dead model and wondering why nothing happens.

**16 coding agents in a tab.** Press `Ctrl+Shift+H` for the agent terminal: Antigravity, Claude, Codex, Copilot, Qwen, OpenCode, MiniMax Code, MiniMax Media, Cline, OpenClaw, Hermes, Pi, Grok, Muse Code, Jules, and more. Paste large prompts without corruption (10.6.3 fixed the ConPTY garbling on big pastes). Each agent runs in its own PTY with bracketed-paste support.

**The LuckyD AI vision.** The direction is a single LuckyD AI: you ask, it routes — free providers first, local models when you want privacy, your own keys when you add them — and it remembers what worked. The 10.5/10.6 provider-health work (live status, last-known-working tracking, free-model rotation) is the foundation. Not a separate model to download; the intelligence is in the routing.

---

## Install in 3 steps

1. **[Download the installer](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)** (Windows 10/11, 64-bit)
2. Run it — installs for just you, **no admin needed**
3. Open LuckyD and press `Ctrl+Shift+A` — the AI works right away with free providers

Then start chatting. That's it. (Want everything on your own machine? Add Ollama in Settings → Models for local-only mode.)

<details>
<summary>Want to use your own AI keys instead? (optional)</summary>

LuckyD also works with cloud providers if you prefer: Gemini, Groq, DeepSeek, OpenAI, Anthropic, OpenRouter, and more. Add a key in Settings → Models. Local mode keeps working as the free fallback.

</details>

---

## See it

<p align="center">
  <img src="docs/screenshots/agent-terminal-dashboard.png" alt="LuckyD Browser — agent terminal and HQ dashboard" width="920">
</p>

<p align="center">
  <b><a href="https://www.youtube.com/watch?v=isa_z1SdoO4">▶ Watch Episode 9: I Rebuilt My AI Browser in One Night</a></b>
</p>

---

## What's new

**v10.7.0** — One unified agent loop: Terminal /mesh routing and Code agents merged into the browser's `CodingAgent`; deterministic model selection (every mid-run switch is now recorded, plus an opt-in pin mode); oversized-input protection (same bug class as the 10.6.3 paste fix); PTY output chunking; durable background tasks that survive restarts; classified retry wired into the HTTP layer; summarize-instead-of-drop on context truncation; subagent results quarantined out of context. New: task-completion evals (`tests/coding_eval.py`) — 14/14 after hardening, up from 12/14.
**v10.6.3** — Antigravity agent paste fix (large pastes no longer corrupt in the terminal); 33 code-health PRs merged (exception handlers, test assertions, sandbox hardening including an RCE fix); provider-health UI polish (live status badges, one-click best-working switch). Nothing else changed since 10.6.2.
**v10.6.1** — Google bot-detection auto-fallback: searches that hit Google's "unusual traffic" page now re-run on the next working engine automatically. Nothing else changed since 10.6.0.
**v10.5.0** — Model & provider handling rebuilt: the browser now shows which AI providers are actually working, warns you before you pick a dead one, and switches to the best working option in one click. Free models stay the default.

Older changes: [full changelog](CHANGELOG.md) · [all releases](https://github.com/Dylanchess0320/LuckyD-Browser/releases)

---

<details>
<summary><b>For developers</b> — build from source, shortcuts, architecture</summary>

### Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+A` | AI Sidebar |
| `Ctrl+Shift+H` | Coding Agent HQ |
| `Ctrl+Shift+U` | Summarize this page |
| `Ctrl+Shift+L` | Read page aloud |
| `Ctrl+Shift+R` | Deep Research |
| `` Ctrl+` `` | Terminal |
| `Ctrl+K` | Command palette |

### Build from source

```powershell
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
pip install -r browser\requirements.txt
browser\run_browser.bat
```

### Architecture

```
browser/   PySide6 / Qt WebEngine · Control API :9777 · Terminal :9881
core/      agent loop, LLM client, checkpoints
tests/     headless pytest (Qt mocked on Linux CI)
```

### The trust layer (`kit/`)

Don't rebuild agent trust plumbing — build *with* ours. [`kit/`](kit/) ships LuckyD's permission scopes, approval engine, secret redaction, and audit log as a stdlib-only Python package, plus installable skills that work in LuckyD, Claude Code, OpenCode, and Codex CLI. See [`kit/SKILL_AUTHORING.md`](kit/SKILL_AUTHORING.md).

### Privacy

- Local-first: prompts never leave your machine unless you pick a cloud provider
- Control API and terminal bridge bind to `127.0.0.1` only
- No telemetry, no bundled API keys

</details>

---

<p align="center">
  <b>MIT © LuckyD</b><br>
  <a href="https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest">Download</a>
  · <a href="https://www.youtube.com/@LuckyDYoutube">YouTube</a>
  · <a href="CHANGELOG.md">Changelog</a>
</p>
