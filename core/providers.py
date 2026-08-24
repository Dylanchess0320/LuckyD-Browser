"""
Provider configuration — deduplicated from config.py and llm/__init__.py.
Single source of truth for provider detection, credentials, and model resolution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ── Provider constants ────────────────────────────────────────────────

VALID_PROVIDERS = {
    "openai",
    "anthropic",
    "google",
    "ollama",
    "deepseek",
    "zai",
    "openrouter",
    "opencode",
    "clinepass",
    "cline-usage",
}

PROVIDER_NAMES = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "ollama": "Ollama",
    "zai": "Z.ai (GLM)",
    "openrouter": "OpenRouter",
    "opencode": "OpenCode Zen",
    "clinepass": "ClinePass",
    "cline-usage": "Cline (usage)",
}

PROVIDER_DEFAULTS = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "env_base": "OPENAI_BASE_URL",
        "env_model": "OPENAI_MODEL",
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    "anthropic": {
        "env_key": "ANTHROPIC_API_KEY",
        "env_base": "ANTHROPIC_BASE_URL",
        "env_model": "ANTHROPIC_MODEL",
        "default_base": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-20250514",
    },
    "google": {
        "env_key": "GOOGLE_API_KEY",
        "env_base": "GOOGLE_BASE_URL",
        "env_model": "GOOGLE_MODEL",
        "default_base": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.0-flash",
    },
    "ollama": {
        "env_key": None,
        "env_base": "OLLAMA_HOST",
        "env_model": "OLLAMA_MODEL",
        "default_base": "http://localhost:11434",
        "default_model": "codellama",
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "env_base": "CODING_AGENT_BASE_URL",
        "env_model": "CODING_AGENT_MODEL",
        "default_base": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "zai": {
        "env_key": "ZAI_API_KEY",
        "env_base": "ZAI_BASE_URL",
        "env_model": "ZAI_MODEL",
        "default_base": "https://api.z.ai/api/paas/v4",
        "default_model": "glm-4.5",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "env_base": "OPENROUTER_BASE_URL",
        "env_model": "OPENROUTER_MODEL",
        "default_base": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-chat-v3.1",
    },
    # OpenCode Zen (opencode.ai gateway) — every model on it is $0/free.
    # Catalog: opencode's open registry (models.dev), 2026-08.
    "opencode": {
        "env_key": "OPENCODE_API_KEY",
        "env_base": "OPENCODE_BASE_URL",
        "env_model": "OPENCODE_MODEL",
        "default_base": "https://opencode.ai/zen/v1",
        "default_model": "nemotron-3-ultra-free",
    },
    # ClinePass (Cline flat-subscription gateway) — OpenAI-compatible.
    # Key comes from CLINEPASS_API_KEY, else the logged-in Cline CLI session.
    "clinepass": {
        "env_key": "CLINEPASS_API_KEY",
        "env_base": "CLINEPASS_BASE_URL",
        "env_model": "CLINEPASS_MODEL",
        "default_base": "https://api.cline.bot/api/v1",
        "default_model": "cline-pass/deepseek-v4-pro",
    },
    # Cline Usage (credit-billed / free tier) — same api.cline.bot gateway and
    # same auth as ClinePass (CLINEPASS_API_KEY, else the logged-in Cline CLI
    # session), but model ids are provider-prefixed (e.g. deepseek/deepseek-chat)
    # and usage is billed per-request. Free-tier models cost $0 (rate-limited).
    "cline-usage": {
        "env_key": "CLINEPASS_API_KEY",
        "env_base": "CLINEPASS_BASE_URL",
        "env_model": "CLINE_USAGE_MODEL",
        "default_base": "https://api.cline.bot/api/v1",
        "default_model": "deepseek/deepseek-chat",
    },
}


# ── Configuration data class ──────────────────────────────────────────


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 8192
    thinking: bool = False
    provider: str = "deepseek"


# ── Detection logic ───────────────────────────────────────────────────


def detect_provider() -> str | None:
    """Auto-detect which provider to use based on environment variables.
    Returns provider name or None if DeepSeek (fallback) should be used."""
    explicit = os.environ.get("CODING_AGENT_PROVIDER", "").lower().strip()
    if explicit in VALID_PROVIDERS:
        return explicit

    # Check env vars in priority order
    checks = [
        ("clinepass", "CLINEPASS_API_KEY"),
        ("cline-usage", "CLINE_USAGE_MODEL"),
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("zai", "ZAI_API_KEY"),
        ("opencode", "OPENCODE_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("ollama", "OLLAMA_MODEL"),
    ]
    for provider, env_var in checks:
        if os.environ.get(env_var):
            return provider

    # Check DeepSeek
    if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODING_AGENT_API_KEY"):
        return "deepseek"

    return None


def _assistant_browser_settings() -> dict:
    """Read the LuckyD browser assistant's saved provider/model picks.

    The AI sidebar persists its selection in browser/data/settings.json under
    "ai_provider" ("" / missing = auto) and "ai_model_overrides" ({provider: model}).
    Returns {} when the file can't be read.
    """
    try:
        import json as _json
        import sys as _sys
        from pathlib import Path

        if getattr(_sys, "frozen", False):
            root = Path(_sys.executable).resolve().parent
        else:
            root = Path(__file__).resolve().parent.parent
        path = root / "browser" / "data" / "settings.json"
        if not path.exists():
            return {}
        # utf-8-sig strips a leading BOM (the browser SettingsStore writes one).
        data = _json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_provider_config(provider: str | None = None) -> dict:
    """Build a full provider config dict. Returns the standard config fields.

    When no explicit CODING_AGENT_PROVIDER is set, the HQ mirrors whatever
    provider + model the browser AI assistant is currently using (read from
    browser/data/settings.json). An explicit CODING_AGENT_PROVIDER still wins.
    """
    mirror_model: str | None = None
    explicit = os.environ.get("CODING_AGENT_PROVIDER", "").lower().strip()

    if not provider:
        if explicit in VALID_PROVIDERS:
            provider = explicit
        else:
            # Mirror the browser AI assistant's chosen provider (and model).
            bs = _assistant_browser_settings()
            ap = str(bs.get("ai_provider", "") or "").lower().strip()
            if ap in VALID_PROVIDERS:
                provider = ap
                overrides = bs.get("ai_model_overrides", {})
                if isinstance(overrides, dict):
                    m = str(overrides.get(provider, "") or "").strip()
                    if m:
                        mirror_model = m
            else:
                provider = detect_provider() or "deepseek"

    provider = provider.lower()
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["deepseek"])

    api_key = ""
    if defaults["env_key"]:
        api_key = os.environ.get(defaults["env_key"], "") or os.environ.get(
            "CODING_AGENT_API_KEY", ""
        )

    # ClinePass: fall back to the logged-in Cline CLI session (WorkOS token,
    # auto-refreshed) when no explicit key is set — same as the browser.
    if provider in ("clinepass", "cline-usage") and not api_key:
        try:
            import sys
            from pathlib import Path

            # cline_session is bundled into the exe (hidden import) or, in the
            # source tree, lives under browser/browser_core/. Try a plain import
            # first (works when bundled), then add the source path as a fallback.
            try:
                import cline_session  # type: ignore
            except ImportError:
                bc = str(Path(__file__).resolve().parent.parent / "browser" / "browser_core")
                if bc not in sys.path:
                    sys.path.insert(0, bc)
                import cline_session  # type: ignore

            api_key = cline_session.fresh_token()
        except Exception as exc:
            # Surface the real reason instead of silently producing an empty key
            # that later turns into a confusing 401 in the agent reply.
            api_key = ""
            # Only warn if using ClinePass intentionally; fall back to DeepSeek for Agent Mesh
            if explicit in ("clinepass", "cline-usage"):
                print(
                    f"\n  [AUTH] Cline session unavailable — {type(exc).__name__}: {exc}\n"
                    "         Run `cline` (or `cline auth`) to log in, or set "
                    "CLINEPASS_API_KEY in .env."
                )
            # For ClinePass fallback: silently retry with DeepSeek (Agent Mesh doesn't need Cline)
            else:
                provider = "deepseek"

    base_url = os.environ.get(defaults["env_base"], defaults["default_base"])
    model_name = os.environ.get(defaults["env_model"], defaults["default_model"])

    # When mirroring the browser assistant, its model pick wins (unless the user
    # also set an explicit model override for this provider in the repo .env).
    if mirror_model and defaults["env_model"] not in os.environ:
        model_name = mirror_model

    # For DeepSeek, resolve "auto" model
    if provider == "deepseek":
        raw_model = os.environ.get("CODING_AGENT_MODEL", "auto")
        if raw_model == "auto" or raw_model.lower() == "auto":
            try:
                from model_resolver import resolve_model as resolve_deepseek_model

                model_name = resolve_deepseek_model(
                    api_key=api_key or os.environ.get("CODING_AGENT_API_KEY", ""),
                    base_url=base_url,
                    preferred="auto",
                    thinking=os.environ.get("CODING_AGENT_THINKING", "").lower()
                    in ("1", "true", "yes"),
                )
            except Exception:
                model_name = defaults["default_model"]
        else:
            model_name = raw_model

    thinking = os.environ.get("CODING_AGENT_THINKING", "").lower() in ("1", "true", "yes")

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model_name,
        "raw_model": os.environ.get(defaults["env_model"], defaults["default_model"]),
        "provider": provider,
        "thinking": thinking,
    }


def build_llm_config(provider: str | None = None) -> LLMConfig:
    """Build an LLMConfig from environment variables."""
    cfg = resolve_provider_config(provider)
    return LLMConfig(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=cfg["model"],
        provider=cfg["provider"],
        thinking=cfg.get("thinking", False),
    )


def detect_api_format(provider: str) -> str:
    """Determine the API format for a provider."""
    formats = {
        "openai": "openai",  # OpenAI-compatible chat completions
        "anthropic": "anthropic",  # Anthropic Messages API
        "google": "google",  # Google Generative AI
        "ollama": "openai",  # Ollama uses OpenAI-compatible
        "deepseek": "openai",  # DeepSeek uses OpenAI-compatible
        "zai": "openai",  # Z.ai GLM uses OpenAI-compatible endpoint
        "openrouter": "openai",  # OpenRouter uses OpenAI-compatible
        "opencode": "openai",  # OpenCode Zen is OpenAI-compatible
        "clinepass": "openai",  # ClinePass gateway is OpenAI-compatible
        "cline-usage": "openai",  # same gateway, usage-billed model ids
    }
    return formats.get(provider, "openai")
