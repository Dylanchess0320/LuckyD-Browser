# Getting Started

## Requirements

- **Windows 10/11 x64** — LuckyD Browser is a Windows desktop app
- No admin rights needed — the installer is per-user
- ~2 GB extra if you want the free local AI bundle (Ollama + `llama3.2:3b`)

## Install the browser

1. Download [**LuckyDBrowserSetup-5.0.0.exe**](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v5.0.0/LuckyDBrowserSetup-5.0.0.exe)
2. Run it — installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser`
3. Leave **"Set up free unlimited local AI"** checked to get Ollama + `llama3.2:3b` in one shot

Silent install for scripts and fleets:

```powershell
LuckyDBrowserSetup-5.0.0.exe /VERYSILENT /NORESTART
```

## First launch in 60 seconds

| Step | Do this |
|---|---|
| Open the AI sidebar | `Ctrl+Shift+A` — chat works offline with the local model |
| Summarize any page | `Ctrl+Shift+U` or the ✨ toolbar button |
| Try Deep Research | `Ctrl+Shift+R` — ask a hard question, get a cited report |
| Open a terminal | `` Ctrl+` `` — a real ConPTY shell, in a tab |
| Meet the coding agent | `Ctrl+Shift+H` — the full agent HQ in a tab |
| Command palette | `Ctrl+K` — every action, fuzzy-searchable |

## Bring your own keys (optional)

Local Ollama is the default and needs nothing. To use cloud models, add keys in Settings — supported providers:

Gemini · Groq · DeepSeek · OpenAI · Anthropic · Z.ai · OpenRouter · Cline · OpenCode

Prompts only leave your machine when *you* pick a cloud provider.

## Build from source

```powershell
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
pip install -r browser\requirements.txt
browser\run_browser.bat

# Shareable installer (needs Inno Setup 6)
powershell -NoProfile -ExecutionPolicy Bypass -File browser\installer\build_installer.ps1
# → browser\installer\output\LuckyDBrowserSetup-5.0.0.exe
```

## Upgrading

Install the new `LuckyDBrowserSetup-5.0.0.exe` right over your existing install — profile, settings, and workspaces carry over. The browser also checks for updates itself and can install them in-app with one click.

Next: [Features →](features.md)
