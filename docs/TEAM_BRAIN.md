# The team brain — one page

*Hub slices 1–6 + 9, 2026-11-10. Ground truth: the code in this repo, not the vision.
One page = 53 lines / 529 words (`wc -l -w`). Count in `TEAM_BRAIN_SCORECARD.md`.*

## The pieces, and who owns what

| Piece | Owns | Lives in |
|---|---|---|
| **LuckyD Browser — Chat page** | The Hub: status, provider switching, handoff note, cards | `browser/browser_core/chat_page.py`, `control_server.py` |
| **Memory** | The handoff note (one short note + append-only log) and the card store — plain JSON files the Browser owns (atomic writes; corrupt file reads as empty) | `hub_memory.py` (`hub_handoff.json`), `hub_cards.py` (`hub_cards.json`) |
| **Provider pin** | The pinned provider (`ai_provider` setting; empty = "auto", chain decides). Survives restarts | `hub_providers.py` |
| **Terminal / LuckyD Code** | Code work on the PC. Wired to Hub memory since slice 9 | `core/hub_handoff.py`, `main.py` (`/handoff`, session start/end), `terminal_server.py` (`LUCKYD_HUB_URL`) |
| **Claw** | Overnight jobs, PR-only, never main. Hub reports liveness only (`/healthz` + allowlist) | `luckyd_services.py: claw_state()` |
| **Puck** | Standalone: talks to Muse cloud directly, firmware 175ag / Flash #16. Hub does **not** control it — `/chat/state` reports "no puck bridge configured" | `luckyd_services.py: puck_state()` |
| **Growth watch** | GitHub stars / Discord joins (cloud `luckyd-watch/check.py`). Adapter POSTs each real change to `/chat/cards`; a no-change day posts nothing | `scripts/hub_growth_watch.py` |

Retired tools (HQ, Workflows, Deep Researcher, Deck Studio) were removed in
slice 4. No YouTube in the Hub — Dylan's rule, enforced by the card kinds
(`github_stars`, `discord_members`).

## What talks to what

- **Status:** `GET /chat/state` — one surface: Hub, Claw, puck liveness (slice 1).
- **Requests:** `POST /chat/send` carries the pinned provider; one tap on the
  Chat page re-pins it, no config-file editing (slice 2 — Chat page only,
  not the Code layer).
- **Memory:** `GET/POST /chat/handoff` — any surface reaching the Browser's
  control server can read/write the note (slice 3).
- **Cards:** `POST /chat/cards` — the growth-watch adapter's only way in;
  Chat renders them as dismissible cards, capped at 20 (slice 5).

## While he sleeps

The growth watch runs in the cloud; on a real star/join the adapter posts a
card, on a no-change day it posts nothing — silence, not a ping. Claw works
in a worktree and opens a PR; nothing merges itself. The puck needs no PC at
all. The Hub does nothing overnight unless a watch fires: no polling loop,
no scheduled job in this repo wakes it.

## The gap — closed by slice 9

**Was: the handoff note is Browser-only.** Only the Chat page read/wrote
it. Terminal / LuckyD Code could not see it, so a note left in chat
never reached the code layer and a Code session's context never came back.

**Fix (slice 9, done):** the Code layer reads/writes the endpoints that
already exist — `GET /chat/handoff` at REPL start (shown as one line),
`POST /chat/handoff` at session end (a short receipt, only when the
session did work), plus `/handoff [text]` in the REPL for manual
read/write. The bridge hands the CLI the control-server URL as
`LUCKYD_HUB_URL`. No new store or protocol: one reader + one writer on
the Code side, gated by the Hub tests plus one round-trip test.
