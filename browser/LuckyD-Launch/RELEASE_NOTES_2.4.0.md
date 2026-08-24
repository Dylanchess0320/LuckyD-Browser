# LuckyD Browser v2.4.0 — Release Notes

> **The AI browser that puts automation, AI agents, and developer tools in one window.**
> Record workflows, run parallel terminals, extract structured data, and drive your browser with AI—all offline and private by default.

**[⬇ Download `LuckyDBrowserSetup-2.4.0.exe`](../../releases/tag/v2.4.0)** — Windows 10/11 x64 · per-user install · no admin needed

---

## The pitch in 30 seconds

**LuckyD Browser 2.4.0** unites AI automation, workflow recording, and developer tools—all running in your browser. Record a sequence of clicks → replay it with intelligent element matching. Ask AI to structure any webpage into JSON. Run multiple terminals (PowerShell, CMD, LuckyD Agent) in tabs. Drive your visible browser with an AI agent while you watch. Everything runs offline and private by default.

---

## What's inside

🤖 **AI Sidebar** (`Ctrl+Shift+A`) — Markdown chat, model picker, page-aware Q&A, 📷 visual analysis, and an autonomous agent that controls your real, visible browser tab.

⚡ **Multi-Terminal Workspace** (`Ctrl+Shift+T`) — Open independent LuckyD Agent, PowerShell, or CMD sessions as browser tabs. Real Windows ConPTY (xterm.js), one click from the dashboard.

🎬 **Workflow Recorder & Replay** — Record browser interactions (clicks, typing, scrolling). Replay workflows with resilient element matching—updates gracefully when page layouts change.

📊 **Structured Data Extraction** — Ask your AI provider to turn any visible webpage into JSON. Automate data scraping and reporting without writing code.

💻 **Coding Agent HQ** — Full `luckyd-code` CLI with 70+ tools, memory graph, sessions, and background tasks. Mirrors the sidebar's AI provider settings.

🌐 **A real daily-driver browser** — Tabs, bookmarks (import/export), history, downloads, incognito, ad/tracker blocker, find-in-page, themes, command palette, print-to-PDF, AI right-click actions (Explain / Summarize / Translate / Extract), copy-as-Markdown.

🎨 **Personal touches** — Synthwave Sunset theme, live theme switching, site letter-tile icons, an offline runner game, and keyboard shortcuts for everything.

🔒 **Private by default** — local-first AI, loopback-only APIs, no telemetry, no keys shipped in the bundle.

## Reliability improvements

- ✅ Fixed the built-in userscript engine so bundled scripts (YouTube ad blocking, Video Speed Controller) run correctly.
- ✅ Repaired the in-app update flow—updates now install cleanly through the Windows installer and restore your browsing session.
- ✅ Improved build reliability and expanded browser self-tests for faster, more stable releases.
- ✅ Enhanced workflow replay accuracy with intelligent element matching.

---

## Download & Checksums

| File | Size | SHA-256 |
|---|---|---|
| `LuckyDBrowserSetup-2.4.0.exe` | 176.3 MB | *(generate on upload)* |

[↓ Get LuckyDBrowserSetup-2.4.0.exe](../../releases/tag/v2.4.0)

---

## Tips & tricks

- **Better AI answers on GPU machines:** `ollama pull qwen3:8b` or `ollama pull mistral`
- **Vision mode (screenshots & tab-driving):** `ollama pull gemma3:4b`
- **Video playback at custom speeds:** Use the bundled Video Speed Controller from the dashboard.
- **Workflow sharing:** Export workflows from the dashboard and share with teammates—they'll replay with their AI providers.
- **Keyboard power user?** Open the command palette (`Ctrl+Shift+P`) to find shortcuts for terminals, workflows, screenshots, and bookmarks.

---

**Full source, docs & changelog:** [github.com/Dylanchess0320/LuckyD-Browser](https://github.com/Dylanchess0320/LuckyD-Browser) · MIT © DylanChess03

