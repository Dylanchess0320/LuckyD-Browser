# Build with LuckyD — WebMCP

Expose your website's capabilities to LuckyD agents via WebMCP, so agents
can use your site the way a power user would — through real, typed tools
instead of blind clicking.

## What WebMCP is

WebMCP lets a web page expose agent-callable tools (search your catalog,
check order status, book a slot) with declared schemas. A LuckyD agent
discovers them with `WebMCPDiscover` and calls them with `WebMCPCall` —
inside the `browser` permission scope, so the user's trust policy applies
(they can allow, ask, or deny per site).

## Minimal example

```html
<script>
// Expose one tool: check an order's status
window.webmcp = {
  tools: [{
    name: "order_status",
    description: "Look up the status of a customer order",
    parameters: {
      type: "object",
      properties: { order_id: { type: "string" } },
      required: ["order_id"]
    },
    call: async ({ order_id }) => {
      const r = await fetch(`/api/orders/${encodeURIComponent(order_id)}/status`);
      return r.json();
    }
  }]
};
</script>
```

That's it. A LuckyD agent visiting your site can now answer "where's my
order?" by calling your tool instead of scraping your DOM.

## Rules for good WebMCP tools

1. **Typed and narrow.** One tool, one job, declared parameters. If it
   needs no parameters, it probably shouldn't be a tool.
2. **Read-only first.** Expose reads before writes. Writes go through the
   user's approval policy automatically (browser scope defaults to `ask`).
3. **Never expose raw eval.** Tools run with the page's privileges — a
   generic "run JS" tool is an XSS gift box.
4. **Idempotency keys on writes.** Agents retry. `book_slot` called twice
   should book once.
5. **Errors are data.** Return `{ ok: false, error: "..." }` — the agent
   will explain it to the user better than a stack trace will.

## Why build this

Every site with WebMCP tools becomes natively usable by agents without
custom integrations. You build the tools once; every LuckyD user — and
every agent built with this kit — can use your site. That's distribution
you don't have to chase.
