"""Night-3 tests: core/approval_hook.py + core/unattended.py.

Covers the trust-policy decision matrix (mode / session / scope / site /
low-risk auto-approve), the interactive request paths (callback, queued with
timeout + UI resolution, remember choices), and the unattended scheduled-run
boundary. All trust state is isolated to a tmp data dir.
"""

from __future__ import annotations

import threading
import time

import pytest

import core.trust as trust_mod
from core.approval_hook import ApprovalHook
from core.types import HookContext, ToolApprovalResult, ToolPermissionLevel
from core.unattended import UnattendedApprovalHook


@pytest.fixture()
def trust_tmp(monkeypatch, tmp_path):
    """Isolate TrustPolicy + audit log to a throwaway data dir."""
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(trust_mod, "_policy", None)
    monkeypatch.setattr(trust_mod, "_audit_log", None)
    yield tmp_path
    monkeypatch.setattr(trust_mod, "_policy", None)
    monkeypatch.setattr(trust_mod, "_audit_log", None)


@pytest.fixture()
def ctx():
    return HookContext(turn=0, messages=[], config=None)


@pytest.fixture()
def hook(trust_tmp):
    return ApprovalHook(session_id="night3")


# ── decision matrix ────────────────────────────────────────────────────


def test_read_always_allowed_in_ask_mode(hook, ctx):
    # Default trust policy auto-allows the "read" scope; force the policy to
    # ask so we exercise the ALWAYS_ALLOW branch instead of the scope branch.
    hook.policy.set_scope_policy("read", "ask")
    decision, reason = hook.evaluate_policy("Read", {"path": "a.py"}, ctx)
    assert decision == "approved"
    assert reason == "read-only tool (always allowed)"


def test_bash_needs_approval_by_default(hook, ctx):
    decision, _ = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "needs_approval"


def test_auto_mode_approves_everything(hook, ctx):
    hook.policy.set_mode("auto")
    decision, reason = hook.evaluate_policy("Bash", {"command": "rm -rf /"}, ctx)
    assert decision == "approved"
    assert "auto" in reason


def test_step_through_asks_even_for_read(hook, ctx):
    hook.policy.set_mode("step-through")
    decision, _ = hook.evaluate_policy("Read", {"path": "a.py"}, ctx)
    assert decision == "needs_approval"


def test_invalid_mode_rejected(hook):
    with pytest.raises(ValueError, match="invalid mode"):
        hook.policy.set_mode("yolo")


def test_blocked_level_denied(hook, ctx):
    hook.set_permission("Bash", ToolPermissionLevel.BLOCKED)
    try:
        decision, reason = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
        assert decision == "denied"
        assert "blocked" in reason
        blocked = hook.before_tool("Bash", {"command": "ls", "_id": "c1"}, ctx)
        assert blocked["role"] == "tool"
        assert "Error" in blocked["content"]
    finally:
        hook.set_permission("Bash", ToolPermissionLevel.REQUIRES_APPROVAL)


def test_auto_approve_low_risk_mode(hook, ctx):
    hook.auto_approve_low_risk = True
    decision, reason = hook.evaluate_policy("Read", {"path": "a.py"}, ctx)
    assert decision == "approved"
    assert reason == "auto-approve low-risk run mode"
    # High-risk tools still go through the policy.
    decision, _ = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "needs_approval"


def test_session_remember_approves_later_calls(hook, ctx):
    hook._apply_remember("Bash", {"command": "ls"}, "session", approved=True)
    decision, reason = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "approved"
    assert "session" in reason


def test_session_remember_not_applied_when_denied(hook, ctx):
    hook._apply_remember("Bash", {"command": "ls"}, "session", approved=False)
    decision, _ = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "needs_approval"


def test_always_remember_sets_scope_policy(hook, ctx):
    hook._apply_remember("Bash", {"command": "ls"}, "always", approved=True)
    assert hook.policy.scope_policy("shell") == "allow"
    decision, _ = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "approved"


def test_scope_deny_wins(hook, ctx):
    hook.policy.set_scope_policy("shell", "deny")
    decision, reason = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "denied"
    assert "shell" in reason


def test_site_rule_allow_and_deny(hook, ctx):
    hook.policy.set_site_policy("example.com", "allow")
    decision, _ = hook.evaluate_policy("BrowserNavigate", {"url": "https://example.com/page"}, ctx)
    assert decision == "approved"
    hook.policy.set_site_policy("evil.test", "deny")
    decision, _ = hook.evaluate_policy("BrowserNavigate", {"url": "https://evil.test/x"}, ctx)
    assert decision == "denied"


def test_site_remember_records_site_policy(hook):
    hook._apply_remember("BrowserNavigate", {"url": "https://example.com/x"}, "site", approved=True)
    assert hook.policy.site_policy("example.com") == "allow"


def test_tool_policy_auto_approve_and_block(hook, ctx):
    ctx.config = {"tool_policies": {"Edit": {"auto_approve": True}}}
    decision, _ = hook.evaluate_policy("Edit", {"path": "a.py"}, ctx)
    assert decision == "approved"
    ctx.config = {"tool_policies": {"Edit": {"block": True}}}
    decision, _ = hook.evaluate_policy("Edit", {"path": "a.py"}, ctx)
    assert decision == "denied"


def test_tool_policy_wildcard(hook, ctx):
    ctx.config = {"tool_policies": {"*": {"auto_approve": True}}}
    decision, _ = hook.evaluate_policy("Edit", {"path": "a.py"}, ctx)
    assert decision == "approved"


def test_unknown_tool_defaults_to_normal(hook):
    assert hook.get_permission("DefinitelyNotATool") == ToolPermissionLevel.NORMAL


def test_auto_approve_all_flag_is_inert(hook):
    hook.auto_approve_all = True  # logs a warning, stays inert
    assert hook.auto_approve_all is False


# ── interactive request paths ──────────────────────────────────────────


def test_callback_approve_runs_tool(trust_tmp):
    hook = ApprovalHook(
        approval_callback=lambda req: ToolApprovalResult(approved=True), session_id="s"
    )
    ctx = HookContext(turn=0, messages=[], config=None)
    assert hook.before_tool("Bash", {"command": "ls", "_id": "c1"}, ctx) is None


def test_callback_deny_blocks_tool(trust_tmp):
    def cb(req):
        assert req.tool_name == "Bash"
        assert req.tool_args == {"command": "ls"}
        return ToolApprovalResult(approved=False, reason="nope")

    hook = ApprovalHook(approval_callback=cb, session_id="s")
    ctx = HookContext(turn=0, messages=[], config=None)
    res = hook.before_tool("Bash", {"command": "ls", "_id": "c1"}, ctx)
    assert res["tool_call_id"] == "c1"
    assert "denied by user" in res["content"]


def test_callback_none_result_blocks_tool(trust_tmp):
    hook = ApprovalHook(approval_callback=lambda req: None, session_id="s")
    ctx = HookContext(turn=0, messages=[], config=None)
    res = hook.before_tool("Bash", {"command": "ls", "_id": "c1"}, ctx)
    assert "denied" in res["content"].lower()


def test_callback_not_called_for_always_allow(trust_tmp):
    def cb(req):  # pragma: no cover
        raise AssertionError("should not ask")

    hook = ApprovalHook(approval_callback=cb, session_id="s")
    ctx = HookContext(turn=0, messages=[], config=None)
    assert hook.before_tool("Read", {"path": "a.py", "_id": "c1"}, ctx) is None


def test_queued_approval_timeout(trust_tmp):
    hook = ApprovalHook(session_id="q", timeout_ms=80)
    ctx = HookContext(turn=0, messages=[], config=None)
    res = hook.before_tool("Bash", {"command": "ls", "_id": "qt"}, ctx)
    assert res["tool_call_id"] == "qt"
    assert "timed out" in res["content"]
    # Pending entry is cleaned up even on timeout.
    assert hook.pending_requests() == []


def test_queued_approval_resolved_from_ui(trust_tmp):
    hook = ApprovalHook(session_id="q", timeout_ms=5000)
    ctx = HookContext(turn=0, messages=[], config=None)
    outcome = {}

    def _call():
        outcome["res"] = hook.before_tool("Bash", {"command": "ls", "_id": "qr"}, ctx)

    t = threading.Thread(target=_call)
    t.start()
    for _ in range(100):
        if hook.pending_requests():
            break
        time.sleep(0.02)
    pending = hook.pending_requests()
    assert len(pending) == 1
    assert pending[0]["tool"] == "Bash"
    assert pending[0]["risk"] == "high"
    assert hook.resolve_approval("qr", True, reason="ok", remember="session") is True
    t.join(timeout=10)
    assert outcome["res"] is None  # approved → tool proceeds
    assert hook.pending_requests() == []
    # remember=session recorded
    assert "Bash" in hook._session_allow


def test_queued_approval_denied_from_ui(trust_tmp):
    hook = ApprovalHook(session_id="q", timeout_ms=5000)
    ctx = HookContext(turn=0, messages=[], config=None)
    outcome = {}

    def _call():
        outcome["res"] = hook.before_tool("Bash", {"command": "ls", "_id": "qd"}, ctx)

    t = threading.Thread(target=_call)
    t.start()
    for _ in range(100):
        if hook.pending_requests():
            break
        time.sleep(0.02)
    assert hook.resolve_approval("qd", False, reason="unsafe") is True
    t.join(timeout=10)
    assert "denied by user" in outcome["res"]["content"]


def test_resolve_unknown_call_id(trust_tmp):
    hook = ApprovalHook(session_id="q", timeout_ms=50)
    assert hook.resolve_approval("missing", True) is False


def test_resolve_bad_remember_falls_back_to_once(trust_tmp):
    hook = ApprovalHook(session_id="q", timeout_ms=5000)
    ctx = HookContext(turn=0, messages=[], config=None)
    outcome = {}

    def _call():
        outcome["res"] = hook.before_tool("Bash", {"command": "ls", "_id": "qb"}, ctx)

    t = threading.Thread(target=_call)
    t.start()
    for _ in range(100):
        if hook.pending_requests():
            break
        time.sleep(0.02)
    assert hook.resolve_approval("qb", True, remember="bogus") is True
    t.join(timeout=10)
    assert outcome["res"] is None
    assert "Bash" not in hook._session_allow  # remember fell back to "once"


# ── audit trail ────────────────────────────────────────────────────────


def test_policy_decisions_are_audited(trust_tmp):
    """Terminal decisions are audit-logged; the intermediate needs_approval
    evaluation defers its audit line to the request path (the eventual
    approve/deny/timeout decision is what gets recorded)."""
    hook = ApprovalHook(
        approval_callback=lambda req: ToolApprovalResult(approved=True), session_id="s"
    )
    ctx = HookContext(turn=0, messages=[], config=None)
    hook.policy.set_scope_policy("read", "ask")
    decision, _ = hook.evaluate_policy("Read", {"path": "a.py"}, ctx)
    assert decision == "approved"
    decision, _ = hook.evaluate_policy("Bash", {"command": "ls"}, ctx)
    assert decision == "needs_approval"
    log_path = trust_tmp / "audit" / "audit.jsonl"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool": "Read"' in line and '"decision": "auto"' in line for line in lines)
    # The needs_approval evaluation itself leaves no line; the request does.
    assert not any('"tool": "Bash"' in line for line in lines)
    assert hook.before_tool("Bash", {"command": "ls", "_id": "c1"}, ctx) is None
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert any('"tool": "Bash"' in line and '"decision": "approved"' in line for line in lines)


# ── unattended boundary ────────────────────────────────────────────────


def test_unattended_allows_granted_scopes(trust_tmp):
    hook = UnattendedApprovalHook(["read", "network"], session_id="u")
    ctx = HookContext(turn=0, messages=[], config=None)
    assert hook.before_tool("Read", {"path": "a.py", "_id": "u1"}, ctx) is None
    assert hook.before_tool("WebSearch", {"query": "x", "_id": "u2"}, ctx) is None


def test_unattended_denies_shell_desktop_system(trust_tmp):
    hook = UnattendedApprovalHook(["read", "network", "shell"], session_id="u")
    ctx = HookContext(turn=0, messages=[], config=None)
    for tool in ("Bash", "PowerShell", "DesktopMouse", "Config", "SQLite"):
        res = hook.before_tool(tool, {"_id": "u"}, ctx)
        assert res is not None and "unattended" in res["content"].lower(), tool


def test_unattended_denies_unlisted_scopes(trust_tmp):
    hook = UnattendedApprovalHook(["read", "network"], session_id="u")
    ctx = HookContext(turn=0, messages=[], config=None)
    res = hook.before_tool("SendMessage", {"to": "x", "_id": "u3"}, ctx)
    assert res is not None
    assert "not in schedule's allowed scopes" in res["content"]
    # Never parks in the interactive pending queue.
    assert hook.pending_requests() == []


def test_unattended_denies_blocked_tool(trust_tmp):
    hook = UnattendedApprovalHook(["read", "network"], session_id="u")
    hook.set_permission("Read", ToolPermissionLevel.BLOCKED)
    try:
        ctx = HookContext(turn=0, messages=[], config=None)
        res = hook.before_tool("Read", {"path": "a.py", "_id": "u4"}, ctx)
        assert res is not None and "blocked" in res["content"].lower()
    finally:
        hook.set_permission("Read", ToolPermissionLevel.ALWAYS_ALLOW)


def test_unattended_audits_decisions(trust_tmp):
    hook = UnattendedApprovalHook(["read"], session_id="u-audit")
    ctx = HookContext(turn=0, messages=[], config=None)
    hook.before_tool("Read", {"path": "a.py", "_id": "u5"}, ctx)
    hook.before_tool("Bash", {"command": "ls", "_id": "u6"}, ctx)
    lines = (trust_tmp / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("unattended allow scope" in line for line in lines)
    assert any("unattended:" in line for line in lines)
