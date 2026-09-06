"""Factory: pick the LLM provider based on settings / flags.

Resolution order:
- dry_run=True -> MockProvider (offline, deterministic).
- DRS_PROVIDER=gemini (or auto with a Gemini key and no LuckyD override)
  -> GeminiProvider (native grounding).
- DRS_PROVIDER=luckyd (or auto otherwise) -> LuckyDProvider (Ollama local
  free, DeepSeek, OpenAI, OpenRouter, ... via LuckyD's provider config).
- DRS_PROVIDER=mock -> MockProvider.
"""

from __future__ import annotations

from ..tools.search_base import LLMProvider
from .gemini import GeminiProvider
from .luckyd import LuckyDProvider
from .mock import MockProvider


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
    # auto: prefer native Gemini grounding when a key exists AND the
    # LuckyD stack isn't pointing at a non-Google provider explicitly.
    if settings.api_key:
        try:
            from core.providers import resolve_provider_config

            resolved = resolve_provider_config()
            if str(resolved.get("provider", "")).lower() == "google":
                return GeminiProvider()
        except Exception:
            return GeminiProvider()
        # Key exists but LuckyD uses another provider -> LuckyD multi-provider.
        return LuckyDProvider()
    # No key at all -> LuckyD path (Ollama local / DDG search; may still
    # need no key). Fall back to mock only when explicitly requested.
    try:
        return LuckyDProvider()
    except Exception:
        return MockProvider()
