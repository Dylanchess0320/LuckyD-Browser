# LuckyD Browser — Roadmap

Honest direction, not promises. Themes are ordered by what helps users most;
exact order and timing shift with feedback and what breaks. Nothing here is a
release commitment.

## Q4 2026 — Make it trustworthy and smooth

- **Installer trust.** Builds are currently unsigned, so Windows SmartScreen
  shows "Unknown publisher" and some corporate antivirus (e.g. Halcyon)
  blocks the installer outright. Goal: signed builds (needs a code-signing
  cert — the free SignPath Foundation route for open source is wired into
  the build), plus the portable ZIP kept as a first-class download so
  IT-managed PCs always have a working option.
- **Agent Mesh depth.** More of the mesh agents working well side by side
  (Cline, MiniMax, OpenCode, Jules), a reliable `doctor` check per backend,
  and clearer labeling of which backends are free vs keyed vs credit-billed.
- **LuckyD Code CLI polish.** Keep improving the `py main.py` CLI: steadier
  provider fallback (so a $0 credit balance degrades gracefully to local
  Ollama instead of confusing anyone), better session resume, and clearer
  cost visibility before a long run.
- **Onboarding.** First-run setup that gets Ollama + a local model working in
  a couple of clicks, and a setup FAQ that answers the real friction points
  (SmartScreen, blocked installers, $0 credits) before users hit them.

## Q1 2027 — Make it more capable

- **Deeper tab automation.** The sidebar agent already drives the visible tab;
  push it further: multi-step web tasks with visible progress and easy undo.
- **Performance and reliability.** Faster cold start, lower memory footprint,
  steadier long sessions.
- **Community.** GitHub Discussions with real seed content, issue templates
  that get answers faster, and a contributor path measured in minutes
  (see [DEV-QUICKSTART.md](DEV-QUICKSTART.md)).

## What won't change

- Free and local-first: the Ollama path stays free, unlimited, and offline.
- No telemetry, ever.
- Windows-first, open source under MIT.

Have a better idea? Open a discussion or an issue — the roadmap follows
what users actually ask for.
