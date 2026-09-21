"""Self-contained AI bridge: speaks to multiple LLM providers via httpx directly.

No dependence on the repo's `llm/` package — reads keys from the repo .env
(or process env). Local keyless servers (Ollama, LM Studio — free, unlimited,
offline) are auto-detected FIRST and need no API key at all; the Cline
gateways (Cline Usage free tier / ClinePass subscription via one logged-in
session) are the default cloud fallback; other keyed clouds (Google Gemini
free tier, Groq free tier, Z.ai, OpenRouter, DeepSeek, OpenAI, Anthropic)
act as optional boosters further down the fallback chain.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

import httpx
from browser_core import cline_session

# Smart model routing (core/router.py) — optional. The router is a pure local
# heuristic (no network); if it cannot be imported, auto mode keeps today's
# static fallback chain.
try:
    from core.router import route_task as _route_task
except Exception:  # pragma: no cover - import guard
    _route_task = None

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_HAS_STREAM_END = re.compile(r"\[DONE\]")


def _contributor_enabled() -> bool:
    """Contributor-tier models are opt-in only (training-data trade-off).

    Reads LUCKYD_CONTRIBUTOR_TIER from the env/.env; a missing or false value
    means the cheaper muse-spark contributor model is never offered.
    """
    raw = (os.environ.get("LUCKYD_CONTRIBUTOR_TIER", "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    try:
        return bool(
            _load_env().get("LUCKYD_CONTRIBUTOR_TIER", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
    except Exception:
        return False


def _is_contributor_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return bool(m) and m.startswith("muse-spark") and "contributor" in m


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
    # Cline / OpenRouter free models with image input (models.dev)
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
# table (subscription) + Cline API docs (credit-billed), 2026-09 (v9.1:
# added glm-5.3, glm-5.2, qwen3.8-max from live docs).
_CLINEPASS_CATALOG = [
    # ── Included in the ClinePass flat subscription — these work with a
    # $0 (even negative) credit balance; usage counts against the sub quota.
    "cline-pass/glm-5.3",
    "cline-pass/glm-5.2",
    "cline-pass/kimi-k3",
    "cline-pass/deepseek-v4-flash",  # fast + cheapest — great agent model
    "cline-pass/kimi-k2.7-code",
    "cline-pass/kimi-k2.6",
    "cline-pass/deepseek-v4-pro",
    "cline-pass/mimo-v2.5",
    "cline-pass/mimo-v2.5-pro",
    "cline-pass/minimax-m3",
    "cline-pass/qwen3.8-max",
    "cline-pass/qwen3.7-max",
    "cline-pass/qwen3.7-plus",
]

# OpenCode Zen (opencode.ai) was RETIRED in 9.8.
# HISTORY: it served a keyless $0 tier of third-party "-free" models until
# 2026-09, then demanded OPENCODE_API_KEY, and now the gateway blocks the
# accounts this project used — so LuckyD no longer registers it, lists it,
# or rotates through it. Cline (api.cline.bot: flat subscription +
# usage-billed free models, authenticated by the logged-in Cline CLI
# session) is the free default that replaced it.

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

# ── Curated Cline gateway top models (free/cheap first) ──
# The chat path rotates through these when the configured Cline model
# fails (404, unsupported, rate limit). Usage-billed free models first —
# they cost nothing and work with a logged-in Cline CLI session.
_CLINE_GATEWAY_TOP_MODELS = [
    "deepseek/deepseek-chat",
    "minimax/minimax-m2.5",
    "qwen/qwen3-8b",
    "deepseek/deepseek-v4-flash",
    "kwaipilot/kat-coder-pro",
    "z-ai/glm-5.3-flash",
    "cline-pass/deepseek-v4-flash",
    "cline-pass/kimi-k3",
    "cline-pass/glm-5.3",
    "cline-pass/claude-sonnet-4",
]
_CLINE_GATEWAY_TOP_MODELS_SET = set(_CLINE_GATEWAY_TOP_MODELS)

# Cline Usage (credit-billed / free tier) — same gateway, usage-based billing.
# Free-tier models work at $0.00 but are rate-limited; credit models deduct
# from your Cline Credits balance.   Sources: Cline API docs, 2026-09 (v9.1:
# added kat-coder-pro, glm-5, deepseek-v4-flash, glm-5.3-flash,
# laguna-s-2.1:free, longcat-2.0 — all FREE in CLI/IDE).
_CLINE_USAGE_MODEL = "deepseek/deepseek-chat"
_CLINE_USAGE_CATALOG = [
    # ── Free tier (rate-limited, $0.00 — needs non-negative credit balance)
    "minimax/minimax-m2.5",
    "deepseek/deepseek-chat",  # DeepSeek V3 — fast general model
    "deepseek/deepseek-r1",  # DeepSeek R1 — reasoning model
    "meta-llama/llama-3.2-3b-instruct",  # Small Llama, quick responses
    "google/gemini-2.0-flash",  # Google Gemini free tier
    "qwen/qwen3-8b",  # Qwen 3 small, CPU-friendly
    "kwaipilot/kat-coder-pro",  # free coding model
    "z-ai/glm-5",  # Z-AI free tier
    "deepseek/deepseek-v4-flash",  # fast DeepSeek free
    "z-ai/glm-5.3-flash",  # Z-AI flash free
    "poolside/laguna-s-2.1:free",  # poolside free
    "cline-free/longcat-2.0",  # Cline promo free
    # ── Credit-billed — deduct from Cline Credits balance
    "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "mistral/mistral-large",
]

# Cline gateway catalog (usage-billed free tier + flat subscription):
# the picker fallback when the live /models request fails, and the rotation
# pool for the chat path. Replaces the retired OpenCode Zen catalog.
_CLINE_GATEWAY_CATALOG = list(_CLINE_USAGE_CATALOG) + list(_CLINEPASS_CATALOG)


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
        # Round-robin cursor for Cline top-model rotation on failures
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
            # GROQ_BASE_URL=https://api.groq.com/openai/v1
            prefix = key_name.removesuffix("_API_KEY")
            model = env.get(f"{prefix}_MODEL", "").strip() or model
            base_url = env.get(f"{prefix}_BASE_URL", "").strip() or base_url
            self._configs[name] = (model, base_url, key, kind)
        # 9.8: OpenCode Zen is not registered any more — the gateway blocks
        # these accounts. Cline (flat subscription + usage-billed free models,
        # auth from the Cline CLI session) is registered by _detect_clinepass.

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

    # Per-server probe diagnosis: name -> "ok" | "not_running" | "no_models".
    # Surfaced through local_status() so the UI can tell the user exactly
    # what's wrong instead of a generic "AI not set up".
    _local_status: dict[str, str] = {}

    @staticmethod
    def _detect_local(env) -> dict[str, tuple[str, str, str, str]]:
        """Probe local OpenAI-compatible servers; returns keyless configs."""
        found: dict[str, tuple[str, str, str, str]] = {}
        AIBridge._local_status = {}
        for name, host_var, default_host, model_var in _LOCAL_SPECS:
            host = (env.get(host_var, "").strip() or default_host).rstrip("/")
            base_url = host if host.endswith("/v1") else host + "/v1"
            try:
                # trust_env=False: localhost must never go through a proxy.
                # With HTTP(S)_PROXY set (VPNs, some ISPs), the default
                # client routes 127.0.0.1 via the proxy and the probe fails
                # even though Ollama is up — the classic "Ollama not
                # detected" ghost.
                resp = httpx.get(base_url + "/models", timeout=1.5, trust_env=False)
                resp.raise_for_status()
                models = [m.get("id", "") for m in resp.json().get("data", [])]
            except Exception:
                AIBridge._local_status[name] = "not_running"
                continue
            models = [m for m in models if m and "embed" not in m.lower()]
            if not models:
                AIBridge._local_status[name] = "no_models"
                continue
            model = env.get(model_var, "").strip() if model_var else ""
            if model not in models:
                model = next(
                    (m for pref in _LOCAL_MODEL_PREF for m in models if pref in m.lower()),
                    models[0],
                )
            found[name] = (model, base_url, "", "openai")
            AIBridge._local_status[name] = "ok"
        return found

    def providers(self) -> list[str]:
        return list(self._configs)

    def local_status(self) -> dict[str, str]:
        """Probe diagnosis per local server: ok | not_running | no_models."""
        return dict(AIBridge._local_status)

    def is_cline_gateway(self, provider: str) -> bool:
        """True when the provider talks to the Cline gateway (api.cline.bot).

        Covers both ClinePass (flat subscription) and Cline Usage (credit-billed
        / free tier) — they share one endpoint and one auth. Replaces the old
        ``is_opencode_zen()`` check after OpenCode Zen was retired in 9.8.
        """
        info = self._configs.get(provider)
        return bool(info) and "api.cline.bot" in info[1]

    def provider_label(self, provider: str) -> str | None:
        """Endpoint-aware display name: 'Cline (subscription)' vs plain 'OpenAI'."""
        if self.is_cline_gateway(provider):
            return "Cline"
        return None

    def default_provider(self) -> str | None:
        """Return the highest-priority available provider.

        Preference order (matches this module's docstring):
          1. Local keyless servers (Ollama, LM Studio) — free, unlimited,
             offline, no key or login needed
          2. cline-usage — Cline free tier, when auth actually exists
             (API key or a logged-in Cline CLI session) — this is the free
             default that replaced the retired OpenCode Zen gateway
          3. Cloud keyed providers, in _PROVIDER_SPECS order
        """
        for name, *_ in _LOCAL_SPECS:
            if name in self._configs:
                return name
        if "cline-usage" in self._configs and self._cline_usable():
            return "cline-usage"
        # clinepass/cline-usage register with empty tokens for the
        # fetch_models() catalog fallback — never default to that dead end.
        for name in self._configs:
            if name in ("clinepass", "cline-usage") and not self._cline_usable():
                continue
            return name
        return None

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

    def provider_config(self, provider: str) -> dict[str, str] | None:
        """Resolved connection details for one provider — the research-swarm
        handoff (features/deep_research/models/luckyd_bridge.py) speaks plain
        OpenAI-compatible /chat/completions and needs model + base_url + key.

        Returns a fresh dict on every call so the Cline session token gets a
        chance to refresh (bridge callers on other threads must not share the
        bridge's internal tuples). Returns None for an unknown provider.
        """
        info = self._configs.get(provider)
        if info is None:
            return None
        model, base_url, api_key, kind = info
        if provider in ("clinepass", "cline-usage") and self._clinepass_from_session:
            with contextlib.suppress(RuntimeError):
                api_key = cline_session.fresh_token()
        return {"model": model, "base_url": base_url, "api_key": api_key, "kind": kind}

    def call_sync(
        self,
        messages: list[dict],
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        timeout: float = 120.0,
    ) -> str:
        """Blocking one-shot chat call — the deep-research swarm's sync
        ``LLMProvider`` runs inside ``asyncio.to_thread`` workers, so it must
        never await the bridge's async ``chat()`` (an event loop in a worker
        thread would deadlock or blow up).

        Speaks the OpenAI-compatible /chat/completions protocol directly (the
        Cline gateway, OpenRouter, DeepSeek, Groq, locals … are all
        OpenAI-flavoured; a native kind is redirected to its OpenAI-compatible
        sibling when one exists). Model rotation mirrors the async chat path:
        the Cline gateway rotates through _CLINE_GATEWAY_TOP_MODELS on any
        failure, other providers keep their configured model.
        """
        pname = provider or self.default_provider() or ""
        cfg = self.provider_config(pname)
        if cfg is None:
            raise RuntimeError(
                f"no AI provider configured for deep research ({pname or 'auto'} not registered)"
            )
        base_url = cfg["base_url"]
        kind = cfg["kind"]
        if kind != "openai":
            # Research speaks OpenAI-compat only; native Gemini/Anthropic
            # accounts keep their keys in .env for the dedicated backends
            # (features/deep_research/models/gemini.py …), so mirror to the
            # OpenAI-compatible endpoint when we know one.
            if pname == "google":
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
            else:
                raise RuntimeError(
                    f"provider {pname!r} ({kind}) is not OpenAI-compatible; "
                    "research can't use it directly"
                )
        key = (cfg["api_key"] or "").strip()
        headers = {"User-Agent": "LuckyDBrowser/9.8", "Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        configured = cfg["model"]
        if model and model != configured:
            candidates = [model, configured, *_CLINE_GATEWAY_TOP_MODELS]
        elif self.is_cline_gateway(pname):
            candidates = [configured, *[m for m in _CLINE_GATEWAY_TOP_MODELS if m != configured]]
        else:
            candidates = [configured]
        seen: set[str] = set()
        ordered = [m for m in candidates if m and not (m in seen or seen.add(m))]
        body: dict = {"messages": messages}
        if temperature is not None:
            body["temperature"] = temperature
        last_err: Exception | None = None
        with httpx.Client(timeout=timeout, trust_env=not self.is_local(pname)) as client:
            for m in ordered:
                payload = {**body, "model": m}
                try:
                    resp = client.post(
                        f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
                    )
                except Exception as exc:  # rotate on network errors
                    last_err = exc
                    continue
                if resp.status_code >= 400:
                    last_err = RuntimeError(
                        f"{pname} {m} HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    if resp.status_code in (400, 401, 404, 429, 500, 502, 503, 504):
                        continue  # rotate to the next model
                    break
                try:
                    data = resp.json()
                except Exception as exc:
                    last_err = exc
                    continue
                choices = data.get("choices", []) if isinstance(data, dict) else []
                msg = (choices[0].get("message", {}) or {}) if choices else {}
                content = msg.get("content", "") or ""
                if isinstance(content, list):
                    content = "".join(
                        p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                    )
                text = str(content).strip()
                if text:
                    # Pin the winning model so the rest of the run stays on it.
                    self._configs[pname] = (m, base_url, cfg["api_key"], cfg["kind"])
                    return text
                last_err = RuntimeError(f"{pname} {m}: empty content")
        raise RuntimeError(f"{pname} chat failed (tried {ordered}): {last_err}")

    def free_top_models(self) -> list[str]:
        """Curated Cline gateway top models — the sidebar's suggestion pool."""
        return list(_CLINE_GATEWAY_TOP_MODELS)

    def _free_unlimited_providers(self) -> list[str]:
        """Providers with no rate limits — local servers only.

        (OpenCode Zen used to be here when it had a $0 keyless tier; it was
        retired in 9.8, so the unlimited pool is keyless locals only.)
        """
        # _LOCAL_SPECS order (Ollama first) — not the _local_names set, whose
        # iteration order is hash-randomized across processes and would flip
        # the pool (and the chat default) between two healthy local servers
        # from run to run.
        return [name for name, *_ in _LOCAL_SPECS if name in self._configs]

    def fetch_models(self, provider: str) -> list[str]:
        """Model ids available on a provider — live catalog when it has one
        (Ollama, LM Studio, most OpenAI-compatible hosts), curated fallback
        for the Cline gateways (gateway exposes no catalog), current model
        otherwise. For LuckyD we expose ONLY free/cheap top models."""
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
        if self.is_cline_gateway(provider):
            # LuckyD: curated top models first, then the rest of the live
            # platform catalog — clean picker, rotation members visible.
            if models:
                live_set = set(models)
                top = [m for m in _CLINE_GATEWAY_TOP_MODELS if m in live_set]
                rest = [m for m in models if m not in top]
                models = top + rest
            else:
                models = list(_CLINE_GATEWAY_CATALOG)
        if not models:
            if provider == "clinepass":
                models = list(_CLINEPASS_CATALOG)
            elif provider == "cline-usage":
                models = list(_CLINE_USAGE_CATALOG)
            elif self.is_cline_gateway(provider):
                models = list(_CLINE_GATEWAY_CATALOG)
            elif provider == "openrouter":
                # Only free tier; sorted :free first for the picker
                models = list(_OPENROUTER_FREE_FALLBACK)
            else:
                models = [model]
        # Ensure the current model is present (user override may be outside top)
        if model not in models and model in _CLINE_GATEWAY_CATALOG:
            # If it's a valid free model but not top, keep it visible at top
            models.insert(0, model)
        elif model in models:
            models.remove(model)
            models.insert(0, model)
        elif (
            model not in models
            and self.is_cline_gateway(provider)
            and model in _CLINE_GATEWAY_CATALOG
        ):
            models.insert(0, model)
        if provider == "openrouter" and len(models) > 1:
            # The "openrouter/free" meta-router heads the curated list; keep
            # it first, then the :free models, then anything else.
            models = sorted(models, key=lambda m: (m != "openrouter/free", not m.endswith(":free")))
        # For the Cline gateway, pin top order: _CLINE_GATEWAY_TOP_MODELS first
        if self.is_cline_gateway(provider):
            top_order = {m: i for i, m in enumerate(_CLINE_GATEWAY_TOP_MODELS)}
            models = sorted(models, key=lambda m: top_order.get(m, 999))
        # Contributor tier is opt-in only: never silently offer a model whose
        # cheap price is paid for with your prompts as training data.
        if not _contributor_enabled():
            models = [m for m in models if not _is_contributor_model(m)]
        self._model_cache[provider] = models
        return models

    # ── Smart routing (Phase 3) ──────────────────────────────────────
    def _is_viable_provider(self, name: str) -> bool:
        """True when the bridge can actually use this provider right now.

        Registration already encodes reachability/credentials: local servers
        are only registered when the startup probe succeeded, keyed clouds
        only when a key exists. Cline entries
        may be registered with an empty token, so they need the explicit
        usability check.
        """
        if self._configs.get(name) is None:
            return False
        if name in ("clinepass", "cline-usage"):
            return self._cline_usable()
        return True

    @staticmethod
    def _routing_text(messages) -> str:
        """Latest user message as plain text ('' when unavailable)."""
        try:
            for msg in reversed(messages or []):
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and isinstance(part.get("text"), str)
                    )
            return ""
        except Exception:
            return ""

    @staticmethod
    def _routing_context_size(messages) -> int:
        """Rough context size in tokens (~4 chars/token) for the router."""
        try:
            chars = 0
            for msg in messages or []:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    chars += len(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            text = part.get("text", "")
                            if isinstance(text, str):
                                chars += len(text)
            return chars // 4
        except Exception:
            return 0

    def _routed_provider(self, messages) -> str | None:
        """Router's provider pick for auto mode, or None.

        Returns a name only when the router's pick is one the bridge already
        considers viable (router catalog names map 1:1 onto bridge provider
        names). Anything else — import failure, router error, non-viable
        pick — returns None and today's static chain runs unchanged.
        """
        if _route_task is None:
            return None
        try:
            decision = _route_task(
                self._routing_text(messages),
                self._routing_context_size(messages),
            )
            name = getattr(decision.model, "provider", "") or ""
        except Exception:
            return None
        return name if name and self._is_viable_provider(name) else None

    async def chat(self, messages, provider=None, on_token=None):
        # Auto (no provider) uses the free unlimited rotation first: local
        # keyless servers + Cline gateway top models round-robin on every call
        # and on 429 rate-limit. Explicit provider skips rotation.
        if isinstance(provider, str) and provider.lower() == "auto":
            provider = None  # the UI's "auto" sentinel means auto mode
        is_auto = provider is None
        # Smart routing (Phase 3): in auto mode the local router picks the
        # provider from the request instead of the static fallback chain.
        # An explicit provider pick always wins — routing never fires then.
        # A non-viable router pick (no credential/server) → None → today's
        # chain runs exactly as before.
        routed = self._routed_provider(messages) if is_auto else None
        fast_path_ran = False
        # Initialized before the free-pool fast path so a real failure there
        # (e.g. Ollama connection refused) survives to the final error when
        # the generic fallback has nothing left to try — otherwise it was
        # swallowed and replaced by a misleading "no AI providers configured".
        last_err: Exception | None = None
        if is_auto and routed is None:
            fast_path_ran = True
            # Prefer free unlimited pool (locals) before falling back to
            # any provider. The Cline gateway still rotates its top models
            # per-model on failure further down.
            free_pool = self._free_unlimited_providers()
            for name in free_pool:
                info = self._configs.get(name)
                if info is None:
                    continue
                if self.is_cline_gateway(name):
                    last_err = None
                    # Try top models starting at cursor (model-level 404s /
                    # unsupported / rate limits rotate to the next)
                    for offset in range(len(_CLINE_GATEWAY_TOP_MODELS)):
                        idx = (self._free_cursor + offset) % len(_CLINE_GATEWAY_TOP_MODELS)
                        m = _CLINE_GATEWAY_TOP_MODELS[idx]
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
                            self._free_cursor = (idx + 1) % len(_CLINE_GATEWAY_TOP_MODELS)
                            self._configs[name] = trial
                            return text, name
                        except Exception as exc:
                            last_err = exc
                            # On any free-model failure (unsupported, rate limit, 500, timeout),
                            # keep trying remaining free models in the pool
                            continue
                    # all top models for this Cline gateway failed — keep last_err and try next pool member
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
                    # local rarely 429 — if it does, try the Cline gateway next
                    continue
            # No free unlimited succeeded — fall through to full provider fallback
        order = [provider] if provider else self.providers()
        if routed is not None:
            # Routed provider goes first; the rest of today's chain follows
            # as fallback (free pool included — still reachable on failure).
            order = [routed] + [p for p in order if p != routed]
        # last_err already holds any free-pool fast-path failure (see above).
        for name in order:
            if provider is None and not self._is_viable_provider(name):
                # Auto mode never burns a call on a provider that can't work
                # (e.g. clinepass registered with an empty token) — the
                # honest end state is "no AI providers configured".
                continue
            # Skip members already tried in the free unlimited fast-path above
            # (only when the fast path actually ran — routing skips it).
            if fast_path_ran and name in self._free_unlimited_providers():
                # The Cline gateway already cycled all top models in the fast
                # path; the current model in config was tried too, so skip it
                # here and let the fallback below cover rate-limited clouds.
                if self.is_cline_gateway(name):
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
            # For an explicit Cline gateway pick, allow per-model rotation on any failure
            if provider is not None and self.is_cline_gateway(name):
                configured_m = info[0]
                candidates = [configured_m] + [
                    m for m in _CLINE_GATEWAY_TOP_MODELS if m != configured_m
                ]
                for m in candidates:
                    trial = (m, info[1], info[2], info[3])
                    try:
                        text = await self._call(name, trial, messages, on_token)
                        self._configs[name] = trial
                        return text, name
                    except Exception as exc:
                        last_err = exc
                        continue
                continue
            try:
                text = await self._call(name, info, messages, on_token)
                return text, name
            except Exception as exc:
                last_err = exc
                if self.is_cline_gateway(name):
                    configured_m = info[0]
                    for m in [alt for alt in _CLINE_GATEWAY_TOP_MODELS if alt != configured_m]:
                        trial = (m, info[1], info[2], info[3])
                        try:
                            text = await self._call(name, trial, messages, on_token)
                            self._configs[name] = trial
                            return text, name
                        except Exception as inner_exc:
                            last_err = inner_exc
                            continue
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
        headers = {"User-Agent": "LuckyDBrowser/9.8"}

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
        # Same proxy ghost as the probe: localhost must bypass proxies.
        no_proxy = name in self._local_names
        async with (
            httpx.AsyncClient(timeout=timeout, trust_env=not no_proxy) as client,
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
