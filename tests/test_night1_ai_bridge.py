"""Night-1 browser-core audit: browser/browser_core/ai_bridge.py.

Provider viability, local-AI status diagnoses, vision heuristics, model
catalogs, routing helpers, and the chat() fallback chain.
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
def hermetic(monkeypatch: pytest.MonkeyPatch):
    """AIBridge with no real env, no localhost probes, no Cline session."""

    def _fake_load_env(**env: str) -> dict[str, str]:
        return dict(env)

    monkeypatch.setattr(ai_bridge, "_load_env", staticmethod(lambda: _fake_load_env()))
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: {}))
    monkeypatch.setattr(cline_session, "has_session", lambda: False)
    return _fake_load_env


def _bridge_with(env: dict, local: dict | None = None) -> AIBridge:
    """Build a bridge with a fixed env and fixed local-server detections."""
    orig_load, orig_detect, orig_session = (
        ai_bridge._load_env,
        AIBridge._detect_local,
        cline_session.has_session,
    )
    ai_bridge._load_env = staticmethod(lambda: dict(env))  # type: ignore[assignment]
    AIBridge._detect_local = staticmethod(lambda e: dict(local or {}))  # type: ignore[assignment]
    cline_session.has_session = lambda: False  # type: ignore[assignment]
    try:
        return AIBridge()
    finally:
        ai_bridge._load_env = orig_load  # type: ignore[assignment]
        AIBridge._detect_local = orig_detect  # type: ignore[assignment]
        cline_session.has_session = orig_session  # type: ignore[assignment]


# ── provider registration & defaults ─────────────────────────────────


def test_default_provider_prefers_local(hermetic) -> None:
    b = _bridge_with({}, local={"ollama": ("qwen3:4b", "http://x/v1", "", "openai")})
    assert b.default_provider() == "ollama"


def test_default_provider_local_order_is_deterministic(hermetic) -> None:
    """Both locals up → Ollama wins, every process (no set-order flakiness)."""
    local = {
        "ollama": ("qwen3:4b", "http://127.0.0.1:11434/v1", "", "openai"),
        "lmstudio": ("m", "http://127.0.0.1:1234/v1", "", "openai"),
    }
    b = _bridge_with({}, local=local)
    assert b._free_unlimited_providers() == ["ollama", "lmstudio"]
    for _ in range(5):
        assert _bridge_with({}, local=local).default_provider() == "ollama"


def test_default_provider_skips_unusable_cline(hermetic) -> None:
    """clinepass/cline-usage register with an empty token for the catalog
    fallback — default_provider must not pick that dead end."""
    b = _bridge_with({})
    assert "cline-usage" in b.providers()
    assert b.default_provider() is None


def test_default_provider_keyed_clouds_in_spec_order(hermetic) -> None:
    b = _bridge_with({"DEEPSEEK_API_KEY": "k", "OPENAI_API_KEY": "k2"})
    assert b.default_provider() == "deepseek"  # _PROVIDER_SPECS order


def test_provider_env_overrides(hermetic) -> None:
    b = _bridge_with(
        {
            "DEEPSEEK_API_KEY": "k",
            "DEEPSEEK_MODEL": "deepseek-pro",
            "DEEPSEEK_BASE_URL": "https://x/v1",
        }
    )
    assert b.model_for("deepseek") == "deepseek-pro"
    info = b._configs["deepseek"]
    assert info[1] == "https://x/v1"


def test_opencode_registers_when_keyed_restored_2026_09_21(hermetic) -> None:
    """OpenCode Zen restored to the mesh 2026-09-21 — keyed registration only.

    The $0 keyless tier died 2026-09, so no key means no registration;
    OPENCODE_API_KEY registers the Zen gateway (endpoint-labelled).
    """
    assert "opencode" not in _bridge_with({}).providers()
    b = _bridge_with({"OPENCODE_API_KEY": "zk"})
    assert "opencode" in b.providers()
    assert b.is_opencode_zen("opencode")
    assert b.provider_label("opencode") == "OpenCode Zen"
    # Cline gateways still register alongside, labelled "Cline".
    assert b.is_cline_gateway("clinepass")
    assert b.is_cline_gateway("cline-usage")
    assert b.provider_label("clinepass") == "Cline"
    assert b.provider_label("openai") is None


def test_is_viable_provider(hermetic) -> None:
    b = _bridge_with(
        {"OPENAI_API_KEY": "k"},
        local={"ollama": ("m", "http://x/v1", "", "openai")},
    )
    assert b._is_viable_provider("ollama")
    assert b._is_viable_provider("openai")
    assert not b._is_viable_provider("cline-usage")  # empty token
    assert not b._is_viable_provider("nope")


def test_is_local_excludes_session_placeholder(hermetic) -> None:
    """A session-backed clinepass with an empty placeholder token is not 'local'."""
    b = _bridge_with({})
    assert not b.is_local("clinepass")
    b2 = _bridge_with({}, local={"ollama": ("m", "http://x/v1", "", "openai")})
    assert b2.is_local("ollama")


# ── vision heuristics ────────────────────────────────────────────────


def test_supports_vision(hermetic) -> None:
    b = _bridge_with({"GOOGLE_API_KEY": "k", "DEEPSEEK_API_KEY": "k"})
    assert b.supports_vision("google")  # gemini-2.0-flash
    assert not b.supports_vision("deepseek")  # deepseek-v4-flash
    assert not b.supports_vision("missing")
    b.set_model_override("deepseek", "gpt-4o-mini")
    assert b.supports_vision("deepseek")
    # Text-only variant vetoed even though the family hint matches.
    b2 = _bridge_with({})
    b2._configs["tiny"] = ("gemma3:1b", "http://x/v1", "", "openai")
    assert not b2.supports_vision("tiny")


def test_set_model_override(hermetic) -> None:
    b = _bridge_with({"OPENAI_API_KEY": "k"})
    before = b.model_for("openai")
    b.set_model_override("openai", "gpt-4o-mini")
    assert b.model_for("openai") == "gpt-4o-mini"
    b.set_model_override("openai", "   ")  # blank ignored
    assert b.model_for("openai") == "gpt-4o-mini"
    b.set_model_override("missing", "x")  # unknown provider ignored
    assert b.model_for("openai") == "gpt-4o-mini"
    assert before == "gpt-6-sol"


# ── local status diagnoses ───────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload=None, exc: Exception | None = None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


def test_detect_local_status_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_bridge.httpx,
        "get",
        lambda *a, **k: _FakeResp({"data": [{"id": "qwen3:4b"}, {"id": "nomic-embed-text"}]}),
    )
    found = AIBridge._detect_local({"OLLAMA_MODEL": "qwen3:4b"})
    assert found["ollama"][0] == "qwen3:4b"  # env pick honored
    assert AIBridge._local_status["ollama"] == "ok"


def test_detect_local_status_not_running(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_bridge.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
    )
    found = AIBridge._detect_local({})
    assert found == {}
    assert AIBridge._local_status["ollama"] == "not_running"


def test_detect_local_status_no_models(monkeypatch) -> None:
    monkeypatch.setattr(ai_bridge.httpx, "get", lambda *a, **k: _FakeResp({"data": []}))
    assert AIBridge._detect_local({}) == {}
    assert AIBridge._local_status["ollama"] == "no_models"


def test_detect_local_model_preference(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_bridge.httpx,
        "get",
        lambda *a, **k: _FakeResp({"data": [{"id": "mistral:7b"}, {"id": "qwen3:8b"}]}),
    )
    found = AIBridge._detect_local({})
    # "qwen3" ranks above "mistral" in _LOCAL_MODEL_PREF
    assert found["ollama"][0] == "qwen3:8b"


def test_detect_local_bypasses_proxy(monkeypatch) -> None:
    """The localhost probe must use trust_env=False (the proxy ghost)."""
    seen: dict = {}

    def _get(url, **kwargs):
        seen.update(kwargs)
        return _FakeResp({"data": [{"id": "qwen3:4b"}]})

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    AIBridge._detect_local({})
    assert seen.get("trust_env") is False


def test_local_status_snapshot(hermetic) -> None:
    b = _bridge_with({})
    assert b.local_status() == dict(AIBridge._local_status)


# ── model catalogs ───────────────────────────────────────────────────


def _raise_httpx(*a, **k):
    raise RuntimeError("offline")


def test_fetch_models_clinepass_curated_fallback(monkeypatch, hermetic) -> None:
    monkeypatch.setattr(ai_bridge.httpx, "get", _raise_httpx)
    b = _bridge_with({})
    models = b.fetch_models("clinepass")
    assert "cline-pass/kimi-k3" in models
    # Fallback is the full gateway catalog, top-sorted: Cline top models lead.
    assert set(models) == set(ai_bridge._CLINE_GATEWAY_CATALOG)
    assert models[:6] == list(ai_bridge._CLINE_GATEWAY_TOP_MODELS[:6])


def test_fetch_models_cline_usage_catalog(monkeypatch, hermetic) -> None:
    monkeypatch.setattr(ai_bridge.httpx, "get", _raise_httpx)
    b = _bridge_with({})
    assert "deepseek/deepseek-chat" in b.fetch_models("cline-usage")


def test_fetch_models_openrouter_free_first(monkeypatch, hermetic) -> None:
    monkeypatch.setattr(ai_bridge.httpx, "get", _raise_httpx)
    b = _bridge_with({"OPENROUTER_API_KEY": "k"})
    models = b.fetch_models("openrouter")
    assert models[0] == "openrouter/free"


def test_fetch_models_live_filters_embeds_and_caches(monkeypatch, hermetic) -> None:
    calls = []

    def _get(url, **kwargs):
        calls.append(url)
        return _FakeResp({"data": [{"id": "qwen3:4b"}, {"id": "nomic-embed-text"}]})

    monkeypatch.setattr(ai_bridge.httpx, "get", _get)
    b = _bridge_with({}, local={"ollama": ("qwen3:4b", "http://x/v1", "", "openai")})
    models = b.fetch_models("ollama")
    assert models == ["qwen3:4b"]
    assert b.fetch_models("ollama") == ["qwen3:4b"]
    assert len(calls) == 1  # cached


def test_fetch_models_unknown_provider(hermetic) -> None:
    assert _bridge_with({}).fetch_models("nope") == []


# ── routing helpers ──────────────────────────────────────────────────


def test_routing_text_picks_latest_user(hermetic) -> None:
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": [{"type": "text", "text": "second"}]},
    ]
    assert AIBridge._routing_text(msgs) == "second"
    assert AIBridge._routing_text([]) == ""
    assert AIBridge._routing_text([{"role": "user", "content": [{"type": "image_url"}]}]) == ""


def test_routing_context_size(hermetic) -> None:
    msgs = [{"role": "user", "content": "x" * 400}]
    assert AIBridge._routing_context_size(msgs) == 100
    assert AIBridge._routing_context_size(None) == 0


# ── message bodies & delta extraction ────────────────────────────────


def test_body_gemini_multimodal(hermetic) -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ],
        },
    ]
    body = AIBridge._body_gemini(msgs)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "hi"}
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}
    assert body["contents"][0]["role"] == "user"


def test_body_anthropic_tool_role_maps_to_assistant(hermetic) -> None:
    # NOTE: _body_anthropic folds "tool" into "assistant" while _body_gemini
    # folds it into "user" — inconsistent; flagged in the night-1 report for
    # review rather than changed blind (live Anthropic payload shape).
    body = AIBridge._body_anthropic(
        [{"role": "tool", "content": "out"}, {"role": "assistant", "content": "ack"}]
    )
    assert [m["role"] for m in body["messages"]] == ["assistant", "assistant"]
    assert body["max_tokens"] == 4096


def test_body_openai_preserves_roles(hermetic) -> None:
    body = AIBridge._body_openai([{"role": "system", "content": "s"}])
    assert body["messages"][0]["role"] == "system"
    assert body["stream"] is True


def test_extract_delta_kinds(hermetic) -> None:
    assert (
        AIBridge._extract_delta(
            {"candidates": [{"content": {"parts": [{"text": "ab"}]}}]}, "gemini"
        )
        == "ab"
    )
    assert AIBridge._extract_delta({"delta": {"text": "cd"}}, "anthropic") == "cd"
    assert AIBridge._extract_delta({"choices": [{"delta": {"content": "ef"}}]}, "openai") == "ef"
    assert AIBridge._extract_delta({"choices": []}, "openai") == ""
    assert AIBridge._extract_delta({}, "openai") == ""


# ── chat() fallback chain ────────────────────────────────────────────


def _mk_auto_bridge(**env) -> AIBridge:
    return _bridge_with(
        env,
        local={"ollama": ("qwen3:4b", "http://127.0.0.1:11434/v1", "", "openai")},
    )


def test_chat_auto_reports_fast_path_error_not_configured(hermetic, monkeypatch) -> None:
    """Ollama down in the fast path used to be misreported as 'no AI
    providers configured'; the real error must survive."""
    b = _mk_auto_bridge()

    async def _boom(name, info, messages, on_token):
        raise RuntimeError("ollama connection refused")

    monkeypatch.setattr(b, "_call", _boom)
    # clinepass/cline-usage register with empty tokens → skipped in auto mode,
    # so only ollama is tried; nothing else configured.
    with pytest.raises(RuntimeError, match="all providers failed"):
        asyncio.run(b.chat([{"role": "user", "content": "hi"}]))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(b.chat([{"role": "user", "content": "hi"}]))
    assert "connection refused" in str(excinfo.value)


def test_chat_auto_honest_when_nothing_configured(hermetic) -> None:
    b = _bridge_with({})
    with pytest.raises(RuntimeError, match="no AI providers configured"):
        asyncio.run(b.chat([{"role": "user", "content": "hi"}]))


def test_chat_auto_falls_back_across_chain(hermetic, monkeypatch) -> None:
    """Auto mode: first keyed provider fails → next in the chain is tried."""
    b = _bridge_with({"OPENAI_API_KEY": "k", "DEEPSEEK_API_KEY": "k2"})
    seen = []

    async def _call(name, info, messages, on_token):
        seen.append(name)
        if name == "deepseek":
            raise RuntimeError("deepseek down")
        return "ok-text"

    monkeypatch.setattr(b, "_call", _call)
    text, name = asyncio.run(b.chat([{"role": "user", "content": "hi"}]))
    assert (text, name) == ("ok-text", "openai")
    assert seen == ["deepseek", "openai"]


def test_chat_explicit_provider_failure_surfaces(hermetic, monkeypatch) -> None:
    b = _bridge_with({"DEEPSEEK_API_KEY": "k"})

    async def _boom(name, info, messages, on_token):
        raise RuntimeError("deepseek down")

    monkeypatch.setattr(b, "_call", _boom)
    with pytest.raises(RuntimeError, match="all providers failed — last error: deepseek down"):
        asyncio.run(b.chat([{"role": "user", "content": "hi"}], provider="deepseek"))


def test_chat_auto_string_sentinel(hermetic, monkeypatch) -> None:
    """The UI's "auto" provider string must behave as auto mode."""
    b = _mk_auto_bridge()
    calls = []

    async def _call(name, info, messages, on_token):
        calls.append(name)
        return "auto-ok"

    monkeypatch.setattr(b, "_call", _call)
    text, name = asyncio.run(b.chat([{"role": "user", "content": "hi"}], provider="auto"))
    assert (text, name) == ("auto-ok", "ollama")
    assert calls == ["ollama"]


def test_chat_cline_explicit_rotates_models(hermetic, monkeypatch) -> None:
    """Explicit Cline provider: per-model rotation on failure, config updated."""
    b = _bridge_with({"CLINEPASS_API_KEY": "zk"})
    tried = []

    async def _call(name, info, messages, on_token):
        tried.append(info[0])
        if info[0] == "deepseek/deepseek-chat":
            raise RuntimeError("404 model not found")
        return "cline-ok"

    monkeypatch.setattr(b, "_call", _call)
    text, name = asyncio.run(b.chat([{"role": "user", "content": "hi"}], provider="cline-usage"))
    assert text == "cline-ok" and name == "cline-usage"
    assert tried[0] == "deepseek/deepseek-chat"
    assert len(tried) >= 2
    assert b.model_for("cline-usage") == tried[-1]
