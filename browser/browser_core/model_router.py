"""LiteLLM-backed model router — config-driven fallback chain for the AI bridge.

Why this exists: the bridge (ai_bridge.py) historically hand-rolled its
provider fallback in httpx code, which made model/provider switching clunky —
changing the chain meant editing logic. This module moves the chain into
CONFIG: an ordered set of tiers (free tier models -> cheap models -> local
Ollama) that settings can reorder, extend, or disable without code changes.

Design rules (same fail-open pattern as the rest of the bridge):
  * litellm is an OPTIONAL dependency (the `router` extra in pyproject.toml).
    It is imported lazily, never at module import time. When it is absent,
    disabled, or its whole chain fails, ModelRouter raises RouterUnavailableError
    and the bridge falls back to its existing httpx chain — behaviour is
    byte-for-byte what it was before this module existed.
  * Free first, always: the default tier order preserves the standing
    free-model rotation directive (free tiers -> cheap keyed clouds -> local
    keyless servers). Config may reorder tiers, but the defaults encode it.
  * Every attempt is recorded in ProviderHealth (success/failure counts,
    consecutive failures, cooldown deadline, last error, last latency) so a
    future provider dashboard has real state to render. Failures put the
    entry on an exponentially-backed-off cooldown; a chain whose entries are
    ALL cooling down fails open and tries them in order anyway.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

TIER_FREE = "free"
TIER_CHEAP = "cheap"
TIER_LOCAL = "local"
KNOWN_TIERS = (TIER_FREE, TIER_CHEAP, TIER_LOCAL)
DEFAULT_TIER_ORDER = KNOWN_TIERS
DEFAULT_COOLDOWN_SEC = 60.0
_MAX_COOLDOWN_SEC = 900.0
_MAX_ENV_COOLDOWN_SEC = 3600.0

_TRUE_WORDS = ("1", "true", "yes", "on")
_FALSE_WORDS = ("0", "false", "no", "off")


class RouterUnavailableError(RuntimeError):
    """The router cannot serve this call — the bridge must use its own chain."""


# ── Lazy litellm loading ─────────────────────────────────────────────────
# Importing litellm pulls a large dependency tree; the browser must never pay
# that cost (or break) when the optional extra is not installed.
_litellm_module: Any = None
_litellm_checked = False


def _load_litellm() -> Any:
    """Import litellm once, lazily. Returns the module or None. Never raises."""
    global _litellm_module, _litellm_checked
    if not _litellm_checked:
        _litellm_checked = True
        try:
            import litellm

            _litellm_module = litellm
        except Exception:
            _litellm_module = None
    return _litellm_module


def litellm_available() -> bool:
    """True when the optional litellm dependency is importable."""
    return _load_litellm() is not None


# ── Config ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouteEntry:
    """One hop in the fallback chain.

    ``name`` is the health/dashboard key and the provider name returned to
    the caller (bridge provider names for auto-built entries, the model id
    itself for explicit config entries). ``model`` is a litellm model id.
    ``api_base``/``api_key`` are passed through to litellm for
    OpenAI-compatible endpoints; empty means litellm resolves them itself.
    """

    name: str
    model: str
    tier: str
    api_base: str = ""
    api_key: str = ""


def _parse_bool(raw: str, default: bool) -> bool:
    word = (raw or "").strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return default


def _parse_cooldown(raw: str) -> float:
    try:
        value = float((raw or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_SEC
    if value <= 0:
        return DEFAULT_COOLDOWN_SEC
    return min(value, _MAX_ENV_COOLDOWN_SEC)


def _parse_tier_order(raw: str) -> tuple[str, ...]:
    order: list[str] = []
    for part in (raw or "").split(","):
        tier = part.strip().lower()
        if tier in KNOWN_TIERS and tier not in order:
            order.append(tier)
    return tuple(order) if order else DEFAULT_TIER_ORDER


def _parse_model_list(raw: str) -> tuple[str, ...]:
    return tuple(m.strip() for m in (raw or "").split(",") if m.strip())


@dataclass
class RouterConfig:
    """Router settings, parsed from the bridge's env/.env mapping.

    Keys (all optional):
      LUCKYD_MODEL_ROUTER          master switch, default on ("off" bypasses)
      LUCKYD_ROUTER_CHAIN          tier order, default "free,cheap,local"
      LUCKYD_ROUTER_COOLDOWN_SEC   base failure cooldown, default 60
      LUCKYD_ROUTER_FREE_MODELS    explicit litellm model ids for the free tier
      LUCKYD_ROUTER_CHEAP_MODELS   explicit litellm model ids for the cheap tier
      LUCKYD_ROUTER_LOCAL_MODELS   explicit litellm model ids for the local tier
    """

    enabled: bool = True
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC
    tier_order: tuple[str, ...] = DEFAULT_TIER_ORDER
    free_models: tuple[str, ...] = ()
    cheap_models: tuple[str, ...] = ()
    local_models: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RouterConfig:
        return cls(
            enabled=_parse_bool(env.get("LUCKYD_MODEL_ROUTER", ""), True),
            cooldown_sec=_parse_cooldown(env.get("LUCKYD_ROUTER_COOLDOWN_SEC", "")),
            tier_order=_parse_tier_order(env.get("LUCKYD_ROUTER_CHAIN", "")),
            free_models=_parse_model_list(env.get("LUCKYD_ROUTER_FREE_MODELS", "")),
            cheap_models=_parse_model_list(env.get("LUCKYD_ROUTER_CHEAP_MODELS", "")),
            local_models=_parse_model_list(env.get("LUCKYD_ROUTER_LOCAL_MODELS", "")),
        )

    def explicit_entries(self) -> list[RouteEntry]:
        """Entries declared directly in settings (litellm resolves their keys)."""
        entries: list[RouteEntry] = []
        for tier, models in (
            (TIER_FREE, self.free_models),
            (TIER_CHEAP, self.cheap_models),
            (TIER_LOCAL, self.local_models),
        ):
            entries.extend(RouteEntry(name=m, model=m, tier=tier) for m in models)
        return entries


# ── Provider health / cooldown state ─────────────────────────────────────


@dataclass
class _HealthEntry:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    last_latency_sec: float = 0.0


class ProviderHealth:
    """In-memory per-provider health: counters + cooldown deadlines.

    Queryable via snapshot() — the seed state for the provider dashboard.
    ``clock`` is injectable so tests can control time without sleeping.
    """

    def __init__(
        self,
        cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
        clock: Any = time.monotonic,
    ) -> None:
        self._cooldown_sec = cooldown_sec
        self._clock = clock
        self._entries: dict[str, _HealthEntry] = {}

    def _entry(self, name: str) -> _HealthEntry:
        entry = self._entries.get(name)
        if entry is None:
            entry = _HealthEntry()
            self._entries[name] = entry
        return entry

    def record_success(self, name: str, latency_sec: float = 0.0) -> None:
        entry = self._entry(name)
        entry.successes += 1
        entry.consecutive_failures = 0
        entry.cooldown_until = 0.0
        entry.last_error = ""
        entry.last_latency_sec = latency_sec

    def record_failure(self, name: str, error: Any) -> None:
        entry = self._entry(name)
        entry.failures += 1
        entry.consecutive_failures += 1
        # Exponential backoff per consecutive failure, capped — a provider
        # that 429s once is retried soon; one that keeps failing is parked.
        backoff = min(
            self._cooldown_sec * (2 ** (entry.consecutive_failures - 1)),
            _MAX_COOLDOWN_SEC,
        )
        entry.cooldown_until = self._clock() + backoff
        entry.last_error = str(error)[:200]

    def cooldown_remaining(self, name: str) -> float:
        entry = self._entries.get(name)
        if entry is None or entry.cooldown_until <= 0.0:
            return 0.0
        return max(0.0, entry.cooldown_until - self._clock())

    def in_cooldown(self, name: str) -> bool:
        return self.cooldown_remaining(name) > 0.0

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """JSON-friendly copy of all recorded health state."""
        return {
            name: {
                "successes": entry.successes,
                "failures": entry.failures,
                "consecutive_failures": entry.consecutive_failures,
                "cooldown_remaining_sec": round(self.cooldown_remaining(name), 3),
                "last_error": entry.last_error,
                "last_latency_sec": round(entry.last_latency_sec, 3),
            }
            for name, entry in self._entries.items()
        }


# ── The router ───────────────────────────────────────────────────────────


def _dig(obj: Any, key: str) -> Any:
    """One level of dict-or-attribute access (litellm returns objects, tests
    and some providers hand back plain dicts)."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_text(response: Any) -> str:
    choices = _dig(response, "choices") or []
    if not choices:
        return ""
    message = _dig(choices[0], "message")
    content = _dig(message, "content") if message is not None else None
    if isinstance(content, list):
        return "".join(
            str(_dig(part, "text") or "") if not isinstance(part, str) else part for part in content
        )
    return str(content or "")


def _extract_delta(chunk: Any) -> str:
    choices = _dig(chunk, "choices") or []
    if not choices:
        return ""
    delta = _dig(choices[0], "delta")
    content = _dig(delta, "content") if delta is not None else None
    return str(content or "")


@dataclass
class ModelRouter:
    """Tries chain entries in tier order via litellm until one answers."""

    entries: list[RouteEntry] = field(default_factory=list)
    config: RouterConfig = field(default_factory=RouterConfig)
    health: ProviderHealth = field(init=False)

    def __post_init__(self) -> None:
        self.health = ProviderHealth(self.config.cooldown_sec)

    def ordered_entries(self) -> list[RouteEntry]:
        """Entries sorted by configured tier order (stable within a tier)."""
        rank = {tier: idx for idx, tier in enumerate(self.config.tier_order)}
        return sorted(self.entries, key=lambda e: rank.get(e.tier, len(rank)))

    def chain_names(self) -> list[str]:
        return [entry.name for entry in self.ordered_entries()]

    def status(self) -> dict[str, Any]:
        """Queryable router state — enabled flag, chain, per-provider health."""
        return {
            "enabled": self.config.enabled,
            "litellm_available": litellm_available(),
            "chain": self.chain_names(),
            "health": self.health.snapshot(),
        }

    async def chat(self, messages: list, on_token: Any = None) -> tuple[str, str]:
        """Run the chain. Returns (text, entry name).

        Raises RouterUnavailableError when the router is disabled, litellm is not
        installed, the chain is empty, or every entry failed — the caller
        (the bridge) then runs its legacy chain unchanged.
        """
        if not self.config.enabled:
            raise RouterUnavailableError("model router disabled by LUCKYD_MODEL_ROUTER")
        litellm = _load_litellm()
        if litellm is None:
            raise RouterUnavailableError("litellm is not installed (optional router extra)")
        ordered = self.ordered_entries()
        if not ordered:
            raise RouterUnavailableError("model router chain is empty")
        # Skip cooling-down entries; if ALL are cooling down, fail open and
        # try the full order anyway (a stale cooldown must never mute the AI).
        active = [e for e in ordered if not self.health.in_cooldown(e.name)]
        if not active:
            active = ordered
        last_err: Exception | None = None
        for entry in active:
            started = time.monotonic()
            try:
                text = await self._complete(litellm, entry, messages, on_token)
            except Exception as exc:
                self.health.record_failure(entry.name, exc)
                last_err = exc
                continue
            if not text.strip():
                self.health.record_failure(entry.name, "empty response")
                last_err = RouterUnavailableError(f"{entry.name}: empty response")
                continue
            self.health.record_success(entry.name, time.monotonic() - started)
            return text, entry.name
        raise RouterUnavailableError(f"model router chain exhausted: {last_err}")

    async def _complete(
        self, litellm: Any, entry: RouteEntry, messages: list, on_token: Any
    ) -> str:
        kwargs: dict[str, Any] = {"model": entry.model, "messages": messages}
        if entry.api_base:
            kwargs["api_base"] = entry.api_base
        if entry.api_key:
            kwargs["api_key"] = entry.api_key
        if on_token is None:
            response = await litellm.acompletion(**kwargs)
            return _extract_text(response)
        kwargs["stream"] = True
        stream = await litellm.acompletion(**kwargs)
        text = ""
        async for chunk in stream:
            delta = _extract_delta(chunk)
            if delta:
                text += delta
                on_token(delta)
        return text
