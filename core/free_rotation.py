"""Free-model auto-rotation — the $0 default provider strategy (10.4).

Dylan's standing default: every LuckyD surface (the lucky-code CLI, the
browser AI features, the assistant/backend) prefers free models and, when the
current free model fails, automatically rotates to the next best free model.
No user config is required. Explicit picks always win over this default:
``CODING_AGENT_PROVIDER``, ``--provider``, and the browser sidebar's
persisted provider are never overridden by rotation.

Default priority — best free first (10.8.8, Dylan 2026-11-10)::

    1. Ollama local      — ``luckyd:8b`` — Dylan's OWN model. The default:
       private, unlimited, offline, no key or login needed. It is BOTH
       the first pick when reachable AND the terminal fallback when
       everything else fails (it may just be starting up).
    2. OpenRouter free  — ``openrouter/free`` meta-router (auto-picks any
       available $0 model — never 404s on a rotated id) + the best
       live-verified ``:free`` model (currently nemotron-3-ultra).
    3. Gemini (free tier) — ``gemini`` via ``GOOGLE_API_KEY``
    4. Groq free tier    — ``groq/compound-mini``
    5. Cline (free tier) — LAST. Cline is stopping free credits, so the
       free tier is demoted to a legacy fallback that only wins when
       nothing else is usable.

The order above is the STATIC fallback. At startup (and at most daily)
:mod:`core.free_detection` re-probes the free gateways and rebuilds the
list from what is actually being served — see
:func:`effective_free_priority`. The detected list never changes the
default (ollama/luckyd:8b) and never admits a paid route.

Rotation triggers: HTTP 402 (quota/balance exhausted), 403 (auth/entitlement
dead on this provider), 429 (rate-limited), 408, 5xx, plus timeouts and
connection errors. HTTP 401 is deliberately NOT a trigger — a dead key stays
dead on every provider, so surfacing the auth error beats burning cycles.

This module is intentionally separate from ``CodingAgent._ROTATE_CODES``
(which ``tests/test_rotation_escape.py`` pins): the agent's same-key
rotation excludes 403 because another model on the same dead key fails
identically, while cross-provider free rotation INCLUDES 403 — a dead key on
provider A says nothing about the free model on provider B.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "FREE_MODEL_PRIORITY",
    "ROTATE_TRIGGER_CODES",
    "FreeModelRotator",
    "active_pair_label",
    "available_free_models",
    "best_free_provider",
    "effective_free_priority",
    "free_provider_usable",
    "get_active_pair",
    "reset_active_pair",
    "rotation_trigger_code",
    "set_active_pair",
    "should_rotate_free_model",
]

#: (provider, model) pairs in default priority order — best free first.
#: 10.8.8 (Dylan 2026-11-10): the DEFAULT is Dylan's own model —
#: Ollama ``luckyd:8b`` leads. Cline is demoted to LAST: it is stopping
#: free credits, so its free tier is a legacy fallback only. OpenRouter's
#: ``openrouter/free`` meta-router is the cloud lead (it auto-picks any
#: available $0 model, so it never 404s when the free roster rotates).
FREE_MODEL_PRIORITY: tuple[tuple[str, str], ...] = (
    ("ollama", "luckyd:8b"),
    ("openrouter", "openrouter/free"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("gemini", "gemini-2.5-flash"),
    ("groq", "groq/compound-mini"),
    ("cline-usage", "minimax/minimax-m2.5"),
)


def effective_free_priority() -> list[tuple[str, str]]:
    """The free priority actually in force right now.

    The live-detected list (see :mod:`core.free_detection` — re-probed at
    startup, at most daily, cached on disk) when one is cached and fresh;
    otherwise the static :data:`FREE_MODEL_PRIORITY`. The detected list
    never changes the default (ollama/luckyd:8b leads it too) and never
    admits a paid route. Reads the cache only — no network on this path.
    """
    try:
        from core.free_detection import cached_detection
    except Exception:
        return list(FREE_MODEL_PRIORITY)
    try:
        cached = cached_detection()
    except Exception:
        return list(FREE_MODEL_PRIORITY)
    if cached is not None and cached.pairs:
        return list(cached.pairs)
    return list(FREE_MODEL_PRIORITY)


#: HTTP codes that trigger a rotation to the next free model. 401 is
#: excluded on purpose (a dead key fails everywhere — surface the error).
ROTATE_TRIGGER_CODES = frozenset({402, 403, 408, 429, 500, 502, 503, 504})

#: The pair currently answering (10.5 live state). Written by the LLM client
#: on every successful call and by the rotator on every rotate(); read by the
#: REPL prompt, the HQ status surfaces, and the health snapshot — never guess.
_ACTIVE_PAIR: tuple[str, str] | None = None


def set_active_pair(provider: str, model: str) -> None:
    """Record the pair that is currently answering (never raises)."""
    global _ACTIVE_PAIR
    try:
        provider = (provider or "").strip().lower()
        model = (model or "").strip()
        _ACTIVE_PAIR = (provider, model) if provider and model else None
    except Exception:
        pass


def get_active_pair() -> tuple[str, str] | None:
    """The pair most recently observed answering, or None when unknown."""
    return _ACTIVE_PAIR


def reset_active_pair() -> None:
    """Clear the live active pair (tests call this in fixtures)."""
    global _ACTIVE_PAIR
    _ACTIVE_PAIR = None


def active_pair_label() -> str:
    """``"provider/model"`` for the active pair, or "" when unknown."""
    pair = get_active_pair()
    return f"{pair[0]}/{pair[1]}" if pair else ""


def _last_working_first(
    pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Move the last-known-working pair to the front (10.5).

    Only reorders pairs already in the list — membership never changes, and
    with no record (or a record for an unusable/absent pair) the priority
    order passes through untouched. A provider-level match wins when the
    recorded model id drifted (e.g. the catalog renamed it).
    """
    if len(pairs) < 2:
        return pairs
    try:
        from core.last_working import read_last_working

        state = read_last_working()
    except Exception:
        return pairs
    if state is None:
        return pairs
    key = (state.provider, state.model)
    if key in pairs:
        return [key, *[p for p in pairs if p != key]]
    for pair in pairs:
        if pair[0] == state.provider:
            return [pair, *[p for p in pairs if p != pair]]
    return pairs


def rotation_trigger_code(error: object) -> int | None:
    """Extract an HTTP status code from an error or agent message.

    Handles ``httpx.HTTPStatusError`` (via ``.response.status_code``),
    objects carrying a ``status_code`` attribute, and the agent loop's
    ``"[API Error: <code> ...]"`` message dicts. Returns None when no code
    can be determined.
    """
    response = getattr(error, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    if isinstance(error, dict):
        try:
            head = str(error.get("content", "")).split("]", 1)[0]
            digits = "".join(c for c in head if c.isdigit())
            if len(digits) >= 3:
                return int(digits[:3])
        except Exception:
            pass
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def should_rotate_free_model(error: object) -> bool:
    """True when a failure should rotate to the next free model.

    Rotation triggers: HTTP 402/403/429, 408, 5xx, timeouts, and connection
    errors. Everything else (including 401 and unknown errors) returns False.
    """
    code = rotation_trigger_code(error)
    if code is not None:
        return code in ROTATE_TRIGGER_CODES
    # No code to inspect — timeouts and transport errors are worth a rotate.
    # (httpx.TimeoutException subclasses httpx.TransportError.)
    # Lazy: httpx costs ~0.7s to import; only needed when a call failed.
    # Fail soft when the optional dependency is absent (minimal installs):
    # an unclassifiable error is not a rotation trigger.
    try:
        import httpx
    except ImportError:
        return False

    return isinstance(error, httpx.TransportError)


def _ollama_reachable() -> bool:
    """True when a local Ollama server answers (the same probe
    ``core.providers.detect_provider`` performs — no new network surface)."""
    import os

    try:
        import httpx  # lazy: only needed when actually probing Ollama
    except ImportError:
        return False  # optional dependency absent — treat as unreachable

    try:
        host = (os.environ.get("OLLAMA_HOST", "") or "http://127.0.0.1:11434").rstrip("/")
        if host.endswith("/v1"):
            host = host[: -len("/v1")]
        r = httpx.get(f"{host}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def free_provider_usable(provider: str) -> bool:
    """True when a free-priority provider can serve requests right now.

    No network beyond the Ollama liveness probe that ``detect_provider``
    already performs. Cline is skipped while a 402 credit-exhaustion marker
    is valid (see ``core.cline_credit``).
    """
    import os

    from core.cline_credit import is_cline_credit_exhausted

    pid = (provider or "").lower()
    if pid == "cline-usage":
        if is_cline_credit_exhausted():
            return False
        try:
            from core.providers import cline_session_token

            return bool(cline_session_token())
        except Exception:
            return False
    if pid == "ollama":
        return _ollama_reachable()
    try:
        from core.providers import PROVIDER_DEFAULTS
    except Exception:
        return False
    env_key = (PROVIDER_DEFAULTS.get(pid) or {}).get("env_key")
    if not env_key:
        return False
    return bool((os.environ.get(env_key, "") or "").strip())


def best_free_provider() -> str | None:
    """First usable provider id in :func:`effective_free_priority` order.

    Used by ``core.providers.detect_provider`` so a usable $0 option always
    beats a paid key by default. Returns None when nothing free is usable.

    10.8.8: Dylan's own model is the default — Ollama wins whenever it
    can serve right now, ahead of the last-known-working preference (the
    10.5 preference still applies among the cloud fallbacks). Cline is
    last: it only wins when it is the only usable free provider left.
    """
    if free_provider_usable("ollama"):
        return "ollama"
    for provider, _model in _last_working_first(effective_free_priority()):
        if provider == "ollama":
            continue  # already checked above (unusable right now)
        if free_provider_usable(provider):
            return provider
    return None


def available_free_models(
    failed: Iterable[tuple[str, str]] | None = None,
    pairs: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Usable free ``(provider, model)`` pairs in priority order.

    Skips providers that aren't usable right now (no key/session, Cline
    while a 402 marker is valid) and pairs already in ``failed``. Ollama
    (``luckyd:8b``) is the DEFAULT — first when its probe answers — and is
    still always appended last as the terminal fallback (``list_providers``
    treats local Ollama as always configured, and the agent's escape legs
    probe it before attempting; it may just be starting up). ``pairs``
    overrides the priority list (the rotator snapshots it at construction
    so a mid-conversation cache refresh never swaps the active pick).

    10.5: the last-known-working pair moves to the front when present —
    among the cloud fallbacks only; the Ollama default stays pinned first
    when it is usable.
    """
    failed_set = set(failed or ())
    ordered = list(pairs if pairs is not None else effective_free_priority())
    ollama_pair = ("ollama", "luckyd:8b")
    usable: list[tuple[str, str]] = []
    if ollama_pair not in failed_set and free_provider_usable("ollama"):
        usable.append(ollama_pair)
    for pair in ordered:
        if pair in failed_set or pair == ollama_pair:
            continue
        if free_provider_usable(pair[0]):
            usable.append(pair)
    if ollama_pair not in failed_set and ollama_pair not in usable:
        usable.append(ollama_pair)
    if usable and usable[0] == ollama_pair:
        # Default pinned: last-working reorders only the cloud tail.
        return [usable[0], *_last_working_first(usable[1:])]
    return _last_working_first(usable)


class FreeModelRotator:
    """Walks the effective free priority, skipping failed/unusable pairs.

    A fresh rotator is cheap: seed it with the just-failed pair (or whole
    provider) and take ``candidates()[0]`` — or drive it with ``rotate()``
    across several failures. Ollama is the default (first when it can
    serve) and the terminal fallback.

    The priority list is SNAPSHOT at construction (see
    :func:`effective_free_priority`): a mid-conversation cache refresh
    from the live detector never swaps the active pick — the new list
    applies to the NEXT conversation / rotator, or when the current pair
    fails and the rotator advances.
    """

    def __init__(self, failed: Iterable[tuple[str, str]] | None = None) -> None:
        self._failed: set[tuple[str, str]] = set(failed or ())
        self._current: tuple[str, str] | None = None
        self._pairs: list[tuple[str, str]] = effective_free_priority()

    @property
    def current(self) -> tuple[str, str] | None:
        """The pair most recently returned by :meth:`rotate`."""
        return self._current

    @property
    def failed(self) -> set[tuple[str, str]]:
        """Pairs marked failed so far (a copy)."""
        return set(self._failed)

    def mark_failed(self, provider: str, model: str) -> None:
        """Record one failed ``(provider, model)`` pair — it won't be retried."""
        self._failed.add(((provider or "").lower(), model))

    def mark_provider_failed(self, provider: str) -> None:
        """Record every priority pair for a provider as failed.

        Use when the provider's auth/quota itself is dead (e.g. HTTP 403) —
        retrying it with a different model would fail identically.
        """
        pid = (provider or "").lower()
        for p, m in self._pairs:
            if p == pid:
                self._failed.add((p, m))

    def candidates(self) -> list[tuple[str, str]]:
        """Usable pairs in priority order, excluding failed ones."""
        return available_free_models(self._failed, pairs=self._pairs)

    def rotate(self) -> tuple[str, str] | None:
        """Advance past the current pair; None when every option is exhausted."""
        if self._current is not None:
            self._failed.add(self._current)
        remaining = self.candidates()
        self._current = remaining[0] if remaining else None
        # 10.5: the rotator's pick is the live answering pair.
        if self._current is not None:
            set_active_pair(self._current[0], self._current[1])
        return self._current
