"""Tests for LuckyD 6.0 trust foundation — scopes, audit log, policy, approvals."""

from __future__ import annotations

import threading

import pytest

import core.trust as trust
from core.approval_hook import ApprovalHook
from core.audit_hook import AuditHook
from core.hooks import HookContext
from core.types import ToolPermissionLevel


@pytest.fixture()
def isolated_trust(tmp_path, monkeypatch):
    """Point trust storage at a temp dir and reset singletons."""
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    trust._audit_log = None
    trust._policy = None
    yield tmp_path
    trust._audit_log = None
    trust._policy = None


def _ctx() -> HookContext:
    return HookContext(turn=1, messages=[], config={})


# ── scopes ────────────────────────────────────────────────────────────────


class TestScopes:
    def test_known_tools(self):
        assert trust.scope_of("BrowserNavigate") == "browser"
        assert trust.scope_of("Bash") == "shell"
        assert trust.scope_of("Write") == "files"
        assert trust.scope_of("Read") == "read"
        assert trust.scope_of("GitPush") == "git"
        assert trust.scope_of("MemoryRecall") == "memory"
        assert trust.scope_of("DesktopMouse") == "desktop"
        assert trust.scope_of("WebSearch") == "network"
        assert trust.scope_of("SubAgent") == "agents"

    def test_prefix_fallback(self):
        assert trust.scope_of("BrowserSomethingNew") == "browser"
        assert trust.scope_of("TotallyUnknownTool") == "system"

    def test_all_scopes_have_metadata(self):
        for sid, meta in trust.SCOPES.items():
            assert meta["title"] and meta["desc"], sid

    def test_risk_levels(self):
        assert trust.risk_of("Read") == "low"  # ALWAYS_ALLOW
        assert trust.risk_of("Bash") == "high"  # REQUIRES_APPROVAL
        assert trust.risk_of("Edit") == "medium"  # NORMAL


# ── redaction ─────────────────────────────────────────────────────────────


class TestRedaction:
    def test_secret_keys_masked(self):
        out = trust.redact_args({"api_key": "sk-live-123", "url": "https://x.com"})
        assert out["api_key"] == "***"
        assert out["url"] == "https://x.com"

    def test_nested_redaction(self):
        out = trust.redact_args({"config": {"password": "hunter2"}})
        assert out["config"]["password"] == "***"

    def test_long_values_truncated(self):
        out = trust.redact_args({"text": "x" * 600})
        assert len(out["text"]) < 600 and "chars" in out["text"]

    def test_private_keys_dropped(self):
        out = trust.redact_args({"_id": "abc", "url": "https://x.com"})
        assert "_id" not in out


# ── audit log ─────────────────────────────────────────────────────────────


class TestAuditLog:
    def test_record_and_recent(self, isolated_trust):
        log = trust.get_audit_log()
        log.record("Bash", {"command": "ls"}, session_id="s1", decision="approved")
        log.record("Read", {"path": "a.txt"}, session_id="s1", decision="executed")
        events = log.recent(limit=10)
        assert len(events) == 2
        assert events[0]["tool"] == "Read"  # newest first
        assert events[0]["scope"] == "read"
        assert events[0]["risk"] == "low"

    def test_filters(self, isolated_trust):
        log = trust.get_audit_log()
        log.record("Bash", {}, session_id="s1", decision="denied")
        log.record("Bash", {}, session_id="s2", decision="approved")
        assert len(log.recent(decision="denied")) == 1
        assert len(log.recent(session_id="s2")) == 1

    def test_stats(self, isolated_trust):
        log = trust.get_audit_log()
        log.record("Bash", {}, decision="denied")
        log.record("Bash", {}, decision="approved")
        stats = log.stats()
        assert stats["total"] == 2
        assert stats["denied"] == 1

    def test_persists_jsonl(self, isolated_trust):
        log = trust.get_audit_log()
        log.record("Write", {"path": "f.txt"})
        assert log.path.exists()
        assert log.path.read_text(encoding="utf-8").strip().startswith("{")


# ── policy ────────────────────────────────────────────────────────────────


class TestPolicy:
    def test_defaults(self, isolated_trust):
        p = trust.get_policy()
        assert p.mode == "ask"
        assert p.scope_policy("read") == "allow"
        assert p.scope_policy("browser") == "ask"
        assert p.scope_policy("shell") == "ask"

    def test_set_scope_persists(self, isolated_trust):
        p = trust.get_policy()
        p.set_scope_policy("shell", "deny")
        trust._policy = None  # force reload from disk
        assert trust.get_policy().scope_policy("shell") == "deny"

    def test_invalid_rejected(self, isolated_trust):
        p = trust.get_policy()
        with pytest.raises(ValueError):
            p.set_scope_policy("nope", "allow")
        with pytest.raises(ValueError):
            p.set_scope_policy("shell", "maybe")
        with pytest.raises(ValueError):
            p.set_mode("chaos")

    def test_site_rules(self, isolated_trust):
        p = trust.get_policy()
        assert p.site_policy("Example.COM") is None
        p.set_site_policy("Example.COM", "allow")
        assert p.site_policy("example.com") == "allow"
        p.clear_site_policy("example.com")
        assert p.site_policy("example.com") is None


# ── approval hook integration ─────────────────────────────────────────────


class TestApprovalHook:
    def _hook(self, **kw) -> ApprovalHook:
        kw.setdefault("timeout_ms", 2000)
        return ApprovalHook(session_id="test", **kw)

    def test_allow_scope_skips_approval(self, isolated_trust):
        trust.get_policy().set_scope_policy("read", "allow")
        hook = self._hook()
        assert hook.before_tool("Read", {"_id": "1", "path": "x"}, _ctx()) is None

    def test_deny_scope_blocks(self, isolated_trust):
        trust.get_policy().set_scope_policy("shell", "deny")
        hook = self._hook()
        result = hook.before_tool("Bash", {"_id": "1", "command": "rm -rf /"}, _ctx())
        assert result is not None and "denied" in result["content"]

    def test_blocked_tool(self, isolated_trust):
        hook = self._hook()
        hook.set_permission("EvilTool", ToolPermissionLevel.BLOCKED)
        result = hook.before_tool("EvilTool", {"_id": "1"}, _ctx())
        assert result is not None and "blocked" in result["content"]

    def test_auto_mode(self, isolated_trust):
        trust.get_policy().set_mode("auto")
        hook = self._hook()
        assert hook.before_tool("Bash", {"_id": "1", "command": "ls"}, _ctx()) is None

    def test_step_through_asks_even_readonly(self, isolated_trust):
        trust.get_policy().set_mode("step-through")
        hook = self._hook()
        assert hook.pending_requests() == []
        result = hook.before_tool("Read", {"_id": "1", "path": "x"}, _ctx())
        # parked in the pending queue, then timed out (2s) -> denied
        assert result is not None and "timed out" in result["content"]

    def test_queued_approval_approve(self, isolated_trust):
        hook = self._hook()
        outcome: dict = {}

        def run():
            outcome["result"] = hook.before_tool(
                "Bash", {"_id": "call-1", "command": "echo hi"}, _ctx()
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # wait until the request is parked
        for _ in range(100):
            if hook.pending_requests():
                break
            __import__("time").sleep(0.05)
        pending = hook.pending_requests()
        assert len(pending) == 1
        assert pending[0]["tool"] == "Bash"
        assert pending[0]["risk"] == "high"
        assert "echo hi" in pending[0]["summary"]
        assert hook.resolve_approval("call-1", True, remember="session")
        t.join(timeout=5)
        assert outcome["result"] is None  # approved -> no interception
        # session memory: next call auto-approved
        assert hook.before_tool("Bash", {"_id": "call-2", "command": "ls"}, _ctx()) is None

    def test_queued_approval_deny(self, isolated_trust):
        hook = self._hook()
        outcome: dict = {}

        def run():
            outcome["result"] = hook.before_tool(
                "Write", {"_id": "call-9", "path": "x.txt"}, _ctx()
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        for _ in range(100):
            if hook.pending_requests():
                break
            __import__("time").sleep(0.05)
        assert hook.resolve_approval("call-9", False, reason="nope")
        t.join(timeout=5)
        assert "denied" in outcome["result"]["content"]

    def test_resolve_unknown(self, isolated_trust):
        hook = self._hook()
        assert hook.resolve_approval("missing", True) is False

    def test_remember_always_sets_scope_policy(self, isolated_trust):
        hook = self._hook()
        outcome: dict = {}

        def run():
            outcome["result"] = hook.before_tool(
                "BrowserNavigate", {"_id": "c1", "url": "https://example.com"}, _ctx()
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        for _ in range(100):
            if hook.pending_requests():
                break
            __import__("time").sleep(0.05)
        hook.resolve_approval("c1", True, remember="site")
        t.join(timeout=5)
        assert outcome["result"] is None
        assert trust.get_policy().site_policy("example.com") == "allow"
        # next navigation to the same site is auto-allowed
        assert (
            hook.before_tool(
                "BrowserNavigate", {"_id": "c2", "url": "https://example.com/a"}, _ctx()
            )
            is None
        )

    def test_approval_callback_still_works(self, isolated_trust):
        from core.types import ToolApprovalResult

        hook = ApprovalHook(
            session_id="test",
            approval_callback=lambda req: ToolApprovalResult(approved=True),
        )
        assert hook.before_tool("Bash", {"_id": "1", "command": "ls"}, _ctx()) is None

    def test_decisions_audited(self, isolated_trust):
        hook = self._hook()
        hook.before_tool("Bash", {"_id": "1", "command": "ls"}, _ctx())  # will time out -> denied
        events = trust.get_audit_log().recent(limit=5)
        assert any(e["tool"] == "Bash" and e["decision"] == "denied" for e in events)


# ── audit hook ────────────────────────────────────────────────────────────


class TestAuditHook:
    def test_execution_recorded(self, isolated_trust):
        hook = AuditHook(session_id="s9")
        args = {"_id": "x1", "path": "a.txt"}
        assert hook.before_tool("Read", args, _ctx()) is None

        class R:
            text = "file contents here"
            error = False

        hook.after_tool("Read", args, R(), _ctx())
        events = trust.get_audit_log().recent(limit=5)
        assert len(events) == 1
        e = events[0]
        assert e["tool"] == "Read" and e["decision"] == "executed"
        assert e["session_id"] == "s9" and e["ok"] is True
        assert "_id" not in e["args"]

    def test_audit_never_breaks_agent(self, isolated_trust, monkeypatch):
        hook = AuditHook()
        monkeypatch.setattr(
            trust, "get_audit_log", lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
        )
        # must not raise
        hook.after_tool("Read", {"_id": "x"}, None, _ctx())
