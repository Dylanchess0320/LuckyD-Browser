"""Coverage push: tools/webmcp_tools.py remaining paths.

Covers: _origin_of_url edge cases, _coerce_discovery forms/error paths,
_cap_schema trimming, _validate_args type branches, _safe_json failure
fallbacks, _parse_args variants, WebMCPDiscover with a pre-navigation URL and
discovery failure, and WebMCPShimTool read/inject paths. Fake page, no browser.
"""

from __future__ import annotations

import pytest

import tools.webmcp_tools as webmcp
from tools.webmcp_tools import (
    WebMCPCallTool,
    WebMCPDiscoverTool,
    WebMCPShimTool,
    _cap_schema,
    _coerce_discovery,
    _origin_of_url,
    _parse_args,
    _safe_json,
    _validate_args,
    reset_webmcp_bindings,
)


@pytest.fixture(autouse=True)
def _clean_bindings():
    reset_webmcp_bindings()
    yield
    reset_webmcp_bindings()


class _FakePage:
    def __init__(self, url, discovery):
        self.url = url
        self._discovery = discovery
        self.goto_calls: list[str] = []
        self.call_result: object = {"ok": True}

    async def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        self.url = url

    async def evaluate(self, js, arg=None, timeout=None):
        if "declarative_forms" in js:
            return self._discovery
        return self.call_result


def _install(monkeypatch, page):
    async def _fake_get_page():
        return page

    monkeypatch.setattr(webmcp, "_get_page", _fake_get_page)


def _discovery_payload(*tools):
    return {"supported": True, "shim": None, "tools": list(tools), "declarative_forms": []}


# ── _origin_of_url ───────────────────────────────────────────────────


def test_origin_of_url_split_failure_returns_empty(monkeypatch) -> None:
    def _boom(url):
        raise ValueError("bad url")

    monkeypatch.setattr(webmcp, "urlsplit", _boom)
    assert _origin_of_url("https://example.com/") == ""


@pytest.mark.parametrize("url", ["about:blank", "data:text/plain,hi", "ftp://x.com/f", ""])
def test_origin_of_url_non_http_scheme_empty(url: str) -> None:
    assert _origin_of_url(url) == ""


def test_origin_of_url_missing_host_empty() -> None:
    assert _origin_of_url("http:///just-a-path") == ""


def test_origin_of_url_normalizes_host_and_ports() -> None:
    assert _origin_of_url("https://Example.COM/a") == "https://example.com"
    assert _origin_of_url("https://example.com:443/a") == "https://example.com"
    assert _origin_of_url("http://example.com:80/a") == "http://example.com"
    assert _origin_of_url("http://example.com:8080/a") == "http://example.com:8080"


# ── _coerce_discovery ────────────────────────────────────────────────


def test_coerce_discovery_non_dict() -> None:
    out = _coerce_discovery(["not", "a", "dict"])
    assert out["supported"] is False and out["tools"] == []


def test_coerce_discovery_valid_and_invalid_forms() -> None:
    out = _coerce_discovery(
        {
            "declarative_forms": [
                {
                    "tool": "search",
                    "description": "d",
                    "fields": [{"name": "q", "type": "text"}],
                },
                {"tool": "", "description": "empty name skipped"},
                "not-a-dict",
                {"description": "no tool key"},
            ]
        }
    )
    assert len(out["declarative_forms"]) == 1
    form = out["declarative_forms"][0]
    assert form["tool"] == "search" and form["fields"] == [{"name": "q", "type": "text"}]


def test_coerce_discovery_error_key_surfaced() -> None:
    out = _coerce_discovery({"error": "page blew up"})
    assert out["error"] == "page blew up"


def test_coerce_discovery_skips_malformed_tools() -> None:
    out = _coerce_discovery(
        {
            "tools": [
                "nope",
                {"description": "no name"},
                {"name": "", "parameters": {}},
                {"name": "ok", "parameters": {"type": "object"}},
            ]
        }
    )
    assert [t["name"] for t in out["tools"]] == ["ok"]


# ── _cap_schema ──────────────────────────────────────────────────────


def test_cap_schema_trims_oversized_properties() -> None:
    schema = {"properties": {f"p{i}": {"type": "string"} for i in range(250)}}
    capped = _cap_schema(schema)
    assert len(capped["properties"]) == 200
    # original untouched
    assert len(schema["properties"]) == 250


def test_cap_schema_leaves_small_schema_alone() -> None:
    schema = {"properties": {"a": {"type": "string"}}}
    assert _cap_schema(schema) is schema


# ── _validate_args ───────────────────────────────────────────────────


def test_validate_args_non_string_required_key_skipped() -> None:
    assert _validate_args({"required": [123, None]}, {}) is None


def test_validate_args_non_dict_spec_skipped() -> None:
    assert _validate_args({"properties": {"x": "not-a-dict"}}, {"x": 1}) is None


def test_validate_args_absent_arg_skipped() -> None:
    assert _validate_args({"properties": {"x": {"type": "string"}}}, {}) is None
    assert _validate_args({"properties": {"x": {"type": "string"}}}, {"x": None}) is None


def test_validate_args_boolean_type() -> None:
    schema = {"properties": {"flag": {"type": "boolean"}}}
    assert _validate_args(schema, {"flag": "yes"}) == "parameter 'flag' must be boolean"
    assert _validate_args(schema, {"flag": True}) is None


def test_validate_args_array_and_object_types() -> None:
    schema = {
        "properties": {"items": {"type": "array"}, "opts": {"type": "object"}},
    }
    assert _validate_args(schema, {"items": "nope"}) == "parameter 'items' must be array"
    assert _validate_args(schema, {"opts": [1]}) == "parameter 'opts' must be object"
    assert _validate_args(schema, {"items": [1], "opts": {}}) is None


def test_validate_args_non_dict_inputs_pass_through() -> None:
    assert _validate_args("nope", {}) is None
    assert _validate_args({}, "nope") is None


# ── _safe_json ───────────────────────────────────────────────────────


def test_safe_json_circular_falls_back_to_str() -> None:
    d: dict = {"a": 1}
    d["self"] = d  # json.dumps raises ValueError
    out = _safe_json(d, 200)
    assert "'a': 1" in out


def test_safe_json_unstringifiable_returns_placeholder() -> None:
    class _Bad:
        def __str__(self):
            raise RuntimeError("no str")

        __repr__ = __str__

    assert _safe_json(_Bad(), 200) == "<unserializable page result>"


def test_safe_json_truncates() -> None:
    assert _safe_json({"k": "v" * 100}, 10) == _safe_json({"k": "v" * 100}, 10)[:10]


# ── _parse_args ──────────────────────────────────────────────────────


def test_parse_args_variants() -> None:
    assert _parse_args(None) == {}
    assert _parse_args({"a": 1}) == {"a": 1}
    assert _parse_args("   ") == {}
    assert _parse_args('{"a": 2}') == {"a": 2}
    with pytest.raises(TypeError):
        _parse_args(42)


# ── WebMCPDiscoverTool ───────────────────────────────────────────────


async def test_discover_with_url_navigates_first(monkeypatch) -> None:
    page = _FakePage("about:blank", _discovery_payload())
    _install(monkeypatch, page)
    out = await WebMCPDiscoverTool().execute(url="https://example.com/shop")
    assert not out.error
    assert page.goto_calls == ["https://example.com/shop"]
    assert out.metadata["origin"] == "https://example.com"


async def test_discover_page_failure_reports_error(monkeypatch) -> None:
    async def _boom():
        raise RuntimeError("browser gone")

    monkeypatch.setattr(webmcp, "_get_page", _boom)
    out = await WebMCPDiscoverTool().execute()
    assert out.error
    assert "WebMCP discovery failed: browser gone" in out.text


# ── WebMCPCallTool argument parsing ──────────────────────────────────


async def test_call_rejects_malformed_arguments_json(monkeypatch) -> None:
    page = _FakePage("https://example.com/", _discovery_payload())
    _install(monkeypatch, page)
    out = await WebMCPCallTool().execute(tool="x", arguments="{bad json", binding_token="t")
    assert out.error and "invalid arguments" in out.text


async def test_call_rejects_non_string_non_dict_arguments(monkeypatch) -> None:
    page = _FakePage("https://example.com/", _discovery_payload())
    _install(monkeypatch, page)
    out = await WebMCPCallTool().execute(tool="x", arguments=42, binding_token="t")
    assert out.error and "invalid arguments" in out.text


# ── WebMCPShimTool ───────────────────────────────────────────────────


async def test_shim_missing_file_reports_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(webmcp, "_SHIM_PATH", tmp_path / "no-shim.js")
    out = await WebMCPShimTool().execute()
    assert out.error
    assert "cannot read WebMCP shim" in out.text


async def test_shim_inject_and_discover_roundtrip(monkeypatch) -> None:
    page = _FakePage(
        "https://example.com/",
        _discovery_payload({"name": "search", "parameters": {"type": "object"}}),
    )
    _install(monkeypatch, page)
    out = await WebMCPShimTool().execute()
    assert not out.error
    assert "WebMCP shim active" in out.text
    assert out.metadata["origin"] == "https://example.com"
    assert out.metadata["binding_token"]


async def test_shim_injection_failure_reports_error(monkeypatch) -> None:
    class _BadPage(_FakePage):
        async def evaluate(self, js, arg=None, timeout=None):
            raise RuntimeError("evaluate died")

    _install(monkeypatch, _BadPage("https://example.com/", _discovery_payload()))
    out = await WebMCPShimTool().execute()
    assert out.error
    assert "shim injection failed" in out.text


def test_validate_args_unlisted_type_falls_through() -> None:
    """A property spec with no "type" key skips every check and passes."""
    assert _validate_args({"properties": {"s": {}}}, {"s": "anything"}) is None
    assert _validate_args({"properties": {"s": {"description": "x"}}}, {"s": 1}) is None
