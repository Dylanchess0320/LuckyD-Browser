# LuckyD Browser v4.0.0 — Release Notes

> **Frontier.** The 4.0 release hardens every local service: cookie-based
> authentication replaces tokens-in-URLs everywhere, Deep Research rendering
> is XSS-hardened, and the Cline bridge requires a bearer token.

**[⬇ Download `LuckyDBrowserSetup-4.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/tag/v4.0.0)** — Windows 10/11 x64 · per-user install · no admin needed

---

## What's new in 4.0

### 🔐 Cookie-based auth for all local services
The Control API (`127.0.0.1:9777`), Coding Agent HQ (`127.0.0.1:8000`), and the
terminal WebSocket (`127.0.0.1:9881`) used to pass bearer tokens in page source
and WebSocket URLs — visible to any script running in the page. In 4.0:

- The browser provisions **HttpOnly session cookies** (`luckyd_ctl`, `luckyd_hq`,
  `luckyd_term`) on its own profiles at startup, including incognito profiles.
- **No token is ever embedded in served HTML again** — dashboard, mesh,
  research, terminal, and HQ pages carry zero credentials.
- API routes accept the cookie **or** an `Authorization: Bearer` header
  (for external tooling); pages navigated to directly use the cookie.
- Everything still fails closed: missing/empty tokens reject, and the Cline
  bridge refuses to start serving without a configured token.

### 🛡️ Deep Research XSS hardening
Research reports render markdown from untrusted sources. The renderer now
escapes HTML before applying formatting, and links are allowlisted to
`http:`, `https:`, and `mailto:` — `javascript:` and `data:` URLs are
neutralized, and links carry `rel="noopener"`.

### 🔌 Cline bridge authentication
`POST /v1/chat/completions` and `GET /v1/models` on the ClinePass-compatible
proxy (`127.0.0.1:8317`) now require a bearer token. Set `CLINE_BRIDGE_TOKEN`
(or reuse `CODING_AGENT_API_KEY`). `/v1/health` stays public for diagnostics.

### 🧰 Reliability fixes
- `CheckpointManager.undo_to()` can no longer loop forever when a checkpoint's
  file was deleted.
- `undo_last()` consumes checkpoints whose files are already gone instead of
  retrying them.
- The Windows updater exits cleanly on non-Windows systems.
- `CREATE_NO_WINDOW` is guarded so non-Windows process spawns don't crash.
- Bracketed IPv6 loopback (`[::1]:port`) is accepted by the loopback guard.

---

## Upgrading from 3.x
Install `LuckyDBrowserSetup-4.0.0.exe` over your existing install — profile,
settings, and workspaces carry over. The auth changes are internal: your
bookmarks, tabs, and agent sessions work exactly as before, minus the tokens
in page source.

## Verify
```powershell
# After install, the local services answer only with the session cookie —
# a bare request is rejected:
curl http://127.0.0.1:9777/dashboard -s -o NUL -w "%{http_code}`n"   # 401
```
