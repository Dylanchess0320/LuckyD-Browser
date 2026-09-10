"""Regression tests for WebMCP multi-tab binding isolation.

Bindings are keyed by (tab_id, origin): one tab's navigation can never
purge or steal another tab's registrations, and a binding token discovered
in one tab is refused in another — even on the same origin. The browser is
single-tab today (current_tab_id() == "default"), so this exercises the
scaffolding via set_current_tab_id() that the future multi-tab browser
core will drive.
"""

from __future__ import annotations

import pytest

import tools.webmcp_tools as webmcp
from tools.webmcp_tools import (
    _BINDINGS,
    _binding_key,
    _check_binding,
    _register_discovery,
    _sync_origin_binding,
    current_tab_id,
    reset_webmcp_bindings,
    set_current_tab_id,
)

ORIGIN_A = "https://shop.example"


def _tool(name):
    return {
        "name": name,
        "description": f"{name} tool",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }


@pytest.fixture(autouse=True)
def _clean_bindings():
    reset_webmcp_bindings()
    yield
    reset_webmcp_bindings()


class TestTabIdentity:
    def test_single_tab_default(self):
        assert current_tab_id() == "default"

    def test_set_and_reset(self):
        set_current_tab_id("tab-1")
        assert current_tab_id() == "tab-1"
        reset_webmcp_bindings()
        assert current_tab_id() == "default"


class TestMultiTabIsolation:
    def test_same_origin_bindings_are_per_tab(self):
        """Two tabs on the same origin get independent binding tokens."""
        key = _binding_key(ORIGIN_A + "/")
        token_a = _register_discovery("tab-a", key, [_tool("search")])
        token_b = _register_discovery("tab-b", key, [_tool("search")])
        assert token_a != token_b
        # Token from tab A must not work while driving tab B.
        set_current_tab_id("tab-b")
        binding, refusal = _check_binding(key, "search", token_a)
        assert binding is None
        assert "token" in refusal.lower()
        # Tab B's own token works.
        binding, refusal = _check_binding(key, "search", token_b)
        assert binding is not None, refusal

    def test_tab_a_navigation_does_not_purge_tab_b(self):
        """Navigation bookkeeping is per tab; tab A's cross-origin hop must
        not wipe tab B's registration on the same origin."""
        key = _binding_key(ORIGIN_A + "/")
        _register_discovery("tab-a", key, [_tool("search")])
        _register_discovery("tab-b", key, [_tool("search")])
        # Tab A navigates away to another origin (purges its own binding).
        _sync_origin_binding("tab-a", ORIGIN_A + "/")
        _sync_origin_binding("tab-a", "https://other.example/")
        assert ("tab-a", key) not in _BINDINGS
        # Tab B is untouched.
        assert ("tab-b", key) in _BINDINGS

    def test_cross_tab_token_replay_refused(self):
        """An attacker page in tab B cannot replay a token leaked from tab A."""
        key = _binding_key(ORIGIN_A + "/")
        token_a = _register_discovery("tab-a", key, [_tool("search")])
        _register_discovery("tab-b", key, [_tool("search")])
        set_current_tab_id("tab-b")
        binding, refusal = _check_binding(key, "search", token_a)
        assert binding is None
        assert refusal  # actionable refusal, not an empty string

    def test_tool_sets_are_per_tab(self):
        """The same origin can expose different tools in different tabs
        (e.g. different pages on the origin); each tab sees only its own."""
        key = _binding_key(ORIGIN_A + "/")
        token_a = _register_discovery("tab-a", key, [_tool("search")])
        _register_discovery("tab-b", key, [_tool("checkout")])
        set_current_tab_id("tab-a")
        binding, refusal = _check_binding(key, "checkout", token_a)
        assert binding is None
        assert "checkout" in refusal


class TestSingleTabBehaviorUnchanged:
    def test_default_tab_roundtrip(self):
        """Without set_current_tab_id, behavior matches the single-tab flow:
        discover (register) -> call (check) on the same implicit tab."""
        key = _binding_key(ORIGIN_A + "/")
        token = _register_discovery(current_tab_id(), key, [_tool("search")])
        binding, refusal = _check_binding(key, "search", token)
        assert binding is not None, refusal

    def test_module_level_helpers_importable(self):
        for name in (
            "current_tab_id",
            "set_current_tab_id",
            "reset_webmcp_bindings",
            "_check_binding",
            "_sync_origin_binding",
            "_register_discovery",
        ):
            assert callable(getattr(webmcp, name)), name
