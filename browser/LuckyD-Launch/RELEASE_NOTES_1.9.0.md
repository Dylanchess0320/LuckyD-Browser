# LuckyD Browser 1.9.0

**A Chromium-based AI browser for Windows — free, unlimited, offline AI built in.**
No accounts. No API keys. No subscriptions.

`LuckyDBrowserSetup-1.9.0.exe` · Windows 10/11 x64 · per-user install, no admin needed

## Highlights since 1.3.0

### 🗂 Browsing, upgraded
- **Session restore** — "continue where you left off" reopens windows, tabs, pinned state (plus *Reopen Previous Session* backup)
- **Tab groups** — named, colored, collapsible, restored with your session — plus an **AI organizer** that sorts them for you
- **Vertical tabs**, **Focus Mode** (`Ctrl+Shift+F`), **Side Pane** link previews
- **Bookmark bar** (`Ctrl+Shift+B`) with per-site identity tiles
- **Reader Mode** (`Ctrl+Alt+R`), text-fragment links, per-site zoom memory
- Screenshots: viewport (`Ctrl+Shift+S`) **and full-page**
- Downloads: live speed + ETA, pause/resume

### 🤖 Automation platform
- **Workflow recorder/replayer** with self-healing element matching (Tools → Workflows…)
- **Scheduled workflows** — auto-replay every 15m / hourly / daily
- **Network monitor** with live request table + HAR export
- **Multi-terminal tabs** — agent CLI, PowerShell, or CMD, each its own session
- `POST /extract` (schema-guided AI extraction), `POST /theme`, Control API 1.4.0

### ✨ Personality & polish
- 4 themes + secret **Synthwave Sunset** (Konami code on the new-tab page)
- Offline runner arcade on error pages · dashboard greetings · confetti on flawless replays
- Dashboard speed-dial tiles now minted locally — **no favicon service ever sees your shortcuts**
- **One-click auto-update with release notes** (that's how the next one reaches you)

### Fixed
- Auto-update pipeline (was silently broken; now verified end-to-end)
- Userscript engine (built-ins never ran; YouTube ad-block + video speed now live)
- Pin Tab crash on Qt6

**Full changelog:** see [CHANGELOG.md](../blob/main/CHANGELOG.md) (repo sections 2.3.0–2.8.0).
