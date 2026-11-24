# LuckyD Browser v10.9.2 - Release Notes

> **Paste an API key in either app — no more editing `.env` by hand.**
> `/key` in LuckyD Code and in the browser AI Assistant saves, verifies, and shares the key instantly.

**[⬇ Download `LuckyDBrowserSetup-10.9.2.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9.2/LuckyDBrowserSetup-10.9.2.exe)** - Windows 10/11 x64 · per-user install · no admin needed

**[`LuckyDBrowser-Portable-10.9.2.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9.2/LuckyDBrowser-Portable-10.9.2.zip)** - unzip and run, for locked-down PCs.

---

## What's new in 10.9.2

- **`/key` in LuckyD Code**
  - `/key openrouter` asks for the key with hidden input, verifies it against the provider, and saves it to the shared per-user `.env` (`%LOCALAPPDATA%/LuckyDBrowser/.env`).
  - `/keys` lists the providers you have configured, with keys masked.
  - `/key <provider> <key>` works inline for scripts.
  - No restart is required: switch with `/model openrouter` and go.

- **`/key` in the browser AI Assistant**
  - Type `/key` in the chat input, or use **Actions → 🔑 Add API key…**, to paste a key without leaving the browser.
  - The provider picker refreshes immediately and the key is ready to use.

- **One key store for both apps**
  - LuckyD Code and the browser read the same per-user file, so a key pasted in either surface connects both.
  - Existing keys keep working; upgrades never overwrite them.

- **Browser Control API**
  - New `GET /keys` (masked list) and `POST /keys` (save + verify) endpoints for scripted setup.

- **Reliability**
  - Fixed the CI quality gate (ruff format, unused `noqa`, mypy) so the full pipeline runs again.
  - Corrected OpenRouter catalog cache typing and synced stale installer/User-Agent/agent labels to 10.9.2.

## Connecting OpenRouter

1. Open LuckyD Code and run `/key openrouter` (or type `/key` in the browser AI Assistant).
2. Paste a key from [openrouter.ai/keys](https://openrouter.ai/keys).
3. LuckyD verifies it and saves it to the shared `.env`.
4. Run `/model openrouter` in Code, or pick OpenRouter in the Assistant — `openrouter/free` is pinned first.

## Upgrading

Run `LuckyDBrowserSetup-10.9.2.exe` over your existing install — profiles, tabs, settings, workspaces, and stored keys are preserved automatically.
