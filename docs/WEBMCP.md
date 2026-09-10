# WebMCP in LuckyD — the Agentic Web, early

LuckyD 6.0 speaks **WebMCP** (Web Model Context Protocol), the open standard
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
| `WebMCPDiscover` | Lists a page's tools: name, description, typed parameters, annotated forms. Read-only. | always allow |
| `WebMCPCall` | Calls a named tool with structured JSON args. Runs the site's handler in the page. | requires approval |
| `WebMCPShim` | Injects LuckyD's `navigator.modelContext` polyfill (`static/webmcp-shim.js`) on pages without native support, and auto-registers annotated forms. | normal |

All three live in the **browser** permission scope, so they show up in the
Trust Center (`/trust`) and every call is audit-logged.

## Demo (no WebMCP site needed)

The shim makes any page WebMCP-capable, so you can try the full loop today:

1. Agent: `BrowserNavigate` to any page with a form (or a blank page).
2. Agent: `WebMCPShim` — polyfill injected.
3. Agent: `WebMCPDiscover` — annotated forms appear as tools.
4. Agent: `WebMCPCall` with the form's fields — the shim fills the form,
   fires `input`/`change` events, and reports what would be submitted
   (it never navigates away while the agent is driving).

On a real WebMCP site (or any page whose JS calls `registerTool()`),
step 2 is unnecessary — discovery just works.

## Security model

- **Discovery is read-only** and always allowed; it only reads tool schemas.
- **Calling a tool executes the site's JavaScript in the page** — treated as
  browser control: it goes through the normal approval flow (ask / session /
  always / per-site) and is recorded in the audit log.
- Argument validation happens twice: the shim validates against the tool's
  schema in-page, and LuckyD validates the JSON before sending.
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
