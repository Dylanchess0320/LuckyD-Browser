# LuckyD Browser — Release Notes

## [10.5.0] — Provider/model switching polish — 2026-09-24

**[`LuckyDBrowserSetup-10.5.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.5.0/LuckyDBrowserSetup-10.5.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-10.5.0.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.5.0/LuckyDBrowser-Portable-10.5.0.zip)** — unzip and run, for locked-down PCs.

- **Provider/model switching polish** — HQ Models view shows per-provider health
  badges (Ready / Needs key / Exhausted) with rotation order, a one-click
  "Switch to best working" button, and the live answering provider/model in the
  sidebar. LuckyD remembers the last working provider and prefers it automatically.
- **BEAST speed pass** — CLI `--help` cold start 2.6s → 0.14s, `providers`/`model`
  commands ~2.3s → ~1.3s, HQ first dashboard load ~500ms → 65ms. Lazy heavy
  imports, cached sys.path/model catalog, eager free-rotation init at HQ startup.

On top of everything from 10.4.0 below.

## [10.4.0] — Provider-credit honesty — 2026-09-24

**[`LuckyDBrowserSetup-10.4.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.4.0/LuckyDBrowserSetup-10.4.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-10.4.0.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.4.0/LuckyDBrowser-Portable-10.4.0.zip)** — unzip and run, for locked-down PCs.

- **Cline credit honesty** — a Cline HTTP 402 (insufficient credits) records `~/.luckyd/cline_credit_state.json` (24-hour TTL). While valid, provider auto-selection skips Cline and the provider list shows an exhausted indicator; `lucky-code providers --clear-credit-state` clears the marker after topping up. No balance API — the 402 is the only trigger.
- **Version unification** — everything reports 10.4.0.

On top of everything from 10.2.3 below.

## [10.2.3] — Smarter-agent edition + 402 failover — 2026-09-23

**[`LuckyDBrowserSetup-10.2.3.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.2.3/LuckyDBrowserSetup-10.2.3.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-10.2.3.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.2.3/LuckyDBrowser-Portable-10.2.3.zip)** — unzip and run, for locked-down PCs.

- **Smarter agent** — live goals in the prompt, enforced plan mode, MCP in one-shot mode, opt-in `--verify`, token-aware compaction, task tool pruning, `FindRelevantFiles` retrieval.
- **402 failover** — exhausted Cline Credits balance escapes to ClinePass, then local Ollama, instead of dead-ending.
- **Browser hardening** — trust/debug/harness/GUI/updater/catalog fixes.

On top of everything from 10.2.1 below.

## [10.2.1] — Dashboard cleanup + HQ auto-rotation — 2026-09-23

**[`LuckyDBrowserSetup-10.2.1.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.2.1/LuckyDBrowserSetup-10.2.1.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-10.2.1.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.2.1/LuckyDBrowser-Portable-10.2.1.zip)** — unzip and run, for locked-down PCs.

- **Cleaner home dashboard** — the four AI preset chips are gone.
- **Ask Lucky reliability** — real error messages, no double-fires, picks up new keys without a restart.
- **HQ auto-rotates to a working model** — retired/rate-limited/down models rotate transparently and pin the winner; OpenCode Zen default fixed to `gemini-3.5-flash-lite`.

On top of everything from 10.2.0 below.

## [10.2.0] — Performance + hardening edition — 2026-09-23

**[`LuckyDBrowserSetup-10.2.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.2.0/LuckyDBrowserSetup-10.2.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-10.2.0.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.2.0/LuckyDBrowser-Portable-10.2.0.zip)** — unzip and run, for locked-down PCs.

- **Faster agent loop** — grep, file read/edit, CSV, LSP rename, secrets, file watching, and memory compression off the event loop; batched memory-decay SQL.
- **Safer + Windows-correct `ProcessTool`** — command-injection hardening plus `shell=True` behind the blocklist gate for builtins, pipes, and redirects.
- **Green everywhere** — POSIX/desktop-optional tests skip cleanly; CI green.
- **Agent 1 + Agent 2 on v10.2.0** — terminal, CLI, and ACP all report v10.2.0.

On top of everything from 10.1.0 below.

## [10.1.0] — Google AI Pro edition — 2026-09-21

**[`LuckyDBrowserSetup-10.1.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.1.0/LuckyDBrowserSetup-10.1.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-10.1.0.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v10.1.0/LuckyDBrowser-Portable-10.1.0.zip)** — unzip and run, for locked-down PCs.

- **OpenCode restored** — `mesh-opencode` is back in the Agent Mesh, keyed via `OPENCODE_API_KEY`, alongside MiniMax and Cline. (Old free tier is gone; keyed only.)
- **Gemini refreshed** — 2.5 Pro/Flash defaults, 3 previews in the model list.
- **Nano Banana + Veo 3.1** — image generation on the free API tier; video generation needs a billing-enabled key.
- **2TB Drive backup** — one command after a one-time Google sign-in.
- Note: Google AI Pro covers the Gemini app; the API LuckyD uses has its own free tier — Pro doesn't add API quota.

## [9.9.0] — Anti-block hardening + portable ZIP — 2026-09-21

**[`LuckyDBrowserSetup-9.9.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.9.0/LuckyDBrowserSetup-9.9.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed.
**[`LuckyDBrowser-Portable-9.9.0.zip`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.9.0/LuckyDBrowser-Portable-9.9.0.zip)** — unzip and run, for locked-down PCs.

- **Anti-block hardening** — no `-ExecutionPolicy Bypass` anywhere in the installer; the Ollama bootstrap no longer auto-runs post-install (manual opt-in only).
- **Portable ZIP** on every release — no installer heuristics fire at all.
- **Code signing wired in** — signs binaries + installer when a cert is configured (unsigned until then; free for open source via SignPath).
- **CI green** — fixed the 3 mypy errors that kept 9.8 red.
- **Fixed:** agent crash when MiniMax CLI tools were imported; stale free-model fallback tests updated for the 9.8 Cline-gateway pool.

On top of everything from 9.8.0 below.

## [9.8.0] — Zen retired, Cline default + goals, plugins, ACP — 2026-09-21

**[`LuckyDBrowserSetup-9.8.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.8.0/LuckyDBrowserSetup-9.8.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **OpenCode Zen retired, Cline is the free default** — the Zen keyless `$0`
  tier died and the gateway blocks these accounts, so LuckyD no longer offers
  it. A logged-in Cline CLI session (`cline auth`) gives free agent models
  with no API key; `CODING_AGENT_PROVIDER=opencode` auto-migrates to Cline.
- **Goals, steering, follow-ups** — `/goal <text>` (+ `budget=50K`, `pause`,
  `resume`, `clear`), `/steer <guidance>` mid-run, `/btw <text>` queued next.
- **Plugins + custom providers** — `lucky-code plugin add/enable/disable`,
  `lucky-code custom-provider add/test/use` for any OpenAI-compatible gateway.
- **ACP stdio server** — `lucky-code --acp` for editor extensions.
- **MiniMax Code + media in the mesh** — `mesh-mcode`/`mesh-mmx` replace the
  retired `mesh-opencode`; `mcode`/`mmx` also work as agent tools.
- **Deep research on your providers** — the swarm rides the sidebar's
  connected providers via the bridge (locals → Cline → keyed clouds).
- **Google-CAPTCHA-proof** — DDG default search, adblock allowlist for
  Sorry/consent pages, desktop UA hardening.

On top of everything from 9.7.0 below.

## [9.7.0] — Flagship Code CLI + MiniMax provider — 2026-09-21

**[`LuckyDBrowserSetup-9.7.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.7.0/LuckyDBrowserSetup-9.7.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **LuckyD Code CLI is now the flagship terminal agent** — permission modes
  (`--permission-mode`), background subagent delegation (`delegate_task`),
  slash commands from Markdown files (`/compact`, `/review`, `/init`),
  automatic context compaction, classified retry with backoff, and session
  autosave.
- **MiniMax is a first-class provider** — `--provider minimax` with
  `MiniMax-M3` (default), `MiniMax-M2.7-highspeed`, `MiniMax-M2.7`.
- **Agent mesh unified** — Agent 1 and Agent 2 both run LuckyD Code v9.7.

## [9.6.0] — Honest research + opt-in contributor tier — 2026-09-19

**[`LuckyDBrowserSetup-9.6.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.6.0/LuckyDBrowserSetup-9.6.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **Deep Research tells the truth** — live engine chip, fail-fast keyed
  backends, evidence from `evidence.json`, honest cancel.
- **Contributor tier opt-in only** — `muse-spark-1.3` vs `-contributor`,
  hidden unless `/contributor on`, honest cached-token costs.

On top of everything from 9.5.0 below.

---

## [9.5.0] — Terminal crash fix — 2026-09-16 (archive)

**[`LuckyDBrowserSetup-9.5.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.5.0/LuckyDBrowserSetup-9.5.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **Agent terminal crash fixed** — a Bash tool call with a missing/null `command`
  raised `AttributeError` and killed the terminal session. The shell tools now
  validate input and return an error result instead.
- **Timeout kill path regression-tested** — timed-out commands return a timeout
  error result and the whole process tree is reaped.

On top of everything from 9.4.0 below.

---

## [9.4.0] — Antigravity replaces Gemini — 2026-09-16 (archive)

**[`LuckyDBrowserSetup-9.4.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.4.0/LuckyDBrowserSetup-9.4.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **Antigravity (agy) mesh agent** — `agy` / `mesh-agy` covers the agent-mesh plan/build stages, probed via PATH plus `%LOCALAPPDATA%\agy\bin`.
- **Gemini CLI removed** — `gemini` / `mesh-gemini` shells, dock chips, and fallback probing are gone.
- **Cline CLI stays first-class** — `cline` alongside `mesh-cline` in both agent terminals.

On top of everything from 9.3.0 below.

---

## [9.3.0] — AI provider list + reliability — 2026-09-15 (archive)

**[`LuckyDBrowserSetup-9.3.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.3.0/LuckyDBrowserSetup-9.3.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **AI provider list** — `lucky-code providers`, `/providers`, `GET /api/providers`: all 13 providers with live status and cost tier.
- **Offline-proof model resolver** — stale cache fallback when the catalog fetch fails.
- **Honest cost tracking** — deepseek-v4 at real rates, free-tier models at $0.
- **Duplicate Deep Research tile fixed.**

On top of everything from 9.1.0 below.

---

## [9.1.0] — Agent terminals upgrade — 2026-09-15 (archive)

**[`LuckyDBrowserSetup-9.1.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.1.0/LuckyDBrowserSetup-9.1.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

- **Gemini CLI first-class** — `gemini` / `mesh-gemini` shells in both Agent 1 (v3.6) and Agent 2 (v2.2) terminals (free 1000 req/day Google login / 250 req/day API key, Flash models).
- **Cline CLI first-class** — `cline` shell alongside `mesh-cline` (free rotating FREE models, quota-limited).
- **Every free model in /model** — Gemini free tier (gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.0-flash, gemini-3.5-flash-lite, gemma-4-*), Cline free tier (12 models incl. kat-coder-pro, glm-5, deepseek-v4-flash, laguna-s-2.1:free, longcat-2.0), plus new ClinePass models (glm-5.3, glm-5.2, qwen3.8-max).
- **`gemini` / `cline` provider aliases** — `CODING_AGENT_PROVIDER=gemini` (= google) and `=cline` (= cline-usage) work everywhere.

On top of everything from 9.0.0.

---

## [9.0.0] — Reliability release — 2026-09-14

**[`LuckyDBrowserSetup-9.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v9.0.0/LuckyDBrowserSetup-9.0.0.exe)** (303,971,333 bytes, SHA-256 `4F5503801D6F41EB39F0454A4E71E2C747725F79DFA229E108C8FF894095CD9B`) — Windows 10/11 x64 · per-user install · no admin needed

- **Ollama detection fixed** — local AI is found even when a proxy/VPN is set
  (localhost no longer routed through the proxy), and the chat path bypasses
  it too.
- **Honest AI status** — the dashboard pill now says exactly what's wrong:
  "Ollama not running", "no models pulled", or the working providers.
- **OpenCode Zen updated** — Zen's old keyless $0 tier is gone (every keyless
  call 401s now). Zen registers only with `OPENCODE_API_KEY` and serves its
  current platform catalog; the default model is `gemini-3.5-flash-lite`.
- **DeepSeek Harness boots** — Agent Mesh now spawns `dsh web` (the old
  `--profile web --no-open` invocation is no longer valid).
- **Ctrl+K palette fixed** — the command palette takes keyboard focus again.
- **Readable home page** — shortcut labels get brighter type with text shadow.
- **YouTube colors fixed** — `--force-color-profile=srgb` on the software
  video path (was rendering with wrong BT.601 colorimetry).

On top of everything from 8.0.0. Verified: **1,855 tests passing**, coverage
**52% → 81%**, **24 production bugs fixed**. Installer hash verified
2026-09-14: the `v9.0.0` Release asset matches the local
`browser/installer/output/LuckyDBrowserSetup-9.0.0.exe` byte-for-byte.

---

## [8.0.0] — The Cleanup, phase one

**[⬇ Download `LuckyDBrowserSetup-8.0.0.exe`](https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v8.0.0/LuckyDBrowserSetup-8.0.0.exe)** — Windows 10/11 x64 · per-user install · no admin needed

v8.0.0 is The Cleanup, phase one: contextual skills move into the AI sidebar,
the model router gets smart, and the home page gets the Neon Night treatment —
on top of everything from 7.0.0.

## What's new in 8.0.0

- **Contextual skill chips** — the 5 bundled skills (`ai-news-brief`, `chess`,
  `graphify`, `movie-picker`, `top-picks`) surface as ✨ chips above the AI
  sidebar input as you type; one tap attaches the skill as chat context.
- **Smart model routing** — `core/router.py`'s ModelRouter is wired into
  `AIBridge.chat()` auto mode: it picks the provider per question. Explicit
  provider picks always win; a non-viable router pick falls back to the
  existing chain.
- **Neon Night home** — the real `/dashboard` and the `newtab.html` fallback
  share one refined typographic system: tabular clock, small-caps labels,
  system-only font stack, fully offline-safe, `prefers-reduced-motion`.
- **Honest AI labels + $0 fallback** — provider chips say `· free tier` /
  `· credit-billed ⚠`; with no Ollama and no keys, chat falls back to the
  free OpenCode Zen gateway instead of an empty-token provider.
- **Antigravity decluttered** — redundant Tools-menu and command-palette
  launchers removed; it's already in the terminal.
- **Copy freshness** — README, docs, `.github`, and in-app text brought
  current; About dialog and window titles now say "LuckyD".
- **Version unified to 8.0.0** everywhere — package, PyInstaller/Inno metadata,
  `LuckyDBrowser/8.0` user-agent, and docs — guarded by a regression test so it
  can't drift again.
- **465 automated tests passing**, `ruff check` clean.

## Upgrade

Install `LuckyDBrowserSetup-8.0.0.exe` over your existing install — profile,
bookmarks, and settings are preserved.

## Verify

```powershell
# After install, the app reports 8.0.0 in Help → About and in the console banner.
```

<details>
<summary>Previously: 7.0.0 — Agentic</summary>

v7.0.0 was the agentic frontier: an AI browser you can trust with real agency.
It added a trust foundation that keeps receipts on everything the agents do —
a **`/trust` dashboard**, permission scopes and risk levels, secret redaction,
and an append-only audit log — plus **WebMCP** tool bindings for websites,
**scheduled background agents** with a morning digest, and an **open Skills
marketplace**, all shipped with the official Windows installer.

Also in 7.0.0: the legacy blanket auto-approve bypasses
(`hook.auto_approve_all`, `CODING_AGENT_AUTO_APPROVE`/`CODING_AGENT_YOLO`,
`--auto-approve`/`--yolo`) became inert (deprecation warning, treated as OFF);
schedule-dashboard routes were gated through the approval hook; scheduled and
interactive agent runs serialize through a shared cross-process run lock;
Lucky identity (named apprentice voice, time-of-day greetings); slim chrome;
correct colors on older GPUs.

</details>

---

*Full changelog: [CHANGELOG.md](changelog.md)*
