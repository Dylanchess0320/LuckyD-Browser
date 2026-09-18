# Build with LuckyD — OpenCode

Use LuckyD's trust layer and skills inside OpenCode.

## Install the skills

OpenCode loads skills from your config directory:

```bash
mkdir -p ~/.config/opencode/skills
cp -r kit/skills/approve-agent-tools ~/.config/opencode/skills/
cp -r kit/skills/find-security-vulnerabilities ~/.config/opencode/skills/
cp -r kit/skills/run-shell-commands-safely ~/.config/opencode/skills/
```

Reference them in `~/.config/opencode/opencode.json` under your agent's
instructions, or invoke directly — OpenCode discovers `SKILL.md`
frontmatter (`name` + `description`) automatically.

## Wrap tool execution

OpenCode's permission model (`permission` config) pairs naturally with
LuckyD's gates. Recommended setup:

- Keep OpenCode's own permissions as the coarse fence.
- Add `lucky_kit`'s `ApprovalEngine` + `AuditLog` around any custom tools
  or MCP servers you expose — that's where OpenCode can't see, and where
  the receipts matter most.

```python
from lucky_kit import ApprovalEngine, AuditLog

engine = ApprovalEngine()  # reads ~/.lucky-kit/trust/policy.json
audit = AuditLog()  # appends to ~/.lucky-kit/audit/audit.jsonl
```

## Publish back

Built a skill worth sharing? See `../SKILL_AUTHORING.md` — the LuckyD
marketplace accepts skills in exactly this `SKILL.md` format, so what you
build for OpenCode works in LuckyD unchanged.
