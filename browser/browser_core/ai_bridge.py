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
import time
from pathlib import Path
from typing import Any

import httpx
from browser_core import cline_session

# Smart model routing (core/router.py) — optional. The router is a pure local
# heuristic (no network); if it cannot be imported, auto mode keeps today's
# static fallback chain.
_route_task: Any = None
try:
    from core.router import route_task as _route_task
except Exception:  # pragma: no cover - import guard
    _route_task = None

# Cline credit-exhaustion marker (core/cline_credit.py) — optional like the
# router above: the frozen browser may not have `core` importable, so fall
# back to reading the state file directly. Both paths agree on location,
# format, TTL (24 h), and the LUCKYD_CLINE_CREDIT_STATE seam; both fail open.
_core_cline_exhausted: Any = None
_core_cline_record: Any = None
try:
    from core.cline_credit import is_cline_credit_exhausted as _core_cline_exhausted
    from core.cline_credit import record_cline_credit_exhausted as _core_cline_record
except Exception:  # pragma: no cover - import guard
    _core_cline_exhausted = None
    _core_cline_record = None

# Live free-model detection (core/free_detection.py) — optional like the
# router above: the frozen browser may not have `core` importable.
# The bridge reads the CACHED list only (no network on the selection path);
# a background refresh is kicked off at bridge init (see __init__).
_effective_free_priority: Any = None
try:
    from core.free_rotation import effective_free_priority as _effective_free_priority
except Exception:  # pragma: no cover - import guard
    _effective_free_priority = None
_ensure_fresh_detection: Any = None
try:
    from core.free_detection import ensure_fresh_detection as _ensure_fresh_detection
except Exception:  # pragma: no cover - import guard
    _ensure_fresh_detection = None

# LiteLLM model router (browser_core/model_router.py) — optional, like the
# seams above. When litellm is installed and the router is enabled,
# auto-mode chat tries its config-driven tier chain (free -> cheap -> local)
# first; any router failure falls back to this bridge's httpx chain, so a
# missing/broken router can never take chat down.
try:
    from browser_core import model_router as _model_router
except Exception:  # pragma: no cover - import guard
    _model_router = None


def _cline_state_path() -> Path:
    """Marker file location, shared by the core and fallback paths."""
    override = (os.environ.get("LUCKYD_CLINE_CREDIT_STATE", "") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".luckyd" / "cline_credit_state.json"


def _cline_credit_exhausted() -> bool:
    """True while a Cline 402 marker is inside its 24 h TTL. Never raises."""
    if _core_cline_exhausted is not None:
        try:
            return bool(_core_cline_exhausted())
        except Exception:
            return False
    try:
        data = json.loads(_cline_state_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        return (time.time() - float(data["exhausted_at"])) < 24 * 60 * 60
    except Exception:
        return False


def _record_cline_credit_exhausted(reason: str) -> None:
    """Persist a Cline 402 marker. Never raises."""
    if _core_cline_record is not None:
        with contextlib.suppress(Exception):
            _core_cline_record(reason)
        return
    try:
        path = _cline_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"exhausted_at": time.time(), "reason": (reason or "")[:300]}),
            encoding="utf-8",
        )
    except Exception:
        pass


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
        "gemini-3.8-flash",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini",
    ),
    ("groq", "GROQ_API_KEY", "groq/compound-mini", "https://api.groq.com/openai/v1", "openai"),
    ("zai", "ZAI_API_KEY", "glm-5.3-flash", "https://api.z.ai/api/paas/v4", "openai"),
    (
        "openrouter",
        "OPENROUTER_API_KEY",
        "openrouter/free",
        "https://openrouter.ai/api/v1",
        "openai",
    ),
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-v4-flash", "https://api.deepseek.com", "openai"),
    ("openai", "OPENAI_API_KEY", "gpt-6-sol", "https://api.openai.com/v1", "openai"),
    (
        "anthropic",
        "ANTHROPIC_API_KEY",
        "claude-opus-5-5",
        "https://api.anthropic.com/v1",
        "anthropic",
    ),
    # 2026-09-24: xAI Grok 4.7 — flagship coding / long-running agent model
    # (500K context). OpenAI-compatible: https://api.x.ai/v1, XAI_API_KEY.
    ("xai", "XAI_API_KEY", "grok-4.7", "https://api.x.ai/v1", "openai"),
    # 2026-09-24: Moonshot Kimi K3 — open-weight multimodal MoE, 1M context.
    # OpenAI-compatible: https://api.moonshot.ai/v1, MOONSHOT_API_KEY.
    # 2026-11-10: default is now Kimi K3.1 Pro — cheaper ($2/$10 vs $3/$15
    # per 1M) and better (AA 46); K3 stays reachable as an override.
    ("moonshot", "MOONSHOT_API_KEY", "kimi-k3.1-pro", "https://api.moonshot.ai/v1", "openai"),
    # 2026-10-12: Meta Muse Wonder — frontier agentic/coding model (launched
    # Oct 12, 2026), ~1M context. OpenAI-compatible: https://api.meta.ai/v1,
    # META_API_KEY.
    ("meta", "META_API_KEY", "muse-wonder-1.0", "https://api.meta.ai/v1", "openai"),
    # 2026-10-12: Mistral Large 4 — public preview Oct 6, 1.05T MoE, 1M
    # context. OpenAI-compatible: https://api.mistral.ai/v1, MISTRAL_API_KEY.
    ("mistral", "MISTRAL_API_KEY", "mistral-large-4", "https://api.mistral.ai/v1", "openai"),
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
    # 2026-09-24: GPT-6 family takes text+image input.
    "gpt-6",
    "gemini",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    # 2026-09-24: Claude 5.x family (Fable 5.1 / Opus 5.5) takes image input.
    "claude-opus-5",
    "claude-fable-5",
    "claude-sonnet-5",
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
    # 2026-09-24: Kimi K3 has native vision; Grok 4.x takes text+image input.
    "kimi-k3",
    "grok-4",
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

# Curated fallback for api.cline.bot — the gateway HAS a public model
# catalog endpoint (GET /api/v1/models, verified 2026-11-10: 448 models).
# Sources: ClinePass docs model table (subscription), updated 2026-10-30
# — the Sept 2026 list below it was stale. ClinePass ids are the
# subscription-routed "cline-pass/..." form of the same gateway models.
_CLINEPASS_CATALOG = [
    # ── Included in the ClinePass flat subscription ($9.99/mo) — these
    # work with a $0 (even negative) credit balance; usage counts against
    # the sub quota. Reference $/1M in/out in _MODEL_PRICES (ClinePass
    # docs reference-pricing table, 2026-10-30). Cheapest first.
    "cline-pass/deepseek-v4.1-flash-1017",  # $0.15/$0.60 off-peak
    "cline-pass/deepseek-v4.1-flash",  # $0.15/$0.60 off-peak
    "cline-pass/mimo-v2.6-flash",  # $0.14/$0.28
    "cline-pass/glm-5.3-flash",  # $0.15/$0.50
    "cline-pass/minimax-m3.1-flash",  # $0.15/$0.60
    "cline-pass/muse-spark-1.3-contributor",  # $0.10/$0.20
    "cline-pass/minimax-m3",  # $0.30/$1.20 (<=512K)
    "cline-pass/mimo-v2.6-pro",  # $0.435/$0.87
    "cline-pass/qwen3.7-plus",  # $0.40/$1.60 (<=256K)
    "cline-pass/deepseek-v4-pro",  # $0.66/$1.98 off-peak
    "cline-pass/glm-5.3",  # $1.40/$4.40
    "cline-pass/kimi-k3.1-pro",  # $2.00/$10.00
    "cline-pass/qwen3.8-max",  # $2.00/$6.00
    "cline-pass/qwen3.7-max",  # $2.50/$7.50
    "cline-pass/kimi-k3",  # $3.00/$15.00
    # Legacy ids from the Sept 2026 catalog, KEPT 2026-11-10: the Oct 30
    # docs table is a pricing reference, not a serving list, and
    # cline-pass/* ids cannot be verified against the gateway listing
    # (client-side namespace — the gateway lists usage ids only), so none
    # of these is proven dead and Dylan's rule keeps them offered.
    "cline-pass/kimi-k2.7-code",
    "cline-pass/kimi-k2.6",
    "cline-pass/deepseek-v4-flash",
    "cline-pass/mimo-v2.5",
    "cline-pass/mimo-v2.5-pro",
    "cline-pass/glm-5.2",
]

# OpenCode Zen (opencode.ai) platform catalog.
# HISTORY: until 2026-09 Zen served a keyless $0 tier of third-party "-free"
# models. That tier is gone — verified 2026-09-13: /models lists only
# opencode-platform models and every chat call without OPENCODE_API_KEY
# returns 401 "Missing API key". The provider is now keyed like any cloud.
# The list below mirrors the live /models catalog — re-verified 2026-11-10
# via GET https://opencode.ai/zen/v1/models (84 models, no key needed) —
# and is only the fetch_models() fallback when the live catalog request
# fails. Zen's "-free" codename models are NOT listed: that free tier is
# OpenCode-CLI-only and refuses calls from LuckyD.
_OPENCODE_ZEN_BASE = "https://opencode.ai/zen/v1"
_OPENCODE_ZEN_DEFAULT = "gemini-3.5-flash-lite"
_ZEN_CATALOG = [
    "gemini-3.5-flash-lite",
    "gpt-5-nano",
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "claude-haiku-4-5",
    "claude-haiku-5-5",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gpt-5.1-codex-mini",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-codex",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-sonnet-5-5",
    "gpt-5.1-codex",
    "gpt-5.2-codex",
    "gpt-5.3-codex",
    "gpt-5.1-codex-max",
    "gpt-5.3-codex-spark",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
    "gpt-5.5-pro",
    "gpt-5.4-pro",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-fable-5-2",
    "gemini-4-argon",
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
    "glm-5.3",
    "glm-5.3-flash",
    "kimi-k2.6",
    "kimi-k2.7-code",
    "kimi-k3",
    "minimax-m2.5",
    "minimax-m2.7",
    "minimax-m3",
    "mistral-large-4",
    "qwen3.8-flash",
    "qwen3.8-max",
    "grok-4.6",
    "grok-4.7",
    # Muse family — verified live on Zen 2026-11-10 (models.dev + Zen
    # /models): Ember is Meta's cheapest model ($0.10/$0.50 per 1M).
    "muse-ember-1.0",
    "muse-spark-1.3",
    "muse-spark-1.2",
    "muse-wonder-1.0",
    # Removed 2026-11-10 — not in the live Zen list any more:
    # claude-sonnet-4, gemini-3-flash, gemini-3.1-pro.
]

# Curated Zen top models — the chat path rotates through these
# when the configured Zen model fails (404, unsupported, rate limit).
# These are NOT free — Zen requires OPENCODE_API_KEY since 2026-09.
# Cheapest first; every entry verified live 2026-11-10.
_ZEN_TOP_MODELS = [
    "muse-ember-1.0",  # $0.10/$0.50 per 1M — cheapest intelligence
    "gemini-3.5-flash-lite",
    "deepseek-v4.1-flash",  # $0.30/$1.20 peak
    "gpt-5-nano",
    "claude-haiku-5-5",
    "gpt-6.1-sol",  # $2/$10 per 1M
    "claude-sonnet-5-5",  # $2/$10 per 1M
    "gemini-4-argon",  # $2/$10 per 1M
    "gpt-6-astra",  # $10/$50 per 1M
    "claude-opus-5-5",  # $4/$20 per 1M — top coding quality
]
_ZEN_TOP_MODELS_SET = set(_ZEN_TOP_MODELS)

# OpenRouter free fallback — the :free chat models plus the auto free-router,
# used only when the live /models request fails. Re-verified 2026-11-10
# against GET https://openrouter.ai/api/v1/models (16 actually-free chat
# models; audio-only lyria models excluded — not chat). Kept largest-first.
_OPENROUTER_FREE_FALLBACK = [
    "openrouter/free",  # meta-router: auto-picks any available free model
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "thinkingmachines/inkling-2-small:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "cohere/north-mini-code:free",
    "liquid/lfm-2.5-2.6b:free",
    "liquid/lfm-3-1.7b:free",
    "recursive/flint-v1.0:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    # Removed 2026-11-10 — no longer free on OpenRouter: z-ai/glm-5.2:free,
    # thinkingmachines/inkling:free, inkling-small:free, nemotron-3-super,
    # poolside/laguna-s-2.1:free, laguna-xs-2.1:free.
]

# ── Curated Cline gateway top models (free/cheap first) ──
# The chat path rotates through these when the configured Cline model
# fails (404, unsupported, rate limit). Usage-billed free models first —
# they cost nothing and work with a logged-in Cline CLI session.
# 10.8.3: live-probe audit (2026-10-22, same evidence as the terminal
# catalog in main.py) — deepseek/deepseek-chat, qwen/qwen3-8b and
# kwaipilot/kat-coder-pro return upstream 404 on this gateway, so they
# can never win a rotation; removed. minimax/minimax-m2.5 leads.
# 2026-11-10: rotated stale ids to the current Pareto picks — every id
# below was in the live GET https://api.cline.bot/api/v1/models list.
_CLINE_GATEWAY_TOP_MODELS = [
    "minimax/minimax-m2.5",
    "meta/muse-ember",
    "z-ai/glm-5.3-flash",
    "deepseek/deepseek-v4.1-flash-1017",
    "cline-pass/deepseek-v4.1-flash-1017",
    "cline-pass/kimi-k3.1-pro",
    "cline-pass/glm-5.3",
    "cline-pass/claude-sonnet-4",
]
_CLINE_GATEWAY_TOP_MODELS_SET = set(_CLINE_GATEWAY_TOP_MODELS)

# Cline Usage (credit-billed / free tier) — same gateway, usage-based billing.
# Free-tier models work at $0.00 but are rate-limited; credit models deduct
# from your Cline Credits balance. Sources: live GET
# https://api.cline.bot/api/v1/models (448 models, 2026-11-10) + ClinePass
# docs reference-pricing table (2026-10-30) + OpenRouter live prices.
# 10.7.8: removed deepseek/deepseek-v4-flash and cline-free/longcat-2.0 from
# the usage gateway (real 403: "available only via Cline product surfaces");
# they are ClinePass/IDE-only ids, so the picker must not offer them here.
# 10.8.8 (2026-11-10): the 10.7.5 removal of paid Cline ids is REVERSED —
# Dylan: paid models go back in WITH their cost visible. The picker shows
# "$in/$out per 1M" next to every paid model (see _model_label in
# browser/browser_ui/ai_sidebar.py); picking one still bills Cline Credits.
_CLINE_USAGE_MODEL = "minimax/minimax-m2.5"
_CLINE_USAGE_CATALOG = [
    # ── Free tier (rate-limited, $0.00 — needs non-negative credit balance)
    # 10.8.3: pruned to the ids that survived the 2026-10-22 live-probe
    # audit of the Cline gateway (mirrors main.py _CLINE_USAGE_FREE_TIER —
    # keep the two in sync). Removed as upstream-404 dead: deepseek/
    # deepseek-chat, meta-llama/llama-3.2-3b-instruct, qwen/qwen3-8b
    # (deprecated 2026-10-09), kwaipilot/kat-coder-pro.
    # NOTE: deepseek-r1 and laguna-s-2.1:free were NOT in the gateway's
    # Nov 10 /models list — they are NOT removed here; a live re-probe
    # with Dylan's Cline session is still needed before pruning them.
    "minimax/minimax-m2.5",
    "deepseek/deepseek-r1",  # DeepSeek R1 — reasoning model
    "google/gemini-2.5-flash",  # Google Gemini free tier
    "z-ai/glm-5",  # Z-AI free tier
    "z-ai/glm-5.3-flash",  # Z-AI flash free
    "poolside/laguna-s-2.1:free",  # poolside free
    # ── Paid — Pareto frontier, cheapest first. Every id verified live
    # on the Cline gateway 2026-11-10; prices $/1M in/out (see _MODEL_PRICES).
    # AA = Artificial Analysis Intelligence Index v5 (Nov 2026).
    "meta/muse-ember",  # $0.10/$0.50 — AA 36, ~$0.14/task, 1M context
    "deepseek/deepseek-v4.1-flash-1017",  # $0.15/$0.60 off-peak, DeepSWE 77.0
    "deepseek/deepseek-v4.1-flash",  # $0.30/$1.20 peak
    "z-ai/glm-5.3",  # $1.40/$4.40 — LMArena value pick
    "moonshotai/kimi-k3.1-pro",  # $2/$10 — AA 46
    "anthropic/claude-sonnet-5.5",  # $2/$10 — AA 56, best frontier value
    "openai/gpt-6.1-sol",  # $2/$10 — ~$1.27/task
    "google/gemini-4-argon",  # $2/$10 — LMArena coding #2 area
    "anthropic/claude-opus-5.5",  # $4/$20 — AA 58, best usable flagship
    "meta/muse-wonder",  # $4/$20 — frontier agentic/coding
    "openai/gpt-6-astra",  # $10/$50 — AA 57
]
# Free tier = the six audited $0 ids above. Paid ids are in the catalog but
# NOT in the free tier — pin both sets with regression tests.
_CLINE_USAGE_FREE_TIER = frozenset(
    {
        "minimax/minimax-m2.5",
        "deepseek/deepseek-r1",
        "google/gemini-2.5-flash",
        "z-ai/glm-5",
        "z-ai/glm-5.3-flash",
        "poolside/laguna-s-2.1:free",
    }
)
# Paid Cline-gateway prices ($/1M tokens in/out) — the picker shows these
# next to the model so a credit-billed pick is never a surprise.
# Sources: ClinePass docs reference pricing (2026-10-30) for the
# cline-pass/* ids; OpenRouter live /api/v1/models (2026-11-10) for the
# usage ids (Cline ref used where the two disagree, e.g. glm-5.3).
# Off-peak shown where Cline splits peak/off-peak (V4.1 Flash, V4 Pro).
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    # ── Cline Usage paid ──
    "meta/muse-ember": (0.10, 0.50),
    "deepseek/deepseek-v4.1-flash-1017": (0.15, 0.60),
    "deepseek/deepseek-v4.1-flash": (0.30, 1.20),
    "z-ai/glm-5.3": (1.40, 4.40),
    "moonshotai/kimi-k3.1-pro": (2.00, 10.00),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
    "openai/gpt-6.1-sol": (2.00, 10.00),
    "google/gemini-4-argon": (2.00, 10.00),
    "anthropic/claude-opus-5.5": (4.00, 20.00),
    "meta/muse-wonder": (4.00, 20.00),
    "openai/gpt-6-astra": (10.00, 50.00),
    # ── ClinePass subscription reference prices (per ClinePass docs) ──
    "cline-pass/muse-spark-1.3-contributor": (0.10, 0.20),
    "cline-pass/mimo-v2.6-flash": (0.14, 0.28),
    "cline-pass/deepseek-v4.1-flash-1017": (0.15, 0.60),
    "cline-pass/deepseek-v4.1-flash": (0.15, 0.60),
    "cline-pass/glm-5.3-flash": (0.15, 0.50),
    "cline-pass/minimax-m3.1-flash": (0.15, 0.60),
    "cline-pass/minimax-m3": (0.30, 1.20),
    "cline-pass/qwen3.7-plus": (0.40, 1.60),
    "cline-pass/mimo-v2.6-pro": (0.435, 0.87),
    "cline-pass/deepseek-v4-pro": (0.66, 1.98),
    "cline-pass/glm-5.3": (1.40, 4.40),
    "cline-pass/kimi-k3.1-pro": (2.00, 10.00),
    "cline-pass/qwen3.8-max": (2.00, 6.00),
    "cline-pass/qwen3.7-max": (2.50, 7.50),
    "cline-pass/kimi-k3": (3.00, 15.00),
}
# The paid ids in the usage catalog — everything in the catalog that is
# NOT in the free tier. Pinned by tests: every paid id must have a price.
_CLINE_USAGE_PAID = frozenset(set(_CLINE_USAGE_CATALOG) - set(_CLINE_USAGE_FREE_TIER))

# Cline gateway catalog (usage-billed free tier + flat subscription):
# the picker fallback when the live /models request fails, and the rotation
# pool for the chat path. Carries the free-tier role OpenCode Zen's keyless
# catalog once had (Zen is back in the mesh but keyed-only since 2026-09).
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
        # Register OpenCode Zen only when keyed. The $0 keyless tier died
        # 2026-09 (every keyless chat call 401s), so registering it without
        # a key just burns rotation cycles on guaranteed failures.
        opencode_key = env.get("OPENCODE_API_KEY", "").strip()
        if opencode_key:
            opencode_base = env.get("OPENCODE_BASE_URL", "").strip() or _OPENCODE_ZEN_BASE
            opencode_model = env.get("OPENCODE_MODEL", "").strip() or _OPENCODE_ZEN_DEFAULT
            self._configs["opencode"] = (opencode_model, opencode_base, opencode_key, "openai")
        # LiteLLM router over the providers registered above (never raises;
        # None when the router module or the feature is unavailable).
        self._router = self._build_router(env)
        # Live free detection (10.8.8): refresh the cached free list in the
        # background (never block startup on the network). The rotation
        # reads the cache only — until the first refresh lands, the static
        # priority in core.free_rotation is the list in force.
        if _ensure_fresh_detection is not None:
            with contextlib.suppress(Exception):
                _ensure_fresh_detection(background=True)

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
        """Configured providers (registration order).

        Registration is not usability: clinepass/cline-usage register
        with an empty token so fetch_models() can fall back to the
        curated catalog. Callers that offer or select a provider use
        usable_providers() instead.
        """
        return list(self._configs)

    def usable_providers(self) -> list[str]:
        """Providers that can serve a call right now.

        Cline gateways are listed only when usable (auth exists and no
        live 402 marker); every other registered provider is usable by
        construction (locals probe at startup, keyed clouds need a key).
        This is the list the Hub switcher, the sidebar, and auto
        routing offer — a dead Cline is never offered as current or
        default, while fetch_models() keeps working from _configs.
        """
        return [name for name in self._configs if self._is_viable_provider(name)]

    def local_status(self) -> dict[str, str]:
        """Probe diagnosis per local server: ok | not_running | no_models."""
        return dict(AIBridge._local_status)

    def onboarding_status(self, recommended_model: str = "llama3.2:3b") -> dict[str, object]:
        """First-run onboarding snapshot for local Ollama + provider health."""
        import shutil

        local = self.local_status()
        ollama_state = local.get("ollama", "not_running")
        ollama_bin = shutil.which("ollama")
        def_prov = self.default_provider()
        return {
            "ready": bool(def_prov),
            "default_provider": def_prov,
            "ollama_installed": bool(ollama_bin),
            "ollama_path": ollama_bin or "",
            "ollama_state": ollama_state,
            "ollama_running": ollama_state in ("ok", "no_models"),
            "ollama_has_models": ollama_state == "ok",
            "recommended_model": recommended_model,
            "cline_credit_exhausted": self.cline_credit_exhausted(),
            "faq_topics": [
                {
                    "id": "ollama",
                    "title": "Free local AI (Ollama)",
                    "summary": f"Install Ollama from https://ollama.com and pull `{recommended_model}` for unlimited offline AI.",
                },
                {
                    "id": "smartscreen",
                    "title": "Windows SmartScreen warning",
                    "summary": "Unsigned open-source installer shows 'Unknown publisher' — click More info -> Run anyway, or use the Portable ZIP.",
                },
                {
                    "id": "halcyon",
                    "title": "Blocked installer on work PC",
                    "summary": "Corporate AV (e.g. Halcyon) may block setup exes — download LuckyDBrowser-Portable-*.zip instead (no admin needed).",
                },
                {
                    "id": "cline_zero",
                    "title": "$0.00 Cline credit balance",
                    "summary": "When Cline credits hit $0, LuckyD automatically falls back to free local Ollama or your next configured free provider.",
                },
            ],
        }

    _ollama_pull_inflight: dict[str, float] = {}

    @staticmethod
    def start_ollama_pull(model: str = "llama3.2:3b") -> dict[str, object]:
        """Start `ollama pull <model>` in the background (safe fixed argv)."""
        import shutil
        import subprocess

        clean = (model or "llama3.2:3b").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", clean):
            return {"started": False, "model": clean, "error": "invalid model name"}
        now = time.monotonic()
        last_started = AIBridge._ollama_pull_inflight.get(clean, 0.0)
        if last_started and (now - last_started) < 60.0:
            return {
                "started": True,
                "already_running": True,
                "model": clean,
                "command": f"ollama pull {clean}",
            }
        exe = shutil.which("ollama")
        if not exe:
            return {
                "started": False,
                "model": clean,
                "error": "ollama binary not found on PATH — install from https://ollama.com",
            }
        resolved = str(Path(exe).resolve())
        if not Path(resolved).is_file():
            return {"started": False, "model": clean, "error": "ollama binary is not a file"}
        try:
            proc = subprocess.Popen(
                [resolved, "pull", clean],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            AIBridge._ollama_pull_inflight[clean] = now
            return {
                "started": True,
                "model": clean,
                "pid": proc.pid,
                "command": f"ollama pull {clean}",
            }
        except Exception as exc:
            return {"started": False, "model": clean, "error": str(exc)}

    def is_opencode_zen(self, provider: str) -> bool:
        """True when the provider's endpoint is the OpenCode Zen gateway."""
        info = self._configs.get(provider)
        return bool(info and "opencode.ai" in info[1])

    def is_cline_gateway(self, provider: str) -> bool:
        """True when the provider talks to the Cline gateway (api.cline.bot).

        Covers both ClinePass (flat subscription) and Cline Usage (credit-billed
        / free tier) — they share one endpoint and one auth.
        """
        info = self._configs.get(provider)
        return bool(info and "api.cline.bot" in info[1])

    def provider_label(self, provider: str) -> str | None:
        """Endpoint-aware display name: 'OpenCode Zen' / 'Cline' vs plain 'OpenAI'."""
        if self.is_opencode_zen(provider):
            return "OpenCode Zen"
        if self.is_cline_gateway(provider):
            return "Cline"
        return None

    def _cline_usable_now(self) -> bool:
        """True when Cline can actually serve a call right now.

        Single source of truth for every surface that selects or
        offers a provider (default_provider, _is_viable_provider):
        auth must exist (API key or a logged-in Cline CLI session)
        AND no live 402 credit-exhaustion marker may be active. A
        session on disk alone is not proof — with $0 credits every
        call 402s, so the marker is part of usability.
        """
        return self._cline_usable() and not _cline_credit_exhausted()

    def _is_free_provider(self, name: str) -> bool:
        """True when a provider's route costs $0 / needs no card.

        Local keyless servers (Ollama, LM Studio) plus the router's free
        tier (Cline free tier, Gemini free tier, OpenRouter free catalog,
        Groq free tier). Everything else — ClinePass ($9.99/mo), Zen
        (keyed), Anthropic/OpenAI/DeepSeek/… (pay-per-token) — is a
        switchable option only, never auto-selected.

        Local servers are read from the LIVE _LOCAL_SPECS, not the
        __init__-time _local_names snapshot: a server registered after
        startup (post-startup local) is still a free local route.
        """
        local_ids = {spec[0] for spec in _LOCAL_SPECS}
        return name in self._local_names or name in local_ids or name in self._ROUTER_FREE_PROVIDERS

    def _auto_free_order(self) -> list[str]:
        """Free providers in live-rotation order for auto mode.

        The same live-detected, benchmark re-ranked list the terminal's
        free rotation uses (core.free_rotation.effective_free_priority —
        cached, re-probed at startup and at most daily), restricted to
        providers this bridge has registered and viable. Paid providers
        are never in this list.
        """
        pairs: list[tuple[str, str]] = []
        if _effective_free_priority is not None:
            try:
                pairs = list(_effective_free_priority())
            except Exception:
                pairs = []
        if not pairs:
            try:
                from core.free_rotation import FREE_MODEL_PRIORITY

                pairs = list(FREE_MODEL_PRIORITY)
            except Exception:
                pairs = []
        order: list[str] = []
        for provider, _model in pairs:
            if provider not in order and self._is_viable_provider(provider):
                order.append(provider)
        # Any other registered viable FREE provider not in the rotation
        # list (e.g. newly detected) — registration order, locals first.
        for name in self._configs:
            if (
                name not in order
                and self._is_free_provider(name)
                and self._is_viable_provider(name)
            ):
                order.append(name)
        return order

    def free_detection_report(self) -> str:
        """Cached live free-detection report ('' when never probed)."""
        try:
            from core.free_detection import cached_detection

            cached = cached_detection()
            return cached.report if cached else ""
        except Exception:
            return ""

    def free_fallback_note(self) -> str:
        """One-line fallback state for the sidebar status (Auto mode).

        "" when the default IS LuckyD itself (local Ollama); otherwise
        "LuckyD (offline) — using <provider> (<model>)" so the user sees
        the fallback instead of silently getting another model; and a
        no-free-provider line when Auto has nothing to use.
        """
        default = self.default_provider()
        if default is None:
            return "LuckyD (offline) — no free provider available"
        if default in self._local_names:
            return ""
        model = self.model_for(default)
        return f"LuckyD (offline) — using {default} ({model})"

    def default_provider(self) -> str | None:
        """Return the highest-priority available FREE provider.

        10.8.8 (Dylan 2026-11-10): the default is his own model — Ollama
        (luckyd:8b) when its startup probe succeeded. Otherwise the
        live-detected free rotation (same list the terminal uses:
        OpenRouter free, Gemini, Groq, Cline last). Paid providers are
        NEVER the auto default (his $0/month line): when no free route
        is usable this returns None and the UI says so — pick a provider
        explicitly (one tap) instead of silently billing.
        """
        order = self._auto_free_order()
        return order[0] if order else None

    def _cline_usable(self) -> bool:
        """True when Cline auth exists (API key or logged-in CLI session).

        clinepass / cline-usage are registered even with an empty token (for
        the model-catalog fallback), so presence alone is not proof of auth.
        """
        if self._clinepass_from_session:
            return True
        info = self._configs.get("cline-usage") or self._configs.get("clinepass")
        return bool(info and info[2])

    def cline_credit_exhausted(self) -> bool:
        """True while a Cline 402 marker steers auto-selection away (10.4).

        The sidebar uses this for the credit-exhausted provider indicator.
        """
        return _cline_credit_exhausted()

    def model_for(self, provider: str) -> str:
        info = self._configs.get(provider)
        return info[0] if info else ""

    def supports_vision(self, provider: str | None) -> bool:
        """True when the provider's current model accepts image input.

        Name-family heuristics (_VISION_HINTS); unknown models get False
        so screenshots are never sent to a text-only endpoint.
        """
        model = self.model_for(provider or "").lower()
        if not model or any(x in model for x in _VISION_EXCLUDE):
            return False
        return any(h in model for h in _VISION_HINTS)

    def is_local(self, provider: str) -> bool:
        """True for keyless local servers (callers may shrink token budgets)."""
        info = self._configs.get(provider)
        # Key check alone is not enough: session-based clinepass can hold an
        # empty placeholder token while it waits for a lazy refresh.
        return bool(info and info[2] == "" and provider in {name for name, *_ in _LOCAL_SPECS})

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

    # ── LiteLLM model router ─────────────────────────────────────────
    # Providers that count as the "free" tier of the router chain; local
    # keyless servers form the "local" tier; every other registered
    # provider is "cheap". Mirrors default_provider()'s priority notion.
    _ROUTER_FREE_PROVIDERS = frozenset({"cline-usage", "google", "openrouter", "groq"})

    def _build_router(self, env) -> Any:
        """Assemble the LiteLLM ModelRouter from config + registered providers.

        Never raises and never requires litellm: the router object is cheap
        to build, and its chat() raises RouterUnavailableError when litellm is
        absent, which chat() treats as "use the legacy chain".
        """
        if _model_router is None:
            return None
        try:
            config = _model_router.RouterConfig.from_env(env)
            if not config.enabled:
                return None
            entries = config.explicit_entries()
            for name in self._configs:
                entry = self._router_entry_for(name)
                if entry is not None:
                    entries.append(entry)
            return _model_router.ModelRouter(entries=entries, config=config)
        except Exception:
            return None

    def _router_entry_for(self, name: str) -> Any:
        """Translate one registered provider into a litellm RouteEntry.

        Returns None for providers the router must not touch: session-based
        Cline auth (its token refresh lifecycle lives in the legacy path)
        and kinds with no litellm mapping.
        """
        info = self._configs.get(name)
        if info is None or _model_router is None:
            return None
        if name in ("clinepass", "cline-usage") and self._clinepass_from_session:
            return None
        model, base_url, api_key, kind = info
        tier = (
            _model_router.TIER_LOCAL
            if name in self._local_names
            else _model_router.TIER_FREE
            if name in self._ROUTER_FREE_PROVIDERS
            else _model_router.TIER_CHEAP
        )
        if name == "ollama":
            # litellm's ollama provider wants the server root, not the
            # OpenAI-compatible /v1 base the bridge registers.
            root = base_url.removesuffix("/v1")
            return _model_router.RouteEntry(
                name=name, model=f"ollama/{model}", tier=tier, api_base=root
            )
        if name == "google":
            return _model_router.RouteEntry(
                name=name, model=f"gemini/{model}", tier=tier, api_key=api_key
            )
        if name == "anthropic":
            return _model_router.RouteEntry(
                name=name, model=f"anthropic/{model}", tier=tier, api_key=api_key
            )
        if name == "deepseek":
            return _model_router.RouteEntry(
                name=name, model=f"deepseek/{model}", tier=tier, api_key=api_key
            )
        if name in ("groq", "openrouter"):
            # The configured model id already carries litellm's provider
            # prefix (e.g. "groq/compound-mini", "openrouter/free").
            return _model_router.RouteEntry(name=name, model=model, tier=tier, api_key=api_key)
        if kind == "openai":
            # Generic OpenAI-compatible endpoint (Cline gateways, Zen, Z.ai,
            # xAI, Moonshot, Meta, Mistral, LM Studio, OpenAI itself).
            return _model_router.RouteEntry(
                name=name,
                model=f"openai/{model}",
                tier=tier,
                api_base=base_url,
                api_key=api_key,
            )
        return None

    def router_status(self) -> dict[str, Any]:
        """Queryable LiteLLM-router state for the future provider dashboard:
        enabled flag, ordered chain, per-provider health/cooldown snapshot.
        Safe to call when the router is unavailable (reports disabled)."""
        if getattr(self, "_router", None) is None:
            return {
                "enabled": False,
                "litellm_available": bool(_model_router and _model_router.litellm_available()),
                "chain": [],
                "health": {},
            }
        return self._router.status()

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
        headers = {"User-Agent": "LuckyDBrowser/10.8", "Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        configured = cfg["model"]
        if model and model != configured:
            candidates = [model, configured, *_CLINE_GATEWAY_TOP_MODELS]
        elif self.is_cline_gateway(pname):
            candidates = [configured, *[m for m in _CLINE_GATEWAY_TOP_MODELS if m != configured]]
        else:
            candidates = [configured]
        ordered = list(dict.fromkeys(m for m in candidates if m))
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
                    if resp.status_code == 402 and self.is_cline_gateway(pname):
                        # 10.4: exhausted Cline Credits — record the signal
                        # (the 402 is the only trigger; no balance API).
                        _record_cline_credit_exhausted(
                            f"HTTP 402 from {base_url} ({m}): {(resp.text or '')[:200]}"
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

        (OpenCode Zen used to be here when it had a $0 keyless tier; that tier
        died in 2026-09 and Zen is keyed-only now, so the unlimited pool is
        keyless locals only.)
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
        if self.is_opencode_zen(provider):
            # LuckyD: curated top models first, then the rest of the live
            # platform catalog — clean picker, rotation members visible.
            if models:
                live_set = set(models)
                top = [m for m in _ZEN_TOP_MODELS if m in live_set]
                rest = [m for m in models if m not in top]
                models = top + rest
            else:
                models = list(_ZEN_CATALOG)
        if not models:
            if provider == "clinepass":
                models = list(_CLINEPASS_CATALOG)
            elif provider == "cline-usage":
                models = list(_CLINE_USAGE_CATALOG)
            elif self.is_cline_gateway(provider):
                models = list(_CLINE_GATEWAY_CATALOG)
            elif self.is_opencode_zen(provider):
                models = list(_ZEN_CATALOG)
            elif provider == "openrouter":
                # Only free tier; sorted :free first for the picker
                models = list(_OPENROUTER_FREE_FALLBACK)
            else:
                models = [model]
        # Ensure the current model is present (user override may be outside top)
        if model not in models and (model in _ZEN_CATALOG or model in _CLINE_GATEWAY_CATALOG):
            # If it's a valid free model but not top, keep it visible at top
            models.insert(0, model)
        elif model in models:
            models.remove(model)
            models.insert(0, model)
        elif (
            model not in models
            and self.is_cline_gateway(provider)
            and model in _CLINE_GATEWAY_CATALOG
        ) or (model not in models and self.is_opencode_zen(provider) and model in _ZEN_CATALOG):
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
        only when a key exists. Cline entries may be registered with an
        empty token, so they need the explicit usability check — auth
        AND no live 402 marker (a $0 balance 402s every call, so a live
        marker means unusable even with a session on disk).
        """
        if self._configs.get(name) is None:
            return False
        if name in ("clinepass", "cline-usage"):
            return self._cline_usable_now()
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
        # Auto mode never routes to a paid provider (Dylan's $0/month
        # line): the router picks by task fit, not by cost, so a paid pick
        # is ignored and the free chain runs instead. An explicit provider
        # pick bypasses routing entirely.
        if not name or not self._is_free_provider(name):
            return None
        return name if self._is_viable_provider(name) else None

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
        # swallowed and replaced by a misleading "no free AI provider available".
        last_err: Exception | None = None
        if is_auto and routed is None and getattr(self, "_router", None) is not None:
            # LiteLLM router (optional): config-driven tier chain
            # (free -> cheap -> local) with per-provider cooldowns. Any
            # failure — litellm missing, router disabled, whole chain
            # down — lands here and the legacy chain below runs unchanged.
            # The router's error is deliberately NOT seeded into last_err:
            # the legacy chain owns the final verdict, so "no AI providers
            # configured" stays honest when nothing was ever viable (the
            # router's failures remain visible via router_status()).
            try:
                text, used = await self._router.chat(messages, on_token)
                return text, used
            except Exception:
                pass
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
        # Auto mode offers only FREE providers that can serve right now —
        # a dead Cline gateway (no auth / live 402 marker) is never
        # attempted, and a paid provider is never auto-picked (Dylan's
        # $0/month line): paid models stay switchable by explicit pick.
        # The order is the live-detected free rotation (same list the
        # terminal uses), locals first. An explicit provider pick uses
        # exactly that provider and skips this chain.
        order = [provider] if provider is not None else self._auto_free_order()
        if routed is not None and routed in order:
            # Routed provider goes first; the rest of the free chain follows
            # as fallback (free pool included — still reachable on failure).
            order = [routed] + [p for p in order if p != routed]
        # last_err already holds any free-pool fast-path failure (see above).
        for name in order:
            if provider is None and not self._is_viable_provider(name):
                # Auto mode never burns a call on a provider that can't work
                # (e.g. clinepass registered with an empty token) — the
                # honest end state is "no free AI provider available".
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
            if provider is not None:
                raise RuntimeError(f"provider {provider} failed — last error: {last_err}")
            raise RuntimeError(f"all free providers failed — last error: {last_err}")
        raise RuntimeError(
            "no free AI provider available — start Ollama (or `ollama pull "
            "qwen3:4b` for a starter model), or add a free-tier key "
            "(GOOGLE_API_KEY / OPENROUTER_API_KEY) to the repo .env. Paid "
            "providers work by explicit pick, never in Auto mode."
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
        headers = {"User-Agent": "LuckyDBrowser/10.8"}

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
        stream_error = None
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
                if resp.status_code == 402 and self.is_cline_gateway(name):
                    # 10.4: exhausted Cline Credits — record the signal.
                    _record_cline_credit_exhausted(f"HTTP 402 from {base_url} ({model}): {detail}")
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
                        if (
                            isinstance(data, dict)
                            and data.get("error")
                            and not data.get("choices")
                            and not data.get("candidates")
                        ):
                            # Gateway error event inside an HTTP-200 stream
                            # (e.g. api.cline.bot reporting a dead upstream
                            # model) — remember it; raised below if nothing
                            # usable arrived (10.8.2).
                            stream_error = data["error"]
                            continue
                        delta = self._extract_delta(data, kind)
                        if delta:
                            text += delta
                            if on_token:
                                on_token(delta)
                    except json.JSONDecodeError:
                        pass
        if not text and stream_error is not None:
            # Returning "" here made chat() report success with an empty
            # reply and never rotate models. Raising feeds the failure
            # into chat()'s existing per-model rotation / fallback chain.
            detail = self._stream_error_detail(stream_error)
            if "402" in detail and self.is_cline_gateway(name):
                # Same signal as an HTTP 402 (10.4): exhausted Cline Credits.
                _record_cline_credit_exhausted(f"stream error from {base_url} ({model}): {detail}")
            raise RuntimeError(f"{name} stream error: {detail}")
        return text

    @staticmethod
    def _stream_error_detail(error) -> str:
        """Human detail for a gateway in-stream error payload."""
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "").strip()
            raw = json.dumps(error, default=str)
            if not detail:
                detail = raw[:300]
        else:
            detail = str(error or "").strip()
            raw = detail
        match = re.search(r"status (\d{3})", raw)
        if match and match.group(1) not in detail:
            detail = f"HTTP {match.group(1)}: {detail}"
        return detail[:300] or "unknown stream error"

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
        body: dict[str, Any] = {"contents": parts}
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
