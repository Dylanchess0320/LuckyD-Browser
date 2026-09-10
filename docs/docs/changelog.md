# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [6.0.0] - 2026-09-10

The agentic frontier, shipped: trust-first AI browsing.

**Added**

- **Official Windows installer** — `LuckyDBrowserSetup-6.0.0.exe`, built on a real Windows runner with Inno Setup 6. Per-user install, no admin needed, silent-install flags supported. Ships from the [releases page](https://github.com/Dylanchess0320/LuckyD-Browser/releases).
- **Trust foundation** — permission scopes and risk levels for every agent action, secret redaction in outputs, an append-only audit log, and an approval queue. Review everything the agents did on the **`/trust` dashboard** ("agentic with receipts").
- **WebMCP support** — websites can expose typed agent tools to LuckyD via a JS shim (native support wins when present). Tool registration, aliases, permission levels, and schema validation.
- **Scheduled/background agents** — cron-scheduled agents that run while you rest, with a **morning digest** of their runs. Manage them at **`/schedules`** (`python main.py schedule ...`).
- **Open Skills marketplace** — skill registries (bundled + remote) with hash-verified **install/update/remove/publish**; tampered or unparseable skills are refused.

**Security**
- **Legacy blanket auto-approve removed** — `hook.auto_approve_all`, `CODING_AGENT_AUTO_APPROVE`/`CODING_AGENT_YOLO`, and the `--auto-approve`/`--yolo` CLI flags are inert (deprecation warning, treated as OFF). Approvals may now only be skipped when the trust policy explicitly authorizes it (policy mode, per-scope/site rules, or the explicit per-run auto-approve-low-risk mode), and every skip is recorded in the audit log. The HQ schedule routes (create/update/delete/pause/run-now) are gated through the approval hook: they return HTTP 403 when the trust policy requires approval.
- **Shared run lock** — scheduled runs and interactive agent runs (CLI, HQ web server) now serialize through a cross-process file lock (`core/run_lock.py`) around run execution, so workspace files, the trust store, and the schedule store are never mutated concurrently.
- **WebMCP hardened against hostile pages** — tool arguments are now validated server-side against the schema captured at discovery, *before* anything is dispatched to the page (a malicious page's own validation can't be trusted), and every dispatch carries a 30-second timeout so a broken or malicious handler can't hang the agent. Discovery returns an opaque **binding token** (rotated on every discovery) that ties the registered tools to the page's origin: calls with a missing, forged, or cross-origin token are refused without touching the page, and stale tool registrations are purged when the tab navigates cross-origin.

**Fixed**
- **Zero-valued schedule fields preserved** — schedule create/update paths no longer drop legitimate `0` values (e.g. `max_retries=0`) via falsy checks; explicit `is None` checks are used instead (invalid zeros like `max_turns=0` now fail loudly with a validation error).
- **Overnight hardening sweep** — 32 bug fixes, each with a regression test, from a 4-worker bug hunt across the browser, deep-research, and coding-agent surfaces (research citation handling, streaming/CLI/shell edge cases, memory and file-tool robustness).

## [5.0.0] - 2026-09-10

The final, maxed-out release. Everything in 4.0, polished to a shine:

**Added**
- **Official Windows installer** — `LuckyDBrowserSetup-5.0.0.exe`, built on a real Windows runner with Inno Setup 6. Per-user install, no admin needed, silent-install flags supported.

**Changed**
- **Version unified to 5.0.0** across the package, installers, user-agent strings, and docs (guarded by the version-unification regression test).
- **Entire tree Ruff-formatted** — 33 files normalized, `ruff check` and `ruff format --check` clean, CI green.

## [4.0.0] - 2026-09-10

### Security
- **Cookie-based auth for all local services** — the Control API (`:9777`), Coding Agent HQ (`:8000`), and terminal WebSocket (`:9881`) now authenticate via HttpOnly session cookies (`luckyd_ctl`, `luckyd_hq`, `luckyd_term`) instead of bearer tokens in URLs or page source. No credential is ever embedded in served HTML again.
- **Deep Research XSS hardening** — report markdown is HTML-escaped before formatting, and link URLs are allowlisted to `http:`, `https:`, and `mailto:` (with `rel="noopener"`).
- **Cline bridge authentication** — `/v1/models` and `/v1/chat/completions` now require a bearer token (`CLINE_BRIDGE_TOKEN`, falling back to `CODING_AGENT_API_KEY`) and fail closed when none is configured.
- **Nav pages require auth** — `/dashboard`, `/research`, `/mesh`, and `/terminal` return 401 without the session cookie, closing the "any local page can drive the browser" gap.
- **Loopback-only origin enforcement** on the Control API, with bracketed IPv6 (`[::1]`) handled correctly.
- Minimal `Content-Security-Policy` headers on local HTML surfaces.

### Fixed
- `CheckpointManager.undo_to()` could loop forever when a checkpoint's target file was deleted; it now terminates.
- `undo_last()` no longer retries a checkpoint whose file is already gone.
- Windows updater exits cleanly on non-Windows systems; `CREATE_NO_WINDOW` is guarded.

## [3.9.0] - 2026-09-06

### Added
- **HTTPS-Only Mode** — public `http://` main-frame navigations upgrade to `https://`. Localhost, `.local`, and private LAN addresses are never rewritten (Settings toggle, default on).
- **Per-site permissions** — camera, microphone, location, notifications, pointer lock, and screen capture prompt once and remember Allow/Block per origin. Incognito never persists. Lock icon / Tools / Settings open the manager.
- **Workspaces** — named tab collections (File → Workspaces, command palette). Switching saves the current window into the active workspace, then loads the target.
- **Memory saver** — idle background tabs freeze after 5 minutes and discard after 15. Pinned, audible, current, and local platform tabs (dashboard / HQ / terminal) stay awake. Sleeping tabs show a 💤 prefix and wake on click.
- **Read Aloud** — `Ctrl+Shift+L` (toolbar 🔊, context menu) speaks the selection or the page via Windows SAPI; click again to stop.
- **Summarize This Page** — `Ctrl+Shift+U` (toolbar ✨) opens the AI sidebar and summarizes the current tab.

## [3.8.0] - 2026-09-06

### Added
- **Deep Research swarm as a native tool** — New `DeepResearch` agent tool (`tools/deep_research_tool.py`, vendored engine in `features/deep_research/`): planner -> parallel grounded workers -> citation-grounded synthesizer -> critic -> claim-level verifier -> finalizer, returning citation-backed markdown plus `data/deep_research/runs/` artifacts.
- **Multi-provider research backends** — Gemini native grounding, LuckyD stack (Ollama local free, DeepSeek, OpenAI, OpenRouter, ...), keyless DDG (+ frozen-safe HTML fallback), Tavily/Brave premium search (`TAVILY_API_KEY`/`BRAVE_API_KEY`), and an offline mock for tests.
- **Depth presets + context** — `depth=quick|standard|deep|max`, `context` injection (browser passes the current tab), `max_sources`, per-run budgets, SQLite cache.
- **Browser hooks** — Tools menu `Deep Research…` (`Ctrl+Shift+R`) and command-palette entry; frozen builds bundle the swarm (features datas + hiddenimports in all 3 PyInstaller specs).

## [3.7.0] - 2026-09-03

### Added
- **Hermes agent in the Agent Mesh terminal dock** — New Hermes chip (`mesh-hermes` shell) spawns `hermes chat` on its own ConPTY; a bare `hermes` exits instantly on closed stdin, so the shell pins the explicit `chat` subcommand (same bug class as the `mesh-dsh` web-profile fix). Availability is probed via PATH, like every other mesh chip.
- **Hermes regression test** — `test_hermes_shell_boots_chat` asserts the dock chip, shell label, and `hermes chat` spawn command.

### Fixed
- **DeepSeek harness mesh wiring** — Agent Mesh `doctor` now validates the `DEEPSEEK_API_KEY` against the live API instead of reporting a false OK when the key is revoked, and `mesh launch dsh` prints the LuckyD Browser tab workflow with port/auth pre-flight checks.

## [3.6.0] - 2026-08-27

### Added
- **LuckyD Code v3.6 & Browser Upgrade** — Unified v3.6.0 release across LuckyD Browser, LuckyD Code CLI, AI Sidebar Assistant, Terminal PTY server, and Coding Agent Workspace.
- **Audited Free AI Models Catalog** — Curated zero-cost ($0) AI models across OpenCode Zen, OpenRouter :free, Ollama, Groq, Z.ai, and Google; pruned unsupported/deprecated free models (such as `nemotron-3-super-free`).
- **Resilient Multi-Model Auto-Fallback** — Autonomous agent loop automatically detects free-tier rate limits or transient errors and hot-swaps to top-tier free fallback models (`nemotron-3-ultra-free`, `hy3-free`, `laguna-s-2.1-free`, `nemotron-3.5-lightning-free`, `deepseek-v4-flash-free`, `mimo-v2.5-free`, `muse-spark-1.2-contributor-free`, `big-pickle`).

### Fixed
- **CI/CD Quality & Formatting** — Fixed Black and Ruff linting/formatting issues across Python modules, tests, and CI workflows.

## [2.5.11] - 2026-08-27

### Added
- **Free Model Picker & Multi-Provider Hub** — Interactive `/model free` command, numeric selection (`model 12`), fuzzy search (`model nemotron`), and direct provider routing (`model opencode <name>`).
- **Terminal & Agent Enhancements** — Hardened multi-agent terminal integration, enhanced Agent Mesh CLIs, and improved session reliability.

## [2.5.10] - 2026-08-25

### Fixed
- **Hardened platform** — `settings.py` now uses `copy.deepcopy(DEFAULTS)` (fixes shared `zoom_levels` mutation), atomic `tmp+replace` saves with corrupt-file backup, and expanded `terminal_cli` migration; `session.py` atomic save with proper `prev` backup and corrupt handling; `terminal_server.py` NUL-sanitizes env block, validates mesh exe, 520-char Desktop buffer, max WS frame 1 MB, and generic spawn error; `control_server.py` now uses `hmac.compare_digest` (constant-time), 1 MB body limit, and DNS-rebinding `Host` check.
- **Build hygiene** — `browser/version_info.txt` now tracked via `!.gitignore` negation (fresh clones build), large local dirs (`LuckyD App/`, `youtube/`, `archive/*`) ignored.

## [2.5.9] - 2026-08-25

### Fixed
- **Terminal completely broken (every shell)** — `browser_core/terminal_server.py:_spawn_pty()` passed the environment as a `dict` to `pywinpty.PTY.spawn()`, which expects a NUL-joined block string (`"k=v\0..."`). `cffi` raised `argument env: 'dict' object is not an instance of str` and the bridge replied `[terminal failed to start: ...]` on every WS connection, so Agent 1, Agent 2, PowerShell, CMD, and all nine Agent Mesh CLIs were dead. Now builds `env_block = "\0".join(f"{k}={v}" …) + "\0"` matching `winpty/ptyprocess.py`. Bumped `browser/__init__.py`, `version_info.txt`, `installer/LuckyDBrowser.iss` to 2.5.9.

## [3.2.0] - 2026-08-12

### Added
- **ClinePass provider** — sign in with Cline as the agent's AI provider. The
  token resolves from the live Cline CLI session (`~/.cline`) and refreshes via
  WorkOS on expiry, so it never goes stale; `CLINEPASS_API_KEY` overrides.
- **Skills system** — `skills/*.md` with YAML frontmatter (name, description,
  version, author, tags) are auto-discovered; ships with `top-picks`,
  `ai-news-brief`, and `movie-picker`. `LUCKYD.md` defines intent patterns that
  auto-trigger the matching skill.
- **Project rules loader** — `LUCKYD.md` (plus AGENTS.md, .clinerules,
  .goosehints, et al.) is injected into the system prompt with per-file and
  total size caps.
- **Cline bridge + launcher** — `cline_bridge.py` and `Start-LuckyD-Cline.bat`
  wire the Cline CLI straight into the agent.
- **Codec self-check utility** — `tests/codec_check.py` reports which media
  codecs the QtWebEngine build supports (offscreen, no window).

### Changed
- **Browser version bumped to 2.2.0**; installer/version info updated.
- Browser Control API, dashboard, profile, and settings polish; terminal bridge
  and second-agent plumbing refined.
- Agent loop, background-process tool, file tools, logging setup, sandbox, CLI
  (`ui.py`), and web server improvements.

### Fixed
- **CI pipeline green and hardened** — secret scanning (gitleaks), dependency
  auditing, and SAST are now blocking gates (no `continue-on-error`); Safety
  replaced with pip-audit (Safety 3.x requires an interactive login that can
  never succeed in CI); all bandit medium+ findings remediated; black + ruff
  clean; the rules-loader test no longer picks up the repo's own `LUCKYD.md`.

## [3.1.0] - 2026-08-08

### Fixed
- **Fullscreen video was completely dead** — Qt WebEngine ships with the
  HTML5 Fullscreen API opt-in and OFF, so every player's fullscreen button
  threw "Fullscreen is not supported". Both profiles now enable
  `FullScreenSupportEnabled`, and the window honors the request properly:
  **all chrome (menu, tabs, omnibox, bookmarks, status bar, docks) hides**
  so the video gets the whole screen, then restores exactly on exit
  (including the maximized state). F11 during a video exits through the page
  so state stays consistent.
- **YouTube Shorts stalled/glitched** — the ad-blocker's `ctier` URL pattern
  matched YouTube's content-tier parameter on ordinary `videoplayback`
  fetches, so Shorts' video data was blocked outright. Pattern removed, with
  regression tests proving real stream URLs pass while ad URLs stay blocked.
- **YouTube ads playing again (server-side insertion)** — ads now ride in on
  googlevideo.com like content, invisible to domain blocking, and were
  scheduled by `/youtubei/v1/player` API calls nobody scrubbed. The userscript
  now runs at document-start and hooks fetch + XHR, **stripping ad placements
  from every player response before the player parses it** — ads never
  schedule. Short-circuit skip, cosmetic removal, and popup dismissal remain
  as fallback layers.
- **Control API `/snapshot` flake** — its page JS could lose the race against
  startup work (harness spawn, update check); the selftest now retries before
  failing.

### Added
- **Second terminal agent ("Agent 2")** — the terminal tab's shell bar gains
  an Agent 2 button next to Agent/PowerShell/CMD: it spawns the standalone
  coding-agent checkout (the Desktop shortcut's `run.bat` project) as an
  interactive REPL on its own ConPTY, booting in that project as its
  workspace. New `terminal_cli2` setting + `LUCKYD_CLI2` env override
  (accepts an exe, `main.py`, or `run.bat` via `cmd.exe /c`), plus a
  Tools → Agent 2 Terminal menu item.

### Changed
- **Browser version bumped to 2.1.0**; installer/version info updated.
- `browser/selftest.py` grew to 111 checks (fullscreen API enabled, chrome
  hide/restore, snapshot retry, 2nd-agent terminal wiring).
- Agent terminal overrides now boot in the override file's own folder, so a
  custom `terminal_cli` script acts on its own project as workspace.

## [3.0.0] - 2026-08-08

### Fixed
- **YouTube videos resuming mid-content after a blocked ad** — ads share the
  `<video>` element with the content, so the short-circuit skip polluted the
  playback position. The userscript now captures the content position when an
  ad starts (via the player's content-time API with the element as fallback)
  and restores it when the ad ends: **0s for pre-rolls, the resume point for
  mid-rolls** — and it restores your pre-ad mute state too.

### Added
- **Spell check everywhere** — both profiles now enable the OS spellchecker
  (en-US default), and the right-click menu shows top-4 suggestions that
  replace the misspelling in place.
- **Translate Page…** — right-click any page to open it in Google Translate
  (auto → English) in a new tab.
- **Read Later queue** (`Ctrl+Alt+S` or Bookmarks menu) — parks the page in
  a dedicated `readlater` list with its own 📖 submenu; kept out of the
  bookmarks bar and the main bookmark list.

### Changed
- **Browser version bumped to 2.0.0**; installer/version info updated.
- `browser/selftest.py` grew to 103 checks (read-later queue separation,
  spellcheck profile flag, YouTube fix shipped in the userscript).

## [2.8.0] - 2026-08-08

### Added
- **Scheduled workflows (autopilot)** — every saved workflow gets an
  interval picker on the Workflows page (Off / 15m / 30m / hourly / 6h /
  daily). A 30-second app tick replays whatever is due via the Control API
  backend, toasts the result, and records it (`last: 2/2 steps · 09:41`)
  in the row. Routes: `GET /schedules`, `POST /schedule`.
- **Full-page screenshots** (File → Save Full-Page Screenshot…) — CDP
  `Page.getLayoutMetrics` + `captureBeyondViewport` captures the entire
  scrollable document, not just the viewport (capped at 16384px).
- **Ad-blocker stats** — the dashboard gained a 🛡 pill showing live
  blocked-request counts, fed by `ads_blocked` in `GET /status`.
- **"What's New" toast** — the first launch after an update greets you
  with the release highlights (`browser.WHATS_NEW`), closing the
  auto-update loop.

### Changed
- Browser version bumped to 1.9.0; installer/version info updated.
- `browser/selftest.py` hit **100 checks** (schedules API round-trip,
  schedules UI, full-page capture entry point).
- `browser/test_browser_core.py` grew to 55 headless checks (schedule
  math, due detection, store round-trips).

## [2.7.0] - 2026-08-08

### Added
- **Side Pane** — right-click any link → "Open in Side Pane": a docked
  second web view (shares the profile — cookies, adblock) sized to a third
  of the window for reference reading without spending a tab.
- **Dashboard letter-tile favicons** — speed-dial tiles are now minted
  locally from the hostname (same hue hash as the app's `icons.py`):
  **the Google favicon service call is gone**, so your shortcuts never
  leave the machine. New one-tap tiles for Terminal, Workflows, Network.
- **Downloads: live speed + ETA + pause/resume** — per-download controls
  gained a pause/resume button, and in-progress rows show throughput and
  time remaining.
- **Close Duplicate Tabs** (tab context menu) — one click closes every
  URL that already has an open sibling (pinned tabs are never touched).

### Changed
- Browser version bumped to 1.8.0; installer/version info updated.
- The shortcuts reference (`Ctrl+/`) now lists Reader Mode, Focus Mode,
  the terminals, the `?` omnibox prefix, and everything since 1.4.
- `browser/selftest.py` grew to 95 checks (dashboard tiles, side pane,
  duplicate closing).

## [2.6.0] - 2026-08-08

### Added
- **Vertical tabs** (`browser_ui/vertical_tabs.py`) — an Arc-style left dock
  with readable rows: site tiles, group-color text, click to switch,
  middle-click to close, drag to reorder, the full tab context menu. Toggle
  in View → Vertical Tabs (persisted); the top bar hides while it's on.
- **Network monitor** (`browser_core/netmon.py`, Tools → Network Monitor) —
  a live request log of the active tab over raw CDP (Network domain):
  method/status/type/size/time rows streaming into a themed page at
  `127.0.0.1:9777/network`, with a text filter, in-place row updates, and
  one-click **HAR 1.2 export** (`GET /network/har`). Control API routes:
  `GET /network`, `GET /network/events?since=N`, `POST /network/start|stop|clear`.
- **Focus mode** (`Ctrl+Shift+F`) — one key strips every chrome surface
  (menu bar, nav bar, bookmark bar, status bar, docks) for pure content;
  pressing it again restores exactly what was open.
- **Omnibox AI ask** — start a query with `?` in the address bar to send it
  straight to the AI sidebar (`? explain quantum computing`).

### Changed
- Browser version bumped to 1.7.0; Control API bumped to 1.4.0.
- `browser/selftest.py` grew to 91 checks (netmon page + capture lifecycle +
  HAR export, vertical-tabs toggle, focus mode hide/restore, sidebar ask).
- `browser/test_browser_core.py` grew to 52 headless checks (netmon reducer,
  row cap, HAR builder).

## [2.5.0] - 2026-08-08

### Added
- **Tab groups** — right-click any tab → Tab Group: create named, colored
  groups (6-color rotation, color strip under grouped tabs, group tooltips).
  Groups **collapse** to a single chip tab (`▸ name · n`) that keeps its
  pages alive, and they're **persisted through session restore** (names,
  colors, collapsed state all survive a restart).
- **AI Tab Organizer** (Tools → Organize Tabs with AI) — the configured AI
  provider clusters your open tabs by topic and builds the groups for you,
  with JSON-loose parsing of the model's reply and full bounds-checking.
- **Reader Mode** (`Ctrl+Alt+R`, View menu) — a readability-lite extractor
  (text-density scoring, link-density penalty) distills articles into a
  clean serif view tinted with the active theme; F5 or the shortcut exits.
- **Copy Link to Highlighted Text** — right-click a selection to copy a
  `#:~:text=` fragment link that deep-scrolls to the quote in Chromium.
- **Reopen Previous Session** (File menu) — the session store now rotates
  one backup generation (`session.prev.json`) on every save.

### Changed
- Browser version bumped to 1.6.0; installer/version info updated.
- `browser/selftest.py` grew to 83 checks (tab-group lifecycle: assign,
  collapse chip, session round-trip, AI-group application, empty dissolve).
- `browser/test_browser_core.py` grew to 48 headless checks (session group
  fields, prev rotation, reader template).

## [2.4.0] - 2026-08-07

### Added
- **Multi-terminal tabs ("second terminal", all built in)** — the in-browser
  terminal now spawns one independent ConPTY session per tab and per shell:
  the LuckyD Code **agent CLI** (Ctrl+`), a plain **PowerShell** console
  (Ctrl+Shift+`), or **CMD**. A shell bar in the terminal page switches
  shells live (reconnect = fresh session); tabs are titled per shell.
  Shell names are allowlisted server-side (`browser_core/terminal_server.py`)
  — the WS query can never inject a command line.
- **Workflow recorder & replayer** (`browser/browser_core/workflows.py`) —
  record Control-API traffic (`/navigate` + `/act`) into named JSON
  workflows, replay them later against the live tab. Indexed steps store an
  element *fingerprint* (tag/text/id/name/aria/href) at record time; replay
  re-snapshots the page and scores candidates so steps land on the right
  element even after re-renders (**self-healing**, Stagehand/browser-use
  style), with the recorded index as fallback. Failed actions are never
  recorded; replay stops at the first broken element step instead of
  clicking blind. Manager page at `http://127.0.0.1:9777/workflows`
  (Tools → Workflows…) with live recorder status and a per-step replay log.
- **`POST /extract`** (`browser/browser_core/extract.py`) — Stagehand-style
  structured extraction: an instruction plus an optional JSON schema turns
  the active page's visible text into parsed JSON via the configured AI
  provider. Lenient parser tolerates fences, surrounding prose, and trailing
  commas (small-model habits).
- Control API bumped to 1.3.0 — new routes: `GET /workflows`,
  `GET /workflows/list`, `POST /workflow/record|stop|replay|delete`,
  `POST /extract`.
- **Site letter-tile icons** (`browser_ui/icons.py`) — offline favicon
  stand-ins: rounded gradient tiles minted from each site's initial with
  hues derived from the hostname (stable identity, zero network). Used by
  the bookmarks bar, the command palette, and tab hover previews.
- **Bookmark bar restyle** — per-site tiles, pill hover states, themed QSS,
  proper text-beside-icon rendering.
- **Clickable zoom pill** — the status-bar zoom indicator is now a themed
  button: click resets to 100%; accent highlight while zoomed.
- **Command palette upgrades** — theme-synced styling (re-applied on every
  open), tab entries now *switch* to the open tab instead of duplicating it,
  site tiles on bookmark/history rows, and new actions: Agent Terminal,
  PowerShell Terminal, Workflows, Save Screenshot, Toggle Bookmarks Bar.
- **Synthwave Sunset theme** — the secret fifth theme (hot pink × cyan on
  deep purple). Unlock with the Konami code (↑↑↓↓←→←→BA) on the new-tab
  page, or just pick it in Settings. New `POST /theme` Control API route
  switches themes live on every window.
- **Offline arcade** — the connection-error page is now a playable canvas
  endless-runner (Space/click to hop, best score tracked) while you wait
  for the network.
- **Dashboard personality** — time-aware greeting with rotating LuckyD
  taglines; flawless workflow replays fire a confetti burst on the manager
  page.

### Fixed
- **Userscript engine was silently dead** — `_glob_to_regex` never escaped
  `/`, so the generated guard wrapper's regex literal terminated early and
  every built-in userscript threw `Uncaught SyntaxError` on every page.
  Fixed and regression-tested; browser console is now completely clean on
  startup. "Dark Mode Everywhere" (invert-everything) is now opt-in by
  default via `userscript_disabled`; YouTube Ad-Block and Video Speed
  Controller actually run for the first time.
- **Auto-update was broken end-to-end** — `UpdateChecker` was constructed
  with a version string as its update source and connected to a nonexistent
  `noUpdate` signal, so every check died silently; the payload is a dict,
  not a `ReleaseInfo`; and `_apply_update` would have moved the *installer*
  over the running exe. Now correctly wired, the prompt previews the release
  notes, and the apply step runs the Inno installer (`/VERYSILENT
  /NORESTART`) from a self-deleting .bat that relaunches the browser —
  session restore brings your tabs back after the update.
- **Selftest GUI deadlock** — `finish()` joined the API thread on the GUI
  thread while API calls still needed the event loop; it now polls with
  QTimer, making the API check section deterministic.
- **Frozen build on Python 3.10.0** — `pygame`/`werkzeug`/`flask` pulled in
  transitively crashed modulegraph's bytecode scan (`dis` tuple-index bug),
  failing the PyInstaller build; excluded like PIL before them. Packaged
  builds also gained a `crash.log` excepthook (windowed apps have no console,
  so startup tracebacks used to vanish into a bare error dialog).

### Changed
- Browser version bumped to 1.5.0; installer/version info updated.
- `browser/selftest.py` grew to 78 checks (terminal shell injection +
  sanitizing, workflows page, userscript wrapper validity, live theme
  switching, and a full live workflow record→save→list→replay→delete cycle
  through the Control API).
- `browser/test_browser_core.py` grew to 44 headless checks (shell
  allowlist, WS query parsing, workflow matching/storage, JSON-loose
  parsing, glob-to-regex escaping, site-tile helpers, updater version math,
  theme completeness).

## [2.3.0] - 2026-08-07

### Added
- **Session restore** (`browser/browser_core/session.py`) — "continue where you
  left off": every normal window snapshots its tabs (URL, title, pinned state,
  active index) to `session.json` via a debounced autosave plus a guaranteed
  save on window close; startup reopens all windows when the new
  `startup_mode` setting is `restore` (the default). Atomic tmp-file writes,
  corrupt/incompatible files fall back to a clean start, safety caps
  (5 windows × 50 tabs), incognito windows are never saved or restored, and
  `LUCKYD_SESSION_PATH` redirects the file for tests/portable runs.
- **Bookmarks bar** — toggleable strip under the navigation toolbar
  (`Ctrl+Shift+B`, View menu, or Settings). Click opens in the current tab;
  right-click offers Open in New Tab / Copy URL / Remove Bookmark. Refreshes
  live on add/remove/import; empty state shows a hint. Persisted via the new
  `bookmark_bar_visible` setting.
- **Save Screenshot** (`Ctrl+Shift+S`, File menu) — captures the visible page
  through the CDP page target (GPU-safe; `QWidget.grab()` returns blank on Qt
  WebEngine), saves via the download folder with a timestamped
  `screenshot-<host>-<stamp>.jpg` name (PNG transcode supported), toast
  confirmation. `browser_core.screenshot.suggested_name()` generates the names.
- **Per-site zoom memory** (`browser/browser_core/zoom.py`) — zooming a page
  (now also with **Ctrl+Scroll**) is remembered per origin in `zoom_levels`
  and re-applied on the next visit; resetting to 100% forgets the site.
  Global **default zoom** picker (50–200%) in Settings (`zoom_factor`,
  previously unused, is now wired up); both gated by the `zoom_remember`
  setting.
- `browser/test_browser_core.py` — 20 headless pytest checks for session,
  zoom and screenshot helpers (no Qt needed).

### Fixed
- **Terminal shortcut collision** — `Ctrl+Shift+T` was double-bound (Reopen
  Closed Tab *and* Tools → Terminal), making both unreliable. Terminal moves
  to `` Ctrl+` `` (VS Code-style); Reopen Closed Tab keeps the
  browser-standard `Ctrl+Shift+T`.
- **Pin Tab crashed** — `BrowserTabWidget` called `setTabButton`/`tabButton`
  on itself, an API PySide6 no longer forwards; rerouted through
  `QTabBar`, which also completes the pinned-tab half of session restore.

### Changed
- Browser version bumped to 1.4.0; installer/version info updated.
- `browser/selftest.py` grew from 44 to 64 checks (session, zoom, bookmark
  bar, pinned tabs, screenshot naming; session storage isolated via
  `LUCKYD_SESSION_PATH` so dev sessions can't skew the run).

## [2.2.0] - 2026-07-27

### Added
- **Browser Control API** (`browser/browser_core/control_server.py`) — localhost
  HTTP control of the live LuckyD Browser (`127.0.0.1:9777`): status, tabs,
  navigate, page snapshots (same element indexing the AI agent uses),
  click/type/press/select/scroll actions, CDP screenshots, raw JS eval, and
  page-grounded AI answers. Optional bearer-token auth; Tools-menu toggle.
  Lets the `luckyd-code.exe` harness, the terminal agent, and scripts drive
  real tabs — "exe brain, browser hands".
- **Harness mode for the browser AI sidebar** — agent tasks can now run on the
  `luckyd-code.exe` backend (98 tools, memory graph, orchestration) via a new
  checkbox. The exe auto-starts when missing, runs via `/api/background/*`
  with live progress, and tasks auto-include Browser Control API instructions.
- `browser/test_control_server.py` — 23 checks for the Control API HTTP layer
  (fake backend, no Qt needed).
- **Harness mode is now the DEFAULT agent path** — no button to press;
  the checkbox (persisted via the `harness_mode` setting) is now an opt-out.
- **Vision steps are automatic** — `AIBridge.supports_vision()` detects
  image-capable models by family (gpt-4o/4.1, gemini, claude-3/4, gemma3,
  qwen-vl, llava, pixtral, …); the sidebar auto-enables per-step
  screenshots for them and hard-gates image payloads away from
  text-only models.
- `browser/selftest.py` grew from 15 to 22 checks (Control API module + live
  end-to-end API calls against the running browser).

### Fixed
- **PyInstaller build** — excluded `PIL` from `LuckyDBrowser.spec`: Pillow
  12.3's `Image.py` crashes Python 3.10.0's `dis` during modulegraph scanning
  (`IndexError: tuple index out of range`); the browser never imports PIL at
  runtime. `websockets` pinned as a hidden import.
- Harness-mode checkbox was dead code — `_start_agent` never routed to it.
- Harness worker emitted `None` on a `Signal(str)`, dropped the orchestrate
  result on the floor, and stopped via `terminate()` — replaced with a
  cooperative-cancel worker that renders results in chat.
- `default_provider()` always preferred `cline-usage` (registered even with no
  credentials), so keyless local providers could never be the default —
  restored the documented local-first order, Cline providers only when
  authed.
- Control API `close_tab` read a closure variable before assignment
  (would crash when closing the current tab).
- `main_window.py` duplicate `show_downloads` definition removed.
- `_body_gemini` / `_body_anthropic` now forward image parts
  (`inlineData` / base64 image blocks) — vision previously only worked
  on OpenAI-compatible endpoints; images were silently dropped.

### Changed
- Browser version bumped to 1.2.0; `browser_api_enabled` (default on),
  `browser_api_port`, `browser_api_token`, `harness_mode` (default on)
  settings added.

## [2.1.0] - 2026-07-22

### Added
- **MCP (Model Context Protocol) support** — connect to any MCP server via
  `mcp_config.json` (claude-desktop / goose / cline compatible). Discovered
  tools auto-register in the tool registry as `mcp__<server>__<tool>`.
- **Z.ai (GLM) provider** — `ZAI_API_KEY` / `glm-4.5` (OpenAI-compatible endpoint).
- **OpenRouter provider** — `OPENROUTER_API_KEY` with 200+ models.
- **AGENTS.md / project rules** — auto-loads `AGENTS.md`, `.clinerules`,
  `.goosehints`, `CLAUDE.md` from the workspace into the system prompt.
- **Session persistence** — every run is auto-saved to `data/sessions/`.
  Resume with `--continue`, `--resume <id>`, or `/sessions` + `/resume`.
- **Tool approval system** — interactive y/n/a prompts in the REPL, `--yes`
  to auto-approve (non-interactive / CI mode). Permission levels per tool.
- **Token & cost tracking** — `/cost` command and cost summary in goodbye.
- **New slash commands** — `/cost`, `/undo`, `/sessions`, `/resume`, `/mcp`,
  `/version`.
- **CLI flags** — `--yes/-y`, `--max-turns`, `--continue/-c`, `--resume`,
  `--provider`, `--version/-v`.
- `mcp_config.example.json` — example MCP server configuration.
- `core/session_store.py` — file-backed session persistence (save/load/list).
- `core/rules_loader.py` — multi-format project rules loader.
- `core/mcp_client.py` — MCP stdio transport + manager.
- `tools/mcp_tools.py` — MCP tool adapter + `MCPList` management tool.

### Fixed
- `switch_provider()` now rebuilds `llm_client` with new credentials (was
  only updating the router, so runtime provider switching was broken).
- Streaming now captures token usage (`include_usage`) for cost tracking.
- `conftest.py` mocks for new providers (zai, openrouter).

### Changed
- `MessageBuilder.build_system()` accepts `project_rules` parameter.
- Version bumped to 2.1.0.
- `test_tools.py` — file tools, bash tool, registry tests
- `agent.py` rewritten as backward-compatibility shim re-exporting `core.agent_loop.CodingAgent`
- `LLMResult` now has `get()` and `to_dict()` for dict-like interface compatibility
- `MemoryGraph.summarize()` implemented (was missing)
- `core/agent_loop.py` — fixed parameter names (`on_token`/`on_think`), added `_emit_event()`, added `try/except` around `chat_stream`

### Fixed
- SyntaxError: stray `]` in `logging_setup.py`
- `ProjectDetector().detect()` throwing `AttributeError` (missing `_detect_package_manager`)
- `toml` hard dependency in `config.py` — now falls back to JSON if `pyproject.toml` missing
- All Python files now parse cleanly (verified with `ast.parse` sweep)

### Security
- No real API keys or network calls in test suite (all mocked)
- Sensitive keys redacted in logs

---


## [2.0.0] - 2026-07-22

### Added
- Complete project infrastructure overhaul
- `pyproject.toml` with Ruff, Black, Mypy, pytest config
- `setup.py` for pip-installable package
- `requirements.txt` and `requirements-dev.txt` with pinned dependencies
- `Makefile` with 30+ commands (test, lint, format, security, docker, etc.)
- `.pre-commit-config.yaml` with 15+ automated checks
- `.editorconfig` for consistent editor settings
- `.github/workflows/ci.yml` — full CI/CD pipeline with 5 job stages
- `Dockerfile` — multi-stage build (builder → slim runtime)
- `docker-compose.yml` with optional Ollama and Redis services
- `logging_setup.py` — structured JSON logging with redaction and timing
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`
- `docs/` directory — MkDocs-based documentation site
- Test directory structure with conftest.py and async fixtures
- `.env.example` template with all providers documented
- `.dockerignore` for lean images

### Changed
- Enhanced `.gitignore` to cover all build artifacts and secrets
- Project version bumped from 1.3.6 → 2.0.0

### Fixed
- [List fixed issues]

### Security
- Added dependency scanning (Safety) to CI
- Bandit security scanning in CI pipeline
- Sensitive data redaction in logging
- Pre-commit hooks for detecting private keys

## [1.3.6] - 2026-07-XX

### Added
- Initial public release
- Multi-provider LLM support (DeepSeek, OpenAI, Anthropic, Google, Ollama)
- 20+ coding tools
- Memory graph with BM25 search and ONNX embeddings
- VS Code extension integration
- Web chat interface
- Project intelligence engine
