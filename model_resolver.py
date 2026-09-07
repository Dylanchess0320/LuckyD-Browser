"""
Dynamic model resolver for DeepSeek API.

On startup, fetches the list of available models from the API,
caches the result for 24 hours, and resolves 'auto' to the
best available model. This means LuckyD Code automatically
picks up whatever DeepSeek releases next — V5, V6, etc.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent / ".model_cache.json"
CACHE_TTL = 86400  # 24 hours
FALLBACK_MODEL = "deepseek-v4-flash"
FALLBACK_PRO_MODEL = "deepseek-v4-pro"

# Keywords used for model classification (order matters: most specific first)
_FAST_KEYWORDS = ("flash", "fast", "lite", "mini", "turbo", "instant")
_PRO_KEYWORDS = ("pro", "reasoner", "thinking", "ultra", "expert", "max")


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
            logger.debug("No API key provided, skipping model catalog fetch")
            return []
        timeout = httpx.Timeout(connect=10.0, read=10.0, write=5.0, pool=5.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            models = [m["id"] for m in resp.json().get("data", [])]
            logger.info("Fetched %d models from %s", len(models), base_url)
            return sorted(models)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "HTTP %d fetching models from %s: %s",
            exc.response.status_code,
            base_url,
            exc,
        )
        return []
    except httpx.TransportError as exc:
        logger.warning("Network error fetching models from %s: %s", base_url, exc)
        return []
    except Exception as exc:
        logger.warning("Unexpected error fetching models from %s: %s", base_url, exc)
        return []


def _classify_models(model_ids: list[str]) -> tuple[str | None, str | None]:
    """
    Given a list of model IDs, classify into 'flash/fast' and 'pro/reasoning'.

    Uses keyword-based heuristics: model IDs containing pro/reasoner/ultra
    keywords are classified as 'pro', those with flash/fast/lite keywords as
    'fast'.  Ties are broken by preferring the shorter ID within each group.

    Returns (fast_model, pro_model).
    """
    pro_candidates: list[str] = []
    fast_candidates: list[str] = []

    for mid in model_ids:
        lower = mid.lower()
        if any(kw in lower for kw in _PRO_KEYWORDS):
            pro_candidates.append(mid)
        elif any(kw in lower for kw in _FAST_KEYWORDS):
            fast_candidates.append(mid)

    # If keyword classification left gaps, fall back to "shortest = fast, rest = pro"
    # for robustness against unknown naming conventions.
    if not fast_candidates and not pro_candidates and model_ids:
        fast_candidates = model_ids[:1]
        pro_candidates = model_ids[1:]

    # Pick shortest within each group (more specific / canonical ID first)
    fast = min(fast_candidates, key=len) if fast_candidates else None
    pro = min(pro_candidates, key=len) if pro_candidates else None

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

    # Try cache first (may include last-known-good fallbacks)
    cached = _cache_get()
    if cached:
        model_ids = cached.get("models", [])
        last_good_fast = cached.get("_last_good_fast")
        last_good_pro = cached.get("_last_good_pro")
    else:
        model_ids = _fetch_models(api_key, base_url)
        last_good_fast = None
        last_good_pro = None
        if model_ids:
            _cache_set({"models": model_ids})

    fast, pro = _classify_models(model_ids) if model_ids else (None, None)

    # Prefer last-known-good over hardcoded fallbacks
    if not fast:
        fast = last_good_fast or FALLBACK_MODEL
    if not pro:
        pro = last_good_pro or FALLBACK_PRO_MODEL

    if fast != FALLBACK_MODEL or pro != FALLBACK_PRO_MODEL:
        logger.debug("Resolved models — fast: %s, pro: %s", fast, pro)

    # Store last-known-good for next time
    if model_ids and (fast or pro):
        try:
            cache_data = _cache_get() or {}
            cache_data["models"] = model_ids
            cache_data["_last_good_fast"] = fast
            cache_data["_last_good_pro"] = pro
            _cache_set(cache_data)
        except OSError:
            pass  # cache write is best-effort

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
