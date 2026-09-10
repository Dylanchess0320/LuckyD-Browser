"""Regression tests for LuckyD 6.1 WebMCP tab/origin binding + spec gaps.

1. Tab/origin binding: discovery registers tools per page origin and returns
   an opaque binding token; WebMCPCall is refused unless the token matches the
   current page's origin AND the tool was discovered on that origin. Cross-
   origin navigation purges stale registrations.
2. Spec gaps: tool arguments are validated server-side against the schema
   captured at discovery (before dispatch); page evaluate calls carry an
   explicit timeout; page-supplied discovery data is coerced defensively.

Uses a fake Playwright page — no browser needed.
"""

from __future__ import annotations

import pytest

import tools.webmcp_tools as webmcp
from tools.webmcp_tools import (
    _BINDINGS,
    _check_binding,
    _coerce_discovery,
    _validate_args,
    reset_webmcp_bindings,
)

ORIGIN_A = "https://shop.example"
ORIGIN_B = "https://evil.example"


def _discovery_payload(*tools):
    return {
        "supported": True,
        "shim": "luckyd-webmcp/1.0",
        "tools": list(tools),
        "declarative_forms": [],
    }


def _tool(name, required=(), properties=None):
    return {
        "name": name,
        "description": f"{name} tool",
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
    }


class _FakePage:
    """Minimal stand-in for a Playwright page."""

    def __init__(self, url, discovery):
        self.url = url
        self._discovery = discovery
        self.evaluate_calls: list[dict] = []
        self.call_result: object = {"ok": True}
        self.call_error: Exception | None = None

    async def goto(self, url, **kwargs):
        self.url = url

    async def evaluate(self, js, arg=None, timeout=None):
        self.evaluate_calls.append({"js": js, "arg": arg, "timeout": timeout})
        if "declarative_forms" in js:
            return self._discovery
        if self.call_error is not None:
            raise self.call_error
        return self.call_result

    def call_evaluates(self):
        return [c for c in self.evaluate_calls if "declarative_forms" not in c["js"]]


@pytest.fixture(autouse=True)
def _clean_bindings():
    reset_webmcp_bindings()
    yield
    reset_webmcp_bindings()


def _install(monkeypatch, page):
    async def _fake_get_page():
        return page

    monkeypatch.setattr(webmcp, "_get_page", _fake_get_page)


async def _discover(monkeypatch, page):
    _install(monkeypatch, page)
    tool = webmcp.WebMCPDiscoverTool()
    return await tool.execute()


async def _call(monkeypatch, page, **kwargs):
    _install(monkeypatch, page)
    tool = webmcp.WebMCPCallTool()
    return await tool.execute(**kwargs)


class TestDiscoveryBinding:
    async def test_discover_registers_origin_and_returns_token(self, monkeypatch):
        page = _FakePage(
            ORIGIN_A + "/",
            _discovery_payload(
                _tool("search", required=["query"], properties={"query": {"type": "string"}})
            ),
        )
        out = await _discover(monkeypatch, page)
        assert not out.error
        token = out.metadata["binding_token"]
        assert token and len(token) >= 16
        assert out.metadata["origin"] == ORIGIN_A
        assert ORIGIN_A in _BINDINGS
        assert "search" in _BINDINGS[ORIGIN_A].tools
        assert "Binding token" in out.text

    async def test_discover_scopes_to_calling_tab_origin(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out_a = await _discover(monkeypatch, page)
        assert _BINDINGS[ORIGIN_A].tools.keys() == {"search"}
        page.url = ORIGIN_B + "/"
        page._discovery = _discovery_payload(_tool("steal"))
        out_b = await _discover(monkeypatch, page)
        # Navigating cross-origin purged origin A's stale registration.
        assert ORIGIN_A not in _BINDINGS
        assert _BINDINGS[ORIGIN_B].tools.keys() == {"steal"}
        assert out_a.metadata["binding_token"] != out_b.metadata["binding_token"]

    async def test_token_rotates_on_rediscovery(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out1 = await _discover(monkeypatch, page)
        out2 = await _discover(monkeypatch, page)
        t1, t2 = out1.metadata["binding_token"], out2.metadata["binding_token"]
        assert t1 != t2
        # Old token no longer valid for the origin.
        _, refusal = _check_binding(ORIGIN_A, "search", t1)
        assert refusal

    async def test_default_port_normalized(self, monkeypatch):
        page = _FakePage("https://shop.example:443/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        assert out.metadata["origin"] == ORIGIN_A
        page.url = ORIGIN_A + "/"  # same origin, different URL spelling
        _, refusal = _check_binding(ORIGIN_A, "search", out.metadata["binding_token"])
        assert refusal == ""  # token binds the origin, not the URL spelling


class TestCallBinding:
    async def test_call_with_valid_token_dispatches(self, monkeypatch):
        page = _FakePage(
            ORIGIN_A + "/",
            _discovery_payload(
                _tool("search", required=["query"], properties={"query": {"type": "string"}})
            ),
        )
        out = await _discover(monkeypatch, page)
        token = out.metadata["binding_token"]
        res = await _call(
            monkeypatch, page, tool="search", arguments='{"query": "laptop"}', binding_token=token
        )
        assert not res.error, res.text
        dispatched = page.call_evaluates()
        assert len(dispatched) == 1
        assert dispatched[0]["arg"] == ["search", {"query": "laptop"}]
        assert dispatched[0]["timeout"] == 30000

    async def test_call_without_discovery_refused(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload())
        res = await _call(
            monkeypatch, page, tool="search", arguments="{}", binding_token="whatever"
        )
        assert res.error
        assert "WebMCPDiscover" in res.text
        assert page.call_evaluates() == []

    async def test_call_cross_origin_refused(self, monkeypatch):
        # Discover on origin A, then the tab navigates to origin B.
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        token = out.metadata["binding_token"]
        page.url = ORIGIN_B + "/"
        res = await _call(monkeypatch, page, tool="search", arguments="{}", binding_token=token)
        assert res.error
        assert "refused" in res.text
        assert page.call_evaluates() == []  # never dispatched to origin B

    async def test_call_forged_token_refused(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        await _discover(monkeypatch, page)
        res = await _call(
            monkeypatch, page, tool="search", arguments="{}", binding_token="forged-token-123"
        )
        assert res.error
        assert "binding token" in res.text.lower()
        assert page.call_evaluates() == []

    async def test_call_missing_token_refused(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        await _discover(monkeypatch, page)
        res = await _call(monkeypatch, page, tool="search", arguments="{}")
        assert res.error
        assert page.call_evaluates() == []

    async def test_call_unknown_tool_on_origin_refused(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        res = await _call(
            monkeypatch,
            page,
            tool="admin_delete_everything",
            arguments="{}",
            binding_token=out.metadata["binding_token"],
        )
        assert res.error
        assert "Unknown WebMCP tool" in res.text
        assert page.call_evaluates() == []

    async def test_call_tool_from_other_origin_refused(self, monkeypatch):
        # Tool "steal" exists on origin B only; the tab is on origin A.
        page_b = _FakePage(ORIGIN_B + "/", _discovery_payload(_tool("steal")))
        out_b = await _discover(monkeypatch, page_b)
        page_a = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out_a = await _discover(monkeypatch, page_a)
        # Attacker presents B's token for B's tool while the tab shows A.
        res = await _call(
            monkeypatch,
            page_a,
            tool="steal",
            arguments="{}",
            binding_token=out_b.metadata["binding_token"],
        )
        assert res.error
        assert page_a.call_evaluates() == []
        # And A's own token doesn't unlock B's tool either.
        res2 = await _call(
            monkeypatch,
            page_a,
            tool="steal",
            arguments="{}",
            binding_token=out_a.metadata["binding_token"],
        )
        assert res2.error

    async def test_subdomain_isolation(self, monkeypatch):
        page = _FakePage("https://shop.example/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        token = out.metadata["binding_token"]
        page.url = "https://evillshop.example/"
        res = await _call(monkeypatch, page, tool="search", arguments="{}", binding_token=token)
        assert res.error
        assert page.call_evaluates() == []


class TestStaleCleanup:
    async def test_cross_origin_navigation_purges_registration(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        token = out.metadata["binding_token"]
        assert ORIGIN_A in _BINDINGS
        page.url = ORIGIN_B + "/"
        page._discovery = _discovery_payload()
        await _discover(monkeypatch, page)  # any follow-up op syncs the binding
        assert ORIGIN_A not in _BINDINGS
        # The old token is dead even if the tab navigates back.
        page.url = ORIGIN_A + "/"
        _, refusal = _check_binding(ORIGIN_A, "search", token)
        assert refusal


class TestServerSideSchemaValidation:
    def test_missing_required_rejected(self):
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        assert _validate_args(schema, {}) == "missing required parameter: query"

    def test_wrong_type_rejected(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        assert _validate_args(schema, {"count": 1.5}) == "parameter 'count' must be integer"
        assert _validate_args(schema, {"count": True}) == "parameter 'count' must be integer"

    def test_bool_not_coerced_to_number(self):
        schema = {"type": "object", "properties": {"n": {"type": "number"}}}
        assert _validate_args(schema, {"n": True}) == "parameter 'n' must be number"

    def test_valid_args_pass(self):
        schema = {
            "type": "object",
            "properties": {"q": {"type": "string"}, "tags": {"type": "array"}},
            "required": ["q"],
        }
        assert _validate_args(schema, {"q": "x", "tags": []}) is None

    def test_malformed_schema_is_lenient(self):
        assert _validate_args("garbage", {"a": 1}) is None
        assert _validate_args(None, {"a": 1}) is None
        assert _validate_args({"properties": "garbage"}, {"a": 1}) is None

    async def test_call_validates_before_dispatch(self, monkeypatch):
        page = _FakePage(
            ORIGIN_A + "/",
            _discovery_payload(
                _tool("search", required=["query"], properties={"query": {"type": "string"}})
            ),
        )
        out = await _discover(monkeypatch, page)
        token = out.metadata["binding_token"]
        res = await _call(
            monkeypatch, page, tool="search", arguments='{"wrong": 1}', binding_token=token
        )
        assert res.error
        assert "missing required parameter: query" in res.text
        assert page.call_evaluates() == []  # refused before touching the page


class TestDefensiveDiscoveryParsing:
    def test_non_object_result(self):
        out = _coerce_discovery("garbage from a malicious page")
        assert out["tools"] == []
        assert out["supported"] is False

    def test_malformed_tool_entries_dropped(self):
        out = _coerce_discovery(
            {
                "supported": True,
                "tools": [
                    {"name": "good", "parameters": {}},
                    {"name": 123, "parameters": {}},  # non-string name
                    {"description": "no name at all"},
                    "not a dict",
                    {"name": "", "parameters": {}},  # empty name
                    {"name": "badparams", "parameters": "nope"},
                ],
                "declarative_forms": "not a list",
            }
        )
        names = [t["name"] for t in out["tools"]]
        assert names == ["good", "badparams"]
        assert out["tools"][1]["parameters"] == {}

    def test_huge_tool_list_capped(self):
        out = _coerce_discovery(
            {
                "tools": [{"name": f"t{i}", "parameters": {}} for i in range(10000)],
            }
        )
        assert len(out["tools"]) == 200


class TestErrorPropagation:
    async def test_page_handler_error_reaches_agent(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        page.call_error = RuntimeError("handler exploded in page")
        res = await _call(
            monkeypatch,
            page,
            tool="search",
            arguments="{}",
            binding_token=out.metadata["binding_token"],
        )
        assert res.error
        assert "handler exploded in page" in res.text

    async def test_invalid_json_arguments_rejected(self, monkeypatch):
        page = _FakePage(ORIGIN_A + "/", _discovery_payload(_tool("search")))
        out = await _discover(monkeypatch, page)
        res = await _call(
            monkeypatch,
            page,
            tool="search",
            arguments="{not json",
            binding_token=out.metadata["binding_token"],
        )
        assert res.error
        assert "invalid arguments" in res.text
        assert page.call_evaluates() == []
