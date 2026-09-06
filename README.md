<div align="center">

# ðŸŒ LuckyD Browser v3.8.0 â€” Hardened

> **The AI browser that doesn't break. Free, unlimited, offline AI + a full coding platform in one window.**
>
> Agent Mesh. Self-healing workflows. 70+ code tools. Real ConPTY terminals.  
> All private, all loopback-only, all in one window.

[![CI](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml/badge.svg)](https://github.com/Dylanchess0320/LuckyD-Browser/actions/workflows/ci.yml)
[![Python 3.10â€“3.12](https://img.shields.io/badge/python-3.10--3.12-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-3.8.0-green.svg)](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.8.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/Dylanchess0320/LuckyD-Browser?color=green)](https://github.com/Dylanchess0320/LuckyD-Browser/releases)

<img src="docs/screenshots/sidebar.png" alt="LuckyD Browser AI sidebar + Agent Mesh" width="720">

---

**[â¬‡ï¸ Download v3.8.0](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.8.0)** Â· **[Browser Guide](README-LuckyD-Browser.md)** Â· **[Changelog](CHANGELOG.md)** Â· **[Security](SECURITY.md)**

</div>

---

## ðŸŽ¥ Showcase â€” Watch LuckyD in Action

> **See LuckyD Browser in action â€” workflows, Agent Mesh, extraction & daily browsing in one window.**

[![LuckyD Browser â€” Video Showcase](https://img.youtube.com/vi/La6bxaa7icY/maxresdefault.jpg)](https://www.youtube.com/watch?v=La6bxaa7icY)

**[â–¶ï¸ Watch on YouTube â€” https://www.youtube.com/watch?v=La6bxaa7icY](https://www.youtube.com/watch?v=La6bxaa7icY)**

---

## Why LuckyD

| | **LuckyD 3.8.0** | Comet / Dia | Edge + Copilot | Chrome + Gemini |
|---|---|---|---|---|
| Free AI **with no account/key** | âœ… unlimited, local Ollama | âŒ | âŒ | âŒ |
| **Offline** | âœ… | âŒ | âŒ | âŒ |
| **9 Agent Mesh CLIs** (Claude, Codex, Copilot, Qwen, OpenCode, Cline, OpenClaw, DeepSeek, Pi) | âœ… | âŒ | âŒ | âŒ |
| Real **ConPTY terminals** in tabs | âœ… | âŒ | âŒ | âŒ |
| Self-healing **workflow recorder** | âœ… | limited | limited | limited |
| Open source MIT | âœ… | âŒ | âŒ | âŒ |
| No admin to install | âœ… | âœ… | âœ… | âœ… |

> The others rent you their AI. **LuckyD runs yours â€” and it doesn't crash your terminal.**

---

## âœ¨ What's New in 2.5.11

### ðŸ”§ Hardened Platform
The `2.5.8` terminal took every shell down (`dict` vs NUL-block). `2.5.11` locks it down:

- **Settings/Session atomic** â€” `tmp â†’ replace`, corrupt backup (`settings.corrupt.*.json`), `deepcopy(DEFAULTS)` fix, `DATA_DIR` fallback, expanded `terminal_cli` migration
- **Terminal sanitized** â€” NUL-filtered `env_block`, 520-char Desktop buffer, mesh `PATH` validation, generic spawn error (no path leak), `max_size 1MB` WS
- **Control API hardened** â€” `hmac.compare_digest` (constant-time), 1 MB body limit, DNS-rebinding `Host` check
- **Build hygiene** â€” `browser/version_info.txt` now tracked, large locals (`LuckyD App/`, `youtube/`) ignored, `ruff` + `black` green
- **CI green** â€” `pytest` mocked `PySide6` on Linux so `test_browser_integrations.py` collects everywhere

### ðŸ•¸ï¸ Agent Mesh (2.5.8â€“2.5.9)
One workspace, **9 live CLIs** on their own ConPTY:

```
ðŸŸ  Claude  ðŸŸ¢ Codex  âš« Copilot  ðŸŸ£ Qwen  ðŸ”µ OpenCode  ðŸŸ¡ Cline  ðŸ¦ž OpenClaw  ðŸ‹ DeepSeek  âšª Pi
```

- **Dock** â€” 9 chips in the terminal tab (`#meshdock`), dimmed when not installed, `mesh install <name>` hint
- **Mesh workspace** (`Ctrl+Alt+M`, toolbar, Tools menu, dashboard) â€” 4 live panes: **Agent 1** Â· **Agent 2** Â· **PowerShell** Â· **CMD**, each an iframe'd `ws://127.0.0.1:9881?token=â€¦&shell=â€¦` with its own PTY
- **Terminal page** now injects `WS_TOKEN` + `MESH_META` and wires `chip` â†’ `switchShell()`

### ðŸ©¹ Terminal Fix (2.5.9)
`_spawn_pty()` passed `env=dict` to `pywinpty.PTY.spawn()` â†’ `cffi: 'dict' not str` â†’ every `Agent`/`Agent2`/`PowerShell`/`CMD`/`mesh-*` â†’ `[terminal failed to start]`. Fixed to `"\0".join(f"{k}={v}" â€¦) + "\0"` matching `winpty/ptyprocess.py`.

### ðŸ¤– OpenCode Zen + Resilient Updater (2.5.8)
- **OpenCode provider** via `OPENCODE_API_KEY`
- **Updater** retries, validates `is_newer`, shows `WHATS_NEW` toast once per version

---

## ðŸŽ¯ Core Features

| Feature | What you get |
|---------|-------------|
| **ðŸ¤– AI Sidebar** | Page-aware chat, per-provider model picker, visual Q&A, Explain/Summarize/Translate, autonomous agent driving your **real, visible tab** |
| **ðŸ•¸ï¸ Agent Mesh** | 9 CLIs on ConPTY + 4-pane workspace (Agent 1 / Agent 2 / PowerShell / CMD) â€” `Ctrl+Alt+M` |
| **ðŸ’» In-Browser Terminal** | `xterm.js` + `pywinpty` ConPTY + `websockets` bridge (`:9881?token=`), `LUCKYD_AGENT_SLOT=1/2`, resizes via `set_size(cols,rows)` |
| **ðŸŽ¬ Workflows** | Record `Control API` `/act` â†’ replay with fingerprint scoring (self-healing) + schedules (`/schedules`) |
| **ðŸ“Š Extract** | `POST /extract` â†’ instruction + JSON schema â†’ AI-parsed page text |
| **ðŸ“± Daily-Driver** | Tabs, groups (collapse chip, 6-color), vertical tabs, side pane, bookmarks bar, history, downloads (speed/ETA/pause), incognito, adblock, Reader/Focus, zoom memory, screenshots, themes (incl. Synthwave Konami), command palette |
| **ðŸ”’ Private** | Loopback-only (`9777`/`9881` + `terminal_token`/`browser_api_token`), no telemetry, no bundled keys |

---

## ðŸ“¥ Install in 10 seconds

1. **[Get LuckyDBrowserSetup-3.8.0.exe](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.8.0)**
2. Run â€” per-user, no admin â†’ `%LOCALAPPDATA%\Programs\LuckyDBrowser` + Start Menu + desktop shortcut
3. Leave **â€œSet up free unlimited local AIâ€** checked â†’ Ollama + `llama3.2:3b` (~2 GB) auto-installs
4. `Ctrl+Shift+A` â†’ chat offline. Or bring your own keys: Gemini, Groq, DeepSeek, OpenAI, Anthropic, Z.ai, OpenRouter, Cline, OpenCode.

Silent: `LuckyDBrowserSetup-3.8.0.exe /VERYSILENT /NORESTART`

---

## ðŸ•¸ï¸ Agent Mesh in Action

```powershell
# Each chip is a real PTY:
ws://127.0.0.1:9881?token=<per-profile> &cols=120&rows=30&shell=mesh-claude
ws://127.0.0.1:9881?token=<per-profile> &cols=120&rows=30&shell=mesh-codex
# ...
```

- **Agent 1** `luckyd-cli.exe` (`main.py` via `main.spec`) â€” rich REPL, `/help`, `/tools`, `AgentHandoff`, `TeamCreate`â€¦
- **Agent 2** `F:\coding-agent\main.py` or `run.bat` via `cmd /c` â€” its own checkout/workspace
- **System** `powershell.exe -NoLogo -NoExit` / `cmd.exe`
- **Mesh** `claude`/`codex`/`copilot`/`qwen`/`opencode`/`cline`/`openclaw`/`dsh`/`pi` via `shutil.which` allowlist

Switch live with the dock â€” no dead PTY (missing CLIs explain `mesh install <name>`).

---

## âŒ¨ï¸ Keyboard

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+A` | AI Sidebar |
| `Ctrl+Shift+H` | Agent HQ |
| `Ctrl+`` ` / `Ctrl+Shift+`` ` | Terminal (Agent / PowerShell) |
| `Ctrl+Alt+M` | Agent Mesh (4 panes) |
| `Ctrl+K` | Command palette |
| `Ctrl+Alt+R` / `Ctrl+Shift+F` | Reader / Focus |
| `Ctrl+Shift+S` / `Ctrl+Alt+S` | Screenshot / Read Later |

---

## ðŸ› ï¸ LuckyD Code â€” Full IDE in a Tab

70+ tools (file, shell, git, web, LSP, SQLite, memory), multi-provider LLM, MCP (`mcp__<server>__<tool>`), sessions (`--continue`/`--resume`), `AGENTS.md`/`.clinerules`/`.goosehints`/`CLAUDE.md` ingestion, `/cost`, `/undo`, memory graph.

```
Provider        Env var              Default model
Ollama (local)  â€”                    llama3.2:3b (free, offline)
OpenCode        OPENCODE_API_KEY     zen
DeepSeek        DEEPSEEK_API_KEY     deepseek-chat
OpenAI          OPENAI_API_KEY       gpt-4o
Anthropic       ANTHROPIC_API_KEY    claude-sonnet-4
Google          GOOGLE_API_KEY       gemini-2.0-flash
OpenRouter      OPENROUTER_API_KEY   â€”
Z.ai            ZAI_API_KEY          glm-4.5
Cline/ClinePass CLINEPASS_API_KEY    â€”
```

---

## ðŸ” Privacy

- Local-first Ollama â†’ prompts never leave device
- `browser_api_token` + `terminal_token` (`secrets.token_urlsafe(32)`) per-profile, `127.0.0.1` only, `fetch` needs `Authorization: Bearer â€¦` or `?token=â€¦`
- No telemetry, no bundled keys, incognito touches nothing

---

## ðŸ—ï¸ Build from Source

```powershell
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
pip install -r requirements.txt

# Run dev
python -m browser.main  # or browser\run_browser.bat

# Build 2.5.11 (PyInstaller 6.21 + Inno 6)
powershell -File browser/installer/build_installer.ps1
# â†’ browser/installer/output/LuckyDBrowserSetup-2.5.11.exe (171.7 MB)
```

---

## ðŸ“š Structure

```
browser/  (PySide6/Qt WebEngine, Control API :9777, Terminal :9881, Dashboard/HQ/Mesh)
core/     (agent loop, llm_client, checkpoint)
tools/    (70+ tools incl. agent_orchestration, subagent)
tests/    (120 tests â€” test_browser_integrations.py now mocks PySide6 on Linux CI)
```

---

## ðŸ™ Credits

Chromium Â· Ollama Â· Qt Â· xterm.js Â· pywinpty Â· PyInstaller Â· Inno Setup

**Made with â¤ï¸ by [Dylan Chess](https://github.com/Dylanchess0320) â€” [â­ Star it](https://github.com/Dylanchess0320/LuckyD-Browser) if it saves you a bill.**
