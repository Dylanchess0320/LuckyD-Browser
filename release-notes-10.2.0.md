# LuckyD Browser v10.2.0 — Performance + hardening edition

## What's new

- **Faster agent loop.** Grep, file read/edit, CSV, LSP rename, secrets,
  file watching, diff, and memory compression no longer block the event
  loop; memory decay batches SQL instead of N+1 queries.
- **Safer, Windows-correct process execution.** `ProcessTool` is hardened
  against command injection and runs `echo`, pipes, redirects, and env vars
  the way the agent types them on stock Windows.
- **Green on every box.** Tests needing POSIX utilities (`cat`, `ls`,
  `sleep`, `true`) or optional desktop packages (`mss`, `pyautogui`,
  `pygetwindow`, `pyperclip`) skip cleanly where unavailable; CI stays
  green.
- **Agent 1 + Agent 2 on v10.2.0.** Both mesh slots report
  `LuckyD Code v10.2.0` — terminal env, `lucky-code --version`, CLI help,
  and ACP.

## Two ways to install

- **Setup EXE** — normal Windows installer.
- **Portable ZIP** — no install, runs from any folder. For locked-down PCs.

## Please read this first

- Builds are **unsigned** (no signing certificate yet): Windows SmartScreen
  will show "Unknown publisher", and strict work PCs (Halcyon/IT-managed)
  may block the files until IT allowlists the hash.
- On top of everything from 10.1.0 (OpenCode in the mesh, Gemini refresh,
  Nano Banana + Veo media, 2TB Drive backup).
