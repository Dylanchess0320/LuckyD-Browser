"""Night-4 browser-core audit: harness_bridge.py round 1.

No dedicated tests existed. Covers exe discovery, the HQ token lookup,
HarnessBridge HTTP plumbing (mocked httpx.AsyncClient), the response
normalizers (list_tools/read_file/background_status), the background-task
registry fallback, and HarnessSupervisor's probing / background-start
logic. No network, no exe spawned.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.harness_bridge as hb
from browser_core.harness_bridge import HarnessBridge, HarnessSupervisor

# ── fake httpx.AsyncClient ───────────────────────────────────────────


class _Resp:
    def __init__(self, payload, status=200, content_type="application/json"):
        self._payload = payload
        self.status_code = status
        self.headers = {"content-type": content_type}

    def json(self):
        return self._payload

    @property
    def text(self):
        return str(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _Client:
    """Async context manager stubbing httpx.AsyncClient."""

    def __init__(self, get_map=None, post_map=None, fail=False):
        self.get_map = get_map or {}
        self.post_map = post_map or {}
        self.fail = fail
        self.get_calls = []
        self.post_calls = []
        self.headers_seen = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.get_calls.append(url)
        self.headers_seen.append(kw.get("headers", {}))
        if self.fail:
            raise ConnectionError("refused")
        return self.get_map.get(url, _Resp({"ok": True}))

    async def post(self, url, **kw):
        self.post_calls.append((url, kw.get("json")))
        self.headers_seen.append(kw.get("headers", {}))
        if self.fail:
            raise ConnectionError("refused")
        return self.post_map.get(url, _Resp({"ok": True}))


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(hb.httpx, "AsyncClient", lambda *a, **k: client)


# ── exe discovery / token lookup ─────────────────────────────────────


def test_find_exe_honors_env_override(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "luckyd-code.exe"
    exe.write_bytes(b"fake")
    monkeypatch.setenv("LUCKYD_EXE", str(exe))
    assert hb._find_exe() == exe


def test_find_exe_missing_override_and_nothing_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LUCKYD_EXE", str(tmp_path / "nope.exe"))
    # LUCKYD_EXE missing; repo candidates don't exist in this env either —
    # if the repo checkout actually contains one, accept it but only as a file.
    found = hb._find_exe()
    assert found is None or found.exists()


def test_hq_token_env_override(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_HQ_TOKEN", "tok-123")
    assert hb._hq_token() == "tok-123"


def test_hq_token_empty_when_nothing_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LUCKYD_HQ_TOKEN", raising=False)
    monkeypatch.setattr(hb, "_find_exe", lambda: None)
    # Point the repo-root fallback lookup at an empty tmp dir (the real
    # checkout may carry a per-install token file we must not read).
    monkeypatch.setattr(hb, "__file__", str(tmp_path / "harness_bridge.py"))
    assert hb._hq_token() == ""


def test_bridge_headers_include_bearer(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_HQ_TOKEN", "sekret")
    assert hb.HarnessBridge._headers() == {"Authorization": "Bearer sekret"}


def test_bridge_headers_empty_without_token(monkeypatch) -> None:
    monkeypatch.delenv("LUCKYD_HQ_TOKEN", raising=False)
    monkeypatch.setattr(hb, "_hq_token", lambda: "")
    assert hb.HarnessBridge._headers() == {}


# ── HarnessBridge HTTP plumbing ──────────────────────────────────────


@pytest.mark.asyncio()
async def test_connect_success(monkeypatch) -> None:
    c = _Client(get_map={"http://127.0.0.1:8000/health": _Resp({})})
    _patch_client(monkeypatch, c)
    b = HarnessBridge()
    assert await b.connect() is True
    assert b.connected is True


@pytest.mark.asyncio()
async def test_connect_failure_sets_disconnected(monkeypatch) -> None:
    _patch_client(monkeypatch, _Client(fail=True))
    b = HarnessBridge()
    assert await b.connect() is False
    assert b.connected is False


@pytest.mark.asyncio()
async def test_get_returns_json_for_json_content_type(monkeypatch) -> None:
    c = _Client(get_map={"http://127.0.0.1:8000/api/models": _Resp({"models": ["a"]})})
    _patch_client(monkeypatch, c)
    b = HarnessBridge()
    assert await b._get("/api/models") == {"models": ["a"]}


@pytest.mark.asyncio()
async def test_get_returns_text_for_non_json(monkeypatch) -> None:
    c = _Client(
        get_map={"http://127.0.0.1:8000/api/models": _Resp("plain", content_type="text/plain")}
    )
    _patch_client(monkeypatch, c)
    b = HarnessBridge()
    assert await b._get("/api/models") == "plain"


@pytest.mark.asyncio()
async def test_get_raises_for_status(monkeypatch) -> None:
    c = _Client(get_map={"http://127.0.0.1:8000/x": _Resp({}, status=500)})
    _patch_client(monkeypatch, c)
    b = HarnessBridge()
    with pytest.raises(Exception, match="HTTP 500"):
        await b._get("/x")


@pytest.mark.asyncio()
async def test_post_sends_json_body(monkeypatch) -> None:
    c = _Client()
    _patch_client(monkeypatch, c)
    b = HarnessBridge()
    await b._post("/api/orchestrate", {"task": "t"})
    url, body = c.post_calls[0]
    assert url.endswith("/api/orchestrate")
    assert body == {"task": "t"}
    assert c.headers_seen[0]["Content-Type"] == "application/json"


@pytest.mark.asyncio()
async def test_list_tools_dict_variants(monkeypatch) -> None:
    b = HarnessBridge()
    b._get = AsyncMock(return_value={"tools": ["a", "b"]})
    assert await b.list_tools() == ["a", "b"]
    b._get = AsyncMock(return_value={"data": ["c"]})
    assert await b.list_tools() == ["c"]
    b._get = AsyncMock(return_value=["d"])
    assert await b.list_tools() == ["d"]
    b._get = AsyncMock(return_value="weird")
    assert await b.list_tools() == []


@pytest.mark.asyncio()
async def test_read_file_prefers_content_field(monkeypatch) -> None:
    b = HarnessBridge()
    b._post = AsyncMock(return_value={"content": "hello"})
    assert await b.read_file("x.py") == "hello"
    b._post = AsyncMock(return_value="raw text")
    assert await b.read_file("x.py") == "raw text"


@pytest.mark.asyncio()
async def test_start_background_returns_task_id(monkeypatch) -> None:
    b = HarnessBridge()
    b._post = AsyncMock(return_value={"task_id": "t-1"})
    assert await b.start_background("do it") == "t-1"
    b._post = AsyncMock(return_value={"id": "t-2"})
    assert await b.start_background("do it") == "t-2"
    b._post = AsyncMock(return_value={"weird": 1})
    with pytest.raises(RuntimeError, match="unexpected background-start"):
        await b.start_background("do it")


@pytest.mark.asyncio()
async def test_background_status_non_dict_becomes_empty(monkeypatch) -> None:
    b = HarnessBridge()
    b._get = AsyncMock(return_value=["not", "a", "dict"])
    assert await b.background_status("t-1") == {}
    b._get = AsyncMock(return_value={"status": "done"})
    assert await b.background_status("t-1") == {"status": "done"}


@pytest.mark.asyncio()
async def test_list_background_dict_and_list(monkeypatch) -> None:
    b = HarnessBridge()
    b._get = AsyncMock(return_value={"tasks": [{"id": "a"}]})
    assert await b.list_background() == [{"id": "a"}]
    b._get = AsyncMock(return_value=[{"id": "b"}])
    assert await b.list_background() == [{"id": "b"}]
    b._get = AsyncMock(return_value=None)
    assert await b.list_background() == []


@pytest.mark.asyncio()
async def test_find_background_task_found_and_missing(monkeypatch) -> None:
    b = HarnessBridge()
    b.list_background = AsyncMock(return_value=[{"id": "a", "status": "running"}, "junk"])
    assert (await b.find_background_task("a"))["status"] == "running"
    assert await b.find_background_task("zzz") is None
    b.list_background = AsyncMock(side_effect=RuntimeError("down"))
    assert await b.find_background_task("a") is None


@pytest.mark.asyncio()
async def test_search_memory_get_then_post_fallback(monkeypatch) -> None:
    b = HarnessBridge()
    b._get = AsyncMock(return_value={"hits": 1})
    assert await b.search_memory("q") == {"hits": 1}
    b._get = AsyncMock(side_effect=RuntimeError("get 404s"))
    b._post = AsyncMock(return_value={"hits": 2})
    assert await b.search_memory("q") == {"hits": 2}
    args = b._post.call_args[0]
    assert args[0] == "/api/brain/search" and args[1] == {"query": "q"}


@pytest.mark.asyncio()
async def test_get_memory_stats_falls_back_to_brain(monkeypatch) -> None:
    b = HarnessBridge()
    b._get = AsyncMock(side_effect=[RuntimeError("nope"), {"stats": 1}])
    assert await b.get_memory_stats() == {"stats": 1}
    assert b._get.call_args_list[1][0][0] == "/api/brain"


@pytest.mark.asyncio()
async def test_get_endpoints_sorts(monkeypatch) -> None:
    b = HarnessBridge()
    b._get = AsyncMock(
        return_value={
            "paths": {
                "/api/tools": {"get": {}},
                "/api/run": {"post": {}, "get": {}},
            }
        }
    )
    assert await b.get_endpoints() == [
        "GET /api/run",
        "GET /api/tools",
        "POST /api/run",
    ]


# ── start/stop lifecycle ─────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_start_when_already_up_short_circuits(monkeypatch) -> None:
    b = HarnessBridge()
    b.connect = AsyncMock(return_value=True)
    assert await b.start() is True


@pytest.mark.asyncio()
async def test_start_without_exe_raises(monkeypatch) -> None:
    b = HarnessBridge()
    b.connect = AsyncMock(return_value=False)
    monkeypatch.setattr(hb, "_find_exe", lambda: None)
    with pytest.raises(FileNotFoundError, match="harness backend not found"):
        await b.start()


@pytest.mark.asyncio()
async def test_start_spawns_exe_and_waits(monkeypatch) -> None:
    b = HarnessBridge()
    exe = MagicMock()
    exe.suffix = ".exe"
    monkeypatch.setattr(hb, "_find_exe", lambda: exe)

    states = iter([False, False, True])

    async def _connect(timeout=10.0):
        return next(states)

    b.connect = _connect
    proc = MagicMock()
    proc.poll.return_value = None
    spawned = {}
    monkeypatch.setattr(
        hb.subprocess, "Popen", lambda *a, **k: spawned.setdefault("p", proc) or proc
    )
    monkeypatch.setattr(hb.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(hb.asyncio, "sleep", AsyncMock())
    assert await b.start(wait=True, timeout=30.0) is True
    assert b._harness_proc is proc


@pytest.mark.asyncio()
async def test_start_wait_false_returns_immediately(monkeypatch) -> None:
    b = HarnessBridge()
    b.connect = AsyncMock(return_value=False)
    exe = MagicMock()
    exe.suffix = ".exe"
    monkeypatch.setattr(hb, "_find_exe", lambda: exe)
    monkeypatch.setattr(hb.subprocess, "Popen", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(hb.sys, "platform", "linux", raising=False)
    assert await b.start(wait=False) is False


@pytest.mark.asyncio()
async def test_start_py_launcher_uses_interpreter(monkeypatch) -> None:
    b = HarnessBridge()
    b.connect = AsyncMock(return_value=False)
    exe = MagicMock()
    exe.suffix = ".py"
    monkeypatch.setattr(hb, "_find_exe", lambda: exe)
    popen = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(hb.subprocess, "Popen", popen)
    monkeypatch.setattr(hb.sys, "platform", "linux", raising=False)
    await b.start(wait=False)
    cmd = popen.call_args[0][0]
    assert cmd[0] == hb.sys.executable


@pytest.mark.asyncio()
async def test_stop_terminates_only_own_proc(monkeypatch) -> None:
    b = HarnessBridge()
    proc = MagicMock()
    b._harness_proc = proc
    b._connected = True
    await b.stop()
    proc.terminate.assert_called_once()
    assert b._harness_proc is None and b.connected is False


@pytest.mark.asyncio()
async def test_stop_kills_on_timeout(monkeypatch) -> None:
    import subprocess as sp

    b = HarnessBridge()
    proc = MagicMock()
    proc.wait.side_effect = sp.TimeoutExpired("cmd", 5.0)
    b._harness_proc = proc
    await b.stop()
    proc.kill.assert_called_once()
    assert b._harness_proc is None


@pytest.mark.asyncio()
async def test_stop_without_proc_is_safe(monkeypatch) -> None:
    b = HarnessBridge()
    b._connected = True
    await b.stop()
    assert b.connected is False
    await b.close()  # close delegates to stop


# ── HarnessSupervisor ────────────────────────────────────────────────


def _sup(monkeypatch, status_code=200, fail=False):
    class _R:
        def __init__(self, code):
            self.status_code = code

    def _get(url, timeout=None):
        if fail:
            raise ConnectionError("down")
        return _R(status_code)

    monkeypatch.setattr(hb.httpx, "get", _get)
    return HarnessSupervisor()


def test_supervisor_probe_up(monkeypatch) -> None:
    s = _sup(monkeypatch, 200)
    st = s.probe()
    assert st["up"] is True
    assert st["url"] == "http://127.0.0.1:8000"
    assert st["tools"] is None


def test_supervisor_probe_down_clears_tools(monkeypatch) -> None:
    s = _sup(monkeypatch, 500)
    s.last["tools"] = 98
    st = s.probe()
    assert st["up"] is False
    assert st["tools"] is None


def test_supervisor_probe_exception_is_down(monkeypatch) -> None:
    s = _sup(monkeypatch, fail=True)
    assert s.probe()["up"] is False


def test_supervisor_status_is_copy(monkeypatch) -> None:
    s = _sup(monkeypatch)
    st = s.status()
    st["up"] = True
    assert s.last["up"] is False  # mutation of the copy doesn't leak


def test_ensure_started_skips_when_starting(monkeypatch) -> None:
    s = _sup(monkeypatch)
    s.last["starting"] = True
    s.ensure_started()
    assert s.last["starting"] is True  # untouched, no thread spawned


def test_ensure_started_skips_when_up(monkeypatch) -> None:
    s = _sup(monkeypatch, 200)
    s.ensure_started()
    assert s.last["starting"] is False


def test_ensure_started_force_clears_error_and_spawns(monkeypatch) -> None:
    s = _sup(monkeypatch, fail=True)
    s.last["error"] = "old"
    started = []
    s.start_blocking = lambda timeout=25.0: started.append(timeout) or (True, "")
    s.ensure_started(force=True, timeout=3.0)
    assert s.last["error"] is None
    for _ in range(100):
        if started:
            break
        import time as _t

        _t.sleep(0.02)
    assert started == [3.0]


def test_start_blocking_success(monkeypatch) -> None:
    s = _sup(monkeypatch)
    s.bridge.start = AsyncMock(return_value=True)
    s.bridge.list_tools = AsyncMock(return_value=["a", "b", "c"])
    ok, err = s.start_blocking(timeout=2.0)
    assert ok is True and err == ""
    assert s.last["up"] is True
    assert s.last["tools"] == 3
    assert s.last["starting"] is False


def test_start_blocking_never_up(monkeypatch) -> None:
    s = _sup(monkeypatch)
    s.bridge.start = AsyncMock(return_value=False)
    ok, err = s.start_blocking(timeout=2.0)
    assert ok is False
    assert "did not answer" in err and "2s" in err
    assert s.last["starting"] is False


def test_start_blocking_missing_exe(monkeypatch) -> None:
    s = _sup(monkeypatch)
    s.bridge.start = AsyncMock(side_effect=FileNotFoundError("no exe"))
    ok, err = s.start_blocking()
    assert ok is False and err == "no exe"
    assert s.last["up"] is False


def test_start_blocking_generic_error(monkeypatch) -> None:
    s = _sup(monkeypatch)
    s.bridge.start = AsyncMock(side_effect=ValueError("weird"))
    ok, err = s.start_blocking()
    assert ok is False
    assert err == "ValueError: weird"
    assert s.last["starting"] is False


def test_bridge_custom_host_port() -> None:
    b = HarnessBridge(host="10.0.0.5", port=9000)
    assert b.base == "http://10.0.0.5:9000"
    assert HarnessSupervisor(b).url == b.base
