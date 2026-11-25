# LuckyD Browser v10.9.3 - Release Notes

> **The hardening release: approval gates that actually gate, retries that
> stop, and terminals that can't impersonate an agent.**

**[`LuckyDBrowserSetup-10.9.3.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9.3/LuckyDBrowserSetup-10.9.3.exe)** - Windows 10/11 x64 - per-user install - no admin needed

**[`LuckyDBrowser-Portable-10.9.3.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9.3/LuckyDBrowser-Portable-10.9.3.zip)** - unzip and run, for locked-down PCs.

---

## What's new in 10.9.3

### Approval gates work everywhere

- **One configured hook per entry point.** The CLI, HQ web server, and ACP
  server each register the approval hook with the right callback. The agent
  no longer installs a second bare hook that parked every Write/Bash/Edit in
  the pending queue and blocked for five minutes.
- **Edit asks like Write and Bash.** `Edit` is now `REQUIRES_APPROVAL`, so an
  agent can't rewrite a file through the edit path while the write path is
  gated.
- **ACP is deny-by-default.** ACP has no interactive approval UI, so
  approval-requiring tools are denied with a clear message instead of running
  ungated. Opt in explicitly with `LUCKYD_ACP_AUTO_APPROVE=1`, or allow the
  relevant scope in the Trust policy.

### Retries and turn limits are consistent

- **Two retries, 120 seconds.** `_with_retry`, `LLMClient`, and the agent all
  use the same 2-retry (3-attempt) policy with a 120s elapsed cap. No more
  hidden 3-retry path in the agent loop.
- **Circuit breaker.** Four consecutive turns where every tool call fails stop
  the run instead of spinning to the turn ceiling.
- **10,000-turn ceiling for long autonomous runs.** `CODING_AGENT_MAX_TURNS`
  now defaults to 10000. The one-shot cost preview estimates a typical
  30-turn task rather than printing a meaningless 10,000-turn price.

### Terminal and test isolation

- **No agent identity leak.** Spawning a plain PowerShell/CMD terminal now
  strips inherited `LUCKYD_AGENT_*` and `LUCKYD_HUB_URL` variables, so a
  browser opened from an agent shell doesn't label every tab as Agent 1/2.
- **Hermetic tests.** Local settings files, a logged-in Cline session, a
  running Ollama daemon, and an installed `jedi` no longer change test
  results. The no-jedi, rotation, provider, and version tests force their own
  environment.

### Version unification

- Browser, installer, UI labels, package metadata, and documentation all
  report **10.9.3**.

## Upgrading

Run `LuckyDBrowserSetup-10.9.3.exe` over your existing install - profiles,
tabs, settings, workspaces, and stored keys are preserved automatically. The
portable ZIP is the same build with no installer.
