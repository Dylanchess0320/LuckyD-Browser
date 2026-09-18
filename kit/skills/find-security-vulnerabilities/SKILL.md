---
name: find-security-vulnerabilities
description: Find security vulnerabilities in a codebase or project — secrets scan, dependency review, injection surfaces, and agent-tool permissions. Produces independently verifiable, machine-readable findings. Use when asked to audit, review for security, or harden a project.
---

# Find Security Vulnerabilities — multi-phase, verifiable findings

Run the phases in order. Each phase must produce findings the user can
independently verify — file, line, and evidence quote. No vibes.

## Phase 1 — Recon

Map the attack surface before judging it:
- Entry points: network listeners, web routes, CLI commands, file watchers,
  installed hooks/schedules/cron jobs.
- Trust boundaries: where user input crosses into shell, SQL, HTML, or
  deserialization.
- Output: a one-paragraph surface map. If you can't enumerate entry
  points, say so and stop — an audit of an unknown surface is theater.

## Phase 2 — Secrets & credentials

- Scan for API keys, tokens, passwords, private keys in code, configs,
  `.env` files, notebooks, and git history (`git log -p --all -S 'api_key'`
  patterns, plus common secret shapes).
- Flag credential *handling*: logged secrets, secrets in URLs, secrets in
  error messages, world-readable secret files.
- Severity: any live secret in history = HIGH, even if "rotated later".

## Phase 3 — Dependencies & supply chain

- List lockfiles and pin status. Unpinned or floating versions = finding.
- Check for known-vulnerable versions of network-facing deps.
- Flag install-time scripts (`postinstall`, build hooks) — each one runs
  arbitrary code on `npm install` / `pip install`.

## Phase 4 — Injection & execution surfaces

- Shell: user input reaching shell commands without allowlisting.
- Web: unescaped HTML (XSS), unvalidated redirects, missing CSP.
- Deserialization: `pickle`, `yaml.load`, `eval` on untrusted input.
- Agents: tools the agent can call without approval — overly broad
  permissions are a vulnerability (see `approve-agent-tools`).

## Phase 5 — Findings report

Write findings as machine-readable Markdown:

```markdown
## [HIGH] Hardcoded Stripe key in config.py
- **File:** `config.py:14` — `STRIPE_KEY = "sk_live_..."`
- **Evidence:** <short quote>
- **Impact:** <one line>
- **Fix:** <concrete step>
- **Verify:** <how the user confirms the fix>
```

Rules:
- Every finding needs file + line + evidence. No evidence, no finding.
- Severity is impact × likelihood, stated in one line each.
- End with a fix checklist ordered by severity, each item independently
  verifiable.
- If a phase finds nothing, say so explicitly — "no findings" is a
  result, not an omission.
