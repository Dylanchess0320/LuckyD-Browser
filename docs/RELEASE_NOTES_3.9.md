# LuckyD Browser v3.9.0 — Daily Driver

**Windows 10/11 x64 · per-user install · no admin**

[Download `LuckyDBrowserSetup-3.9.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v3.9.0)

LuckyD 3.9 is the daily-driver release: a Chromium AI browser you can actually live in, with local unlimited AI, a coding agent, and real terminals — plus the security and tab hygiene of a modern browser.

## Highlights

- **HTTPS-Only Mode** — public `http://` main-frame navigations upgrade to `https://`. Localhost and private LAN addresses are never rewritten. On by default; toggle in Settings.
- **Per-site permissions** — camera, microphone, location, notifications, pointer lock, and screen capture prompt once and remember Allow/Block. Click the lock icon in the status bar, or **Tools → Site Permissions**. Incognito never persists.
- **Workspaces** — named tab collections under **File → Workspaces** and the command palette (`Ctrl+K`). Switching saves the current window into the active workspace first.
- **Memory Saver** — idle background tabs freeze after 5 minutes and discard after 15. Pinned, audible, current, and local platform tabs (dashboard / HQ / terminal) stay awake. Sleeping tabs show a 💤 prefix and wake on click.
- **Read Aloud** — `Ctrl+Shift+L` or toolbar 🔊 speaks the selection or the page via Windows SAPI. Click again to stop.
- **Summarize This Page** — `Ctrl+Shift+U` or toolbar ✨ opens the AI sidebar and summarizes the current tab.

## Install

1. Run `LuckyDBrowserSetup-3.9.0.exe`
2. Leave **Set up free unlimited local AI** checked if you want Ollama + `llama3.2:3b`
3. Open the sidebar with `Ctrl+Shift+A`

Silent: `LuckyDBrowserSetup-3.9.0.exe /VERYSILENT /NORESTART`

Installs to `%LOCALAPPDATA%\Programs\LuckyDBrowser` with Start Menu + optional desktop shortcut.

## Also in this lineage

Deep Research swarm (`Ctrl+Shift+R`), Agent Mesh, self-healing workflows, session restore, tab groups, Reader Mode, and one-click in-app updates.
