"""Factory: pick the LLM provider based on settings / flags.

Resolution order:
- dry_run=True -> MockProvider (offline, deterministic).
- DRS_PROVIDER=gemini -> GeminiProvider (native grounding, needs key).
- DRS_PROVIDER=luckyd -> LuckyDProvider (LuckyD's resolved provider config).
- DRS_PROVIDER=opencode | openrouter | ollama -> OpenAICompatProvider on the
  verified-free pool for that backend (best free models, live-probed).
- DRS_PROVIDER=mock -> MockProvider.
- auto (default): Gemini native grounding only when the LuckyD stack resolves
  to provider=google with a key; otherwise the verified-free OpenCode Zen
  pool (nemotron-3-ultra-free, no per-token cost), then OpenRouter :free,
  then local Ollama, then LuckyD multi-provider, then mock.
"""

from __future__ import annotations

import os

from ..tools.search_base import LLMProvider
from .gemini import GeminiProvider
from .luckyd import LuckyDProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

_FREE_BACKENDS = ("opencode", "openrouter", "ollama")


def _has_opencode_key() -> bool:
    if (os.getenv("OPENCODE_API_KEY", "") or "").strip():
        return True
    # OPENAI_* pointing at the Zen gateway counts, but ONLY with a real key.
    # (Previous code returned True on default_base alone, so frozen builds with
    # no keys picked a broken opencode backend with an empty key -> 401.)
    if (os.getenv("OPENAI_API_KEY", "") or "").strip():
        base = (os.getenv("OPENAI_BASE_URL", "") or "").strip()
        if "opencode.ai" in base:
            return True
        try:
            from core.providers import resolve_provider_config

            cfg = resolve_provider_config("openai")
            if cfg.get("api_key") and "opencode.ai" in str(cfg.get("base_url", "")):
                return True
        except Exception:
            pass
    try:
        from core.providers import resolve_provider_config

        cfg = resolve_provider_config("opencode")
        if cfg.get("api_key") and "opencode.ai" in str(cfg.get("base_url", "")):
            return True
    except Exception:
        pass
    return False


def _has_openrouter_key() -> bool:
    return bool((os.getenv("OPENROUTER_API_KEY", "") or "").strip())


def _has_ollama() -> bool:
    raw = (os.getenv("OLLAMA_HOST", "") or "").strip().rstrip("/")
    # Frozen default is ".../v1" (OpenAI-compat base); /api/tags lives on the
    # bare host, so strip a trailing /v1 before probing.
    if raw.lower().endswith("/v1"):
        raw = raw[:-3].rstrip("/")
    host = raw or "http://127.0.0.1:11434"
    try:
        import httpx

        r = httpx.get(f"{host}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _auto_llm() -> LLMProvider:
    from ..config import settings

    # Preserve Gemini native grounding when the LuckyD stack is explicitly on
    # Google with a key (grounding citations need the native API).
    if settings.api_key:
        try:
            from core.providers import resolve_provider_config

            resolved = resolve_provider_config()
            if str(resolved.get("provider", "")).lower() == "google":
                return GeminiProvider()
        except Exception:
            return GeminiProvider()
    # Verified-free pools first (no per-token cost).
    if _has_opencode_key():
        try:
            return OpenAICompatProvider(backend="opencode")
        except Exception:
            pass
    if _has_openrouter_key():
        try:
            return OpenAICompatProvider(backend="openrouter")
        except Exception:
            pass
    if _has_ollama():
        try:
            return OpenAICompatProvider(backend="ollama")
        except Exception:
            pass
    # A Gemini key alone (non-Google LuckyD stack) still works via native API.
    if settings.api_key:
        try:
            return GeminiProvider()
        except Exception:
            pass
    try:
        return LuckyDProvider()
    except Exception:
        return MockProvider()


def get_llm(dry_run: bool = False, provider: str | None = None) -> LLMProvider:
    if dry_run:
        return MockProvider()
    from ..config import settings

    name = (provider or settings.provider or "auto").lower()
    if name == "mock":
        return MockProvider()
    if name == "gemini":
        return GeminiProvider()
    if name == "luckyd":
        return LuckyDProvider()
    if name in _FREE_BACKENDS or name in ("local", "ollama-local"):
        backend = "ollama" if name in ("local", "ollama-local") else name
        return OpenAICompatProvider(backend=backend)
    return _auto_llm()
