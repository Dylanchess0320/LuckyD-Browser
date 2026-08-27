"""Self-contained AI bridge: speaks to multiple LLM providers via httpx directly.

No dependence on the repo's `llm/` package — reads keys from the repo .env
(or process env). Local keyless servers (Ollama, LM Studio — free, unlimited,
offline) are auto-detected FIRST and need no API key at all; cloud providers
(Google Gemini free tier, Groq free tier, Z.ai, OpenRouter, DeepSeek, OpenAI,
Anthropic) act as optional boosters further down the fallback chain.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
from browser_core import cline_session

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_HAS_STREAM_END = re.compile(r"\[DONE\]")

_PROVIDER_SPECS = [
    (
        "google",
        "GOOGLE_API_KEY",
        "gemini-2.0-flash",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini",
    ),
    ("groq", "GROQ_API_KEY", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1", "openai"),
    ("zai", "ZAI_API_KEY", "glm-4.5-flash", "https://api.z.ai/api/paas/v4", "openai"),
    (
        "openrouter",
        "OPENROUTER_API_KEY",
        "deepseek/deepseek-chat-v3.1",
        "https://openrouter.ai/api/v1",
        "openai",
    ),
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-v4-flash", "https://api.deepseek.com", "openai"),
    ("openai", "OPENAI_API_KEY", "gpt-4o", "https://api.openai.com/v1", "openai"),
    (
        "anthropic",
        "ANTHROPIC_API_KEY",
        "claude-sonnet-4-20250514",
        "https://api.anthropic.com/v1",
        "anthropic",
    ),
]

# Keyless local servers — probed at startup, registered before keyed clouds.
# Any OpenAI-compatible /v1 endpoint works (Ollama, LM Studio, llama.cpp…).
# (name, host env override, default host, model env override)
_LOCAL_SPECS = [
    ("ollama", "OLLAMA_HOST", "http://127.0.0.1:11434", "OLLAMA_MODEL"),
    ("lmstudio", "LMSTUDIO_HOST", "http://127.0.0.1:1234", ""),
]

# Preferred local chat/agent models — first substring match in the server's
# installed-model list wins; otherwise the first installed model is used.
# Small CPU-friendly models rank first: most machines have no GPU.
_LOCAL_MODEL_PREF = (
    "llama3.2",
    "qwen3",
    "gemma3",
    "phi4",
    "qwen2.5",
    "llama3.3",
    "llama3.1",
    "mistral",
    "deepseek",
    "gpt-oss",
)

# Model-name families that accept image input. Vision-off is the safe
# default for unknown models: the agent then skips screenshots instead of
# dying with an HTTP 400 mid-task.
_VISION_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-5",
    "gemini",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "gemma3",
    "gemma-3",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "llama3.2-vision",
    "pixtral",
    "glm-4v",
    "glm-4.5v",
    "kimi-vl",
    "internvl",
    "phi-3-vision",
    "phi-3.5-vision",
    "phi-4-multimodal",
    # OpenCode Zen / OpenRouter free models with image input (models.dev)
    "gemma-4",
    "gemma4",
    "grok-code",
    "kimi-k2.5",
    "mimo-v2",
    "qwen3.6",
    "muse-spark",
)
# Substrings that veto a hint match (text-only variants of vision lines).
_VISION_EXCLUDE = ("gemma3:1b", "gemma-3-1b")

# ClinePass (Cline flat-subscription gateway) — OpenAI-compatible.
# Auth: CLINEPASS_API_KEY from .env, else the logged-in Cline CLI session.
_CLINEPASS_BASE = "https://api.cline.bot/api/v1"
_CLINEPASS_MODEL = "cline-pass/deepseek-v4-pro"

# Curated fallback for api.cline.bot — the gateway has no public model
# catalog endpoint (only /chat/completions). Sources: ClinePass docs model
# table (subscription) + Cline API docs (credit-billed), 2026-07.
_CLINEPASS_CATALOG = [
    # ── Included in the ClinePass flat subscription — these work with a
    # $0 (even negative) credit balance; usage counts against the sub quota.
    "cline-pass/kimi-k3",
    "cline-pass/deepseek-v4-flash",  # fast + cheapest — great agent model
    "cline-pass/kimi-k2.7-code",
    "cline-pass/kimi-k2.6",
    "cline-pass/deepseek-v4-pro",
    "cline-pass/mimo-v2.5",
    "cline-pass/mimo-v2.5-pro",
    "cline-pass/minimax-m3",
    "cline-pass/qwen3.7-max",
    "cline-pass/qwen3.7-plus",
]

# OpenCode Zen (opencode.ai) free catalog — every $0 model on the Zen
# gateway, synced from opencode's open model registry (models.dev), 2026-08.
# Used as the fetch_models() fallback for the zen endpoint and to keep the
# sidebar picker complete when the live catalog request fails. All of these
# are tool-capable unless noted; [V] marks vision-capable entries.
_OPENCODE_ZEN_BASE = "https://opencode.ai/zen/v1"
_OPENCODE_ZEN_DEFAULT = "mimo-v2.5-free"
_FREE_TOP_ZEN = (
    "mimo-v2.5-free",
    "big-pickle",
    "hy3-free",
    "laguna-s-2.1-free",
)
_OPENCODE_FREE_CATALOG = [
    "mimo-v2.5-free",  # [V]
    "big-pickle",
    "hy3-free",
    "laguna-s-2.1-free",
    "deepseek-v4-flash-free",
    "glm-4.7-free",
    "glm-5-free",
    "grok-code",  # [V]
    "hy3-free",
    "hy3-preview-free",
    "kimi-k2.5-free",  # [V]
    "laguna-s-2.1-free",
    "ling-2.6-flash-free",
    "ling-3.0-flash-free",
    "ling-3.0-tiny-free",
    "longcat-2.0-free",
    "mimo-v2-flash-free",
    "mimo-v2-omni-free",  # [V]
    "mimo-v2-pro-free",  # [V]
    "mimo-v2.5-free",  # [V]
    "minimax-m2.1-free",
    "minimax-m2.5-free",
    "minimax-m3-free",
    "muse-spark-1.2-contributor-free",  # [V]
    "nemotron-3-super-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "north-mini-code-free",
    "qwen3.6-plus-free",  # [V]
    "ring-2.6-1t-free",
    "trinity-large-preview-free",
    "x-preview-f-free",  # [V]
]

# OpenRouter free fallback — the :free chat models plus the auto free-router,
# used only when the live /models request fails. Kept roughly largest-first.
_OPENROUTER_FREE_FALLBACK = [
    "openrouter/free",  # meta-router: auto-picks any available free model
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "z-ai/glm-5.2:free",
    "thinkingmachines/inkling:free",
    "thinkingmachines/inkling-small:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "cohere/north-mini-code:free",
    "liquid/lfm-2.5-2.6b:free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
]

# ── Curated FREE top models with no strict rate limits ────────────────
# These are the $0 models that run without per-minute throttling and are
# therefore safe to rotate through on 429s. Local Ollama/LMStudio are
# always unlimited (no key, no quota); OpenCode Zen's $0 gateway is
# generous and the models below are the highest-quality picks (size +
# reasoning + vision). Free-tier Google/Groq/Cline are intentionally
# EXCLUDED here because they rate-limit aggressively.
_FREE_TOP_ZEN = [
    "nemotron-3-ultra-free",  # flagship — 550B, best overall
    "nemotron-3-super-free",
    "kimi-k2.5-free",  # vision + long context
    "mimo-v2.5-free",  # vision
    "qwen3.6-plus-free",  # vision
    "muse-spark-1.2-contributor-free",  # vision
    "glm-4.7-free",
    "grok-code",  # vision, coder
    "deepseek-v4-flash-free",
    "minimax-m3-free",
]
_FREE_TOP_ZEN_SET = set(_FREE_TOP_ZEN)

# Cline Usage (credit-billed / free tier) — same gateway, usage-based billing.
# Free-tier models work at $0.00 but are rate-limited; credit models deduct
# from your Cline Credits balance.   Sources: Cline API docs, 2026-07.
_CLINE_USAGE_MODEL = "deepseek/deepseek-chat"
_CLINE_USAGE_CATALOG = [
    # ── Free tier (rate-limited, $0.00 — needs non-negative credit balance)
    "minimax/minimax-m2.5",
    "deepseek/deepseek-chat",  # DeepSeek V3 — fast general model
    "deepseek/deepseek-r1",  # DeepSeek R1 — reasoning model
    "meta-llama/llama-3.2-3b-instruct",  # Small Llama, quick responses
    "google/gemini-2.0-flash",  # Google Gemini free tier
    "qwen/qwen3-8b",  # Qwen 3 small, CPU-friendly
    # ── Credit-billed — deduct from Cline Credits balance
    "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "mistral/mistral-large",
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    for key, value in os.environ.items():
        env[key] = value
    return env


class AIBridge:
    def __init__(self) -> None:
        env = _load_env()
        self._configs: dict[str, tuple[str, str, str, str]] = {}
        self._model_cache: dict[str, list[str]] = {}
        self._clinepass_from_session = False
        # Round-robin cursor for free unlimited model rotation (Zen top pool)
        self._free_cursor: int = 0
        # Local keyless servers first: free + unlimited should be the default.
        self._configs.update(self._detect_local(env))
        # Remember which providers are keyless locals (used by
        # default_provider; _LOCAL_SPECS itself may change under tests).
        self._local_names = {name for name, *_ in _LOCAL_SPECS}
        self._detect_clinepass(env)
        for name, key_name, model, base_url, kind in _PROVIDER_SPECS:
            key = env.get(key_name, "").strip()
            if not key:
                continue
            # Per-provider overrides, e.g. DEEPSEEK_MODEL=deepseek-v4-pro or
            # OPENAI_BASE_URL=https://opencode.ai/zen/v1
            prefix = key_name.removesuffix("_API_KEY")
            model = env.get(f"{prefix}_MODEL", "").strip() or model
            base_url = env.get(f"{prefix}_BASE_URL", "").strip() or base_url
            self._configs[name] = (model, base_url, key, kind)
        # Register OpenCode Zen ($0 free gateway, key optional)
        opencode_key = env.get("OPENCODE_API_KEY", "").strip()
        opencode_base = env.get("OPENCODE_BASE_URL", "").strip() or _OPENCODE_ZEN_BASE
        opencode_model = env.get("OPENCODE_MODEL", "").strip() or _OPENCODE_ZEN_DEFAULT
        self._configs["opencode"] = (opencode_model, opencode_base, opencode_key, "openai")

    def _detect_clinepass(self, env) -> None:
        """Register ClinePass subscription + Cline Usage (credit-billed/free tier).

        Both share the same auth: CLINEPASS_API_KEY from .env, else the
        logged-in Cline CLI session.  The session path registers whenever a
        login exists on disk — even with every access token expired.  Tokens
        are refreshed via the stored WorkOS refresh token lazily before each
        call (off the UI thread), so ClinePass and Cline Usage stay available
        with the Cline terminal closed.

        When no session exists, we still register with an empty token so that
        fetch_models() can fall back to the curated catalog.
        """
        token = env.get("CLINEPASS_API_KEY", "").strip()
        if not token and cline_session.has_session():
            self._clinepass_from_session = True
            try:
                token = cline_session.stored_token()
            except RuntimeError:
                token = ""  # expired now — refreshed lazily before each call
        # else: no session, token stays "" — catalog fallback will work
        base = env.get("CLINEPASS_BASE_URL", "").strip() or _CLINEPASS_BASE

        # ── ClinePass (flat subscription) ──────────────────────────────
        model = env.get("CLINEPASS_MODEL", "").strip() or _CLINEPASS_MODEL
        self._configs["clinepass"] = (model, base, token, "openai")

        # ── Cline Usage (credit-billed / free tier) ────────────────────
        usage_model = env.get("CLINE_USAGE_MODEL", "").strip() or _CLINE_USAGE_MODEL
        self._configs["cline-usage"] = (usage_model, base, token, "openai")

    @staticmethod
    def _detect_local(env) -> dict[str, tuple[str, str, str, str]]:
        """Probe local OpenAI-compatible servers; returns keyless configs."""
        found: dict[str, tuple[str, str, str, str]] = {}
        for name, host_var, default_host, model_var in _LOCAL_SPECS:
            host = (env.get(host_var, "").strip() or default_host).rstrip("/")
            base_url = host if host.endswith("/v1") else host + "/v1"
            try:
                resp = httpx.get(base_url + "/models", timeout=1.5)
                resp.raise_for_status()
                models = [m.get("id", "") for m in resp.json().get("data", [])]
            except Exception:
                continue
            models = [m for m in models if m and "embed" not in m.lower()]
            if not models:
                continue
            model = env.get(model_var, "").strip() if model_var else ""
            if model not in models:
                model = next(
                    (m for pref in _LOCAL_MODEL_PREF for m in models if pref in m.lower()),
                    models[0],
                )
            found[name] = (model, base_url, "", "openai")
        return found

    def providers(self) -> list[str]:
        return list(self._configs)

    def is_opencode_zen(self, provider: str) -> bool:
        """True when the provider's endpoint is the OpenCode Zen gateway."""
        info = self._configs.get(provider)
        return bool(info) and "opencode.ai" in info[1]

    def provider_label(self, provider: str) -> str | None:
        """Endpoint-aware display name: 'OpenCode Zen' vs plain 'OpenAI'."""
        if self.is_opencode_zen(provider):
            return "OpenCode Zen"
        return None

    def default_provider(self) -> str | None:
        """Return the highest-priority available provider.

        Preference order (matches this module's docstring):
          1. Local keyless servers (Ollama, LM Studio) — free, unlimited,
             offline, no key or login needed
          2. cline-usage — Cline free tier, when auth actually exists
             (API key or a logged-in Cline CLI session)
          3. Cloud keyed providers, in _PROVIDER_SPECS order
        """
        for name in self._local_names:
            if name in self._configs:
                return name
        if "cline-usage" in self._configs and self._cline_usable():
            return "cline-usage"
        return next(iter(self._configs), None)

    def _cline_usable(self) -> bool:
        """True when Cline auth exists (API key or logged-in CLI session).

        clinepass / cline-usage are registered even with an empty token (for
        the model-catalog fallback), so presence alone is not proof of auth.
        """
        if self._clinepass_from_session:
            return True
        info = self._configs.get("cline-usage") or self._configs.get("clinepass")
        return bool(info and info[2])

    def model_for(self, provider: str) -> str:
        info = self._configs.get(provider)
        return info[0] if info else ""

    def supports_vision(self, provider: str | None) -> bool:
        """True when the provider's current model accepts image input.

        Name-family heuristics (_VISION_HINTS); unknown models get False
        so screenshots are never sent to a text-only endpoint.
        """
        model = self.model_for(provider).lower()
        if not model or any(x in model for x in _VISION_EXCLUDE):
            return False
        return any(h in model for h in _VISION_HINTS)

    def is_local(self, provider: str) -> bool:
        """True for keyless local servers (callers may shrink token budgets)."""
        info = self._configs.get(provider)
        # Key check alone is not enough: session-based clinepass can hold an
        # empty placeholder token while it waits for a lazy refresh.
        return bool(info) and info[2] == "" and provider in {name for name, *_ in _LOCAL_SPECS}

    def set_model_override(self, provider: str, model: str) -> None:
        """Switch a provider's model at runtime (sidebar model picker)."""
        info = self._configs.get(provider)
        if info is None or not model.strip():
            return
        self._configs[provider] = (model.strip(), info[1], info[2], info[3])

    def free_top_models(self) -> list[str]:
        """Cherry-picked free top models with no strict rate limits (Zen)."""
        return list(_FREE_TOP_ZEN)

    def _free_unlimited_providers(self) -> list[str]:
        """Providers with no rate limits — local + Zen free gateway."""
        order: list[str] = []
        for name in self._local_names:
            if name in self._configs:
                order.append(name)
        # Zen free (via opencode or openai pointed at zen) is also unlimited
        for name in ("opencode", "openai"):
            if name in self._configs and self.is_opencode_zen(name) and name not in order:
                order.append(name)
        return order

    def fetch_models(self, provider: str) -> list[str]:
        """Model ids available on a provider — live catalog when it has one
        (Ollama, LM Studio, most OpenAI-compatible hosts), curated fallback
        for ClinePass / Cline Usage (gateway exposes no catalog), current
        model otherwise. For LuckyD we expose ONLY free top models."""
        info = self._configs.get(provider)
        if info is None:
            return []
        if provider in self._model_cache:
            return self._model_cache[provider]
        model, base_url, api_key, kind = info
        models: list[str] = []
        if kind == "openai":
            key = api_key
            if provider in ("clinepass", "cline-usage") and self._clinepass_from_session:
                try:
                    key = cline_session.fresh_token()
                except RuntimeError:
                    key = ""
            key = (key or "").strip()
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            try:
                resp = httpx.get(base_url + "/models", headers=headers, timeout=5.0)
                resp.raise_for_status()
                models = [
                    m["id"]
                    for m in resp.json().get("data", [])
                    if m.get("id") and "embed" not in m["id"].lower()
                ]
            except Exception:
                models = []
        if self.is_opencode_zen(provider):
            # LuckyD: ONLY free TOP models (no rate limits) — clean picker
            # and rotation members always visible. Live catalog is ignored
            # beyond confirming the endpoint is reachable; the curated top
            # set is authoritative until vetted.
            if models:
                # Confirm endpoint up — keep curated order, but push
                # live-present models slightly ahead for fidelity
                live_set = set(models)
                models = sorted(list(_FREE_TOP_ZEN), key=lambda m: (0 if m in live_set else 1, _FREE_TOP_ZEN.index(m)))
            # empty live (no network/auth) falls through to fallback below
        if not models:
            if provider == "clinepass":
                models = list(_CLINEPASS_CATALOG)
            elif provider == "cline-usage":
                models = list(_CLINE_USAGE_CATALOG)
            elif self.is_opencode_zen(provider):
                # Only free top (no rate limits) — not the full 29-model catalog
                models = list(_FREE_TOP_ZEN)
            elif provider == "openrouter":
                # Only free tier; sorted :free first for the picker
                models = list(_OPENROUTER_FREE_FALLBACK)
            else:
                models = [model]
        # Ensure the current model is present (user override may be outside top)
        if model not in models and model in _OPENCODE_FREE_CATALOG:
            # If it's a valid free model but not top, keep it visible at top
            models.insert(0, model)
        elif model in models:
            models.remove(model)
            models.insert(0, model)
        elif model not in models and self.is_opencode_zen(provider) and model.endswith("-free"):
            models.insert(0, model)
        if provider == "openrouter" and len(models) > 1:
            models = sorted(models, key=lambda m: not m.endswith(":free"))
        # For Zen, pin top order: _FREE_TOP_ZEN order first, then any extra
        if self.is_opencode_zen(provider):
            top_order = {m: i for i, m in enumerate(_FREE_TOP_ZEN)}
            models = sorted(models, key=lambda m: top_order.get(m, 999))
        self._model_cache[provider] = models
        return models

    async def chat(self, messages, provider=None, on_token=None):
        # Auto (no provider) uses the free unlimited rotation first: local
        # keyless servers + Zen top free models round-robin on every call and
        # on 429 rate-limit. Explicit provider skips rotation.
        if provider is None:
            # Prefer free unlimited pool before falling back to any provider
            free_pool = self._free_unlimited_providers()
            # If we have a Zen free gateway, try its top models round-robin
            # before touching rate-limited clouds.
            for name in free_pool:
                info = self._configs.get(name)
                if info is None:
                    continue
                if self.is_opencode_zen(name):
                    last_err = None
                    # Try up to len(_FREE_TOP_ZEN) models starting at cursor
                    for offset in range(len(_FREE_TOP_ZEN)):
                        idx = (self._free_cursor + offset) % len(_FREE_TOP_ZEN)
                        m = _FREE_TOP_ZEN[idx]
                        trial = (m, info[1], info[2], info[3])
                        if name in ("clinepass", "cline-usage") and self._clinepass_from_session:
                            try:
                                token = cline_session.fresh_token()
                                trial = (m, info[1], token, info[3])
                            except RuntimeError as exc:
                                last_err = exc
                                break
                        try:
                            text = await self._call(name, trial, messages, on_token)
                            self._free_cursor = (idx + 1) % len(_FREE_TOP_ZEN)
                            self._configs[name] = trial
                            return text, name
                        except Exception as exc:
                            last_err = exc
                            msg = str(exc)
                            is_rate = "429" in msg or "rate" in msg.lower() or "quota" in msg.lower()
                            if not is_rate:
                                break  # non-rate error — try next provider pool entry
                            continue
                    # all top models for this Zen gateway failed — keep last_err and try next pool member
                    continue
                # Local (ollama/lmstudio) — single model, no inner rotation
                if name in ("clinepass", "cline-usage") and self._clinepass_from_session:
                    try:
                        token = cline_session.fresh_token()
                        info = (info[0], info[1], token, info[3])
                        self._configs[name] = info
                    except RuntimeError as exc:
                        last_err = exc
                        continue
                try:
                    text = await self._call(name, info, messages, on_token)
                    return text, name
                except Exception as exc:
                    last_err = exc
                    # local rarely 429 — if it does, try Zen next
                    continue
            # No free unlimited succeeded — fall through to full provider fallback
        order = [provider] if provider else self.providers()
        last_err: Exception | None = None
        for name in order:
            # Skip members already tried in the free unlimited fast-path above
            if provider is None and name in self._free_unlimited_providers():
                # For Zen we already cycled all top models; the current model in config is already tried.
                # Only retry the free pool's current configured model once more with the generic path.
                # To avoid double-dipping, skip unless the error was non-rate and we want a second chance.
                # Instead just skip - full fallback below covers rate-limited clouds.
                if self.is_opencode_zen(name):
                    continue
                # local already tried — skip
                if name in self._local_names:
                    continue
            info = self._configs.get(name)
            if info is None:
                continue
            if name in ("clinepass", "cline-usage") and self._clinepass_from_session:
                try:
                    token = cline_session.fresh_token()
                    info = (info[0], info[1], token, info[3])
                    self._configs[name] = info
                except RuntimeError as exc:
                    last_err = exc
                    continue
            # For explicit provider that is Zen, allow per-model rotation on 429 as well
            if provider is not None and self.is_opencode_zen(name):
                for offset in range(len(_FREE_TOP_ZEN)):
                    idx = (self._free_cursor + offset) % len(_FREE_TOP_ZEN)
                    m = _FREE_TOP_ZEN[idx]
                    trial = (m, info[1], info[2], info[3])
                    try:
                        text = await self._call(name, trial, messages, on_token)
                        self._free_cursor = (idx + 1) % len(_FREE_TOP_ZEN)
                        self._configs[name] = trial
                        return text, name
                    except Exception as exc:
                        last_err = exc
                        msg = str(exc)
                        if "429" not in msg and "rate" not in msg.lower():
                            break
                        continue
                continue
            try:
                text = await self._call(name, info, messages, on_token)
                return text, name
            except Exception as exc:
                last_err = exc
        if last_err is not None:
            raise RuntimeError(f"all providers failed — last error: {last_err}")
        raise RuntimeError(
            "no AI providers configured — install Ollama and run "
            "`ollama pull qwen3:4b`, or add cloud keys to the repo .env"
        )

    async def _call(self, name, info, messages, on_token):
        model, base_url, api_key, kind = info
        body = (
            self._body_gemini(messages)
            if kind == "gemini"
            else (
                self._body_anthropic(messages)
                if kind == "anthropic"
                else self._body_openai(messages)
            )
        )
        body["model"] = model
        headers = {"User-Agent": "LuckyDBrowser/1.0"}

        if kind == "gemini":
            url = f"{base_url}/models/{model}:streamGenerateContent?key={api_key}&alt=sse"
        elif kind == "anthropic":
            url = f"{base_url}/messages"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            url = f"{base_url}/chat/completions"
            key = (api_key or "").strip()
            if key:
                # An EMPTY / whitespace-only Authorization header ('Bearer ' /
                # 'Bearer  ') makes h11 raise "Illegal header value b'Bearer '"
                # at send time — never send it without a real key.
                headers["Authorization"] = f"Bearer {key}"

        text = ""
        # Local keyless models can be slow on CPU — allow a longer first token.
        timeout = 300.0 if not api_key else 60.0
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("POST", url, json=body, headers=headers) as resp,
        ):
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode(errors="replace")[:300]
                raise RuntimeError(f"{name} HTTP {resp.status_code}: {detail}")
            buf = ""
            async for chunk in resp.aiter_bytes():
                buf += chunk.decode(errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line or _HAS_STREAM_END.search(line):
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        data = json.loads(line)
                        delta = self._extract_delta(data, kind)
                        if delta:
                            text += delta
                            if on_token:
                                on_token(delta)
                    except json.JSONDecodeError:
                        pass
        return text

    @staticmethod
    def _text_of(content) -> str:
        """Plain-text view of a message content (str or multimodal parts)."""
        if isinstance(content, str):
            return content
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )

    @staticmethod
    def _parse_data_url(url: str) -> tuple[str, str] | None:
        """data:image/jpeg;base64,XXXX -> (mime, b64), else None."""
        if url.startswith("data:") and ";base64," in url:
            mime, _, data = url[5:].partition(";base64,")
            if mime and data:
                return mime, data
        return None

    @staticmethod
    def _parts_gemini(content) -> list:
        """Gemini parts from str or OpenAI-style multimodal content."""
        if isinstance(content, str):
            return [{"text": content}]
        parts = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                parts.append({"text": p.get("text", "")})
            elif p.get("type") == "image_url":
                img = AIBridge._parse_data_url(p.get("image_url", {}).get("url", ""))
                if img:
                    parts.append({"inlineData": {"mimeType": img[0], "data": img[1]}})
        return parts or [{"text": ""}]

    @staticmethod
    def _parts_anthropic(content):
        """Anthropic content from str or OpenAI-style multimodal content."""
        if isinstance(content, str):
            return content
        parts = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                parts.append({"type": "text", "text": p.get("text", "")})
            elif p.get("type") == "image_url":
                img = AIBridge._parse_data_url(p.get("image_url", {}).get("url", ""))
                if img:
                    parts.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img[0],
                                "data": img[1],
                            },
                        }
                    )
        return parts or ""

    @staticmethod
    def _body_gemini(messages):
        parts, system = [], ""
        for m in messages:
            if m["role"] == "system":
                system += AIBridge._text_of(m["content"]) + "\n"
            else:
                r = "user" if m["role"] in ("user", "tool") else "model"
                parts.append({"role": r, "parts": AIBridge._parts_gemini(m["content"])})
        body = {"contents": parts}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system.strip()}]}
        return body

    @staticmethod
    def _body_anthropic(messages):
        sys_text, normal = "", []
        for m in messages:
            if m["role"] == "system":
                sys_text += AIBridge._text_of(m["content"]) + "\n"
            else:
                normal.append(
                    {
                        "role": "user" if m["role"] == "user" else "assistant",
                        "content": AIBridge._parts_anthropic(m["content"]),
                    }
                )
        body = {"messages": normal, "max_tokens": 4096}
        if sys_text:
            body["system"] = sys_text.strip()
        return body

    @staticmethod
    def _body_openai(messages):
        out = []
        for m in messages:
            r = m["role"]
            out.append(
                {
                    "role": r if r in ("user", "assistant", "system") else "user",
                    "content": m["content"],
                }
            )
        return {"messages": out, "stream": True}

    @staticmethod
    def _extract_delta(data, kind):
        if kind == "gemini":
            c = data.get("candidates", [])
            if c:
                parts = c[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts)
        if kind == "anthropic":
            d = data.get("delta", {})
            t = d.get("text", "")
            if t:
                return t
            content = data.get("content", [])
            return "".join(b.get("text", "") for b in content if b.get("type") == "text")
        choices = data.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            text = delta.get("content", "")
            if text:
                return text
            msg = choices[0].get("message", {})
            return msg.get("content", "")
        return ""
