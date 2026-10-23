"""Regression: AI Assistant AGENT mode dead on Cline while Ask worked.

Field evidence (Dylan's PC, installed 10.8.1, 2026-10-22 ~21:17 EDT):
- Browser settings (%LOCALAPPDATA%/LuckyDBrowser/settings.json):
  ai_provider=cline-usage with model override xiaomi/mimo-v2.6-pro -> Ask
  mode works (mimo streams fine, verified with a live probe).
- Harness /api/settings showed provider=cline-usage but model=
  deepseek/deepseek-chat (the provider DEFAULT — the user's pick never
  arrived), and Dylan's background task ended status=done with result
  "[ERR] The Cline (usage) model (deepseek/deepseek-chat) returned an
  empty reply…".
- Live gateway probe (same session token, same model): POST
  api.cline.bot/api/v1/chat/completions returns the failure as an SSE
  error EVENT on HTTP 200 — the gateway maps deepseek/deepseek-chat to
  deepseek/deepseek-v3, which upstream 404s (model_not_found).

Two defects pinned here:
1. core.providers._assistant_browser_settings() looked for settings.json
   under <exe dir>/browser/data/ (source-tree layout) — a path the
   installed browser never writes (it writes
   %LOCALAPPDATA%/LuckyDBrowser/settings.json) — so the harness mirror
   silently saw {} and agent mode fell back to detection/defaults
   instead of the user's working provider+model.
2. In-stream error events were swallowed by every client that met them:
   core.llm_client._read_stream ignored {"error": …} chunks and
   returned an empty "successful" message (so the agent loop reported
   an empty reply instead of auto-rotating models), chat_nonstreaming
   did the same for error bodies, and browser_core.ai_bridge._call
   returned "" so chat()'s per-model rotation never fired.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from core import providers
from core.agent_loop import CodingAgent
from core.llm_client import LLMClient
from core.providers import _assistant_browser_settings as _real_browser_settings

# NOTE: tests/conftest.py stubs providers._assistant_browser_settings to
# {} for isolation; the module-level import above (same pattern as
# test_cov_providers.py) keeps a reference to the REAL reader so the
# mirror's file lookup itself is what gets exercised here.

# ── the gateway payloads, exactly as captured live ─────────────────────

_GATEWAY_STREAM_ERROR = {
    "error": {
        "code": "stream_initialization_failed",
        "message": (
            "Failed to create stream: inference request failed: failed to "
            "generate stream from Vercel: failed to invoke model "
            "'deepseek/deepseek-chat' with streaming: request failed with "
            'status 404: {"error":{"message":"Model \'deepseek/deepseek-v3\' '
            'not found","type":"model_not_found","param":'
            '{"modelId":"deepseek/deepseek-v3"}}}'
        ),
        "request_id": "req-test",
        "type": "stream_error",
    }
}

_GATEWAY_NONSTREAM_ERROR = {
    "error": (
        "inference request failed: failed to invoke model "
        "'deepseek/deepseek-chat' from Vercel: request failed with status "
        '404: {"error":{"message":"Model \'deepseek/deepseek-v3\' not '
        'found","type":"model_not_found"}}'
    ),
    "success": False,
}


# ── core.llm_client fakes (house pattern from test_night5_llm_client) ──


class _FakeStreamResp:
    def __init__(self, lines, status=200):
        self.status_code = status
        self.headers = {}
        self._lines = lines
        self.request = httpx.Request("POST", "https://api.cline.bot/api/v1/chat/completions")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self
            )


class _FakePostResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.request = httpx.Request("POST", "https://api.cline.bot/api/v1/chat/completions")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self
            )

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)

    @property
    def headers(self):
        return {}


class _FakeAsyncClient:
    instances: list = []
    script: list = []

    def __init__(self, *a, **k):
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, kind):
        for i, (k, _item) in enumerate(_FakeAsyncClient.script):
            if k == kind:
                return _FakeAsyncClient.script.pop(i)
        raise AssertionError(f"no scripted {kind} left")

    async def post(self, url, headers=None, json=None):
        _kind, item = self._next("post")
        return item

    def stream(self, method, url, headers=None, json=None):
        _kind, item = self._next("stream")
        return item


@pytest.fixture(autouse=True)
def _reset_llm_fake():
    _FakeAsyncClient.instances.clear()
    _FakeAsyncClient.script.clear()
    yield
    _FakeAsyncClient.instances.clear()
    _FakeAsyncClient.script.clear()


@pytest.fixture()
def cline_client():
    return LLMClient(
        api_key="session-token",
        base_url="https://api.cline.bot/api/v1",
        model="deepseek/deepseek-chat",
        max_retries=0,
        provider="cline-usage",
    )


def _sse_lines(*payloads) -> list[str]:
    lines = ["data: " + json.dumps(p) for p in payloads]
    lines.append("data: [DONE]")
    return lines


# ── 1) llm_client: in-stream error events ──────────────────────────────


async def test_stream_error_event_becomes_api_error(cline_client):
    """The gateway's HTTP-200 error event must surface as an [API Error]
    the agent loop can rotate on — not as an empty successful reply."""
    _FakeAsyncClient.script.append(("stream", _FakeStreamResp(_sse_lines(_GATEWAY_STREAM_ERROR))))
    with patch("core.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        msg = await cline_client.chat_stream([{"role": "user", "content": "go to youtube"}])
    assert msg is not None
    content = msg.get("content", "")
    assert content.startswith("[API Error: 404]"), content
    assert "not found" in content
    assert not msg.get("tool_calls")
    # The interlock the agent loop relies on: the parsed code is a
    # rotation-worthy one.
    assert CodingAgent._api_error_code(content) == 404
    assert 404 in CodingAgent._ROTATE_CODES


async def test_stream_error_after_partial_content_keeps_content(cline_client):
    """A trailing error event must not throw away a usable reply."""
    chunk = {"choices": [{"delta": {"content": "partial answer"}}]}
    _FakeAsyncClient.script.append(
        ("stream", _FakeStreamResp(_sse_lines(chunk, _GATEWAY_STREAM_ERROR)))
    )
    with patch("core.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        msg = await cline_client.chat_stream([{"role": "user", "content": "hi"}])
    assert msg["content"] == "partial answer"


async def test_nonstreaming_error_body_becomes_api_error(cline_client):
    """The non-streaming fallback path: an error body on HTTP 200 is an
    [API Error], not an empty message."""
    _FakeAsyncClient.script.append(("post", _FakePostResp(_GATEWAY_NONSTREAM_ERROR)))
    with patch("core.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        msg = await cline_client.chat_nonstreaming([{"role": "user", "content": "go to youtube"}])
    assert msg is not None
    content = msg.get("content", "")
    assert content.startswith("[API Error: 404]"), content
    assert "not found" in content


async def test_nonstreaming_dict_error_body_becomes_api_error(cline_client):
    payload = {"error": {"message": "Model 'x' not found", "code": "model_not_found"}}
    _FakeAsyncClient.script.append(("post", _FakePostResp(payload)))
    with patch("core.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        msg = await cline_client.chat_nonstreaming([{"role": "user", "content": "hi"}])
    assert msg is not None
    assert msg.get("content", "").startswith("[API Error:")
    assert "not found" in msg["content"]


# ── 2) providers: the settings mirror must find the installed file ─────


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    """Scrub provider env (mirrors test_cov_providers' fixture)."""
    for var in (
        "CODING_AGENT_PROVIDER",
        "CODING_AGENT_MODEL",
        "CLINEPASS_API_KEY",
        "CLINE_USAGE_MODEL",
        "CLINEPASS_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "CODING_AGENT_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLINE_DATA_DIR", str(tmp_path / "cline-data"))
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")
    monkeypatch.setattr(httpx, "get", _refusing_get)
    return monkeypatch


def _refusing_get(url, **kwargs):
    raise ConnectionError("refused")


def _freeze_at(monkeypatch, exe_dir: Path):
    exe_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "luckyd-code.exe"), raising=False)


def _write_installed_settings(localappdata: Path, payload: dict) -> Path:
    path = localappdata / "LuckyDBrowser" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_frozen_reads_localappdata_settings(clean_env, monkeypatch, tmp_path):
    """Installed layout: the browser writes %LOCALAPPDATA%/LuckyDBrowser/
    settings.json — the mirror must read THAT file, not <exe>/browser/data."""
    _freeze_at(monkeypatch, tmp_path / "Programs" / "LuckyDBrowser" / "_internal")
    localappdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    _write_installed_settings(
        localappdata,
        {
            "ai_provider": "cline-usage",
            "ai_model_overrides": {"cline-usage": "xiaomi/mimo-v2.6-pro"},
        },
    )
    got = _real_browser_settings()
    assert got.get("ai_provider") == "cline-usage"
    assert got["ai_model_overrides"]["cline-usage"] == "xiaomi/mimo-v2.6-pro"


def test_frozen_localappdata_wins_over_bundled_copy(clean_env, monkeypatch, tmp_path):
    """When both exist, the live user settings beat a bundled stale copy."""
    exe_dir = tmp_path / "Programs" / "LuckyDBrowser" / "_internal"
    _freeze_at(monkeypatch, exe_dir)
    bundled = exe_dir / "browser" / "data"
    bundled.mkdir(parents=True, exist_ok=True)
    (bundled / "settings.json").write_text(json.dumps({"ai_provider": "ollama"}), encoding="utf-8")
    localappdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    _write_installed_settings(localappdata, {"ai_provider": "cline-usage"})
    assert _real_browser_settings().get("ai_provider") == "cline-usage"


def test_resolve_mirror_applies_users_model_pick(clean_env, monkeypatch, tmp_path):
    """End to end: with the installed settings present, resolve mirrors
    BOTH the provider and Dylan's model override (the pick that makes Ask
    work) instead of the provider default deepseek/deepseek-chat."""
    _freeze_at(monkeypatch, tmp_path / "Programs" / "LuckyDBrowser" / "_internal")
    localappdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    _write_installed_settings(
        localappdata,
        {
            "ai_provider": "cline-usage",
            "ai_model_overrides": {"cline-usage": "xiaomi/mimo-v2.6-pro"},
        },
    )
    monkeypatch.setattr(providers, "cline_session_token", lambda: "session-token")
    # resolve_provider_config consults the module-global reader, which the
    # suite conftest stubbed to {} — restore the real one for this test.
    monkeypatch.setattr(providers, "_assistant_browser_settings", _real_browser_settings)
    # …and it fetches the session token via a local `import cline_session`;
    # give it a logged-in session (Dylan's machine has one).
    import types as _types

    fake_session = _types.SimpleNamespace(fresh_token=lambda: "session-token")
    monkeypatch.setitem(sys.modules, "cline_session", fake_session)
    cfg = providers.resolve_provider_config()
    assert cfg["provider"] == "cline-usage"
    assert cfg["model"] == "xiaomi/mimo-v2.6-pro"
    assert cfg["base_url"] == "https://api.cline.bot/api/v1"


# ── 3) ai_bridge: the Ask-path client must raise, so rotation fires ────

from browser_core import ai_bridge, cline_session
from browser_core.ai_bridge import AIBridge


class _BridgeStreamResp:
    def __init__(self, chunks):
        self.status_code = 200
        self._chunks = list(chunks)

    async def aread(self):
        return b""

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _BridgeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _BridgeAsyncClient:
    responses: list = []

    def __init__(self, timeout=None, trust_env=True):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None, headers=None):
        resp = _BridgeAsyncClient.responses.pop(0)
        return _BridgeStreamCtx(resp)


def _sse_bytes(payload) -> bytes:
    return ("data: " + json.dumps(payload) + "\n").encode()


@pytest.fixture()
def bridge_env(monkeypatch):
    state = {"env": {"CLINEPASS_API_KEY": "k", "CLINE_USAGE_MODEL": "deepseek/deepseek-chat"}}
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: dict(state["env"]))
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: {}))
    monkeypatch.setattr(cline_session, "has_session", lambda: False)
    monkeypatch.setattr(ai_bridge, "_route_task", None)
    _BridgeAsyncClient.responses = []
    monkeypatch.setattr(ai_bridge.httpx, "AsyncClient", _BridgeAsyncClient)
    return state


def test_bridge_call_stream_error_event_raises(bridge_env):
    bridge = AIBridge()
    info = bridge._configs["cline-usage"]
    _BridgeAsyncClient.responses.append(
        _BridgeStreamResp([_sse_bytes(_GATEWAY_STREAM_ERROR), b"data: [DONE]\n"])
    )

    async def _run():
        return await bridge._call("cline-usage", info, [{"role": "user", "content": "hi"}], None)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_run())
    assert "404" in str(excinfo.value)


def test_bridge_chat_rotates_past_dead_cline_model(bridge_env):
    """Explicit Cline pick, first model dead on the gateway: chat() must
    rotate to the next gateway model instead of returning an empty reply."""
    bridge = AIBridge()
    assert bridge._configs["cline-usage"][0] == "deepseek/deepseek-chat"
    _BridgeAsyncClient.responses.append(
        _BridgeStreamResp([_sse_bytes(_GATEWAY_STREAM_ERROR), b"data: [DONE]\n"])
    )
    ok_chunk = {"choices": [{"delta": {"content": "rotated answer"}}]}
    _BridgeAsyncClient.responses.append(_BridgeStreamResp([_sse_bytes(ok_chunk)]))

    async def _run():
        return await bridge.chat(
            [{"role": "user", "content": "go to youtube"}], provider="cline-usage"
        )

    text, used = asyncio.run(_run())
    assert text == "rotated answer"
    assert used == "cline-usage"
    assert bridge._configs["cline-usage"][0] == "minimax/minimax-m2.5"


def test_bridge_call_partial_content_survives_error_event(bridge_env):
    bridge = AIBridge()
    info = bridge._configs["cline-usage"]
    ok_chunk = {"choices": [{"delta": {"content": "kept"}}]}
    _BridgeAsyncClient.responses.append(
        _BridgeStreamResp(
            [_sse_bytes(ok_chunk), _sse_bytes(_GATEWAY_STREAM_ERROR), b"data: [DONE]\n"]
        )
    )

    async def _run():
        return await bridge._call("cline-usage", info, [{"role": "user", "content": "hi"}], None)

    assert asyncio.run(_run()) == "kept"
