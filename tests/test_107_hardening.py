"""Regression tests for v10.7 loop hardening.

1. Input cap: CodingAgent.run() rejects oversize user messages with a clear
   error (never silently corrupts); web_server._body() rejects huge bodies
   with _BodyTooLargeError (do_POST maps it to 413).
2. PTY->WS chunking: pump_out must not issue a single giant ws.send().
3. Recorded rotation: mid-run provider/model swaps emit MODEL_ROTATED on the
   session/event stream; CODING_AGENT_PIN_PROVIDER=1 disables rotation.
4. Durable task registry: _save_tasks/_load_tasks round-trip; stale
   running/queued tasks are failed on startup.
5. Retry policy: retryable_codes covers 429 + the full 5xx range.
6. Summarize on truncation: when compaction fails, the 40-message fallback
   splices a deterministic structural summary instead of deleting silently.
7. Delegation quarantine: large delegated results go to disk; only a pointer
   note enters context.
"""

from __future__ import annotations

import io
import json
import threading
from unittest.mock import AsyncMock, patch

import pytest

import tools.session_tools  # noqa: F401 — registers TodoRead/TodoWrite for the loop tests

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "eval-fake-key-not-a-secret"


def _make_agent():
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(
            api_key=TEST_API_KEY,
            model="test-model",
            temperature=0.0,
            permission_mode="bypassPermissions",
        )
        # Memory extraction spawns a real background LLM call; neutralize it.
        ag._extract_session_memories = AsyncMock()
        return ag


def _tool_call(name, call_id, arguments="{}"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _llm_result(text="", tool_calls=None):
    from llm import LLMResult

    return LLMResult(content=text, tool_calls=tool_calls)


# ── 1. Input cap ─────────────────────────────────────────────────────


class TestInputCap:
    @pytest.mark.asyncio
    async def test_oversize_input_rejected_with_clear_error(self, monkeypatch):
        import core.agent_loop as al

        monkeypatch.setattr(al, "MAX_USER_MESSAGE_CHARS", 50)
        ag = _make_agent()
        ag.llm_client = AsyncMock()
        result = await ag.run("x" * 51)
        assert "[ERR] Input too large" in result
        assert "50 char limit" in result
        ag.llm_client.chat_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_input_at_cap_passes_through(self, monkeypatch):
        import core.agent_loop as al

        monkeypatch.setattr(al, "MAX_USER_MESSAGE_CHARS", 50)
        ag = _make_agent()
        ag.llm_client = AsyncMock()
        ag.llm_client.chat_stream = AsyncMock(return_value=_llm_result(text="ok"))
        result = await ag.run("x" * 50)
        assert result == "ok"

    def test_default_cap_is_one_million(self):
        import core.agent_loop as al

        assert al.MAX_USER_MESSAGE_CHARS == 1_000_000

    def test_body_rejects_huge_content_length(self):
        from web_server import _MAX_BODY_BYTES, HQHandler, _BodyTooLargeError

        h = HQHandler.__new__(HQHandler)
        h.headers = {"Content-Length": str(_MAX_BODY_BYTES + 1)}
        h.rfile = io.BytesIO(b"{}")
        with pytest.raises(_BodyTooLargeError):
            h._body()

    def test_body_accepts_small_json(self):
        from web_server import HQHandler

        h = HQHandler.__new__(HQHandler)
        h.headers = {"Content-Length": "14"}
        h.rfile = io.BytesIO(b'{"task": "hi"}')
        assert h._body() == {"task": "hi"}


# ── 2. PTY -> WS chunking ────────────────────────────────────────────


class FakePtyOut:
    """Returns one big blob, then goes quiet (isalive False ends pump_out)."""

    def __init__(self, blob):
        self._blob = blob

    def isalive(self):
        return self._blob is not None

    def read(self, blocking=False):
        if self._blob is None:
            return ""
        blob, self._blob = self._blob, None
        return blob

    def cancel_io(self):
        pass


class FakeWsOut:
    """Holds the input loop open until the full blob has been ws.send() chunk by chunk."""

    def __init__(self, expected_len):
        self.sent: list = []
        self.expected_len = expected_len
        self._done = threading.Event()

    def __iter__(self):
        assert self._done.wait(timeout=10), "pump_out never delivered the blob"
        return iter([])

    def send(self, data):
        self.sent.append(data)
        if sum(len(c) for c in self.sent) >= self.expected_len:
            self._done.set()

    def close(self, *a, **k):
        pass


class TestPumpOutChunking:
    def test_large_output_is_chunked(self, monkeypatch):
        from browser.browser_core import terminal_server
        from browser.browser_core.terminal_server import TerminalServer

        srv = TerminalServer(token="test-token")
        monkeypatch.setattr(srv, "_authorized", lambda ws: True)
        monkeypatch.setattr(terminal_server, "_client_options", lambda ws: (80, 24, "cmd"))
        blob = "z" * 100_000
        monkeypatch.setattr(terminal_server, "_spawn_pty", lambda *a, **k: FakePtyOut(blob))
        ws = FakeWsOut(len(blob))
        t = threading.Thread(target=srv._handle, args=(ws,), daemon=True)
        t.start()
        t.join(timeout=15)
        assert not t.is_alive(), "_handle did not finish"
        assert len(ws.sent) > 1, "expected chunked ws.send() calls"
        for chunk in ws.sent:
            assert len(chunk) <= 8192, f"chunk of {len(chunk)} exceeds 8KB limit"
        assert "".join(ws.sent) == blob, "PTY output did not arrive intact"


# ── 3. Recorded rotation + deterministic pin ─────────────────────────


class TestRotationRecorded:
    @pytest.mark.asyncio
    async def test_pin_provider_disables_rotation(self, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_PIN_PROVIDER", "1")
        ag = _make_agent()
        ag.llm_client = AsyncMock()
        result = await ag._auto_rotate_model({"content": "[API Error: 429]"}, [], [])
        assert result is None
        ag.llm_client.chat_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_rotation_emits_event(self):
        from core.types import AgentEventType

        ag = _make_agent()
        events = []
        ag.callbacks.on_event = lambda e: events.append(e)
        ag.llm_client = AsyncMock()
        ag.llm_client.chat_stream = AsyncMock(return_value=_llm_result(text="recovered"))
        msg = await ag._try_candidate_model("alt-model", [], [])
        assert msg is not None
        rotated = [e for e in events if e.type == AgentEventType.MODEL_ROTATED]
        assert rotated, "expected a MODEL_ROTATED event on the session stream"
        payload = rotated[0].payload
        assert payload["to_model"] == "alt-model"
        assert payload["reason"] == "auto-rotate"
        assert payload["from_model"] == "test-model"

    def test_explicit_switch_is_recorded(self):
        from core.types import AgentEventType
        from llm import LLMConfig

        ag = _make_agent()
        events = []
        ag.callbacks.on_event = lambda e: events.append(e)
        cfg = LLMConfig(
            api_key="x",
            base_url="http://localhost:1",
            model="other-model",
            provider="openai",
        )
        ag.switch_provider(cfg)
        rotated = [e for e in events if e.type == AgentEventType.MODEL_ROTATED]
        assert rotated and rotated[0].payload["reason"] == "explicit"
        assert rotated[0].payload["to_model"] == "other-model"


# ── 4. Durable task registry ─────────────────────────────────────────


class TestDurableTasks:
    def test_stale_running_tasks_fail_on_startup(self, tmp_path, monkeypatch):
        import web_server

        monkeypatch.setattr(web_server, "_TASKS_FILE", tmp_path / "hq_tasks.json")
        with web_server._TASKS_LOCK:
            web_server._TASKS.clear()
            web_server._TASKS["t1"] = {"id": "t1", "status": "running", "task": "x"}
            web_server._TASKS["t2"] = {"id": "t2", "status": "queued", "task": "y"}
            web_server._TASKS["t3"] = {
                "id": "t3",
                "status": "done",
                "task": "z",
                "result": "ok",
            }
        try:
            web_server._save_tasks()
            with web_server._TASKS_LOCK:
                web_server._TASKS.clear()
            web_server._load_tasks()
            with web_server._TASKS_LOCK:
                tasks = dict(web_server._TASKS)
        finally:
            with web_server._TASKS_LOCK:
                web_server._TASKS.clear()
        assert tasks["t1"]["status"] == "failed"
        assert "restarted" in tasks["t1"]["error"]
        assert tasks["t2"]["status"] == "failed"
        assert tasks["t3"]["status"] == "done"
        assert tasks["t3"]["result"] == "ok"
        # The failed marks are themselves persisted.
        saved = json.loads((tmp_path / "hq_tasks.json").read_text(encoding="utf-8"))
        assert saved["t1"]["status"] == "failed"

    def test_load_tasks_tolerates_missing_file(self, tmp_path, monkeypatch):
        import web_server

        monkeypatch.setattr(web_server, "_TASKS_FILE", tmp_path / "nope.json")
        web_server._load_tasks()  # must not raise


# ── 5. Retry policy ──────────────────────────────────────────────────


class TestRetryPolicy:
    def test_retryable_codes_cover_full_5xx(self):
        from core.llm_client import LLMClient

        client = LLMClient(api_key="x", base_url="http://localhost:1", model="m")
        assert 429 in client.retryable_codes
        assert client.retryable_codes >= set(range(500, 600))
        assert 400 not in client.retryable_codes
        assert 401 not in client.retryable_codes
        assert 403 not in client.retryable_codes

    def test_is_retryable_matches_http_post_policy(self):
        import httpx

        from core.agent_loop import _is_retryable

        def _err(code):
            req = httpx.Request("POST", "http://x")
            resp = httpx.Response(code, request=req)
            return httpx.HTTPStatusError("e", request=req, response=resp)

        for code in (429, 500, 501, 502, 503, 599):
            assert _is_retryable(_err(code)) is True, code
        for code in (400, 401, 403, 404):
            assert _is_retryable(_err(code)) is False, code
        assert _is_retryable(httpx.WriteTimeout("t")) is True
        assert _is_retryable(httpx.PoolTimeout("t")) is True


# ── 6. Summarize on truncation ───────────────────────────────────────


class TestTruncationSummary:
    def test_structural_summary_counts_work(self):
        from core.agent_loop import CodingAgent

        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "do things"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _tool_call("Write", "c1", '{"file_path": "/tmp/a.txt"}'),
                    _tool_call("Write", "c2", '{"file_path": "/tmp/b.txt"}'),
                ],
            },
            {"role": "tool", "content": "ok", "tool_call_id": "c1"},
            {"role": "tool", "content": "Error: boom", "tool_call_id": "c2"},
            {"role": "user", "content": "again"},
        ]
        summary = CodingAgent._structural_summary(messages)
        assert "2 user turns" in summary
        assert "Writex2" in summary
        assert "/tmp/a.txt" in summary
        assert "Error: boom" in summary

    @pytest.mark.asyncio
    async def test_fallback_preserves_middle_when_compaction_fails(self, tmp_path):
        """20+ turns with a dead summarizer: the middle must be summarized
        structurally, never deleted silently."""
        from core.types import AgentEventType

        ag = _make_agent()
        events = []
        ag.callbacks.on_event = lambda e: events.append(e)

        async def _boom(messages, **kwargs):
            raise RuntimeError("summarizer down")

        script = [_llm_result(tool_calls=[_tool_call("TodoRead", f"c{i}")]) for i in range(20)]
        script.append(_llm_result(text="done"))
        ag.llm_client = AsyncMock()
        ag.llm_client.chat_stream = AsyncMock(side_effect=script)
        ag.llm_client.chat_nonstreaming = _boom

        result = await ag.run("track todos for a while", max_turns=25)
        assert result == "done"
        summaries = [
            m
            for m in ag.messages
            if isinstance(m.get("content"), str) and "Structural summary" in m["content"]
        ]
        assert summaries, "expected a structural summary of the dropped middle"
        assert "TodoRead" in summaries[0]["content"]
        truncated = [e for e in events if e.type == AgentEventType.CONTEXT_TRUNCATED]
        assert truncated and truncated[0].payload["compacted"] is False


# ── 7. Delegation quarantine ─────────────────────────────────────────


class TestQuarantine:
    def test_small_result_passes_through(self):
        from tools.delegate import quarantine_result

        assert quarantine_result("short", "t") == "short"

    def test_large_result_quarantined_with_pointer(self, tmp_path, monkeypatch):
        import config
        from tools.delegate import QUARANTINE_CHARS, quarantine_result

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        big = "Q" * (QUARANTINE_CHARS + 100)
        note = quarantine_result(big, "mytask")
        assert "quarantined" in note
        assert "mytask" in note
        assert "Q" * 100 in note  # head excerpt present
        assert len(note) < len(big)
        files = list((tmp_path / "delegate-results").glob("mytask-*.md"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == big

    @pytest.mark.asyncio
    async def test_subagent_tool_quarantines_big_result(self, tmp_path, monkeypatch):
        import sys
        import types

        import config
        from tools.delegate import QUARANTINE_CHARS
        from tools.subagent_tool import SubAgentTool

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)

        class FakeAgent:
            turn_count = 3

            async def run(self, task, max_turns=None):
                return "R" * (QUARANTINE_CHARS + 10)

        # NOTE: do not `import agent` here — inside pytest that name resolves
        # to browser/browser_core/agent.py (Qt), not the repo-root shim.
        fake_agent_mod = types.ModuleType("agent")
        fake_agent_mod.CodingAgent = lambda **kw: FakeAgent()
        monkeypatch.setitem(sys.modules, "agent", fake_agent_mod)
        out = await SubAgentTool().execute(task="do a big thing", max_turns=5)
        assert "quarantined" in out.text
        assert len(out.text) < QUARANTINE_CHARS + 10
        assert (tmp_path / "delegate-results").exists()
