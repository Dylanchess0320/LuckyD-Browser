"""Lucky — the browser's own apprentice: identity, voice, and quiet initiative.

This module is deliberately Qt-free and LLM-free so every rule is a pure
function the test suite can pin:

* identity copy (name, one-liner, greeting per time-of-day)
* the chat system prompt (Lucky's voice: warm apprentice, never purple prose)
* suggest-only initiative: local signals in, at most ONE suggested next step
  out — Lucky never acts on its own, it only notices and proposes.

Phase 1 of "a mind of its own" is suggest-only by design: suggesting is safe
to get 100% right (no side effects), while acting needs the permission-scope
+ audit-log work that comes next. Nothing here touches the network, the DOM,
or settings — callers pass plain data in and render the result.
"""

from __future__ import annotations

NAME = "Lucky"
ROLE = "your apprentice inside LuckyD Browser"
ONE_LINER = "Lucky notices, proposes, and waits — it never acts on its own."

# Lucky's voice for the chat model: warm apprentice, concise, grounded.
# Kept short on purpose — long persona prompts drift into purple prose.
VOICE = (
    "You are Lucky, the user's apprentice inside LuckyD Browser — "
    "warm, a little playful, genuinely useful. "
    "Concise and factual; short paragraphs or bullets. "
    "When page context is provided, ground answers in it and say when the "
    "answer is not on the page. "
    "Format answers in GitHub-flavored Markdown: fenced code blocks for "
    "code/commands, **bold** for key terms, short bullet lists for steps. "
    "You NEVER act on your own — you notice, propose one next step, and wait."
)

_CHAT_BASE = (
    "When page context is provided, ground answers in it and say when the "
    "answer is not on the page. Use short paragraphs or bullets. "
    "Format answers in GitHub-flavored Markdown: fenced code blocks for "
    "code/commands, **bold** for key terms, short bullet lists for steps."
)


def system_prompt() -> str:
    """The chat system prompt with Lucky's voice (replaces the generic one)."""
    return VOICE


def greeting(hour: int) -> str:
    """Time-of-day greeting headline — pure function of the hour (0-23)."""
    try:
        h = int(hour) % 24
    except (TypeError, ValueError):
        h = 12
    if 5 <= h < 12:
        part = "Good morning"
    elif 12 <= h < 17:
        part = "Good afternoon"
    elif 17 <= h < 22:
        part = "Good evening"
    else:
        part = "Up late"
    return f"{part} — Lucky's ready"


def suggest(
    open_tabs: int = 0,
    downloads_done: int = 0,
    agent_finished: bool = False,
    update_pending: bool = False,
) -> str:
    """At most ONE suggest-only nudge from local signals. Empty = stay quiet.

    Priority: finished agent work > pending update > finished downloads >
    tab overload. Anything else (or nothing) returns "" — Lucky stays quiet
    rather than nagging. Suggest-only: the strings propose, never perform.
    """
    try:
        tabs = max(0, int(open_tabs))
    except (TypeError, ValueError):
        tabs = 0
    try:
        done = max(0, int(downloads_done))
    except (TypeError, ValueError):
        done = 0
    if bool(agent_finished):
        return "Your agent run just finished — want me to show the result?"
    if bool(update_pending):
        return "A browser update is waiting — want to review it? (click ⬆)"
    if done > 0:
        return "Your download finished — want me to open the folder?"
    if tabs >= 12:
        return f"{tabs} tabs open — want me to group the related ones?"
    return ""
