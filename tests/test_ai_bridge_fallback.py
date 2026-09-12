"""Fallback-default regression tests for browser/browser_core/ai_bridge.py.

QUICK-WIN FIX 2: when no Ollama/LM Studio server is reachable, no provider
keys are configured, and Cline auth is absent, the default provider must be
the OpenCode Zen $0 free gateway ("opencode", key optional) — never
"clinepass" registered with an empty token (a dead end).
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


def test_fallback_default_is_opencode_zen(no_local_no_keys) -> None:
    """With no local server, no keys, no Cline auth → Zen gateway."""
    bridge = AIBridge()
    assert bridge.default_provider() == "opencode"


def test_fallback_default_never_empty_token_clinepass(no_local_no_keys) -> None:
    """clinepass with an empty token must not be the chosen default."""
    bridge = AIBridge()
    default = bridge.default_provider()
    assert default != "clinepass"
    token = bridge._configs[default][2]  # pyright: ignore[reportPrivateUsage]
    # Zen is the only provider allowed to run keyless.
    assert default == "opencode" or token


def test_opencode_zen_registered_keyless(no_local_no_keys) -> None:
    """The Zen gateway is registered with no API key required."""
    bridge = AIBridge()
    model, base, key, kind = bridge._configs["opencode"]
    assert "opencode.ai" in base
    assert key == ""  # key optional — still usable
    assert kind == "openai"  # OpenAI-compatible endpoint
    assert model  # a default free model is pinned


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
