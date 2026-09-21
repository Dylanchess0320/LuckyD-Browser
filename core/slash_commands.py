"""
Slash commands defined as markdown files in the repo-root ``slash_commands/`` dir.

Each ``*.md`` file is one command: the command name is the file stem
(``compact.md`` -> ``/compact``), the optional first line ``# /name - description``
provides the help description, and the body is the prompt template with
optional ``{args}`` placeholder support.

``handle_slash`` never calls ``agent.run`` itself: it returns text for the REPL
to display or feed back into the agent (the REPL wiring lives in main.py).
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

COMMANDS_DIR = Path(__file__).resolve().parent.parent / "slash_commands"

# Built-in commands that don't need a .md file (or augment one).
BUILTIN_COMMANDS: dict[str, str] = {
    "help": "List all available slash commands",
    "compact": "Summarize the conversation so far to reclaim context",
    "resume": "Restore a saved session by id: /resume <id>",
    "init": "Generate project guidance for this repo",
    "review": "Review recent changes and give a verdict",
    "goal": "Set/show the active objective: /goal <text> | /goal budget=50K | /goal pause|resume|edit|clear|status",
    "steer": "Guide the current response mid-run: /steer <guidance>",
    "btw": "Queue a follow-up after the current response: /btw <text>",
}


def _parse_header(first_line: str, fallback_name: str) -> tuple[str, str]:
    """Parse ``# /name - description`` (or ``# name - description``) headers."""
    name = fallback_name
    description = ""
    header = first_line.lstrip("#").strip()
    if " - " in header:
        raw_name, description = header.split(" - ", 1)
    else:
        raw_name, description = header, ""
    raw_name = raw_name.strip()
    if raw_name.startswith("/"):
        raw_name = raw_name[1:]
    if raw_name:
        name = raw_name
    return name, description.strip()


def discover_commands() -> dict[str, dict[str, str]]:
    """Scan ``COMMANDS_DIR`` for ``*.md`` files and return name -> command dicts."""
    commands: dict[str, dict[str, str]] = {}
    if not COMMANDS_DIR.is_dir():
        return commands
    for path in sorted(COMMANDS_DIR.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        description = ""
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("#"):
            _, description = _parse_header(lines[0], path.stem)
        commands[path.stem] = {
            "name": path.stem,
            "description": description,
            "template": text,
        }
    return commands


def list_commands() -> list[tuple[str, str]]:
    """Return [(name, description)] for built-ins plus discovered .md commands."""
    discovered = discover_commands()
    result: list[tuple[str, str]] = []
    for name, description in BUILTIN_COMMANDS.items():
        if name in discovered and discovered[name]["description"]:
            description = discovered[name]["description"]
        result.append((name, description))
    for name in sorted(discovered):
        if name not in BUILTIN_COMMANDS:
            result.append((name, discovered[name]["description"]))
    return result


def _command_template(name: str, fallback: str) -> str:
    """Return the discovered template for *name*, or *fallback* if the .md is missing."""
    command = discover_commands().get(name)
    if command:
        return command["template"]
    return fallback


def _handle_help() -> str:
    lines = ["Available slash commands:"]
    for name, description in list_commands():
        suffix = f" — {description}" if description else ""
        lines.append(f"  /{name}{suffix}")
    return "\n".join(lines)


def _handle_compact(agent: Any) -> str:
    try:
        from core import compaction
    except Exception as e:
        return f"Compaction is unavailable: could not import core.compaction ({e})."
    maybe_compact = getattr(compaction, "maybe_compact", None)
    if maybe_compact is None:
        return "Compaction is unavailable: core.compaction has no maybe_compact()."
    try:
        pending = maybe_compact(agent)
        if inspect.iscoroutine(pending):
            # maybe_compact is async. The interactive REPL (main.py) awaits it
            # directly; here we can only drive it when no loop is running.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                compacted = asyncio.run(pending)
            else:
                return "COMPACT_DEFERRED"
        else:
            compacted = pending
    except Exception as e:
        return f"Compaction failed: {e}."
    return "Compaction complete." if compacted else "No compaction needed yet."


def _handle_resume(agent: Any, resume_id: str) -> str:
    if not resume_id:
        return "Usage: /resume <session-id>"
    restore = getattr(agent, "restore_session", None)
    if restore is None:
        return "/resume is not supported by this agent (no restore_session)."
    try:
        restore(resume_id)
    except Exception as e:
        return f"Could not restore session '{resume_id}': {e}."
    return f"Restored session '{resume_id}'."


def _handle_goal(agent: Any, args: str) -> str:
    """Route /goal to the agent's GoalStore (honest when agent lacks one)."""
    store = getattr(agent, "goals", None)
    if store is None or not hasattr(store, "handle_command"):
        return "/goal is not supported by this agent (no goal store)."
    try:
        return str(store.handle_command(args or ""))
    except Exception as e:
        return f"Could not update goal: {e}."


def _handle_steer(agent: Any, args: str) -> str:
    """Route /steer to agent.steer() — mid-run guidance for this response."""
    fn = getattr(agent, "steer", None)
    if fn is None:
        return "/steer is not supported by this agent."
    if not (args or "").strip():
        return "Usage: /steer <guidance>"
    try:
        return str(fn(args))
    except Exception as e:
        return f"Could not steer: {e}."


def _handle_btw(agent: Any, args: str) -> str:
    """Route /btw to agent.queue() — deferred follow-up after this response."""
    fn = getattr(agent, "queue", None)
    if fn is None:
        return "/btw is not supported by this agent."
    if not (args or "").strip():
        return "Usage: /btw <follow-up text>"
    try:
        return str(fn(args))
    except Exception as e:
        return f"Could not queue follow-up: {e}."


def handle_slash(text: str, agent: Any = None) -> tuple[bool, str | None]:
    """Handle a ``/command args`` line.

    Returns ``(handled, response)`` where *response* is text for the REPL to
    display or feed back into the agent. Never calls ``agent.run``.
    """
    if not text or not text.strip().startswith("/"):
        return False, None

    stripped = text.strip()[1:]
    parts = stripped.split(None, 1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command == "help":
        return True, _handle_help()
    if command == "compact":
        return True, _handle_compact(agent)
    if command == "resume":
        return True, _handle_resume(agent, args.strip())
    if command == "goal":
        return True, _handle_goal(agent, args)
    if command == "steer":
        return True, _handle_steer(agent, args)
    if command == "btw":
        return True, _handle_btw(agent, args)
    if command in ("init", "review"):
        fallback = (
            f"(No {command}.md template found.)\n\n/{command} args: {args}"
            if args
            else f"(No {command}.md template found.)"
        )
        return True, _command_template(command, fallback).replace("{args}", args)

    discovered = discover_commands()
    if command in discovered:
        template = discovered[command]["template"].replace("{args}", args)
        note = f"\n\n_Follow the prompt template above to handle this /{command} request._"
        return True, template + note

    return True, "Unknown command. Try /help."
