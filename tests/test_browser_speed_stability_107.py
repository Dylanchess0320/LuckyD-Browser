"""v10.7 speed + stability: lazy session restore, write coalescing, WAL.

Covers the Qt-free contracts behind the 10.7 performance pass:

* ``plan_restore`` — only the active restored tab loads eagerly; every
  other tab is deferred until first shown (startup no longer spawns a
  Chromium renderer per restored tab).
* ``effective_url`` — a deferred tab's pending URL wins over its live
  ``about:blank`` so snapshots/duplicate/copy never lose it.
* ``SessionStore.save`` — an unchanged snapshot performs no disk I/O.
* ``SettingsStore.set`` — writing the value already stored performs no
  disk I/O, and saves stage through a unique tmp file.
* ``Storage`` — history/bookmarks DB runs in WAL mode so per-page-load
  history writes stop fsync-stalling the GUI thread.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.session import SessionStore, effective_url, plan_restore
from browser_core.settings import SettingsStore
from browser_core.storage import Storage

TABS = [
    {"url": "https://a.example/1", "title": "A", "pinned": False},
    {"url": "https://b.example/2", "title": "B", "pinned": True},
    {"url": "https://c.example/3", "title": "C", "pinned": False},
]


# ── lazy restore planning ────────────────────────────────────────────


def test_plan_restore_loads_only_active_tab_eagerly() -> None:
    plan, current = plan_restore(TABS, current=1)
    assert current == 1
    assert [entry["load_now"] for entry in plan] == [False, True, False]
    assert [entry["url"] for entry in plan] == [t["url"] for t in TABS]
    # Saved titles/pins ride along so deferred tabs still look right.
    assert plan[0]["title"] == "A"
    assert plan[1]["pinned"] is True


def test_plan_restore_clamps_current_and_defaults_to_first() -> None:
    plan, current = plan_restore(TABS, current=99)
    assert current == len(TABS) - 1
    assert plan[current]["load_now"] is True
    plan, current = plan_restore(TABS, current=-5)
    assert current == 0
    assert plan[0]["load_now"] is True


def test_plan_restore_drops_unrestorable_records() -> None:
    tabs = [
        *TABS,
        {"url": "about:blank", "title": "blank"},
        {"title": "no url at all"},
        "not-a-dict",
    ]
    plan, current = plan_restore(tabs, current=2)
    assert [entry["url"] for entry in plan] == [t["url"] for t in TABS]
    assert current == 2
    assert plan_restore([], 0) == ([], 0)
    assert plan_restore([{"url": "about:blank"}], 0) == ([], 0)


def test_effective_url_prefers_pending() -> None:
    assert effective_url("https://real.example/", "about:blank") == "https://real.example/"
    assert effective_url("", "https://live.example/") == "https://live.example/"
    assert effective_url(None, "https://live.example/") == "https://live.example/"
    assert effective_url("", "") == ""


# ── session write coalescing ─────────────────────────────────────────


def _windows(url: str) -> list[dict]:
    return [{"tabs": [{"url": url, "title": "T", "pinned": False}], "current": 0}]


def test_session_save_skips_unchanged_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    store = SessionStore(path)
    assert store.save(_windows("https://a.example/")) is True
    first_ino = path.stat().st_ino
    # Identical snapshot: no rewrite, no .prev churn.
    assert store.save(_windows("https://a.example/")) is True
    assert path.stat().st_ino == first_ino
    assert not store._prev_path().exists()
    # Changed snapshot: writes, and .prev keeps the previous generation.
    assert store.save(_windows("https://b.example/")) is True
    assert path.stat().st_ino != first_ino
    prev = json.loads(store._prev_path().read_text(encoding="utf-8"))
    assert prev["windows"][0]["tabs"][0]["url"] == "https://a.example/"
    # A fresh store instance has no memory of the payload: it must write
    # (and must not corrupt) even when the content happens to match.
    fresh = SessionStore(path)
    assert fresh.save(_windows("https://b.example/")) is True
    assert json.loads(path.read_text(encoding="utf-8"))["windows"][0]["tabs"][0]["url"] == (
        "https://b.example/"
    )


def test_session_clear_forgets_coalesced_payload(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    store = SessionStore(path)
    assert store.save(_windows("https://a.example/")) is True
    store.clear()
    assert not path.exists()
    # After clear, the same payload is a real save again.
    assert store.save(_windows("https://a.example/")) is True
    assert path.exists()


# ── settings write coalescing ────────────────────────────────────────


def test_settings_set_unchanged_value_does_not_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    ino = path.stat().st_ino
    store.set("search_engine", store.get("search_engine"))
    store.set("zoom_levels", dict(store.get("zoom_levels", {})))
    assert path.stat().st_ino == ino
    store.set("search_engine", "Bing")
    assert path.stat().st_ino != ino
    assert SettingsStore(path).get("search_engine") == "Bing"


def test_settings_save_leaves_no_tmp_files(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    for i in range(5):
        store.set("homepage", f"https://example{i}.com/")
    assert json.loads(path.read_text(encoding="utf-8"))["homepage"] == "https://example4.com/"
    assert list(tmp_path.glob("*.tmp")) == []


# ── storage journal mode ─────────────────────────────────────────────


def test_storage_uses_wal_journal(tmp_path: Path) -> None:
    store = Storage(tmp_path / "browser.db")
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    store.add_visit("https://a.example/", "A")
    assert store.recent(1)[0][0] == "https://a.example/"


def test_settings_set_saves_container_mutated_in_place(tmp_path: Path) -> None:
    """get() returns the live dict; mutating it then set()-ing it back must
    still persist (ai_sidebar's ai_model_overrides does exactly this)."""
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    saved = store.get("ai_model_overrides", {}) or {}
    store.set("ai_model_overrides", saved)  # make it the live stored object
    saved = store.get("ai_model_overrides")
    saved["ollama"] = "llama3.2"
    store.set("ai_model_overrides", saved)
    assert SettingsStore(path).get("ai_model_overrides") == {"ollama": "llama3.2"}


def test_storage_recent_orders_same_timestamp_newest_first(tmp_path: Path, monkeypatch) -> None:
    """WAL makes back-to-back inserts fast enough to share visit_time."""
    import browser_core.storage as storage_mod

    monkeypatch.setattr(storage_mod.time, "time", lambda: 1_700_000_000.0)
    store = Storage(tmp_path / "browser.db")
    store.add_visit("https://a.example/", "A")
    store.add_visit("https://b.example/", "B")
    assert [u for u, _, _ in store.recent()] == ["https://b.example/", "https://a.example/"]
