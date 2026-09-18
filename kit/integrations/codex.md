# Build with LuckyD — Codex CLI

Use LuckyD's trust layer and skills inside OpenAI's Codex CLI.

## Install the skills

Codex CLI discovers skills on its path — copy them where yours live:

```bash
mkdir -p ~/.codex/skills
cp -r kit/skills/trust-guard ~/.codex/skills/
cp -r kit/skills/security-audit ~/.codex/skills/
cp -r kit/skills/safe-shell ~/.codex/skills/
```

Point Codex at them from `~/.codex/config.toml` or your project's
`AGENTS.md`:

```markdown
## Skills
- `trust-guard`: gate every tool call through scopes + approvals + audit log
- `security-audit`: multi-phase security audit with verifiable findings
- `safe-shell`: allowlisted, dry-run, redacted shell execution
```

## Pair with Codex's sandbox

Codex already sandboxes execution — LuckyD's layer adds what sandboxes
don't:

| Codex sandbox | LuckyD kit adds |
|---|---|
| Blocks syscalls | Per-scope user policy (ask/allow/deny) |
| Read-only FS modes | Human-readable approval prompts (`describe_decision`) |
| — | Append-only audit log with secret redaction |
| — | Circuit breaker for stuck agent loops |

Defense in depth: sandbox stops the damage, the kit stops the *decision*
and keeps the receipts.

## The one import that matters

```python
from lucky_kit import ApprovalEngine, AuditLog, CircuitBreaker
```

Three classes, stdlib-only, no LuckyD install. That's the whole integration.
