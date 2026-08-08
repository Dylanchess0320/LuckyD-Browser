# LuckyD Browser 2.1.0

**A Chromium-based AI browser for Windows — free, unlimited, offline AI built in.**
No accounts. No API keys. No subscriptions.

`LuckyDBrowserSetup-2.1.0.exe` · Windows 10/11 x64 · per-user install, no admin needed

## Headline fixes

**True fullscreen video.** The fullscreen button on every player was dead (the
engine's Fullscreen API is opt-in and was never enabled). It works now — and the
browser hides all of its own chrome (tabs, toolbars, docks) so the video gets the
*entire* screen, restoring everything exactly when you exit. F11 behaves too.

**YouTube Shorts un-glitched.** Our own ad-blocker was eating Shorts' video data
(a too-broad `ctier` pattern matched ordinary stream fetches). Shorts now load
and play smoothly.

**YouTube ads blocked at the source again.** YouTube's server-side ad insertion
made ads invisible to domain blocking — so the blocker now strips ad placements
from the player's own API responses before the player sees them. Ads never
schedule; the old short-circuit skip stays as a fallback.

## New in 2.1

- **Agent 2 in the terminal** — a second agent button in the terminal tab's shell
  bar. It launches the standalone coding-agent checkout (your Desktop shortcut's
  project) as a full interactive REPL in its own workspace, side by side with the
  built-in agent, PowerShell, and CMD. Override it with the `terminal_cli2`
  setting or `LUCKYD_CLI2` env var — Tools → Agent 2 Terminal opens it directly.

## Everything since 1.3.0

Session restore · tab groups + AI organizer · vertical tabs · focus mode · side pane ·
bookmark bar with identity tiles · Reader Mode · per-site zoom · full-page screenshots ·
multi-terminal (2 agents/PowerShell/CMD) · workflow recorder with self-healing replay ·
scheduled workflows · network monitor with HAR export · AI extraction API ·
live theme switching (+ secret Synthwave) · offline runner arcade · spell check ·
Translate Page · Read Later queue · one-click auto-update with release notes

**Full changelog:** see [CHANGELOG.md](../blob/main/CHANGELOG.md) (repo sections 2.3.0–3.1.0).
