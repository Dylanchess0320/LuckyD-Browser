"""A5: retrieval that works without ONNX + SmartContext as a tool.

- ``SmartContextEngine`` (file ranking + budget packing) shipped with
  zero product callers; it is now the ``FindRelevantFiles`` tool.
- The night-6 no-ONNX tests assumed the import was absent (they fail on
  any box with onnxruntime installed); absence is now simulated
  hermetically (see tests/test_night6_memory_vec.py).
"""

from __future__ import annotations

from unittest.mock import patch

import tools.context_tools  # noqa: F401  (register FindRelevantFiles)


def _project(tmp_path):
    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n"
        "    '''Authenticate a user against the account store.'''\n"
        "    return check_password(user, password)\n"
    )
    (tmp_path / "main.py").write_text("from auth import login\n\ndef run():\n    login('a', 'b')\n")
    (tmp_path / "notes.txt").write_text("grocery list: apples, bread, cheese\n")
    return tmp_path


class TestFindRelevantFiles:
    async def test_ranks_task_files_first(self, tmp_path):
        from tools.context_tools import FindRelevantFilesTool

        root = _project(tmp_path)
        out = await FindRelevantFilesTool().execute(
            query="user login flow", root=str(root), max_tokens=4000
        )
        assert out.error is False
        files = out.metadata["files"]
        assert any("auth.py" in f for f in files)
        # Task-relevant code outranks the unrelated text file.
        if any("notes.txt" in f for f in files):
            top = [f for f in files if "auth.py" in f or "main.py" in f]
            assert files.index(top[0]) < files.index(next(f for f in files if "notes.txt" in f))

    async def test_respects_token_budget(self, tmp_path):
        from tools.context_tools import FindRelevantFilesTool

        root = _project(tmp_path)
        out = await FindRelevantFilesTool().execute(query="login", root=str(root), max_tokens=500)
        assert out.metadata["total_tokens"] <= 500

    async def test_root_defaults_to_project(self):
        import tools.context_tools as ct

        seen = {}

        class _FakeEngine:
            def __init__(self, root="."):
                seen["root"] = root

            def build_context(self, task_query, max_tokens=8000):
                return []

        monkey_cls = patch.object(ct, "SmartContextEngine", _FakeEngine)
        with monkey_cls:
            out = await ct.FindRelevantFilesTool().execute(query="x")
        from config import PROJECT_DIR

        assert seen["root"] == str(PROJECT_DIR)
        assert "No relevant files" in out.text

    def test_registered_and_always_advertised(self):
        from core.agent_loop import ALWAYS_ADVERTISED_TOOLS
        from tools.registry import registry

        assert registry.get("findrelevantfiles") is not None
        assert "findrelevantfiles" in ALWAYS_ADVERTISED_TOOLS

    async def test_allowed_in_plan_mode(self):
        from tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool
        from tools.registry import registry

        with patch("llm.ProviderRouter"):
            from core.agent_loop import CodingAgent

            ag = CodingAgent(api_key="test-key-not-a-secret", model="m")
        await EnterPlanModeTool().execute()
        try:
            decision, _ = ag._permission_mode_decision(
                registry.get("findrelevantfiles"), "FindRelevantFiles"
            )
            assert decision == "allow"
        finally:
            await ExitPlanModeTool().execute(plan="cleanup")
