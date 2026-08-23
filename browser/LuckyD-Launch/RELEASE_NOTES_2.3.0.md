# LuckyD Browser 2.3.0

**A Chromium-based AI browser for Windows — free, unlimited, offline AI built in.**
No accounts. No API keys. No subscriptions.

`LuckyDBrowserSetup-2.3.0.exe` · Windows 10/11 x64 · per-user install, no admin needed

## New in 2.3

- **TileRegistry — your whole platform on one dashboard.** Every LuckyD tool now
  lives as a tile on the browser dashboard. Adding a tool is a single JSON entry
  in `platform_tiles.json` — zero Python changes. Tiles health-probe their
  service live (green/grey dot) and support `enabled:false` to hide without
  deleting.
- **Agent HQ tile.** The luckyd-code harness gateway (port 8000) is pinned as
  the first tile with accent styling and auto-start, so the agent backend comes
  up with the browser.
- **Deck Studio tile.** Your YouTube/Marp deck generator (`LuckyD Generator`)
  is now one click away: the dashboard auto-launches its `studio-server.js`
  (port 8770), rate-limited to one attempt per minute, and never takes the
  browser down if it fails.
- **Auto-start wiring.** The Control API's `/status` poll (every 5 s) feeds
  `ensure_autostart()` — services that are already running are detected and
  left alone, so nothing double-starts.

## Under the hood

- `browser_core/tile_registry.py` is deliberately stdlib-only (no Qt imports),
  so it ships inside the frozen app and can be unit-tested headlessly.
- Selftest expanded to 111 checks — including deterministic AI-provider
  detection — all green.
- PyInstaller spec ships `browser_core/` recursively, so registry config rides
  along in every installed build.

## Verify your download

```
SHA256: d7f620e927f13a0d00938b29af4c7dd495f9b5b54b0e4b31fe12e6c3d4a36d28
File:   LuckyDBrowserSetup-2.3.0.exe (157.7 MB)
```

## Everything since 1.3.0

Session restore · tab groups + AI organizer · vertical tabs · focus mode · side pane ·
bookmark bar with identity tiles · Reader Mode · per-site zoom · full-page screenshots ·
multi-terminal (2 agents/PowerShell/CMD) · workflow recorder with self-healing replay ·
scheduled workflows · network monitor with HAR export · AI extraction API ·
live theme switching (+ secret Synthwave) · offline runner arcade · spell check ·
Translate Page · Read Later queue · one-click auto-update with release notes ·
ClinePass provider · agent skills · project rules · TileRegistry platform tiles