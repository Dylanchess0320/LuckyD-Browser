# LuckyD Browser v5.0.0 — Release Notes

**[⬇ Download `LuckyDBrowserSetup-5.0.0.exe`](../releases)** — Windows 10/11 x64 · per-user install · no admin needed

v5.0.0 is the final, maxed-out release of LuckyD Browser. It takes everything
that made 4.0 "Frontier" — the security hardening, the reliability fixes —
and ships it the way a finished product should ship: with an official
installer, a fully formatted and linted codebase, green CI, and complete docs.

## What's new in 5.0.0

- **Official Windows installer** (`LuckyDBrowserSetup-5.0.0.exe`), built on a
  real Windows runner with Inno Setup 6. Per-user install, no admin required.
  Silent install: `LuckyDBrowserSetup-5.0.0.exe /VERYSILENT /NORESTART`.
- **Version unified to 5.0.0** everywhere — package, PyInstaller/Inno metadata,
  `LuckyDBrowser/5.0` user-agent, and docs — guarded by a regression test so it
  can't drift again.
- **Codebase fully Ruff-formatted** — the whole tree passes `ruff check` and
  `ruff format --check`; CI is green.

## Everything from 4.0.0 — Frontier

- **Cookie-based auth for all local services** — Control API (`:9777`), Coding
  Agent HQ (`:8000`), and terminal WebSocket (`:9881`) authenticate via HttpOnly
  session cookies. No credential is ever embedded in served HTML.
- **Deep Research XSS hardening** — report markdown is HTML-escaped before
  formatting; link URLs are allowlisted to `http:`, `https:`, `mailto:`.
- **Cline bridge bearer auth** — `/v1/models` and `/v1/chat/completions`
  require a token and fail closed when none is configured.
- **SSRF protection** with IP pinning and redirect revalidation on the research
  fetcher; consolidated command blocklist.
- **Nav pages require auth** — `/dashboard`, `/research`, `/mesh`, `/terminal`
  return 401 without the session cookie.
- Reliability fixes across terminal sessions, settings persistence, the tile
  registry, and CDP; checkpoint-undo infinite-loop fix; Windows updater
  portability fixes.
- **189 automated tests passing**, including 14 new 4.0 security regression
  tests (XSS tests execute the real served `parseMarkdown` in Node).

## Upgrade

Install `LuckyDBrowserSetup-5.0.0.exe` over your existing install — profile,
bookmarks, and settings are preserved.

## Verify

```powershell
# After install, the app reports 5.0.0 in Help → About and in the console banner.
```

---

*Full changelog: [CHANGELOG.md](../CHANGELOG.md)*
