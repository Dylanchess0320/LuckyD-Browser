"""Factory: pick the LLM provider based on settings / flags.

Resolution order:
- dry_run=True -> MockProvider (offline, deterministic).
- DRS_PROVIDER=gemini -> GeminiProvider (native grounding, needs key).
- DRS_PROVIDER=luckyd -> LuckyDProvider (LuckyD's resolved provider config).
- DRS_PROVIDER=bridge -> LuckyDBridgeProvider on the AI assistant's connected
  provider — the bridge-resolved chain: keyless locals → Cline gateways →
  keyed clouds (9.8 default after the OpenCode Zen retirement).
  DRS_PROVIDER=cline | clinepass | cline-usage pins the Cline gateways.
- DRS_PROVIDER=openrouter | ollama -> OpenAICompatProvider on the
  verified-free pool for that backend (best free models, live-probed).
- DRS_PROVIDER=mock -> MockProvider.
- auto (default): Gemini native grounding only when the LuckyD stack resolves
  to provider=google with a key; otherwise the AI assistant's providers via
  the bridge, then OpenRouter :free, then local Ollama, then LuckyD
  multi-provider, then mock.
"""

from __future__ import annotations

import os

from ..tools.search_base import LLMProvider
from .gemini import GeminiProvider
from .luckyd import LuckyDProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

_FREE_BACKENDS = ("opencode", "openrouter", "ollama")

# Assistant-side providers the bridge handoff can pin (Cline gateways).
# ("opencode" stays on the direct gateway path via _FREE_BACKENDS below —
# same as pre-9.8 — rather than the bridge chat handoff.)
_BRIDGE_PROVIDERS = ("bridge", "cline", "clinepass", "cline-usage")


def _has_openrouter_key() -> bool:
    return bool((os.getenv("OPENROUTER_API_KEY", "") or "").strip())


def _has_bridge_provider() -> bool:
    """True when the AI assistant has any usable provider registered.

    The bridge owns the 9.8 chain (keyless locals → Cline gateways → keyed
    clouds); deep research rides on it instead of probing gateways itself.
    """
    try:
        from browser.browser_core.ai_bridge import AIBridge as _Bridge1

        return bool(_Bridge1().providers())
    except Exception:
        try:
            from browser_core.ai_bridge import AIBridge as _Bridge2

            return bool(_Bridge2().providers())
        except Exception:
            return False


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
    # The AI assistant's connected providers (bridge-resolved chain:
    # keyless locals → Cline gateways → keyed clouds) — free $0 tiers first.
    if _has_bridge_provider():
        try:
            from .luckyd_bridge import LuckyDBridgeProvider

            return LuckyDBridgeProvider()
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
    if name in _BRIDGE_PROVIDERS:
        from .luckyd_bridge import LuckyDBridgeProvider

        # "bridge" -> assistant default; the Cline aliases pin that gateway.
        return LuckyDBridgeProvider(None if name == "bridge" else name)
    if name in _FREE_BACKENDS or name in ("local", "ollama-local"):
        backend = "ollama" if name in ("local", "ollama-local") else name
        return OpenAICompatProvider(backend=backend)
    return _auto_llm()
