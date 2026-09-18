"""Build with LuckyD — the integration kit for building on top of LuckyD.

"Agentic with receipts": LuckyD's permission scopes, approval policy engine,
secret redaction, append-only audit log, and agent-loop circuit breaker,
packaged so any project can build with them. Zero dependencies beyond the
Python standard library.

Copy this package into any project, or install the skills into
Claude Code, OpenCode, or Codex CLI — no LuckyD install required.
"""

from .approval import ApprovalEngine, Decision
from .harness import CircuitBreaker
from .trust import (
    SCOPES,
    AuditLog,
    TrustPolicy,
    describe_decision,
    get_audit_log,
    get_policy,
    host_of_url,
    redact_args,
    risk_of,
    scope_of,
)

__all__ = [
    "SCOPES",
    "AuditLog",
    "TrustPolicy",
    "ApprovalEngine",
    "CircuitBreaker",
    "Decision",
    "describe_decision",
    "get_audit_log",
    "get_policy",
    "host_of_url",
    "redact_args",
    "risk_of",
    "scope_of",
]

__version__ = "1.0.0"
