---
name: trust-guard
description: Wrap any agent's tool calls with LuckyD's trust layer — permission scopes, approval policy, secret redaction, and an append-only audit log. Use when building or hardening an AI agent that executes tools.
---

# Trust Guard — agentic with receipts

Give any tool-calling agent LuckyD's trust foundation in ~20 lines. No LuckyD install required; the `lucky_kit` package in this repo is stdlib-only.

## The pattern

Every tool call passes through three gates before it executes:

1. **Scope it** — `scope_of(tool_name)` maps the tool to a human-meaningful
   capability (`read`, `files`, `shell`, `browser`, `git`, `desktop`,
   `memory`, `agents`, `mcp`, `network`, `system`). Show the user *what the
   agent can touch*, not a 100-tool list.
2. **Decide** — `ApprovalEngine.decide(tool, args)` returns
   `allow` / `ask` / `deny` from the persistent `TrustPolicy`
   (global mode + per-scope + per-site rules). Fail closed: no approver
   available means deny.
3. **Receipt it** — `AuditLog.record(...)` writes every call (tool, redacted
   args, scope, risk, decision, duration, outcome) to append-only JSONL.
   `redact_args` masks secret-looking values first, so credentials never
   land in the log.

## Minimal integration

```python
from lucky_kit import ApprovalEngine, AuditLog, describe_decision
import time

engine = ApprovalEngine()
audit = AuditLog()

def guarded_call(tool, args, execute, ask_user):
    t0 = time.time()
    result, _ = engine.decide(tool, args), None
    if result.decision == "ask":
        approved = ask_user(describe_decision(tool, args))
        decision = "approved" if approved else "denied"
    else:
        decision = "auto" if result.decision == "allow" else "blocked"
    output, ok = None, True
    if decision in ("approved", "auto"):
        try:
            output = execute()
        except Exception as e:
            ok, output = False, str(e)
    audit.record(tool, args, decision=decision,
                 duration_ms=(time.time() - t0) * 1000, ok=ok)
    return output
```

## Rules

- **Redact before you log.** Always pass raw args through `redact_args`
  (or `AuditLog.record`, which does it for you). The audit log must never
  contain passwords, tokens, or API keys.
- **Scopes are yours to extend.** Add your tools to `TOOL_SCOPE_OVERRIDES`
  in `lucky_kit/trust.py`. Unknown tools fall back to the `system` scope
  and medium risk — review them, don't ignore them.
- **Risk is a default, not a verdict.** `risk_of` derives low/medium/high
  from scope. Override per-tool where your domain knows better
  (e.g. `GitPush` is high even though reads are low).
- **Deny beats ask.** If a scope is policy-denied, don't prompt the user
  "just in case". A deny is a decision.
- **Keep the receipts queryable.** `audit.recent(scope="shell")` and
  `audit.stats()` exist so you — or your user — can answer "what did the
  agent do?" at any time. That's the feature.
