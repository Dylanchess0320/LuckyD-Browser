"""
Audit hook — records every tool execution to the audit log ("the receipts").

Installs as an AgentPlugin: before_tool stamps the start time, after_tool
writes the execution record (duration, outcome, output summary). Approval
decisions are recorded separately by ApprovalHook.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from core.hooks import AgentPlugin, HookContext
from core.trust import get_audit_log, scope_of


class AuditHook(AgentPlugin):
    """Plugin that audits every tool execution to the JSONL audit log."""

    name = "audit"

    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self._starts: dict[str, float] = {}

    def before_tool(self, tool_name: str, tool_args: dict, ctx: HookContext) -> Any | None:
        call_id = str(tool_args.get("_id", f"{tool_name}-{time.time_ns()}"))
        self._starts[call_id] = time.monotonic()
        return None

    def after_tool(self, tool_name: str, tool_args: dict, result: Any, ctx: HookContext) -> Any:
        call_id = str(tool_args.get("_id", "unknown"))
        started = self._starts.pop(call_id, None)
        duration_ms = (time.monotonic() - started) * 1000 if started else 0.0
        ok, summary = self._summarize_result(result)
        # Don't double-log tool calls that hooks already blocked/denied —
        # the hook returns a plain dict message in that case, and ApprovalHook
        # records the decision itself. We only skip when the tool never ran.
        if isinstance(result, dict) and result.get("_audit_skip"):
            return result
        with contextlib.suppress(Exception):  # auditing must never break the agent
            get_audit_log().record(
                tool_name,
                {k: v for k, v in tool_args.items() if not k.startswith("_")},
                session_id=self.session_id,
                decision="executed",
                scope=scope_of(tool_name),
                duration_ms=duration_ms,
                ok=ok,
                summary=summary,
            )
        return result

    @staticmethod
    def _summarize_result(result: Any) -> tuple[bool, str]:
        if result is None:
            return True, ""
        if isinstance(result, dict):
            content = str(result.get("content", ""))
            ok = not content.startswith("Error")
            return ok, content
        text = getattr(result, "text", "")
        ok = not getattr(result, "error", False)
        return ok, str(text)
