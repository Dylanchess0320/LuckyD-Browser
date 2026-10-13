"""Tests for core modules."""

from core.context_manager import estimate_tokens, truncate_messages
from core.types import AgentEvent, AgentEventType, ToolPermissionLevel


class TestTokenEstimation:
    def test_estimate_tokens_empty(self):
        assert estimate_tokens("") == 0

    def test_estimate_tokens_basic(self):
        t = estimate_tokens("hello world")
        assert 1 <= t <= 5


class TestTruncateMessages:
    def test_under_limit(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        r = truncate_messages(msgs, max_messages=40)
        assert len(r) == 2

    def test_preserves_tool_pairs(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do"},
            {"role": "assistant", "content": "ok", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "c1"},
        ]
        r = truncate_messages(msgs, max_messages=3, keep_recent=2)
        assert len(r) >= 3


class TestTypes:
    def test_agent_event(self):
        e = AgentEvent(type=AgentEventType.TOOL_START, payload={"tool": "bash"})
        assert e.type == AgentEventType.TOOL_START

    def test_permission_levels(self):
        assert ToolPermissionLevel.ALWAYS_ALLOW.value == "always_allow"


class TestCheckpoint:
    def test_record_change(self):
        from core.checkpoint import CheckpointManager

        cm = CheckpointManager()
        cp = cm.record_change("/tmp/t.py", "old", "new")
        assert cp.checkpoint_id.startswith("cp_")
        assert cp.content_before == "old"

    def test_list_clear(self):
        from core.checkpoint import CheckpointManager

        cm = CheckpointManager()
        cm.record_change("/tmp/a.py", "a", "b")
        cm.record_change("/tmp/b.py", "c", "d")
        assert len(cm.list_checkpoints()) == 2


class TestApprovalHook:
    def test_permission_levels(self):
        from core.approval_hook import ApprovalHook

        hook = ApprovalHook()
        assert hook.get_permission("Read") == ToolPermissionLevel.ALWAYS_ALLOW
        assert hook.get_permission("Bash") == ToolPermissionLevel.REQUIRES_APPROVAL

    def test_blocked_tool(self):
        from core.approval_hook import ApprovalHook

        hook = ApprovalHook()
        hook.set_permission("Evil", ToolPermissionLevel.BLOCKED)


class TestProviders:
    def test_detect_api_format(self):
        from core.providers import detect_api_format

        assert detect_api_format("openai") == "openai"
        assert detect_api_format("deepseek") == "openai"
        # 2026-10-12: Meta Model API + Mistral La Plateforme are OpenAI-compatible.
        assert detect_api_format("meta") == "openai"
        assert detect_api_format("mistral") == "openai"
