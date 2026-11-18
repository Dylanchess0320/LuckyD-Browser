# LuckyD Browser v10.9 - Release Notes

> **The local-first Chat release.** Hub chat and Claw tasks are optional extras now -
> the browser starts clean, with no personal service addresses baked in.

**[⬇ Download `LuckyDBrowserSetup-10.9.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9/LuckyDBrowserSetup-10.9.exe)** - Windows 10/11 x64 · per-user install · no admin needed

**[`LuckyDBrowser-Portable-10.9.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.9/LuckyDBrowser-Portable-10.9.zip)** - unzip and run, for locked-down PCs.

---

## What's new in 10.9 - Local-first Chat

- **Chat opens local-only.** The provider pick and the shared handoff note are always there. The Hub chat panel and the Claw tasks panel only render when that service has both an address and a token in Settings → Connections.
- **No personal addresses in the build.** The baked-in Tailscale/LAN defaults (`100.96.167.54`) and their Settings placeholders are removed, so a shipped installer never points at one person's machine.
- **No dead panels or fake status.** Unconfigured is the normal state and stays quiet. A configured service that is offline shows one plain line naming the fix.
- **Puck row removed.** The stale Puck bridge entry is gone from the Chat status line; the block covers only services that are actually connected.

## Upgrading

Install `LuckyDBrowserSetup-10.9.exe` over your existing install - profile, settings, and workspaces carry over. If you use Hub chat or Claw tasks, set the address and token once in Settings → Connections; nothing else changes.

## Known limitations

- Twitch video and some game audio still don't play: the web engine ships without H.264/AAC decoders. Use another browser for those sites.
- Code signing is not configured, so Windows may show a SmartScreen prompt on first run.
