# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
