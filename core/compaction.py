"""Context compaction for long conversations (LuckyD 9.7).

When a conversation grows past a heuristic threshold, the stale middle of
the history is summarized and replaced with a single system message carrying
the summary. The first system prompt and the most recent turns are kept
verbatim so the agent never loses its instructions or current context.

Adapted from the minimax-code compaction idea; native implementation, no
new dependencies.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Compact once the agent has run this many turns in one run() call.
COMPACT_TURN_THRESHOLD = 30
#: ... or once the message list has grown this long.
COMPACT_MESSAGE_THRESHOLD = 60

SUMMARY_PREFIX = "CONVERSATION SUMMARY SO FAR:\n"

SUMMARIZATION_PROMPT = """You are summarizing a coding-agent conversation so it can continue
with a fresh context window. Read the transcript below and write a compact
but complete summary covering:

- The user's original goal and any follow-up requests
- Key decisions made and why
- Files created, modified, or deleted (with paths when mentioned)
- Tool results that matter for continuing (errors hit, fixes applied)
- Anything still unfinished or explicitly deferred

Write the summary as concise bullet points. Do not add advice or commentary
beyond what happened in the transcript."""


def should_compact(
    agent: Any,
    max_turns: int = COMPACT_TURN_THRESHOLD,
    max_messages: int = COMPACT_MESSAGE_THRESHOLD,
) -> bool:
    """Heuristic: compact when the turn count or the message count is too high."""
    try:
        if int(getattr(agent, "turn_count", 0) or 0) >= max_turns:
            return True
        messages = getattr(agent, "messages", None) or []
        return len(messages) >= max_messages
    except Exception:
        return False


async def compact(agent: Any, keep_recent_turns: int = 6, summarizer=None) -> bool:
    """Summarize stale history and splice the summary back into agent.messages.

    Keeps the first system message and the last ``keep_recent_turns``
    messages verbatim; everything in between is summarized via ``summarizer``
    (a sync or async callable taking the middle messages) or, when omitted,
    via the agent's own LLM client (non-streaming chat). On any failure the
    message list is left untouched and False is returned.
    """
    try:
        messages = list(getattr(agent, "messages", None) or [])
    except Exception:
        return False
    if len(messages) <= keep_recent_turns + 1:
        return False  # nothing worth summarizing

    system_msg = messages[0]
    if keep_recent_turns > 0:
        recent = messages[len(messages) - keep_recent_turns :]
        middle = messages[1 : len(messages) - keep_recent_turns]
    else:
        recent = []
        middle = messages[1:]
    if not middle:
        return False

    try:
        if summarizer is not None:
            summary = summarizer(middle)
            if inspect.isawaitable(summary):
                summary = await summary
        else:
            summary = await _summarize_via_llm(agent, middle)
        summary = (summary or "").strip()
        if not summary:
            return False
    except Exception as exc:
        log.warning("context compaction failed during summarization: %s", exc)
        return False

    agent.messages = [
        system_msg,
        {"role": "system", "content": SUMMARY_PREFIX + summary},
        *recent,
    ]
    return True


async def _summarize_via_llm(agent: Any, middle: list[dict]) -> str:
    """Summarize ``middle`` with the agent's own LLM client (non-streaming)."""
    lines: list[str] = []
    for m in middle:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        if role == "tool":
            content = content[:300]
        elif role == "assistant":
            content = content[:800]
        content = content.strip()
        if content:
            lines.append(f"[{role}] {content}")
    transcript = "\n".join(lines)
    result = await agent.llm_client.chat_nonstreaming(
        messages=[
            {"role": "system", "content": SUMMARIZATION_PROMPT},
            {
                "role": "user",
                "content": f"Conversation transcript:\n\n{transcript}\n\nWrite the summary:",
            },
        ]
    )
    if not result:
        return ""
    return (result.get("content") or "").strip()


async def maybe_compact(agent: Any, **kwargs: Any) -> bool:
    """Check should_compact() and compact() when needed. Never raises."""
    try:
        if should_compact(agent):
            return await compact(agent, **kwargs)
        return False
    except Exception as exc:
        log.warning("maybe_compact failed: %s", exc)
        return False
