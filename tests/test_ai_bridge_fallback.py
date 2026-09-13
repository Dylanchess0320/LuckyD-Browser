"""Fallback-default regression tests for browser/browser_core/ai_bridge.py.

HISTORY: these tests once asserted OpenCode Zen as a keyless $0 fallback.
That tier died 2026-09 (verified: every keyless Zen chat call 401s), so
Zen now registers ONLY with OPENCODE_API_KEY. With no local server, no
keys, and no Cline auth there is simply no provider — the dashboard says
so honestly instead of routing into a guaranteed 401.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import (
    ai_bridge,
    cline_session,
)
from browser_core.ai_bridge import AIBridge


@pytest.fixture()
def no_local_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate: no Ollama/LM Studio reachable, no provider keys, no Cline login."""
    # Hermetic env: AIBridge reads this instead of the real .env/os.environ.
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: {})
    # Never probe localhost (keeps the test fast and offline).
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: {}))
    # No Cline CLI session on disk in this fixture.
    monkeypatch.setattr(cline_session, "has_session", lambda: False)


def test_fallback_default_is_opencode_zen(
    no_local_no_keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With OPENCODE_API_KEY set → Zen gateway is the default."""
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: {"OPENCODE_API_KEY": "zk-test"})
    bridge = AIBridge()
    assert bridge.default_provider() == "opencode"


def test_no_key_no_zen_registered(no_local_no_keys) -> None:
    """Without OPENCODE_API_KEY, Zen must NOT register (keyless 401s)."""
    bridge = AIBridge()
    assert "opencode" not in bridge.providers()
    assert bridge.default_provider() is None


def test_fallback_default_never_empty_token_clinepass(no_local_no_keys) -> None:
    """clinepass with an empty token must not be the chosen default."""
    bridge = AIBridge()
    default = bridge.default_provider()
    assert default != "clinepass"
    # No provider at all in this fixture — nothing may run keyless except
    # local servers (none here).
    assert default is None


def test_opencode_zen_registered_when_keyed(
    no_local_no_keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Zen gateway registers when OPENCODE_API_KEY is set."""
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: {"OPENCODE_API_KEY": "zk-test"})
    bridge = AIBridge()
    model, base, key, kind = bridge._configs["opencode"]
    assert "opencode.ai" in base
    assert key == "zk-test"
    assert kind == "openai"  # OpenAI-compatible endpoint
    assert model  # a default platform model is pinned
    assert model in ai_bridge._ZEN_CATALOG


def test_local_server_still_beats_zen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reachable Ollama keeps priority #1 over the Zen gateway."""
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: {})
    monkeypatch.setattr(
        AIBridge,
        "_detect_local",
        staticmethod(lambda env: {"ollama": ("llama3", "http://localhost:11434/v1", "", "openai")}),
    )
    bridge = AIBridge()
    assert bridge.default_provider() == "ollama"
