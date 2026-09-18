# Skill Authoring Guide — publish to the LuckyD marketplace

Skills are LuckyD's distribution unit. A skill is a Markdown file with
frontmatter — no code required, no review queue theater. Write one good
skill and every LuckyD user (plus Claude Code, OpenCode, and Codex CLI
users via this kit) can install it.

## The format

```
my-skill/
  SKILL.md
```

`SKILL.md`:

```markdown
---
name: my-skill
description: One line — what it does and when to use it. This is what
  agents read when deciding whether to invoke it, so make it specific.
---

# My Skill

## When to use
...

## The procedure
1. ...
2. ...

## Rules
- ...
```

- `name`: lowercase, hyphens, matches the directory.
- `description`: imperative, specific, mentions the trigger
  ("Use when asked to audit a codebase", not "Helps with security").
- Body: procedure the agent follows, plus hard rules. Write for an agent
  reader: numbered steps, no prose paragraphs, every claim verifiable.

## What makes a skill spread

1. **One job.** `find-security-vulnerabilities` audits. It doesn't also deploy.
2. **Verifiable output.** Findings cite file + line + evidence. Advice
   cites commands the user can run. No vibes.
3. **Fail-closed defaults.** If the skill touches shell, files, or
   network, say which scope it needs and what it refuses to do.
4. **Copy-paste runnable.** Include the exact commands, not descriptions
   of commands.
5. **No secrets, ever.** A skill that asks for an API key in chat is
   rejected. Point at env vars.

## Publishing

1. Put your skill in `kit/skills/<name>/SKILL.md` and open a PR.
2. Add it to the marketplace registry (`skills/registry.json` in the main
   repo) with a sha256 of the file.
3. Write the description like a headline — it's the only marketing the
   skill gets.

The three skills in this kit (`approve-agent-tools`, `find-security-vulnerabilities`,
`run-shell-commands-safely`) are the reference implementations. Steal their structure.
