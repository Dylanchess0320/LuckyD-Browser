"""Night-6 wave-2 tests: tools/harness_tool.py.

The harness is a local exe web server; here httpx.AsyncClient is replaced
with a fake that routes (method, path) to canned responses or raises.
Covers: _get/_post success and error shapes, action routing + validation,
endpoint listing, fallback paths (brain_search GET->POST, memories
plural->singular), output truncation, and unreachable-harness handling.
"""

from __future__ import annotations

import json
import types

import httpx
import pytest

import tools.harness_tool as ht
from tools.registry import registry

BASE = "http://127.0.0.1:8000"


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


class _FakeResp:
    def __init__(self, payload=None, text="", status=200, json_ct=True):
        self._payload = payload
        self._text = text
        self.status_code = status
        self.headers = {"content-type": "application/json" if json_ct else "text/plain"}

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://x")
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError("boom", request=req, response=resp)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def text(self):
        return self._text


@pytest.fixture
def fake_http(monkeypatch):
    """Route (method, path) -> _FakeResp or exception instance."""
    calls = []
    routes = {}

    def path_of(url):
        assert url.startswith(BASE), url
        return url[len(BASE) :]

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def _dispatch(self, method, url, **kw):
            calls.append((method, path_of(url), kw))
            target = routes.get((method, path_of(url)))
            if isinstance(target, Exception):
                raise target
            assert target is not None, f"no route for {method} {path_of(url)}"
            return target

        async def get(self, url, **kw):
            return await self._dispatch("GET", url, **kw)

        async def post(self, url, **kw):
            return await self._dispatch("POST", url, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return types.SimpleNamespace(calls=calls, routes=routes)


def _json(payload, status=200):
    return _FakeResp(payload=payload, status=status)


# ── _get / _post primitives ────────────────────────────────────────────


class TestGetPost:
    async def test_get_json(self, fake_http):
        fake_http.routes[("GET", "/openapi.json")] = _json({"paths": {"/a": {}}})
        data, err = await ht._get("/openapi.json")
        assert err is None
        assert data == {"paths": {"/a": {}}}
        method, path, _kw = fake_http.calls[0]
        assert (method, path) == ("GET", "/openapi.json")

    async def test_get_plain_text(self, fake_http):
        fake_http.routes[("GET", "/")] = _FakeResp(text="hi", json_ct=False)
        data, err = await ht._get("/")
        assert err is None
        assert data == "hi"

    async def test_get_http_error_with_detail(self, fake_http):
        req = httpx.Request("GET", "http://x")
        resp = httpx.Response(404, json={"detail": "nope"}, request=req)
        err_obj = httpx.HTTPStatusError("nf", request=req, response=resp)
        # route via exception through the fake client
        fake_http.routes[("GET", "/x")] = err_obj
        data, err = await ht._get("/x")
        assert data is None
        assert err == "HTTP 404: nope"

    async def test_get_http_error_without_json_detail(self, fake_http):
        req = httpx.Request("GET", "http://x")
        resp = httpx.Response(500, text="oops", request=req)
        fake_http.routes[("GET", "/x")] = httpx.HTTPStatusError(
            "server blew up", request=req, response=resp
        )
        data, err = await ht._get("/x")
        assert data is None
        assert err.startswith("HTTP 500: ")

    async def test_get_unreachable(self, fake_http):
        fake_http.routes[("GET", "/x")] = httpx.ConnectError("refused")
        data, err = await ht._get("/x")
        assert data is None
        assert err.startswith(f"Cannot reach harness at {BASE}")

    async def test_post_sends_json_body(self, fake_http):
        fake_http.routes[("POST", "/api/write-file")] = _json({"ok": True})
        data, err = await ht._post("/api/write-file", {"path": "a", "content": "b"})
        assert err is None
        assert data == {"ok": True}
        method, path, kw = fake_http.calls[0]
        assert (method, path) == ("POST", "/api/write-file")
        assert kw["json"] == {"path": "a", "content": "b"}
        assert kw["headers"] == {"Content-Type": "application/json"}

    async def test_post_none_body_becomes_empty_dict(self, fake_http):
        fake_http.routes[("POST", "/api/brain/search")] = _json([])
        await ht._post("/api/brain/search", None)
        _, _, kw = fake_http.calls[0]
        assert kw["json"] == {}

    async def test_fmt_non_serializable(self):
        assert ht._fmt({"x": 1}) == json.dumps({"x": 1}, indent=2, ensure_ascii=False)
        assert ht._fmt(object()).startswith("<object object")
        assert ht._fmt("s") == "s"


# ── action routing / validation ────────────────────────────────────────


class TestRouting:
    async def test_unknown_action(self):
        r = await _tool("Harness").execute(action="teleport")
        assert not _ok(r)
        assert r.text == "Unknown action: teleport"

    @pytest.mark.parametrize(
        "action,kw",
        [
            ("read_file", {}),
            ("write_file", {}),
            ("edit_file", {"path": "p"}),
            ("brain_search", {}),
            ("orchestrate", {}),
            ("parallel", {}),
        ],
    )
    async def test_required_args(self, action, kw):
        r = await _tool("Harness").execute(action=action, **kw)
        assert not _ok(r)
        assert "required" in r.text

    async def test_execute_wraps_exceptions(self, monkeypatch):
        async def boom(**kw):
            raise RuntimeError("kaput")

        monkeypatch.setattr(_tool("Harness"), "_health", boom)
        r = await _tool("Harness").execute(action="health")
        assert not _ok(r)
        assert r.text == "Harness error: kaput"


# ── harness status ─────────────────────────────────────────────────────


class TestHarnessStatus:
    async def test_lists_endpoints_sorted_capped(self, fake_http):
        paths = {f"/z{i}": {"get": {}} for i in range(50)}
        paths["/api/files"] = {"get": {}, "post": {}}
        fake_http.routes[("GET", "/openapi.json")] = _json({"paths": paths})
        r = await _tool("Harness").execute(action="harness")
        assert _ok(r)
        assert f"✅ **Harness** at {BASE}" in r.text
        assert "**52 endpoints**" in r.text  # 50 + 2 methods on /api/files
        lines = r.text.splitlines()
        assert "  GET    /api/files" in lines
        # capped at 40 endpoint lines + 2 header lines
        assert len(lines) == 42

    async def test_unreachable(self, fake_http):
        fake_http.routes[("GET", "/openapi.json")] = httpx.ConnectError("down")
        fake_http.routes[("GET", "/")] = httpx.ConnectError("down")
        r = await _tool("Harness").execute(action="harness")
        assert not _ok(r)
        assert r.text.startswith(f"❌ Harness unreachable at {BASE}")

    async def test_openapi_fails_but_root_ok(self, fake_http):
        fake_http.routes[("GET", "/openapi.json")] = httpx.ConnectError("down")
        fake_http.routes[("GET", "/")] = _FakeResp(text="ok", json_ct=False)
        r = await _tool("Harness").execute(action="harness")
        assert _ok(r)
        assert "**0 endpoints**" in r.text


# ── file actions ───────────────────────────────────────────────────────


class TestFileActions:
    async def test_list_files_formats(self, fake_http):
        fake_http.routes[("GET", "/api/files")] = _json(
            {
                "current": "/proj",
                "files": [
                    {"name": "a.py", "size": 10, "is_dir": False},
                    {"name": "sub", "size": 0, "is_dir": True},
                ],
            }
        )
        r = await _tool("Harness").execute(action="list_files")
        assert _ok(r)
        assert "**/proj**" in r.text
        assert "📄 a.py (10B)" in r.text
        assert "📁 sub/" in r.text
        assert r.title == "📁 Files (2)"

    async def test_list_files_error(self, fake_http):
        fake_http.routes[("GET", "/api/files")] = httpx.ConnectError("down")
        r = await _tool("Harness").execute(action="list_files")
        assert not _ok(r)
        assert r.text.startswith("Error: Cannot reach harness")

    async def test_read_file_truncates(self, fake_http):
        fake_http.routes[("POST", "/api/read-file")] = _json({"content": "z" * 9000})
        r = await _tool("Harness").execute(action="read_file", path="a.txt")
        assert _ok(r)
        assert len(r.text) == 8000
        assert r.title == "📖 a.txt"
        _, _, kw = fake_http.calls[0]
        assert kw["json"] == {"path": "a.txt"}

    async def test_write_file(self, fake_http):
        fake_http.routes[("POST", "/api/write-file")] = _json({"ok": True})
        r = await _tool("Harness").execute(action="write_file", path="a.txt", content="hello")
        assert _ok(r)
        assert r.text == "✅ Written 5B to a.txt"

    async def test_edit_file(self, fake_http):
        fake_http.routes[("POST", "/api/edit-file")] = _json({"ok": True})
        r = await _tool("Harness").execute(
            action="edit_file", path="a.txt", old_string="x", new_string="y"
        )
        assert _ok(r)
        assert r.text == "✅ Edited a.txt"
        _, _, kw = fake_http.calls[0]
        assert kw["json"] == {"path": "a.txt", "old_string": "x", "new_string": "y"}


# ── brain + fallbacks ──────────────────────────────────────────────────


class TestBrain:
    async def test_brain_search_get_first(self, fake_http):
        fake_http.routes[("GET", "/api/brain/search?q=hello%20world")] = _json([{"t": 1}])
        r = await _tool("Harness").execute(action="brain_search", query="hello world")
        assert _ok(r)
        assert "🧠 hello world" in r.title

    async def test_brain_search_get_fails_falls_back_to_post(self, fake_http):
        fake_http.routes[("GET", "/api/brain/search?q=x")] = httpx.ConnectError("down")
        fake_http.routes[("POST", "/api/brain/search")] = _json([{"t": 2}])
        r = await _tool("Harness").execute(action="brain_search", query="x")
        assert _ok(r)
        assert '"t": 2' in r.text

    async def test_brain_stats_fallback_endpoint(self, fake_http):
        fake_http.routes[("GET", "/api/brain/stats")] = httpx.ConnectError("down")
        fake_http.routes[("GET", "/api/brain")] = _json({"memories": 3})
        r = await _tool("Harness").execute(action="brain_stats")
        assert _ok(r)
        assert '"memories": 3' in r.text
        assert r.title == "🧠 Brain Stats"

    async def test_list_memories_fallback_endpoint(self, fake_http):
        fake_http.routes[("GET", "/api/memories")] = httpx.ConnectError("down")
        fake_http.routes[("GET", "/api/memory")] = _json([{"id": 1}])
        r = await _tool("Harness").execute(action="list_memories")
        assert _ok(r)
        assert r.title == "💭 Memories"

    async def test_list_tools_formats_dict_and_str(self, fake_http):
        fake_http.routes[("GET", "/api/tools")] = _json({"tools": [{"name": "Read"}, "Write"]})
        r = await _tool("Harness").execute(action="list_tools")
        assert _ok(r)
        assert "🔧 Read" in r.text and "🔧 Write" in r.text
        assert r.title == "🔧 Tools (2)"

    async def test_output_truncated(self, fake_http):
        fake_http.routes[("GET", "/api/context")] = _json({"big": "z" * 5000})
        r = await _tool("Harness").execute(action="get_context")
        assert _ok(r)
        assert len(r.text) == 4000

    async def test_health(self, fake_http):
        fake_http.routes[("GET", "/health")] = _json({"status": "ok"})
        r = await _tool("Harness").execute(action="health")
        assert _ok(r)
        assert r.title == "💚 Health"

    async def test_health_failure(self, fake_http):
        fake_http.routes[("GET", "/health")] = httpx.ConnectError("down")
        r = await _tool("Harness").execute(action="health")
        assert not _ok(r)
        assert r.text.startswith("Health failed:")

    async def test_orchestrate_posts_task(self, fake_http):
        fake_http.routes[("POST", "/api/orchestrate")] = _json({"plan": []})
        r = await _tool("Harness").execute(action="orchestrate", task_prompt="do it")
        assert _ok(r)
        assert r.title == "🎭 Orchestration"
        _, _, kw = fake_http.calls[0]
        assert kw["json"] == {"task": "do it"}

    @pytest.mark.parametrize(
        "action,path,title",
        [
            ("list_models", "/api/models", "🤖 Models"),
            ("list_tasks", "/api/tasks", "📋 Tasks"),
            ("list_sessions", "/api/sessions", "💾 Sessions"),
            ("get_settings", "/api/settings", "⚙️ Settings"),
            ("get_context", "/api/context", "📐 Context"),
            ("get_cost", "/api/cost", "💰 Cost"),
        ],
    )
    async def test_simple_get_actions(self, fake_http, action, path, title):
        fake_http.routes[("GET", path)] = _json({"ok": True})
        r = await _tool("Harness").execute(action=action)
        assert _ok(r)
        assert r.title == title
        assert '"ok": true' in r.text

    async def test_parallel(self, fake_http):
        fake_http.routes[("POST", "/api/parallel")] = _json({"done": 2})
        r = await _tool("Harness").execute(action="parallel", task_prompt="go")
        assert _ok(r)
        assert r.title == "⚡ Parallel"

    async def test_simple_actions_error(self, fake_http):
        fake_http.routes[("GET", "/api/models")] = httpx.ConnectError("down")
        r = await _tool("Harness").execute(action="list_models")
        assert not _ok(r)
        assert r.text.startswith("Error: Cannot reach harness")

    async def test_get_generic_exception(self, fake_http):
        fake_http.routes[("GET", "/x")] = RuntimeError("weird")
        data, err = await ht._get("/x")
        assert data is None
        assert err == "weird"

    async def test_post_generic_exception(self, fake_http):
        fake_http.routes[("POST", "/x")] = RuntimeError("weird")
        data, err = await ht._post("/x", {})
        assert data is None
        assert err == "weird"
