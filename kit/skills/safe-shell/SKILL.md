---
name: safe-shell
description: Execute shell commands safely inside an agent loop — allowlists, dry-run previews, output redaction, and failure budgets. Use whenever an agent runs Bash or PowerShell on a user's machine.
---

# Safe Shell — commands without regrets

Shell is the highest-risk scope most agents have. Treat every command as
guilty until proven innocent.

## 1. Allowlist, don't blocklist

Blocklists lose — attackers are more creative than your regex. Define what
the agent *may* run and deny everything else:

```python
ALLOWED = {"git", "ls", "cat", "grep", "python", "pytest", "npm", "node"}
cmd = shlex.split(command)
if not cmd or cmd[0] not in ALLOWED:
    deny(f"command not allowlisted: {cmd[0] if cmd else '(empty)'}")
```

- Prefer absolute paths for the interpreter (`/usr/bin/python3`).
- Never allowlist `bash -c`, `sh -c`, `powershell -enc`, or `curl | bash`
  patterns — they smuggle arbitrary commands past the allowlist.

## 2. Dry-run preview for destructive commands

Before `rm`, `mv`, `dd`, disk/registry writes, or recursive operations:
- Show the exact command and its blast radius ("deletes 14 files under
  ./build").
- Require explicit approval. `rm -rf` with a variable in the path is a
  finding, not a command — refuse and ask for the literal path.

## 3. Redact the output, not just the input

Command output lands in logs and model context. Scrub it:
- Reuse `redact_args`-style patterns on stdout/stderr for tokens, keys,
  and connection strings.
- Truncate: cap output (e.g. 200 lines / 20KB) and say so. An agent
  doesn't need 50,000 lines of `find /` to answer a question.

## 4. Failure budget

- Timeouts on every command (default 60s; no unbounded runs).
- Track consecutive failures — after 3, stop and report instead of
  retrying the same failing command with small variations. (See the
  `CircuitBreaker` in `lucky_kit/harness.py`: 4 consecutive all-failure
  turns trips the run.)
- `set -euo pipefail` semantics: fail fast, fail loud, never silently
  continue past an error in a chained command.

## 5. Environment hygiene

- Never export secrets into the environment of a child process that
  doesn't need them. Pass them as one-shot stdin or files with 0600
  permissions, then delete.
- Log the command that ran (redacted), its exit code, and duration —
  into the audit log (`trust-guard`), not just the terminal scrollback.
