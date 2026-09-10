"""
Tool approval hook — requests user permission before executing dangerous tools.
Borrows pattern from Cline's tool-approval.ts with file-based IPC mechanism.

LuckyD 6.0: wired into the trust foundation (core/trust.py):
  - global mode (ask / auto / step-through) and per-scope + per-site policy
  - "remember" choices: once / session / always (scope) / site (browser)
  - pending-approval queue so a web UI (Trust dashboard) can approve/deny
  - every decision recorded to the audit log ("agentic with receipts")
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.hooks import AgentPlugin, HookContext
from core.trust import (
    describe_decision,
    get_audit_log,
    get_policy,
    host_of_url,
    risk_of,
    scope_of,
)
from core.types import ToolApprovalRequest, ToolApprovalResult, ToolPermissionLevel


@dataclass
class _PendingApproval:
    request: ToolApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    result: ToolApprovalResult | None = None
    remember: str = "once"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_TOOL_PERMISSIONS: dict[str, ToolPermissionLevel] = {
    # ---- Always allow (read-only / stateless / harmless) ----
    "Read": ToolPermissionLevel.ALWAYS_ALLOW,
    "Glob": ToolPermissionLevel.ALWAYS_ALLOW,
    "Grep": ToolPermissionLevel.ALWAYS_ALLOW,
    "FileSearch": ToolPermissionLevel.ALWAYS_ALLOW,
    "Diff": ToolPermissionLevel.ALWAYS_ALLOW,
    "WebSearch": ToolPermissionLevel.ALWAYS_ALLOW,
    "WebMCPDiscover": ToolPermissionLevel.ALWAYS_ALLOW,
    "WebFetch": ToolPermissionLevel.ALWAYS_ALLOW,
    "Http": ToolPermissionLevel.ALWAYS_ALLOW,
    "ShellHistory": ToolPermissionLevel.ALWAYS_ALLOW,
    "TodoRead": ToolPermissionLevel.ALWAYS_ALLOW,
    "DateTime": ToolPermissionLevel.ALWAYS_ALLOW,
    "Sleep": ToolPermissionLevel.ALWAYS_ALLOW,
    "Brief": ToolPermissionLevel.ALWAYS_ALLOW,
    "MemoryRecall": ToolPermissionLevel.ALWAYS_ALLOW,
    "MemorySummary": ToolPermissionLevel.ALWAYS_ALLOW,
    "MemorySearch": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspHover": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspReferences": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspDefinition": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspDocumentSymbols": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspImplementation": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspIncomingCalls": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspOutgoingCalls": ToolPermissionLevel.ALWAYS_ALLOW,
    "LspWorkspaceSymbols": ToolPermissionLevel.ALWAYS_ALLOW,
    "GitDiff": ToolPermissionLevel.ALWAYS_ALLOW,
    "GitLog": ToolPermissionLevel.ALWAYS_ALLOW,
    "GitStatus": ToolPermissionLevel.ALWAYS_ALLOW,
    "GitBranch": ToolPermissionLevel.ALWAYS_ALLOW,
    "Notify": ToolPermissionLevel.ALWAYS_ALLOW,
    "Process": ToolPermissionLevel.ALWAYS_ALLOW,
    "ListAgents": ToolPermissionLevel.ALWAYS_ALLOW,
    "MCPList": ToolPermissionLevel.ALWAYS_ALLOW,
    "SkillList": ToolPermissionLevel.ALWAYS_ALLOW,
    "SessionList": ToolPermissionLevel.ALWAYS_ALLOW,
    "TaskList": ToolPermissionLevel.ALWAYS_ALLOW,
    "TaskGet": ToolPermissionLevel.ALWAYS_ALLOW,
    "ReceiveMessage": ToolPermissionLevel.ALWAYS_ALLOW,
    "DesktopPosition": ToolPermissionLevel.ALWAYS_ALLOW,
    "DesktopWindow": ToolPermissionLevel.ALWAYS_ALLOW,
    # ---- Normal (auto-approved) ----
    "Edit": ToolPermissionLevel.NORMAL,
    "SubAgent": ToolPermissionLevel.NORMAL,
    "TodoWrite": ToolPermissionLevel.NORMAL,
    "MemoryRemember": ToolPermissionLevel.NORMAL,
    "MemoryForget": ToolPermissionLevel.NORMAL,
    "MemoryClear": ToolPermissionLevel.NORMAL,
    "AgentHandoff": ToolPermissionLevel.NORMAL,
    "AskUserQuestion": ToolPermissionLevel.NORMAL,
    "Config": ToolPermissionLevel.NORMAL,
    "OpenInBrowser": ToolPermissionLevel.NORMAL,
    "SkillRun": ToolPermissionLevel.NORMAL,
    "SkillDelete": ToolPermissionLevel.NORMAL,
    "Plan": ToolPermissionLevel.NORMAL,
    "PlanApprove": ToolPermissionLevel.NORMAL,
    "EnterPlanMode": ToolPermissionLevel.NORMAL,
    "ExitPlanMode": ToolPermissionLevel.NORMAL,
    "SessionSave": ToolPermissionLevel.NORMAL,
    "SessionDelete": ToolPermissionLevel.NORMAL,
    "TaskUpdate": ToolPermissionLevel.NORMAL,
    "SendMessage": ToolPermissionLevel.NORMAL,
    "TeamCreate": ToolPermissionLevel.NORMAL,
    "Watch": ToolPermissionLevel.NORMAL,
    "DesktopClipboard": ToolPermissionLevel.NORMAL,
    # ---- Requires approval (write, execute, destructive) ----
    "Write": ToolPermissionLevel.REQUIRES_APPROVAL,
    "Bash": ToolPermissionLevel.REQUIRES_APPROVAL,
    "PowerShell": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserNavigate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserClick": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserType": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserSnapshot": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserScreenshot": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserEvaluate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserClose": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserState": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserEmulate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserIntercept": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserTrace": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserToggleHeadless": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserUse": ToolPermissionLevel.REQUIRES_APPROVAL,
    "BrowserUseClose": ToolPermissionLevel.REQUIRES_APPROVAL,
    "WebMCPCall": ToolPermissionLevel.REQUIRES_APPROVAL,
    "WebMCPShim": ToolPermissionLevel.NORMAL,
    # Scheduled agents (6.0): managing unattended work needs approval; reading is free.
    "ScheduleCreate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "ScheduleUpdate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "ScheduleDelete": ToolPermissionLevel.REQUIRES_APPROVAL,
    "ScheduleEnable": ToolPermissionLevel.REQUIRES_APPROVAL,
    "ScheduleRunNow": ToolPermissionLevel.REQUIRES_APPROVAL,
    "ScheduleDisable": ToolPermissionLevel.NORMAL,
    "ScheduleList": ToolPermissionLevel.ALWAYS_ALLOW,
    "ScheduleGet": ToolPermissionLevel.ALWAYS_ALLOW,
    "ScheduleHistory": ToolPermissionLevel.ALWAYS_ALLOW,
    "ScheduleDigest": ToolPermissionLevel.ALWAYS_ALLOW,
    "GitAdd": ToolPermissionLevel.REQUIRES_APPROVAL,
    "GitCommit": ToolPermissionLevel.REQUIRES_APPROVAL,
    "GitPush": ToolPermissionLevel.REQUIRES_APPROVAL,
    "GitPR": ToolPermissionLevel.REQUIRES_APPROVAL,
    "Graphify": ToolPermissionLevel.REQUIRES_APPROVAL,
    "SQLite": ToolPermissionLevel.REQUIRES_APPROVAL,
    "CSV": ToolPermissionLevel.REQUIRES_APPROVAL,
    "Secrets": ToolPermissionLevel.REQUIRES_APPROVAL,
    "LspRename": ToolPermissionLevel.REQUIRES_APPROVAL,
    "DesktopMouse": ToolPermissionLevel.REQUIRES_APPROVAL,
    "DesktopKeyboard": ToolPermissionLevel.REQUIRES_APPROVAL,
    "DesktopScreenshot": ToolPermissionLevel.REQUIRES_APPROVAL,
    "TaskCreate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "TaskStop": ToolPermissionLevel.REQUIRES_APPROVAL,
}


class ApprovalHook(AgentPlugin):
    """Hook that requests user approval before executing dangerous tools."""

    name = "approval"

    def __init__(
        self,
        approval_callback: Callable[[ToolApprovalRequest], ToolApprovalResult] | None = None,
        approval_dir: str | None = None,
        session_id: str = "default",
        timeout_ms: int = 300000,
    ):
        self.approval_callback = approval_callback
        self.approval_dir = approval_dir
        self.session_id = session_id
        self.timeout_ms = timeout_ms
        self.auto_approve_all = os.environ.get("CODING_AGENT_AUTO_APPROVE", "").lower() in (
            "1",
            "true",
            "yes",
        ) or os.environ.get("CODING_AGENT_YOLO", "").lower() in ("1", "true", "yes")

        # 6.0 trust foundation
        self._policy = None  # lazy: get_policy()
        self._session_allow: set[str] = set()  # tool names approved for this session
        self._pending: dict[str, _PendingApproval] = {}
        self._pending_lock = threading.Lock()

    @property
    def policy(self):
        if self._policy is None:
            self._policy = get_policy()
        return self._policy

    # ── pending approvals (for the Trust dashboard / web UI) ──────────────

    def pending_requests(self) -> list[dict[str, Any]]:
        """Snapshot of approvals currently waiting on the user."""
        with self._pending_lock:
            items = list(self._pending.values())
        return [
            {
                "call_id": p.request.call_id,
                "tool": p.request.tool_name,
                "scope": scope_of(p.request.tool_name),
                "risk": p.request.risk_level,
                "summary": describe_decision(p.request.tool_name, p.request.tool_args),
                "args": p.request.tool_args,
                "session_id": p.request.session_id,
                "created_at": p.created_at,
            }
            for p in items
        ]

    def resolve_approval(
        self,
        call_id: str,
        approved: bool,
        reason: str | None = None,
        remember: str = "once",
    ) -> bool:
        """Resolve a pending approval from the UI. Returns False if unknown."""
        with self._pending_lock:
            pending = self._pending.get(call_id)
            if pending is None:
                return False
            pending.result = ToolApprovalResult(approved=approved, reason=reason)
            pending.remember = (
                remember if remember in ("once", "session", "always", "site") else "once"
            )
            pending.event.set()
            return True

    def _apply_remember(
        self, tool_name: str, tool_args: dict, remember: str, approved: bool
    ) -> None:
        if not approved:
            return
        if remember == "session":
            self._session_allow.add(tool_name)
        elif remember == "always":
            self.policy.set_scope_policy(scope_of(tool_name), "allow")
        elif remember == "site" and scope_of(tool_name) == "browser":
            url = tool_args.get("url", "")
            host = host_of_url(url) if url else None
            if host:
                self.policy.set_site_policy(host, "allow")

    def _audit_decision(
        self, tool_name: str, tool_args: dict, decision: str, reason: str = ""
    ) -> None:
        with contextlib.suppress(Exception):  # auditing must never break the agent
            get_audit_log().record(
                tool_name,
                tool_args,
                session_id=self.session_id,
                decision=decision,
                summary=reason or describe_decision(tool_name, tool_args),
            )

    def set_permission(self, tool_name: str, level: ToolPermissionLevel):
        _TOOL_PERMISSIONS[tool_name] = level

    def get_permission(self, tool_name: str) -> ToolPermissionLevel:
        return _TOOL_PERMISSIONS.get(tool_name, ToolPermissionLevel.NORMAL)

    def before_tool(self, tool_name: str, tool_args: dict, ctx: HookContext) -> dict | None:
        level = self.get_permission(tool_name)
        clean_args = {k: v for k, v in tool_args.items() if not k.startswith("_")}
        scope = scope_of(tool_name)

        if level == ToolPermissionLevel.BLOCKED:
            self._audit_decision(tool_name, clean_args, "blocked", "Tool is blocked")
            return {
                "role": "tool",
                "tool_call_id": tool_args.get("_id", "unknown"),
                "content": f"Error: Tool '{tool_name}' is blocked for security reasons.",
            }

        mode = self.policy.mode
        # Global auto mode (or legacy env flag): approve everything.
        if self.auto_approve_all or mode == "auto":
            return None

        # "Approve for this session" memory.
        if tool_name in self._session_allow:
            self._audit_decision(tool_name, clean_args, "approved", "Allowed for session")
            return None

        # Step-through mode: ask for every tool call, even read-only ones.
        if mode == "step-through":
            return self._request_approval(tool_name, tool_args)

        # Per-site rules for browser control ("always allow on this site").
        if scope == "browser":
            url = clean_args.get("url", "")
            host = host_of_url(url) if url else None
            if host:
                site = self.policy.site_policy(host)
                if site == "allow":
                    self._audit_decision(tool_name, clean_args, "auto", f"Site rule: allow {host}")
                    return None
                if site == "deny":
                    self._audit_decision(tool_name, clean_args, "denied", f"Site rule: deny {host}")
                    return {
                        "role": "tool",
                        "tool_call_id": tool_args.get("_id", "unknown"),
                        "content": f"Tool execution denied by site policy for {host}.",
                    }

        # Per-scope policy.
        scope_policy = self.policy.scope_policy(scope)
        if scope_policy == "deny":
            self._audit_decision(
                tool_name, clean_args, "denied", f"Scope '{scope}' denied by policy"
            )
            return {
                "role": "tool",
                "tool_call_id": tool_args.get("_id", "unknown"),
                "content": f"Tool execution denied: scope '{scope}' is denied in trust policy.",
            }
        if scope_policy == "allow":
            return None

        if level == ToolPermissionLevel.ALWAYS_ALLOW:
            return None
        if level == ToolPermissionLevel.REQUIRES_APPROVAL:
            return self._request_approval(tool_name, tool_args)
        # NORMAL: check policy
        if ctx.config:
            policies = ctx.config.get("tool_policies", {})
            tp = policies.get(tool_name, policies.get("*", {}))
            if tp.get("auto_approve", False):
                return None
            if tp.get("block", False):
                self._audit_decision(tool_name, clean_args, "blocked", "Blocked by tool policy")
                return {
                    "role": "tool",
                    "tool_call_id": tool_args.get("_id", "unknown"),
                    "content": f"Error: Tool '{tool_name}' is blocked by policy.",
                }
        return self._request_approval(tool_name, tool_args)

    def _request_approval(self, tool_name: str, tool_args: dict) -> dict | None:
        clean_args = {k: v for k, v in tool_args.items() if not k.startswith("_")}
        request = ToolApprovalRequest(
            tool_name=tool_name,
            tool_args=clean_args,
            call_id=tool_args.get("_id", "unknown"),
            session_id=self.session_id,
            turn=0,
            risk_level=risk_of(tool_name),
        )
        if self.approval_callback:
            result = self.approval_callback(request)
            approved = bool(result and result.approved)
            self._audit_decision(
                tool_name,
                clean_args,
                "approved" if approved else "denied",
                (result.reason if result else None) or "Decided via approval callback",
            )
            if not approved:
                reason = result.reason if result else "No approval provided"
                return {
                    "role": "tool",
                    "tool_call_id": request.call_id,
                    "content": f"Tool execution denied by user: {reason}",
                }
            return None
        if self.approval_dir:
            return self._file_based_approval(request)
        # No callback and no approval dir: park in the pending queue so a UI
        # (e.g. the Trust dashboard) can approve/deny. Used by web_server HQ.
        return self._queued_approval(request)

    def _queued_approval(self, request: ToolApprovalRequest) -> dict | None:
        pending = _PendingApproval(request=request)
        with self._pending_lock:
            self._pending[request.call_id] = pending
        try:
            signaled = pending.event.wait(self.timeout_ms / 1000)
        finally:
            with self._pending_lock:
                self._pending.pop(request.call_id, None)
        if not signaled or pending.result is None:
            self._audit_decision(
                request.tool_name,
                request.tool_args,
                "denied",
                f"Approval timed out after {self.timeout_ms / 1000:.0f}s",
            )
            return {
                "role": "tool",
                "tool_call_id": request.call_id,
                "content": (
                    f"Tool approval timed out after {self.timeout_ms / 1000:.0f}s "
                    f"for '{request.tool_name}'."
                ),
            }
        result = pending.result
        self._apply_remember(
            request.tool_name, request.tool_args, pending.remember, result.approved
        )
        self._audit_decision(
            request.tool_name,
            request.tool_args,
            "approved" if result.approved else "denied",
            (result.reason or "") + f" [remember: {pending.remember}]",
        )
        if not result.approved:
            return {
                "role": "tool",
                "tool_call_id": request.call_id,
                "content": f"Tool execution denied by user: {result.reason or 'No reason'}",
            }
        return None

    def _file_based_approval(self, request: ToolApprovalRequest) -> dict | None:
        """File-based IPC approval (pattern from Cline's tool-approval.ts)."""
        approval_dir = Path(self.approval_dir)
        approval_dir.mkdir(parents=True, exist_ok=True)
        rid = request.tool_name.replace(" ", "_").lower()
        req_path = approval_dir / f"{self.session_id}.request.{rid}.json"
        dec_path = approval_dir / f"{self.session_id}.decision.{rid}.json"
        req_path.write_text(
            json.dumps(
                {
                    "requestId": rid,
                    "sessionId": self.session_id,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "toolCallId": request.call_id,
                    "toolName": request.tool_name,
                    "input": request.tool_args,
                    "risk": "high",
                },
                indent=2,
            )
        )
        started_at = time.monotonic()
        while (time.monotonic() - started_at) * 1000 < self.timeout_ms:
            if dec_path.exists():
                try:
                    data = json.loads(dec_path.read_text())
                    dec_path.unlink(missing_ok=True)
                    req_path.unlink(missing_ok=True)
                    approved = bool(data.get("approved", False))
                    self._audit_decision(
                        request.tool_name,
                        request.tool_args,
                        "approved" if approved else "denied",
                        "Decided via file-based approval",
                    )
                    if approved:
                        return None
                    return {
                        "role": "tool",
                        "tool_call_id": request.call_id,
                        "content": f"Tool execution denied: {data.get('reason', 'No reason')}",
                    }
                except Exception:
                    pass
            time.sleep(0.2)
        req_path.unlink(missing_ok=True)
        return {
            "role": "tool",
            "tool_call_id": request.call_id,
            "content": f"Tool approval timed out after {self.timeout_ms / 1000}s for '{request.tool_name}'.",
        }
