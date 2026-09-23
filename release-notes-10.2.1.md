# LuckyD Browser v10.2.1 — Dashboard cleanup + HQ auto-rotation

## What's new

- **Cleaner home dashboard.** The four AI preset chips (AI News Digest,
  Agent Architecture, Python Automation, Security Checklist) are removed —
  the dashboard is back to search, Ask Lucky, apps, and shortcuts.
- **Ask Lucky actually works.** The dashboard's Ask mode now shows the real
  error when something fails (instead of a bare HTTP status), won't
  double-fire while thinking, and picks up newly added keys or a freshly
  started Ollama without a browser restart.
- **LuckyD HQ auto-rotates to a working model.** When the configured model
  is retired (400/404), rate-limited (429), or the gateway is down (5xx),
  HQ transparently rotates — same-gateway models first, then the provider
  default, then the Cline free tier — and pins the winner. Also fixed:
  OpenCode Zen's dead `nemotron-3-ultra-free` default is now
  `gemini-3.5-flash-lite`.

## Two ways to install

- **Setup EXE** — normal Windows installer.
- **Portable ZIP** — no install, runs from any folder. For locked-down PCs.

## Please read this first

- Builds are **unsigned** (no signing certificate yet): Windows SmartScreen
  will show "Unknown publisher", and strict work PCs (Halcyon/IT-managed)
  may block the files until IT allowlists the hash.
- On top of everything from 10.2.0 (async non-blocking tools, hardened
  ProcessTool, Agent 1 + Agent 2 unified).
