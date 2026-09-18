"""Tests for the Build with LuckyD kit (stdlib-only, no LuckyD imports)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lucky_kit import (
    ApprovalEngine,
    AuditLog,
    CircuitBreaker,
    TrustPolicy,
    describe_decision,
    redact_args,
    risk_of,
    scope_of,
)


@pytest.fixture
def tmp_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKY_KIT_DATA_DIR", str(tmp_path))
    import lucky_kit.trust as trust_mod

    trust_mod._audit_log = None
    trust_mod._policy = None
    yield tmp_path
    trust_mod._audit_log = None
    trust_mod._policy = None


def test_scope_of_known_tools():
    assert scope_of("Bash") == "shell"
    assert scope_of("Read") == "read"
    assert scope_of("BrowserNavigate") == "browser"
    assert scope_of("GitPush") == "git"
    assert scope_of("WebSearch") == "network"
    assert scope_of("SubAgent") == "agents"


def test_scope_of_prefix_fallback():
    assert scope_of("BrowserAnythingNew") == "browser"
    assert scope_of("TotallyUnknownTool") == "system"


def test_risk_levels():
    assert risk_of("Bash") == "high"
    assert risk_of("DesktopMouse") == "high"
    assert risk_of("Write") == "medium"
    assert risk_of("BrowserClick") == "medium"
    assert risk_of("Read") == "low"
    assert risk_of("WebSearch") == "low"


def test_redact_args_masks_secrets():
    out = redact_args({"api_key": "sk-live-123", "path": "/tmp/x", "nested": {"token": "abc"}})
    assert out["api_key"] == "***"
    assert out["path"] == "/tmp/x"
    assert out["nested"]["token"] == "***"


def test_redact_args_truncates_long_values():
    out = redact_args({"blob": "x" * 600})
    assert len(out["blob"]) < 600 and "chars" in out["blob"]


def test_audit_log_roundtrip(tmp_policy):
    audit = AuditLog()
    entry = audit.record(
        "Bash",
        {"command": "ls", "api_key": "secret"},
        decision="denied",
        ok=False,
        summary="blocked by policy",
    )
    assert entry["scope"] == "shell"
    assert entry["risk"] == "high"
    assert entry["args"]["api_key"] == "***"
    assert entry["decision"] == "denied"
    recent = audit.recent(limit=5)
    assert recent and recent[0]["tool"] == "Bash"
    assert audit.stats()["total"] == 1


def test_trust_policy_defaults_and_persistence(tmp_policy):
    p = TrustPolicy()
    assert p.mode == "ask"
    assert p.scope_policy("read") == "allow"
    assert p.scope_policy("shell") == "ask"
    p.set_scope_policy("shell", "deny")
    p2 = TrustPolicy()
    assert p2.scope_policy("shell") == "deny"
    p2.set_mode("auto")
    assert TrustPolicy().mode == "auto"


def test_approval_engine_modes(tmp_policy):
    engine = ApprovalEngine()
    assert engine.decide("Read", {}).decision == "allow"
    assert engine.decide("Bash", {"command": "ls"}).decision == "ask"
    engine.policy.set_scope_policy("shell", "deny")
    assert engine.decide("Bash", {"command": "ls"}).decision == "deny"
    engine.policy.set_mode("auto")
    assert engine.decide("Bash", {"command": "rm -rf /"}).decision == "allow"
    engine.policy.set_mode("step-through")
    assert engine.decide("Read", {}).decision == "ask"


def test_approval_engine_run_fail_closed(tmp_policy):
    engine = ApprovalEngine()
    called = []
    result, _ = engine.run("Bash", {"command": "ls"}, execute=lambda: called.append(1))
    assert result.decision == "deny"  # ask with no approver -> deny
    assert called == []


def test_approval_engine_run_approved(tmp_policy):
    engine = ApprovalEngine()
    result, out = engine.run(
        "Bash", {"command": "ls"}, execute=lambda: "ok", ask_user=lambda desc: True
    )
    assert result.decision == "allow"
    assert out == "ok"


def test_site_policy(tmp_policy):
    engine = ApprovalEngine()
    engine.policy.set_site_policy("example.com", "allow")
    r = engine.decide(
        "BrowserNavigate",
        {"url": "https://example.com/a"},
    )
    assert r.decision == "allow"
    engine.policy.set_site_policy("evil.test", "deny")
    r = engine.decide("BrowserClick", {"selector": "#x"}, site_host="evil.test")
    assert r.decision == "deny"


def test_describe_decision():
    assert "rm -rf" in describe_decision("Bash", {"command": "rm -rf /tmp/x"})
    assert "example.com" in describe_decision("BrowserNavigate", {"url": "https://example.com"})


def test_circuit_breaker_trips():
    cb = CircuitBreaker(max_consecutive_failures=4)
    for _ in range(3):
        assert cb.record_turn(tools_called=2, tools_failed=2) is False
    assert cb.record_turn(tools_called=1, tools_failed=1) is True
    assert cb.tripped


def test_circuit_breaker_resets_on_success():
    cb = CircuitBreaker(max_consecutive_failures=4)
    for _ in range(3):
        cb.record_turn(tools_called=2, tools_failed=2)
    cb.record_turn(tools_called=2, tools_failed=0)
    assert cb.consecutive_failures == 0
    assert not cb.tripped


def test_audit_log_jsonl_is_valid_json(tmp_policy):
    audit = AuditLog()
    audit.record("Write", {"path": "/tmp/a.txt"})
    lines = Path(audit.path).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    json.loads(lines[0])
