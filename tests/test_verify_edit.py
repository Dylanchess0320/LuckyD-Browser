"""P2: verify-after-edit failure recovery.

A Write/Edit that leaves a .py file uncompilable now surfaces a syntax
error immediately (instead of reporting success), so the next turn
fixes real code. Non-Python files and unreadable paths pass through.
"""

from __future__ import annotations

from unittest.mock import patch

import tools.file_tools  # noqa: F401 (register Write for the tool-loop tests)
from core.agent_loop import CodingAgent


def _make_agent():
    with patch("llm.ProviderRouter"):
        ag = CodingAgent(api_key="test-key-not-a-secret", model="test-model", temperature=0.0)
    ag._result_handler = None
    ag.permission_mode = "bypassPermissions"
    return ag


class TestVerifyPythonEdit:
    def test_broken_python_flagged(self, tmp_path):
        target = tmp_path / "bad.py"
        target.write_text("def broken(:\n", encoding="utf-8")
        err = CodingAgent._verify_python_edit({"file_path": str(target)})
        assert err is not None and err.startswith("Error:")
        assert "line 1" in err

    def test_good_python_passes(self, tmp_path):
        target = tmp_path / "good.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert CodingAgent._verify_python_edit({"file_path": str(target)}) is None

    def test_non_python_ignored(self, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("def broken(:\n", encoding="utf-8")
        assert CodingAgent._verify_python_edit({"file_path": str(target)}) is None

    def test_missing_file_fail_open(self, tmp_path):
        assert CodingAgent._verify_python_edit({"file_path": str(tmp_path / "no.py")}) is None
        assert CodingAgent._verify_python_edit({}) is None

    async def test_write_reports_syntax_error(self, tmp_path):
        ag = _make_agent()
        out = await ag._execute_tool(
            "Write", {"file_path": str(tmp_path / "w.py"), "content": "def broken(:\n"}
        )
        assert out["content"].startswith("Error:")
        assert "syntax error" in out["content"]

    async def test_write_success_untouched(self, tmp_path):
        ag = _make_agent()
        out = await ag._execute_tool(
            "Write", {"file_path": str(tmp_path / "w.py"), "content": "x = 1\n"}
        )
        assert not out["content"].startswith("Error:")
