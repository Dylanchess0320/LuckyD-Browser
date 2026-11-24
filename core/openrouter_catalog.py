"""OpenRouter's live model catalog, cached on disk.

The browser AI sidebar already asks OpenRouter's public ``/models`` endpoint
for its picker.  LuckyD Code historically shipped only a small curated list,
so the terminal could not select most of the models the browser offered.
This module gives both surfaces the same live roster without making every
``/model`` call wait on the network:

* ``models(live=True)`` fetches the catalog and refreshes a 24-hour cache.
* ``models()`` (the default) returns the cached list and never touches the
  network - offline, CI, and cold-start paths stay fast.
* ``free_ids()`` / ``paid_ids()`` split the catalog for the free and paid
  sections of the picker.  ``openrouter/free`` is always pinned first.

No API key is required to read the public catalog; when one is present it is
sent so OpenRouter can return account-specific availability.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_TIMEOUT = 3.0
_USER_AGENT = "LuckyD-Code/1.0"

__all__ = [
    "CACHE_TTL_SECONDS",
    "MODELS_URL",
    "cache_path",
    "free_ids",
    "models",
    "paid_ids",
]


def cache_path() -> Path:
    """Writable per-user cache location (survives app upgrades)."""
    base = (os.environ.get("LOCALAPPDATA", "") or "").strip()
    if base:
        return Path(base) / "LuckyDBrowser" / "openrouter_models.json"
    return Path(__file__).resolve().parent.parent / "data" / "openrouter_models.json"


def _read_cache() -> dict[str, Any]:
    try:
        raw = cache_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        models = [str(m) for m in (data.get("models") or []) if isinstance(m, str) and m]
        try:
            fetched_at = float(data.get("fetched_at") or 0)
        except (TypeError, ValueError):
            fetched_at = 0.0
        return {"models": models, "fetched_at": fetched_at}
    except Exception:
        return {}


def _write_cache(model_ids: list[str]) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "models": model_ids}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        # The cache is an optimization; a failed write must never break /model.
        pass


def _fetch(timeout: float) -> list[str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    key = (os.environ.get("OPENROUTER_API_KEY", "") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(MODELS_URL, headers=headers)
    # nosec B310: MODELS_URL is the hardcoded https:// constant above.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        if int(getattr(response, "status", 200) or 200) != 200:
            return []
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        # Embedding models cannot serve chat completions; the browser picker
        # filters the same way so both catalogs stay aligned.
        if "embed" in model_id.lower():
            continue
        seen.add(model_id)
        found.append(model_id)
    return found


def models(
    *,
    live: bool = False,
    force: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """OpenRouter chat model ids, cached for 24 hours.

    ``live=False`` never touches the network: it returns the cached list (even
    a stale one) or ``[]``.  ``live=True`` fetches when the cache is stale or
    missing; a failed fetch falls back to the stale cache.  ``force=True``
    always fetches.
    """
    cache = _read_cache()
    cached = [str(m) for m in (cache.get("models") or [])]
    try:
        fetched_at = float(cache.get("fetched_at") or 0)
    except (TypeError, ValueError):
        fetched_at = 0.0
    fresh = bool(cached) and (time.time() - fetched_at) < CACHE_TTL_SECONDS

    if cached and not force and (not live or fresh):
        return cached
    if not live and not force:
        return cached

    try:
        fetched = _fetch(timeout)
    except Exception:
        return cached
    if fetched:
        _write_cache(fetched)
        return fetched
    return cached


def _ordered(model_ids: list[str]) -> list[str]:
    """Deduplicate and pin ``openrouter/free`` first, then sort."""
    unique = {m for m in model_ids if m}
    rest = sorted(unique - {"openrouter/free"}, key=str.lower)
    if "openrouter/free" in unique:
        return ["openrouter/free", *rest]
    return rest


def free_ids(model_ids: list[str] | None = None) -> list[str]:
    """The $0 subset: the free meta-router plus every ``:free`` id."""
    ids = list(model_ids) if model_ids is not None else models()
    free = [m for m in ids if m == "openrouter/free" or m.lower().endswith(":free")]
    return _ordered(free)


def paid_ids(model_ids: list[str] | None = None) -> list[str]:
    """Every catalog id that is not a ``:free`` model."""
    ids = list(model_ids) if model_ids is not None else models()
    paid = [m for m in ids if m != "openrouter/free" and not m.lower().endswith(":free")]
    return _ordered(paid)
