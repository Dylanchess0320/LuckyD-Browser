"""Night-1 browser-core audit: settings.py, permissions.py, session.py,
storage.py, scheduler.py (browser-side).

Load/save/defaults, permission decisions, session safety rails, history and
bookmark storage, workflow-schedule timing.
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

from browser_core import scheduler as bkscheduler
from browser_core.permissions import (
    ALLOW,
    ASK,
    DENY,
    PermissionStore,
    feature_key,
    feature_label,
    origin_of,
)
from browser_core.scheduler import ScheduleStore, is_due, next_run_at
from browser_core.session import (
    MAX_TABS_PER_WINDOW,
    MAX_WINDOWS,
    SessionStore,
    is_restorable,
    session_path,
    tab_record,
    window_record,
)
from browser_core.settings import (
    DEFAULTS,
    SettingsStore,
    webengine_chromium_flags,
)
from browser_core.storage import Storage

# ── settings ─────────────────────────────────────────────────────────


def test_settings_defaults_loaded_when_missing(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    assert s.get("search_engine") == "DuckDuckGo"
    assert s.get("adblock_enabled") is True
    # every DEFAULTS key resolves without KeyError
    for key in DEFAULTS:
        assert s.get(key) is not None or key in DEFAULTS


def test_settings_tokens_generated_and_persisted(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    s = SettingsStore(p)
    t1, t2 = s.get("browser_api_token"), s.get("terminal_token")
    assert t1 and t2 and t1 != t2
    assert len(t1) >= 32
    again = SettingsStore(p)
    assert again.get("browser_api_token") == t1  # stable across restarts


def test_settings_corrupt_file_preserved_and_defaults_used(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text("{not valid json", encoding="utf-8")
    s = SettingsStore(p)
    assert s.get("search_engine") == "DuckDuckGo"  # fell back to defaults
    preserved = list(tmp_path.glob("settings.corrupt.*.json"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "{not valid json"
    # and a fresh settings.json was written (defaults + fresh tokens)
    assert p.exists()


def test_settings_overrides_and_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    s = SettingsStore(p)
    s.set("zoom_factor", 1.25)
    assert SettingsStore(p).get("zoom_factor") == 1.25
    # unknown keys in the file are preserved, not dropped
    s.set("custom_plugin_key", "x")
    assert SettingsStore(p).get("custom_plugin_key") == "x"


def test_settings_search_url_encoding(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    s.set("search_engine", "DuckDuckGo")
    url = s.search_url_for("hello world & more")
    assert url.startswith("https://duckduckgo.com/?q=")
    assert "hello+world+%26+more" in url
    # unknown engine falls back to DuckDuckGo
    s.set("search_engine", "Nope")
    assert s.search_url_for("x").startswith("https://duckduckgo.com/?q=")


def test_settings_legacy_terminal_cli_migrated(tmp_path: Path) -> None:
    """luckyd-code.exe (the web server, no prompt) must not stay as the
    terminal CLI; without a sibling luckyd-cli.exe it clears to auto-detect."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"terminal_cli": "/x/luckyd-code.exe"}), encoding="utf-8")
    s = SettingsStore(p)
    assert s.get("terminal_cli") == ""
    # non-legacy paths are untouched
    p2 = tmp_path / "s2.json"
    p2.write_text(json.dumps({"terminal_cli": "/x/other.exe"}), encoding="utf-8")
    assert SettingsStore(p2).get("terminal_cli") == "/x/other.exe"


def test_webengine_chromium_flags() -> None:
    soft = webengine_chromium_flags(False)
    assert "--force-color-profile=srgb" in soft
    assert "--disable-accelerated-video-decode" in soft
    assert webengine_chromium_flags(True) == ""


# ── permissions ──────────────────────────────────────────────────────


def test_origin_of() -> None:
    assert origin_of("https://Example.COM:8443/path") == "https://example.com:8443"
    assert origin_of("https://example.com/") == "https://example.com"
    assert origin_of("http://example.com:80/") == "http://example.com"
    assert origin_of("https://example.com:443/") == "https://example.com"
    assert origin_of("ftp://example.com/") == ""
    assert origin_of("about:blank") == ""
    assert origin_of("") == ""
    assert origin_of("https://[::1]:9777/x") == "https://[::1]:9777"


def test_feature_key_mapping() -> None:
    class _Feat:
        name = "MediaVideoCapture"

    assert feature_key(_Feat()) == "camera"
    assert feature_key("Notifications") == "notifications"
    assert feature_key("SomeFutureThing") == "somefuturething"
    assert feature_label("camera") == "Camera"
    assert feature_label("mystery_x") == "Mystery X"


def test_permission_store_ask_by_default(tmp_path: Path) -> None:
    p = PermissionStore(tmp_path / "p.json")
    assert p.get("https://example.com", "camera") == ASK


def test_permission_store_set_and_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "p.json"
    s = PermissionStore(p)
    s.set("https://a.com", "camera", ALLOW)
    s.set("https://a.com", "microphone", DENY)
    again = PermissionStore(p)
    assert again.get("https://a.com", "camera") == ALLOW
    assert again.get("https://a.com", "microphone") == DENY
    # ASK removes the entry
    again.set("https://a.com", "camera", ASK)
    assert again.get("https://a.com", "camera") == ASK


def test_permission_store_persist_false_writes_nothing(tmp_path: Path) -> None:
    p = tmp_path / "p.json"
    s = PermissionStore(p)
    s.set("https://a.com", "camera", ALLOW, persist=False)
    assert s.get("https://a.com", "camera") == ALLOW  # in memory
    assert not p.exists()  # incognito-style: nothing hit disk


def test_permission_store_invalid_inputs_ignored(tmp_path: Path) -> None:
    s = PermissionStore(tmp_path / "p.json")
    s.set("", "camera", ALLOW)
    s.set("https://a.com", "camera", "maybe")
    assert s.sites() == []


def test_permission_store_clear_and_sort(tmp_path: Path) -> None:
    s = PermissionStore(tmp_path / "p.json")
    s.set("https://b.com", "camera", ALLOW)
    s.set("https://a.com", "camera", DENY)
    assert [o for o, _ in s.sites()] == ["https://a.com", "https://b.com"]
    s.clear_origin("https://a.com")
    assert [o for o, _ in s.sites()] == ["https://b.com"]
    s.clear_all()
    assert s.sites() == []
    # save payload shape
    raw = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1 and raw["sites"] == {}


def test_permission_store_corrupt_file_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "p.json"
    p.write_text("garbage", encoding="utf-8")
    assert PermissionStore(p).sites() == []


# ── session ──────────────────────────────────────────────────────────


def test_is_restorable() -> None:
    assert is_restorable("https://example.com")
    assert is_restorable("http://127.0.0.1:9777/dashboard")
    assert is_restorable("file:///C:/x.html")
    assert not is_restorable("about:blank")
    assert not is_restorable("view-source:https://example.com")
    assert not is_restorable("")


def test_tab_record() -> None:
    rec = tab_record("https://a.com", title="T" * 500, pinned=True, group="g")
    assert rec is not None
    assert len(rec["title"]) == 200
    assert rec["pinned"] is True and rec["group"] == "g"
    assert tab_record("about:blank") is None
    rec2 = tab_record("https://a.com")
    assert "group" not in rec2


def test_window_record_clamps() -> None:
    tabs = [tab_record(f"https://a.com/{i}") for i in range(5)]
    rec = window_record(tabs, current=99)
    assert rec is not None and rec["current"] == 4
    assert window_record([None, None]) is None
    many = [tab_record(f"https://a.com/{i}") for i in range(MAX_TABS_PER_WINDOW + 10)]
    assert len(window_record(many)["tabs"]) == MAX_TABS_PER_WINDOW


def test_session_store_roundtrip_and_prev(tmp_path: Path) -> None:
    p = tmp_path / "session.json"
    store = SessionStore(p)
    w1 = window_record([tab_record("https://a.com")])
    assert store.save([w1])
    loaded = store.load()
    assert loaded["version"] == 1 and len(loaded["windows"]) == 1
    # second save preserves the previous generation
    w2 = window_record([tab_record("https://b.com")])
    assert store.save([w2])
    prev = store.load_previous()
    assert prev["windows"][0]["tabs"][0]["url"] == "https://a.com"
    assert store.load()["windows"][0]["tabs"][0]["url"] == "https://b.com"


def test_session_store_corrupt_and_version_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "session.json"
    p.write_text("nope", encoding="utf-8")
    assert SessionStore(p).load() == {}
    assert not p.exists()  # moved aside
    p.write_text(json.dumps({"version": 999, "windows": []}), encoding="utf-8")
    assert SessionStore(p).load() == {}
    p.write_text(json.dumps({"version": 1, "windows": "x"}), encoding="utf-8")
    assert SessionStore(p).load() == {}


def test_session_store_max_windows_and_clear(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_SESSION_PATH", str(tmp_path / "s.json"))
    assert session_path() == tmp_path / "s.json"
    store = SessionStore()
    wins = [window_record([tab_record(f"https://a.com/{i}")]) for i in range(MAX_WINDOWS + 3)]
    store.save(wins)
    assert len(store.load()["windows"]) == MAX_WINDOWS
    store.clear()
    assert store.load() == {}


# ── storage ──────────────────────────────────────────────────────────


def test_storage_history(tmp_path: Path) -> None:
    s = Storage(tmp_path / "b.db")
    s.add_visit("https://a.com", "A")
    s.add_visit("https://b.com", "B")
    recent = s.recent()
    assert [u for u, _, _ in recent] == ["https://b.com", "https://a.com"]
    assert s.search_history("a.com")
    assert not s.search_history("zzz-no-match")
    s.delete_history_entry("https://a.com")
    assert [u for u, _, _ in s.recent()] == ["https://b.com"]
    s.clear_history()
    assert s.recent() == []


def test_storage_bookmarks(tmp_path: Path) -> None:
    s = Storage(tmp_path / "b.db")
    assert not s.is_bookmarked("https://a.com")
    s.add_bookmark("https://a.com", "A", folder="work")
    s.add_bookmark("https://a.com", "A2")  # replace keeps uniqueness
    assert s.is_bookmarked("https://a.com")
    rows = s.bookmarks()
    assert len(rows) == 1 and rows[0][1] == "A2"
    s.remove_bookmark("https://a.com")
    assert not s.is_bookmarked("https://a.com")


def test_storage_import_from_html(tmp_path: Path) -> None:
    html = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><DT><A HREF="https://a.com" ADD_DATE="1">Alpha</A>
<DT><A HREF="https://b.com">Beta</A>
<DT><A HREF="javascript:alert(1)">nope</A></DL>"""
    f = tmp_path / "bm.html"
    f.write_text(html, encoding="utf-8")
    s = Storage(tmp_path / "b.db")
    assert s.import_from_html(f) == 2
    assert s.is_bookmarked("https://a.com") and s.is_bookmarked("https://b.com")
    assert not s.is_bookmarked("javascript:alert(1)")


# ── browser-side scheduler ───────────────────────────────────────────


def test_next_run_at_and_is_due() -> None:
    assert next_run_at(60, from_ts=1000.0) == 4600.0
    assert is_due({"every_min": 60, "next_run": 100}, now=200.0)
    assert not is_due({"every_min": 60, "next_run": 300}, now=200.0)
    assert not is_due({"every_min": 0, "next_run": 0}, now=999.0)
    assert not is_due({}, now=999.0)


def test_schedule_store_set_disable_due(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "sched.json")
    entry = store.set("wf1", 60)
    assert entry["every_min"] == 60 and entry["next_run"] > 0
    assert store.set("wf1", 0) == {"every_min": 0}  # disable removes
    assert store.list() == []
    store.set("wf2", 15)
    rows = store.list()
    assert rows[0]["name"] == "wf2" and rows[0]["label"] == "Every 15 min"
    assert "wf2" not in store.due(now=0.0)  # next_run is in the future
    store.set("wf3", 15)
    # force wf3 due by backdating
    data = json.loads((tmp_path / "sched.json").read_text(encoding="utf-8"))
    data["wf3"]["next_run"] = 1.0
    (tmp_path / "sched.json").write_text(json.dumps(data), encoding="utf-8")
    assert store.due(now=100.0) == ["wf3"]


def test_schedule_mark_run_advances_next(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "sched.json")
    store.set("wf1", 60)
    store.mark_run("wf1", "ok " * 200, now=1000.0)  # result truncated at 200 chars
    rows = store.list()
    assert rows[0]["last_run"] == 1000.0
    assert len(rows[0]["last_result"]) == 200
    assert rows[0]["next_run"] == 4600.0
    store.mark_run("ghost", "x")  # unknown name: no crash, no entry
    assert len(store.list()) == 1


def test_schedule_intervals_labels() -> None:
    assert bkscheduler.INTERVALS[0] == "Off"
    assert bkscheduler.INTERVALS[1440] == "Daily"
    assert bkscheduler.INTERVALS.get(999, bkscheduler.INTERVALS[0]) == "Off"
