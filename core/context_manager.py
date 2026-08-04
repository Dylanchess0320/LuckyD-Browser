"""
Context manager — token counting, truncation, and compaction strategies.
Borrows patterns from Cline's session-compaction.ts with multiple strategies.
"""

from __future__ import annotations

from collections.abc import Callable

from .types import CompactionStrategy

# ── Token estimation ──────────────────────────────────────────────────

_CHARS_PER_TOKEN_EN = 4.0
_CHARS_PER_TOKEN_CODE = 3.5


def estimate_tokens(text: str, is_code: bool = False) -> int:
    """Estimate the number of tokens in a text string."""
    if not text:
        return 0
    cpt = _CHARS_PER_TOKEN_CODE if is_code else _CHARS_PER_TOKEN_EN
    return max(1, int(len(text) / cpt))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a list of messages."""
    total = 0
    for msg in messages:
        total += 4  # overhead
        content = msg.get("content", "") or ""
        total += estimate_tokens(content)
        for tc in msg.get("tool_calls", []):
            total += 6
            func = tc.get("function", {})
            total += estimate_tokens(func.get("name", ""))
            total += estimate_tokens(func.get("arguments", ""))
    return total


def truncate_messages(
    messages: list[dict],
    max_messages: int = 40,
    keep_recent: int = 20,
    max_tool_output_chars: int = 300,
    max_assistant_chars: int = 500,
) -> list[dict]:
    """Truncate old messages — keep system prompts + last N messages intact.
    Never splits a tool_calls/tool pair."""
    if len(messages) <= max_messages:
        keep_intact = 6
        for i in range(len(messages)):
            if i >= len(messages) - keep_intact:
                break
            msg = messages[i]
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content:
                continue
            if role == "tool":
                if len(content) > max_tool_output_chars:
                    msg["content"] = content[:max_tool_output_chars] + "\n... [truncated]"
            elif role == "assistant" and len(content) > max_assistant_chars:
                msg["content"] = content[:max_assistant_chars] + "\n... [truncated]"
        return messages

    # Keep only leading system messages (the actual prompt).
    # System messages can appear mid-history (memory refreshes, hints),
    # so counting all of them and slicing messages[:count] scrambles history.
    sys_end = 0
    while sys_end < len(messages) and messages[sys_end].get("role") == "system":
        sys_end += 1

    keep = min(keep_recent, len(messages) - sys_end)
    start_idx = len(messages) - keep

    # Never split a tool_calls/tool pair at the boundary
    while start_idx > sys_end and messages[start_idx].get("role") == "tool":
        start_idx -= 1
    if start_idx > sys_end:
        prev = messages[start_idx - 1]
        if prev.get("role") == "assistant" and prev.get("tool_calls"):
            start_idx -= 1

    return [
        *messages[:sys_end],
        {"role": "system", "content": "[Context truncated — keeping most recent messages only]"},
        *messages[start_idx:],
    ]


async def summarize_messages(
    messages: list[dict],
    max_messages: int = 40,
    keep_recent: int = 15,
    summarizer_fn: Callable[[str], str] | None = None,
) -> list[dict]:
    """Summarize older messages using an LLM call.
    Falls back to truncation if no summarizer_fn provided.
    Pattern from Cline: session-compaction.ts."""
    if len(messages) <= max_messages:
        return messages

    system_count = sum(1 for m in messages if m.get("role") == "system")
    keep = min(keep_recent, len(messages) - system_count)
    split_idx = len(messages) - keep

    while split_idx > 0 and messages[split_idx].get("role") == "tool":
        split_idx -= 1
        keep += 1
    if (
        split_idx > 0
        and messages[split_idx - 1].get("role") == "assistant"
        and messages[split_idx - 1].get("tool_calls")
    ):
        split_idx -= 1
        keep += 1

    to_summarize = messages[system_count:split_idx]
    to_keep = messages[split_idx:]

    if not to_summarize:
        return messages

    if summarizer_fn and len(to_summarize) > 2:
        transcript_lines = []
        for msg in to_summarize:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "system":
                continue
            if role == "tool":
                content = content[:200]
            elif role == "assistant":
                content = content[:300]
            if content.strip():
                transcript_lines.append(f"[{role}] {content[:500]}")
        transcript = "\n".join(transcript_lines[-30:])
        try:
            summary_text = await summarizer_fn(
                f"Summarize the following conversation turns, keeping key decisions, code changes, and context:\n\n{transcript}"
            )
            summary_msg = {
                "role": "system",
                "content": f"[Context summary of previous turns]\n{summary_text[:2000]}",
            }
            return [*messages[:system_count], summary_msg, *to_keep]
        except Exception:
            pass

    return truncate_messages(messages, max_messages, keep_recent)


class ContextManager:
    """Manages conversation context with configurable compaction strategy."""

    def __init__(
        self,
        strategy: CompactionStrategy = CompactionStrategy.TRUNCATE,
        max_messages: int = 40,
        keep_recent: int = 20,
        summarizer_fn: Callable[[str], str] | None = None,
        max_tool_result_chars: int = 2000,
    ):
        self.strategy = strategy
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.summarizer_fn = summarizer_fn
        self.max_tool_result_chars = max_tool_result_chars

    def set_strategy(self, strategy: CompactionStrategy):
        self.strategy = strategy

    async def compact(self, messages: list[dict]) -> list[dict]:
        """Compact messages according to the current strategy."""
        if self.strategy == CompactionStrategy.NONE:
            return messages
        elif self.strategy == CompactionStrategy.TRUNCATE:
            return truncate_messages(
                messages,
                self.max_messages,
                self.keep_recent,
                max_tool_output_chars=self.max_tool_result_chars,
            )
        elif self.strategy == CompactionStrategy.SUMMARIZE:
            return await summarize_messages(
                messages, self.max_messages, self.keep_recent, self.summarizer_fn
            )
        elif self.strategy == CompactionStrategy.HYBRID:
            if self.summarizer_fn:
                result = await summarize_messages(
                    messages, self.max_messages, self.keep_recent, self.summarizer_fn
                )
                if len(result) <= self.max_messages:
                    return result
            return truncate_messages(
                messages,
                self.max_messages,
                self.keep_recent,
                max_tool_output_chars=self.max_tool_result_chars,
            )
        elif self.strategy == CompactionStrategy.MICROCOMPACT:
            return await self._microcompact(messages)
        return messages

    async def _microcompact(self, messages: list[dict]) -> list[dict]:
        """Aggressive micro-compaction: shrink large tool results in older messages.

        Keeps the most recent ``keep_recent`` messages intact. For older messages,
        any ``tool`` role message whose content exceeds ``max_tool_result_chars``
        is replaced with a short placeholder noting the tool_call_id and original
        length. Non-tool messages are left alone. Falls back to truncation if the
        result is still over ``max_messages``.
        """
        result: list[dict] = []
        cutoff = max(0, len(messages) - self.keep_recent)
        for i, msg in enumerate(messages):
            if i >= cutoff or msg.get("role") != "tool":
                result.append(msg)
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) <= self.max_tool_result_chars:
                result.append(msg)
                continue
            call_id = msg.get("tool_call_id", "?")
            compacted = dict(msg)
            compacted["content"] = (
                f"[microcompact] tool result {call_id} elided "
                f"({len(content)} chars, originally returned successfully)"
            )
            result.append(compacted)
        if len(result) > self.max_messages:
            return truncate_messages(
                result,
                self.max_messages,
                self.keep_recent,
                max_tool_output_chars=self.max_tool_result_chars,
            )
        return result

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return estimate_tokens(text)

    @staticmethod
    def estimate_messages_tokens(messages: list[dict]) -> int:
        return estimate_messages_tokens(messages)
