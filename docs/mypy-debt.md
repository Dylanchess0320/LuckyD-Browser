# mypy typing debt — burn-down list

The CI mypy gate is **hard** (any error in a non-grandfathered module fails
the build). The modules below predate the gate and are explicitly
grandfathered in `pyproject.toml` (`[[tool.mypy.overrides]]`,
`ignore_errors = true`).

**Debt as of 2026-10-19 (v10.7.5): 62 errors in 17 modules** (down from 140
errors in 39 modules in v10.7.4, 182 errors in 57 modules on 2026-09-13, and
~1,400 originally under `strict = true`; `tools/` and `browser/` now have
**zero** grandfathered modules; the gate enforces every strict check except
the four annotation-coverage flags the codebase was never written for — see
`pyproject.toml`).

## How to burn it down
1. Pick a module, fix its errors for real (no drive-by `# type: ignore`).
2. Run `.venv/bin/python -m mypy <module path>` — must be clean.
3. Remove the module from the grandfather list in `pyproject.toml`.
4. Update the counts below.

## Inventory (errors per module)
- `core/agent_loop.py` — 14
- `core/multi_model_orchestrator.py` — 8
- `core/project_intelligence.py` — 6
- `features/deep_research/models/openai_compat.py` — 5
- `core/run_lock.py` — 5
- `features/deep_research/graph.py` — 4
- `memory/store.py` — 3
- `llm/anthropic_client.py` — 3
- `core/code_sandbox.py` — 3
- `memory/graph.py` — 2
- `memory/embeddings.py` — 2
- `core/parallel_executor.py` — 2
- `core/mcp_client.py` — 2
- `core/approval_hook.py` — 2
- `core/advanced_memory.py` — 2
- `core/advanced_debugging.py` — 2
- `features/deep_research/runtime/tui.py` — 1

## Already fixed
- **2026-10-19 (v10.7.5):** Burned down 22 more modules — achieving 100% strict mypy coverage across all of `tools/` and `browser/` (`browser.browser_core.ai_bridge`, `browser.browser_core.control_server`, `browser.browser_core.netmon`, `browser.browser_core.profile`, `browser.browser_core.scheduler`, `browser.browser_core.terminal_server`, `browser.browser_core.workflows`, `browser.browser_core.workspaces`, `browser.browser_ui.ai_sidebar`, `browser.browser_ui.main_window`, `browser.browser_ui.tab_widget`, `core.scheduler`, `sandbox`, `tools.bash_tool`, `tools.browser_tools`, `tools.file_tools`, `tools.git_tools`, `tools.harness_tool`, `tools.lsp_tools`, `tools.memory_tools`, `tools.schedule_tools`, `tools.skill_market_tools`, `tools.task_tools`, `tools.utility_tools`, `tools.web_tools`).
- **2026-10-19 (v10.7.4):** Burned down 18 modules (`browser.browser_core.agent`, `browser.browser_core.harness_bridge`, `browser.browser_core.settings`, `core.context_manager`, `core.llm_client`, `core.marketplace`, `core.schedule_runner`, `core.smart_context`, `features.deep_research.cli`, `features.deep_research.evals.harness`, `features.deep_research.runtime.budget`, `features.deep_research.tools.fetch`, `features.deep_research.workers.finalizer`, `features.deep_research.workers.verifier`, `llm`, `llm.google_client`, `project.detector`, `tools.skill_tools`).
- **2026-09-13:** 13 stale `# type: ignore` comments removed (`warn_unused_ignores`), 7 missing variable annotations added (`var-annotated`), 3 untyped-decorator sites documented via grandfathering.
