# LuckyD Browser v10.4.0 — Free-model rotation + provider-credit honesty

## What's new

- **Free-model auto-rotation (default).** Every LuckyD tool now prefers
  free models and automatically rotates to the next best free model when
  the current one fails (rate limit, HTTP 402/403/429, timeout, or
  error) — no configuration needed. Default priority: Cline (free tier)
  → Gemini (free tier) → Ollama local (`llama3.2:3b`, always the final
  fallback) → other free providers. Explicit picks
  (`CODING_AGENT_PROVIDER`, `--provider`, the browser sidebar's saved
  provider) always win over rotation, and paid/keyed providers remain
  fully available.
- **Cline credit honesty.** When the Cline gateway returns HTTP 402
  (insufficient credits), LuckyD records the signal in
  `~/.luckyd/cline_credit_state.json` (timestamp + reason, 24-hour TTL).
  While valid, provider auto-selection skips Cline, the provider list
  shows an exhausted indicator, and `lucky-code providers
  --clear-credit-state` clears the marker by hand after topping up.
  No balance API — the 402 is the only trigger.
- **Version unification.** Browser, backend, installer, docs, and
  version-pinned tests all report 10.4.0.

## Two ways to install

- **Setup EXE** — normal Windows installer.
- **Portable ZIP** — no install, runs from any folder. For locked-down PCs.

## Please read this first

- Builds are **unsigned** (no signing certificate yet): Windows SmartScreen
  will show "Unknown publisher", and strict work PCs (Halcyon/IT-managed)
  may block the files until IT allowlists the hash.
- On top of everything from 10.2.3 (smarter-agent edition + 402 failover).
