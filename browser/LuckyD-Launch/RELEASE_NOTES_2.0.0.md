# LuckyD Browser 2.0.0

**A Chromium-based AI browser for Windows — free, unlimited, offline AI built in.**
No accounts. No API keys. No subscriptions.

`LuckyDBrowserSetup-2.0.0.exe` · Windows 10/11 x64 · per-user install, no admin needed

## Headline fix

**YouTube resumes correctly after blocked ads.** Ads share the video element with
the content, so ad-skipping used to leave the video starting mid-stream. LuckyD now
captures the content position when an ad starts and restores it when it ends —
**0s for pre-rolls, the right spot for mid-rolls** — and restores your mute state too.

## New in 2.0

- **Spell check** with right-click suggestions (drop a hunspell `.bdic` into
  `assets/qtwebengine_dictionaries` to light it up)
- **Translate Page…** — right-click any page to open it in Google Translate
- **Read Later queue** — `Ctrl+Alt+S` parks a page in its own 📖 submenu,
  separate from real bookmarks

## Everything since 1.3.0

Session restore · tab groups + AI organizer · vertical tabs · focus mode · side pane ·
bookmark bar with identity tiles · Reader Mode · per-site zoom · full-page screenshots ·
multi-terminal (agent/PowerShell/CMD) · workflow recorder with self-healing replay ·
scheduled workflows · network monitor with HAR export · AI extraction API ·
live theme switching (+ secret Synthwave) · offline runner arcade ·
one-click auto-update with release notes

**Full changelog:** see [CHANGELOG.md](../blob/main/CHANGELOG.md) (repo sections 2.3.0–3.0.0).
