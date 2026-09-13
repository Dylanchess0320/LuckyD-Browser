"""Night-4 browser-core audit: control_server.py route handlers round 2.

Night-1 covered: auth model, host/origin defenses, body-size cap, basic
dispatch (/tabs, /navigate, /tab/close, /help), traversal guard.
This file extends into the remaining route handlers: tab lifecycle,
snapshot/act/eval/ask, workflows, schedules, network capture, extract,
theme, research swarm, /hq gateway, /status enrichment, and the
BrowserControlServer lifecycle. Mocked at the HTTP boundary — real
ThreadingHTTPServer, fake backend object.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

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

import browser_core.control_server as control_server
from browser_core import tile_registry
from browser_core.control_server import BrowserControlServer, make_handler

TOKEN = "night4-sentinel-token"


class _Backend:
    """Full backend protocol — records calls for assertion."""

    def __init__(self):
        self.calls = []

    def _rec(self, name, *args):
        self.calls.append((name, *args))

    def status(self):
        return {"ok": True}

    def ai_info(self):
        return {"provider": "kimi"}

    def tabs(self):
        return [{"index": 0, "url": "https://a.com", "title": "A", "active": True}]

    def navigate(self, url, new_tab):
        self._rec("navigate", url, new_tab)
        return url

    def new_tab(self, url):
        self._rec("new_tab", url)
        return 3

    def activate_tab(self, index):
        self._rec("activate_tab", index)
        return index

    def close_tab(self, index):
        self._rec("close_tab", index)
        return True

    def snapshot(self):
        return {"url": "https://a.com", "elements": "[0] <a> x"}

    def act(self, body):
        if body.get("action") == "boom":
            raise RuntimeError("act exploded")
        return "acted"

    def eval_js(self, js):
        return {"echo": js}

    def screenshot(self, url):
        self._rec("screenshot", url)
        return "aGVsbG8="

    def ask(self, question, provider):
        self._rec("ask", question, provider)
        return "answer!"

    def list_workflows(self):
        return {"workflows": ["w1"]}

    def start_recording(self, name):
        self._rec("start_recording", name)
        return name

    def stop_recording(self):
        return {"name": "w1", "steps": 2}

    def replay_workflow(self, name):
        self._rec("replay_workflow", name)
        return {"replayed": name}

    def delete_workflow(self, name):
        self._rec("delete_workflow", name)
        return True

    def schedule_list(self):
        return {"schedules": [], "intervals": {"0": "Off"}}

    def schedule_set(self, name, every_min):
        self._rec("schedule_set", name, every_min)
        return {"name": name, "every_min": every_min}

    def extract(self, instruction, schema, provider):
        self._rec("extract", instruction, schema, provider)
        return {"field": "value"}

    def set_theme(self, name):
        self._rec("set_theme", name)
        return name

    def netmon_start(self, url):
        self._rec("netmon_start", url)
        return {"started": True, "target": url}

    def netmon_stop(self):
        return {"stopped": True}

    def netmon_clear(self):
        return {"cleared": True}

    def netmon_events(self, since):
        self._rec("netmon_events", since)
        return {"rows": [], "seq": 0}

    def netmon_har(self):
        return {"log": {"entries": []}}


@pytest.fixture()
def server():
    handler = make_handler(_Backend(), token=TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()
    t.join(timeout=5)


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    out = (
        resp.status,
        resp.getheader("Content-Type", ""),
        resp.getheader("Content-Disposition", ""),
        data,
    )
    conn.close()
    return out


def _authz(extra=None):
    h = {"Authorization": f"Bearer {TOKEN}"}
    if extra:
        h.update(extra)
    return h


# ── tab lifecycle ────────────────────────────────────────────────────


def test_tab_new_with_url(server) -> None:
    status, _, _, data = _req(
        server, "POST", "/tab/new", body={"url": "https://b.com"}, headers=_authz()
    )
    assert status == 200
    assert json.loads(data)["index"] == 3


def test_tab_new_without_url_passes_none(server) -> None:
    status, _, _, data = _req(server, "POST", "/tab/new", body={}, headers=_authz())
    assert status == 200
    assert json.loads(data)["index"] == 3


def test_tab_activate(server) -> None:
    status, _, _, data = _req(server, "POST", "/tab/activate", body={"index": 2}, headers=_authz())
    assert status == 200
    assert json.loads(data)["index"] == 2


def test_tab_activate_non_int_index_is_500(server) -> None:
    status, _, _, data = _req(
        server, "POST", "/tab/activate", body={"index": "abc"}, headers=_authz()
    )
    assert status == 500
    assert json.loads(data)["ok"] is False


def test_tab_close_default_index(server) -> None:
    status, _, _, data = _req(server, "POST", "/tab/close", body={}, headers=_authz())
    assert status == 200
    assert json.loads(data)["closed"] is True


def test_navigate_new_tab_flag(server) -> None:
    status, _, _, data = _req(
        server,
        "POST",
        "/navigate",
        body={"url": "https://c.com", "new_tab": True},
        headers=_authz(),
    )
    assert status == 200
    assert json.loads(data)["url"] == "https://c.com"


# ── snapshot / act / eval / ask ──────────────────────────────────────


def test_snapshot(server) -> None:
    status, _, _, data = _req(server, "POST", "/snapshot", body={}, headers=_authz())
    assert status == 200
    assert json.loads(data)["snapshot"]["url"] == "https://a.com"


def test_act_roundtrip(server) -> None:
    status, _, _, data = _req(
        server, "POST", "/act", body={"action": "click", "index": 1}, headers=_authz()
    )
    assert status == 200
    assert json.loads(data)["result"] == "acted"


def test_act_requires_action(server) -> None:
    status, _, _, data = _req(server, "POST", "/act", body={}, headers=_authz())
    assert status == 400
    assert json.loads(data)["error"] == "action required"


def test_act_backend_error_is_500(server) -> None:
    status, _, _, data = _req(server, "POST", "/act", body={"action": "boom"}, headers=_authz())
    assert status == 500
    assert json.loads(data)["error"] == "act exploded"


def test_eval_roundtrip(server) -> None:
    status, _, _, data = _req(server, "POST", "/eval", body={"js": "1+1"}, headers=_authz())
    assert status == 200
    assert json.loads(data)["result"] == {"echo": "1+1"}


def test_eval_requires_js(server) -> None:
    status, _, _, data = _req(server, "POST", "/eval", body={}, headers=_authz())
    assert status == 400
    assert json.loads(data)["error"] == "js required"


def test_ask_roundtrip(server) -> None:
    status, _, _, data = _req(
        server,
        "POST",
        "/ask",
        body={"question": "what is this?", "provider": "kimi"},
        headers=_authz(),
    )
    assert status == 200
    assert json.loads(data)["answer"] == "answer!"


def test_ask_requires_question(server) -> None:
    status, _, _, data = _req(server, "POST", "/ask", body={}, headers=_authz())
    assert status == 400
    assert json.loads(data)["error"] == "question required"


def test_ask_provider_defaults_to_none(server) -> None:
    handler = make_handler(be := _Backend(), token=TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, _, _, _ = _req(
            srv.server_address[1],
            "POST",
            "/ask",
            body={"question": "q"},
            headers=_authz(),
        )
        assert status == 200
        assert ("ask", "q", None) in be.calls
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ── GET extras ───────────────────────────────────────────────────────


def test_screenshot_get(server) -> None:
    status, _, _, data = _req(server, "GET", "/screenshot?url=https://a.com", headers=_authz())
    assert status == 200
    body = json.loads(data)
    assert body["image_b64"] == "aGVsbG8=" and body["mime"] == "image/jpeg"


def test_workflows_list(server) -> None:
    status, _, _, data = _req(server, "GET", "/workflows/list", headers=_authz())
    assert status == 200
    assert json.loads(data)["workflows"] == ["w1"]


def test_schedules_get(server) -> None:
    status, _, _, data = _req(server, "GET", "/schedules", headers=_authz())
    assert status == 200
    assert json.loads(data)["schedules"] == []


def test_network_events_bad_since_defaults_to_zero(server) -> None:
    status, _, _, data = _req(server, "GET", "/network/events?since=nope", headers=_authz())
    assert status == 200
    assert json.loads(data)["rows"] == []


def test_network_events_since_parsed(server) -> None:
    status, _, _, data = _req(server, "GET", "/network/events?since=42", headers=_authz())
    assert status == 200
    assert json.loads(data)["seq"] == 0


def test_network_har_download_headers(server) -> None:
    status, ctype, disp, data = _req(server, "GET", "/network/har", headers=_authz())
    assert status == 200
    assert "application/json" in ctype
    assert 'filename="luckyd-capture.har"' in disp
    assert json.loads(data)["log"]["entries"] == []


def test_unknown_get_404(server) -> None:
    status, _, _, data = _req(server, "GET", "/nope", headers=_authz())
    assert status == 404
    assert json.loads(data)["ok"] is False


def test_unknown_post_404_json(server) -> None:
    status, ctype, _, data = _req(server, "POST", "/nope", body={}, headers=_authz())
    assert status == 404
    assert "application/json" in ctype
    assert "unknown route" in json.loads(data)["error"]


def test_post_without_auth_is_json_401(server) -> None:
    status, ctype, _, data = _req(server, "POST", "/tabs", body={})
    assert status == 401
    assert "application/json" in ctype
    assert json.loads(data)["error"] == "unauthorized"


def test_post_forbidden_host_403(server) -> None:
    status, _, _, data = _req(
        server,
        "POST",
        "/tabs",
        body={},
        headers={**_authz(), "Host": "evil.example.com"},
    )
    assert status == 403
    assert json.loads(data)["error"] == "forbidden host"


def test_terminal_asset_missing_is_404_json(server) -> None:
    status, _, _, data = _req(server, "GET", "/static/terminal/xterm-missing.js", headers=_authz())
    assert status == 404
    assert json.loads(data)["error"] == "asset not found"


def test_body_non_dict_json_is_ignored(server) -> None:
    """A JSON array body is not a dict → treated as empty → 400."""
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request(
        "POST",
        "/navigate",
        body=b"[1,2]",
        headers={"Content-Type": "application/json", **_authz()},
    )
    assert conn.getresponse().status == 400
    conn.close()


# ── workflows / schedules / misc POST ────────────────────────────────


def test_workflow_record(server) -> None:
    status, _, _, data = _req(
        server, "POST", "/workflow/record", body={"name": "w1"}, headers=_authz()
    )
    assert status == 200
    assert json.loads(data)["name"] == "w1"


def test_workflow_record_requires_name(server) -> None:
    status, _, _, _data = _req(server, "POST", "/workflow/record", body={}, headers=_authz())
    assert status == 400


def test_workflow_stop(server) -> None:
    status, _, _, data = _req(server, "POST", "/workflow/stop", body={}, headers=_authz())
    assert status == 200
    assert json.loads(data)["steps"] == 2


def test_workflow_replay(server) -> None:
    status, _, _, data = _req(
        server, "POST", "/workflow/replay", body={"name": "w1"}, headers=_authz()
    )
    assert status == 200
    assert json.loads(data)["replayed"] == "w1"


def test_workflow_replay_requires_name(server) -> None:
    status, _, _, _data = _req(server, "POST", "/workflow/replay", body={}, headers=_authz())
    assert status == 400


def test_workflow_delete(server) -> None:
    status, _, _, data = _req(
        server, "POST", "/workflow/delete", body={"name": "w1"}, headers=_authz()
    )
    assert status == 200
    assert json.loads(data)["deleted"] is True


def test_schedule_set(server) -> None:
    status, _, _, data = _req(
        server,
        "POST",
        "/schedule",
        body={"name": "morning", "every_min": 60},
        headers=_authz(),
    )
    assert status == 200
    body = json.loads(data)
    assert body["name"] == "morning" and body["every_min"] == 60


def test_schedule_requires_name(server) -> None:
    status, _, _, _data = _req(server, "POST", "/schedule", body={}, headers=_authz())
    assert status == 400


def test_schedule_bad_every_min_becomes_zero(server) -> None:
    handler = make_handler(be := _Backend(), token=TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, _, _, _ = _req(
            srv.server_address[1],
            "POST",
            "/schedule",
            body={"name": "n", "every_min": "abc"},
            headers=_authz(),
        )
        assert status == 200
        assert ("schedule_set", "n", 0) in be.calls
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_extract_roundtrip(server) -> None:
    status, _, _, data = _req(
        server,
        "POST",
        "/extract",
        body={"instruction": "get title", "schema": {"a": 1}},
        headers=_authz(),
    )
    assert status == 200
    assert json.loads(data)["data"] == {"field": "value"}


def test_extract_requires_instruction(server) -> None:
    status, _, _, _data = _req(server, "POST", "/extract", body={}, headers=_authz())
    assert status == 400


def test_extract_non_dict_schema_becomes_none(server) -> None:
    handler = make_handler(be := _Backend(), token=TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, _, _, _ = _req(
            srv.server_address[1],
            "POST",
            "/extract",
            body={"instruction": "x", "schema": ["not", "a", "dict"]},
            headers=_authz(),
        )
        assert status == 200
        assert ("extract", "x", None, None) in be.calls
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_theme_roundtrip(server) -> None:
    status, _, _, data = _req(server, "POST", "/theme", body={"name": "neon"}, headers=_authz())
    assert status == 200
    assert json.loads(data)["theme"] == "neon"


def test_theme_requires_name(server) -> None:
    status, _, _, _data = _req(server, "POST", "/theme", body={}, headers=_authz())
    assert status == 400


def test_network_start_stop_clear(server) -> None:
    s1, _, _, d1 = _req(server, "POST", "/network/start", body={"url": "ex"}, headers=_authz())
    assert s1 == 200 and json.loads(d1)["started"] is True
    s2, _, _, d2 = _req(server, "POST", "/network/stop", body={}, headers=_authz())
    assert s2 == 200 and json.loads(d2)["stopped"] is True
    s3, _, _, d3 = _req(server, "POST", "/network/clear", body={}, headers=_authz())
    assert s3 == 200 and json.loads(d3)["cleared"] is True


# ── research swarm routes (swarm_manager mocked) ─────────────────────


class _Swarm:
    def __init__(self):
        self.start_calls = []

    def get_status(self, run_id):
        return {"run_id": run_id, "state": "running"}

    def list_runs(self):
        return [{"run_id": "r1"}]

    def get_run(self, run_id):
        return {"run_id": run_id, "report": "# hi"}

    def start_research(self, **kw):
        self.start_calls.append(kw)
        if kw["query"] == "explode":
            raise ValueError("bad query")
        return "rid-9"

    def cancel_run(self):
        return True


@pytest.fixture()
def swarm(monkeypatch):
    fake = _Swarm()
    monkeypatch.setattr(control_server, "swarm_manager", fake)
    return fake


def test_research_status(server, swarm) -> None:
    status, _, _, data = _req(server, "GET", "/research/status?run_id=r1", headers=_authz())
    assert status == 200
    body = json.loads(data)
    assert body["run_id"] == "r1" and body["state"] == "running"


def test_research_runs(server, swarm) -> None:
    status, _, _, data = _req(server, "GET", "/research/runs", headers=_authz())
    assert status == 200
    assert json.loads(data)["runs"] == [{"run_id": "r1"}]


def test_research_run(server, swarm) -> None:
    status, _, _, data = _req(server, "GET", "/research/run?id=r1", headers=_authz())
    assert status == 200
    assert json.loads(data)["report"] == "# hi"


def test_research_start(server, swarm) -> None:
    status, _, _, data = _req(
        server,
        "POST",
        "/research/start",
        body={"query": "cheap laptops", "depth": "deep"},
        headers=_authz(),
    )
    assert status == 200
    assert json.loads(data)["run_id"] == "rid-9"
    assert swarm.start_calls[0]["query"] == "cheap laptops"
    assert swarm.start_calls[0]["depth"] == "deep"
    assert swarm.start_calls[0]["dry_run"] is False


def test_research_start_requires_query(server, swarm) -> None:
    status, _, _, _data = _req(server, "POST", "/research/start", body={}, headers=_authz())
    assert status == 400


def test_research_start_error_is_400(server, swarm) -> None:
    status, _, _, data = _req(
        server, "POST", "/research/start", body={"query": "explode"}, headers=_authz()
    )
    assert status == 400
    assert "bad query" in json.loads(data)["error"]


def test_research_cancel(server, swarm) -> None:
    status, _, _, data = _req(server, "POST", "/research/cancel", body={}, headers=_authz())
    assert status == 200
    assert json.loads(data)["cancelled"] is True


# ── /status enrichment + /hq gateway ─────────────────────────────────


class _Harness:
    def __init__(self, up=True, starting=False, error=None):
        self._up = up
        self._starting = starting
        self._error = error
        self.ensure_calls = []
        self.url = "http://127.0.0.1:8000"

    def probe(self):
        return {"up": self._up}

    def status(self):
        return {
            "up": self._up,
            "starting": self._starting,
            "error": self._error,
            "tools": 98 if self._up else None,
            "url": self.url,
        }

    def ensure_started(self, force=False):
        self.ensure_calls.append(force)
        self._starting = True
        self._error = None


def _srv_with(backend=None, harness=None):
    handler = make_handler(backend or _Backend(), token=TOKEN, harness=harness)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t, port


def test_status_enriched_with_harness_and_ai(monkeypatch) -> None:
    monkeypatch.setattr(tile_registry, "ensure_autostart", lambda: None)
    srv, t, port = _srv_with(harness=_Harness())
    try:
        status, _, _, data = _req(srv and port, "GET", "/status", headers=_authz())
        body = json.loads(data)
        assert status == 200
        assert body["ok"] is True
        assert body["harness"] is True
        assert body["harness_tools"] == 98
        assert body["harness_url"] == "http://127.0.0.1:8000"
        assert body["provider"] == "kimi"  # ai_info merged
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_status_harness_error_surfaced(monkeypatch) -> None:
    monkeypatch.setattr(tile_registry, "ensure_autostart", lambda: None)
    srv, t, port = _srv_with(harness=_Harness(up=False, error="port busy"))
    try:
        _, _, _, data = _req(port, "GET", "/status", headers=_authz())
        body = json.loads(data)
        assert body["harness"] is False
        assert body["harness_error"] == "port busy"
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_status_harness_exception_suppressed(monkeypatch) -> None:
    monkeypatch.setattr(tile_registry, "ensure_autostart", lambda: None)

    class _BadHarness:
        def status(self):
            raise RuntimeError("boom")

    srv, t, port = _srv_with(harness=_BadHarness())
    try:
        status, _, _, data = _req(port, "GET", "/status", headers=_authz())
        assert status == 200
        assert json.loads(data)["ok"] is True  # backend status still served
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_hq_no_harness_gives_error_page() -> None:
    srv, t, port = _srv_with()
    try:
        status, ctype, _, data = _req(port, "GET", "/hq", headers=_authz())
        assert status == 200
        assert "text/html" in ctype
        assert b"No harness supervisor" in data
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_hq_up_serves_workspace_shell() -> None:
    srv, t, port = _srv_with(harness=_Harness(up=True))
    try:
        status, ctype, _, data = _req(port, "GET", "/hq", headers=_authz())
        assert status == 200
        assert "text/html" in ctype
        assert b"http://127.0.0.1:8000" in data
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_hq_starting_shows_splash() -> None:
    srv, t, port = _srv_with(harness=_Harness(up=False, starting=True))
    try:
        status, ctype, _, _data = _req(port, "GET", "/hq", headers=_authz())
        assert status == 200
        assert "text/html" in ctype
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_hq_retry_forces_restart() -> None:
    h = _Harness(up=False, error="died")
    srv, t, port = _srv_with(harness=h)
    try:
        status, _, _, _ = _req(port, "GET", "/hq?retry=1", headers=_authz())
        assert status == 200
        assert h.ensure_calls == [True]  # force=True from the retry link
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_hq_auto_starts_when_idle() -> None:
    h = _Harness(up=False)
    srv, t, port = _srv_with(harness=h)
    try:
        _req(port, "GET", "/hq", headers=_authz())
        assert h.ensure_calls == [False]
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ── BrowserControlServer lifecycle ───────────────────────────────────


def test_control_server_start_stop_roundtrip() -> None:
    srv = BrowserControlServer(_Backend(), port=0, token=TOKEN)
    assert not srv.running
    srv.start()
    assert srv.running
    assert srv.base_url.startswith("http://127.0.0.1:")
    status, _, _, data = _req(srv.port, "GET", "/tabs", headers=_authz())
    assert status == 200
    assert json.loads(data)["tabs"][0]["url"] == "https://a.com"
    srv.start()  # idempotent
    assert srv.running
    srv.stop()
    assert not srv.running
    srv.stop()  # idempotent


# ── _export_env_to_os ────────────────────────────────────────────────


def test_export_env_to_os_pushes_key_vars(monkeypatch) -> None:
    import browser_core.ai_bridge as ai_bridge

    monkeypatch.setattr(
        ai_bridge,
        "_load_env",
        lambda: {
            # NOTE: single-char fake value — no real secret here; keeps
            # gitleaks' generic-api-key rule quiet.
            "LUCKYD_N4_TEST_KEY": "v",
            "LUCKYD_N4_PLAIN": "nope",
            "LUCKYD_N4_MODEL": "m",
        },
    )
    for k in ("LUCKYD_N4_TEST_KEY", "LUCKYD_N4_PLAIN", "LUCKYD_N4_MODEL"):
        monkeypatch.delenv(k, raising=False)
    control_server._export_env_to_os()
    import os

    assert os.environ["LUCKYD_N4_TEST_KEY"] == "v"
    assert os.environ["LUCKYD_N4_MODEL"] == "m"
    assert "LUCKYD_N4_PLAIN" not in os.environ


def test_export_env_to_os_never_overrides(monkeypatch) -> None:
    import browser_core.ai_bridge as ai_bridge

    monkeypatch.setattr(ai_bridge, "_load_env", lambda: {"LUCKYD_N4_TEST_KEY": "new-value"})
    monkeypatch.setenv("LUCKYD_N4_TEST_KEY", "existing")
    control_server._export_env_to_os()
    import os

    assert os.environ["LUCKYD_N4_TEST_KEY"] == "existing"


def test_export_env_to_os_survives_loader_failure(monkeypatch) -> None:
    import browser_core.ai_bridge as ai_bridge

    def _boom():
        raise RuntimeError("no .env")

    monkeypatch.setattr(ai_bridge, "_load_env", _boom)
    control_server._export_env_to_os()  # must not raise
