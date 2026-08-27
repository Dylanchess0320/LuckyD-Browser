#!/usr/bin/env python3
"""
LuckyD Code — AI coding agent with streaming and rich terminal UI.

Usage:
  lucky-code                        Interactive REPL
  lucky-code "fix the bug"          One-shot
  lucky-code --model auto --thinking
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

# Ensure the coding-agent dir is on the path
AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(AGENT_DIR))

if sys.platform == "win32":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env FIRST -- it sets CODING_AGENT_PROVIDER / *_API_KEY that
# core/providers.py reads from os.environ. Importing config before any
# other module that pulls in providers guarantees the .env provider/key win.
import config as _config  # noqa: F401  (runs load_env() on import)
from agent import CodingAgent
from config import PROJECT_DIR, get_config
from core.approval_hook import ApprovalHook
from core.hooks import get_hooks, register_plugin
from core.mcp_client import MCPManager
from core.session_store import get_session_store
from model_resolver import invalidate_cache, resolve_model
from tools.registry import registry
from ui import ui

_PROVIDER_DISPLAY_NAMES = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "ollama": "Ollama",
    "zai": "Z.ai",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "opencode": "OpenCode Zen",
    "clinepass": "ClinePass",
    "cline-usage": "Cline (usage)",
}

# ── Cline model catalogs ───────────────────────────────────────────────
# Mirror of browser/browser_core/ai_bridge.py — keep the two in sync.
# Source of truth: https://docs.cline.bot/getting-started/clinepass
# (api.cline.bot has no public model-list endpoint, so these are curated.)

# ClinePass flat-subscription models — these work with a $0 (even negative)
# credit balance; usage counts against the subscription quota.
_CLINEPASS_CATALOG = [
    "cline-pass/kimi-k3",
    "cline-pass/deepseek-v4-flash",
    "cline-pass/kimi-k2.7-code",
    "cline-pass/kimi-k2.6",
    "cline-pass/deepseek-v4-pro",
    "cline-pass/mimo-v2.5",
    "cline-pass/mimo-v2.5-pro",
    "cline-pass/minimax-m3",
    "cline-pass/qwen3.7-max",
    "cline-pass/qwen3.7-plus",
]

# Cline Usage (credit-billed / free tier) — same gateway, usage-based billing.
# Free-tier models work at $0.00 but are rate-limited; credit models deduct
# from your Cline Credits balance.
_CLINE_USAGE_CATALOG = [
    # ── Free tier (rate-limited, $0.00 — needs non-negative credit balance)
    "minimax/minimax-m2.5",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-r1",
    "meta-llama/llama-3.2-3b-instruct",
    "google/gemini-2.0-flash",
    "qwen/qwen3-8b",
    # ── Credit-billed — deduct from Cline Credits balance
    "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "mistral/mistral-large",
]

_CLINE_USAGE_FREE_TIER = frozenset(_CLINE_USAGE_CATALOG[:6])

# ClinePass has no free-tier model — every ClinePass model counts against the
# flat-subscription quota.
_CLINEPASS_FREE_TIER = frozenset()

# OpenCode Zen (opencode.ai) free catalog — every model on this gateway is
# $0. Mirror of browser/browser_core/ai_bridge.py's _OPENCODE_FREE_CATALOG —
# keep the two in sync.
_OPENCODE_FREE_CATALOG = [
    "big-pickle",
    "deepseek-v4-flash-free",
    "glm-4.7-free",
    "glm-5-free",
    "grok-code",
    "hy3-free",
    "hy3-preview-free",
    "kimi-k2.5-free",
    "laguna-s-2.1-free",
    "ling-2.6-flash-free",
    "ling-3.0-flash-free",
    "ling-3.0-tiny-free",
    "longcat-2.0-free",
    "mimo-v2-flash-free",
    "mimo-v2-omni-free",
    "mimo-v2-pro-free",
    "mimo-v2.5-free",
    "minimax-m2.1-free",
    "minimax-m2.5-free",
    "minimax-m3-free",
    "muse-spark-1.2-contributor-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "north-mini-code-free",
    "qwen3.6-plus-free",
    "ring-2.6-1t-free",
    "trinity-large-preview-free",
    "x-preview-f-free",
]

# Aliases accepted in "/model <provider> <name>" on top of canonical names.
_PROVIDER_ALIASES = {
    "cline-pass": "clinepass",
    "cline": "cline-usage",
}


def _load_free_providers() -> dict:
    """Load free model catalog from browser/models/providers_config.json."""
    try:
        path = Path(__file__).parent / "browser" / "models" / "providers_config.json"
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data.get("ai_providers", {})
    except Exception:
        return {}


def _is_free_provider_available(pid: str, pinfo: dict) -> bool:
    """Check if a provider's free models would actually work (key present or local)."""
    if pinfo.get("local"):
        # Ollama is always considered available — runs locally, no key needed
        return True
    env_key = pinfo.get("env_key")
    if not env_key:
        return False
    # For OpenCode/OpenRouter/etc, check if API key is set in current env
    # or if .env has it (via config.load_env which already ran)
    return bool(os.environ.get(env_key, "").strip())


def model_catalog(free_only: bool = False) -> list[dict]:
    """Tiered model catalog for ui.show_models() and the web /api/models panel.

    Returns a list of sections, each with a cost ``tier`` ("free" | "paid"),
    a human ``label``, and ``groups`` of {provider, models}. Free covers local
    Ollama + all $0 models from browser/models/providers_config.json.

    When ``free_only`` is True, returns only the free tier — filtered to
    providers where the free models would actually work (API key present or
    local). This powers ``/model free`` for v3.6.
    """
    cline_free = [m for m in _CLINE_USAGE_CATALOG if m in _CLINE_USAGE_FREE_TIER]
    cline_paid = [m for m in _CLINE_USAGE_CATALOG if m not in _CLINE_USAGE_FREE_TIER]
    clinepass_paid = list(_CLINEPASS_CATALOG)

    # Build free groups from providers_config.json (single source of truth for $0 models)
    free_providers = _load_free_providers()
    # "openai" entry duplicates the opencode gateway list verbatim — prefer
    # the canonical "opencode" name so the catalog says "OpenCode Zen", not
    # "OpenAI-compatible / OpenCode Zen".
    if "openai" in free_providers and "opencode" in free_providers:
        free_providers = {k: v for k, v in free_providers.items() if k != "openai"}
    free_groups: list[dict] = []
    if free_providers:
        # Sort: available providers first, then alphabetically
        def _sort_key(item: tuple[str, dict]) -> tuple[int, str]:
            pid, pinfo = item
            available = _is_free_provider_available(pid, pinfo)
            return (0 if available else 1, pid)

        seen_models: set[str] = set()
        for pid, pinfo in sorted(free_providers.items(), key=_sort_key):
            models = pinfo.get("free_models", [])
            if not models:
                continue
            # Only include free tier that is marked free_tier=true
            if not pinfo.get("free_tier"):
                continue
            # Deduplicate: "openai" entry in providers_config.json duplicates
            # the opencode gateway list verbatim — skip the alias to avoid
            # showing the same 28 models twice in the catalog.
            deduped = [m for m in models if m.lower() not in seen_models]
            # If >80% would be duplicates, this provider is an alias — skip it
            if pid == "openai" and len(deduped) < len(models) * 0.2:
                continue
            if not deduped:
                continue
            for m in deduped:
                seen_models.add(m.lower())
            # Also include Cline free tier inline if this is cline-usage alias (handled below)
            name = pinfo.get("name", pid)
            # Mark availability in provider label for UI: "✓" if key present
            available = _is_free_provider_available(pid, pinfo)
            label = f"{name} ✓" if available else f"{name} (needs {pinfo.get('env_key','key')})"
            # For free_only, skip providers where we'd need a missing key (except local)
            if free_only and not available:
                # still keep models in seen_models so they don't reappear elsewhere
                continue
            free_groups.append(
                {
                    "provider": label,
                    "models": deduped,
                    "provider_key": pid if pid != "openai" else "opencode",
                }
            )
        # Fallback to hardcoded if config gave no groups (should not happen)
        if not free_groups:
            free_groups = [
                {"provider": "Ollama ✓", "models": ["codellama", "llama3.1", "mistral", "phi3"]},
                {"provider": "OpenCode Zen (free) ✓", "models": list(_OPENCODE_FREE_CATALOG)},
                {"provider": "Cline Usage (free tier)", "models": cline_free},
            ]
    else:
        free_groups = [
            {"provider": "Ollama", "models": ["codellama", "llama3.1", "mistral", "phi3"]},
            {"provider": "OpenCode Zen (free)", "models": list(_OPENCODE_FREE_CATALOG)},
            {"provider": "Cline Usage (free tier)", "models": cline_free},
        ]

    free_section = {
        "tier": "free",
        "label": "Free — $0 ✓ = ready to use",
        "groups": free_groups,
    }
    if free_only:
        return [free_section]

    return [
        free_section,
        {
            "tier": "paid",
            "label": "Paid — costs money",
            "groups": [
                {
                    "provider": "OpenAI",
                    "models": ["gpt-4o", "gpt-4o-mini", "o1-preview", "o1-mini"],
                },
                {
                    "provider": "Anthropic",
                    "models": [
                        "claude-sonnet-4-20250514",
                        "claude-opus-4-20250514",
                        "claude-3-5-haiku-20241022",
                    ],
                },
                {
                    "provider": "Google",
                    "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
                },
                {
                    "provider": "DeepSeek",
                    "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"],
                },
                {"provider": "Z.ai", "models": ["glm-4.6", "glm-4.5", "glm-4.5-air"]},
                {
                    "provider": "OpenRouter",
                    "models": [
                        "deepseek/deepseek-chat-v3.1",
                        "anthropic/claude-sonnet-4",
                        "google/gemini-2.0-flash-001",
                    ],
                },
                {"provider": "ClinePass (subscription)", "models": clinepass_paid},
                {"provider": "Cline Usage (credit-billed)", "models": cline_paid},
            ],
        },
    ]


def _cline_model_entries() -> list[tuple[str, str]]:
    """(provider, full model id) for every curated Cline gateway model."""
    return [("clinepass", m) for m in _CLINEPASS_CATALOG] + [
        ("cline-usage", m) for m in _CLINE_USAGE_CATALOG
    ]


def _match_cline_model(desired: str) -> tuple[str, str] | None:
    """Resolve a partial model name against the Cline catalogs.

    Returns (provider, full_model_id) on a confident match. On ambiguity or
    no match, prints the reason and returns None — never guesses a provider.
    """
    desired = desired.strip().lower()
    entries = _cline_model_entries()

    exact = [e for e in entries if e[1].lower() == desired]
    if exact:
        return exact[0]

    suffix = [e for e in entries if e[1].lower().endswith("/" + desired)]
    if len(suffix) == 1:
        return suffix[0]
    candidates = suffix

    if not candidates:
        candidates = [e for e in entries if desired in e[1].lower()]

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        ui.warn(f"Ambiguous model: {desired}  ->  matches:")
        for _p, m in candidates:
            print(f"  {m}")
        print("  Re-run with the full id, e.g. /model " + candidates[0][1])
        return None

    ui.warn(f"Unknown model: {desired}")
    print("  Pick from /model (no args), or force any model with:")
    print("    /model <provider> <name>   e.g. /model cline-usage openai/gpt-4o")
    print("  Providers: openai, anthropic, google, ollama, deepseek, zai,")
    print("             openrouter, opencode, clinepass (subscription), cline-usage (free/credits)")
    return None


# ── Free-model fuzzy matching (opencode-style) ─────────────────────────


def _free_entries() -> list[tuple[str, str]]:
    """Every free model that actually works: (provider, model_id).

    Aggregates browser/models/providers_config.json + Cline free tier.
    Deduplicated by model id (first provider wins). This is the single
    source of truth for ``/model <fuzzy>``.
    """
    seen: set[str] = set()
    entries: list[tuple[str, str]] = []
    # 1) providers_config.json (opencode, openrouter, groq, zai, ollama…)
    try:
        free_providers = _load_free_providers()
        for pid, pinfo in free_providers.items():
            if not pinfo.get("free_tier"):
                continue
            models = pinfo.get("free_models", [])
            # "openai" entry duplicates opencode gateway models — fold into opencode
            provider = "opencode" if pid == "openai" else pid
            # Only map known valid providers; groq is now valid, others skip unknown
            for mid in models:
                key = mid.lower()
                if key in seen:
                    continue
                seen.add(key)
                entries.append((provider, str(mid)))
    except Exception:
        pass
    # 2) Fallback if config missing — hardcoded opencode catalog
    if not entries:
        for mid in _OPENCODE_FREE_CATALOG:
            if mid.lower() not in seen:
                seen.add(mid.lower())
                entries.append(("opencode", mid))
    # 3) Cline Usage free tier (same gateway as ClinePass but $0)
    for mid in _CLINE_USAGE_CATALOG:
        if mid in _CLINE_USAGE_FREE_TIER and mid.lower() not in seen:
            seen.add(mid.lower())
            entries.append(("cline-usage", mid))
    return entries


def _norm_model(s: str) -> str:
    """Lowercase + replace separators with spaces for token matching."""
    s = s.lower().strip()
    for ch in "-_/:.":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _fuzzy_score(query: str, model_id: str, provider: str) -> float:
    """Score 0-100 for how well query matches model_id (and provider)."""
    import difflib

    q = _norm_model(query)
    raw = model_id.lower()
    norm = _norm_model(model_id)
    prov = provider.lower()

    if not q:
        return 0.0
    # Exact / suffix wins
    if q == norm or raw == query.strip().lower():
        return 100.0
    if raw.endswith("/" + query.strip().lower()):
        return 96.0
    if q == _norm_model(provider + " " + model_id):
        return 100.0
    # Provider-qualified exact like "opencode nemotron" -> boost
    if q.startswith(prov + " "):
        rest = q[len(prov) :].strip()
        if rest and rest in norm:
            return 92.0

    # Substring in raw id
    if query.strip().lower() in raw:
        # Prefer short, prefix-ish matches
        if raw.startswith(query.strip().lower()):
            return 90.0
        if raw.endswith(query.strip().lower()):
            return 86.0
        # score penalised slightly by length distance
        return max(78.0, 88.0 - (len(raw) - len(query)) * 0.15)

    # Token containment
    q_tokens = q.split()
    if q_tokens and all(t in norm for t in q_tokens):
        # All tokens present — strong signal (e.g. "nemotron ultra")
        return 80.0 if len(q_tokens) > 1 else 76.0
    if q_tokens:
        matched = sum(1 for t in q_tokens if t in norm)
        if matched:
            base = 55.0 + matched * 9.0
            ratio = difflib.SequenceMatcher(None, q, norm).ratio()
            return base * 0.55 + ratio * 45.0

    # Pure fuzzy fallback
    ratio = difflib.SequenceMatcher(None, q, norm).ratio()
    if ratio > 0.62:
        return ratio * 78.0
    ratio2 = difflib.SequenceMatcher(None, query.strip().lower(), raw).ratio()
    return max(ratio, ratio2) * 52.0


def _fuzzy_match_free(query: str, limit: int = 8) -> list[tuple[str, str, float]]:
    """Ranked free-model matches for query. Returns [(provider, model, score)...] sorted desc."""
    entries = _free_entries()
    scored: list[tuple[str, str, float]] = []
    for prov, mid in entries:
        s = _fuzzy_score(query, mid, prov)
        if s >= 28.0:
            scored.append((prov, mid, s))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:limit]


def _resolve_free_query(query: str) -> tuple[str, str] | None:
    """Fuzzy-resolve query to a single free model. On ambiguity shows a picker.

    Returns (provider, model) on a confident match, otherwise None after
    printing disambiguation. Never guesses.
    """
    candidates = _fuzzy_match_free(query, limit=8)
    if not candidates:
        ui.warn(f"No free model matches '{query}'")
        # Suggest closest 3
        all_free = _free_entries()
        import difflib as _dif

        names = [mid for _, mid in all_free]
        close = _dif.get_close_matches(query, names, n=3, cutoff=0.45)
        if close:
            print("  Did you mean:")
            for c in close:
                print(f"    {c}  →  /model {c}")
        print("  Run /model to browse all free models.")
        return None

    # High-confidence single winner: score gap + absolute threshold
    top_score = candidates[0][2]
    second_score = candidates[1][2] if len(candidates) > 1 else 0.0
    if top_score >= 84.0 and (top_score - second_score) >= 12.0:
        return candidates[0][0], candidates[0][1]
    # Exact substring uniqueness at top
    if top_score >= 76.0 and len([c for c in candidates if c[2] >= 70.0]) == 1:
        return candidates[0][0], candidates[0][1]

    # Ambiguous — show top 5 in a compact table
    top = candidates[:5]
    if len(top) == 1:
        return top[0][0], top[0][1]
    ui.warn(f"Multiple matches for '{query}':")
    try:
        from rich import box as _box
        from rich.table import Table as _Table

        if ui.rich:
            t = _Table(
                box=_box.ROUNDED,
                show_header=True,
                header_style="dim",
                border_style="dim",
                padding=(0, 1),
            )
            t.add_column("#", justify="right", style="dim", width=3)
            t.add_column("Model", style="cyan")
            t.add_column("Provider", style="white")
            t.add_column("Score", justify="right", style="dim", width=6)
            for i, (prov, mid, sc) in enumerate(top, 1):
                t.add_row(str(i), mid, prov, f"{sc:.0f}")
            ui._console.print(t)
        else:
            raise ImportError
    except Exception:
        for i, (prov, mid, sc) in enumerate(top, 1):
            print(f"  {i}. {mid:<36} {prov}  ({sc:.0f})")
    print(f"  Pick one: /model {top[0][1]}  or  /model 1  (number from list above)")
    # If exactly 2-3 and scores close, don't auto-pick — let user choose
    return None


def _prompt_and_save_api_key(env_var: str, provider_name: str) -> str:
    """Interactively prompt the user for an API key and persist it to .env."""
    ui.warn(f"No API key found for {provider_name}.")
    print(f"  Paste your {provider_name} API key below (or press Enter to cancel):")
    print(f"    {env_var}= ", end="")
    key = sys.stdin.readline().strip()
    if not key:
        ui.error("No key provided -- cannot continue without an API key.")
        sys.exit(1)

    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    key_prefix = f"{env_var}="
    replaced = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(key_prefix) and not stripped.startswith("#"):
            new_lines.append(f"{env_var}={key}\n")
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("\n")
        new_lines.append(f"# {provider_name}\n")
        new_lines.append(f"{env_var}={key}\n")

    env_path.write_text("".join(new_lines), encoding="utf-8")
    os.environ[env_var] = key
    ui.success(f"Saved {env_var} to .env  (available now -- no restart needed)")
    return key


def _resolve_provider(provider_hint: str | None, model_name: str) -> dict:
    """Build an LLMConfig for the requested provider+model.

    If a required API key is missing and we are in an interactive terminal,
    the user will be prompted to enter one.
    """
    cfg = get_config()

    # Map provider names to their alias + env vars
    provider_map = {
        "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"),
        "google": ("GOOGLE_API_KEY", "GOOGLE_BASE_URL", "GOOGLE_MODEL"),
        "ollama": (None, "OLLAMA_HOST", "OLLAMA_MODEL"),
        "zai": ("ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL"),
        "groq": ("GROQ_API_KEY", "GROQ_BASE_URL", "GROQ_MODEL"),
        "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL"),
        "opencode": ("OPENCODE_API_KEY", "OPENCODE_BASE_URL", "OPENCODE_MODEL"),
        "clinepass": ("CLINEPASS_API_KEY", "CLINEPASS_BASE_URL", "CLINEPASS_MODEL"),
        # Same gateway + auth as ClinePass; only the model env var differs.
        "cline-usage": ("CLINEPASS_API_KEY", "CLINEPASS_BASE_URL", "CLINE_USAGE_MODEL"),
    }

    if provider_hint and provider_hint in provider_map:
        env_key, env_base, env_model = provider_map[provider_hint]
        api_key = os.environ.get(env_key, "") if env_key else ""
        # ClinePass: no key set -> try the logged-in Cline CLI session before
        # ever prompting for a raw API key (mirrors core/providers.py).
        if provider_hint in ("clinepass", "cline-usage") and not api_key:
            try:
                import sys as _sys
                from pathlib import Path as _Path

                try:
                    import cline_session  # type: ignore
                except ImportError:
                    bc = str(_Path(__file__).resolve().parent / "browser" / "browser_core")
                    if bc not in _sys.path:
                        _sys.path.insert(0, bc)
                    import cline_session  # type: ignore
                api_key = cline_session.fresh_token()
            except Exception as exc:
                ui.warn(f"ClinePass session unavailable ({exc}).")
        # If a key-required provider is still missing a key, prompt interactively
        if env_key and not api_key and sys.stdin.isatty():
            api_key = _prompt_and_save_api_key(
                env_key,
                _PROVIDER_DISPLAY_NAMES.get(provider_hint, provider_hint),
            )
        _base_defaults = {
            "clinepass": "https://api.cline.bot/api/v1",
            "cline-usage": "https://api.cline.bot/api/v1",
            "opencode": "https://opencode.ai/zen/v1",
        }
        base_url = os.environ.get(env_base, "") or _base_defaults.get(provider_hint, "")
        _model_defaults = {
            "clinepass": "cline-pass/kimi-k3",
            "cline-usage": "deepseek/deepseek-chat",
            "opencode": "nemotron-3-ultra-free",
        }
        resolved_model = (
            model_name or os.environ.get(env_model, "") or _model_defaults.get(provider_hint, "")
        )
        if not resolved_model:
            ui.warn(f"{provider_hint} model name required. Try: /model {provider_hint} <name>")
            return None
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": resolved_model,
            "provider": provider_hint,
            "thinking": False,
        }

    # No provider hint — try to detect from model name itself
    model_lower = (model_name or "").lower()
    for p in ("openai", "anthropic", "google", "ollama"):
        if model_lower.startswith(p):
            return _resolve_provider(p, model_name[len(p) + 1 :].strip())

    # Default: DeepSeek current config
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODING_AGENT_API_KEY", "")
    if not api_key and sys.stdin.isatty():
        api_key = _prompt_and_save_api_key("DEEPSEEK_API_KEY", "DeepSeek")
    return {
        "api_key": api_key,
        "base_url": cfg.get("base_url", "https://api.deepseek.com/v1"),
        "model": model_name or cfg.get("model", "deepseek-chat"),
        "provider": "deepseek",
        "thinking": cfg.get("thinking", False),
    }


def _switch_model(agent, provider: str | None = None, model_name: str = ""):
    """Switch agent to a new provider and/or model at runtime."""
    from llm import LLMConfig

    new_cfg = _resolve_provider(provider, model_name)
    if not new_cfg:
        return

    provider = new_cfg["provider"]
    model = new_cfg["model"]

    # Update the agent via its public API (no poking at internals)
    agent.switch_provider(
        LLMConfig(
            api_key=new_cfg["api_key"],
            base_url=new_cfg["base_url"],
            model=model,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            provider=provider,
            thinking=new_cfg.get("thinking", False),
        )
    )

    # Update the UI session info
    ui.set_session_info(
        project_name=(
            agent._project_info.name
            if agent._project_info and not agent._project_info.is_empty()
            else ""
        ),
        provider=agent.provider_name,
        model=model,
    )
    _persist_model_selection(provider, model)
    ui.success(f"Switched to {agent.provider_name} / {model}")


def _persist_model_selection(provider: str, model: str) -> None:
    """Persist an interactive ``/model`` switch for the next agent launch.

    Each terminal agent runs ``main.py`` from its own checkout, so writing
    that checkout's ``.env`` keeps Agent 1 and Agent 2 independent while the
    newly selected model is already active in the current request loop.
    """
    from config import ENV_FILE

    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    env_key = {
        "openai": "OPENAI_MODEL",
        "anthropic": "ANTHROPIC_MODEL",
        "google": "GOOGLE_MODEL",
        "ollama": "OLLAMA_MODEL",
        "deepseek": "CODING_AGENT_MODEL",
        "zai": "ZAI_MODEL",
        "groq": "GROQ_MODEL",
        "openrouter": "OPENROUTER_MODEL",
        "opencode": "OPENCODE_MODEL",
        "clinepass": "CLINEPASS_MODEL",
        "cline-usage": "CLINE_USAGE_MODEL",
    }.get(provider)
    if not env_key or not model:
        return
    try:
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines() if ENV_FILE.exists() else []
        values = {"CODING_AGENT_PROVIDER": provider, env_key: model}
        pending = set(values)
        rewritten = []
        for line in lines:
            key = (
                line.split("=", 1)[0].strip()
                if "=" in line and not line.lstrip().startswith("#")
                else ""
            )
            if key in values:
                rewritten.append(f"{key}={values[key]}")
                pending.discard(key)
            else:
                rewritten.append(line)
        if pending:
            if rewritten and rewritten[-1].strip():
                rewritten.append("")
            rewritten.extend(f"{key}={values[key]}" for key in sorted(pending))
        ENV_FILE.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        os.environ.update(values)
    except OSError as exc:
        ui.warn(f"Model switched for this session, but could not save it: {exc}")


# ── Slash commands ────────────────────────────────────────────────────

# ── Approval callback ───────────────────────────────────────────────


def _console_approval(request) -> type(None):
    """Prompt user for tool approval in REPL. Returns None to proceed; returning a dict blocks the tool.

    Note: When using the interactive REPL, type your response when you see the box below.
    For non-interactive use, run with --auto-approve or set CODING_AGENT_AUTO_APPROVE=1.
    """
    from core.types import ToolPermissionLevel

    preview = request.tool_args.get("command", request.tool_name)[:120]
    print(f"\n  [APPROVAL] {request.tool_name}: {preview}")
    print("  " + "=" * 57)
    print("  | This tool requires your permission to execute.            |")
    print("  |                                                           |")
    print("  |  Type:  y or yes   to Approve this call                   |")
    print("  |         n or no    to Deny this call                      |")
    print("  |         a          to Always Allow (auto-approve)         |")
    print("  " + "=" * 57)
    sys.stdout.flush()
    ans = sys.stdin.readline().strip().lower() or "n"
    if ans == "a":
        for p in get_hooks().before_tool:
            if hasattr(p, "set_permission"):
                p.set_permission(request.tool_name, ToolPermissionLevel.ALWAYS_ALLOW)
                break
        print("  > Tool auto-approved for future calls")
        return None
    if ans in ("y", "yes"):
        return None
    print("  > Tool execution denied.")
    return {
        "role": "tool",
        "tool_call_id": request.call_id,
        "content": "Tool execution denied by user.",
    }


async def handle_command(agent: CodingAgent, cmd: str) -> bool:
    """
    Handle a slash command. Returns True if the REPL should exit,
    False otherwise.
    """
    cmd = cmd[1:].strip().lower()  # strip leading '/'

    if cmd in ("q", "quit", "exit"):
        ui.goodbye()
        return True

    elif cmd in ("h", "help"):
        ui.show_help()

    elif cmd == "clear":
        agent.reset()
        # Also clear the terminal screen so you truly start fresh
        os.system("cls" if os.name == "nt" else "clear")
        project_name = (
            agent._project_info.name
            if agent._project_info and not agent._project_info.is_empty()
            else ""
        )
        ui.set_session_info(
            project_name=project_name,
            provider=agent.provider_name,
            model=getattr(agent, "model", "") or "",
        )
        ui.enhanced_banner()

    elif cmd == "history":
        ui.markdown(
            f"**Conversation:** {agent.conversation_id}\n**Turns:** {agent.turn_count}\n**Messages:** {len(agent.messages)}"
        )

    elif cmd == "tools":
        ui.show_tools(sorted(registry.list_tools()))

    elif cmd == "memory":
        try:
            from memory.store import get_memory

            ui.markdown(get_memory().summarize())
        except Exception as e:
            ui.error(f"Memory error: {e}")

    elif cmd.startswith("model"):
        # ── Professional free-model browser + fuzzy picker (opencode-style) ──
        # "/model"              → browse free catalog with numbers + interactive prompt
        # "/model free" / list  → same (explicit)
        # "/model all"          → full catalog (free + paid)
        # "/model 12"           → pick by number from free catalog
        # "/model <provider> <name>" → direct provider switch
        # "/model <fuzzy>"      → fuzzy across all free models (e.g. nemotron, kimi, qwen, spark)
        raw = cmd[len("model") :].strip()
        low = raw.lower()

        def _current_provider_name() -> str:
            try:
                return getattr(getattr(agent, "_provider_config", None), "provider", "") or ""
            except Exception:
                return ""

        def _flat_for_catalog(free_only: bool) -> dict[int, tuple[str, str]]:
            """Build the same number→(provider,model) map that ui.show_models() uses."""
            sections = model_catalog(free_only=free_only)
            flat: dict[int, tuple[str, str]] = {}
            idx = 0
            for section in sections or []:
                for group in section.get("groups", []) or []:
                    label = str(group.get("provider", ""))
                    # mirror _provider_key logic in ui.show_models
                    low_label = label.lower()
                    if "opencode" in low_label:
                        pkey = "opencode"
                    elif "openrouter" in low_label:
                        pkey = "openrouter"
                    elif "ollama" in low_label:
                        pkey = "ollama"
                    elif "openai" in low_label:
                        pkey = "opencode"
                    elif "z.ai" in low_label or low_label.strip().startswith("zai"):
                        pkey = "zai"
                    elif "groq" in low_label:
                        pkey = "groq"
                    elif "google" in low_label or "gemini" in low_label or "gemma" in low_label:
                        pkey = "google"
                    elif "cline" in low_label:
                        pkey = "cline-usage" if "usage" in low_label else "clinepass"
                    elif "deepseek" in low_label:
                        pkey = "deepseek"
                    else:
                        pkey = label.split()[0].lower() if label else "opencode"
                    if group.get("provider_key"):
                        pkey = str(group["provider_key"])
                    for m in group.get("models", []) or []:
                        idx += 1
                        flat[idx] = (pkey, str(m))
            return flat

        # ── No args → browse free catalog + interactive picker
        if not raw:
            sections = model_catalog(free_only=True)
            flat = ui.show_models(
                sections,
                current_model=getattr(agent, "model", "") or "",
                current_provider=_current_provider_name(),
            )
            # Built-in terminal picker — no separate bat file needed
            if sys.stdin.isatty():
                try:
                    choice = await asyncio.to_thread(
                        ui.prompt_text, "Select model (number / name, Enter to cancel)"
                    )
                except Exception:
                    choice = ""
                choice = (choice or "").strip()
                if not choice:
                    return False
                if choice.isdigit():
                    n = int(choice)
                    hit = flat.get(n)
                    if hit:
                        _switch_model(agent, provider=hit[0], model_name=hit[1])
                    else:
                        ui.warn(f"No model #{n} — pick 1…{len(flat)}")
                    return False
                # Text choice → try provider-prefixed first, then fuzzy
                prov = None
                des = choice
                for p in (
                    "cline-usage",
                    "cline-pass",
                    "clinepass",
                    "cline",
                    "openrouter",
                    "opencode",
                    "groq",
                    "zai",
                    "google",
                    "ollama",
                    "deepseek",
                    "openai",
                    "anthropic",
                ):
                    if choice.lower().startswith(p + " "):
                        prov = _PROVIDER_ALIASES.get(p, p)
                        des = choice[len(p) + 1 :].strip()
                        break
                if prov:
                    _switch_model(agent, provider=prov, model_name=des)
                else:
                    hit = _resolve_free_query(choice)
                    if hit is None:
                        # also try legacy cline exact (covers paid ClinePass picks)
                        hit2 = _match_cline_model(choice)
                        if hit2:
                            _switch_model(agent, provider=hit2[0], model_name=hit2[1])
                    else:
                        _switch_model(agent, provider=hit[0], model_name=hit[1])
            return False

        # Explicit browse variants
        if low in (
            "free",
            "--free",
            "free --check",
            "--free --check",
            "list",
            "free list",
            "list free",
        ):
            sections = model_catalog(free_only=True)
            flat = ui.show_models(
                sections,
                current_model=getattr(agent, "model", "") or "",
                current_provider=_current_provider_name(),
            )
            if sys.stdin.isatty():
                try:
                    choice = await asyncio.to_thread(
                        ui.prompt_text, "Select model (number / name, Enter to cancel)"
                    )
                except Exception:
                    choice = ""
                choice = (choice or "").strip()
                if choice.isdigit() and choice:
                    hit = flat.get(int(choice))
                    if hit:
                        _switch_model(agent, provider=hit[0], model_name=hit[1])
                    else:
                        ui.warn(f"No model #{choice}")
                elif choice:
                    hit = _resolve_free_query(choice)
                    if hit:
                        _switch_model(agent, provider=hit[0], model_name=hit[1])
            return False

        if low in ("all", "paid", "free all", "all free", "show all", "full"):
            ui.show_models(
                model_catalog(free_only=False),
                current_model=getattr(agent, "model", "") or "",
                current_provider=_current_provider_name(),
            )
            ui.info(
                "Tip: /model <name> fuzzy-switches free models · /model free for free-only picker"
            )
            return False

        # Numeric pick without prior browse: "/model 12"
        if low.isdigit():
            flat = _flat_for_catalog(free_only=True)
            n = int(low)
            hit = flat.get(n)
            if hit:
                _switch_model(agent, provider=hit[0], model_name=hit[1])
            else:
                ui.warn(f"No model #{n} — run /model to see 1…{len(flat)}")
                # Show catalog to help
                ui.show_models(
                    model_catalog(free_only=True),
                    current_model=getattr(agent, "model", "") or "",
                    current_provider=_current_provider_name(),
                )
            return False

        # Provider-prefixed direct switch: "/model opencode nemotron-3-ultra-free"
        provider = None
        desired = raw
        for p in (
            "cline-usage",
            "cline-pass",
            "clinepass",
            "cline",
            "openrouter",
            "opencode",
            "groq",
            "anthropic",
            "deepseek",
            "openai",
            "google",
            "ollama",
            "zai",
        ):
            if low.startswith(p + " "):
                provider = _PROVIDER_ALIASES.get(p, p)
                desired = raw[len(p) + 1 :].strip()
                break
        if provider:
            _switch_model(agent, provider=provider, model_name=desired)
            return False

        # ── Fuzzy free-model resolve (the main opencode-style path) ──
        # "/model nemotron" / "/model kimi" / "/model qwen" / "/model spark" etc.
        hit = _resolve_free_query(raw)
        if hit:
            _switch_model(agent, provider=hit[0], model_name=hit[1])
            return False
        # Fallback: legacy exact Cline match (covers subscription picks like kimi-k3)
        hit2 = _match_cline_model(raw)
        if hit2:
            _switch_model(agent, provider=hit2[0], model_name=hit2[1])

    elif cmd == "refresh":
        invalidate_cache()
        _switch_model(agent, model_name="auto")
        ui.success(f"Cache cleared. Model: {agent.model}")

    elif cmd == "save":
        save_path = PROJECT_DIR / f"conversation_{agent.conversation_id}.json"
        save_path.write_text(json.dumps(agent.messages, indent=2, default=str))
        ui.success(f"Saved to: {save_path}")

    elif cmd == "cost":
        ui.markdown(agent.cost_tracker.summary())

    elif cmd == "undo":
        from core.checkpoint import get_checkpoint_manager

        cm = get_checkpoint_manager()
        diff = cm.undo_last()
        if diff:
            adds = diff.additions
            dels = diff.deletions
            ui.success(f"Undid changes to {diff.file_path} ({adds}+ / {dels}-)")
        else:
            ui.warn("Nothing to undo")

    elif cmd == "sessions":
        sessions = get_session_store().list(limit=10)
        if not sessions:
            ui.info("No saved sessions")
        else:
            parts = []
            for s in sessions:
                sid = s.get("conversation_id", "?")
                ts = (s.get("updated_at") or "")[-19:] if s.get("updated_at") else "---"
                prev = (s.get("preview") or "")[:80]
                mod = s.get("model", "?")
                parts.append(f"  {sid}  {ts}  {mod}  | {prev}")
            ui.markdown("**Recent Sessions:**\n" + "\n".join(parts))

    elif cmd.startswith("resume"):
        parts = cmd.split(maxsplit=1)
        sid = parts[1] if len(parts) > 1 else "latest"
        sess = get_session_store().latest() if sid == "latest" else get_session_store().load(sid)
        if sess:
            agent.restore_session(sess)
            prev = (sess.get("preview") or "")[:80]
            ui.success(f"Resumed: {prev}")
        else:
            ui.error(f"Session not found: {sid}")

    elif cmd == "mcp":
        mgr = getattr(agent, "_mcp_manager", None)
        if mgr and mgr.is_connected:
            ui.markdown("**MCP Status**\n" + mgr.status_report())
        else:
            ui.warn("MCP not configured or no servers connected")

    elif cmd == "version":
        ui.info("LuckyD Code 3.6.0")

    elif cmd == "":
        pass  # Empty command

    else:
        ui.warn(f"Unknown command: /{cmd}. Try /help")

    return False


# ── Main application ──────────────────────────────────────────────────


async def run_one_shot(agent: CodingAgent, message: str):
    """Single query mode with streaming."""
    agent.stream_callback = ui.stream_token
    agent.think_callback = ui.stream_think_token
    ui.start_streaming()
    ui.start_spinner("working…")
    result = ""
    try:
        result = await agent.run(message)
    except Exception as e:
        ui.error(f"Agent error: {e}")
        result = ""
    finally:
        ui.stop_spinner()
        ui.end_streaming()
    if result and not ui.streamed_chars:
        ui.markdown(result)
    ui.play_done_sound()


async def run_one_shot_json(agent: CodingAgent, message: str):
    """Single query mode -- clean JSON-line output for editor extensions."""
    agent.stream_callback = lambda token: (
        sys.stdout.write(json.dumps({"type": "token", "text": token}) + chr(10))
    )
    agent.think_callback = lambda token: (
        sys.stdout.write(json.dumps({"type": "thinking", "text": token}) + chr(10))
    )
    result = ""
    try:
        result = await agent.run(message)
    except Exception as e:
        json.dump({"type": "error", "text": str(e)}, sys.stdout)
        sys.stdout.write(chr(10))
        sys.stdout.flush()
        return
    finally:
        sys.stdout.flush()
    if result:
        json.dump({"type": "result", "text": result[:100000]}, sys.stdout)
        sys.stdout.write(chr(10))
    json.dump({"type": "done"}, sys.stdout)
    sys.stdout.write(chr(10))
    sys.stdout.flush()


async def run_repl(agent: CodingAgent):
    """Interactive REPL with streaming and session info."""

    # Connect MCP servers lazily in this event loop
    try:
        mcp_manager = getattr(agent, "_mcp_manager", None)
        if mcp_manager:
            n_srv = await mcp_manager.connect_all()
            if n_srv:
                from tools.mcp_tools import register_mcp_tools

                n_tools = register_mcp_tools(mcp_manager)
                print(f"  [MCP] {n_srv} server(s), {n_tools} tool(s) registered")
    except Exception as e:
        print(f"  [MCP] Connection: {e}")

    # Show enhanced banner with project info
    project_name = (
        agent._project_info.name
        if agent._project_info and not agent._project_info.is_empty()
        else ""
    )
    ui.set_session_info(
        project_name=project_name,
        provider=agent.provider_name,
        model=getattr(agent, "model", "") or "",
    )
    ui.enhanced_banner()

    while True:
        try:
            user_input = await asyncio.to_thread(ui.prompt)
        except (KeyboardInterrupt, EOFError):
            cost = agent.cost_tracker.summary() if hasattr(agent, "cost_tracker") else ""
            ui.goodbye(cost_summary=cost)
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            should_exit = await handle_command(agent, user_input)
            if should_exit:
                cost = agent.cost_tracker.summary() if hasattr(agent, "cost_tracker") else ""
                ui.goodbye(cost_summary=cost)
                break
            continue

        # Normal message — stream response
        agent.stream_callback = ui.stream_token
        agent.think_callback = ui.stream_think_token
        ui.start_streaming()
        ui.start_spinner("working…")
        result = ""
        try:
            result = await agent.run(user_input)
        except Exception as e:
            ui.error(f"Agent error: {e}")
            result = ""
        finally:
            ui.stop_spinner()
            ui.end_streaming()
        if result and not ui.streamed_chars:
            ui.markdown(result)
        ui.play_done_sound()


# ── "model" CLI subcommand ─────────────────────────────────────────────


def _cli_model(args):
    """luckyd-code model [list | <fuzzy-name> | <number>]

    Professional, opencode-style free-model switcher for the CLI.

    - No args:            show current model + hint
    - "list" / "free":    browse free catalog (Panel + Table)
    - "all":              free + paid catalog
    - "<number>":         pick by number from free catalog
    - "<fuzzy>"           fuzzy — e.g. ``nemotron``, ``kimi``, ``qwen``, ``spark``
    - "<provider> <name>" direct — e.g. ``opencode nemotron-3-ultra-free``
    """
    from config import ENV_FILE
    from core.providers import PROVIDER_DEFAULTS

    def _read_env_pairs() -> dict:
        pairs = {}
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                pairs[key.strip()] = val.strip().strip('"').strip("'")
        return pairs

    def _set_env_key(lines: list[str], key: str, value: str) -> list[str]:
        """Replace (in place) or append `key=value` in a list of .env lines."""
        prefix = key + "="
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(prefix) and not stripped.startswith("#"):
                lines[idx] = f"{key}={value}"
                return lines
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")
        return lines

    def _current_model_and_provider() -> tuple[str, str]:
        pairs = _read_env_pairs()
        prov = pairs.get("CODING_AGENT_PROVIDER", "").lower()
        cur = ""
        if prov == "cline-usage" and pairs.get("CLINE_USAGE_MODEL"):
            cur = pairs["CLINE_USAGE_MODEL"]
        elif prov == "clinepass" and pairs.get("CLINEPASS_MODEL"):
            cur = pairs["CLINEPASS_MODEL"]
        elif prov and pairs.get(PROVIDER_DEFAULTS.get(prov, {}).get("env_model", "")):
            cur = pairs.get(PROVIDER_DEFAULTS[prov]["env_model"], "")
        if not cur:
            try:
                from core.providers import resolve_provider_config

                cfg = resolve_provider_config()
                cur = cfg.get("model", "unknown")
                prov = cfg.get("provider", prov)
            except Exception:
                cur = "unknown"
        return cur, prov

    current, cur_prov = _current_model_and_provider()

    # ── Browse modes (no separate bat file — built into the CLI) ─────
    if not args:
        print(f"Current: {cur_prov}/{current}\n")
        print("Usage:")
        print("  lucky-code model list              — browse free catalog (interactive)")
        print("  lucky-code model all               — browse free + paid")
        print("  lucky-code model <n>               — pick by number (e.g. model 12)")
        print(
            "  lucky-code model <name>            — fuzzy (e.g. model nemotron, model kimi, model qwen)"
        )
        print("  lucky-code model <provider> <name> — direct (e.g. model opencode grok-code)")
        print("\nTip: inside the terminal use /model — same fuzzy picker, no restart needed.")
        return

    low0 = " ".join(args).strip().lower()
    if low0 in ("list", "free", "--free", "free --check", "list free", "free list"):
        ui.show_models(
            model_catalog(free_only=True), current_model=current, current_provider=cur_prov
        )
        print("Switch: lucky-code model <name>  e.g. lucky-code model nemotron")
        return
    if low0 in ("all", "paid", "free all", "all free", "full"):
        ui.show_models(
            model_catalog(free_only=False), current_model=current, current_provider=cur_prov
        )
        return

    # ── Resolve the desired model ────────────────────────────────────
    raw_query = " ".join(args).strip()

    # Numeric pick: "12" from free catalog
    if raw_query.strip().isdigit():
        # Build flat index exactly like ui.show_models does
        sections = model_catalog(free_only=True)
        flat: dict[int, tuple[str, str]] = {}
        idx = 0
        for section in sections:
            for group in section.get("groups", []):
                label = str(group.get("provider", ""))
                low_label = label.lower()
                if "opencode" in low_label:
                    pkey = "opencode"
                elif "openrouter" in low_label:
                    pkey = "openrouter"
                elif "ollama" in low_label:
                    pkey = "ollama"
                elif "openai" in low_label:
                    pkey = "opencode"
                elif "z.ai" in low_label or low_label.strip().startswith("zai"):
                    pkey = "zai"
                elif "groq" in low_label:
                    pkey = "groq"
                elif "google" in low_label or "gemini" in low_label or "gemma" in low_label:
                    pkey = "google"
                elif "cline" in low_label:
                    pkey = "cline-usage" if "usage" in low_label else "clinepass"
                elif "deepseek" in low_label:
                    pkey = "deepseek"
                else:
                    pkey = label.split()[0].lower() if label else "opencode"
                if group.get("provider_key"):
                    pkey = str(group["provider_key"])
                for m in group.get("models", []) or []:
                    idx += 1
                    flat[idx] = (pkey, str(m))
        n = int(raw_query.strip())
        hit = flat.get(n)
        if not hit:
            print(f"No model #{n} — run: lucky-code model list  (1…{len(flat)})")
            return
        provider, picked = hit
    else:
        # Provider-prefixed direct switch
        provider = None
        picked = ""
        for p in (
            "cline-usage",
            "cline-pass",
            "clinepass",
            "cline",
            "openrouter",
            "opencode",
            "groq",
            "zai",
            "google",
            "ollama",
            "deepseek",
            "openai",
            "anthropic",
        ):
            if raw_query.lower().startswith(p + " "):
                provider = _PROVIDER_ALIASES.get(p, p)
                picked = raw_query[len(p) + 1 :].strip()
                break
        if provider:
            # direct — no fuzzy, use exactly what user typed
            pass
        else:
            # Fuzzy over the free catalog (opencode-style: knows what you want)
            hit = _resolve_free_query(raw_query)
            # Fallback to legacy exact Cline match for subscription models
            if hit is None:
                hit = _match_cline_model(raw_query)
            if not hit:
                print("Run: lucky-code model list  to browse free models")
                return
            provider, picked = hit

    # ── Persist to .env (generic — works for any provider) ──────────
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS.get("opencode"))
    env_model = defaults.get("env_model") if defaults else None

    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()

    if env_model:
        lines = _set_env_key(lines, env_model, picked)
    lines = _set_env_key(lines, "CODING_AGENT_PROVIDER", provider)

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    # Friendly tier label
    if provider == "clinepass":
        tier = "ClinePass subscription"
    elif provider == "opencode":
        tier = "OpenCode Zen — free $0"
    elif provider == "openrouter":
        tier = "OpenRouter — free"
    elif provider in ("groq", "zai", "google", "ollama"):
        tier = f"{_PROVIDER_DISPLAY_NAMES.get(provider, provider)} — free"
    elif picked in _CLINE_USAGE_FREE_TIER:
        tier = "Cline Usage — free tier"
    else:
        tier = provider

    print(f"Model set to: {picked}  [{tier} → {provider}]")
    print("Restart the terminal (or /model inside the REPL) for the change to take effect.")


# ── Entry point ────────────────────────────────────────────────────────


def main():
    # Parse CLI args
    args = sys.argv[1:]

    # Dispatch "model" subcommand early (no API key needed)
    if args and args[0] == "model":
        _cli_model(args[1:])
        return

    cfg = get_config()
    model = cfg["model"]
    temperature = cfg["temperature"]
    one_shot = ""
    resume_session_id = ""

    json_mode = False
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = resolve_model(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                preferred=args[i + 1],
                thinking=cfg.get("thinking", False),
            )
            i += 2
        elif args[i] == "--provider" and i + 1 < len(args):
            os.environ["CODING_AGENT_PROVIDER"] = args[i + 1]
            cfg = get_config()
            i += 2
        elif args[i] == "--thinking":
            os.environ["CODING_AGENT_THINKING"] = "true"
            cfg = get_config()
            model = cfg["model"]
            i += 1
        elif args[i] == "--temp" and i + 1 < len(args):
            temperature = float(args[i + 1])
            i += 2
        elif args[i] in ("-y", "--yes", "--auto-approve"):
            os.environ["CODING_AGENT_AUTO_APPROVE"] = "1"
            i += 1
        elif args[i] == "--max-turns" and i + 1 < len(args):
            os.environ["CODING_AGENT_MAX_TURNS"] = args[i + 1]
            cfg = get_config()
            i += 2
        elif args[i] in ("-c", "--continue"):
            resume_session_id = "latest"
            i += 1
        elif args[i] == "--resume" and i + 1 < len(args):
            resume_session_id = args[i + 1]
            i += 2
        elif args[i] in ("-v", "--version"):
            print("LuckyD Code 3.6.0")
            sys.exit(0)
        elif args[i] == "--help":
            print(
                """
LuckyD Code — AI Coding Agent  v3.6.0

Usage:
  lucky-code                       Interactive REPL
  lucky-code "your query"          One-shot mode
  lucky-code -c                    Continue last session
  lucky-code --resume <id>         Resume specific session

Options:
  --model NAME       Model: auto (default), flash, pro, or specific name
  --provider NAME    Set provider: deepseek, openai, anthropic, google,
                     ollama, zai, openrouter
  --thinking         Use the thinking/reasoning model (pro)
  --temp FLOAT       Temperature (default: 0.0)
  -y, --yes          Auto-approve all tool calls (non-interactive mode)
  --max-turns N      Override max agent turns (default: 30)
  -c, --continue     Resume most recent session
  --resume <id>      Resume a specific session by ID or prefix
  --json             Structured JSON-line output (for extensions)
  -v, --version      Show version
  --help             Show this help

Environment:
  <PROVIDER>_API_KEY   Set in .env for your provider
  CODING_AGENT_PROVIDER Explicit provider override
  CODING_AGENT_AUTO_APPROVE=1   Bypass all tool approvals
"""
            )
            sys.exit(0)
        elif args[i] == "--json":
            json_mode = True
            i += 1
        else:
            one_shot = " ".join(args[i:])
            break

    # Validate API key
    provider = cfg.get("provider", "deepseek")
    if provider != "ollama" and (not cfg["api_key"] or cfg["api_key"] == "sk-your-api-key-here"):
        from core.providers import PROVIDER_DEFAULTS, PROVIDER_NAMES

        provider_defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["deepseek"])
        env_var = provider_defaults.get("env_key") or (provider.upper() + "_API_KEY")
        display_name = PROVIDER_NAMES.get(provider, provider.title())

        if one_shot:
            # One-shot mode -- cannot interact; exit with instructions
            ui.error(f"{env_var} not set.")
            print(f"  Set it in {PROJECT_DIR / '.env'} or as an environment variable.")
            if provider == "deepseek":
                print("  Get a key: https://platform.deepseek.com/api_keys")
            elif provider in ("clinepass", "cline-usage"):
                print("  Run 'cline auth' to log into your Cline account, or")
                print("  set CLINEPASS_API_KEY in .env for a manual API key.")
            sys.exit(1)
        else:
            # REPL mode -- prompt the user interactively
            _prompt_and_save_api_key(env_var, display_name)
            # Re-resolve config with the new key and model
            cfg = get_config()
            model = cfg["model"]

    # Create agent
    agent = CodingAgent(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=model,
        temperature=temperature,
        max_tokens=cfg["max_tokens"],
    )

    # Wire approval hook (auto-approve all tools by default)
    hook = ApprovalHook(session_id=agent.conversation_id)
    hook.auto_approve_all = True
    register_plugin(hook)

    # Connect MCP servers (lazy: connected inside async loop when needed)
    mcp_manager = MCPManager()
    agent._mcp_manager = mcp_manager

    # Session resume
    if resume_session_id == "latest":
        sess = get_session_store().latest()
        if sess:
            agent.restore_session(sess)
            print(f"  [Session] Resumed: {(sess.get('preview') or '')[:60]}")
    elif resume_session_id:
        sess = get_session_store().load(resume_session_id)
        if sess:
            agent.restore_session(sess)
            print(f"  [Session] Resumed: {(sess.get('preview') or '')[:60]}")

    try:
        if one_shot:
            if json_mode:
                asyncio.run(run_one_shot_json(agent, one_shot))
            else:
                asyncio.run(run_one_shot(agent, one_shot))
        else:
            asyncio.run(run_repl(agent))
    finally:
        with contextlib.suppress(Exception):
            agent.save_session()
        try:
            loop = asyncio.new_event_loop()
            # Only close if there are connected clients
            if mcp_manager.is_connected:
                loop.run_until_complete(mcp_manager.close_all())
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
