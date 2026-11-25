# LuckyD Browser - New User Setup Guide

Applies to **LuckyD Browser 10.9** on **Windows 10/11 x64**.
Last verified against the installed build: 2026-11-11.

This guide covers:

1. Installing and hardening the browser.
2. Setting up a free local AI engine with Ollama.
3. Using the built-in terminal agents (Agent 1 and Agent 2).
4. Installing the Agent Mesh CLIs.
5. Optional extras: MCP servers, LSP, Graphify, deep research, Drive backup.
6. Verification, troubleshooting, and security notes.

A companion script, `LuckyD-Bootstrap.ps1`, can automate the core installs.
It never writes secrets and is safe to re-run. The 10.9 installer bundles
the script and this guide but downloads nothing itself; Start Menu shortcuts
let you run any optional setup when you choose.

---

## 0. Before you start

| Item | Requirement |
|---|---|
| OS | Windows 10/11 x64 |
| RAM | 8 GB minimum; 16 GB recommended for local models |
| Disk | About 1 GB for the browser, plus 2-10 GB for Ollama models and Agent Mesh CLIs |
| Admin rights | Not required for the browser or Ollama (per-user install). Admin is needed for WSL2 and some system-wide tools |
| Network | Required for installation. Local AI works offline after models are downloaded |

The browser bundles its own Chromium engine (Qt WebEngine), Python runtime, and
the `luckyd-cli.exe` agent, so it does not need Chrome, Edge, or a system Python
to run its core features.

---

## 1. Install LuckyD Browser

### 1.1 Installer

1. Obtain the installer from the author's official release channel. The expected
   filename is `LuckyDBrowserSetup-<version>.exe` (for example,
   `LuckyDBrowserSetup-10.9.3.exe`), or use the portable ZIP if one is provided.
2. Run the installer. It is a per-user install and does not require admin.
3. Windows SmartScreen will show "Unknown publisher" because the executable is
   not code-signed. Choose **More info -> Run anyway** only if you trust the
   source. If you are on a managed work PC and the installer is blocked, use the
   portable ZIP.
4. The installer does not download optional tools. After installation, open
   the LuckyD Browser folder in the Start Menu and choose **Set up Local AI**
   (about 2 GB) if you want a working offline agent, or **Install Agent Mesh
   CLIs** (several GB) if you need all coding-agent CLIs. Both are optional
   and can be run later.
5. Launch **LuckyD Browser**.

### 1.2 Verify what you installed

- Open the install folder:
  `%LOCALAPPDATA%\Programs\LuckyDBrowser`
- Right-click `LuckyDBrowser.exe` -> **Properties** -> **Details**.
  `FileVersion` should match the release you downloaded (for example, `10.9.0.0`).
- Optional integrity check, if a checksum was published with your download:

```powershell
Get-FileHash "$env:USERPROFILE\Downloads\LuckyDBrowserSetup-<version>.exe" -Algorithm SHA256
```

Compare the output with the published SHA-256 hash.

> Warning: At the time of writing (2026-11-11) the GitHub release repository
> referenced by the auto-updater returned 404 and the author's public account
> showed no repositories. The built-in update check will fail until a release
> source is restored. Verify any new download independently before installing.

### 1.3 Where LuckyD stores things

| What | Location |
|---|---|
| Program files | `%LOCALAPPDATA%\Programs\LuckyDBrowser` |
| Bundled agent, runtime, config | `%LOCALAPPDATA%\Programs\LuckyDBrowser\_internal` |
| Shared agent config (`.env`) | `%LOCALAPPDATA%\Programs\LuckyDBrowser\_internal\.env` |
| Browser settings and tokens | `%LOCALAPPDATA%\LuckyDBrowser\settings.json` |
| History, bookmarks, downloads | `%LOCALAPPDATA%\LuckyDBrowser\browser.db` |
| Restored tabs | `%LOCALAPPDATA%\LuckyDBrowser\session.json` |
| Agent data, audit logs, schedules | `%USERPROFILE%\.luckyd` |
| Ollama models | `%USERPROFILE%\.ollama\models` |

The install is per-user, so these files are writable by your Windows account.
That is convenient, but it also means any program running as you can modify
them. Treat the install like a local developer tool, not a sandboxed store app.

---

## 2. First-run browser settings

Open the browser settings (Tools/Settings menu) and review the following.

### 2.1 Automation services

| Setting | Default | Recommendation |
|---|---|---|
| `browser_api_enabled` | On | Keep on if you want the agent to drive tabs; turn off if you only browse |
| `browser_api_port` | 9777 | Leave unless the port is already in use |
| `cdp_debugging` | On | Keep on only for agent/screenshot features; requires a restart to change |
| `harness_mode` | On | Keep on for the full agent experience |
| `harness_autostart` | On | Keep on if you use the HQ/harness features |
| `https_only` | On | Keep on |
| `update_auto_check` | On | Keep on, but see the update-source warning above |

All local services bind to `127.0.0.1` only:

| Service | Port |
|---|---|
| Browser Control API | 9777 |
| Terminal WebSocket/PTY | 9881 |
| Harness HQ | 8000 |
| Chromium CDP | 9222 |

### 2.2 Tokens

The browser generates strong random tokens on first run and stores them in
`%LOCALAPPDATA%\LuckyDBrowser\settings.json`:

- `browser_api_token` guards the Control API.
- `terminal_token` guards the terminal WebSocket.
- A separate HQ token is stored in
  `_internal\.luckyd-code\hq_token`.

Do not clear or reuse these values. If you turn a service off, leave the token
in place so it is still protected when you turn the service back on.

### 2.3 Trust and approvals

The agent has a trust layer with:

- Permission scopes: read, network, browser, files, shell, desktop, git, memory,
  agents, MCP, system.
- Global mode: `ask` (default), `auto`, or `step-through`.
- Per-scope policy: `ask`, `allow`, or `deny`.
- An audit log under `%USERPROFILE%\.luckyd`.

Keep the global mode on **ask** and leave shell, files, browser, desktop, git,
and MCP scopes on **ask** until you understand the agent's behavior. Read the
prompts before approving. The audit log records every tool call and decision.

---

## 3. AI setup

LuckyD can use a local model (free, unlimited, offline, private) or cloud
providers (free tiers or paid API keys). The automatic priority is:

1. Local Ollama
2. OpenRouter free models
3. Google Gemini free tier
4. Groq free tier
5. Cline free tier (last; quota-limited)

The automatic rotation is designed to stay on free models. A paid provider is
used only when you configure it as the active provider (or when no free option
is available on your machine).

### 3.1 Option A: local Ollama (recommended)

The browser ships an Ollama bootstrap script:

`%LOCALAPPDATA%\Programs\LuckyDBrowser\ollama_setup.ps1`

It installs Ollama per-user, starts the server, and pulls a small,
tool-capable model. Run it from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File "$env:LOCALAPPDATA\Programs\LuckyDBrowser\ollama_setup.ps1" -Model llama3.2:3b
```

If the script is missing, install Ollama manually:

```powershell
winget install --id Ollama.Ollama -e
# The installer normally starts the server. If it is not running, open a
# separate window and run: ollama serve
ollama pull llama3.2:3b
```

Then verify:

```powershell
ollama list
ollama run llama3.2:3b "Reply with OK"
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags
```

The last command should return JSON that lists your installed models.

#### Choosing a local model

| Model | Approx. size | Best for |
|---|---|---|
| `llama3.2:3b` | ~2 GB | Default; tool-capable; runs on CPU-only machines |
| `qwen3:8b` | ~5 GB | Better reasoning; needs 8 GB+ RAM and is slow on CPU |
| `gemma3:4b` | ~3 GB | Vision/screenshot questions |
| `qwen2.5-coder:7b` | ~4.7 GB | Coding; heavier than llama3.2 |
| `phi4` | ~9 GB | Stronger reasoning on machines with enough RAM |

Notes:

- The first response after a model loads is slow; later responses are faster.
- The app's internal default tag is `luckyd:8b`. That is a custom local model
  used by the author. If you do not have that tag, the app picks the best
  installed model in this order: `llama3.2`, `qwen3`, `gemma3`, `phi4`,
  `qwen2.5`, `llama3.3`, `llama3.1`, `mistral`, `deepseek`, `gpt-oss`.
- To pin a specific model, set `OLLAMA_MODEL=llama3.2:3b` in the `.env` file
  described in section 3.3.
- The terminal and browser share the same local Ollama server at
  `http://127.0.0.1:11434`.

### 3.2 Option B: free and paid cloud providers

If your machine is slow, or you want a stronger model, add a cloud provider.
Free tiers are enough to start.

| Provider | Environment variable | Where to get it | Notes |
|---|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/keys` | Free `:free` models; the app uses a free meta-router |
| Google Gemini | `GOOGLE_API_KEY` | `https://aistudio.google.com/apikey` | Free API tier; separate from Google AI Pro |
| Groq | `GROQ_API_KEY` | `https://console.groq.com/keys` | Fast free tier |
| Cline | none (CLI login) | `npm i -g cline` then `cline auth` | Free tier is quota-limited |
| DeepSeek | `DEEPSEEK_API_KEY` | DeepSeek platform | Paid, inexpensive |
| OpenAI | `OPENAI_API_KEY` | OpenAI platform | Paid |
| Anthropic | `ANTHROPIC_API_KEY` | Anthropic Console | Paid |
| xAI | `XAI_API_KEY` | `https://console.x.ai` | Paid |
| Moonshot | `MOONSHOT_API_KEY` | `https://platform.kimi.ai` | Paid; OpenAI-compatible |
| Z.ai | `ZAI_API_KEY` | Z.ai platform | Paid |
| MiniMax | `MINIMAX_API_KEY` | MiniMax platform | Paid |
| Meta | `META_API_KEY` | Meta API | Paid; also used by Muse |
| Mistral | `MISTRAL_API_KEY` | Mistral platform | Paid |

### 3.3 Where to put API keys

Edit the shared config file:

`%LOCALAPPDATA%\Programs\LuckyDBrowser\_internal\.env`

Open it with Notepad, uncomment the provider block you want, and add the key:

```dotenv
# One provider is enough. The agent auto-detects which key is set.
GOOGLE_API_KEY=your-google-api-key
# OPENROUTER_API_KEY=your-openrouter-key
# GROQ_API_KEY=your-groq-key
# OLLAMA_MODEL=llama3.2:3b
```

Rules and tips:

- Keep one assignment per line as `KEY=value`. Do not add spaces around `=`.
- Lines beginning with `#` are comments.
- Restart the terminal tab (or the browser) after editing so child processes
  pick up the new environment.
- The browser and both terminal agents read the same `.env`.
- The agent also supports per-slot overlays for different model choices:
  `_internal\.luckyd-agent-1.env` and `_internal\.luckyd-agent-2.env`.
  Values in the slot overlay override the shared `.env` for that agent only.
- API keys in `.env` are passed to child terminals and third-party agent CLIs.
  Do not add keys you are unwilling to expose to those processes.
- Back up `.env` before reinstalling or updating the browser; an installer
  update may replace files in the install folder.

You can also set an explicit provider and model in `.env`:

```dotenv
CODING_AGENT_PROVIDER=ollama
CODING_AGENT_MODEL=llama3.2:3b
```

### 3.4 Verify the AI setup

From PowerShell:

```powershell
$cli = "$env:LOCALAPPDATA\Programs\LuckyDBrowser\_internal\luckyd-cli.exe"
& $cli providers
& $cli doctor
```

In the browser terminal, type `/providers` or `/doctor`.

The provider list reports statuses such as `ready`, `needs key`, and
`not installed`. Note that the local-Ollama row can look optimistic in the
provider list; the definitive test is an actual prompt.

---

## 4. The built-in terminal and agents

Open the browser's **Terminal** tab (`http://127.0.0.1:9777/terminal` inside the
browser). A shell dock lets you choose the backend.

### 4.1 Agent 1 and Agent 2 work out of the box

| Shell | What it runs |
|---|---|
| `agent` | The bundled `luckyd-cli.exe` interactive REPL (Agent 1) |
| `agent2` | The `coding-agent` checkout on your Desktop if present; otherwise the same bundled CLI launched with `--agent 2` |
| `powershell` | A normal PowerShell session in your home directory |
| `cmd` | A normal Command Prompt session in your home directory |
| `mesh-*` | Third-party Agent Mesh CLIs, if installed (section 5) |

Both agent slots are full agent sessions with file, shell, browser, and desktop
tools. Agent 2 does not require a separate checkout to work.

Useful REPL slash commands:

| Command | Purpose |
|---|---|
| `/providers` | Show provider readiness and active provider |
| `/doctor` | Check provider and Agent Mesh backend health |
| `/model` | Switch provider/model |
| `/init` | Index/initialize the current project |
| `/cost` | Show token/cost usage |
| `/sessions` | List saved sessions |
| `/resume` | Resume a session |
| `/compact` | Compress the conversation context |
| `/goal` | Set a budgeted goal for a long task |
| `/help` | Full command list |

### 4.2 Permission modes

When launched from a normal console, `luckyd-cli.exe` accepts
`--permission-mode`:

| Mode | Behavior |
|---|---|
| `default` | Every tool call goes through the approval hook |
| `acceptEdits` | Read/normal tools and file edits are auto-allowed; shell/browser tools are blocked |
| `auto` (default) | Read/normal tools are auto-allowed; tools that require approval go to the approval hook |
| `bypassPermissions` | Everything except hard-blocked tools is allowed; use only in isolated environments |
| `off` | Only read-only tools are allowed; every state-changing tool is blocked |

The browser terminal launches the agent without extra flags, so it uses the
default `auto` behavior plus the Trust policy. Keep the Trust policy on **ask**
and approve shell/file/browser actions manually. If you launch the CLI yourself
from PowerShell and want maximum caution, use:

```powershell
& "$env:LOCALAPPDATA\Programs\LuckyDBrowser\_internal\luckyd-cli.exe" --permission-mode default
```

### 4.3 Optional terminal overrides

The browser settings file can point the terminal at a different CLI. Close the
browser, edit `%LOCALAPPDATA%\LuckyDBrowser\settings.json`, and set:

```json
{
  "terminal_cli": "C:\\path\\to\\luckyd-cli.exe",
  "terminal_cli2": "C:\\path\\to\\coding-agent\\main.py",
  "terminal_port": 9881
}
```

Restart the browser afterward. Leave `terminal_cli` empty to use the bundled
CLI auto-discovery. `terminal_cli2` accepts an `.exe`, `main.py`, `run.bat`, or
`run.cmd`.

The terminal server caps concurrent sessions at 6 by default. You can raise it
with the `LUCKYD_TERMINAL_MAX_SESSIONS` environment variable, but each session
is a full CLI process tree, so do not raise it casually.

---

## 5. Agent Mesh setup

The Agent Mesh is the set of third-party coding CLIs that can run inside the
LuckyD terminal and the `/mesh` page (four parallel sessions). LuckyD finds a
mesh backend by looking for its executable on `PATH` (plus a few well-known
install folders). A just-installed CLI can take up to about 5 seconds to appear;
reload the terminal page if it is still dimmed.

### 5.1 Prerequisites

Install these once, then close and reopen LuckyD Browser so the terminal
inherits the updated `PATH`.

```powershell
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Git.Git -e
winget install --id GitHub.cli -e
winget install --id Python.Python.3.12 -e
```

- **Node.js LTS** provides `npm`, needed by most mesh CLIs.
- **Git** is needed by git tools and by GitHub CLI authentication.
- **GitHub CLI** is needed for Copilot and GitHub operations.
- **Python** is needed for the pip-based agents (Hermes, Aider) and optional
  tools such as LSP and Graphify.

Make sure the npm global bin directory is on `PATH`:

```powershell
npm config get prefix
# Usually %APPDATA%\npm; add it to PATH if npm-installed commands are not found.
```

For Muse Code only, install WSL2 with Ubuntu (admin PowerShell, then reboot):

```powershell
wsl --install -d Ubuntu
```

### 5.2 Install the mesh CLIs

Run only the rows you want. Each command installs the CLI; authentication is
covered in section 5.3.

| Shell name(s) | Install | Tier | Notes |
|---|---|---|---|
| `mesh-agy`, `agy`, `antigravity` | `winget install --id Google.AntigravityCLI -e` | Free | Google sign-in on first run |
| `mesh-claude` | `npm i -g @anthropic-ai/claude-code` | Credit-billed | Needs `ANTHROPIC_API_KEY` or Claude login |
| `mesh-codex` | `npm i -g @openai/codex` | Credit-billed | Needs `OPENAI_API_KEY` or CLI login |
| `mesh-copilot`, `copilot` | `winget install --id GitHub.Copilot -e` | Credit-billed | Requires `gh auth login` and a Copilot subscription |
| `mesh-qwen`, `qwen` | `npm i -g @qwen-code/qwen-code` | Free | Qwen account/OAuth |
| `mesh-opencode`, `opencode` | `npm i -g opencode-ai` | Keyed | Needs `OPENCODE_API_KEY` or provider keys |
| `mesh-mcode`, `mcode` | `npm i -g @minimax-ai/code` | Keyed | `mcode login` or `MINIMAX_API_KEY` |
| `mesh-mmx`, `mmx` | `npm i -g mmx-cli` | Keyed | `mmx auth login` or `MINIMAX_API_KEY` |
| `mesh-cline`, `cline` | `npm i -g cline` | Credit-billed | `cline auth`; free tier is quota-limited |
| `mesh-openclaw`, `openclaw` | `npm i -g openclaw` | Free | Login on first run if prompted |
| `mesh-hermes`, `hermes` | `python -m pip install --user hermes-agent` | Free | LuckyD launches `hermes chat` |
| `mesh-pi`, `pi` | `npm i -g @mariozechner/pi-coding-agent` | Free | Provider login on first run |
| `mesh-grok`, `grok` | `npm i -g @xai-official/grok` (or the official Windows installer) | Keyed | `XAI_API_KEY` or `grok` login |
| `mesh-muse`, `muse` | WSL2 + Ubuntu, then inside Ubuntu: `curl -fsSL https://dev.meta.ai/install.sh \| bash` | Keyed | `muse login` or `META_API_KEY` |
| `mesh-jules`, `jules` | `npm i -g @google/jules` | Free | `JULES_API_KEY`; async cloud coder |
| `mesh-aider`, `aider` | `python -m pip install --user aider-chat` | Keyed | Needs a provider key in `.env` |

> Note: Hermes and Aider pin conflicting dependency versions in the shared
> per-user Python environment. Installing both can produce pip
> dependency-conflict warnings. Install only the one you use, or isolate each
> with `pipx` or a virtual environment.

The `mesh-` names are the canonical dock entries; the short aliases (`claude`,
`codex`, `cline`, `aider`, `agy`, `grok`, `muse`, etc.) are also exposed where
the app registers them.

### 5.3 Authenticate each agent

Run these once in a normal terminal (the browser terminal works too):

| Agent | Authentication |
|---|---|
| Antigravity | Launch `agy` and complete Google sign-in |
| Claude | `claude` first-run login, or set `ANTHROPIC_API_KEY` |
| Codex | `codex` first-run login, or set `OPENAI_API_KEY` |
| Copilot | `copilot login` (or sign in through the CLI); `gh auth login` for GitHub operations |
| Qwen | `qwen` first-run login |
| OpenCode | Set `OPENCODE_API_KEY` in `.env` |
| MiniMax Code | `mcode login` (or `MINIMAX_API_KEY`) |
| MiniMax Media | `mmx auth login` (or `MINIMAX_API_KEY`) |
| Cline | `cline auth` |
| OpenClaw | First-run login if prompted |
| Hermes | No separate login on the free path |
| Pi | First-run provider login |
| Grok | `grok` login or `XAI_API_KEY` |
| Muse | Inside Ubuntu: `muse login` (or `META_API_KEY`) |
| Jules | Set `JULES_API_KEY` |
| Aider | Configure a provider key in `.env` |

### 5.4 Verify the mesh

From PowerShell:

```powershell
$cli = "$env:LOCALAPPDATA\Programs\LuckyDBrowser\_internal\luckyd-cli.exe"
& $cli doctor
```

Expected results:

- Each installed and authenticated backend shows `ready`.
- Missing binaries show `not installed` with an install hint.
- Installed but unauthenticated keyed backends show `needs_auth`.
- A new install starts at a low count (for example, `1/16`) until you add
  mesh CLIs; that is normal.

Inside the browser terminal, `/doctor` produces the same report.

Caveats:

- **Muse** may show `ready` as soon as `wsl.exe` exists, even if Ubuntu or
  `muse` is missing. Verify by launching the `muse` shell and looking for the
  Muse prompt.
- **Antigravity** is detected when `agy.exe` is on `PATH`; the winget package
  installs it under `%LOCALAPPDATA%\Microsoft\WinGet\Packages` and adds it to
  User `PATH`. The legacy `%LOCALAPPDATA%\agy\bin` location is also checked.
- **Copilot** is the standalone GitHub Copilot CLI (`copilot`), installed by
  winget; it is not the old `gh-copilot` extension.
- **Cline** reports `exhausted` when its free/credit balance is empty; use
  another agent or add credit.
- If a newly installed CLI does not appear, close and reopen the terminal tab,
  then confirm the executable is on `PATH` in a fresh PowerShell window.

---

## 6. Optional extras

### 6.1 MCP servers

LuckyD speaks the standard Claude Desktop MCP config format. It looks for a
config file in these locations, in order:

1. The path in `CODING_AGENT_MCP_CONFIG`
2. `_internal\mcp_config.json`
3. `_internal\.luckyd-code\mcp.json`
4. `%USERPROFILE%\.luckyd\mcp_config.json`

Example `mcp_config.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "C:\\Users\\<you>\\Projects"
      ],
      "enabled": true,
      "timeout": 60
    }
  }
}
```

Restart the terminal after editing. MCP tool calls require approval by default.
Only connect MCP servers you trust: they run as local processes with your
privileges.

### 6.2 Deep research search keys (optional)

The deep-research feature works without premium search by using DuckDuckGo.
For better coverage, add one or both to `.env`:

```dotenv
TAVILY_API_KEY=
BRAVE_API_KEY=
```

### 6.3 LSP and code intelligence (optional)

```powershell
python -m pip install --user jedi
```

Optional Python language servers:

```powershell
python -m pip install --user python-lsp-server
npm i -g pyright
```

### 6.4 Graphify (optional)

```powershell
python -m pip install --user graphifyy
```

Then, in a project folder:

```powershell
graphify extract . --code-only
```

### 6.5 Skills

Bundled skills live in `_internal\skills`. User-installed skills live under
`%USERPROFILE%\.luckyd\skills`. Marketplace installs are two-step: fetch, then
install with approval, and the content hash is verified. Review a skill before
enabling it; skills can instruct the agent.

### 6.6 Google Drive backup (optional)

The Drive backup helper uses an OAuth desktop client and the `drive.file`
scope. Create a desktop OAuth client in Google Cloud, download the client
secret JSON, and run the helper's `auth` subcommand. The token is cached at
`%USERPROFILE%\.luckyd\drive_token.json`. This is optional and unrelated to
the terminal agents.

### 6.7 Hub and Claw services

The browser's Chat page can talk to optional Hub and Claw services. They are
not required for the terminal agents, and the terminal works without them.
Leave `hub_url`, `hub_token`, `claw_url`, and `claw_token` empty unless you are
running those services yourself.

---

## 7. End-to-end verification checklist

- [ ] Browser launches and shows version 10.9.
- [ ] `settings.json` contains non-empty `browser_api_token` and `terminal_token`.
- [ ] `ollama list` shows `llama3.2:3b` (or your chosen model).
- [ ] `Invoke-WebRequest http://127.0.0.1:11434/api/tags` returns JSON.
- [ ] Terminal -> `agent` shows the LuckyD Code prompt.
- [ ] In the agent REPL, `/providers` shows a usable provider and `/doctor` runs.
- [ ] Ask the agent a simple question and get a response.
- [ ] Terminal -> `agent2` starts (Desktop checkout or fallback).
- [ ] Terminal -> `powershell` and `cmd` start.
- [ ] `luckyd-cli.exe doctor` reports your installed mesh backends as ready.
- [ ] Each mesh CLI you installed launches and passes its login.
- [ ] Only after testing: keep Trust mode on `ask` for daily use.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| SmartScreen blocks the installer | Unsigned executable | More info -> Run anyway, or use the portable ZIP. Verify the source |
| Corporate AV blocks setup | Endpoint protection | Use the portable ZIP or ask IT; do not disable AV |
| Browser opens but AI says offline | No provider configured | Install Ollama and pull a model, or add a cloud key in `.env` |
| Ollama installed but not detected | Server not running or PATH stale | Run `ollama serve`, verify `http://127.0.0.1:11434/api/tags`, restart the browser |
| `ollama_setup.ps1` is blocked by execution policy | Downloaded file has Mark-of-the-Web, or policy is Restricted | Rerun it with `-ExecutionPolicy Bypass`; if that is not allowed, install Ollama manually with winget |
| Ollama model is very slow | CPU-only inference | Use `llama3.2:3b` or a free cloud provider |
| Terminal shows `[terminal failed to start]` | CLI path wrong or missing | Clear `terminal_cli` in settings, or point it at `luckyd-cli.exe` |
| Terminal says `too many live sessions` | 6-session cap | Close unused terminal/mesh panes |
| `needs key` for a cloud provider | No API key in `.env` | Add the key, restart the terminal |
| `needs_auth` for a mesh agent | CLI installed but not logged in | Run the login command in section 5.3 |
| `not installed` for a mesh agent | Binary not on PATH | Install it, reopen the terminal, check `where.exe <exe>` |
| npm command not found after install | `%APPDATA%\npm` not on PATH | Reopen the shell/browser; add it to PATH |
| `gh: command not found` | GitHub CLI missing | `winget install --id GitHub.cli -e`, reopen shell |
| Muse shows ready but does not launch | WSL present but Ubuntu/muse missing | `wsl --install -d Ubuntu`, then install Muse inside Ubuntu |
| Update check fails | Release repository unavailable | See the warning in section 1.2; update manually from a trusted source |
| Port already in use | Another local app | Change `browser_api_port` / `terminal_port` in settings and restart |

---

## 9. Security, privacy, and maintenance

- **Localhost is not a security boundary against local malware.** The Control
  API and terminal are protected by random tokens, but any process running as
  your user can read the token files. Run LuckyD on a standard user account and
  do not install untrusted software alongside it.
- **Do not expose the local ports** (9777, 9881, 8000, 9222) through a firewall,
  reverse proxy, or router. They are designed for loopback only.
- **Approve tool calls deliberately.** The agent can read pages, click, type,
  run shell commands, and edit files. Untrusted web content can attempt prompt
  injection. Keep Trust mode on `ask`, and never use `bypassPermissions` while
  browsing untrusted sites.
- **API keys are stored in plaintext** in `.env` and are inherited by child
  terminal processes. Use dedicated keys with spending limits, and remove keys
  you no longer use.
- **The build is unsigned.** Verify downloads. When the release source is
  available again, prefer a checksum-verified installer.
- **Data retention:** history, bookmarks, downloads, sessions, audit logs, and
  schedules are stored locally. Delete `%LOCALAPPDATA%\LuckyDBrowser`,
  `%USERPROFILE%\.luckyd`, and the relevant Ollama models if you want a clean
  slate.
- **Uninstall:** run `unins000.exe` in
  `%LOCALAPPDATA%\Programs\LuckyDBrowser`, then remove the data folders above
  if desired. Uninstalling does not remove Ollama or the mesh CLIs.
- **Back up** `_internal\.env` and `%LOCALAPPDATA%\LuckyDBrowser\settings.json`
  before reinstalling, then re-check tokens and paths after an update.

---

## 10. Appendix: environment variables

Set these in `_internal\.env` unless noted otherwise.

| Variable | Purpose |
|---|---|
| `OLLAMA_MODEL` | Default local model, e.g. `llama3.2:3b` |
| `OLLAMA_HOST` | Ollama server URL, default `http://127.0.0.1:11434` |
| `CODING_AGENT_PROVIDER` | Force a provider (`ollama`, `google`, `openrouter`, `groq`, `cline-usage`, `deepseek`, `openai`, `anthropic`, ...) |
| `CODING_AGENT_MODEL` | Force a model name or `auto` |
| `CODING_AGENT_MAX_TURNS` | Agent loop limit (default 30) |
| `CODING_AGENT_TIMEOUT` | Per-call timeout in seconds |
| `CODING_AGENT_CMD_TIMEOUT` | Shell command timeout in seconds |
| `CODING_AGENT_MCP_CONFIG` | Path to an MCP config file |
| `LUCKYD_TERMINAL_MAX_SESSIONS` | Max concurrent terminal sessions (environment variable, not `.env`) |
| `LUCKYD_CLI` | Override the Agent 1 CLI path |
| `LUCKYD_CLI2` | Override the Agent 2 CLI path |
| `TAVILY_API_KEY` | Optional deep-research search |
| `BRAVE_API_KEY` | Optional deep-research search |
| `DRS_*` | Optional deep-research tuning (see `.env.example`) |

The full commented template is at
`%LOCALAPPDATA%\Programs\LuckyDBrowser\_internal\.env.example\.env.example`.
