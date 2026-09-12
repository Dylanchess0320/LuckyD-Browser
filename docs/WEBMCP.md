# WebMCP in LuckyD — the Agentic Web, early

LuckyD speaks **WebMCP** (Web Model Context Protocol), the open standard
proposed at Google I/O 2026 for the "Agentic Web". Instead of scraping the DOM
and simulating clicks, sites expose structured tools — and LuckyD's agent
calls them directly.

## The standard in 30 seconds

- **Imperative API** — sites call `navigator.modelContext.registerTool()`
  with a name, natural-language description, typed parameters (JSON Schema
  style), and a handler. Like OpenAI/Anthropic function calling, but the
  handler runs client-side in the page.
- **Declarative API** — existing HTML forms are annotated so agents can use
  them without custom JS. LuckyD's convention (provisional — the declarative
  half of the spec is still being written):
  `<form data-webmcp-tool="tool_name" data-webmcp-description="...">`.
- Gated by the `tools` Permissions Policy; origin-isolated. Chrome 149 ships
  the origin trial; Microsoft co-develops it through the W3C Web ML community
  group. Expedia, Booking.com, Shopify, Target and others are experimenting.

Sources: [Developers Digest overview](https://www.developersdigest.tech/blog/webmcp-google-browser-agent-standard-2026),
[VentureBeat](https://venturebeat.com/infrastructure/google-chrome-ships-webmcp-in-early-preview-turning-every-website-into-a),
[SD Times on I/O 2026](https://sdtimes.com/ai/google-i-o-2026-introduces-the-agentic-web-era-with-major-chrome-updates/).

## What LuckyD implements

| Tool | What it does | Permission |
|---|---|---|
| `WebMCPDiscover` | Lists a page's tools: name, description, typed parameters, annotated forms. Read-only. Returns a **binding token** for the page's origin. | always allow |
| `WebMCPCall` | Calls a named tool with structured JSON args. Runs the site's handler in the page. Requires the binding token from `WebMCPDiscover` for the current origin. | requires approval |
| `WebMCPShim` | Injects LuckyD's `navigator.modelContext` polyfill (`static/webmcp-shim.js`) on pages without native support, and auto-registers annotated forms. Also returns a binding token like `WebMCPDiscover`. | normal |

All three live in the **browser** permission scope, so they show up in the
Trust Center (`/trust`) and every call is audit-logged. Permission levels
are enforced by the agent loop's approval hook (`core/approval_hook.py`):
`WebMCPCall` cannot run without passing the trust policy (ask / session /
always / per-site) even though the binding check below also gates it.

## Demo (no WebMCP site needed)

The shim makes any page WebMCP-capable, so you can try the full loop today:

1. Agent: `BrowserNavigate` to any page with a form (or a blank page).
2. Agent: `WebMCPShim` — polyfill injected.
3. Agent: `WebMCPDiscover` — annotated forms appear as tools, along with a
   **binding token** for the page's origin.
4. Agent: `WebMCPCall` with the form's fields **and the binding token** —
   the shim fills the form, fires `input`/`change` events, and reports what
   would be submitted (it never navigates away while the agent is driving).

On a real WebMCP site (or any page whose JS calls `registerTool()`),
step 2 is unnecessary — discovery just works.

## Tab/origin binding

The core threat: a page on origin X must not discover or invoke tools
registered by origin Y, nor impersonate another page's identity. LuckyD
binds every discovery to the page's origin (scheme + host + port):

- **Discovery is scoped to the calling tab's origin.** `WebMCPDiscover`
  records each tool's name and parameter schema under the current page's
  origin and returns an opaque, unguessable **binding token** for it. The
  token rotates on every discovery, so stale tokens die immediately.
- **Calls must carry the token.** `WebMCPCall` takes a `binding_token`
  parameter and, before dispatching, verifies server-side (with
  constant-time comparison) that the token matches the *current* page's
  origin **and** that the named tool was discovered on that origin. A
  missing, forged, or cross-origin token is refused without touching the
  page — as is a tool name that was never discovered on this origin.
- **Stale registrations are purged.** When the tab navigates cross-origin
  (or a new origin is discovered), the previous origin's registration is
  dropped, so tools from a page you left can never be invoked later.
- Origin-less pages (`about:blank`, `data:` URLs) bind to their URL
  instead of an origin — there is no origin to confuse there.

The token is an agent-side secret: it is generated with
`secrets.token_urlsafe`, never exposed to page JavaScript, and checked
only in Python. Bindings are keyed by **(tab, origin)**: a binding token
discovered in one tab is refused in another, so a token can never leak
across tabs.

## Security model

- **Discovery is read-only** and always allowed; it only reads tool schemas.
  Page-supplied discovery data is coerced defensively (non-object results,
  malformed tool entries, and oversized payloads are dropped or capped) so a
  hostile page can't crash the agent or poison the registry.
- **Calling a tool executes the site's JavaScript in the page** — treated as
  browser control: it goes through the normal approval flow (ask / session /
  always / per-site) and is recorded in the audit log. On top of that, the
  origin binding above must check out or the call is refused.
- **Argument validation happens twice:** the shim validates against the
  tool's schema in-page, and LuckyD validates the JSON arguments
  server-side against the schema captured at discovery — before anything
  is dispatched to the page. A native (non-shim) page that skips validation
  still can't receive malformed input.
- **Dispatch has a 30-second timeout**, so a malicious or broken page
  handler can't hang the agent. Page handler errors propagate back to the
  agent as error results; page return values are serialized defensively
  and truncated.
- The shim never exfiltrates: it only fills forms and reports submissions.
- Threat model caveat (shared with the spec itself): a malicious page could
  register misleading tools. LuckyD shows the tool's origin page and
  description in the approval prompt — approve tools on sites you trust,
  exactly like you'd trust the site's buttons.

## For site developers

Expose `registerTool()` in your page JS (or annotate forms with
`data-webmcp-tool`) and LuckyD agents — and eventually every WebMCP-capable
agent — can use your site structurally. No LuckyD-specific code needed:
the shim implements the proposal's surface, and native browser support
takes precedence automatically.
