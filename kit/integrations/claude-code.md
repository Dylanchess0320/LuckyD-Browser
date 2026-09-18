# Build with LuckyD — Claude Code

Use LuckyD's trust layer and skills inside Claude Code.

## Install the skills

```bash
mkdir -p ~/.claude/skills
cp -r kit/skills/approve-agent-tools ~/.claude/skills/
cp -r kit/skills/find-security-vulnerabilities ~/.claude/skills/
cp -r kit/skills/run-shell-commands-safely ~/.claude/skills/
```

Claude Code picks up `SKILL.md` files automatically — no config needed.
Invoke with `/approve-agent-tools`, or let Claude apply them when relevant.

## Use the trust layer in hooks

Claude Code hooks are the natural place for LuckyD's gates. Example
`~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/kit/examples/claude_pretooluse.py"
          }
        ]
      }
    ]
  }
}
```

The example script (adapt to your path) applies `ApprovalEngine` +
`AuditLog` to every Bash call: allowlisted commands run, the rest ask,
everything gets a receipt.

## The pattern to copy

Even without the kit, the portable idea is the three gates:

1. `scope_of(tool)` — what capability is this?
2. `ApprovalEngine.decide(tool, args)` — allow / ask / deny
3. `AuditLog.record(...)` — receipt, with `redact_args` first

That's LuckyD's whole trust model. It's yours now.
