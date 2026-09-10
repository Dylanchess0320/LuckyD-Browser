# Security

LuckyD is local-first: your browsing, your models, your agents — on your machine. Here's how 6.0 protects that.

## Cookie-based auth for local services (4.0)

The Control API (`127.0.0.1:9777`), Coding Agent HQ (`127.0.0.1:8000`), and terminal WebSocket (`127.0.0.1:9881`) used to pass bearer tokens in page source and WebSocket URLs — visible to any script running in the page. In 4.0:

- The browser provisions **HttpOnly session cookies** (`luckyd_ctl`, `luckyd_hq`, `luckyd_term`) on its own profiles at startup — including incognito profiles
- **No token is ever embedded in served HTML again** — dashboard, mesh, research, terminal, and HQ pages carry zero credentials
- API routes accept the cookie **or** an `Authorization: Bearer` header (for external tooling); navigated pages use the cookie
- Everything fails closed: missing or empty tokens are rejected

**Nav pages require auth** — `/dashboard`, `/research`, `/mesh`, and `/terminal` return **401** without the session cookie.

## Loopback-only by design

- The Control API and terminal bridge **bind 127.0.0.1 only** — nothing is reachable from your LAN or the internet
- **Loopback origin enforcement** with DNS-rebinding `Host` checks (bracketed IPv6 `[::1]` handled)
- Per-profile tokens; minimal `Content-Security-Policy` headers on local HTML surfaces

## Research rendering hardened (4.0)

Deep Research reports render markdown from untrusted sources. The renderer escapes HTML before applying formatting, and links are allowlisted to `http:`, `https:`, and `mailto:` — `javascript:` and `data:` URLs are neutralized, links carry `rel="noopener"`.

## Cline bridge auth (4.0)

`POST /v1/chat/completions` and `GET /v1/models` on the ClinePass-compatible proxy (`127.0.0.1:8317`) require a bearer token (`CLINE_BRIDGE_TOKEN`, falling back to `CODING_AGENT_API_KEY`) with constant-time comparison — and the bridge **fails closed** when no token is configured. `/v1/health` stays public for diagnostics.

## Everyday privacy

- **Local-first Ollama** — prompts never leave the machine unless you pick a cloud provider
- **HTTPS-Only Mode** — public `http://` upgrades to `https://` (localhost/LAN never rewritten)
- **Per-site permissions** — camera, mic, location, notifications, pointer lock, screen capture prompt once per origin; incognito never persists
- **No telemetry, no bundled API keys, incognito writes nothing to disk**

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, open a [private security advisory](https://github.com/Dylanchess0320/LuckyD-Browser/security/advisories/new) on the repository with:

- A description of the vulnerability and steps to reproduce
- The potential impact and any suggested fixes

## Verifying the 4.0 auth model

```powershell
# A bare request to the Control API is rejected — only the session cookie gets in:
# (curl.exe, not the `curl` alias — on Windows PowerShell `curl` is Invoke-WebRequest)
curl.exe http://127.0.0.1:9777/dashboard -s -o NUL -w "%{http_code}`n"   # 401
```

Next: [FAQ →](faq.md)
