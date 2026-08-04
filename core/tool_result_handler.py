"""
Advanced tool result handler — continuation, truncation, and backpressure.

Implements three-stage tool result processing:
  1. CONTINUATION — multi-part results (file chunks, paged output)
  2. TRUNCATION — smart semantic truncation with diff-aware compression
  3. BACKPRESSURE — rate limiting and result size capping

Borrows patterns from:
  - LangChain's tool result chunking
  - Cline's file-read continuation
  - Anthropic's tool result truncation spec
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from core.types import ToolResult

# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────


class ContinuationStrategy(Enum):
    """How to handle multi-part tool results."""

    NONE = auto()  # Single-shot, no continuation
    CHUNKED = auto()  # Fixed-size chunks with offset/limit
    CURSOR = auto()  # Cursor-based pagination (e.g., LSP)
    STREAM = auto()  # Streaming with backpressure
    ADAPTIVE = auto()  # Auto-select based on result size


class TruncationStrategy(Enum):
    """How to truncate oversized tool results."""

    NONE = auto()  # No truncation (pass through)
    HEAD = auto()  # Keep first N lines/chars
    TAIL = auto()  # Keep last N lines/chars
    HEAD_TAIL = auto()  # Keep first + last with middle elided
    SEMANTIC = auto()  # AST-aware / semantic chunking
    DIFF = auto()  # Diff-aware compression (keep changed regions)


class BackpressureStrategy(Enum):
    """How to apply backpressure to tool results."""

    NONE = auto()  # No backpressure
    RATE_LIMIT = auto()  # Max results per second
    SIZE_CAP = auto()  # Max total result size
    TOKEN_BUDGET = auto()  # Token-based budgeting
    ADAPTIVE = auto()  # Auto-adjust based on context pressure


@dataclass
class ContinuationState:
    """State for a multi-part tool result."""

    tool_name: str
    call_id: str
    strategy: ContinuationStrategy
    total_parts: int = 0
    current_part: int = 0
    offset: int = 0
    limit: int = 0
    cursor: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)


@dataclass
class TruncationResult:
    """Result of truncation operation."""

    content: str
    was_truncated: bool
    original_size: int
    truncated_size: int
    strategy: TruncationStrategy
    elided_ranges: list[tuple[int, int]] = field(default_factory=list)
    continuation_hint: str = ""


@dataclass
class BackpressureContext:
    """Context for backpressure decisions."""

    current_result_size: int
    total_session_size: int
    remaining_budget: int
    recent_results: list[int]  # sizes of last N results
    context_pressure: float  # 0.0 (low) to 1.0 (high)


# ──────────────────────────────────────────────────────────────────────────────
# Truncator
# ──────────────────────────────────────────────────────────────────────────────


class ToolResultTruncator:
    """Smart truncation with multiple strategies."""

    def __init__(
        self,
        max_output_chars: int = 4000,
        semantic_threshold: int = 2000,
        diff_aware: bool = True,
    ):
        self.max_output_chars = max_output_chars
        self.semantic_threshold = semantic_threshold
        self.diff_aware = diff_aware

    def truncate(
        self,
        content: str,
        strategy: TruncationStrategy | None = None,
        max_chars: int | None = None,
        tool_name: str = "",
    ) -> TruncationResult:
        """Truncate content using the best strategy for the tool."""
        max_chars = max_chars or self.max_output_chars
        original_size = len(content)

        if original_size <= max_chars:
            return TruncationResult(
                content=content,
                was_truncated=False,
                original_size=original_size,
                truncated_size=original_size,
                strategy=TruncationStrategy.NONE,
            )

        # Auto-select strategy if not specified
        if strategy is None:
            strategy = self._select_strategy(tool_name, content, max_chars)

        if strategy == TruncationStrategy.SEMANTIC:
            return self._truncate_semantic(content, max_chars)
        elif strategy == TruncationStrategy.DIFF:
            return self._truncate_diff(content, max_chars)
        elif strategy == TruncationStrategy.HEAD_TAIL:
            return self._truncate_head_tail(content, max_chars)
        elif strategy == TruncationStrategy.TAIL:
            return self._truncate_tail(content, max_chars)
        else:  # HEAD
            return self._truncate_head(content, max_chars)

    def _select_strategy(self, tool_name: str, content: str, max_chars: int) -> TruncationStrategy:
        """Auto-select truncation strategy based on tool and content."""
        # File operations: semantic if code, head_tail if logs
        if tool_name in ("Read", "Write", "Edit"):
            if self._looks_like_code(content):
                return TruncationStrategy.SEMANTIC
            return TruncationStrategy.HEAD_TAIL

        # Shell commands: head_tail (keep command + output start/end)
        if tool_name in ("Bash", "PowerShell", "Shell"):
            return TruncationStrategy.HEAD_TAIL

        # Git operations: diff-aware
        if tool_name.startswith("Git"):
            return TruncationStrategy.DIFF if self.diff_aware else TruncationStrategy.HEAD_TAIL

        # Search results: head (most relevant first)
        if tool_name in ("Grep", "Glob", "WebSearch"):
            return TruncationStrategy.HEAD

        # Default: head_tail for balanced context
        return TruncationStrategy.HEAD_TAIL

    def _looks_like_code(self, content: str) -> bool:
        """Heuristic: does content look like source code?"""
        code_indicators = [
            r"^\s*(def|class|import|from|if|for|while|return)\s",  # Python
            r"^\s*(function|const|let|var|import|export)\s",  # JavaScript
            r"^\s*(public|private|protected|class|interface)\s",  # Java/C#
            r"[{}();]\s*$",  # Brackets at end of lines
        ]
        lines = content.split("\n")[:50]  # Check first 50 lines
        code_lines = 0
        for line in lines:
            if any(re.search(p, line) for p in code_indicators):
                code_lines += 1
        return code_lines > len(lines) * 0.3

    def _truncate_head(self, content: str, max_chars: int) -> TruncationResult:
        """Keep first N chars."""
        truncated = content[:max_chars]
        return TruncationResult(
            content=truncated + f"\n... [truncated {len(content) - max_chars} chars]",
            was_truncated=True,
            original_size=len(content),
            truncated_size=len(truncated),
            strategy=TruncationStrategy.HEAD,
            continuation_hint=f"Use offset={max_chars} to continue reading",
        )

    def _truncate_tail(self, content: str, max_chars: int) -> TruncationResult:
        """Keep last N chars."""
        truncated = content[-max_chars:]
        return TruncationResult(
            content=f"[truncated {len(content) - max_chars} chars from start]\n" + truncated,
            was_truncated=True,
            original_size=len(content),
            truncated_size=len(truncated),
            strategy=TruncationStrategy.TAIL,
        )

    def _truncate_head_tail(self, content: str, max_chars: int) -> TruncationResult:
        """Keep first and last portions, elide middle."""
        head_size = max_chars // 2
        tail_size = max_chars - head_size - 100  # Reserve for elision marker

        head = content[:head_size]
        tail = content[-tail_size:] if tail_size > 0 else ""

        elision = f"\n\n... [middle {len(content) - head_size - tail_size} chars elided] ...\n\n"

        return TruncationResult(
            content=head + elision + tail,
            was_truncated=True,
            original_size=len(content),
            truncated_size=len(head) + len(elision) + len(tail),
            strategy=TruncationStrategy.HEAD_TAIL,
            elided_ranges=[(head_size, len(content) - tail_size)],
            continuation_hint=f"Use offset={head_size} to read elided section",
        )

    def _truncate_semantic(self, content: str, max_chars: int) -> TruncationResult:
        """AST-aware truncation — keep complete functions/classes."""
        lines = content.split("\n")
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_size = 0

        # Group by top-level definitions
        for line in lines:
            line_size = len(line) + 1  # +1 for newline
            is_definition = bool(
                re.match(r"^\s*(def |class |async def |function |interface |type )", line)
            )

            if is_definition and current_chunk and current_size > max_chars * 0.7:
                # Save current chunk if it's getting big
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_size = line_size
            else:
                current_chunk.append(line)
                current_size += line_size

            if current_size > max_chars:
                break

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        truncated = "\n\n".join(chunks)
        if len(truncated) < len(content):
            truncated += (
                f"\n\n... [semantic truncation: {len(content) - len(truncated)} chars elided]"
            )

        return TruncationResult(
            content=truncated,
            was_truncated=True,
            original_size=len(content),
            truncated_size=len(truncated),
            strategy=TruncationStrategy.SEMANTIC,
            continuation_hint="Semantic truncation preserved complete definitions",
        )

    def _truncate_diff(self, content: str, max_chars: int) -> TruncationResult:
        """Diff-aware truncation — keep changed regions, elide unchanged."""
        lines = content.split("\n")
        result_lines: list[str] = []
        current_size = 0
        in_hunk = False
        hunk_buffer: list[str] = []

        for line in lines:
            line_size = len(line) + 1

            # Detect diff hunks
            if line.startswith("@@"):
                if (
                    hunk_buffer
                    and current_size + sum(len(line) + 1 for line in hunk_buffer) > max_chars
                ):
                    # Flush buffer if it fits
                    result_lines.extend(hunk_buffer)
                    current_size += sum(len(line) + 1 for line in hunk_buffer)
                hunk_buffer = [line]
                in_hunk = True
            elif in_hunk and line.startswith(("+", "-", " ")):
                hunk_buffer.append(line)
            else:
                if hunk_buffer:
                    # Flush hunk
                    hunk_text = "\n".join(hunk_buffer)
                    if current_size + len(hunk_text) <= max_chars:
                        result_lines.extend(hunk_buffer)
                        current_size += len(hunk_text)
                    else:
                        # Truncate hunk
                        remaining = max_chars - current_size
                        if remaining > 200:
                            partial = hunk_text[:remaining]
                            result_lines.append(partial + "\n... [hunk truncated]")
                        current_size = max_chars
                    hunk_buffer = []
                in_hunk = False

                if current_size + line_size <= max_chars:
                    result_lines.append(line)
                    current_size += line_size

            if current_size >= max_chars:
                break

        # Flush remaining buffer
        if hunk_buffer and current_size < max_chars:
            hunk_text = "\n".join(hunk_buffer)
            remaining = max_chars - current_size
            if len(hunk_text) <= remaining:
                result_lines.extend(hunk_buffer)
            else:
                result_lines.append(hunk_text[:remaining] + "\n... [hunk truncated]")

        truncated = "\n".join(result_lines)
        return TruncationResult(
            content=truncated,
            was_truncated=True,
            original_size=len(content),
            truncated_size=len(truncated),
            strategy=TruncationStrategy.DIFF,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Continuation Manager
# ──────────────────────────────────────────────────────────────────────────────


class ContinuationManager:
    """Manages multi-part tool result continuation."""

    def __init__(self, max_continuations: int = 10, chunk_size: int = 4000):
        self.max_continuations = max_continuations
        self.chunk_size = chunk_size
        self._states: dict[str, ContinuationState] = {}
        self._callbacks: dict[str, Callable[[str, int, int], ToolResult]] = {}

    def register_continuation(
        self,
        call_id: str,
        tool_name: str,
        strategy: ContinuationStrategy,
        total_size: int,
        callback: Callable[[str, int, int], ToolResult] | None = None,
    ) -> ContinuationState:
        """Register a new continuation session."""
        state = ContinuationState(
            tool_name=tool_name,
            call_id=call_id,
            strategy=strategy,
            total_parts=(total_size + self.chunk_size - 1) // self.chunk_size,
            limit=self.chunk_size,
        )
        self._states[call_id] = state
        if callback:
            self._callbacks[call_id] = callback
        return state

    def get_continuation(self, call_id: str) -> ContinuationState | None:
        """Get continuation state for a call."""
        return self._states.get(call_id)

    def advance(self, call_id: str) -> ContinuationState | None:
        """Advance to next part. Returns None if no more parts."""
        state = self._states.get(call_id)
        if not state:
            return None

        state.current_part += 1
        state.offset += state.limit
        state.last_accessed = time.time()

        if state.current_part >= state.total_parts:
            # Cleanup
            self._states.pop(call_id, None)
            self._callbacks.pop(call_id, None)
            return None

        return state

    def get_next_chunk(self, call_id: str) -> tuple[str, bool] | None:
        """Get next chunk of content. Returns (content, has_more)."""
        state = self._states.get(call_id)
        if not state:
            return None

        callback = self._callbacks.get(call_id)
        if not callback:
            return None

        result = callback(state.tool_name, state.offset, state.limit)
        has_more = state.current_part + 1 < state.total_parts

        return result.text, has_more

    def cleanup_expired(self, max_age_sec: float = 300.0):
        """Remove expired continuation states."""
        now = time.time()
        expired = [
            cid for cid, state in self._states.items() if now - state.last_accessed > max_age_sec
        ]
        for cid in expired:
            self._states.pop(cid, None)
            self._callbacks.pop(cid, None)


# ──────────────────────────────────────────────────────────────────────────────
# Backpressure Controller
# ──────────────────────────────────────────────────────────────────────────────


class BackpressureController:
    """Controls tool result flow to prevent context overflow."""

    def __init__(
        self,
        max_session_chars: int = 100000,
        max_result_chars: int = 4000,
        rate_limit_per_sec: float = 10.0,
        token_budget: int = 8192,
    ):
        self.max_session_chars = max_session_chars
        self.max_result_chars = max_result_chars
        self.rate_limit_per_sec = rate_limit_per_sec
        self.token_budget = token_budget

        self._session_chars = 0
        self._last_result_time = 0.0
        self._result_sizes: list[int] = []
        self._max_recent = 10

    def check(
        self, result_size: int, strategy: BackpressureStrategy = BackpressureStrategy.ADAPTIVE
    ) -> tuple[bool, str]:
        """Check if result should be allowed. Returns (allowed, reason)."""
        now = time.time()

        # Rate limiting
        if strategy in (BackpressureStrategy.RATE_LIMIT, BackpressureStrategy.ADAPTIVE):
            min_interval = 1.0 / self.rate_limit_per_sec
            if now - self._last_result_time < min_interval:
                return False, f"Rate limited: {self.rate_limit_per_sec}/s max"

        # Size cap
        if (
            strategy in (BackpressureStrategy.SIZE_CAP, BackpressureStrategy.ADAPTIVE)
            and self._session_chars + result_size > self.max_session_chars
        ):
            return False, f"Session size cap reached ({self.max_session_chars} chars)"

        # Token budget
        if strategy in (BackpressureStrategy.TOKEN_BUDGET, BackpressureStrategy.ADAPTIVE):
            estimated_tokens = result_size // 4  # Rough estimate
            if estimated_tokens > self.token_budget // 10:  # Don't use >10% of budget on one result
                return False, f"Result too large for token budget ({estimated_tokens} tokens)"

        # Adaptive: reduce limit if recent results are large
        if strategy == BackpressureStrategy.ADAPTIVE and self._result_sizes:
            avg_recent = sum(self._result_sizes) / len(self._result_sizes)
            # Recent results are large — be more conservative
            if (
                avg_recent > self.max_result_chars * 0.8
                and result_size > self.max_result_chars * 0.5
            ):
                return False, "Adaptive backpressure: recent results large, truncating"

        return True, ""

    def record(self, result_size: int):
        """Record a processed result."""
        self._session_chars += result_size
        self._last_result_time = time.time()
        self._result_sizes.append(result_size)
        if len(self._result_sizes) > self._max_recent:
            self._result_sizes.pop(0)

    def get_pressure(self) -> float:
        """Get current backpressure level (0.0 to 1.0)."""
        size_pressure = self._session_chars / self.max_session_chars
        recent_pressure = (
            sum(self._result_sizes[-5:]) / (self.max_result_chars * 5) if self._result_sizes else 0
        )
        return min(1.0, (size_pressure + recent_pressure) / 2)

    def reset(self):
        """Reset session state."""
        self._session_chars = 0
        self._result_sizes.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Main Handler
# ──────────────────────────────────────────────────────────────────────────────


class ToolResultHandler:
    """Unified tool result processing pipeline."""

    def __init__(
        self,
        max_output_chars: int = 4000,
        max_session_chars: int = 100000,
        enable_continuation: bool = True,
        enable_truncation: bool = True,
        enable_backpressure: bool = True,
    ):
        self.truncator = ToolResultTruncator(max_output_chars=max_output_chars)
        self.continuation = ContinuationManager(chunk_size=max_output_chars)
        self.backpressure = BackpressureController(
            max_session_chars=max_session_chars,
            max_result_chars=max_output_chars,
        )
        self.enable_continuation = enable_continuation
        self.enable_truncation = enable_truncation
        self.enable_backpressure = enable_backpressure

    def process(
        self,
        result: ToolResult,
        tool_name: str,
        call_id: str,
        max_chars: int | None = None,
    ) -> ToolResult:
        """Process a tool result through the full pipeline."""
        content = result.text
        max_chars = max_chars or self.truncator.max_output_chars

        # Stage 1: Backpressure check
        if self.enable_backpressure:
            allowed, reason = self.backpressure.check(len(content))
            if not allowed:
                # Force truncation
                content = content[: max_chars // 2]
                result.text = f"[BACKPRESSURE: {reason}]\n{content}"
                result.error = True

        # Stage 2: Truncation
        if self.enable_truncation and len(content) > max_chars:
            truncation = self.truncator.truncate(content, tool_name=tool_name, max_chars=max_chars)
            content = truncation.content
            if truncation.continuation_hint and self.enable_continuation:
                # Register continuation
                self.continuation.register_continuation(
                    call_id=call_id,
                    tool_name=tool_name,
                    strategy=ContinuationStrategy.CHUNKED,
                    total_size=truncation.original_size,
                )

        # Stage 3: Record for backpressure
        if self.enable_backpressure:
            self.backpressure.record(len(content))

        result.text = content
        return result

    def get_continuation_prompt(self, call_id: str) -> str | None:
        """Get prompt for continuing a multi-part result."""
        state = self.continuation.get_continuation(call_id)
        if not state:
            return None

        return (
            f"Continue reading {state.tool_name} result "
            f"(part {state.current_part + 1}/{state.total_parts}, "
            f"offset={state.offset}, limit={state.limit})"
        )

    def advance_continuation(self, call_id: str) -> ToolResult | None:
        """Advance to next part of a continued result."""
        chunk = self.continuation.get_next_chunk(call_id)
        if chunk is None:
            return None

        content, has_more = chunk
        return ToolResult(
            text=content,
            error=False,
            metadata={"has_more": has_more, "call_id": call_id},
        )

    def cleanup(self):
        """Cleanup expired states."""
        self.continuation.cleanup_expired()

    def get_stats(self) -> dict:
        """Get handler statistics."""
        return {
            "backpressure": {
                "session_chars": self.backpressure._session_chars,
                "pressure": self.backpressure.get_pressure(),
            },
            "continuation": {
                "active": len(self.continuation._states),
                "max_continuations": self.continuation.max_continuations,
            },
            "truncation": {
                "max_output_chars": self.truncator.max_output_chars,
            },
        }


# ──────────────────────────────────────────────────────────────────────────────
# Global instance
# ──────────────────────────────────────────────────────────────────────────────

_handler: ToolResultHandler | None = None


def get_tool_result_handler(**kwargs) -> ToolResultHandler:
    """Get or create the global tool result handler."""
    global _handler
    if _handler is None:
        _handler = ToolResultHandler(**kwargs)
    return _handler


def reset_tool_result_handler():
    """Reset the global handler (for testing)."""
    global _handler
    _handler = None
