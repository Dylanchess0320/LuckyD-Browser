"""Regression tests for the 2026-09-10 overnight hardening swarm (root surface).

Each test fails on the pre-fix code and passes after the minimal fix.
Only the modules this worker owns are exercised:
main.py, ui.py, web_server.py, cline_bridge.py, tools/, llm/.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# The repo root is on sys.path via tests/conftest.py.


# ── helpers ────────────────────────────────────────────────────────────


class _FakeStream:
    """Minimal stand-in for the httpx response returned by client.stream()."""

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    def raise_for_status(self):
        pass


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient (stream-only usage)."""

    def __init__(self, lines, *args, **kwargs):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeStream(self._lines)


# ── 1. MCP registration must work when called from a running event loop ──


class TestMcpRegistrationAsync:
    async def test_register_mcp_tools_awaits_discovery(self):
        from core.mcp_client import MCPToolSchema
        from tools.mcp_tools import register_mcp_tools
        from tools.registry import registry

        class FakeManager:
            async def discover_tools(self):
                return {
                    "srv": [
                        MCPToolSchema(
                            name="ping",
                            description="p",
                            input_schema={"type": "object", "properties": {}},
                        )
                    ]
                }

        n = await register_mcp_tools(FakeManager())
        assert n == 1
        assert registry.get("mcp__srv__ping") is not None


# ── 2. OpenAI/DeepSeek streaming must merge tool_call deltas by index ───


def _openai_tool_call_stream_lines():
    def sse(obj):
        return "data: " + json.dumps(obj)

    return [
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "Read",
                                        "arguments": '{"file_path":',
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": ' "x.py"}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]


class TestStreamingToolCalls:
    def test_accumulator_merges_fragments_by_index(self):
        from llm import StreamingToolCallAccumulator

        acc = StreamingToolCallAccumulator()
        acc.add(
            [
                {
                    "index": 0,
                    "id": "a",
                    "type": "function",
                    "function": {"name": "Re", "arguments": "{'k':"},
                }
            ]
        )
        acc.add([{"index": 0, "function": {"arguments": " 1}"}}])
        acc.add(
            [
                {
                    "index": 1,
                    "id": "b",
                    "type": "function",
                    "function": {"name": "Write", "arguments": "{}"},
                }
            ]
        )
        calls = acc.calls()
        assert len(calls) == 2
        assert calls[0]["id"] == "a"
        assert calls[0]["function"]["name"] == "Re"
        assert calls[0]["function"]["arguments"] == "{'k': 1}"
        assert calls[1]["id"] == "b"

    async def test_openai_stream_tool_calls_merged(self, monkeypatch):
        import llm.openai_client as oc
        from llm import LLMConfig
        from llm.openai_client import OpenAIClient

        lines = _openai_tool_call_stream_lines()
        monkeypatch.setattr(oc.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(lines))
        client = OpenAIClient(LLMConfig(provider="openai", api_key="k", model="gpt-x"))
        result = await client.chat_stream([{"role": "user", "content": "hi"}])
        assert result.tool_calls == [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "Read",
                    "arguments": '{"file_path": "x.py"}',
                },
            }
        ]

    async def test_deepseek_stream_tool_calls_merged(self, monkeypatch):
        import llm.deepseek_client as dc
        from llm import LLMConfig
        from llm.deepseek_client import DeepSeekClient

        lines = _openai_tool_call_stream_lines()
        monkeypatch.setattr(dc.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(lines))
        client = DeepSeekClient(LLMConfig(provider="deepseek", api_key="k", model="deepseek-chat"))
        result = await client.chat_stream([{"role": "user", "content": "hi"}])
        assert result.tool_calls == [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "Read",
                    "arguments": '{"file_path": "x.py"}',
                },
            }
        ]


# ── 3/4. Shell history: shell filter actually filters; last_n clamped ───


def _reset_shell_history():
    import tools.session_tools as st

    saved = list(st._shell_history)
    st._shell_history.clear()
    return st, saved


class TestShellHistory:
    async def test_shell_filter_matches_recorded_shell(self):
        st, saved = _reset_shell_history()
        try:
            st.record_shell_command("echo hi", 0, "", shell="bash")
            st.record_shell_command("Get-ChildItem", 0, "", shell="powershell")
            tool = st.ShellHistoryTool()
            bash_only = await tool.execute(shell_filter="bash")
            assert "echo hi" in bash_only.text
            assert "Get-ChildItem" not in bash_only.text
            ps_only = await tool.execute(shell_filter="powershell")
            assert "Get-ChildItem" in ps_only.text
            assert "echo hi" not in ps_only.text
        finally:
            st._shell_history.clear()
            st._shell_history.extend(saved)

    async def test_last_n_zero_returns_empty(self):
        st, saved = _reset_shell_history()
        try:
            st.record_shell_command("echo one", 0, "", shell="bash")
            st.record_shell_command("echo two", 0, "", shell="bash")
            result = await st.ShellHistoryTool().execute(last_n=0)
            assert "No matching commands" in result.text
        finally:
            st._shell_history.clear()
            st._shell_history.extend(saved)

    async def test_last_n_clamped_to_documented_max(self):
        st, saved = _reset_shell_history()
        try:
            for i in range(105):
                st.record_shell_command(f"echo {i}", 0, "", shell="bash")
            result = await st.ShellHistoryTool().execute(last_n=500)
            assert result.title == "History (100 commands)", result.title
            assert result.metadata.get("count") == 100
            assert "echo 104" in result.text
        finally:
            st._shell_history.clear()
            st._shell_history.extend(saved)


# ── 5. MemoryForget must not delete on an empty memory_id ────────────────


class TestMemoryForget:
    async def test_empty_memory_id_rejected(self, monkeypatch):
        import tools.memory_tools as mt
        from tools.memory_tools import MemoryForget

        deleted = []

        class _Mem:
            """mem.graph.memories is a list of ID strings; mem.delete(id)."""

            class Graph:
                memories = ["abc123"]

            graph = Graph()

            def delete(self, mid):
                deleted.append(mid)
                return True

        monkeypatch.setattr(mt, "get_memory", lambda: _Mem())
        # An empty id matches every memory via str.startswith("") — it must be
        # rejected, never treated as "delete the first match".
        result = await MemoryForget().execute(memory_id="")
        assert result.error
        assert deleted == []


# ── 6. EditTool must reject an empty old_string ──────────────────────────


class TestEditTool:
    async def test_empty_old_string_rejected(self, tmp_path):
        from tools.file_tools import EditTool

        f = tmp_path / "f.txt"
        f.write_text("hello", encoding="utf-8")
        result = await EditTool().execute(
            file_path=str(f), old_string="", new_string="X", replace_all=True
        )
        assert result.error
        assert f.read_text(encoding="utf-8") == "hello"


# ── 7. ReadTool must not wrap on a negative offset ───────────────────────


class TestReadTool:
    async def test_negative_offset_clamped(self, tmp_path):
        from tools.file_tools import ReadTool

        f = tmp_path / "f.txt"
        f.write_text("\n".join(f"L{i}" for i in range(1, 11)), encoding="utf-8")
        result = await ReadTool().execute(file_path=str(f), offset=-3, limit=5)
        lines = result.text.splitlines()
        assert lines[0].rstrip().endswith("L1"), lines[:2]
        assert lines[4].rstrip().endswith("L5"), lines[:5]


# ── 8. Task persistence must create TASKS_DIR ────────────────────────────


class TestTaskPersistence:
    async def test_task_create_creates_tasks_dir(self, tmp_path, monkeypatch):
        import tools.task_tools as tt

        monkeypatch.setattr(tt, "TASKS_DIR", tmp_path / "deep" / "tasks")
        result = await tt.TaskCreateTool().execute(subject="T1")
        assert not result.error, result.text
        assert (tmp_path / "deep" / "tasks").exists()


# ── 9. Brain search must URL-encode the query ────────────────────────────


class TestHarnessSearch:
    async def test_brain_search_encodes_query(self, monkeypatch):
        import tools.harness_tool as ht

        captured = {}

        async def fake_get(path, timeout=10.0):
            captured["path"] = path
            return ({"results": []}, None)

        monkeypatch.setattr(ht, "_get", fake_get)
        out = await ht.HarnessTool()._brain_search("foo&bar baz")
        assert "q=foo%26bar%20baz" in captured["path"], captured["path"]
        assert "foo&bar" not in out.text


# ── 10. Watch wait must report the actual wait, never negative ───────────


class TestWatchWait:
    async def test_negative_timeout_clamped(self, tmp_path):
        from tools.utility_tools import WatchTool

        f = tmp_path / "f.txt"
        f.write_text("x", encoding="utf-8")
        w = WatchTool()
        arm = await w.execute(
            op="arm", path=str(f), condition="contains", contains_text="zzz-no-match"
        )
        watch_id = arm.text.split("[", 1)[1].split("]", 1)[0]
        result = await w.execute(op="wait", watch_id=watch_id, timeout_sec=-5)
        assert "timed out after 0s" in result.text, result.text


# ── 11. Deep research must not mutate global settings on invalid input ───


class TestDeepResearchSettings:
    async def test_settings_restored_after_invalid_backend(self):
        from features.deep_research.config import settings as drs
        from tools.deep_research_tool import DeepResearchTool

        before = (
            drs.research_rounds,
            drs.max_parallel,
            drs.provider,
            drs.search_backend,
        )
        result = await DeepResearchTool().execute(
            query="test", depth="quick", search_backend="bogus-backend"
        )
        assert result.error
        after = (
            drs.research_rounds,
            drs.max_parallel,
            drs.provider,
            drs.search_backend,
        )
        assert after == before, (before, after)


# ── 12/13/14. Browser: driver exit, intercept restart, state restore ─────


class _FakeRoute:
    def __init__(self):
        self.fulfilled = None

    async def fulfill(self, **kwargs):
        self.fulfilled = kwargs


class _FakeContext:
    def __init__(self):
        self.cookies_added = []

    async def storage_state(self):
        return {"cookies": [], "origins": []}

    async def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    async def new_page(self):
        return _FakePage(self)


class _FakePage:
    def __init__(self, context):
        self.context = context
        self.routes = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def close(self):
        pass


class _FakeBrowser:
    def __init__(self):
        self.contexts_kwargs = []
        self.closed = False

    async def new_context(self, **kwargs):
        self.contexts_kwargs.append(kwargs)
        return _FakeContext()

    async def close(self):
        self.closed = True


class _FakePW:
    """Stands in for the async_playwright() context manager."""

    instances: list = []

    def __init__(self):
        self.exited = False
        self.browser = _FakeBrowser()
        _FakePW.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False

    @property
    def chromium(self):
        return self

    async def launch(self, **kwargs):
        return self.browser


async def _fake_get_playwright():
    return _FakePW


@pytest.fixture
def fake_browser(monkeypatch):
    import tools.browser_tools as bt

    _FakePW.instances.clear()
    monkeypatch.setattr(bt, "_get_playwright", _fake_get_playwright)
    monkeypatch.setattr(bt, "_page", None)
    monkeypatch.setattr(bt, "_browser", None)
    monkeypatch.setattr(bt, "_pw_manager", None, raising=False)
    monkeypatch.setattr(bt, "_intercepts", [])
    monkeypatch.setattr(bt, "_storage_state_path", None, raising=False)
    monkeypatch.setattr(bt, "_device_config", None)
    return bt


class TestBrowserLifecycle:
    async def test_restart_stops_playwright_driver(self, fake_browser):
        bt = fake_browser
        await bt._get_page()
        mgr = _FakePW.instances[-1]
        assert not mgr.exited
        await bt._restart_browser()
        assert mgr.exited, "playwright driver process was not stopped on restart"

    async def test_intercept_mock_handler_is_awaitable(self, fake_browser):
        bt = fake_browser
        tool = bt.BrowserInterceptTool()
        r = await tool.execute(action="mock", url_pattern="**/x.png", status=418)
        assert not r.error, r.text
        pattern, handler = bt._page.routes[0]
        assert pattern == "**/x.png"
        assert asyncio.iscoroutinefunction(handler), (
            "route handler must be awaitable or the request hangs"
        )
        route = _FakeRoute()
        await handler(route)
        assert route.fulfilled is not None
        assert route.fulfilled["status"] == 418

    async def test_intercept_survives_restart(self, fake_browser):
        bt = fake_browser
        tool = bt.BrowserInterceptTool()
        r = await tool.execute(action="mock", url_pattern="**/x.png", status=418)
        assert not r.error, r.text
        await bt._restart_browser()
        page = await bt._get_page()  # must not raise KeyError
        assert len(page.routes) == 1
        assert page.routes[0][0] == "**/x.png"
        assert asyncio.iscoroutinefunction(page.routes[0][1])

    async def test_state_load_passes_storage_state(self, fake_browser, tmp_path, monkeypatch):
        bt = fake_browser
        monkeypatch.chdir(tmp_path)
        state = {
            "cookies": [
                {
                    "name": "c",
                    "value": "v",
                    "domain": "example.com",
                    "path": "/",
                }
            ],
            "origins": [
                {
                    "origin": "https://example.com",
                    "localStorage": [{"name": "k", "value": "v"}],
                }
            ],
        }
        (tmp_path / ".browser_state.json").write_text(json.dumps(state))
        r = await bt.BrowserStateTool().execute(action="load")
        assert not r.error, r.text
        ctx_kwargs = _FakePW.instances[-1].browser.contexts_kwargs[-1]
        assert ctx_kwargs.get("storage_state") == str(tmp_path / ".browser_state.json"), ctx_kwargs


# ── 15. Agent messaging must fail for unknown recipients ─────────────────


class TestAgentMessaging:
    def test_send_message_unknown_agent_returns_false(self):
        from tools.agent_orchestration import _send_message

        assert _send_message("no_such_agent_xyz", "main_agent", "hello") is False

    async def test_send_message_tool_unknown_agent(self):
        from tools.agent_orchestration import SendMessageTool

        result = await SendMessageTool().execute(to="no_such_agent_xyz", message="hello")
        assert result.error
        assert "Failed to send" in result.text


# ── 16. LLMConfig.from_env must not KeyError on explicit provider ────────


class TestLLMConfig:
    def test_explicit_provider_without_key(self, monkeypatch):
        from llm import LLMConfig

        monkeypatch.setenv("CODING_AGENT_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = LLMConfig.from_env()
        assert cfg.provider == "anthropic"
        assert cfg.api_key == ""


# ── 17. CostTracker must tolerate null token counts ──────────────────────


class TestCostTracker:
    def test_none_token_counts(self):
        from llm import CostTracker

        t = CostTracker()
        t.add_usage({"prompt_tokens": None, "completion_tokens": None}, "gpt-4o")
        assert t.total_input_tokens == 0
        assert t.total_output_tokens == 0
        assert t.total_cost >= 0


# ── 18. UI spinner ANSI fallback line must be cleared on stop ────────────


class TestSpinner:
    def test_ansi_spinner_line_cleared(self, capsys):
        from ui import TerminalUI

        ui = TerminalUI()
        ui.rich = False
        ui.start_spinner("working")
        ui.stop_spinner()
        out = capsys.readouterr().out
        # The clearing sweep (CR + spaces + CR) must have been written.
        assert "\r" + " " * (len("working") + 8) + "\r" in out


# ── 19. AskQuestion must tolerate malformed question objects ─────────────


class TestAskQuestion:
    async def test_malformed_question_no_crash(self):
        from tools.ask_question_tool import AskUserQuestionTool

        r = await AskUserQuestionTool().execute(questions=[{"header": "H"}])
        assert not r.error
        assert "H" in r.text

    async def test_empty_questions_rejected(self):
        from tools.ask_question_tool import AskUserQuestionTool

        r = await AskUserQuestionTool().execute(questions=[])
        assert r.error


# ── 20. Google streaming must preserve functionCall parts ────────────────


class TestGoogleStream:
    async def test_stream_keeps_function_calls(self, monkeypatch):
        import llm.google_client as gc
        from llm import LLMConfig
        from llm.google_client import GoogleClient

        lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"Working"}],"role":"model"}}]}',
            'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"Read","args":{"file_path":"x.py"}}}],"role":"model"}}]}',
            'data: {"candidates":[{"finishReason":"STOP"}]}',
        ]
        monkeypatch.setattr(gc.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(lines))
        client = GoogleClient(LLMConfig(provider="google", api_key="k", model="gemini-x"))
        result = await client.chat_stream([{"role": "user", "content": "hi"}])
        assert result.tool_calls, "streaming functionCall was dropped"
        assert result.tool_calls[0]["function"]["name"] == "Read"
        assert json.loads(result.tool_calls[0]["function"]["arguments"]) == {"file_path": "x.py"}


# ── 21. Bash timeout must kill the whole process tree ────────────────────


class TestBashTimeout:
    async def test_timeout_kills_process_tree(self, tmp_path):
        from tools.bash_tool import BashTool

        marker = tmp_path / "leaked.txt"
        # Helper script: forks a grandchild that writes the marker after 2s,
        # while the parent sleeps 30s (long past the tool timeout). If the
        # timeout only kills the direct child, the grandchild survives and
        # the marker appears. Run via "python3 <abs path>" to satisfy the
        # tool's command allowlist.
        script = tmp_path / "spawner.py"
        script.write_text(
            "import os, sys, time\n"
            "marker = sys.argv[1]\n"
            "pid = os.fork()\n"
            "if pid == 0:\n"
            "    time.sleep(2)\n"
            "    open(marker, 'w').write('leaked')\n"
            "    os._exit(0)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        tool = BashTool()
        result = await tool.execute(command=f"python3 {script} {marker}", timeout=400)
        assert result.error
        await asyncio.sleep(2.5)
        assert not marker.exists(), "grandchild process survived the timeout kill"

    async def test_powershell_startup_error_returns_tool_output(self, monkeypatch):
        import asyncio as _aio

        from tools.bash_tool import PowerShellTool

        async def boom(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "powershell.exe")

        monkeypatch.setattr(_aio, "create_subprocess_exec", boom)
        result = await PowerShellTool().execute(command="Get-ChildItem", timeout=5000)
        assert result.error
        assert "Error executing PowerShell command" in result.text


# ── 22. UI model catalog must not crash on an empty provider label ───────


class TestModelCatalog:
    def test_empty_provider_label(self, capsys):
        from ui import TerminalUI

        ui = TerminalUI()
        flat = ui.show_models(
            [
                {
                    "tier": "free",
                    "label": "Free",
                    "groups": [{"provider": "", "models": ["m1"]}],
                }
            ]
        )
        assert flat[1] == ("opencode", "m1")
        capsys.readouterr()  # discard rendered table
