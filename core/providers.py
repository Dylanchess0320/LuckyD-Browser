"""
Provider configuration — deduplicated from config.py and llm/__init__.py.
Single source of truth for provider detection, credentials, and model resolution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TypedDict

from core.cline_credit import is_cline_credit_exhausted


class ProviderDefaults(TypedDict, total=False):
    env_key: str | None
    env_base: str
    env_model: str
    default_base: str
    default_model: str


# ── Provider constants ────────────────────────────────────────────────

VALID_PROVIDERS = {
    "openai",
    "anthropic",
    "google",
    "gemini",
    "ollama",
    "deepseek",
    "zai",
    "groq",
    "openrouter",
    "opencode",
    "clinepass",
    "cline-usage",
    "minimax",
}

#: Short-hands accepted anywhere a provider id is read (CODING_AGENT_PROVIDER,
#: --provider, /model, browser settings mirror). Canonicalized at the
#: boundary so sessions never carry the alias id (10.2.2: "cline" was briefly
#: a full provider entry, which duplicated the Cline (usage) row in
#: `lucky-code providers` and bypassed the Cline rotation pool).
PROVIDER_ALIASES = {
    "cline": "cline-usage",
    "cline-pass": "clinepass",
}


def normalize_provider_id(provider: str | None) -> str:
    """Map a provider alias to its canonical id (unknown ids pass through)."""
    pid = (provider or "").lower().strip()
    return PROVIDER_ALIASES.get(pid, pid)


PROVIDER_NAMES = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "gemini": "Google Gemini",
    "ollama": "Ollama",
    "zai": "Z.ai (GLM)",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "opencode": "OpenCode Zen",
    "clinepass": "ClinePass",
    "cline-usage": "Cline (usage)",
    "minimax": "MiniMax",
}

PROVIDER_DEFAULTS: dict[str, ProviderDefaults] = {
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
        "default_model": "gemini-2.5-flash",
    },
    "gemini": {
        "env_key": "GOOGLE_API_KEY",
        "env_base": "GOOGLE_BASE_URL",
        "env_model": "GOOGLE_MODEL",
        "default_base": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
    },
    "ollama": {
        "env_key": None,
        "env_base": "OLLAMA_HOST",
        "env_model": "OLLAMA_MODEL",
        "default_base": "http://127.0.0.1:11434/v1",
        "default_model": "llama3.2:3b",
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
    "groq": {
        "env_key": "GROQ_API_KEY",
        "env_base": "GROQ_BASE_URL",
        "env_model": "GROQ_MODEL",
        "default_base": "https://api.groq.com/openai/v1",
        "default_model": "groq/compound-mini",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "env_base": "OPENROUTER_BASE_URL",
        "env_model": "OPENROUTER_MODEL",
        "default_base": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-chat-v3.1",
    },
    # OpenCode Zen (opencode.ai gateway) — OpenAI-compatible.
    # Key comes from OPENCODE_API_KEY. The old $0 "-free" tier died in
    # 2026-09 (every keyless call 401s), so the default must be a live
    # catalog model, not a retired "-free" id.
    "opencode": {
        "env_key": "OPENCODE_API_KEY",
        "env_base": "OPENCODE_BASE_URL",
        "env_model": "OPENCODE_MODEL",
        "default_base": "https://opencode.ai/zen/v1",
        "default_model": "gemini-3.5-flash-lite",
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
    # MiniMax (MiniMax-M3, Anthropic-compatible Messages API).
    # mesh: python main.py --provider minimax --agent 1
    "minimax": {
        "env_key": "MINIMAX_API_KEY",
        "env_base": "MINIMAX_BASE_URL",
        "env_model": "MINIMAX_MODEL",
        "default_base": "https://api.minimax.io/anthropic",
        "default_model": "MiniMax-M3",
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

#: Cached Cline CLI session token ("" = probed and absent, None = not probed).
_CLINE_SESSION_TOKEN: str | None = None


def cline_session_token() -> str:
    """Return a live Cline CLI session token, or "" when there is none.

    The Cline CLI stores its WorkOS session under ``~/.cline``; the browser and
    the CLI both reuse it so Cline (flat subscription *or* usage-billed free
    models) works with no API key. Probed once per process — it touches disk and
    may refresh a token over the network, so callers must not hammer it.

    9.8: this is the auth path that made Cline a drop-in replacement for the
    OpenCode Zen gateway's free tier (Zen is back in the mesh since 2026-09-21
    but keyed-only).
    """
    global _CLINE_SESSION_TOKEN
    if _CLINE_SESSION_TOKEN is not None:
        return _CLINE_SESSION_TOKEN
    try:
        import sys
        from pathlib import Path

        # cline_session is bundled into the exe (hidden import) or, in the
        # source tree, lives under browser/browser_core/.
        try:
            import cline_session
        except ImportError:
            bc = str(Path(__file__).resolve().parent.parent / "browser" / "browser_core")
            if bc not in sys.path:
                sys.path.insert(0, bc)
            import cline_session

        token = (cline_session.fresh_token() or "").strip()
    except Exception:
        token = ""
    _CLINE_SESSION_TOKEN = token
    return token


def reset_cline_session_cache() -> None:
    """Clear the cached Cline session token (tests call this in fixtures)."""
    global _CLINE_SESSION_TOKEN
    _CLINE_SESSION_TOKEN = None


def detect_provider() -> str | None:
    """Auto-detect which provider to use based on environment variables.
    Returns provider name or None if no provider is usable.

    10.4 free-first: after an explicit CODING_AGENT_PROVIDER (which always
    wins), the free-model priority is walked first
    (``core.free_rotation.best_free_provider``: Cline free tier -> Gemini
    free tier -> local Ollama -> other free providers) — a usable $0 option
    always beats a paid key by default. Keyed/paid providers follow in the
    historical priority order.

    9.8: a logged-in Cline CLI session is detected FIRST and returns
    ``cline-usage`` — Cline's usage-billed gateway carries free agent models and
    its auth comes from the on-disk session, so it is the free default
    (OpenCode Zen's keyless tier died in 2026-09; Zen is back in the mesh
    since 2026-09-21 but keyed-only, so it can't be the free default).

    10.4: while a Cline 402 marker is valid (see core/cline_credit.py),
    auto-detection skips both Cline gateways and falls through to the next
    usable provider. An explicit CODING_AGENT_PROVIDER always wins — forcing
    Cline by hand (e.g. right after topping up) is the documented override.
    """
    explicit = normalize_provider_id(os.environ.get("CODING_AGENT_PROVIDER", ""))
    if explicit in VALID_PROVIDERS:
        return explicit

    # 10.4 free-first: walk the free-model priority before any paid/keyed
    # provider. (Covers the old session-first Cline check and the Ollama
    # liveness probe; no new network surface.)
    from core.free_rotation import best_free_provider

    free = best_free_provider()
    if free:
        return free

    cline_blocked = is_cline_credit_exhausted()

    # Check env vars in priority order (keyed/paid providers)
    checks = [
        ("clinepass", "CLINEPASS_API_KEY"),
        ("cline-usage", "CLINE_USAGE_MODEL"),
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("zai", "ZAI_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("opencode", "OPENCODE_API_KEY"),
        ("minimax", "MINIMAX_API_KEY"),
        ("ollama", "OLLAMA_MODEL"),
    ]
    for provider, env_var in checks:
        if provider in ("clinepass", "cline-usage") and cline_blocked:
            continue
        if os.environ.get(env_var):
            return provider

    # Check DeepSeek
    if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODING_AGENT_API_KEY"):
        return "deepseek"

    return None


def _assistant_browser_settings() -> dict[str, object]:
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


def resolve_provider_config(provider: str | None = None) -> dict[str, object]:
    """Build a full provider config dict. Returns the standard config fields.

    When no explicit CODING_AGENT_PROVIDER is set, the HQ mirrors whatever
    provider + model the browser AI assistant is currently using (read from
    browser/data/settings.json). An explicit CODING_AGENT_PROVIDER still wins.
    """
    mirror_model: str | None = None
    explicit = normalize_provider_id(os.environ.get("CODING_AGENT_PROVIDER", ""))

    if not provider:
        if explicit in VALID_PROVIDERS:
            provider = explicit
        else:
            # Mirror the browser AI assistant's chosen provider (and model).
            bs = _assistant_browser_settings()
            ap = normalize_provider_id(str(bs.get("ai_provider", "") or ""))
            if ap in VALID_PROVIDERS:
                provider = ap
                overrides = bs.get("ai_model_overrides", {})
                if isinstance(overrides, dict):
                    m = str(overrides.get(provider, "") or "").strip()
                    if m:
                        mirror_model = m
            else:
                provider = detect_provider() or "ollama"  # 10.4: terminal free fallback

    provider = normalize_provider_id(provider)
    fallback = PROVIDER_DEFAULTS["deepseek"]
    entry = PROVIDER_DEFAULTS.get(provider, fallback)
    env_key = entry.get("env_key")
    env_base = str(entry.get("env_base") or fallback["env_base"])
    env_model = str(entry.get("env_model") or fallback["env_model"])
    default_base = str(entry.get("default_base") or fallback["default_base"])
    default_model = str(entry.get("default_model") or fallback["default_model"])

    api_key = ""
    if env_key:
        api_key = os.environ.get(env_key, "") or os.environ.get("CODING_AGENT_API_KEY", "")

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
                import cline_session
            except ImportError:
                bc = str(Path(__file__).resolve().parent.parent / "browser" / "browser_core")
                if bc not in sys.path:
                    sys.path.insert(0, bc)
                import cline_session

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

    env_base = str(entry.get("env_base") or fallback["env_base"])
    env_model = str(entry.get("env_model") or fallback["env_model"])
    default_base = str(entry.get("default_base") or fallback["default_base"])
    default_model = str(entry.get("default_model") or fallback["default_model"])

    base_url = os.environ.get(env_base, default_base)
    model_name: str = os.environ.get(env_model, default_model)

    # When mirroring the browser assistant, its model pick wins (unless the user
    # also set an explicit model override for this provider in the repo .env).
    if mirror_model and env_model not in os.environ:
        model_name = mirror_model

    # For Ollama, normalize endpoint and auto-detect available installed models
    if provider == "ollama":
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        try:
            import httpx

            r = httpx.get(f"{base_url}/models", timeout=1.5)
            if r.status_code == 200:
                data = r.json()
                entries = data.get("data", []) if isinstance(data, dict) else []
                avail: list[str] = [
                    str(mid) for m in entries if isinstance(m, dict) for mid in [m.get("id")] if mid
                ]
                if avail and (not model_name or model_name not in avail):
                    pref = next((m for m in avail if "llama3" in m or "qwen" in m), avail[0])
                    model_name = pref
        except Exception:
            pass

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
                model_name = default_model
        else:
            model_name = raw_model

    thinking = os.environ.get("CODING_AGENT_THINKING", "").lower() in ("1", "true", "yes")

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model_name,
        "raw_model": os.environ.get(env_model, default_model),
        "provider": provider,
        "thinking": thinking,
    }


def build_llm_config(provider: str | None = None) -> LLMConfig:
    """Build an LLMConfig from environment variables."""
    cfg = resolve_provider_config(provider)
    return LLMConfig(
        api_key=str(cfg["api_key"]),
        base_url=str(cfg["base_url"]),
        model=str(cfg["model"]),
        provider=str(cfg["provider"]),
        thinking=bool(cfg.get("thinking", False)),
    )


def detect_api_format(provider: str) -> str:
    """Determine the API format for a provider."""
    formats = {
        "openai": "openai",  # OpenAI-compatible chat completions
        "anthropic": "anthropic",  # Anthropic Messages API
        "google": "google",  # Google Generative AI
        "gemini": "google",  # alias of google
        "ollama": "openai",  # Ollama uses OpenAI-compatible
        "deepseek": "openai",  # DeepSeek uses OpenAI-compatible
        "zai": "openai",  # Z.ai GLM uses OpenAI-compatible endpoint
        "groq": "openai",  # Groq uses OpenAI-compatible
        "openrouter": "openai",  # OpenRouter uses OpenAI-compatible
        "opencode": "openai",  # OpenCode Zen is OpenAI-compatible
        "clinepass": "openai",  # ClinePass gateway is OpenAI-compatible
        "cline-usage": "openai",  # same gateway, usage-billed model ids
        "cline": "openai",  # alias of cline-usage
        "minimax": "anthropic",  # Anthropic-compatible Messages API
    }
    return formats.get(provider, "openai")


# ── Provider listing ────────────────────────────────────────────────


def provider_key_from_label(label: str) -> str:
    """Map a human catalog group label to its provider id.

    Shared by ``ui.show_models()``, the ``/model`` picker and the
    ``luckyd-code model`` subcommand so a label like
    ``"Cline Usage (free tier) ✓"`` always resolves to the same provider key.

    Unknown labels fall back to the first word, then to ``cline-usage`` (the
    free default) — never to a retired provider.
    """
    low = (label or "").lower()
    if not low.split():
        return "cline-usage"
    if "clinepass" in low.replace("-", "").replace(" ", ""):
        return "clinepass"
    if "cline" in low:
        return "cline-usage" if "usage" in low else "clinepass"
    if "openrouter" in low:
        return "openrouter"
    if "ollama" in low:
        return "ollama"
    if "openai" in low:
        return "openai"
    if "anthropic" in low or "claude" in low:
        return "anthropic"
    if "z.ai" in low or low.strip().startswith("zai"):
        return "zai"
    if "groq" in low:
        return "groq"
    if "google" in low or "gemini" in low or "gemma" in low:
        return "google"
    if "deepseek" in low:
        return "deepseek"
    if "minimax" in low:
        return "minimax"
    return label.split()[0].lower()


# Providers with a usable $0 tier: local servers, the OpenRouter free catalog,
# Groq's free tier, the Gemini free tier (GOOGLE_API_KEY), and the Cline
# gateways (flat subscription or usage-billed free models). Everything else
# bills per token.
#
# NOTE (2026-09-21): OpenCode Zen is restored to the mesh at Dylan's request,
# but keyed only — its $0 keyless tier died in 2026-09 (every keyless call
# 401s) — so it is NOT in the free tier anymore. Cline (usage-billed free
# models + the logged-in CLI session) remains the default free agent brain —
# see resolve_provider_config().
FREE_TIER_PROVIDERS = frozenset(
    {"openrouter", "ollama", "groq", "clinepass", "cline-usage", "gemini"}
)

# Stable display order for the provider list (local first, then free, then paid).
PROVIDER_ORDER = (
    "ollama",
    "opencode",
    "clinepass",
    "cline-usage",
    "openrouter",
    "groq",
    "deepseek",
    "zai",
    "google",
    "gemini",
    "openai",
    "anthropic",
    "minimax",
)


def list_providers() -> list[dict[str, object]]:
    """List every known AI provider with its live configuration status.

    Each entry has: ``id``, ``name``, ``base_url``, ``model`` (effective default
    or env override), ``env_key`` (or None for keyless local), ``key_present``,
    ``local``, ``free_tier``, ``configured`` (usable right now: local, or key /
    session auth present), ``current`` (the active provider), and
    ``credit_exhausted`` (10.4: True for the Cline rows while a 402 marker is
    valid — those rows report ``configured`` False and are never auto-current).

    10.5 health snapshot (one source of truth for "what would work right
    now"): ``rotation_order`` (0-based index into
    ``core.free_rotation.FREE_MODEL_PRIORITY``, None when the provider is not
    in the rotation), ``next_in_rotation`` (True for the single provider
    ``best_free_provider()`` would pick), ``credit_ttl_remaining_sec`` (whole
    seconds until the Cline 402 marker expires on the Cline rows, else 0),
    ``last_working`` (True when this provider last answered successfully),
    ``last_working_ago`` ("worked 2h ago", else None), and
    ``last_working_model`` (the recorded model id, else None).

    No network calls — availability is derived from env vars only.
    """
    explicit = normalize_provider_id(os.environ.get("CODING_AGENT_PROVIDER", ""))
    current = explicit if explicit in VALID_PROVIDERS else detect_provider() or "deepseek"
    cline_ok = bool(cline_session_token())
    # 10.4: a valid 402 marker steers auto-selection away from Cline (an
    # explicit CODING_AGENT_PROVIDER still wins — see detect_provider()).
    cline_blocked = is_cline_credit_exhausted()

    # 10.5 health snapshot inputs — each guarded so a failure here can never
    # break the provider list itself.
    rotation_order: dict[str, int] = {}
    next_free: str | None = None
    try:
        from core.free_rotation import FREE_MODEL_PRIORITY, best_free_provider

        rotation_order = {p: i for i, (p, _m) in enumerate(FREE_MODEL_PRIORITY)}
        next_free = best_free_provider()
    except Exception:
        rotation_order, next_free = {}, None
    credit_ttl = 0
    try:
        from core.cline_credit import credit_ttl_remaining

        credit_ttl = int(credit_ttl_remaining())
    except Exception:
        credit_ttl = 0
    lw_provider: str | None = None
    lw_model: str | None = None
    lw_ago: str | None = None
    try:
        from core.last_working import last_working_age_label, read_last_working

        _lw = read_last_working()
        if _lw is not None:
            lw_provider, lw_model = _lw.provider, _lw.model
            lw_ago = last_working_age_label()
    except Exception:
        lw_provider, lw_model, lw_ago = None, None, None

    providers: list[dict[str, object]] = []
    for pid in PROVIDER_ORDER:
        defaults = PROVIDER_DEFAULTS.get(pid)
        if defaults is None:
            continue
        env_key = defaults.get("env_key")
        base_url = os.environ.get(defaults["env_base"], defaults["default_base"])
        model = os.environ.get(defaults["env_model"], defaults["default_model"])
        local = pid == "ollama"
        if pid in ("clinepass", "cline-usage"):
            # Auth may come from the logged-in Cline CLI session instead of a key.
            key_present = bool((os.environ.get(env_key or "", "") or "").strip())
            if not key_present:
                key_present = cline_ok
            configured = key_present and not cline_blocked
        elif local:
            configured = True
            key_present = False
        else:
            key_present = bool((os.environ.get(env_key or "", "") or "").strip())
            configured = key_present
        is_last_working = lw_provider is not None and pid == lw_provider
        providers.append(
            {
                "id": pid,
                "name": PROVIDER_NAMES.get(pid, pid.title()),
                "base_url": base_url,
                "model": model,
                "env_key": env_key,
                "key_present": key_present,
                "local": local,
                "free_tier": pid in FREE_TIER_PROVIDERS,
                "configured": configured,
                "current": pid == current,
                "credit_exhausted": cline_blocked and pid in ("clinepass", "cline-usage"),
                # 10.5 health snapshot — "what would work right now".
                "rotation_order": rotation_order.get(pid),
                "next_in_rotation": next_free is not None and pid == next_free,
                "credit_ttl_remaining_sec": (
                    credit_ttl if pid in ("clinepass", "cline-usage") else 0
                ),
                "last_working": is_last_working,
                "last_working_ago": lw_ago if is_last_working else None,
                "last_working_model": lw_model if is_last_working else None,
            }
        )
    return providers
