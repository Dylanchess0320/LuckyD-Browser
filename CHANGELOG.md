# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
