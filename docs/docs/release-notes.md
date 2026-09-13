# LuckyD Browser — Release Notes

## [9.0.0] — Reliability release

**`LuckyDBrowserSetup-9.0.0.exe`** — Windows 10/11 x64 · per-user install · no admin needed

- **Ollama detection fixed** — local AI is found even when a proxy/VPN is set
  (localhost no longer routed through the proxy), and the chat path bypasses
  it too.
- **Honest AI status** — the dashboard pill now says exactly what's wrong:
  "Ollama not running", "no models pulled", or the working providers.
- **OpenCode Zen updated** — Zen's old keyless $0 tier is gone (every keyless
  call 401s now). Zen registers only with `OPENCODE_API_KEY` and serves its
  current platform catalog; the default model is `gemini-3.5-flash-lite`.
- **DeepSeek Harness boots** — Agent Mesh now spawns `dsh web` (the old
  `--profile web --no-open` invocation is no longer valid).
- **Ctrl+K palette fixed** — the command palette takes keyboard focus again.
- **Readable home page** — shortcut labels get brighter type with text shadow.
- **YouTube colors fixed** — `--force-color-profile=srgb` on the software
  video path (was rendering with wrong BT.601 colorimetry).

On top of everything from 8.0.0.

---

## [8.0.0] — The Cleanup, phase one

**[⬇ Download `LuckyDBrowserSetup-8.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v8.0.0/LuckyDBrowserSetup-8.0.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

v8.0.0 is The Cleanup, phase one: contextual skills move into the AI sidebar,
the model router gets smart, and the home page gets the Neon Night treatment —
on top of everything from 7.0.0.

## What's new in 8.0.0

- **Contextual skill chips** — the 5 bundled skills (`ai-news-brief`, `chess`,
  `graphify`, `movie-picker`, `top-picks`) surface as ✨ chips above the AI
  sidebar input as you type; one tap attaches the skill as chat context.
- **Smart model routing** — `core/router.py`'s ModelRouter is wired into
  `AIBridge.chat()` auto mode: it picks the provider per question. Explicit
  provider picks always win; a non-viable router pick falls back to the
  existing chain.
- **Neon Night home** — the real `/dashboard` and the `newtab.html` fallback
  share one refined typographic system: tabular clock, small-caps labels,
  system-only font stack, fully offline-safe, `prefers-reduced-motion`.
- **Honest AI labels + $0 fallback** — provider chips say `· free tier` /
  `· credit-billed ⚠`; with no Ollama and no keys, chat falls back to the
  free OpenCode Zen gateway instead of an empty-token provider.
- **Antigravity decluttered** — redundant Tools-menu and command-palette
  launchers removed; it's already in the terminal.
- **Copy freshness** — README, docs, `.github`, and in-app text brought
  current; About dialog and window titles now say "LuckyD".
- **Version unified to 8.0.0** everywhere — package, PyInstaller/Inno metadata,
  `LuckyDBrowser/8.0` user-agent, and docs — guarded by a regression test so it
  can't drift again.
- **465 automated tests passing**, `ruff check` clean.

## Upgrade

Install `LuckyDBrowserSetup-8.0.0.exe` over your existing install — profile,
bookmarks, and settings are preserved.

## Verify

```powershell
# After install, the app reports 8.0.0 in Help → About and in the console banner.
```

<details>
<summary>Previously: 7.0.0 — Agentic</summary>

v7.0.0 was the agentic frontier: an AI browser you can trust with real agency.
It added a trust foundation that keeps receipts on everything the agents do —
a **`/trust` dashboard**, permission scopes and risk levels, secret redaction,
and an append-only audit log — plus **WebMCP** tool bindings for websites,
**scheduled background agents** with a morning digest, and an **open Skills
marketplace**, all shipped with the official Windows installer.

Also in 7.0.0: the legacy blanket auto-approve bypasses
(`hook.auto_approve_all`, `CODING_AGENT_AUTO_APPROVE`/`CODING_AGENT_YOLO`,
`--auto-approve`/`--yolo`) became inert (deprecation warning, treated as OFF);
schedule-dashboard routes were gated through the approval hook; scheduled and
interactive agent runs serialize through a shared cross-process run lock;
Lucky identity (named apprentice voice, time-of-day greetings); slim chrome;
correct colors on older GPUs.

</details>

---

*Full changelog: [CHANGELOG.md](changelog.md)*
