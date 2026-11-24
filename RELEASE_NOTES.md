# LuckyD Browser v10.9.1 - Release Notes

> **New-user auto-setup restored + full live OpenRouter model catalog in LuckyD Code.**
> Zero-friction first launch, live access to 400+ OpenRouter models, and shared credential support.

**[⬇ Download `LuckyDBrowserSetup-10.9.1.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9.1/LuckyDBrowserSetup-10.9.1.exe)** - Windows 10/11 x64 · per-user install · no admin needed

**[`LuckyDBrowser-Portable-10.9.1.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9.1/LuckyDBrowser-Portable-10.9.1.zip)** - unzip and run, for locked-down PCs.

---

## What's new in 10.9.1

- **Restored 10.8.9-style new-user auto-setup:**
  - Installs are ready at first launch out of the box.
  - The installer includes an "agentsetup" task (checked by default) that signals first-run readiness via `.first-run-setup`.
  - On first launch, the browser runs the bundled idempotent `LuckyD-Bootstrap.ps1` (`-Groups core,free,ollama`) with `-ExecutionPolicy RemoteSigned` in its own console window so progress is transparent while the browser remains responsive.
  - Keeps installer `[Run]` safe and compliant with behavioral anti-malware (no PowerShell in `[Run]`, no `-ExecutionPolicy Bypass`).
  - Start Menu shortcuts ("Agent Setup", "Set up Local AI", "Install Agent Mesh CLIs") remain available for re-running setup at any time.

- **Full live OpenRouter support in LuckyD Code:**
  - LuckyD Code now matches the browser AI assistant's live model picker, offering the complete live OpenRouter catalog (400+ models) with disk cache fallback and `openrouter/free` pinned first.
  - Free and paid models are clearly categorized in `/model`.
  - Instant exact-model matching and fuzzy resolution across all OpenRouter models via `/model <name>` or `/model openrouter <name>`.

- **Shared credential loading:**
  - Shared credentials and provider configs in `%LOCALAPPDATA%\LuckyDBrowser\.env` are seamlessly loaded by both LuckyD Code and the browser.
  - Real API keys survive application updates and reinstalls without ever being bundled into public release binaries.

- **Performance & workspace cleanup:**
  - Fast startup, responsive fuzzy matching, eliminated cache bloat, and optimized terminal session handling.

## Upgrading

Run `LuckyDBrowserSetup-10.9.1.exe` over your existing install — profiles, tabs, settings, workspaces, and stored keys are preserved automatically.
