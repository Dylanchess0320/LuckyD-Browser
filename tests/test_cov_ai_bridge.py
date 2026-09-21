"""Coverage push for browser/browser_core/ai_bridge.py.

Fills the gaps left by the earlier suites: _load_env file parsing, the
ClinePass session-registration paths, fetch_models() live/reorder/fallback
branches, the chat() fast-path + generic-fallback rotation (including the
Zen per-model rotation in auto mode), the streaming _call() (SSE parsing,
per-kind URLs/headers, timeouts, HTTP errors), and the body/delta helpers.

All providers are hand-written fakes with real logic; HTTP is stubbed at
the httpx seam the module uses. No network, no MagicMock subclasses.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import ai_bridge, cline_session
from browser_core.ai_bridge import AIBridge


@pytest.fixture()
def hermetic(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Hermetic bridge factory state: fixed env, no localhost probes, no
    Cline session, router disabled (static fallback chain)."""
    state: dict = {"env": {}, "local": {}}
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: dict(state["env"]))
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: dict(state["local"])))
    monkeypatch.setattr(cline_session, "has_session", lambda: False)
    monkeypatch.setattr(ai_bridge, "_route_task", None)
    return state


def _bridge(state: dict) -> AIBridge:
    return AIBridge()


# ── _load_env ────────────────────────────────────────────────────────


def test_load_env_reads_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "FOO=bar\n"
        'QUOTED="hello world"\n'
        "SINGLE='xyz'\n"
        "EMPTY=\n"
        "SPACED = spaced value \n"
        "A=B=C\n"
        "no-equals-line\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_bridge, "ENV_PATH", env_file)
    env = ai_bridge._load_env()
    assert env["FOO"] == "bar"
    assert env["QUOTED"] == "hello world"
    assert env["SINGLE"] == "xyz"
    assert env["EMPTY"] == ""
    assert env["SPACED"] == "spaced value"
    assert env["A"] == "B=C"
    assert "no-equals-line" not in env


def test_load_env_survives_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ENV_PATH pointing at a directory: read_text raises → caught, process
    env still applied."""
    monkeypatch.setattr(ai_bridge, "ENV_PATH", tmp_path)
    env = ai_bridge._load_env()
    # conftest's autouse fixture seeds DEEPSEEK_API_KEY in os.environ —
    # its presence proves the os.environ merge still ran after the file
    # read blew up. (Value not compared: the real shell env may carry a
    # live secret under the same name.)
    assert "DEEPSEEK_API_KEY" in env


# ── _detect_clinepass ────────────────────────────────────────────────


def test_clinepass_token_from_session(hermetic, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cline_session, "has_session", lambda: True)
    monkeypatch.setattr(cline_session, "stored_token", lambda: "sess-token")
    bridge = _bridge(hermetic)
    assert bridge._clinepass_from_session is True
    assert bridge._configs["clinepass"][2] == "sess-token"
    assert bridge._configs["cline-usage"][2] == "sess-token"
    assert bridge._cline_usable() is True
    assert bridge.default_provider() == "cline-usage"


def test_clinepass_expired_session_registers_empty_token(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> str:
        raise RuntimeError("no valid Cline session token stored")

    monkeypatch.setattr(cline_session, "has_session", lambda: True)
    monkeypatch.setattr(cline_session, "stored_token", _boom)
    bridge = _bridge(hermetic)
    assert bridge._clinepass_from_session is True
    assert bridge._configs["clinepass"][2] == ""
    # Session exists (even with expired tokens) → still usable: tokens are
    # refreshed lazily before each call.
    assert bridge._cline_usable() is True
    assert bridge.default_provider() == "cline-usage"


def test_clinepass_env_key_skips_session_lookup(hermetic, monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_run() -> bool:
        raise AssertionError("session must not be consulted when CLINEPASS_API_KEY is set")

    hermetic["env"] = {"CLINEPASS_API_KEY": "env-key"}
    monkeypatch.setattr(cline_session, "has_session", _must_not_run)
    bridge = _bridge(hermetic)
    assert bridge._clinepass_from_session is False
    assert bridge._configs["clinepass"][2] == "env-key"
    assert bridge._cline_usable() is True


def test_default_provider_cline_usage_when_keyed(hermetic) -> None:
    hermetic["env"] = {"CLINEPASS_API_KEY": "k"}
    assert _bridge(hermetic).default_provider() == "cline-usage"


# ── free_top_models ──────────────────────────────────────────────────


def test_free_top_models_returns_a_copy(hermetic) -> None:
    bridge = _bridge(hermetic)
    models = bridge.free_top_models()
    assert models == list(ai_bridge._CLINE_GATEWAY_TOP_MODELS)
    models.append("junk")
    assert bridge.free_top_models() == list(ai_bridge._CLINE_GATEWAY_TOP_MODELS)


# ── fetch_models ─────────────────────────────────────────────────────


class _FakeGetResp:
    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_models_cline_usage_live_reorders_top_first(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live Cline Usage catalog: top models first (in _CLINE_GATEWAY_TOP_MODELS
    order), extras after, embed models filtered, current model pinned at the head."""
    hermetic["env"] = {"CLINEPASS_API_KEY": "zk-test"}
    seen: dict = {}

    def _get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return _FakeGetResp(
            {
                "data": [
                    {"id": "custom-x"},
                    {"id": "minimax/minimax-m2.5"},
                    {"id": "deepseek/deepseek-chat"},
                    {"id": "nomic-embed-text"},
                    {"id": "kwaipilot/kat-coder-pro"},
                ]
            }
        )

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    bridge = _bridge(hermetic)
    models = bridge.fetch_models("cline-usage")
    assert seen["headers"] == {"Authorization": "Bearer zk-test"}
    assert models == [
        "deepseek/deepseek-chat",  # current model pinned first (also TOP[0])
        "minimax/minimax-m2.5",  # _CLINE_GATEWAY_TOP_MODELS order…
        "kwaipilot/kat-coder-pro",
        "custom-x",  # …then the rest of the live catalog
    ]


def test_fetch_models_cline_usage_live_failure_uses_gateway_catalog(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermetic["env"] = {"CLINEPASS_API_KEY": "zk-test"}

    def _get(url, headers=None, timeout=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    bridge = _bridge(hermetic)
    models = bridge.fetch_models("cline-usage")
    top_order = {m: i for i, m in enumerate(ai_bridge._CLINE_GATEWAY_TOP_MODELS)}
    expected = sorted(ai_bridge._CLINE_GATEWAY_CATALOG, key=lambda m: top_order.get(m, 999))
    assert models == expected
    assert set(models) == set(ai_bridge._CLINE_GATEWAY_CATALOG)


def test_fetch_models_generic_fallback_is_current_model(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermetic["env"] = {"DEEPSEEK_API_KEY": "k"}

    def _get(url, headers=None, timeout=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    bridge = _bridge(hermetic)
    assert bridge.fetch_models("deepseek") == ["deepseek-v4-flash"]


def test_fetch_models_clinepass_model_override_pinned_first(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLINEPASS_MODEL override in the gateway catalog but not in TOP is
    kept visible after the top-sorted gateway catalog (top-sort runs after
    pinning)."""
    hermetic["env"] = {"CLINEPASS_MODEL": "cline-pass/kimi-k2.6"}

    def _get(url, headers=None, timeout=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    bridge = _bridge(hermetic)
    models = bridge.fetch_models("clinepass")
    assert "cline-pass/kimi-k2.6" in models
    assert models[:6] == list(ai_bridge._CLINE_GATEWAY_TOP_MODELS[:6])
    assert "cline-pass/kimi-k3" in models  # curated catalog still present


def test_fetch_models_cline_session_uses_fresh_token(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cline_session, "has_session", lambda: True)
    monkeypatch.setattr(cline_session, "stored_token", lambda: "sess")
    monkeypatch.setattr(cline_session, "fresh_token", lambda: "fresh-123")
    seen: dict = {}

    def _get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return _FakeGetResp({"data": [{"id": "cline-pass/kimi-k3"}]})

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    bridge = _bridge(hermetic)
    models = bridge.fetch_models("clinepass")
    assert models[0] == "cline-pass/kimi-k3"
    assert "cline-pass/deepseek-v4-pro" in models
    assert len(models) >= 2
    assert seen["headers"] == {"Authorization": "Bearer fresh-123"}


def test_fetch_models_cline_session_refresh_failure_sends_no_auth(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cline_session, "has_session", lambda: True)
    monkeypatch.setattr(cline_session, "stored_token", lambda: "sess")

    def _boom() -> str:
        raise RuntimeError("session expired")

    monkeypatch.setattr(cline_session, "fresh_token", _boom)
    seen: dict = {}

    def _get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return _FakeGetResp({"data": [{"id": "cline-pass/kimi-k3"}]})

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    bridge = _bridge(hermetic)
    models = bridge.fetch_models("clinepass")
    assert models[0] == "cline-pass/kimi-k3"
    assert "cline-pass/deepseek-v4-pro" in models
    assert len(models) >= 2
    assert seen["headers"] == {}


# ── routing helpers ──────────────────────────────────────────────────


def test_routing_text_skips_non_user_entries(hermetic) -> None:
    # reversed(): the non-user entries are evaluated first and skipped.
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        "not-a-dict",
        {"role": "user", "content": 123},  # neither str nor list → skipped
    ]
    assert AIBridge._routing_text(msgs) == "first"


def test_routing_text_non_iterable_returns_empty(hermetic) -> None:
    assert AIBridge._routing_text(5) == ""


def test_routing_context_size_mixed_content(hermetic) -> None:
    msgs = [
        {"role": "user", "content": "abcd"},  # 4 chars
        "junk-entry",  # skipped
        {"role": "user", "content": 123},  # neither str nor list → skipped
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "abcdefgh"},  # 8 chars
                {"type": "text", "text": 123},  # non-str text ignored
                "not-a-dict",  # skipped
                {"type": "image_url"},  # no text → contributes 0
            ],
        },
    ]
    assert AIBridge._routing_context_size(msgs) == (4 + 8) // 4


def test_routing_context_size_non_iterable_returns_zero(hermetic) -> None:
    assert AIBridge._routing_context_size(9) == 0


# ── chat(): fast path & generic fallback ─────────────────────────────


def test_chat_auto_sentinel_means_auto(hermetic, monkeypatch: pytest.MonkeyPatch) -> None:
    hermetic["local"] = {"ollama": ("qwen3:4b", "http://127.0.0.1:11434/v1", "", "openai")}
    calls = []

    async def _call(name, info, messages, on_token):
        calls.append(name)
        return "ok"

    bridge = _bridge(hermetic)
    monkeypatch.setattr(bridge, "_call", _call)
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}], provider="AUTO"))
    assert (text, name) == ("ok", "ollama")
    assert calls == ["ollama"]


def test_chat_fast_path_cline_gateway_rotates_models(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local-spec entry whose endpoint is the Cline gateway exercises the
    fast-path per-model rotation: 404s rotate to the next top model, the
    cursor advances, and the winning model is stored in the config."""
    monkeypatch.setattr(
        ai_bridge,
        "_LOCAL_SPECS",
        [("clinelocal", "CLINELOCAL_HOST", "https://api.cline.bot/api/v1", "")],
    )
    hermetic["local"] = {
        "clinelocal": ("deepseek/deepseek-chat", "https://api.cline.bot/api/v1", "", "openai")
    }
    tried: list[str] = []

    async def _call(name, info, messages, on_token):
        tried.append(info[0])
        if info[0] in ("deepseek/deepseek-chat", "minimax/minimax-m2.5"):
            raise RuntimeError("404 model not found")
        return "cline-fast-ok"

    bridge = _bridge(hermetic)
    assert bridge.is_cline_gateway("clinelocal")
    monkeypatch.setattr(bridge, "_call", _call)
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))
    assert (text, name) == ("cline-fast-ok", "clinelocal")
    assert tried == list(ai_bridge._CLINE_GATEWAY_TOP_MODELS[:3])
    assert bridge._configs["clinelocal"][0] == "qwen/qwen3-8b"
    assert bridge._free_cursor == 3


def test_chat_fast_path_cline_all_fail_then_cloud(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fast path burns all Cline top models; the generic fallback then skips
    the already-tried Cline gateway and reaches the keyed cloud."""
    monkeypatch.setattr(
        ai_bridge,
        "_LOCAL_SPECS",
        [("clinelocal", "CLINELOCAL_HOST", "https://api.cline.bot/api/v1", "")],
    )
    hermetic["env"] = {"OPENAI_API_KEY": "k"}
    hermetic["local"] = {
        "clinelocal": ("deepseek/deepseek-chat", "https://api.cline.bot/api/v1", "", "openai")
    }
    calls: list[str] = []

    async def _call(name, info, messages, on_token):
        calls.append(name)
        if name == "clinelocal":
            raise RuntimeError("429 rate limited")
        return "cloud-ok"

    bridge = _bridge(hermetic)
    monkeypatch.setattr(bridge, "_call", _call)
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))
    assert (text, name) == ("cloud-ok", "openai")
    # All 10 top models tried in the fast path, then the Cline gateway skipped
    # once in the generic loop — openai tried exactly once.
    assert calls.count("clinelocal") == len(ai_bridge._CLINE_GATEWAY_TOP_MODELS)
    assert calls.count("openai") == 1
    assert calls[-1] == "openai"


def test_chat_fast_path_local_failure_skipped_in_fallback(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermetic["env"] = {"OPENAI_API_KEY": "k"}
    hermetic["local"] = {"ollama": ("qwen3:4b", "http://127.0.0.1:11434/v1", "", "openai")}
    calls: list[str] = []

    async def _call(name, info, messages, on_token):
        calls.append(name)
        if name == "ollama":
            raise RuntimeError("connection refused")
        return "cloud-ok"

    bridge = _bridge(hermetic)
    monkeypatch.setattr(bridge, "_call", _call)
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))
    assert (text, name) == ("cloud-ok", "openai")
    assert calls == ["ollama", "openai"]  # ollama not retried by the generic loop


def test_chat_unknown_explicit_provider_is_honest(hermetic) -> None:
    bridge = _bridge(hermetic)
    with pytest.raises(RuntimeError, match="no AI providers configured"):
        asyncio.run(bridge.chat([{"role": "user", "content": "hi"}], provider="nope"))


def test_chat_generic_cline_session_refresh(hermetic, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit clinepass with a CLI session: the token is refreshed lazily
    before the call and stored back into the config."""
    monkeypatch.setattr(cline_session, "has_session", lambda: True)
    monkeypatch.setattr(cline_session, "stored_token", lambda: "sess")
    monkeypatch.setattr(cline_session, "fresh_token", lambda: "fresh-tok")
    seen: dict = {}

    async def _call(name, info, messages, on_token):
        seen["token"] = info[2]
        return "cline-ok"

    bridge = _bridge(hermetic)
    monkeypatch.setattr(bridge, "_call", _call)
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}], provider="clinepass"))
    assert (text, name) == ("cline-ok", "clinepass")
    assert seen["token"] == "fresh-tok"
    assert bridge._configs["clinepass"][2] == "fresh-tok"


def test_chat_generic_cline_refresh_failure_surfaces(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cline_session, "has_session", lambda: True)
    monkeypatch.setattr(cline_session, "stored_token", lambda: "sess")

    def _boom() -> str:
        raise RuntimeError("session expired — run `cline auth` once")

    monkeypatch.setattr(cline_session, "fresh_token", _boom)
    bridge = _bridge(hermetic)
    with pytest.raises(RuntimeError, match="all providers failed — last error: session expired"):
        asyncio.run(bridge.chat([{"role": "user", "content": "hi"}], provider="clinepass"))


def test_chat_auto_cline_rotation_on_generic_failure(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto mode, no locals: the Cline gateway's configured model fails in the
    generic fallback → rotates through alternates and pins the winner."""
    hermetic["env"] = {"CLINEPASS_API_KEY": "zk-test"}
    tried: list[str] = []

    async def _call(name, info, messages, on_token):
        tried.append(info[0])
        if info[0] == "deepseek/deepseek-chat":
            raise RuntimeError("404 model not found")
        return "cline-ok"

    bridge = _bridge(hermetic)
    # _detect_clinepass registers both clinepass + cline-usage; isolate
    # cline-usage so the rotation covers exactly the TOP pool.
    del bridge._configs["clinepass"]
    monkeypatch.setattr(bridge, "_call", _call)
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))
    assert (text, name) == ("cline-ok", "cline-usage")
    assert tried == ["deepseek/deepseek-chat", "minimax/minimax-m2.5"]
    assert bridge._configs["cline-usage"][0] == "minimax/minimax-m2.5"


# ── _call: streaming, per-kind URLs/headers, errors ───────────────────


class _FakeStreamResp:
    def __init__(self, status: int = 200, chunks: tuple = (), body: bytes = b""):
        self.status_code = status
        self._chunks = list(chunks)
        self._body = body

    async def aread(self) -> bytes:
        return self._body

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCtx:
    def __init__(self, resp: _FakeStreamResp):
        self._resp = resp

    async def __aenter__(self) -> _FakeStreamResp:
        return self._resp

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeAsyncClient:
    """Hand-written stand-in for httpx.AsyncClient; records construction
    args and stream() calls, serves canned responses FIFO."""

    last: _FakeAsyncClient | None = None
    responses: list[_FakeStreamResp] = []

    def __init__(self, timeout=None, trust_env: bool = True):
        self.timeout = timeout
        self.trust_env = trust_env
        self.stream_calls: list = []
        _FakeAsyncClient.last = self

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def stream(self, method, url, json=None, headers=None) -> _FakeStreamCtx:
        self.stream_calls.append((method, url, json, headers))
        cls = type(self)
        resp = cls.responses.pop(0) if cls.responses else _FakeStreamResp()
        return _FakeStreamCtx(resp)


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAsyncClient]:
    _FakeAsyncClient.last = None
    _FakeAsyncClient.responses = []
    monkeypatch.setattr(ai_bridge.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


_OPENAI_CHUNKS = (
    b'data: {"choices": [{"delta": {"content": "hel"}}]}\n',
    b"\n",  # blank line skipped
    b'data: {"choices": [{"delta": {"content": "lo"}}]}\n[DONE]\n',  # [DONE] skipped
    b"this is not json\n",  # JSONDecodeError swallowed
)


def test_call_openai_streaming_success(
    hermetic, fake_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermetic["env"] = {"OPENAI_API_KEY": "k"}
    tokens: list[str] = []

    fake_http.responses.append(_FakeStreamResp(chunks=_OPENAI_CHUNKS))

    async def _run() -> str:
        bridge = _bridge(hermetic)
        info = bridge._configs["openai"]
        return await bridge._call(
            "openai", info, [{"role": "user", "content": "hi"}], tokens.append
        )

    text = asyncio.run(_run())
    assert text == "hello"
    assert tokens == ["hel", "lo"]
    client = fake_http.last
    assert client.timeout == 60.0
    assert client.trust_env is True
    (method, url, body, headers) = client.stream_calls[0]
    assert method == "POST"
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"
    assert headers["User-Agent"] == "LuckyDBrowser/9.9"
    assert body["model"] == "gpt-4o"
    assert body["stream"] is True


def test_call_gemini_url_and_delta(hermetic, fake_http) -> None:
    hermetic["env"] = {"GOOGLE_API_KEY": "gk"}

    fake_http.responses.append(
        _FakeStreamResp(
            chunks=(b'data: {"candidates": [{"content": {"parts": [{"text": "g1"}]}}]}\n',)
        )
    )

    async def _run() -> str:
        bridge = _bridge(hermetic)
        return await bridge._call(
            "google", bridge._configs["google"], [{"role": "user", "content": "hi"}], None
        )

    assert asyncio.run(_run()) == "g1"
    (_method, url, body, headers) = fake_http.last.stream_calls[0]
    assert (
        url == "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-2.0-flash:streamGenerateContent?key=gk&alt=sse"
    )
    assert body["contents"][0]["parts"] == [{"text": "hi"}]
    assert "Authorization" not in headers


def test_call_anthropic_headers_and_delta(hermetic, fake_http) -> None:
    hermetic["env"] = {"ANTHROPIC_API_KEY": "ak"}

    fake_http.responses.append(_FakeStreamResp(chunks=(b'data: {"delta": {"text": "a1"}}\n',)))

    async def _run() -> str:
        bridge = _bridge(hermetic)
        return await bridge._call(
            "anthropic",
            bridge._configs["anthropic"],
            [{"role": "user", "content": "hi"}],
            None,
        )

    assert asyncio.run(_run()) == "a1"
    (_method, url, _body, headers) = fake_http.last.stream_calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "ak"
    assert headers["anthropic-version"] == "2023-06-01"


def test_call_local_keyless_no_auth_long_timeout(hermetic, fake_http) -> None:
    """Keyless local server: no Authorization header (an empty 'Bearer '
    header would make h11 raise), 300s first-token timeout, proxy bypass."""
    hermetic["local"] = {"ollama": ("qwen3:4b", "http://127.0.0.1:11434/v1", "", "openai")}

    fake_http.responses.append(_FakeStreamResp(chunks=_OPENAI_CHUNKS))

    async def _run() -> str:
        bridge = _bridge(hermetic)
        return await bridge._call(
            "ollama", bridge._configs["ollama"], [{"role": "user", "content": "hi"}], None
        )

    assert asyncio.run(_run()) == "hello"
    client = fake_http.last
    assert client.timeout == 300.0
    assert client.trust_env is False
    (_method, url, _body, headers) = client.stream_calls[0]
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in headers


def test_call_http_error_surfaces_detail(hermetic, fake_http) -> None:
    hermetic["env"] = {"OPENAI_API_KEY": "k"}

    fake_http.responses.append(_FakeStreamResp(status=429, body=b"rate_limited: slow down"))

    async def _run() -> None:
        bridge = _bridge(hermetic)
        await bridge._call(
            "openai", bridge._configs["openai"], [{"role": "user", "content": "hi"}], None
        )

    with pytest.raises(RuntimeError, match=r"openai HTTP 429: rate_limited: slow down"):
        asyncio.run(_run())


# ── body / delta helpers ─────────────────────────────────────────────


def test_text_of_mixed_parts(hermetic) -> None:
    content = [
        {"type": "text", "text": "a"},
        "not-a-dict",
        {"type": "image_url", "text": "ignored"},
        {"type": "text", "text": "c"},
    ]
    assert AIBridge._text_of(content) == "a c"
    assert AIBridge._text_of("plain") == "plain"


def test_parse_data_url_variants(hermetic) -> None:
    assert AIBridge._parse_data_url("data:image/png;base64,QUJD") == ("image/png", "QUJD")
    assert AIBridge._parse_data_url("https://example.com/x.png") is None
    assert AIBridge._parse_data_url("data:;base64,QUJD") is None  # empty mime
    assert AIBridge._parse_data_url("data:image/png;base64,") is None  # empty data
    assert AIBridge._parse_data_url("data:image/png,QUJD") is None  # not base64


def test_parts_gemini_str_and_skips(hermetic) -> None:
    assert AIBridge._parts_gemini("hi") == [{"text": "hi"}]
    parts = AIBridge._parts_gemini(
        [
            "not-a-dict",
            {"type": "text", "text": "t"},
            {"type": "other", "text": "skipped"},  # unknown part type
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ]
    )
    assert parts == [
        {"text": "t"},
        {"inlineData": {"mimeType": "image/png", "data": "QUJD"}},
    ]
    assert AIBridge._parts_gemini([]) == [{"text": ""}]


def test_parts_anthropic_branches(hermetic) -> None:
    assert AIBridge._parts_anthropic("plain") == "plain"
    parts = AIBridge._parts_anthropic(
        [
            "not-a-dict",
            {"type": "text", "text": "t"},
            {"type": "other", "text": "skipped"},  # unknown part type
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ]
    )
    assert parts == [
        {"type": "text", "text": "t"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
        },
    ]
    assert AIBridge._parts_anthropic([]) == ""


def test_body_gemini_without_system_omits_instruction(hermetic) -> None:
    body = AIBridge._body_gemini([{"role": "user", "content": "hi"}])
    assert "systemInstruction" not in body
    assert body["contents"][0]["role"] == "user"


def test_body_anthropic_system_message(hermetic) -> None:
    body = AIBridge._body_anthropic(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    )
    assert body["system"] == "s"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_extract_delta_anthropic_content_blocks(hermetic) -> None:
    data = {"content": [{"type": "text", "text": "xy"}, {"type": "tool_use", "text": "zz"}]}
    assert AIBridge._extract_delta(data, "anthropic") == "xy"


def test_extract_delta_openai_message_fallback(hermetic) -> None:
    assert (
        AIBridge._extract_delta({"choices": [{"delta": {}, "message": {"content": "z"}}]}, "openai")
        == "z"
    )
    assert AIBridge._extract_delta({"choices": [{"delta": {}}]}, "openai") == ""


def test_extract_delta_gemini_empty_candidates(hermetic) -> None:
    assert AIBridge._extract_delta({"kind": "unrelated"}, "gemini") == ""


def test_fetch_models_non_openai_kind_skips_live_block(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gemini-kind provider never enters the OpenAI live-/models block;
    it falls back to its configured model."""

    def _must_not_run(url, headers=None, timeout=None):
        raise AssertionError("gemini-kind providers must not hit /models")

    hermetic["env"] = {"GOOGLE_API_KEY": "gk"}
    monkeypatch.setattr(ai_bridge.httpx, "get", _must_not_run)
    bridge = _bridge(hermetic)
    assert bridge.fetch_models("google") == ["gemini-2.0-flash"]


def test_routed_provider_route_task_error_returns_none(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(text: str, context_size: int = 0):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(ai_bridge, "_route_task", _boom)
    bridge = _bridge(hermetic)
    assert bridge._routed_provider([{"role": "user", "content": "hi"}]) is None


def test_chat_generic_loop_covers_post_startup_local(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A free-pool member missing from the __init__-time _local_names (a
    local server registered after startup) is NOT skipped by the generic
    loop — it proceeds to the real _call."""
    hermetic["local"] = {"ollama": ("qwen3:4b", "http://127.0.0.1:11434/v1", "", "openai")}
    calls: list[str] = []
    attempts = {"latelocal": 0}

    async def _call(name, info, messages, on_token):
        calls.append(name)
        if name == "ollama":
            raise RuntimeError("connection refused")
        if name == "latelocal":
            attempts["latelocal"] += 1
            if attempts["latelocal"] == 1:
                raise RuntimeError("warming up")
            return "late-ok"
        raise AssertionError(f"unexpected provider {name}")

    bridge = _bridge(hermetic)
    monkeypatch.setattr(bridge, "_call", _call)
    # Registered after __init__: in the free pool via _LOCAL_SPECS, but not
    # in the _local_names snapshot taken at construction.
    monkeypatch.setattr(
        ai_bridge,
        "_LOCAL_SPECS",
        [*ai_bridge._LOCAL_SPECS, ("latelocal", "LATELOCAL_HOST", "http://127.0.0.1:9999/v1", "")],
    )
    bridge._configs["latelocal"] = ("m", "http://127.0.0.1:9999/v1", "", "openai")
    text, name = asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))
    assert (text, name) == ("late-ok", "latelocal")
    assert calls == ["ollama", "latelocal", "latelocal"]


def test_chat_explicit_cline_all_models_fail(hermetic, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit Cline provider: every rotation candidate fails → the honest
    'all providers failed' error, not a hang or a silent empty reply."""
    hermetic["env"] = {"CLINEPASS_API_KEY": "zk-test"}
    tried: list[str] = []

    async def _call(name, info, messages, on_token):
        tried.append(info[0])
        raise RuntimeError("500 everywhere")

    bridge = _bridge(hermetic)
    monkeypatch.setattr(bridge, "_call", _call)
    with pytest.raises(RuntimeError, match="all providers failed — last error: 500 everywhere"):
        asyncio.run(bridge.chat([{"role": "user", "content": "hi"}], provider="cline-usage"))
    # Configured model + every alternate top model tried exactly once.
    assert tried == ["deepseek/deepseek-chat"] + [
        m for m in ai_bridge._CLINE_GATEWAY_TOP_MODELS if m != "deepseek/deepseek-chat"
    ]


def test_chat_auto_cline_rotation_exhausted_raises(
    hermetic, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto mode: the Cline gateway fails on its configured model AND every
    rotation alternate → the rotation is exhausted and the honest
    'all providers failed' error surfaces."""
    hermetic["env"] = {"CLINEPASS_API_KEY": "zk-test"}
    calls: list[str] = []

    async def _call(name, info, messages, on_token):
        calls.append(info[0])
        raise RuntimeError("cline down")

    bridge = _bridge(hermetic)
    # Isolate cline-usage (clinepass also registers from the same key).
    del bridge._configs["clinepass"]
    monkeypatch.setattr(bridge, "_call", _call)
    with pytest.raises(RuntimeError, match="all providers failed — last error: cline down"):
        asyncio.run(bridge.chat([{"role": "user", "content": "hi"}]))
    # Configured model + every alternate tried exactly once.
    assert calls == ["deepseek/deepseek-chat"] + [
        m for m in ai_bridge._CLINE_GATEWAY_TOP_MODELS if m != "deepseek/deepseek-chat"
    ]


def test_call_empty_delta_chunk_skipped(hermetic, fake_http) -> None:
    """A well-formed SSE chunk with no extractable delta contributes
    nothing and does not end the stream."""
    hermetic["env"] = {"OPENAI_API_KEY": "k"}
    fake_http.responses.append(
        _FakeStreamResp(
            chunks=(
                b'data: {"choices": [{"delta": {}}]}\n',
                b'data: {"choices": [{"delta": {"content": "x"}}]}\n',
            )
        )
    )

    async def _run() -> str:
        bridge = _bridge(hermetic)
        return await bridge._call(
            "openai", bridge._configs["openai"], [{"role": "user", "content": "hi"}], None
        )

    assert asyncio.run(_run()) == "x"
