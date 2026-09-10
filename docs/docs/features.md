# Features

## The one-window platform

| Surface | What it is | Open it |
|---|---|---|
| **AI Sidebar** | Markdown chat, model picker, visual Q&A, autonomous agent on your **real visible tab** | `Ctrl+Shift+A` |
| **Coding Agent HQ** | Full `luckyd-code` workspace in a tab — 70+ tools, memory, sessions | `Ctrl+Shift+H` |
| **Terminals** | Real Windows ConPTY via xterm.js — agent CLI, PowerShell, CMD, Agent Mesh | `` Ctrl+` `` |
| **Dashboard** | Live new-tab hub: status pills, Ask LuckyD, one-tap tiles, speed dial | New tab |
| **Workflows** | Record Control-API actions, replay with self-healing element matching | Tools → Workflows |

## Daily-driver essentials

- **HTTPS-Only Mode** — public `http://` navigations upgrade to `https://`. Localhost and private LAN addresses are never rewritten (Settings toggle, default on).
- **Site permissions** — camera, mic, location, notifications, pointer lock, and screen capture prompt **once** and remember Allow/Block per origin. Click the lock icon. Incognito never persists.
- **Workspaces** — named tab collections (File → Workspaces, or `Ctrl+K`). Switching saves the current window first.
- **Memory Saver** — idle background tabs freeze at 5 minutes and discard at 15. Pinned, audible, and local HQ/terminal tabs stay awake. Sleeping tabs show 💤.
- **Read Aloud** — `Ctrl+Shift+L` or toolbar 🔊 — speaks the selection or the page via Windows SAPI. Click again to stop.
- **Summarize this page** — `Ctrl+Shift+U` or toolbar ✨ — opens the AI sidebar and summarizes the current tab.
- **Ad-blocker** — document-start userscript scrubs ad placements from player responses; the dashboard 🛡 pill shows live blocked-request counts.
- **Reader Mode** (`Ctrl+Alt+R`) and **Focus Mode** (`Ctrl+Shift+F`) — distill articles, or strip all chrome for pure content.
- **Omnibox AI ask** — start a query with `?` in the address bar to send it straight to the AI sidebar.

## Tab power tools

- **Tab groups** — right-click a tab → Tab Group. Named, colored groups that collapse to a single chip and persist through session restore.
- **AI Tab Organizer** — Tools → Organize Tabs with AI clusters your open tabs by topic and builds the groups for you.
- **Vertical tabs** — an Arc-style left dock (View → Vertical Tabs).
- **Side Pane** — right-click any link → "Open in Side Pane": a docked second web view sharing the profile.
- **Session restore** — "continue where you left off" is the default; incognito windows are never saved.
- **Network monitor** — Tools → Network Monitor: live CDP request log with HAR 1.2 export.
- **Full-page screenshots** — File → Save Full-Page Screenshot captures the whole scrollable document.
- **Close Duplicate Tabs** and **Reopen Previous Session** round out the File/tab menus.

## Personality

- **Synthwave Sunset theme** — the secret fifth theme. Unlock with the Konami code (↑↑↓↓←→←→BA) on the new-tab page, or pick it in Settings.
- **Offline arcade** — the connection-error page is a playable endless-runner while you wait for the network.
- **Scheduled workflows (autopilot)** — every saved workflow gets an interval picker (15m → daily); a background tick replays whatever is due and toasts the result.

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

Next: [AI & Agents →](ai-agents.md)
