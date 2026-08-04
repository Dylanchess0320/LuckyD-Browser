"""
Typed dataclasses for the agent engine — events, messages, results, and state.
Borrows patterns from Cline's typed event system (CoreSessionEvent, etc.).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ── Enums ──────────────────────────────────────────────────────────────


class ToolStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    PENDING_APPROVAL = "pending_approval"


class AgentEventType(Enum):
    """Typed event system — mirrors Cline's CoreSessionEvent pattern."""

    MODEL_REQUEST = "model_request"
    MODEL_CHUNK = "model_chunk"
    MODEL_THINK_CHUNK = "model_think_chunk"
    MODEL_RESPONSE = "model_response"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_ERROR = "tool_error"
    TOOL_APPROVAL_REQUEST = "tool_approval_request"
    TOOL_APPROVAL_RESULT = "tool_approval_result"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    ERROR = "error"
    WARNING = "warning"
    DEBUG = "debug"
    CONTEXT_TRUNCATED = "context_truncated"
    MEMORY_REFRESH = "memory_refresh"
    CHECKPOINT_CREATED = "checkpoint_created"
    TOOL_RESULT_TRUNCATED = "tool_result_truncated"
    TOOL_CONTINUATION = "tool_continuation"
    BACKPRESSURE_APPLIED = "backpressure_applied"


class CompactionStrategy(Enum):
    """Context compaction strategies. Mirrors Cline's GlobalCompactionStrategy."""

    NONE = "none"  # No compaction
    TRUNCATE = "truncate"  # Simple truncation (current default)
    SUMMARIZE = "summarize"  # LLM-based summarization of old turns
    HYBRID = "hybrid"  # Summarize old, keep recent intact
    MICROCOMPACT = "microcompact"  # Aggressive micro-compaction of tool results


class ToolPermissionLevel(Enum):
    """Permission levels for tools. Used by the approval system."""

    ALWAYS_ALLOW = "always_allow"  # No approval needed (e.g., Read, Grep)
    NORMAL = "normal"  # Approval at user's discretion
    REQUIRES_APPROVAL = "requires_approval"  # Always require approval (e.g., Bash, Write, Edit)
    BLOCKED = "blocked"  # Never allowed


# ── Events ────────────────────────────────────────────────────────────


@dataclass
class AgentEvent:
    """A structured event emitted by the agent during execution."""

    type: AgentEventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    turn: int = 0
    agent_id: str = "main"


@dataclass
class ToolCallEvent:
    """Emitted when a tool call starts or ends."""

    tool_name: str
    tool_args: dict[str, Any]
    call_id: str
    status: ToolStatus
    result_preview: str = ""
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class ToolApprovalRequest:
    """Request for user approval before executing a tool."""

    tool_name: str
    tool_args: dict[str, Any]
    call_id: str
    session_id: str
    turn: int
    risk_level: str = "unknown"  # "low", "medium", "high"


@dataclass
class ToolApprovalResult:
    """Result of a tool approval request."""

    approved: bool
    reason: str | None = None


@dataclass
class ToolResult:
    """Standardized tool result with metadata."""

    text: str
    error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    truncated: bool = False
    has_continuation: bool = False


# ── Agent state ───────────────────────────────────────────────────────


@dataclass
class AgentState:
    """Snapshot of agent state at any point."""

    turn_count: int
    message_count: int
    memory_refreshes: int
    total_tool_calls: int
    total_errors: int
    conversation_id: str
    provider: str
    model: str
    duration_seconds: float = 0.0


# ── Checkpoint ────────────────────────────────────────────────────────


@dataclass
class FileCheckpoint:
    """A snapshot of file state before an edit, for undo/restore."""

    file_path: str
    content_before: str
    content_after: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checkpoint_id: str = ""
    tool_call_id: str = ""


@dataclass
class CheckpointDiff:
    """A unified diff between two checkpoints."""

    file_path: str
    diff_text: str
    additions: int = 0
    deletions: int = 0


# ── Hook types ────────────────────────────────────────────────────────


@dataclass
class HookContext:
    """Context passed to hooks during execution."""

    turn: int
    messages: list[dict]
    config: dict[str, Any] | None = None


# Type aliases for hook callbacks
BeforeToolHook = Callable[[str, dict[str, Any], HookContext], Any | None]
AfterToolHook = Callable[[str, dict[str, Any], Any, HookContext], Any]
BeforeModelHook = Callable[[list[dict], HookContext], list[dict]]
AfterModelHook = Callable[[dict | None, HookContext], dict | None]


# ── Callbacks (for UI integration — kept for backward compat) ─────────


@dataclass
class AgentCallbacks:
    """Callbacks for UI streaming and interaction."""

    stream_token: Callable[[str], None] | None = None
    stream_think_token: Callable[[str], None] | None = None
    on_event: Callable[[AgentEvent], None] | None = None
    on_tool_approval: Callable[[ToolApprovalRequest], ToolApprovalResult] | None = None
