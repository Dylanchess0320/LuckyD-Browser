"""Night-6 wave-2 tests: tools/web_tools.py.

No real network is used: the module's `_safe_opener` is stubbed with a fake
opener, `socket.getaddrinfo` is monkeypatched for DNS-based SSRF checks, and
the ddgs/duckduckgo_search imports are faked via sys.modules.
Covers: SSRF URL validation, HttpTool request shaping (incl. the header
non-mutation regression), WebFetch HTML extraction, and WebSearch's
library -> HTML fallback chain.
"""

from __future__ import annotations

import socket
import sys
import types
import urllib.error

import pytest

import tools.web_tools as wt
from tools.registry import registry


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


# ── fake opener ────────────────────────────────────────────────────────


class _FakeHTTPResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, body: bytes = b"", status: int = 200, exc=None):
        self.body = body
        self.status = status
        self.exc = exc
        self.requests = []  # (Request, timeout) pairs

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if self.exc is not None:
            raise self.exc
        return _FakeHTTPResponse(self.body, self.status)


@pytest.fixture
def fake_opener(monkeypatch):
    opener = _FakeOpener()
    monkeypatch.setattr(wt, "_safe_opener", lambda: opener)
    return opener


def _req_headers(req):
    return {k.lower(): v for k, v in req.header_items()}


# ── SSRF validation ────────────────────────────────────────────────────


class TestAssertPublicUrl:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            wt._assert_public_url("ftp://example.com/x")

    def test_rejects_missing_hostname(self):
        with pytest.raises(ValueError, match="no hostname"):
            wt._assert_public_url("http:///nohost")

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",  # ipv4-mapped loopback must not smuggle
            "http://224.0.0.1/",  # multicast
        ],
    )
    def test_blocks_non_public_literal_ips(self, url):
        with pytest.raises(ValueError, match="Blocked SSRF target"):
            wt._assert_public_url(url)

    def test_blocks_dns_resolving_to_private(self, monkeypatch):
        def fake_gai(host, *a, **k):
            return [(socket.AF_INET, 0, 0, "", ("10.9.9.9", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        with pytest.raises(ValueError, match="Blocked SSRF target"):
            wt._assert_public_url("http://evil.example/")

    def test_dns_failure_raises(self, monkeypatch):
        def fake_gai(host, *a, **k):
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        with pytest.raises(ValueError, match="DNS resolution failed"):
            wt._assert_public_url("http://nonexistent.invalid/")

    def test_public_ip_returned(self, monkeypatch):
        def fake_gai(host, *a, **k):
            return [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        assert wt._assert_public_url("http://example.com/") == ["93.184.216.34"]

    def test_redirect_handler_revalidates(self, monkeypatch):
        # The redirect guard calls _assert_public_url on the new URL.
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("169.254.169.254", 0))],
        )
        handler = wt._SSRFRedirectHandler()
        with pytest.raises(ValueError, match="Blocked SSRF target"):
            handler.redirect_request(None, None, 302, "Found", {}, "http://x/")


# ── HttpTool ───────────────────────────────────────────────────────────


class TestHttpTool:
    async def test_get_pretty_prints_json(self, fake_opener):
        fake_opener.body = b'{"b":2,"a":1}'
        r = await _tool("Http").execute(url="https://example.com/api")
        assert _ok(r)
        assert r.text == '{\n  "b": 2,\n  "a": 1\n}'
        assert r.title == "HTTP GET https://example.com/api -> 200"
        assert r.metadata == {
            "status_code": 200,
            "method": "GET",
            "url": "https://example.com/api",
        }

    async def test_long_body_truncated(self, fake_opener):
        fake_opener.body = b"z" * 9000
        r = await _tool("Http").execute(url="https://example.com/")
        assert _ok(r)
        assert r.text == "z" * 8000 + "\n... [truncated]"

    async def test_status_400_is_error(self, fake_opener):
        fake_opener.body = b"nope"
        fake_opener.status = 404
        r = await _tool("Http").execute(url="https://example.com/missing")
        assert not _ok(r)
        assert r.text == "nope"

    async def test_http_error_body_capped(self, fake_opener):
        fake_opener.exc = urllib.error.HTTPError(
            "https://example.com/", 500, "Server Error", {}, None
        )
        r = await _tool("Http").execute(url="https://example.com/")
        assert not _ok(r)
        assert r.metadata["status_code"] == 500

    async def test_caller_headers_not_mutated(self, fake_opener):
        caller = {"X-Custom": "1"}
        r = await _tool("Http").execute(
            url="https://example.com/", headers=caller, bearer_token="tok"
        )
        assert _ok(r)
        assert caller == {"X-Custom": "1"}  # regression: was mutated in place
        sent = _req_headers(fake_opener.requests[0][0])
        assert sent["x-custom"] == "1"
        assert "authorization" in sent  # bearer injected into the copy
        assert "content-type" not in sent  # no json_body -> no content-type added

    async def test_bearer_refused_over_http(self, fake_opener):
        r = await _tool("Http").execute(url="http://example.com/", bearer_token="tok")
        assert not _ok(r)
        assert "only sent over HTTPS" in r.text
        assert fake_opener.requests == []  # no request went out

    async def test_json_body_sets_content_type(self, fake_opener):
        r = await _tool("Http").execute(
            url="https://example.com/post", method="POST", json_body={"a": 1}
        )
        assert _ok(r)
        req, _ = fake_opener.requests[0]
        assert _req_headers(req)["content-type"] == "application/json"
        assert req.data == b'{"a": 1}'

    async def test_existing_content_type_preserved(self, fake_opener):
        await _tool("Http").execute(
            url="https://example.com/",
            method="POST",
            headers={"Content-Type": "text/csv"},
            json_body={"a": 1},
        )
        sent = _req_headers(fake_opener.requests[0][0])
        assert sent["content-type"] == "text/csv"  # setdefault, not overwrite

    async def test_timeout_clamped(self, fake_opener):
        await _tool("Http").execute(url="https://example.com/", timeout_sec=500)
        _, timeout = fake_opener.requests[0]
        assert timeout == 120

    async def test_bad_scheme(self, fake_opener):
        r = await _tool("Http").execute(url="ftp://example.com/x")
        assert not _ok(r)
        assert "Unsupported URL scheme" in r.text


# ── WebFetchTool ───────────────────────────────────────────────────────


class TestWebFetchTool:
    def test_extracts_text_strips_junk(self, fake_opener):
        fake_opener.body = (
            b"<html><head><title>T</title><script>var x=1;</script>"
            b"<style>.a{}</style></head><body><nav>links</nav>"
            b"<h1>Hi &amp; bye</h1><p>Body.</p></body></html>"
        )
        r = wt.WebFetchTool._do_fetch("https://example.com/")
        assert _ok(r)
        assert "Hi & bye" in r.text and "Body." in r.text
        assert "var x=1" not in r.text
        assert "links" not in r.text  # nav stripped
        assert "<h1>" not in r.text
        assert r.title == "Fetched https://example.com/"
        assert r.metadata["url"] == "https://example.com/"

    def test_truncates_at_10000(self, fake_opener):
        fake_opener.body = ("<p>" + "w" * 11000 + "</p>").encode()
        r = wt.WebFetchTool._do_fetch("https://example.com/")
        assert r.text == "w" * 10000 + "\n... [truncated]"

    def test_http_error(self, fake_opener):
        fake_opener.exc = urllib.error.HTTPError("https://example.com/", 404, "Not Found", {}, None)
        r = wt.WebFetchTool._do_fetch("https://example.com/")
        assert not _ok(r)
        assert r.text == "HTTP 404: Not Found"

    async def test_execute_rejects_bad_scheme(self, fake_opener):
        r = await _tool("WebFetch").execute(url="file:///etc/passwd")
        assert not _ok(r)
        assert "Unsupported URL scheme" in r.text
        assert fake_opener.requests == []

    async def test_execute_sends_user_agent(self, fake_opener):
        fake_opener.body = b"<p>ok</p>"
        r = await _tool("WebFetch").execute(url="https://example.com/")
        assert _ok(r)
        sent = _req_headers(fake_opener.requests[0][0])
        assert "CodingAgent" in sent["user-agent"]


# ── WebSearchTool ──────────────────────────────────────────────────────


def _fake_ddgs_module(monkeypatch, results=None, exc=None):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=10):
            assert max_results == 10
            if exc is not None:
                raise exc
            return results or []

    mod = types.ModuleType("ddgs")
    mod.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", mod)


class TestWebSearchTool:
    async def test_ddgs_library_path(self, monkeypatch):
        _fake_ddgs_module(
            monkeypatch,
            results=[
                {"title": "T1", "href": "https://a.example/1", "body": "B1"},
                {"title": "T2", "href": "https://a.example/2", "body": "B2"},
            ],
        )
        r = await _tool("WebSearch").execute(query="q")
        assert _ok(r)
        assert "Results for: q" in r.text
        assert "[1] T1" in r.text and "https://a.example/1" in r.text
        assert "B2" in r.text
        assert r.metadata == {"query": "q", "results": 2}

    async def test_ddgs_empty_results(self, monkeypatch):
        _fake_ddgs_module(monkeypatch, results=[])
        r = await _tool("WebSearch").execute(query="q")
        assert _ok(r)
        assert r.text == "No results for: q"

    async def test_old_package_name_fallback(self, monkeypatch):
        # `from ddgs import DDGS` fails -> `from duckduckgo_search import DDGS`
        monkeypatch.setitem(sys.modules, "ddgs", None)

        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=10):
                return [{"title": "Old", "href": "https://o.example/", "body": ""}]

        mod = types.ModuleType("duckduckgo_search")
        mod.DDGS = FakeDDGS
        monkeypatch.setitem(sys.modules, "duckduckgo_search", mod)
        r = await _tool("WebSearch").execute(query="q")
        assert _ok(r)
        assert "[1] Old" in r.text

    async def test_library_failure_falls_back_to_html(self, monkeypatch, fake_opener):
        monkeypatch.setitem(sys.modules, "ddgs", None)
        monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
        fake_opener.body = (
            b"<html><body>"
            b'<a class="result__a" href="https://example.com/one">First <b>Result</b></a>'
            b'<a class="result__snippet">First snippet</a>'
            b'<a class="result__a" href="https://example.com/two">Second</a>'
            b'<a class="result__snippet">Second snippet</a>'
            b"</body></html>"
        )
        r = await _tool("WebSearch").execute(query="test query")
        assert _ok(r)
        assert "[1] First Result" in r.text
        assert "https://example.com/one" in r.text
        assert "First snippet" in r.text
        assert "[2] Second" in r.text
        assert r.metadata == {"query": "test query", "results": 2}

    async def test_html_no_results(self, monkeypatch, fake_opener):
        monkeypatch.setitem(sys.modules, "ddgs", None)
        monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
        fake_opener.body = b"<html><body>nothing here</body></html>"
        r = await _tool("WebSearch").execute(query="zzz")
        assert _ok(r)
        assert r.text == "No results for: zzz"

    async def test_both_paths_fail_combines_errors(self, monkeypatch, fake_opener):
        _fake_ddgs_module(monkeypatch, exc=RuntimeError("api down"))
        fake_opener.exc = RuntimeError("html down")
        r = await _tool("WebSearch").execute(query="q")
        assert not _ok(r)
        assert "Search error (library: api down)" in r.text
        assert "(fallback: html down)" in r.text
