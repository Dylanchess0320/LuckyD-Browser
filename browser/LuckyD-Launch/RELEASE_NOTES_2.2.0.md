# LuckyD Browser 2.2.0

**A Chromium-based AI browser for Windows — free, unlimited, offline AI built in.**
No accounts. No API keys. No subscriptions.

`LuckyDBrowserSetup-2.2.0.exe` · Windows 10/11 x64 · per-user install, no admin needed

## New in 2.2

- **ClinePass provider — sign in with Cline.** LuckyD Code can now run on your
  Cline account: it resolves the live Cline CLI session token on every call and
  refreshes it via WorkOS when it expires, so there is no key to copy and it
  never goes stale. Set `CLINEPASS_API_KEY` to override explicitly.
- **Agent skills.** Drop a markdown file with YAML frontmatter into `skills/`
  and the agent auto-discovers it. Three ship in the box — `top-picks`,
  `ai-news-brief`, `movie-picker` — and `LUCKYD.md` can auto-trigger a skill
  when your request matches its intent pattern.
- **Project rules (`LUCKYD.md`).** Repo-level agent instructions are loaded into
  every session's system prompt — alongside AGENTS.md, .clinerules, .goosehints
  and friends — so LuckyD drops cleanly into any repo that already has rules
  written for another agent.
- **Browser control upgrades** — Control API, dashboard, profiles, and settings
  polish, plus a one-click `Start-LuckyD-Cline.bat` launcher that wires the
  Cline CLI straight into the agent.
- **Agent core improvements** — smarter agent loop, background process tool,
  expanded file tools, structured logging, and a refreshed CLI + web UI.

## Hardening

- **CI is now a real gate.** Secret scanning (gitleaks), dependency auditing
  (pip-audit), and SAST (bandit) are blocking checks — no more
  continue-on-error — and the full test matrix (ubuntu + windows ×
  Python 3.10/3.11/3.12) is green.

## Everything since 1.3.0

Session restore · tab groups + AI organizer · vertical tabs · focus mode · side pane ·
bookmark bar with identity tiles · Reader Mode · per-site zoom · full-page screenshots ·
multi-terminal (2 agents/PowerShell/CMD) · workflow recorder with self-healing replay ·
scheduled workflows · network monitor with HAR export · AI extraction API ·
live theme switching (+ secret Synthwave) · offline runner arcade · spell check ·
Translate Page · Read Later queue · one-click auto-update with release notes ·
ClinePass provider · agent skills · project rules

**Full changelog:** see [CHANGELOG.md](../blob/main/CHANGELOG.md) (repo sections 2.3.0–3.2.0).
