"""Headless coverage for LuckyD Browser 3.9 daily-driver upgrades."""

from __future__ import annotations

from pathlib import Path

from browser.browser_core.https_only import is_upgradable, upgrade_url
from browser.browser_core.lifecycle import (
    ACTIVE,
    DISCARDED,
    FROZEN,
    is_protected_url,
    next_state,
    should_protect,
)
from browser.browser_core.permissions import (
    ALLOW,
    ASK,
    DENY,
    PermissionStore,
    feature_key,
    feature_label,
    origin_of,
)
from browser.browser_core.settings import DEFAULTS
from browser.browser_core.workspaces import WorkspaceStore, sanitize_tabs


def test_https_only_upgrades_public_http() -> None:
    assert is_upgradable("http://example.com/a")
    assert upgrade_url("http://example.com/a") == "https://example.com/a"
    assert upgrade_url("http://example.com:80/x?q=1") == "https://example.com/x?q=1"
    assert upgrade_url("https://example.com/") is None


def test_https_only_skips_localhost_and_lan() -> None:
    for url in (
        "http://127.0.0.1:9777/dashboard",
        "http://localhost/hq",
        "http://192.168.1.1/",
        "http://10.0.0.5/admin",
        "http://printer.local/",
        "file:///C:/page.html",
    ):
        assert not is_upgradable(url), url
        assert upgrade_url(url) is None


def test_permission_store_round_trip(tmp_path: Path) -> None:
    store = PermissionStore(tmp_path / "permissions.json")
    origin = origin_of("https://meet.example.com/room")
    assert origin == "https://meet.example.com"
    assert store.get(origin, "camera") == ASK
    store.set(origin, "camera", ALLOW)
    store.set(origin, "microphone", DENY)
    again = PermissionStore(tmp_path / "permissions.json")
    assert again.get(origin, "camera") == ALLOW
    assert again.get(origin, "microphone") == DENY
    again.clear_origin(origin)
    assert again.get(origin, "camera") == ASK
    assert feature_key("MediaVideoCapture") == "camera"
    assert "Camera" in feature_label("camera")


def test_workspace_store_create_switch_delete(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces.json")
    tabs = sanitize_tabs(
        [
            {"url": "https://example.com", "title": "Example", "pinned": True},
            {"url": "about:blank", "title": "skip me"},
            {"url": "https://news.example", "title": "News"},
        ]
    )
    assert len(tabs) == 2
    created = store.create("Research", tabs, current=1)
    assert store.active_id == created["id"]
    assert store.get(created["id"])["tabs"][0]["pinned"]
    other = store.create("Shopping", [{"url": "https://shop.example", "title": "Shop"}], 0)
    assert len(store.list()) == 2
    store.set_active(created["id"])
    assert store.active_id == created["id"]
    store.delete(other["id"])
    assert store.get(other["id"]) is None
    assert store.active_id == created["id"]


def test_memory_saver_policy() -> None:
    assert is_protected_url("http://127.0.0.1:9777/dashboard")
    assert is_protected_url("http://localhost:8000/hq")
    assert not is_protected_url("https://example.com/article")
    assert should_protect(url="https://a.com", is_current=True, pinned=False, audible=False)
    assert should_protect(url="https://a.com", is_current=False, pinned=True, audible=False)
    assert should_protect(url="https://a.com", is_current=False, pinned=False, audible=True)
    assert not should_protect(url="https://a.com", is_current=False, pinned=False, audible=False)
    assert next_state(10) == ACTIVE
    assert next_state(5 * 60) == FROZEN
    assert next_state(15 * 60) == DISCARDED
    assert next_state(120, freeze_after=30, discard_after=60) == DISCARDED


def test_new_defaults_are_on() -> None:
    assert DEFAULTS["https_only"] is True
    assert DEFAULTS["memory_saver"] is True
    assert DEFAULTS["memory_saver_freeze_sec"] == 300
    assert DEFAULTS["memory_saver_discard_sec"] == 900


def test_software_video_decode_is_default() -> None:
    """Colors-first default: GPU paths stay off unless opted in."""
    from browser.browser_core.settings import webengine_chromium_flags

    assert DEFAULTS["hw_video_decode"] is False
    assert (
        webengine_chromium_flags(False)
        == "--disable-accelerated-video-decode "
        "--disable-gpu-compositing --disable-gpu-rasterization"
    )
    assert webengine_chromium_flags(True) == ""
