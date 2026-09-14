"""Coverage tests for browser_core.zoom — per-site zoom memory (pure logic)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.zoom import (
    DEFAULT_ZOOM,
    MAX_ZOOM,
    MIN_ZOOM,
    clamp_zoom,
    origin_key,
    remember,
    zoom_for,
)


class TestClampZoom:
    def test_identity_inside_range(self):
        assert clamp_zoom(1.0) == 1.0
        assert clamp_zoom(2.5) == 2.5

    def test_below_minimum_clamps(self):
        assert clamp_zoom(0.0) == MIN_ZOOM
        assert clamp_zoom(-3.0) == MIN_ZOOM

    def test_above_maximum_clamps(self):
        assert clamp_zoom(9.99) == MAX_ZOOM
        assert clamp_zoom(100.0) == MAX_ZOOM

    def test_rounds_to_whole_percents(self):
        assert clamp_zoom(1.234) == 1.23
        assert clamp_zoom(1.235) == 1.24  # banker's? no — round-half-even on binary float
        assert clamp_zoom(1.999) == 2.0

    def test_non_numeric_returns_default(self):
        assert clamp_zoom(None) == DEFAULT_ZOOM
        assert clamp_zoom("not-a-number") == DEFAULT_ZOOM
        assert clamp_zoom([1.5]) == DEFAULT_ZOOM

    def test_numeric_strings_coerce(self):
        assert clamp_zoom("1.5") == 1.5

    def test_int_coerces(self):
        assert clamp_zoom(2) == 2.0

    def test_boundary_values_pass_through(self):
        assert clamp_zoom(MIN_ZOOM) == MIN_ZOOM
        assert clamp_zoom(MAX_ZOOM) == MAX_ZOOM


class TestOriginKey:
    def test_basic_http(self):
        assert origin_key("http://example.com/page") == "http://example.com"

    def test_basic_https(self):
        assert origin_key("https://example.com/a/b?c=d") == "https://example.com"

    def test_host_lowercased(self):
        assert origin_key("https://EXAMPLE.com/") == "https://example.com"

    def test_non_default_port_kept(self):
        assert origin_key("http://example.com:8080/x") == "http://example.com:8080"
        assert origin_key("https://example.com:8443/x") == "https://example.com:8443"

    def test_default_ports_normalized_away(self):
        assert origin_key("http://example.com:80/x") == "http://example.com"
        assert origin_key("https://example.com:443/x") == "https://example.com"

    def test_cross_scheme_default_port_kept(self):
        # http on 443 is NOT http's default — keep it.
        assert origin_key("http://example.com:443/x") == "http://example.com:443"
        assert origin_key("https://example.com:80/x") == "https://example.com:80"

    def test_localhost_participates(self):
        assert origin_key("http://127.0.0.1:9222/json") == "http://127.0.0.1:9222"

    def test_non_http_schemes_rejected(self):
        assert origin_key("file:///etc/passwd") == ""
        assert origin_key("about:blank") == ""
        assert origin_key("view-source:https://example.com") == ""
        assert origin_key("data:text/plain,hi") == ""

    def test_empty_and_none_rejected(self):
        assert origin_key("") == ""
        assert origin_key(None) == ""

    def test_missing_host_rejected(self):
        assert origin_key("https:///path-only") == ""
        assert origin_key("http://") == ""

    def test_malformed_port_rejected(self):
        assert origin_key("http://example.com:abc/x") == ""

    def test_urlsplit_failure_returns_empty(self):
        # Invalid IPv6 literal makes urlsplit raise ValueError.
        assert origin_key("http://[::1") == ""

    def test_uppercase_scheme_accepted(self):
        assert origin_key("HTTPS://example.com/") == "https://example.com"


class TestZoomFor:
    def test_remembered_value_returned(self):
        levels = {"https://example.com": 1.5}
        assert zoom_for(levels, "https://example.com/page") == 1.5

    def test_unknown_site_returns_default(self):
        assert zoom_for({}, "https://example.com/") == DEFAULT_ZOOM

    def test_custom_default_used(self):
        assert zoom_for({}, "https://example.com/", default=2.0) == 2.0

    def test_default_clamped(self):
        assert zoom_for({}, "https://example.com/", default=99.0) == MAX_ZOOM

    def test_stored_value_clamped_on_read(self):
        levels = {"https://example.com": 1234.0}
        assert zoom_for(levels, "https://example.com/") == MAX_ZOOM

    def test_non_dict_levels_uses_default(self):
        assert zoom_for(None, "https://example.com/") == DEFAULT_ZOOM
        assert zoom_for("junk", "https://example.com/") == DEFAULT_ZOOM

    def test_unkeyable_url_uses_default(self):
        assert zoom_for({"https://example.com": 2.0}, "file:///x") == DEFAULT_ZOOM

    def test_port_specific_lookup(self):
        levels = {"http://example.com:8080": 1.25}
        assert zoom_for(levels, "http://example.com:8080/a") == 1.25
        assert zoom_for(levels, "http://example.com/a") == DEFAULT_ZOOM


class TestRemember:
    def test_stores_new_entry(self):
        out = remember({}, "https://example.com/a", 1.5)
        assert out == {"https://example.com": 1.5}

    def test_returns_new_dict_original_untouched(self):
        levels = {"https://a.com": 2.0}
        out = remember(levels, "https://b.com/", 1.5)
        assert levels == {"https://a.com": 2.0}
        assert out == {"https://a.com": 2.0, "https://b.com": 1.5}

    def test_factor_clamped_on_store(self):
        out = remember({}, "https://example.com/", 99.0)
        assert out == {"https://example.com": MAX_ZOOM}

    def test_near_default_deletes_entry(self):
        levels = {"https://example.com": 1.5}
        out = remember(levels, "https://example.com/", 1.0)
        assert out == {}
        # tolerance: 1.004 is within 0.01 of default
        out = remember({"https://example.com": 1.5}, "https://example.com/", 1.004)
        assert out == {}

    def test_just_outside_tolerance_kept(self):
        out = remember({}, "https://example.com/", 1.02)
        assert out == {"https://example.com": 1.02}

    def test_unkeyable_url_returns_copy_unchanged(self):
        levels = {"https://a.com": 2.0}
        out = remember(levels, "file:///x", 1.5)
        assert out == {"https://a.com": 2.0}
        assert out is not levels

    def test_none_levels_starts_empty(self):
        out = remember(None, "https://example.com/", 1.5)
        assert out == {"https://example.com": 1.5}

    def test_non_dict_levels_starts_empty(self):
        out = remember("junk", "https://example.com/", 1.5)
        assert out == {"https://example.com": 1.5}
