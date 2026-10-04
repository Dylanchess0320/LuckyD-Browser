"""Alias-bypass regression tests for the trust system.

The tool executor resolves aliases/case variants through the tool registry,
so every policy check (permission, scope, risk, audit) must resolve the
same way. Previously the hooks judged the RAW spelling the model typed,
letting all 42 approval-required tools run via an alias or case variant
with no approval. These tests pin the fix.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import tools
from core.approval_hook import ApprovalHook
from core.hooks import HookContext
from core.trust import canonical_tool_name, risk_of, scope_of
from core.types import ToolPermissionLevel
from tools.registry import canonical_name, registry


def _load_tools() -> None:
    for mod in pkgutil.iter_modules(tools.__path__):
        if mod.name in ("base", "registry"):
            continue
        try:
            importlib.import_module(f"tools.{mod.name}")
        except Exception:
            pass


_load_tools()


def _approval_required_canonical() -> list[str]:
    from core.approval_hook import _TOOL_PERMISSIONS

    names: dict[str, object] = {}
    for key in registry.list_tools():
        tool = registry.get(key)
        names[tool.name] = tool
    return sorted(
        n for n in names if _TOOL_PERMISSIONS.get(n) == ToolPermissionLevel.REQUIRES_APPROVAL
    )


def _ctx() -> HookContext:
    return HookContext(turn=1, messages=[], config={})


@pytest.fixture()
def isolated_policy(tmp_path, monkeypatch):
    """Point trust storage at a temp dir so default scope policies apply.

    Without this, the hook loads the developer's real on-disk policy
    (which may allow the shell scope), hiding the approval decision.
    """
    import core.trust as trust

    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    trust._audit_log = None
    trust._policy = None
    yield tmp_path
    trust._audit_log = None
    trust._policy = None


class TestCanonicalName:
    def test_alias_resolves(self):
        assert canonical_name("Shell") == "Bash"
        assert canonical_name("WebTask") == "BrowserUse"

    def test_case_variant_resolves(self):
        assert canonical_name("bash") == "Bash"
        assert canonical_name("BASH") == "Bash"
        assert canonical_name("wRiTe") == "Write"

    def test_canonical_is_stable(self):
        assert canonical_name("Bash") == "Bash"

    def test_unknown_unchanged(self):
        assert canonical_name("NoSuchTool") == "NoSuchTool"

    def test_trust_helper_matches(self):
        assert canonical_tool_name("Shell") == "Bash"
        assert canonical_tool_name("bash") == "Bash"


class TestPermissionBypassFixed:
    def test_alias_gets_canonical_permission(self):
        hook = ApprovalHook(approval_callback=None, session_id="t")
        assert hook.get_permission("Shell") == ToolPermissionLevel.REQUIRES_APPROVAL
        assert hook.get_permission("WebTask") == ToolPermissionLevel.REQUIRES_APPROVAL

    def test_case_variant_gets_canonical_permission(self):
        hook = ApprovalHook(approval_callback=None, session_id="t")
        assert hook.get_permission("bash") == ToolPermissionLevel.REQUIRES_APPROVAL
        assert hook.get_permission("BASH") == ToolPermissionLevel.REQUIRES_APPROVAL
        assert hook.get_permission("write") == ToolPermissionLevel.REQUIRES_APPROVAL

    def test_all_approval_aliases_require_approval(self):
        """Every alias of every REQUIRES_APPROVAL tool must also require approval."""
        from core.approval_hook import _TOOL_PERMISSIONS

        hook = ApprovalHook(approval_callback=None, session_id="t")
        checked = 0
        for canon in _approval_required_canonical():
            tool = registry.get(canon)
            for alias in tool.aliases or []:
                assert hook.get_permission(alias) == ToolPermissionLevel.REQUIRES_APPROVAL, (
                    f"alias {alias!r} of {canon} bypasses approval"
                )
                checked += 1
        assert checked > 0, "expected approval-required tools to have aliases"


class TestScopeRiskBypassFixed:
    def test_alias_scope_matches_canonical(self):
        assert scope_of("Shell") == scope_of("Bash") == "shell"
        assert scope_of("webtask") == scope_of("BrowserUse") == "browser"

    def test_alias_risk_matches_canonical(self):
        assert risk_of("Shell") == risk_of("Bash") == "high"
        assert risk_of("bash") == "high"

    def test_all_alias_scopes_match_canonical(self):
        # An alias string may be claimed by more than one tool; the registry
        # (and the executor) resolve it to exactly one. Policy must agree
        # with that resolution.
        checked = 0
        seen: set[str] = set()
        for key in registry.list_tools():
            tool = registry.get(key)
            for alias in tool.aliases or []:
                if alias.lower() in seen:
                    continue
                seen.add(alias.lower())
                resolved = canonical_name(alias)
                assert scope_of(alias) == scope_of(resolved), (
                    f"alias {alias!r} scope {scope_of(alias)} != "
                    f"resolved {resolved} scope {scope_of(resolved)}"
                )
                checked += 1
        assert checked > 0


class TestEvaluatePolicyBypassFixed:
    def test_alias_needs_approval(self, isolated_policy):
        hook = ApprovalHook(approval_callback=None, session_id="t")
        decision, _ = hook.evaluate_policy("Shell", {"command": "x"}, _ctx())
        assert decision == "needs_approval", f"alias 'Shell' got {decision}"

    def test_case_variant_needs_approval(self, isolated_policy):
        hook = ApprovalHook(approval_callback=None, session_id="t")
        decision, _ = hook.evaluate_policy("bash", {"command": "x"}, _ctx())
        assert decision == "needs_approval", f"'bash' got {decision}"

    def test_alias_and_canonical_get_same_decision(self, isolated_policy):
        # Core regression property: spelling must not change the verdict.
        hook = ApprovalHook(approval_callback=None, session_id="t")
        for spelling in ["Shell", "Cmd", "Run", "bash", "BASH"]:
            d_alias, _ = hook.evaluate_policy(spelling, {"command": "x"}, _ctx())
            d_canon, _ = hook.evaluate_policy("Bash", {"command": "x"}, _ctx())
            assert d_alias == d_canon == "needs_approval", (
                f"{spelling!r} -> {d_alias}, Bash -> {d_canon}"
            )

    def test_before_tool_resolves_for_approval_request(self, isolated_policy):
        # before_tool must judge the canonical tool so the approval request
        # names the real tool and can't be dodged via spelling.
        hook = ApprovalHook(approval_callback=None, session_id="t")
        calls = []
        hook._request_approval = lambda name, args: calls.append(name) or {"intercepted": True}  # type: ignore
        hook.before_tool("Shell", {"command": "x"}, _ctx())
        assert calls == ["Bash"], f"approval requested for {calls}"


class TestUnattendedBypassFixed:
    def test_network_only_schedule_cannot_run_webtask_alias(self):
        from core.unattended import UnattendedApprovalHook

        hook = UnattendedApprovalHook(allow_scopes=["network"], session_id="t")
        # WebTask is an alias of BrowserUse (scope 'browser'); the raw alias
        # used to resolve to scope 'network' and slip through.
        result = hook.before_tool("webtask", {"url": "http://x"}, _ctx())
        assert result is not None, "webtask alias slipped through a network-only schedule"
        assert "not permitted" in result["content"]

    def test_shell_alias_denied_by_hard_deny(self):
        from core.unattended import UnattendedApprovalHook
        from core.scheduler import HARD_DENY_SCOPES

        assert "shell" in HARD_DENY_SCOPES
        hook = UnattendedApprovalHook(allow_scopes=["network", "system"], session_id="t")
        result = hook.before_tool("Shell", {"command": "x"}, _ctx())
        assert result is not None, "'Shell' alias bypassed HARD_DENY_SCOPES"
