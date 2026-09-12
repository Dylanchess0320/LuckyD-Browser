# LuckyD Browser v7.0.0 — Release Notes

**[⬇ Download `LuckyDBrowserSetup-7.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v7.0.0/LuckyDBrowserSetup-7.0.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

v7.0.0 is the agentic frontier of LuckyD Browser: an AI browser you can trust
with real agency. It adds a trust foundation that keeps receipts on everything
the agents do — a **`/trust` dashboard**, permission scopes and risk levels,
secret redaction, and an append-only audit log — plus **WebMCP** tool bindings
for websites, **scheduled background agents** with a morning digest, and an
**open Skills marketplace**, all shipped with an official Windows installer.

## What's new in 7.0.0

- **Trust dashboard** (`/trust`) — "agentic with receipts": permission scopes
  and risk levels for every agent action, an approval queue, secret redaction
  in outputs, and an append-only audit log. Review everything the agents did.
- **Trust hardening** — the legacy blanket auto-approve bypasses
  (`hook.auto_approve_all`, `CODING_AGENT_AUTO_APPROVE`/`CODING_AGENT_YOLO`,
  `--auto-approve`/`--yolo`) are now inert (deprecation warning, treated as
  OFF). Approvals may only be skipped when the trust policy explicitly
  authorizes it (policy mode, per-scope/site rules, or the explicit per-run
  auto-approve-low-risk mode), and every skip is recorded in the audit log.
  Schedule-dashboard routes (create/update/delete/pause/run-now) are gated
  through the approval hook (HTTP 403 when approval is required), and
  scheduled and interactive agent runs serialize through a shared
  cross-process run lock so they can never collide.
- **WebMCP** — websites can expose typed agent tools to LuckyD via a JS shim
  (native support wins when present): tool registration, aliases, permission
  levels, and schema validation — hardened so a hostile page can't abuse it.
  Tool arguments are validated server-side against the schema captured at
  discovery, every dispatch carries a 30-second timeout, and an opaque
  rotating binding token ties registered tools to the page's origin: calls
  with a missing, forged, or cross-origin token are refused, and stale tool
  registrations are purged when the tab navigates cross-origin.
- **Scheduled/background agents** — cron-scheduled agents that run while you
  rest, with a **morning digest** of their runs. Manage them at
  **`/schedules`** (`python main.py schedule ...`).
- **Open Skills marketplace** — skill registries (bundled + remote) with
  hash-verified **install/update/remove/publish**; tampered or unparseable
  skills are refused.
- **Official Windows installer** (`LuckyDBrowserSetup-7.0.0.exe`), built on a
  real Windows runner with Inno Setup 6. Per-user install, no admin required.
  Silent install: `LuckyDBrowserSetup-7.0.0.exe /VERYSILENT /NORESTART`.
- **Version unified to 7.0.0** everywhere — package, PyInstaller/Inno metadata,
  `LuckyDBrowser/7.0` user-agent, and docs — guarded by a regression test so it
  can't drift again.
- **428 automated tests passing**, `ruff check` and `ruff format --check`
  clean, CI green — including an overnight hardening sweep of 32 bug fixes,
  each with a regression test (research citation handling, streaming/CLI/shell
  edge cases, memory and file-tool robustness).

## Everything from 5.0.0 — Final

- **Official Windows installer** (introduced in 5.0) — per-user install, no
  admin required, silent-install flags supported.
- **Version unified in 5.0** — package, installers, user-agent strings, and
  docs, guarded by the version-unification regression test.
- **Codebase fully Ruff-formatted** — the whole tree passes `ruff check` and
  `ruff format --check`.

## Upgrade

Install `LuckyDBrowserSetup-7.0.0.exe` over your existing install — profile,
bookmarks, and settings are preserved.

## Verify

```powershell
# After install, the app reports 7.0.0 in Help → About and in the console banner.
```

---

*Full changelog: [CHANGELOG.md](changelog.md)*
