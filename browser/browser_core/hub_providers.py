"""One-tap provider switching for the Hub Chat page (Hub slice 2).

The Qt sidebar already lets Dylan pick a provider and saves it as the
``ai_provider`` setting — but the Hub Chat page, the unification surface,
had no provider control at all, and ``/chat/send`` never told the Hub
which provider to use. Switching meant leaving the Chat page for the
sidebar or editing config. This module is the pure, Qt-free half of the
fix: read the saved pick, report the available providers, and pin a new
pick (persisted, so it survives restarts).

Every function is defensive in the same fail-open style as
``luckyd_services``: a backend without ``ai_info`` simply reports no
available providers instead of raising, and a settings object without
``set``/``save`` still validates the pick in memory.
"""

from __future__ import annotations

#: The "let the chain decide" pick — stored as "" in settings, like the
#: sidebar does, and reported as "auto" on the wire.
AUTO = "auto"


def current_provider(settings) -> str:
    """The pinned provider name, or ``"auto"`` when nothing is pinned."""
    try:
        raw = str(settings.get("ai_provider", "") or "").strip() if settings is not None else ""
    except Exception:
        raw = ""
    return raw or AUTO


def provider_state(settings, backend=None) -> dict:
    """Queryable provider state for ``/chat/state``.

    ``available``/``default``/``local_status`` come from the backend's
    ``ai_info()`` when it has one (the real AppBackend does); a backend
    without it — the unit-test fake, for example — reports an empty
    available list rather than failing the whole status surface.
    """
    info: dict = {}
    try:
        ai_info = getattr(backend, "ai_info", None)
        if callable(ai_info):
            got = ai_info()
            if isinstance(got, dict):
                info = got
    except Exception:
        info = {}
    available = info.get("ai_providers") or []
    available = [str(p) for p in available if str(p).strip()]
    current = current_provider(settings)
    # A pinned provider that is no longer offered (e.g. a dead Cline
    # gateway dropped from the usable list) is reported as Auto — the
    # pin itself is only cleared by picking Auto or another provider,
    # mirroring the Chat page picker's own fallback. Without this the
    # status surface keeps showing a dead provider as "current".
    # (An empty available list means the backend told us nothing, so
    # the pin is reported as-is rather than guessed away.)
    if available and current != AUTO and current not in available:
        current = AUTO
    return {
        "current": current,
        "available": available,
        "default": info.get("ai_default"),
        "local_status": dict(info.get("ai_local_status") or {}),
    }


def pin_provider(settings, name, available=None) -> dict:
    """Pin ``name`` as the chat provider (``"auto"``/empty clears the pin).

    ``available`` is the allowlist to validate against; when it is None
    the pick cannot be validated and any non-empty name is accepted
    (fail-open: a stale provider list must never block a switch). An
    unknown name against a known list is rejected and nothing is saved.
    Returns ``{"state": "ok", "current": ...}`` or
    ``{"state": "error", "error": ...}``. Never raises.
    """
    try:
        raw = str(name or "").strip()
    except Exception:
        raw = ""
    if raw in ("", AUTO):
        _save_pin(settings, "")
        return {"state": "ok", "current": AUTO}
    if available is not None and raw not in [str(p) for p in available]:
        return {"state": "error", "error": f"unknown provider: {raw}"}
    _save_pin(settings, raw)
    return {"state": "ok", "current": raw}


def _save_pin(settings, value: str) -> None:
    try:
        setter = getattr(settings, "set", None)
        if callable(setter):
            setter("ai_provider", value)
    except Exception:
        pass
    try:
        saver = getattr(settings, "save", None)
        if callable(saver):
            saver()
    except Exception:
        pass
