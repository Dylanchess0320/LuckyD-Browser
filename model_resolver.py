"""
Dynamic model resolver for DeepSeek API.

On startup, fetches the list of available models from the API,
caches the result for 24 hours, and resolves 'auto' to the
best available model. This means LuckyD Code automatically
picks up whatever DeepSeek releases next — V5, V6, etc.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

CACHE_FILE = Path(__file__).parent / ".model_cache.json"
CACHE_TTL = 86400  # 24 hours
FALLBACK_MODEL = "deepseek-v4-flash"
FALLBACK_PRO_MODEL = "deepseek-v4-pro"


def _cache_get() -> dict | None:
    """Read cached model list if still fresh."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data.get("_timestamp", 0) < CACHE_TTL:
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _cache_set(data: dict) -> None:
    """Write model list to cache."""
    data["_timestamp"] = time.time()
    CACHE_FILE.write_text(json.dumps(data))


def _fetch_models(api_key: str, base_url: str) -> list[str]:
    """Fetch available model IDs from DeepSeek API."""
    try:
        key = (api_key or "").strip()
        if not key:
            return []  # no key -> no catalog; avoid "Illegal header value"
        timeout = httpx.Timeout(connect=10.0, read=10.0, write=5.0, pool=5.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            models = [m["id"] for m in resp.json().get("data", [])]
            return sorted(models)
    except Exception:
        return []


def _classify_models(model_ids: list[str]) -> tuple[str | None, str | None]:
    """
    Given a list of model IDs, classify into 'flash/fast' and 'pro/reasoning'.

    Heuristic: the model with the shorter name is usually the fast one;
    the one with 'pro' or 'reasoner' in its name is the thinking one.

    Returns (fast_model, pro_model).
    """
    fast = None
    pro = None

    for mid in model_ids:
        lower = mid.lower()
        if "pro" in lower or "reasoner" in lower or "thinking" in lower:
            if pro is None or len(mid) < len(pro):
                pro = mid
        else:
            if fast is None or len(mid) < len(fast):
                fast = mid

    return fast, pro


def resolve_model(
    api_key: str,
    base_url: str,
    preferred: str = "auto",
    thinking: bool = False,
) -> str:
    """
    Resolve a model name, supporting 'auto' for automatic discovery.

    Args:
        api_key: DeepSeek API key
        base_url: API base URL
        preferred: 'auto', 'flash', 'pro', or a specific model name
        thinking: If True and preferred=='auto', prefer the pro/reasoning model

    Returns:
        Resolved model ID string (e.g. 'deepseek-v4-flash')
    """
    # If user specified an exact model, use it
    if preferred not in ("auto", "flash", "pro"):
        return preferred

    # Try cache first
    cached = _cache_get()
    if cached:
        model_ids = cached.get("models", [])
    else:
        model_ids = _fetch_models(api_key, base_url)
        if model_ids:
            _cache_set({"models": model_ids})

    fast, pro = _classify_models(model_ids) if model_ids else (None, None)

    # If API fetch failed, use fallbacks
    if not fast:
        fast = FALLBACK_MODEL
    if not pro:
        pro = FALLBACK_PRO_MODEL

    if preferred == "pro":
        return pro
    if preferred == "flash":
        return fast

    # 'auto': prefer flash unless thinking mode is requested
    return pro if thinking else fast


def invalidate_cache() -> None:
    """Force re-fetch on next call."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def get_cached_models() -> list[str]:
    """Get currently cached model list (may be stale)."""
    cached = _cache_get()
    if cached:
        return cached.get("models", [])
    return []
