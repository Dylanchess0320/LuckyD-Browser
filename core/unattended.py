"""
Unattended approval hook — the permission boundary for scheduled agents.

A scheduled agent runs while the user is asleep: nobody can approve or deny
in real time. So this hook NEVER prompts, NEVER parks in the pending queue,
and NEVER falls back to the interactive policy:

- Tool whose permission level is BLOCKED -> denied.
- Tool whose scope is in HARD_DENY_SCOPES (shell, desktop, system) -> denied.
- Tool whose scope is in the schedule's allow_scopes -> approved.
- Everything else -> denied, with a message telling the agent it may ask
  the user to widen the schedule's scopes.

Every decision is audit-logged under the run's session id, exactly like an
interactive run ("agentic with receipts" applies overnight too).
"""

from __future__ import annotations

from typing import Any

from core.approval_hook import ApprovalHook
from core.trust import scope_of
from core.types import HookContext, ToolPermissionLevel


class UnattendedApprovalHook(ApprovalHook):
    name = "unattended-approval"

    def __init__(
        self,
        allow_scopes: list[str] | set[str],
        session_id: str = "scheduled",
        schedule_name: str = "",
    ):
        # No callback, no approval dir, no waiting: unattended by construction.
        super().__init__(approval_callback=None, session_id=session_id, timeout_ms=0)
        self.auto_approve_all = False
        self.allow_scopes = set(allow_scopes)
        self.schedule_name = schedule_name

    def _deny(self, tool_name: str, tool_args: dict, reason: str) -> dict[str, Any]:
        clean = {k: v for k, v in tool_args.items() if not k.startswith("_")}
        self._audit_decision(tool_name, clean, "denied", f"unattended: {reason}")
        return {
            "role": "tool",
            "tool_call_id": tool_args.get("_id", "unknown"),
            "content": (
                f"Error: Tool '{tool_name}' is not permitted in unattended scheduled runs "
                f"({reason}). Ask the user to widen this schedule's allowed scopes if needed."
            ),
        }

    def before_tool(self, tool_name: str, tool_args: dict, ctx: HookContext) -> dict | None:
        from core.scheduler import HARD_DENY_SCOPES

        level = self.get_permission(tool_name)
        scope = scope_of(tool_name)
        clean = {k: v for k, v in tool_args.items() if not k.startswith("_")}

        if level == ToolPermissionLevel.BLOCKED:
            return self._deny(tool_name, tool_args, "tool is blocked")
        if scope in HARD_DENY_SCOPES:
            return self._deny(
                tool_name,
                tool_args,
                f"scope '{scope}' can never be granted to an unattended schedule",
            )
        if scope in self.allow_scopes:
            self._audit_decision(tool_name, clean, "auto", f"unattended allow scope '{scope}'")
            return None
        return self._deny(tool_name, tool_args, f"scope '{scope}' not in schedule's allowed scopes")
