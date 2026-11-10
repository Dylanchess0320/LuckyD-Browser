"""LuckyD Code side of the shared Hub handoff note (Hub slice 9).

The Browser owns the note (``browser/browser_core/hub_memory.py``:
``HandoffStore``, local JSON, exposed on ``GET/POST /chat/handoff``).
This module is the Code side's reader + writer against those same
endpoints — no second store, no new protocol (docs/TEAM_BRAIN.md fix).

The Hub base URL reaches the CLI as the ``LUCKYD_HUB_URL`` env var,
set by the terminal bridge (``browser_core/terminal_server.py``) from
the live control server; the default is the control server's default
port. Auth mirrors ``scripts/hub_growth_watch.py``: the control
server's bearer token, if one is configured, comes from
``LUCKYD_CTL_TOKEN``. It is never printed.

Fail-quiet like the rest of the Hub surface: the Hub may be down (the
CLI also runs standalone, with no browser at all), so ``read_latest``
returns ``None`` and ``write_note`` returns ``(False, reason)`` on any
failure instead of raising.
"""

from __future__ import annotations

import contextlib
import json
import os
import urllib.error
import urllib.request

#: Default Hub base URL — the Browser Control API's default port.
DEFAULT_HUB_URL = "http://127.0.0.1:9777"
#: Source tag for notes the Code layer writes (visible on the Chat page).
SOURCE = "code"
#: Loopback calls should never stall the REPL.
TIMEOUT_SEC = 5.0


def hub_url() -> str:
    """Hub base URL for this CLI process (env override, else default)."""
    return (os.environ.get("LUCKYD_HUB_URL", "") or "").strip() or DEFAULT_HUB_URL


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    token = (os.environ.get("LUCKYD_CTL_TOKEN", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_latest(hub: str | None = None, opener=None) -> dict | None:
    """Latest handoff entry (``{"ts", "source", "text"}``) or None.

    None means "no note yet" AND "Hub unreachable" — callers show the
    note when one comes back and stay silent otherwise. Never raises.
    """
    url = (hub or hub_url()).rstrip("/") + "/chat/handoff"
    open_fn = opener or urllib.request.urlopen
    try:
        req = urllib.request.Request(url, headers=_headers(), method="GET")
        with open_fn(req, timeout=TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    latest = body.get("latest")
    return latest if isinstance(latest, dict) else None


def write_note(
    text: str, hub: str | None = None, source: str = SOURCE, opener=None
) -> tuple[bool, str]:
    """Append one handoff note. Returns ``(True, "")`` or ``(False, reason)``.

    Never raises — a down Hub is a quiet no-op for the caller, not an
    error that should interrupt a coding session.
    """
    body_text = str(text or "").strip()
    if not body_text:
        return False, "text required"
    url = (hub or hub_url()).rstrip("/") + "/chat/handoff"
    payload = json.dumps({"source": source, "text": body_text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=_headers(), method="POST")
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = json.loads(exc.read().decode("utf-8", "replace") or "{}").get("error", "")
        return False, f"Hub rejected the note ({exc.code}): {detail or exc.reason}"
    except Exception as exc:
        return False, f"could not reach the Hub: {exc}"
    if not isinstance(body, dict) or body.get("state") != "ok":
        reason = body.get("error", body) if isinstance(body, dict) else body
        return False, f"Hub rejected the note: {reason}"
    return True, ""


def session_end_note(agent) -> str | None:
    """Session-end receipt text for the handoff note, or None.

    None when the session did nothing (zero turns) — opening and
    closing the CLI without working must not spam the shared log.
    Never raises; the agent shape is read defensively.
    """
    try:
        turns = int(getattr(agent, "turn_count", 0) or 0)
    except Exception:
        turns = 0
    if turns <= 0:
        return None
    try:
        project = ""
        info = getattr(agent, "_project_info", None)
        if info is not None and not info.is_empty():
            project = str(info.name or "")
    except Exception:
        project = ""
    try:
        model = str(getattr(agent, "model", "") or "")
    except Exception:
        model = ""
    noun = "turn" if turns == 1 else "turns"
    return f"Code session ended - {project or 'no project'} - {turns} {noun}" + (
        f", {model}" if model else ""
    )
