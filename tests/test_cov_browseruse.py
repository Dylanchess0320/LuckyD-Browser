"""Coverage tests for tools/browser_use_tool.py.

Covers _check_browser_use caching, _get_api_key (env + .env file), the
BrowserUseTool execution paths (unavailable / no key / timeout / error /
result-shape variants) with a hand-written fake browser_use package, and
BrowserUseCloseTool. No network, no real browser.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import tools.browser_use_tool as bu
from tools.browser_use_tool import BrowserUseCloseTool, BrowserUseTool


@pytest.fixture()
def fresh_availability(monkeypatch):
    monkeypatch.setattr(bu, "_browser_use_available", None)
    yield
    # monkeypatch reverts the attribute automatically


def _install_fake_browser_use(monkeypatch, result=None, run_exc=None):
    """Install a fake `browser_use` package into sys.modules."""
    mod = types.ModuleType("browser_use")
    browser_mod = types.ModuleType("browser_use.browser")
    views_mod = types.ModuleType("browser_use.browser.views")

    class BrowserConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Browser:
        def __init__(self, config=None):
            self.config = config
            self.closed = False

        async def close(self):
            self.closed = True

    class Agent:
        last: Agent | None = None

        def __init__(self, task, llm, browser, use_vision):
            self.task = task
            self.llm = llm
            self.browser = browser
            self.use_vision = use_vision
            Agent.last = self

        async def run(self, max_steps=None):
            self.max_steps = max_steps
            if run_exc is not None:
                raise run_exc
            return result

    mod.Agent = Agent
    mod.Browser = Browser
    views_mod.BrowserConfig = BrowserConfig
    monkeypatch.setitem(sys.modules, "browser_use", mod)
    monkeypatch.setitem(sys.modules, "browser_use.browser", browser_mod)
    monkeypatch.setitem(sys.modules, "browser_use.browser.views", views_mod)
    return Agent, Browser


def _no_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("CODING_AGENT_API_KEY", raising=False)


# ── _check_browser_use ────────────────────────────────────────────────────


def test_check_browser_use_unavailable_and_cached(fresh_availability, monkeypatch):
    monkeypatch.delitem(sys.modules, "browser_use", raising=False)
    monkeypatch.delitem(sys.modules, "browser_use.browser", raising=False)
    monkeypatch.delitem(sys.modules, "browser_use.browser.views", raising=False)
    assert bu._check_browser_use() is False
    # cached: second call does not re-import
    assert bu._check_browser_use() is False


def test_check_browser_use_available_and_cached(fresh_availability, monkeypatch):
    _install_fake_browser_use(monkeypatch)
    assert bu._check_browser_use() is True
    monkeypatch.delitem(sys.modules, "browser_use")
    assert bu._check_browser_use() is True  # cached True


# ── _get_api_key ──────────────────────────────────────────────────────────


def test_get_api_key_prefers_deepseek_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("CODING_AGENT_API_KEY", "coding-key")
    assert bu._get_api_key() == "deep-key"


def test_get_api_key_falls_back_to_coding_agent_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CODING_AGENT_API_KEY", "coding-key")
    assert bu._get_api_key() == "coding-key"


def _fake_path_module(monkeypatch, content: str, exists: bool):
    class _FakeEnvFile:
        def exists(self):
            return exists

        def read_text(self):
            return content

    class _FakePath:
        def __init__(self, *args):
            pass

        @property
        def parent(self):
            return self

        def __truediv__(self, other):
            assert other == ".env"
            return _FakeEnvFile()

    monkeypatch.setattr(bu, "Path", _FakePath)


def test_get_api_key_from_env_file_double_quotes(monkeypatch):
    _no_api_key(monkeypatch)
    _fake_path_module(monkeypatch, 'OTHER=1\nDEEPSEEK_API_KEY="quoted-key"\n', True)
    assert bu._get_api_key() == "quoted-key"


def test_get_api_key_from_env_file_single_quotes_and_skips_bare_line(monkeypatch):
    _no_api_key(monkeypatch)
    content = "NOEQUALS DEEPSEEK_API_KEY\nDEEPSEEK_API_KEY='sq-key'\n"
    _fake_path_module(monkeypatch, content, True)
    assert bu._get_api_key() == "sq-key"


def test_get_api_key_empty_when_no_env_and_no_file(monkeypatch):
    _no_api_key(monkeypatch)
    _fake_path_module(monkeypatch, "", False)
    assert bu._get_api_key() == ""


def test_get_api_key_empty_env_file(monkeypatch):
    _no_api_key(monkeypatch)
    _fake_path_module(monkeypatch, "", True)
    assert bu._get_api_key() == ""


# ── BrowserUseTool.execute ────────────────────────────────────────────────


async def test_execute_when_browser_use_missing(monkeypatch):
    monkeypatch.setattr(bu, "_check_browser_use", lambda: False)
    out = await BrowserUseTool().execute(task="do things")
    assert out.error
    assert out.title == "browser-use unavailable"
    assert "pip install browser-use" in out.text


async def test_execute_without_api_key(monkeypatch):
    _install_fake_browser_use(monkeypatch)
    monkeypatch.setattr(bu, "_check_browser_use", lambda: True)
    _no_api_key(monkeypatch)
    _fake_path_module(monkeypatch, "", False)
    out = await BrowserUseTool().execute(task="do things")
    assert out.error
    assert out.title == "No API key"
    assert "DEEPSEEK_API_KEY" in out.text


class _Result:
    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)

    def __str__(self):
        return "str-of-result"


async def _run_success(monkeypatch, result, **kwargs):
    agent_cls, _ = _install_fake_browser_use(monkeypatch, result=result)
    monkeypatch.setattr(bu, "_check_browser_use", lambda: True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    out = await BrowserUseTool().execute(task="search fastapi", **kwargs)
    return out, agent_cls


async def test_execute_success_final_result_callable(monkeypatch):
    out, agent_cls = await _run_success(monkeypatch, _Result(final_result=lambda: "the answer"))
    assert not out.error
    assert out.text == "the answer"
    assert out.title == "Browser Agent Task Complete"
    assert out.metadata["task"] == "search fastapi"
    assert out.metadata["max_steps"] == 15
    assert out.metadata["headless"] is True
    assert out.metadata["use_vision"] is True
    assert agent_cls.last.llm == "k"
    assert agent_cls.last.max_steps == 15
    assert agent_cls.last.use_vision is True
    assert agent_cls.last.browser.config.kwargs["headless"] is True
    assert agent_cls.last.browser.config.kwargs["disable_security"] is False


async def test_execute_success_custom_flags(monkeypatch):
    out, agent_cls = await _run_success(
        monkeypatch,
        _Result(final_result="plain"),
        max_steps=5,
        headless=False,
        use_vision=False,
    )
    assert not out.error
    assert out.text == "plain"
    assert out.metadata["max_steps"] == 5
    assert agent_cls.last.max_steps == 5
    assert agent_cls.last.browser.config.kwargs["headless"] is False


async def test_execute_final_result_callable_raises_falls_back(monkeypatch):
    def bad():
        raise RuntimeError("nope")

    out, _ = await _run_success(monkeypatch, _Result(final_result=bad))
    assert not out.error
    assert out.text == "str-of-result"


async def test_execute_model_dump_with_history(monkeypatch):
    dump = {
        "history": [
            {"result": "found 10k stars"},
            {"no_result": True},
            "not-a-dict",
            {"result": ""},
            {"result": "done"},
        ]
    }
    out, _ = await _run_success(monkeypatch, _Result(model_dump=lambda: dump))
    assert not out.error
    assert "Step 1: found 10k stars" in out.text
    assert "Step 5: done" in out.text
    assert "Step 2" not in out.text


async def test_execute_model_dump_empty_history_uses_dump_str(monkeypatch):
    out, _ = await _run_success(monkeypatch, _Result(model_dump=lambda: {"history": []}))
    assert not out.error
    assert "{'history': []}" in out.text


async def test_execute_model_dump_non_callable_dict(monkeypatch):
    out, _ = await _run_success(monkeypatch, _Result(model_dump={"history": [{"result": "x"}]}))
    assert not out.error
    assert "Step 1: x" in out.text


async def test_execute_model_dump_non_dict_falls_back_to_str(monkeypatch):
    out, _ = await _run_success(monkeypatch, _Result(model_dump=lambda: [1, 2]))
    assert not out.error
    assert out.text == "str-of-result"


async def test_execute_plain_result_str(monkeypatch):
    out, _ = await _run_success(monkeypatch, "just a string")
    assert not out.error
    assert out.text == "just a string"


async def test_execute_timeout(monkeypatch):
    _install_fake_browser_use(monkeypatch, run_exc=asyncio.TimeoutError())
    monkeypatch.setattr(bu, "_check_browser_use", lambda: True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    out = await BrowserUseTool().execute(task="slow task")
    assert out.error
    assert out.title == "Browser Task Timeout"
    assert "120s" in out.text


async def test_execute_generic_error(monkeypatch):
    _install_fake_browser_use(monkeypatch, run_exc=ValueError("bad page"))
    monkeypatch.setattr(bu, "_check_browser_use", lambda: True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    out = await BrowserUseTool().execute(task="task")
    assert out.error
    assert out.title == "Browser Task Error"
    assert "ValueError: bad page" in out.text


# ── BrowserUseCloseTool ───────────────────────────────────────────────────


async def test_close_tool():
    out = await BrowserUseCloseTool().execute()
    assert not out.error
    assert out.text == "browser-use resources released."
    assert out.title == "Browser Closed"
