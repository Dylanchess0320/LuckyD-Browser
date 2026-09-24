"""Free-model auto-rotation — the $0 default provider strategy (10.4).

Dylan's standing default: every LuckyD surface (the lucky-code CLI, the
browser AI features, the assistant/backend) prefers free models and, when the
current free model fails, automatically rotates to the next best free model.
No user config is required. Explicit picks always win over this default:
``CODING_AGENT_PROVIDER``, ``--provider``, and the browser sidebar's
persisted provider are never overridden by rotation.

Default priority — best free first::

    1. Cline (free tier)  — ``cline-usage`` via the logged-in Cline CLI session
    2. Gemini (free tier) — ``gemini`` via ``GOOGLE_API_KEY``
    3. Ollama local      — ``llama3.2:3b`` (terminal fallback, always listed)
    4. Other free tiers  — OpenRouter free catalog, Groq free tier

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

import httpx

__all__ = [
    "FREE_MODEL_PRIORITY",
    "ROTATE_TRIGGER_CODES",
    "FreeModelRotator",
    "available_free_models",
    "best_free_provider",
    "free_provider_usable",
    "rotation_trigger_code",
    "should_rotate_free_model",
]

#: (provider, model) pairs in default priority order — best free first.
FREE_MODEL_PRIORITY: tuple[tuple[str, str], ...] = (
    ("cline-usage", "deepseek/deepseek-chat"),
    ("gemini", "gemini-2.5-flash"),
    ("ollama", "llama3.2:3b"),
    ("openrouter", "deepseek/deepseek-chat-v3.1:free"),
    ("groq", "groq/compound-mini"),
)

#: HTTP codes that trigger a rotation to the next free model. 401 is
#: excluded on purpose (a dead key fails everywhere — surface the error).
ROTATE_TRIGGER_CODES = frozenset({402, 403, 408, 429, 500, 502, 503, 504})


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
    return isinstance(error, httpx.TransportError)


def _ollama_reachable() -> bool:
    """True when a local Ollama server answers (the same probe
    ``core.providers.detect_provider`` performs — no new network surface)."""
    import os

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
    """First usable provider id in :data:`FREE_MODEL_PRIORITY` order.

    Used by ``core.providers.detect_provider`` so a usable $0 option always
    beats a paid key by default. Returns None when nothing free is usable.
    """
    for provider, _model in FREE_MODEL_PRIORITY:
        if free_provider_usable(provider):
            return provider
    return None


def available_free_models(
    failed: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Usable free ``(provider, model)`` pairs in priority order.

    Skips providers that aren't usable right now (no key/session, Cline
    while a 402 marker is valid) and pairs already in ``failed``. Ollama
    (``llama3.2:3b``) is always appended last as the terminal fallback —
    ``list_providers()`` treats local Ollama as always configured, and the
    agent's escape legs probe it before attempting.
    """
    failed_set = set(failed or ())
    usable: list[tuple[str, str]] = []
    for pair in FREE_MODEL_PRIORITY:
        if pair in failed_set or pair[0] == "ollama":
            continue
        if free_provider_usable(pair[0]):
            usable.append(pair)
    ollama_pair = ("ollama", "llama3.2:3b")
    if ollama_pair not in failed_set:
        usable.append(ollama_pair)
    return usable


class FreeModelRotator:
    """Walks :data:`FREE_MODEL_PRIORITY`, skipping failed/unusable pairs.

    A fresh rotator is cheap: seed it with the just-failed pair (or whole
    provider) and take ``candidates()[0]`` — or drive it with ``rotate()``
    across several failures. Ollama is always the terminal fallback.
    """

    def __init__(self, failed: Iterable[tuple[str, str]] | None = None) -> None:
        self._failed: set[tuple[str, str]] = set(failed or ())
        self._current: tuple[str, str] | None = None

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
        for p, m in FREE_MODEL_PRIORITY:
            if p == pid:
                self._failed.add((p, m))

    def candidates(self) -> list[tuple[str, str]]:
        """Usable pairs in priority order, excluding failed ones."""
        return available_free_models(self._failed)

    def rotate(self) -> tuple[str, str] | None:
        """Advance past the current pair; None when every option is exhausted."""
        if self._current is not None:
            self._failed.add(self._current)
        remaining = self.candidates()
        self._current = remaining[0] if remaining else None
        return self._current
