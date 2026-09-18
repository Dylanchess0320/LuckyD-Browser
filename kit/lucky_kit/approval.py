"""Approval policy engine — decide allow / ask / deny for a tool call.

Build with LuckyD: plug this into your own agent loop. Before executing a
tool, call ``ApprovalEngine.decide``. If it returns ``ask``, present
``describe_decision(tool, args)`` to the user; record the outcome with
``AuditLog.record(decision=...)`` either way. Receipts, automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .trust import TrustPolicy, describe_decision, host_of_url, scope_of

Decision = Literal["allow", "ask", "deny"]

#: Signature of the function you provide to ask the user.
#: Receives the human-readable description, returns True to approve.
AskUserFn = Callable[[str], bool]


@dataclass
class ApprovalResult:
    decision: Decision
    reason: str
    description: str


class ApprovalEngine:
    """Turns a TrustPolicy into per-tool-call allow / ask / deny decisions."""

    def __init__(self, policy: TrustPolicy | None = None):
        self.policy = policy or TrustPolicy()

    def decide(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        site_host: str | None = None,
    ) -> ApprovalResult:
        args = args or {}
        scope = scope_of(tool)
        description = describe_decision(tool, args)

        # Global mode shortcuts.
        mode = self.policy.mode
        if mode == "auto":
            return ApprovalResult("allow", "global mode is auto", description)
        if mode == "step-through":
            return ApprovalResult("ask", "global mode is step-through", description)

        # Per-site rules win inside the browser scope.
        if scope == "browser":
            host = site_host or self._host_from_args(args)
            if host:
                site_policy = self.policy.site_policy(host)
                if site_policy == "allow":
                    return ApprovalResult("allow", f"site rule: always allow {host}", description)
                if site_policy == "deny":
                    return ApprovalResult("deny", f"site rule: deny {host}", description)

        scope_policy = self.policy.scope_policy(scope)
        if scope_policy == "allow":
            return ApprovalResult("allow", f"scope '{scope}' is allowed", description)
        if scope_policy == "deny":
            return ApprovalResult("deny", f"scope '{scope}' is denied", description)
        return ApprovalResult("ask", f"scope '{scope}' requires approval", description)

    def run(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        execute: Callable[[], Any] | None = None,
        ask_user: AskUserFn | None = None,
        *,
        site_host: str | None = None,
    ) -> tuple[ApprovalResult, Any]:
        """Decide, optionally ask the user, optionally execute.

        Returns (result, tool_output or None). If decision is ask and no
        ``ask_user`` is provided, the call is denied (fail closed).
        """
        result = self.decide(tool, args, site_host=site_host)
        output: Any = None
        if result.decision == "ask":
            approved = ask_user(result.description) if ask_user else False
            result = ApprovalResult(
                "allow" if approved else "deny",
                ("user approved: " if approved else "user denied: ") + result.description,
                result.description,
            )
        if result.decision == "allow" and execute is not None:
            output = execute()
        return result, output

    @staticmethod
    def _host_from_args(args: dict[str, Any]) -> str | None:
        for key in ("url", "site", "host"):
            value = args.get(key)
            if isinstance(value, str):
                host = host_of_url(value)
                if host:
                    return host
        return None
