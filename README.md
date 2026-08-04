<div align="center">

# LuckyD Code

**AI-powered coding agent for Windows — work in your terminal or VS Code.**

[![CI](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml/badge.svg)](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml)
[![Python 3.10–3.12](https://img.shields.io/badge/python-3.10--3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

<img src="docs/screenshots/hq.png" alt="LuckyD Code terminal UI" width="720">

</div>

---

## Features

- **Multi-provider LLM support** — DeepSeek, OpenAI, Anthropic, Google, Z.ai, OpenRouter, or local models via Ollama. Swap providers mid-session with `/model`.
- **Full tool suite** — file editing, shell commands, git, web search/fetch, browser automation, LSP code intelligence, SQLite, and persistent memory.
- **MCP (Model Context Protocol)** — plug in any MCP server; tools auto-register as `mcp__<server>__<tool>`.
- **Sessions & resume** — every conversation is auto-saved; continue where you left off.
- **Project rules** — auto-loads `AGENTS.md`, `.clinerules`, `.goosehints`, `CLAUDE.md` into the system prompt.
- **VS Code extension** — webview-based chat UI inside your editor.
- **Safe by default** — sandboxed shell execution, secret scanning, `.env` never committed.

## Quick Start

```bat
:: 1. Clone and enter the repo
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser

:: 2. Configure your API key
copy .env.example .env
::    then edit .env and set DEEPSEEK_API_KEY (or your provider's key)

:: 3. Run
run.bat
```

| Action | Command |
|--------|---------|
| Interactive REPL | `run.bat` or `python main.py` |
| One-shot task | `python main.py "refactor this file"` |
| Help | `python main.py --help` or `/help` in the REPL |
| Switch model | `/model openai gpt-4o` |
| Resume last session | `python main.py --continue` |

## Requirements

- Python 3.10 – 3.12
- Git (for repo-aware features)
- An API key for at least one LLM provider (see table below)

## Providers

Configure in `.env` — set the key for the provider you want:

| Provider | Env var | Default model |
|----------|---------|---------------|
| DeepSeek | `DEEPSEEK_API_KEY` | deepseek-chat |
| OpenAI | `OPENAI_API_KEY` | gpt-4o |
| Anthropic | `ANTHROPIC_API_KEY` | claude-sonnet-4 |
| Google | `GOOGLE_API_KEY` | gemini-2.0-flash |
| Ollama (local) | *(none)* | codellama |
| Z.ai (GLM) | `ZAI_API_KEY` | glm-4.5 |
| OpenRouter | `OPENROUTER_API_KEY` | deepseek/deepseek-chat-v3.1 |

Swap models at runtime:

```
/model openai gpt-4o
/model anthropic claude-sonnet-4-20250514
/model zai glm-4.6
```

## MCP (Model Context Protocol)

Connect to any MCP server for extensible tooling:

1. Copy `mcp_config.example.json` to `mcp_config.json`
2. Add your servers (filesystem, github, playwright, etc.)
3. Tools auto-register as `mcp__<server>__<tool>`

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/project"]
    }
  }
}
```

## Sessions & Resume

Every run is auto-saved. Resume with:

```
python main.py --continue          :: resume most recent
python main.py --resume conv_2025  :: resume by ID prefix
/sessions                          :: list sessions in the REPL
/resume conv_2025                  :: switch mid-REPL
```

## Project Rules

LuckyD Code auto-loads `AGENTS.md`, `.clinerules`, `.goosehints`, and
`CLAUDE.md` from your workspace into the system prompt, so the agent follows
your project's conventions automatically.

## Project Layout

```
coding-agent/
├── main.py              ← Entry point
├── ui.py                ← Terminal UI
├── config.py            ← Paths + runtime settings
├── agent.py             ← Core agent logic
├── core/                ← Agent loop, LLM client, providers
├── tools/               ← Tool registry (bash, files, web, git, LSP, memory, …)
├── vscode-extension/    ← VS Code webview extension
├── assets/              ← Static assets (chat.html)
├── data/                ← Runtime data (memory, tasks, workspace, checkpoints)
├── scripts/             ← Helper scripts (auth, build)
├── docs/                ← Documentation source
├── tests/               ← Test suite
├── .env.example         ← Template for your API keys
└── run.bat              ← Windows launcher
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please read
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) first. Report security issues via
[SECURITY.md](SECURITY.md).

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
