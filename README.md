<div align="center">

<img src="docs/screenshots/hero.svg" alt="LuckyD Browser 3.9.0 — Daily Driver" width="100%">

# LuckyD Browser

**The AI browser that runs *your* models — local, unlimited, offline — plus a full coding agent and real terminals in one window.**

[![CI](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml/badge.svg)](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=7c5cff&label=release)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Dylanchess0320/LuckyD-Browser/total?color=34d399)](https://github.com/Dylanchess0320/LuckyD-Browser/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%2F11%20x64-0078D4?logo=windows&logoColor=white)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10--3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

**[⬇ Download v3.9.0](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.9.0)**
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

| | LuckyD 3.9 | Comet / Dia | Edge Copilot | Chrome Gemini |
|---|:---:|:---:|:---:|:---:|
| Free AI with **no account / no key** | ✅ local Ollama | ❌ | ❌ | ❌ |
| Works **fully offline** | ✅ | ❌ | ❌ | ❌ |
| Agent **drives your live tabs** | ✅ | ✅ | limited | limited |
| **Coding agent + terminals in tabs** | ✅ | ❌ | ❌ | ❌ |
| **Workspaces · HTTPS-Only · Memory Saver** | ✅ | mixed | mixed | mixed |
| Open source MIT · no admin to install | ✅ | ❌ | ❌ | ❌ |

> The others rent you their AI. **LuckyD runs yours.**

---

## New in 3.9.0 — Daily Driver

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

---

## The one-window platform

| Surface | What it is | Open it |
|---|---|---|
| **AI Sidebar** | Markdown chat, model picker, visual Q&A, autonomous agent on your **real visible tab** | `Ctrl+Shift+A` |
| **Coding Agent HQ** | Full `luckyd-code` workspace in a tab — 70+ tools, memory, sessions | `Ctrl+Shift+H` |
| **Terminals** | Real Windows ConPTY via xterm.js — agent CLI, PowerShell, CMD, Agent Mesh | `` Ctrl+` `` |
| **Dashboard** | Live new-tab hub: status pills, Ask LuckyD, one-tap tiles, speed dial | New tab |
| **Workflows** | Record Control-API actions, replay with self-healing element matching | Tools → Workflows |

**Harness mode is on by default:** sidebar tasks run on the coding-agent backend, which can drive the tabs you are looking at.

---

## Install in 10 seconds

1. Get **[LuckyDBrowserSetup-3.9.0.exe](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.9.0)**
2. Run it — per-user, **no admin**, installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
3. Leave **“Set up free unlimited local AI”** checked → Ollama + `llama3.2:3b` (~2 GB, one time)
4. `Ctrl+Shift+A` → chat offline. Or bring your own keys (Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, Cline, OpenCode)

Silent: `LuckyDBrowserSetup-3.9.0.exe /VERYSILENT /NORESTART`

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
# → browser\installer\output\LuckyDBrowserSetup-3.9.0.exe
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
  <a href="https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.9.0">Download v3.9.0</a>
  · <a href="https://www.youtube.com/watch?v=La6bxaa7icY">Watch the showcase</a>
</p>
