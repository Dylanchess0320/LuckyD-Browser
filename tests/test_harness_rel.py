"""B3: frozen-safe harness spawn + unique swarm run ids.

- ``HarnessBridge.start`` ran a ``.py`` launcher through
  ``sys.executable`` — under a frozen browser that is the browser exe
  itself, not Python. A resolver picks a real interpreter (or fails
  with a clear error) instead of spawning garbage.
- Swarm run ids had one-second resolution, so back-to-back runs could
  share an id and cross-route stale worker events. Ids now carry a
  unique suffix (single-flight rejection is unchanged by design).
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

from browser_core.harness_bridge import _python_executable
from browser_core.research_page import SwarmManager


class TestPythonResolver:
    def test_source_uses_sys_executable(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert _python_executable() == sys.executable

    def test_frozen_env_override(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("LUCKYD_PYTHON", "C:/py/python.exe")
        assert _python_executable() == "C:/py/python.exe"

    def test_frozen_falls_back_to_path(self, monkeypatch):
        import browser_core.harness_bridge as hb

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delenv("LUCKYD_PYTHON", raising=False)
        monkeypatch.setattr(
            hb.shutil, "which", lambda name: "/x/python3" if name == "python3" else None
        )
        assert _python_executable() == "/x/python3"

    def test_frozen_without_python_raises(self, monkeypatch):
        import browser_core.harness_bridge as hb

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delenv("LUCKYD_PYTHON", raising=False)
        monkeypatch.setattr(hb.shutil, "which", lambda name: None)
        try:
            _python_executable()
        except FileNotFoundError as exc:
            assert "LUCKYD_PYTHON" in str(exc)
        else:
            raise AssertionError("expected FileNotFoundError")


class TestUniqueRunIds:
    def test_sequential_runs_get_unique_ids(self, monkeypatch):
        import browser_core.research_page as rp

        # Freeze the clock: without a unique suffix both runs would share
        # an id even across seconds.
        monkeypatch.setattr(rp.time, "strftime", lambda fmt: "20200101-000000")
        mgr = SwarmManager()
        id1 = mgr.start_research("first?", provider="mock", dry_run=True)
        assert mgr.wait_for_completion(timeout=120)
        id2 = mgr.start_research("second?", provider="mock", dry_run=True)
        assert mgr.wait_for_completion(timeout=120)
        assert id1.startswith("run-") and id2.startswith("run-")
        assert id1 != id2
