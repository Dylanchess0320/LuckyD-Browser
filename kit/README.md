# Build with LuckyD 🍀

Don't build your own trust plumbing. Build *with* LuckyD.

This kit packages LuckyD's battle-tested agent infrastructure — the same
code running in LuckyD Browser v9.5 — so any project can use it:

- **`lucky_kit/`** — stdlib-only Python package: permission scopes, approval
  policy engine, secret redaction, append-only audit log, agent-loop circuit
  breaker. Copy the folder, `import lucky_kit`, done.
- **`skills/`** — installable agent skills (`SKILL.md` format) that work in
  LuckyD, Claude Code, OpenCode, and Codex CLI:
  - `trust-guard` — gate any agent's tool calls: scopes + approvals + receipts
  - `security-audit` — multi-phase security audit with verifiable findings
  - `safe-shell` — allowlisted, dry-run, redacted shell execution
- **`integrations/`** — setup guides: [Claude Code](integrations/claude-code.md),
  [OpenCode](integrations/opencode.md), [Codex CLI](integrations/codex.md),
  [WebMCP](integrations/webmcp.md) (expose your site to agents)
- **`SKILL_AUTHORING.md`** — write a skill, publish it to the LuckyD marketplace

## 60-second start

```bash
cp -r kit/lucky_kit /your/project/
```

```python
from lucky_kit import ApprovalEngine, AuditLog, CircuitBreaker

engine = ApprovalEngine()      # allow / ask / deny per tool call
audit = AuditLog()             # every call, redacted, append-only JSONL
breaker = CircuitBreaker()     # trips after 4 all-failure turns

result = engine.decide("Bash", {"command": "rm -rf /"})
print(result.decision, "-", result.reason)   # ask - scope 'shell' requires approval
```

## The philosophy

**Agentic with receipts.** Agents should do real work — and every action
should be scoped, approved under a clear policy, and recorded where the
user can see it. That's LuckyD's trust model, and now it's yours.

## Install a skill anywhere

```bash
# Claude Code
cp -r kit/skills/trust-guard ~/.claude/skills/
# OpenCode
cp -r kit/skills/trust-guard ~/.config/opencode/skills/
# Codex CLI
cp -r kit/skills/trust-guard ~/.codex/skills/
```

## Tests

```bash
python -m pytest kit/tests/ -q
```

## License

Same as LuckyD Browser — see the repo root `LICENSE`.
