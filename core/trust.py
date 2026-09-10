"""
Trust foundation — "agentic with receipts" (LuckyD 6.0).

Three pieces:
  - Permission *scopes*: tools grouped into human-meaningful capabilities
    ("Control browser tabs", "Run shell commands", ...) instead of a flat
    ~100-tool list. Powers the Trust dashboard's "what the agent can touch".
  - AuditLog: every tool call recorded (tool, redacted args, scope, risk,
    approval decision, duration, outcome) to a JSONL log. The receipts.
  - TrustPolicy: persistent user policy — global mode (ask / auto /
    step-through), per-scope policy (ask / allow / deny), and per-site
    rules for browser control.

Storage lives under ~/.luckyd (LUCKYD_DATA_DIR overrides, used by tests).
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    override = os.environ.get("LUCKYD_DATA_DIR")
    base = Path(override) if override else Path.home() / ".luckyd"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ── Permission scopes ────────────────────────────────────────────────────

SCOPES: dict[str, dict[str, str]] = {
    "read": {
        "title": "Read files & code",
        "desc": "Read files, search code, inspect symbols. Never modifies anything.",
    },
    "network": {
        "title": "Fetch from the web",
        "desc": "Web search and page fetches. Read-only network access.",
    },
    "browser": {
        "title": "Control browser tabs",
        "desc": "Navigate, click, type and screenshot in live browser tabs.",
    },
    "files": {
        "title": "Write & edit files",
        "desc": "Create, edit and delete files on disk.",
    },
    "shell": {
        "title": "Run shell commands",
        "desc": "Execute Bash / PowerShell commands with your user privileges.",
    },
    "desktop": {
        "title": "Control mouse & keyboard",
        "desc": "Move the mouse, press keys, capture the screen.",
    },
    "git": {
        "title": "Git operations",
        "desc": "Stage, commit, push and open pull requests.",
    },
    "memory": {
        "title": "Agent memory",
        "desc": "Recall and store long-term memories about you and your work.",
    },
    "agents": {
        "title": "Spawn agents & tasks",
        "desc": "Launch sub-agents, background tasks and skills.",
    },
    "mcp": {
        "title": "External MCP servers",
        "desc": "Call tools on connected third-party MCP servers.",
    },
    "system": {
        "title": "System & config",
        "desc": "Configuration, sessions, notifications and misc utilities.",
    },
}

# Explicit tool -> scope overrides (most precise). Everything else falls
# through to the prefix heuristics below, then to "system".
_TOOL_SCOPE_OVERRIDES: dict[str, str] = {
    # read
    "Read": "read",
    "Glob": "read",
    "Grep": "read",
    "FileSearch": "read",
    "Diff": "read",
    "LspHover": "read",
    "LspReferences": "read",
    "LspDefinition": "read",
    "LspDocumentSymbols": "read",
    "LspImplementation": "read",
    "LspIncomingCalls": "read",
    "LspOutgoingCalls": "read",
    "LspWorkspaceSymbols": "read",
    "ShellHistory": "read",
    # network
    "WebSearch": "network",
    "WebFetch": "network",
    "Http": "network",
    # webmcp — site-exposed agent tools drive live tabs, so they belong to
    # the browser scope (not network), even though the names start with Web.
    "WebMCPDiscover": "browser",
    "WebMCPCall": "browser",
    "WebMCPShim": "browser",
    # scheduled agents manage the agent fleet
    "ScheduleCreate": "agents",
    "ScheduleList": "agents",
    "ScheduleGet": "agents",
    "ScheduleUpdate": "agents",
    "ScheduleDelete": "agents",
    "ScheduleEnable": "agents",
    "ScheduleDisable": "agents",
    "ScheduleRunNow": "agents",
    "ScheduleHistory": "agents",
    "ScheduleDigest": "agents",
    # browser
    "BrowserNavigate": "browser",
    "BrowserClick": "browser",
    "BrowserType": "browser",
    "BrowserSnapshot": "browser",
    "BrowserScreenshot": "browser",
    "BrowserEvaluate": "browser",
    "BrowserClose": "browser",
    "BrowserState": "browser",
    "BrowserEmulate": "browser",
    "BrowserIntercept": "browser",
    "BrowserTrace": "browser",
    "BrowserToggleHeadless": "browser",
    "BrowserUse": "browser",
    "BrowserUseClose": "browser",
    "OpenInBrowser": "browser",
    # files
    "Write": "files",
    "Edit": "files",
    # shell
    "Bash": "shell",
    "PowerShell": "shell",
    # desktop
    "DesktopMouse": "desktop",
    "DesktopKeyboard": "desktop",
    "DesktopScreenshot": "desktop",
    "DesktopClipboard": "desktop",
    "DesktopPosition": "desktop",
    "DesktopWindow": "desktop",
    # git
    "GitAdd": "git",
    "GitCommit": "git",
    "GitPush": "git",
    "GitPR": "git",
    "GitDiff": "read",
    "GitLog": "read",
    "GitStatus": "read",
    "GitBranch": "read",
    # memory
    "MemoryRecall": "memory",
    "MemoryRemember": "memory",
    "MemoryForget": "memory",
    "MemoryClear": "memory",
    "MemorySummary": "memory",
    "MemorySearch": "memory",
    # agents / tasks / skills
    "SubAgent": "agents",
    "AgentHandoff": "agents",
    "TaskCreate": "agents",
    "TaskUpdate": "agents",
    "TaskStop": "agents",
    "TaskList": "agents",
    "TaskGet": "agents",
    "Watch": "agents",
    "TeamCreate": "agents",
    "SendMessage": "agents",
    "ReceiveMessage": "agents",
    "SkillRun": "agents",
    "SkillList": "agents",
    "SkillDelete": "agents",
    "SkillSearch": "agents",
    "SkillInfo": "agents",
    "SkillFetch": "agents",
    "SkillInstall": "agents",
    "SkillUpdate": "agents",
    "SkillRemove": "agents",
    "SkillPublish": "agents",
    "SkillSources": "agents",
    "SkillSourceAdd": "agents",
    "SkillSourceRemove": "agents",
    "Plan": "agents",
    "PlanApprove": "agents",
    "EnterPlanMode": "agents",
    "ExitPlanMode": "agents",
    # mcp
    "MCPList": "mcp",
    # system
    "Config": "system",
    "Process": "system",
    "Notify": "system",
    "SessionSave": "system",
    "SessionDelete": "system",
    "SessionList": "system",
    "TodoWrite": "system",
    "TodoRead": "system",
    "Brief": "system",
    "DateTime": "system",
    "Sleep": "system",
    "AskUserQuestion": "system",
    "LspRename": "system",
    "Graphify": "system",
    "SQLite": "system",
    "CSV": "system",
    "Secrets": "system",
}

#: Public view of explicit tool -> scope overrides (used by tests and UI).
TOOL_SCOPE_OVERRIDES: dict[str, str] = _TOOL_SCOPE_OVERRIDES

_SCOPE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Browser", "browser"),
    ("Desktop", "desktop"),
    ("Git", "git"),
    ("Memory", "memory"),
    ("MCP", "mcp"),
    ("Web", "network"),
    ("Lsp", "read"),
    ("Task", "agents"),
    ("Session", "system"),
    ("Skill", "agents"),
)


def scope_of(tool_name: str) -> str:
    """Return the permission scope id for a tool name."""
    if tool_name in _TOOL_SCOPE_OVERRIDES:
        return _TOOL_SCOPE_OVERRIDES[tool_name]
    for prefix, scope in _SCOPE_PREFIXES:
        if tool_name.startswith(prefix):
            return scope
    return "system"


def risk_of(tool_name: str) -> str:
    """low / medium / high — derived from the approval permission level."""
    try:
        from core.approval_hook import _TOOL_PERMISSIONS
        from core.types import ToolPermissionLevel

        level = _TOOL_PERMISSIONS.get(tool_name, ToolPermissionLevel.NORMAL)
    except Exception:
        return "medium"
    if level.name == "ALWAYS_ALLOW":
        return "low"
    if level.name == "REQUIRES_APPROVAL":
        return "high"
    return "medium"


# ── Argument redaction ───────────────────────────────────────────────────

_SECRET_KEY_RE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|bearer|credential|auth", re.I
)


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Mask secret-looking values so the audit log never stores credentials."""
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            if _SECRET_KEY_RE.search(key):
                redacted[key] = "***"
            elif len(value) > 500:
                redacted[key] = value[:500] + f"…[{len(value)} chars]"
            else:
                redacted[key] = value
        elif isinstance(value, dict):
            redacted[key] = redact_args(value)
        else:
            redacted[key] = value
    return redacted


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Audit log ────────────────────────────────────────────────────────────


class AuditLog:
    """Append-only JSONL record of everything the agent did. The receipts."""

    def __init__(self, path: Path | None = None):
        self.path = path or (_data_dir() / "audit" / "audit.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        session_id: str = "default",
        decision: str = "executed",  # executed | approved | denied | blocked | auto
        scope: str | None = None,
        risk: str | None = None,
        duration_ms: float = 0.0,
        ok: bool = True,
        summary: str = "",
    ) -> dict[str, Any]:
        entry = {
            "ts": _utcnow(),
            "session_id": session_id,
            "tool": tool,
            "scope": scope or scope_of(tool),
            "risk": risk or risk_of(tool),
            "args": redact_args(args or {}),
            "decision": decision,
            "duration_ms": round(duration_ms, 1),
            "ok": ok,
            "summary": summary[:300],
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self._lock, self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events

    def recent(
        self,
        limit: int = 100,
        *,
        tool: str | None = None,
        scope: str | None = None,
        session_id: str | None = None,
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        events = self._read_all()
        if tool:
            events = [e for e in events if e.get("tool") == tool]
        if scope:
            events = [e for e in events if e.get("scope") == scope]
        if session_id:
            events = [e for e in events if e.get("session_id") == session_id]
        if decision:
            events = [e for e in events if e.get("decision") == decision]
        return events[-limit:][::-1]

    def stats(self) -> dict[str, Any]:
        events = self._read_all()
        by_scope: dict[str, int] = {}
        by_decision: dict[str, int] = {}
        for e in events:
            by_scope[e.get("scope", "?")] = by_scope.get(e.get("scope", "?"), 0) + 1
            by_decision[e.get("decision", "?")] = by_decision.get(e.get("decision", "?"), 0) + 1
        return {
            "total": len(events),
            "by_scope": by_scope,
            "by_decision": by_decision,
            "denied": by_decision.get("denied", 0) + by_decision.get("blocked", 0),
        }

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()


_audit_log: AuditLog | None = None


def get_audit_log() -> AuditLog:
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog()
    return _audit_log


# ── Trust policy ─────────────────────────────────────────────────────────

# mode: ask (default) | auto (approve everything) | step-through (ask for every tool)
# scope policy: ask | allow | deny
# site policy (browser scope): allow | deny — "approve always for this site"
_DEFAULT_SCOPE_POLICIES: dict[str, str] = {
    "read": "allow",
    "network": "allow",
    "memory": "allow",
    "system": "allow",
    "browser": "ask",
    "files": "ask",
    "shell": "ask",
    "desktop": "ask",
    "git": "ask",
    "agents": "ask",
    "mcp": "ask",
}

_VALID_POLICIES = ("ask", "allow", "deny")
_VALID_MODES = ("ask", "auto", "step-through")


class TrustPolicy:
    """Persistent user policy: global mode, per-scope and per-site rules."""

    def __init__(self, path: Path | None = None):
        self.path = path or (_data_dir() / "trust" / "policy.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                data.setdefault("mode", "ask")
                data.setdefault("scopes", {})
                data.setdefault("sites", {})
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"version": 1, "mode": "ask", "scopes": {}, "sites": {}, "updated_at": _utcnow()}

    def _save(self) -> None:
        self._data["updated_at"] = _utcnow()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- mode --
    @property
    def mode(self) -> str:
        return self._data.get("mode", "ask")

    def set_mode(self, mode: str) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"invalid mode: {mode}")
        with self._lock:
            self._data["mode"] = mode
            self._save()

    # -- scopes --
    def scope_policy(self, scope: str) -> str:
        return self._data.get("scopes", {}).get(scope, _DEFAULT_SCOPE_POLICIES.get(scope, "ask"))

    def set_scope_policy(self, scope: str, policy: str) -> None:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope: {scope}")
        if policy not in _VALID_POLICIES:
            raise ValueError(f"invalid policy: {policy}")
        with self._lock:
            self._data.setdefault("scopes", {})[scope] = policy
            self._save()

    def all_scope_policies(self) -> dict[str, str]:
        return {sid: self.scope_policy(sid) for sid in SCOPES}

    # -- sites (browser scope) --
    def site_policy(self, host: str) -> str | None:
        return self._data.get("sites", {}).get(host.lower())

    def set_site_policy(self, host: str, policy: str) -> None:
        if policy not in ("allow", "deny"):
            raise ValueError(f"invalid site policy: {policy}")
        with self._lock:
            self._data.setdefault("sites", {})[host.lower()] = policy
            self._save()

    def clear_site_policy(self, host: str) -> None:
        with self._lock:
            self._data.get("sites", {}).pop(host.lower(), None)
            self._save()

    def sites(self) -> dict[str, str]:
        return dict(self._data.get("sites", {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scopes": self.all_scope_policies(),
            "sites": self.sites(),
            "updated_at": self._data.get("updated_at"),
        }


_policy: TrustPolicy | None = None


def get_policy() -> TrustPolicy:
    global _policy
    if _policy is None:
        _policy = TrustPolicy()
    return _policy


def host_of_url(url: str) -> str | None:
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").lower() or None
    except Exception:
        return None


def describe_decision(tool: str, args: dict[str, Any]) -> str:
    """One-line human summary of what a tool call would do (for approval UI)."""
    scope = scope_of(tool)
    if tool == "BrowserNavigate":
        return f"Navigate to {args.get('url', '?')}"
    if tool == "BrowserClick":
        return f"Click {args.get('selector', '?')}"
    if tool == "BrowserType":
        text = str(args.get("text", ""))
        return f"Type into {args.get('selector', '?')}: {text[:60]!r}"
    if tool in ("Bash", "PowerShell"):
        cmd = str(args.get("command", args.get("script", "")))
        return f"Run command: {cmd[:90]}"
    if tool == "Write":
        return f"Write file {args.get('path', args.get('file', '?'))}"
    if tool == "Edit":
        return f"Edit file {args.get('path', args.get('file', '?'))}"
    if tool in ("GitCommit",):
        return f"Git commit: {str(args.get('message', ''))[:80]}"
    if tool == "GitPush":
        return "Push commits to remote"
    if tool == "DesktopMouse":
        return f"Mouse {args.get('action', 'move')} at ({args.get('x', '?')}, {args.get('y', '?')})"
    return f"{tool} ({scope})"
