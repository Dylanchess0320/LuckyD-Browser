# LuckyD 10.4.0 — implementation report

v10.4.0 ships two features:

1. **Cline credit honesty** — applied 2026-09-24 via `apply_104.py`
   (two-phase verify-then-write: all 77 anchors matched exactly before
   anything was written). No feature removed; nothing outside this
   worktree touched.
2. **Free-model auto-rotation (default)** — added 2026-09-24 per Dylan's
   requirement: every LuckyD tool auto-rotates the best FREE AI models by
   default (CLI, browser, assistant, everything). New module
   `core/free_rotation.py`; default priority Cline (free tier) → Gemini
   (free tier) → Ollama local (`llama3.2:3b`, terminal fallback) →
   other free providers. Rotation triggers: HTTP 402/403/429, 408, 5xx,
   timeouts, connection errors (401 deliberately excluded — a dead key
   fails everywhere). Wired into `detect_provider()` /
   `resolve_provider_config()` (CLI + backend default), the browser
   `AIBridge.default_provider()`, and the agent's 403 leg
   (`_rotate_free_models`). Explicit picks (`CODING_AGENT_PROVIDER`,
   `--provider`, sidebar saved provider) always win; paid/keyed providers
   untouched.

## Result

- Cline-credit focused pytest: **exit=0 passed=265 failed=0 errors=0
  skipped=0**
- Free-rotation focused pytest: **exit=0 passed=134 failed=0 errors=0
  skipped=0** (`test_free_rotation` 41 tests + `test_rotation_escape` +
  `test_providers_list` + `test_cline_credit` + `test_cov_providers`);
  extra `test_cov_agent_loop` + `test_cov_ai_bridge`: **141 passed**
- `ruff check` and `ruff format --check` clean on all changed files

## Files changed

New (5): `core/cline_credit.py`, `core/free_rotation.py`,
`release-notes-10.4.0.md`, `tests/test_cline_credit.py`,
`tests/test_free_rotation.py` + `IMPLEMENTATION_REPORT.md`
(this file).
Edited (26): `CHANGELOG.md`, `README-LuckyD-Browser.md`, `README.md`, `acp_server.py`, `apps/luckyd-ui/src/views/Agents.tsx`, `browser/__init__.py`, `browser/browser_core/ai_bridge.py`, `browser/browser_core/terminal_page.py`, `browser/browser_core/terminal_server.py`, `browser/browser_ui/ai_sidebar.py`, `browser/installer/LuckyDBrowser.iss`, `browser/version_info.txt`, `build_nuitka.py`, `core/llm_client.py`, `core/providers.py`, `docs/docs/changelog.md`, `docs/docs/getting-started.md`, `docs/docs/index.md`, `docs/docs/release-notes.md`, `main.py`, `pyproject.toml`, `tests/conftest.py`, `tests/test_cov_ai_bridge.py`, `tests/test_cov_terminal_server.py`, `tests/test_security_40.py`, `ui.py`.

Behavior: Cline HTTP 402 records `~/.luckyd/cline_credit_state.json`
(timestamp + reason, 24 h TTL); while valid, `list_providers()` /
`detect_provider()` / browser `default_provider()` skip Cline, the provider
UIs show an exhausted indicator, and `lucky-code providers
--clear-credit-state` clears the marker. No balance API — the 402 is the
only trigger. All current version strings unified at 10.4.0.

## Preserved on purpose (not oversights)

- CHANGELOG/docs history for 10.2.3/10.2.1, release-tag URLs, 10.2.3
  code comments, the `test_security_40.py` 10.2.0 stale-guard (plus a
  new 10.2.3-absence guard), and `muse-prompt.txt` (task prompt, not
  product).
- `luckyd-code-v10.2/v9.7.exe` probe fallbacks (v10.4 added first).
- `providers_config.json` static `default_provider` (not a runtime selector).
- Explicit Cline selection still wins while exhausted (documented override).

## Skipped

- Nothing in (a)–(d). Full suite + `ruff check` left to CI (focused sets
  above cover every touched area). Delete `apply_104.py` and
  `adapt_104.py` after review.

## Pytest tail

```
........................................................................ [ 27%]
........................................................................ [ 54%]
........................................................................ [ 81%]
.................................................                        [100%]
=============================== warnings summary ===============================
tests/test_security_40.py::TestClineBridgeAuth::test_bridge_requires_bearer_token
  /home/hatch/workspace/LuckyD-Browser/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
265 passed, 1 warning in 10.08s
```
