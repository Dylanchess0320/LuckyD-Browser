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
import logging
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

log = logging.getLogger(__name__)


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
    "SkillSearch": ToolPermissionLevel.ALWAYS_ALLOW,
    "SkillInfo": ToolPermissionLevel.ALWAYS_ALLOW,
    "SkillFetch": ToolPermissionLevel.ALWAYS_ALLOW,
    "SkillSources": ToolPermissionLevel.ALWAYS_ALLOW,
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
    "SkillPublish": ToolPermissionLevel.NORMAL,
    # ---- Requires approval ----
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
    "SkillInstall": ToolPermissionLevel.REQUIRES_APPROVAL,
    "SkillUpdate": ToolPermissionLevel.REQUIRES_APPROVAL,
    "SkillRemove": ToolPermissionLevel.REQUIRES_APPROVAL,
    "SkillSourceAdd": ToolPermissionLevel.REQUIRES_APPROVAL,
    "SkillSourceRemove": ToolPermissionLevel.REQUIRES_APPROVAL,
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

#: Public view of tool -> permission-level mapping (used by tests and UI).
TOOL_PERMISSIONS: dict[str, ToolPermissionLevel] = _TOOL_PERMISSIONS


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
        # Explicit per-run "auto-approve low-risk only" mode. When True, tools
        # whose trust risk level is "low" (read-only / harmless) skip the
        # approval request — everything else still follows the trust policy
        # below. Every such skip is recorded in the audit log. This is the
        # only sanctioned auto-approve mode; the legacy blanket
        # auto_approve_all flag is inert (see the property below).
        self.auto_approve_low_risk = False

        # 6.0 trust foundation
        self._policy = None  # lazy: get_policy()
        self._session_allow: set[str] = set()  # tool names approved for this session
        self._pending: dict[str, _PendingApproval] = {}
        self._pending_lock = threading.Lock()

    @property
    def auto_approve_all(self) -> bool:
        """Legacy blanket auto-approve flag — inert since 6.1.

        Always reads False. Assigning a truthy value logs a deprecation
        warning and is ignored: approvals may only be skipped when the trust
        policy explicitly authorizes it (policy mode, per-scope/site rules,
        or the explicit per-run ``auto_approve_low_risk`` mode), and every
        skip is recorded in the audit log.
        """
        return False

    @auto_approve_all.setter
    def auto_approve_all(self, value: bool) -> None:
        if value:
            log.warning(
                "auto_approve_all is deprecated and inert since LuckyD 6.1: "
                "blanket auto-approval was removed. Use the trust policy "
                "(mode / per-scope rules) or the explicit per-run "
                "auto_approve_low_risk mode instead."
            )

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

    def evaluate_policy(self, tool_name: str, tool_args: dict, ctx: HookContext) -> tuple[str, str]:
        """Evaluate the trust policy for a tool call without asking anyone.

        Returns ``(decision, reason)`` where decision is one of:

        - ``"approved"`` — the trust policy explicitly authorizes skipping the
          approval request (policy mode, per-scope/site allow rules, or the
          explicit per-run ``auto_approve_low_risk`` mode for low-risk tools).
        - ``"denied"`` — the trust policy blocks this tool call.
        - ``"needs_approval"`` — a human must decide; the caller should use
          the interactive request path (:meth:`before_tool`).

        Every evaluation is recorded in the audit log with its reason — no
        silent skips. This is also what non-interactive callers (e.g. the HQ
        web server's schedule routes) use to gate mutating actions.
        """
        level = self.get_permission(tool_name)
        clean_args = {k: v for k, v in tool_args.items() if not k.startswith("_")}
        scope = scope_of(tool_name)

        if level == ToolPermissionLevel.BLOCKED:
            self._audit_decision(tool_name, clean_args, "blocked", "Tool is blocked")
            return "denied", f"Tool '{tool_name}' is blocked for security reasons."

        # Explicit per-run low-risk auto-approve: only low-risk tools skip the
        # approval request. Everything else continues through the policy below.
        if self.auto_approve_low_risk and risk_of(tool_name) == "low":
            reason = "auto-approve low-risk run mode"
            self._audit_decision(tool_name, clean_args, "auto", reason)
            return "approved", reason

        mode = self.policy.mode
        # The trust policy's own "auto" mode is the explicit, user-configured
        # way to approve everything (set via the Trust dashboard). Unlike the
        # removed legacy flag, the skip is recorded in the audit log.
        if mode == "auto":
            self._audit_decision(tool_name, clean_args, "auto", "trust policy mode: auto")
            return "approved", "trust policy mode: auto"

        # "Approve for this session" memory.
        if tool_name in self._session_allow:
            self._audit_decision(tool_name, clean_args, "approved", "Allowed for session")
            return "approved", "Allowed for session"

        # Step-through mode: ask for every tool call, even read-only ones.
        if mode == "step-through":
            return "needs_approval", "trust policy mode: step-through (ask for every tool)"

        # Per-site rules for browser control ("always allow on this site").
        if scope == "browser":
            url = clean_args.get("url", "")
            host = host_of_url(url) if url else None
            if host:
                site = self.policy.site_policy(host)
                if site == "allow":
                    reason = f"Site rule: allow {host}"
                    self._audit_decision(tool_name, clean_args, "auto", reason)
                    return "approved", reason
                if site == "deny":
                    reason = f"Tool execution denied by site policy for {host}."
                    self._audit_decision(tool_name, clean_args, "denied", reason)
                    return "denied", reason

        # Per-scope policy.
        scope_policy = self.policy.scope_policy(scope)
        if scope_policy == "deny":
            reason = f"Tool execution denied: scope '{scope}' is denied in trust policy."
            self._audit_decision(tool_name, clean_args, "denied", reason)
            return "denied", reason
        if scope_policy == "allow":
            reason = f"Scope '{scope}' allowed by trust policy."
            self._audit_decision(tool_name, clean_args, "auto", reason)
            return "approved", reason

        if level == ToolPermissionLevel.ALWAYS_ALLOW:
            reason = "read-only tool (always allowed)"
            self._audit_decision(tool_name, clean_args, "auto", reason)
            return "approved", reason
        if level == ToolPermissionLevel.REQUIRES_APPROVAL:
            return "needs_approval", f"Tool '{tool_name}' requires approval."
        # NORMAL: check policy
        if ctx.config:
            policies = ctx.config.get("tool_policies", {})
            tp = policies.get(tool_name, policies.get("*", {}))
            if tp.get("auto_approve", False):
                reason = "auto-approved by tool policy"
                self._audit_decision(tool_name, clean_args, "auto", reason)
                return "approved", reason
            if tp.get("block", False):
                reason = f"Tool '{tool_name}' is blocked by policy."
                self._audit_decision(tool_name, clean_args, "blocked", reason)
                return "denied", reason
        return "needs_approval", f"Tool '{tool_name}' requires approval."

    def before_tool(self, tool_name: str, tool_args: dict, ctx: HookContext) -> dict | None:
        decision, reason = self.evaluate_policy(tool_name, tool_args, ctx)
        if decision == "approved":
            return None
        if decision == "denied":
            return {
                "role": "tool",
                "tool_call_id": tool_args.get("_id", "unknown"),
                "content": f"Error: {reason}",
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
