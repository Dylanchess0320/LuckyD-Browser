"""Client for Dylan's own PC services: the Lucky Hub (chat) and Claw (tasks).

The LuckyD Chat page never talks to these services directly — the
browser's control server proxies through this module, so the tokens
(Settings → Connections) stay server-side and never reach page JS.

Every function returns a plain dict and NEVER raises. Failures collapse
to a ``state`` the UI renders as one quiet line (Dylan's failures-quiet
rule): ``ok`` | ``no_token`` | ``bad_token`` | ``unreachable`` | ``error``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

#: Defaults match the services on Dylan's PC (Tailscale IP). Both are
#: editable in Settings → Connections in case the addresses ever change.
DEFAULT_HUB_URL = "http://100.96.167.54:18790"
DEFAULT_CLAW_URL = "http://100.96.167.54:18791"

#: Profiles the Chat page offers (Claw also accepts "full"; the page
#: deliberately offers the two everyday ones).
TASK_PROFILES = ("look", "build")

_QUICK_TIMEOUT = 8
_CHAT_TIMEOUT = 180

#: Short timeout for the /chat/state status probes. Two sequential probes
#: at this budget keep the status endpoint under ~1s even when a service
#: black-holes instead of refusing the connection; healthy local services
#: answer in milliseconds and refused connections fail instantly.
STATUS_TIMEOUT = 0.4

#: Last time (epoch seconds) each component answered a probe with "ok".
#: Lives in-process in the browser — the Hub status surface reads it so
#: an unreachable component can still show when it was last seen alive.
_LAST_SEEN: dict[str, float] = {}


def _note_seen(name: str) -> float:
    now = time.time()
    _LAST_SEEN[name] = now
    return now


def last_seen(name: str):
    """Epoch seconds the named component was last seen ok, or None."""
    return _LAST_SEEN.get(name)


def _base(settings, key: str, default: str) -> str:
    try:
        url = str(settings.get(key, "") or "").strip() if settings is not None else ""
    except Exception:
        url = ""
    return (url or default).rstrip("/")


def _token(settings, key: str) -> str:
    try:
        return str(settings.get(key, "") or "").strip() if settings is not None else ""
    except Exception:
        return ""


def hub_base(settings) -> str:
    return _base(settings, "hub_url", DEFAULT_HUB_URL)


def claw_base(settings) -> str:
    return _base(settings, "claw_url", DEFAULT_CLAW_URL)


def _request(url, *, method="GET", payload=None, timeout=_QUICK_TIMEOUT, opener=None):
    """One HTTP call -> (status, obj). status None = transport failure."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except ValueError:
                return resp.status, {}
    except urllib.error.HTTPError as exc:
        try:
            obj = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:
            obj = {}
        return exc.code, obj
    except Exception:
        return None, None


def _service_state(status, obj) -> str:
    if status is None:
        return "unreachable"
    if status == 403:
        return "bad_token"
    if status != 200 or not isinstance(obj, dict):
        return "error"
    return "ok"


def _get_authed(base: str, path: str, token: str, opener=None, timeout=_QUICK_TIMEOUT):
    sep = "&" if "?" in path else "?"
    url = f"{base}{path}{sep}token={urllib.parse.quote(token, safe='')}"
    return _request(url, opener=opener, timeout=timeout)


# ── Hub ────────────────────────────────────────────────────────────────


def hub_state(settings, opener=None, timeout=_QUICK_TIMEOUT) -> dict:
    """Hub liveness for the Chat page's one-line status."""
    token = _token(settings, "hub_token")
    if not token:
        return {"state": "no_token", "last_seen": _LAST_SEEN.get("hub")}
    status, obj = _get_authed(hub_base(settings), "/healthz", token, opener, timeout)
    state = _service_state(status, obj)
    if state != "ok":
        return {"state": state, "last_seen": _LAST_SEEN.get("hub")}
    return {"state": "ok", "ollama": bool(obj.get("ollama")), "last_seen": _note_seen("hub")}


def hub_chat(settings, messages, opener=None, provider=None) -> dict:
    """Send the conversation to the Hub's /chat route (stateless: the
    caller passes the full message list, the Hub routes local-first).

    ``provider`` is the pinned provider name (slice 2): when set and not
    "auto" it is passed through in the payload so the Hub answers with
    the provider Dylan picked; the Hub is free to ignore an unknown
    field, so an older Hub keeps working unchanged.
    """
    token = _token(settings, "hub_token")
    if not token:
        return {"state": "no_token"}
    payload: dict = {"token": token, "messages": messages}
    if provider and str(provider).strip() not in ("", "auto"):
        payload["provider"] = str(provider).strip()
    status, obj = _request(
        f"{hub_base(settings)}/chat",
        method="POST",
        payload=payload,
        timeout=_CHAT_TIMEOUT,
        opener=opener,
    )
    state = _service_state(status, obj)
    if state != "ok":
        return {"state": state}
    if not obj.get("ok"):
        return {"state": "error", "error": str(obj.get("error") or "chat failed")}
    return {
        "state": "ok",
        "text": str(obj.get("text") or ""),
        "via": str(obj.get("via") or ""),
        "model": str(obj.get("model") or ""),
    }


# ── Claw ───────────────────────────────────────────────────────────────


def claw_state(settings, opener=None, timeout=_QUICK_TIMEOUT) -> dict:
    """Claw liveness + the project allowlist (from /healthz)."""
    token = _token(settings, "claw_token")
    if not token:
        return {"state": "no_token", "last_seen": _LAST_SEEN.get("claw")}
    status, obj = _get_authed(claw_base(settings), "/healthz", token, opener, timeout)
    state = _service_state(status, obj)
    if state != "ok":
        return {"state": state, "last_seen": _LAST_SEEN.get("claw")}
    projects = obj.get("projects")
    return {
        "state": "ok",
        "enabled": bool(obj.get("enabled", True)),
        "projects": [str(p) for p in projects] if isinstance(projects, list) else [],
        "last_seen": _note_seen("claw"),
    }


def puck_state(settings, opener=None, timeout=_QUICK_TIMEOUT) -> dict:
    """Puck-bridge liveness for the Hub status surface.

    No puck bridge exists yet — zero puck code in the repo, no puck
    process on the PC — so with no bridge URL configured this honestly
    reports ``not_running`` and never fakes a green status. If a bridge
    URL is ever configured (settings key ``puck_url``) its /healthz is
    probed like the other services, so the status turns live for real
    the day a bridge exists.
    """
    base = _base(settings, "puck_url", "")
    if not base:
        return {
            "state": "not_running",
            "last_seen": _LAST_SEEN.get("puck"),
            "detail": "no puck bridge configured",
        }
    status, obj = _request(f"{base}/healthz", timeout=timeout, opener=opener)
    state = _service_state(status, obj)
    if state == "ok":
        return {"state": "ok", "last_seen": _note_seen("puck")}
    if status is None:
        state = "not_running"
    return {"state": state, "last_seen": _LAST_SEEN.get("puck")}


def claw_tasks(settings, opener=None, timeout=_QUICK_TIMEOUT) -> dict:
    token = _token(settings, "claw_token")
    if not token:
        return {"state": "no_token"}
    status, obj = _get_authed(claw_base(settings), "/tasks", token, opener, timeout)
    state = _service_state(status, obj)
    if state != "ok":
        return {"state": state}
    tasks = obj.get("tasks")
    return {"state": "ok", "tasks": tasks if isinstance(tasks, list) else []}


def claw_submit(settings, project, prompt, profile, opener=None) -> dict:
    token = _token(settings, "claw_token")
    if not token:
        return {"state": "no_token"}
    if profile not in TASK_PROFILES:
        profile = "build"
    status, obj = _request(
        f"{claw_base(settings)}/task",
        method="POST",
        payload={
            "token": token,
            "project": project,
            "prompt": prompt,
            "profile": profile,
            "source": "browser-chat",
        },
        opener=opener,
    )
    state = _service_state(status, obj)
    if state != "ok":
        out = {"state": state}
        if isinstance(obj, dict) and obj.get("error"):
            out["error"] = str(obj["error"])
        return out
    if not obj.get("ok"):
        return {"state": "error", "error": str(obj.get("error") or "submit failed")}
    return {"state": "ok", "job_id": obj.get("job_id")}


def claw_task(settings, job_id, opener=None, timeout=_QUICK_TIMEOUT) -> dict:
    token = _token(settings, "claw_token")
    if not token:
        return {"state": "no_token"}
    status, obj = _get_authed(
        claw_base(settings),
        f"/task/{urllib.parse.quote(str(job_id), safe='')}",
        token,
        opener,
        timeout,
    )
    state = _service_state(status, obj)
    if state != "ok":
        return {"state": state}
    task = obj.get("task")
    return {"state": "ok", "task": task if isinstance(task, dict) else {}}


def claw_cancel(settings, job_id, opener=None) -> dict:
    token = _token(settings, "claw_token")
    if not token:
        return {"state": "no_token"}
    status, obj = _request(
        f"{claw_base(settings)}/task/{urllib.parse.quote(str(job_id), safe='')}/cancel",
        method="POST",
        payload={"token": token},
        opener=opener,
    )
    state = _service_state(status, obj)
    if state != "ok":
        return {"state": state}
    task = obj.get("task")
    return {"state": "ok", "task": task if isinstance(task, dict) else {}}
