"""Router-wiring tests for browser/browser_core/ai_bridge.py.

PHASE 3: in auto mode (provider None or "auto"), AIBridge.chat() consults
core/router.py's route_task() to choose the provider instead of the static
fallback chain. Rules under test:

- an explicit provider pick always wins (router never consulted);
- the router may only pick among providers the bridge already considers
  viable (no empty-token providers, no unreachable local servers);
- a non-viable router pick falls back to today's default chain exactly;
- route_task() never raises on empty/garbage input.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import ai_bridge, cline_session
from browser_core.ai_bridge import AIBridge

from core.router import ModelConfig, RoutingDecision, TaskComplexity, route_task


def _hermetic(monkeypatch: pytest.MonkeyPatch, env: dict | None = None) -> None:
    """No real .env, no localhost probes, no Cline CLI session.

    When env is None the conftest fake API keys stay visible to _load_env
    (so keyed cloud providers register as viable); pass {} for no keys.
    """
    if env is not None:
        monkeypatch.setattr(ai_bridge, "_load_env", lambda: dict(env))
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda e: {}))
    monkeypatch.setattr(cline_session, "has_session", lambda: False)


class _CallRecorder:
    """Stand-in for AIBridge._call: records provider names, always succeeds."""

    def __init__(self) -> None:
        self.names: list[str] = []

    async def __call__(self, name, info, messages, on_token):
        self.names.append(name)
        return "ok-text"


def _fake_route_task(provider_name: str, seen: dict | None = None):
    """Build a fake ai_bridge._route_task returning a fixed provider pick."""

    def _fake(request: str, context_size: int = 0) -> RoutingDecision:
        if seen is not None:
            seen["request"] = request
            seen["context_size"] = context_size
        return RoutingDecision(
            model=ModelConfig(
                name=f"test-{provider_name}", provider=provider_name, model_id="test"
            ),
            complexity=TaskComplexity.MODERATE,
            reason="test pick",
        )

    return _fake


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch) -> _CallRecorder:
    rec = _CallRecorder()
    monkeypatch.setattr(AIBridge, "_call", rec)
    return rec


def test_auto_mode_consults_router(
    monkeypatch: pytest.MonkeyPatch, recorder: _CallRecorder
) -> None:
    """Auto mode routes via core/router.py; the pick goes first."""
    _hermetic(monkeypatch)  # conftest fake keys → "openrouter" is viable
    seen: dict = {}
    monkeypatch.setattr(ai_bridge, "_route_task", _fake_route_task("openrouter", seen))

    bridge = AIBridge()
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "refactor the auth module, then add tests"},
    ]
    text, used = asyncio.run(bridge.chat(messages))

    assert text == "ok-text"
    assert used == "openrouter"
    assert recorder.names[0] == "openrouter"
    # Router saw the latest user message and a sane context size.
    assert seen["request"] == "refactor the auth module, then add tests"
    assert seen["context_size"] >= 0


def test_auto_string_is_auto_mode(monkeypatch: pytest.MonkeyPatch, recorder: _CallRecorder) -> None:
    """provider="auto" behaves exactly like provider=None."""
    _hermetic(monkeypatch)
    monkeypatch.setattr(ai_bridge, "_route_task", _fake_route_task("openrouter"))

    bridge = AIBridge()
    text, used = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))

    assert text == "ok-text"
    assert used == "openrouter"
    assert recorder.names[0] == "openrouter"


def test_explicit_provider_bypasses_router(
    monkeypatch: pytest.MonkeyPatch, recorder: _CallRecorder
) -> None:
    """An explicit pick always wins — the router must never be consulted."""

    def _boom(request: str, context_size: int = 0):  # pragma: no cover
        raise AssertionError("router must not run for an explicit provider")

    _hermetic(monkeypatch)
    monkeypatch.setattr(ai_bridge, "_route_task", _boom)

    bridge = AIBridge()
    text, used = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}], provider="google"))

    assert text == "ok-text"
    assert used == "google"
    assert recorder.names == ["google"]


def test_nonviable_router_pick_falls_back_to_default_chain(
    monkeypatch: pytest.MonkeyPatch, recorder: _CallRecorder
) -> None:
    """Router picks "anthropic" but no anthropic credential exists → today."""
    _hermetic(monkeypatch, env={})  # no keys: only Zen gateway is usable
    monkeypatch.setattr(ai_bridge, "_route_task", _fake_route_task("anthropic"))

    bridge = AIBridge()
    assert not bridge._is_viable_provider("anthropic")
    text, used = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))

    # Today's chain: free unlimited pool first → OpenCode Zen gateway.
    assert text == "ok-text"
    assert used == "opencode"
    assert recorder.names == ["opencode"]


def test_router_import_failure_keeps_today_chain(
    monkeypatch: pytest.MonkeyPatch, recorder: _CallRecorder
) -> None:
    """If the router cannot be imported, auto mode is exactly today's chain."""
    _hermetic(monkeypatch, env={})
    monkeypatch.setattr(ai_bridge, "_route_task", None)

    bridge = AIBridge()
    text, used = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))

    assert text == "ok-text"
    assert used == "opencode"
    assert recorder.names == ["opencode"]


def test_empty_token_cline_never_routed(
    monkeypatch: pytest.MonkeyPatch, recorder: _CallRecorder
) -> None:
    """clinepass registered with an empty token is not a viable router pick."""
    _hermetic(monkeypatch, env={})
    monkeypatch.setattr(ai_bridge, "_route_task", _fake_route_task("clinepass"))

    bridge = AIBridge()
    assert not bridge._is_viable_provider("clinepass")
    _text, used = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))

    assert used == "opencode"
    assert "clinepass" not in recorder.names


def test_routed_failure_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the routed provider's call fails, the rest of the chain is tried."""
    _hermetic(monkeypatch)  # conftest fake keys → clouds viable
    monkeypatch.setattr(ai_bridge, "_route_task", _fake_route_task("openrouter"))

    calls: list[str] = []

    async def _flaky(self, name, info, messages, on_token):
        calls.append(name)
        if name == "openrouter":
            raise RuntimeError("simulated 500")
        return "recovered"

    monkeypatch.setattr(AIBridge, "_call", _flaky)

    bridge = AIBridge()
    text, used = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))

    assert text == "recovered"
    assert calls[0] == "openrouter"  # routed pick tried first
    assert used != "openrouter"  # …then the fallback chain recovered
    assert used in calls[1:]
    assert len(calls) > 1


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "   \n\t  ",
        "🤖" * 5000 + "\x00\x01\x02",
        "a" * 200_000,
        "'; DROP TABLE users; --",
    ],
)
def test_route_task_handles_garbage(prompt: str) -> None:
    """The real route_task never raises on empty/garbage input."""
    decision = route_task(prompt, 0)
    assert isinstance(decision, RoutingDecision)
    assert decision.model is not None
    assert decision.model.provider


def test_route_task_handles_odd_context_sizes() -> None:
    """Negative/huge context sizes don't break routing."""
    for size in (-5, 0, 10**9):
        decision = route_task("summarize this", size)
        assert isinstance(decision, RoutingDecision)
        assert decision.complexity in TaskComplexity
