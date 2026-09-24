"""B2: CDP remote debugging is a real setting (default on, documented).

The agent's CDP driver + GPU-safe screenshots need
QTWEBENGINE_REMOTE_DEBUGGING at engine startup, and it only ever binds
loopback (same trust domain as the control server) — so the default
stays ON. But it is now a first-class setting with an env override and
docs, instead of a hardcoded setdefault, and /status reports the
effective endpoint instead of a constant.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.settings import (
    CDP_DEBUG_ENDPOINT,
    DEFAULTS,
    cdp_debugging_endpoint,
)


class TestCdpToggle:
    def test_default_is_on(self):
        assert DEFAULTS["cdp_debugging"] is True
        assert cdp_debugging_endpoint() == CDP_DEBUG_ENDPOINT
        assert CDP_DEBUG_ENDPOINT == "127.0.0.1:9222"

    def test_setting_disables(self):
        assert cdp_debugging_endpoint({"cdp_debugging": False}) is None
        assert cdp_debugging_endpoint({"cdp_debugging": True}) == CDP_DEBUG_ENDPOINT

    def test_env_override_wins_both_ways(self):
        assert cdp_debugging_endpoint({"cdp_debugging": True}, {"LUCKYD_CDP_DEBUG": "0"}) is None
        assert (
            cdp_debugging_endpoint({"cdp_debugging": False}, {"LUCKYD_CDP_DEBUG": "1"})
            == CDP_DEBUG_ENDPOINT
        )

    def test_settings_store_object(self, tmp_path):
        from browser_core.settings import SettingsStore

        store = SettingsStore(tmp_path / "settings.json")
        assert cdp_debugging_endpoint(store) == CDP_DEBUG_ENDPOINT
        store.set("cdp_debugging", False)
        assert cdp_debugging_endpoint(store) is None
