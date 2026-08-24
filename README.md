<div align="center">

# 🌐 LuckyD Browser v2.4

> **The complete AI automation & development platform built into your browser.**
>
> Record workflows. Run multiple agents. Extract structured data. Code in parallel.  
> All offline, all private, all in one window.

[![CI](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml/badge.svg)](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10--3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=green)](https://github.com/Dylanchess0320/LuckyD-Browser/releases)

<img src="docs/screenshots/sidebar.png" alt="LuckyD Browser AI sidebar" width="720">

---

**[⬇️ Download v2.4.0](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v2.4.0)** · **[Browser Guide](README-LuckyD-Browser.md)** · **[What's New](browser/LuckyD-Launch/RELEASE_NOTES_2.4.0.md)** · **[Changelog](CHANGELOG.md)**

</div>

---

## What is LuckyD Browser?

LuckyD Browser is a **Chromium-based browser + AI coding environment + multi-agent orchestration platform** for Windows. It merges everyday browsing, AI-powered automation, and professional development tools into one seamless experience.

**Key idea:** Use free local AI (or your favorite cloud provider) to record workflows, drive the browser autonomously, extract data, and run multiple agents in parallel—all without leaving your browser tabs.

## 🎯 Core Features

| Feature | What you get |
|---------|-------------|
| **🤖 AI Sidebar** | Page-aware chat, model picker, visual Q&A, Explain/Summarize/Translate, autonomous browser agent |
| **🎬 Workflow Recorder & Replay** | Record clicks, typing, scrolling → replay with intelligent element matching that adapts when pages change |
| **📊 Structured Data Extraction** | Ask AI to turn any webpage into JSON—automate data scraping without code |
| **⚡ Multi-Agent Terminal Mesh** | Run parallel LuckyD Agent sessions, PowerShell, or CMD tabs. Swap AI providers per agent. |
| **💻 Coding Agent HQ** | Full `luckyd-code` IDE inside a browser tab: 70+ tools, memory graph, sessions, background tasks |
| **📱 Real Daily-Driver Browser** | Tabs, bookmarks, history, downloads, incognito, ad/tracker blocker, Reader/Focus modes, themes, command palette |
| **🔒 Private by Default** | Local-first AI, loopback-only APIs, no telemetry, no bundled keys |

---

## 🚀 What's New in v2.4.0

### Multi-Terminal Agent Mesh
Open multiple independent terminal tabs running **LuckyD Agent, PowerShell, or CMD**—each with its own ConPTY (real Windows console). Switch between agents and power tools without context switching.

```
[Agent 1 (LuckyD)] [Agent 2 (LuckyD)] [PowerShell] [CMD]
```

Pick different AI providers per agent—one uses Ollama locally, another uses Gemini, a third uses your OpenAI key. All at once.

### Workflow Recorder with Self-Healing Replay
1. **Record:** Click, type, scroll, interact with a page. The browser captures every step.
2. **Save:** Workflows are stored and can be scheduled (run every 15m, hourly, daily, etc.).
3. **Replay:** Run the workflow against a changed page. **Intelligent element matching** finds the right button even if the page layout shifted.

Use it for data entry, testing, or routine tasks—the replay adapts gracefully.

### Structured Data Extraction
Ask the AI to analyze a visible page and return JSON:

```
"Extract all products with price, rating, and availability into JSON"
→ [{ "name": "...", "price": 99.99, "rating": 4.5, ... }, ...]
```

No need to write scrapers or CSS selectors. The AI reads what it sees and structures it for you.

### Enhanced Dashboard & Service Tiles
- Every LuckyD service (Agent HQ, Terminal, Workflows, Network Monitor, etc.) is a tile on the dashboard.
- Health probes show live status (green/grey dot).
- Add new tools by editing `platform_tiles.json`—zero code changes needed.

### Reliability & Polish
- ✅ Fixed userscript engine (YouTube ad blocker, Video Speed Controller now work correctly)
- ✅ Repaired in-app updates (clean Windows Installer integration, session restore)
- ✅ Enhanced workflow replay accuracy
- ✅ 111+ self-tests, all green

---

### Keyboard Power User?

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+A` | Toggle AI sidebar |
| `Ctrl+Shift+H` | Open Coding Agent HQ |
| `Ctrl+Shift+T` | Open Terminal Mesh (Agent/PowerShell/CMD tabs) |
| `Ctrl+K` | Command palette |
| `Ctrl+Alt+R` / `Ctrl+Shift+F` | Reader Mode / Focus Mode |
| `Ctrl+Alt+S` / `Ctrl+Shift+S` | Read Later queue / Full-page screenshot |
| `Ctrl+/` | Keyboard shortcuts reference |

---

## 🔌 LuckyD Code — The Integrated IDE

LuckyD Code is a **full coding-agent IDE** that runs inside the browser as a tab. It ships with:

- **70+ built-in tools** — file editing, shell commands, git, web search/fetch, browser automation, LSP code intelligence, SQLite, persistent memory
- **Multi-provider LLM support** — DeepSeek, OpenAI, Anthropic, Google, Z.ai, OpenRouter, or free local models (Ollama)
- **MCP (Model Context Protocol)** — plug in any MCP server; tools auto-register
- **Session persistence** — every conversation is auto-saved; pick up where you left off
- **Project-aware** — auto-loads `AGENTS.md`, `.clinerules`, `.goosehints`, `CLAUDE.md` into the system prompt
- **Multi-turn agent loop** — with memory, checkpoints, and background tasks
- **VS Code extension** — webview chat UI embedded in your editor

### Agent Mesh Terminal Integration

The **Agent Mesh** gives you terminal tabs for multiple AI agents plus system shells, all running in parallel inside the browser:

| Agent | What it does |
|-------|-------------|
| **LuckyD Agent 1** | Main `luckyd-code` agent. Full coding tools, web search, git, memory. |
| **LuckyD Agent 2** | Secondary agent from your projects directory. Different LLM provider, independent workspace. |
| **PowerShell** | Windows PowerShell with full environment. |
| **CMD** | Windows Command Prompt. |

Swap AI providers on the fly with `/model` commands. Each agent keeps its own session and memory. Run workflows, coding tasks, and system automation in parallel.

---

## 📥 Installation & Quick Start

### Download & Install

1. **[Get LuckyDBrowserSetup-2.4.0.exe](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v2.4.0)** (176.3 MB)
2. Run the installer. No admin rights needed—installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`.
3. Launch from Start Menu or desktop shortcut.
4. (Optional) AI bootstrap runs once—skips if you only want cloud providers.

### No Account. No Keys. No Cost.

The installer sets up free **Ollama** + `llama3.2:3b` (offline, local AI). Your prompts never leave your machine.

Prefer cloud? Connect your own keys for Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, or log in with Cline.

### Build from Source

```bat
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
python -m pip install -r requirements.txt
python main.py --help
```

---

## 🛠️ LuckyD Code Configuration

```bat
cd LuckyD-Browser

:: 1. Copy the example .env
copy .env.example .env

:: 2. Edit .env and add your API key(s)
::    DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.

:: 3. Run the agent
run.bat
```

**Providers table:**

| Provider | Env var | Default model |
|----------|---------|---------------|
| DeepSeek | `DEEPSEEK_API_KEY` | deepseek-chat |
| OpenAI | `OPENAI_API_KEY` | gpt-4o |
| Anthropic | `ANTHROPIC_API_KEY` | claude-sonnet-4 |
| Google | `GOOGLE_API_KEY` | gemini-2.0-flash |
| Ollama (local) | *(none)* | codellama |
| Z.ai (GLM) | `ZAI_API_KEY` | glm-4.5 |
| OpenRouter | `OPENROUTER_API_KEY` | deepseek/deepseek-chat-v3.1 |
| ClinePass | `CLINEPASS_API_KEY` | cline-pass/kimi-k3 |

### Swap Models at Runtime

```
/model openai gpt-4o
/model anthropic claude-sonnet-4
/model zai glm-4.6
```

### Sessions & Resume

Every run auto-saves. Resume anytime:

```
python main.py --continue          # Resume last session
python main.py --resume conv_2025  # Resume by ID
/sessions                          # List in REPL
/resume conv_2025                  # Switch mid-session
```

### Project Rules

Auto-loaded into every session:
- `AGENTS.md` — agent personality & constraints
- `.clinerules` — Cline-compatible rules
- `.goosehints` — Goose framework hints
- `CLAUDE.md` — Anthropic instructions

---

## 🔐 Privacy & Security

- **Local-first:** With Ollama, your prompts and page data never leave your machine.
- **No telemetry:** LuckyD Browser phones home zero times.
- **Loopback-only:** Control API, Agent HQ, Terminal, Network Monitor all bind to `127.0.0.1`.
- **Secrets scanning:** Built-in secret detection; `.env` never committed.
- **Sandboxed execution:** Shell commands run isolated with safety guardrails.

## 📚 Project Structure

```
LuckyD-Browser/
├── browser/                 ← Chromium browser + UI
│   ├── LuckyD-Launch/       ← Release notes & build artifacts
│   ├── browser_core/        ← Tile registry, services, dashboard
│   ├── installer/           ← Windows installer (NSIS)
│   └── ...                  ← UI, profiles, userscripts
├── core/                    ← LuckyD Code agent engine
│   ├── agent_loop.py        ← Main agent loop
│   ├── llm_client.py        ← Multi-provider LLM support
│   ├── context_manager.py   ← Memory & context
│   ├── hooks.py             ← Agent callbacks
│   └── checkpoint.py        ← Session persistence
├── tools/                   ← 70+ agent tools
│   ├── file_tools.py        ← File I/O
│   ├── bash_tool.py         ← Shell execution
│   ├── git_tools.py         ← Git operations
│   ├── web_tools.py         ← Web search/fetch
│   ├── browser_tools.py     ← Control API integration
│   ├── lsp_tools.py         ← Code intelligence
│   ├── memory_tools.py      ← Persistent memory
│   ├── terminal_cli2.py     ← Agent Mesh support
│   └── ...                  ← MCP, SQLite, scheduler, etc.
├── llm/                     ← Provider-specific implementations
├── vscode-extension/        ← VS Code webview chat
├── data/                    ← Runtime data (sessions, memory, tasks)
├── docs/                    ← Documentation
├── tests/                   ← Test suite
├── .env.example             ← Template for API keys
├── main.py                  ← Agent REPL entry point
├── ui.py                    ← Terminal UI
├── config.py                ← Runtime configuration
├── run.bat                  ← Windows launcher
└── README.md                ← This file
```

---

## 🎓 Advanced Usage

### MCP (Model Context Protocol)

Extend the agent with any MCP server:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/code"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..." }
    }
  }
}
```

Tools auto-register as `mcp__<server>__<tool>`.

### Skills System

Auto-discovered skills in `skills/*.md`:

```yaml
---
name: top-picks
description: Find the best X by category
version: 1.0.0
tags: [ranking, research]
---

# How to use this skill...
```

Defined in `LUCKYD.md` with intent patterns—auto-triggered by the agent.

### Project Rules

Create any of these files in your project root to auto-inject conventions:

- `AGENTS.md` — Agent personality & capabilities
- `.clinerules` — Cline-compatible rules
- `.goosehints` — Goose framework hints
- `CLAUDE.md` — Anthropic instructions
- `LUCKYD.md` — LuckyD Code project rules

---

## 🤝 Contributing

We'd love your help! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Before starting:** Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

**Security issues?** Report privately via [SECURITY.md](SECURITY.md).

### Development Setup

```bat
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
python -m pip install -r requirements-dev.txt
```

Run tests:

```bat
pytest tests/
python -m black --check .
python -m ruff check .
```

### Build the Browser Installer

```bat
cd browser
python -m PyInstaller LuckyDBrowser.spec
::    → dist/LuckyDBrowserSetup-2.4.0.exe
```

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

**LuckyD Browser** and **LuckyD Code** are powered by:

- [Chromium](https://www.chromium.org/) — the web engine
- [Ollama](https://ollama.com/) — free, offline LLMs
- [PyInstaller](https://pyinstaller.org/) — executable packaging
- [Qt](https://www.qt.io/) — desktop UI (WebEngine)
- [xterm.js](https://xtermjs.org/) — terminal emulation
- The open-source community

---

**Made with ❤️ by [Dylan Chess](https://github.com/Dylanchess0320)**

**Get started:** [Download v2.4.0 →](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v2.4.0)
