"""Night-4 browser-core audit: scripts.py (userscript engine) round 1.

Pure-logic functions (parse_userscript, _glob_to_regex, wrapped_source,
load_scripts) plus ScriptEngine with a stubbed QWebEngine profile and a
dict-backed settings object. No Qt WebEngine, no real profile.
"""

from __future__ import annotations

import re
import sys
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

import browser_core.scripts as scripts
from browser_core.scripts import (
    ScriptEngine,
    _glob_to_regex,
    load_scripts,
    parse_userscript,
    wrapped_source,
)

# ── parse_userscript ─────────────────────────────────────────────────


def test_parse_full_metadata() -> None:
    text = """// ==UserScript==
// @name        Dark Everything
// @match       *://*.example.com/*
// @match       https://other.org/x/*
// @run-at      document-start
// ==/UserScript==
console.log('hi');
"""
    us = parse_userscript(text, Path("dark.user.js"))
    assert us.name == "Dark Everything"
    assert us.matches == ["*://*.example.com/*", "https://other.org/x/*"]
    assert us.run_at == "document-start"
    assert us.code == text
    assert us.builtin is False


def test_parse_no_metadata_uses_defaults() -> None:
    us = parse_userscript("alert(1);", Path("/x/my-script.user.js"))
    assert us.name == "my-script.user"
    assert us.matches == ["*://*/*"]
    assert us.run_at == "document-end"


def test_parse_invalid_run_at_ignored() -> None:
    text = "// ==UserScript==\n// @run-at sometime\n// ==/UserScript==\n"
    assert parse_userscript(text, Path("a.user.js")).run_at == "document-end"


def test_parse_empty_name_ignored() -> None:
    # Regression: a whitespace-only @name used to capture the next line's
    # "//" comment prefix as the script name.
    text = "// ==UserScript==\n// @name   \n// ==/UserScript==\n"
    assert parse_userscript(text, Path("fallback.user.js")).name == "fallback.user"


def test_parse_unknown_tags_ignored() -> None:
    text = "// ==UserScript==\n// @namespace foo\n// @name Real\n// ==/UserScript==\n"
    us = parse_userscript(text, Path("a.user.js"))
    assert us.name == "Real"


def test_parse_malformed_match_still_recorded() -> None:
    # bare @match (no value) is ignored → default glob. Regression: the old
    # regex crossed the newline and captured the next line's "//" as a match.
    text = "// ==UserScript==\n// @match\n// ==/UserScript==\n"
    assert parse_userscript(text, Path("a.user.js")).matches == ["*://*/*"]
    assert "//" not in parse_userscript(text, Path("a.user.js")).matches


# ── _glob_to_regex ───────────────────────────────────────────────────


def test_glob_to_regex_matches_expected_urls() -> None:
    rx = re.compile(_glob_to_regex("*://*.example.com/*"))
    assert rx.search("https://www.example.com/page")
    assert not rx.search("https://example.com.evil.com/")


def test_glob_to_regex_escapes_dots_and_slashes() -> None:
    src = _glob_to_regex("https://a.com/b/c")
    assert "\\." in src and "\\/" in src
    assert re.compile(src).search("https://a.com/b/c")


def test_glob_to_regex_special_chars_escaped() -> None:
    src = _glob_to_regex("*://x.com/a+b?c*")
    assert "\\+" in src and "\\?" in src
    assert re.compile(src).search("https://x.com/a+b?cZZZ")


# ── wrapped_source ───────────────────────────────────────────────────


def test_wrapped_source_guards_and_embeds_code() -> None:
    us = parse_userscript(
        "// ==UserScript==\n// @name N\n// @match *://a.com/*\n// ==/UserScript==\ncode();",
        Path("n.user.js"),
    )
    out = wrapped_source(us)
    assert out.startswith("(() => {")
    assert out.endswith("})();")
    assert "code();" in out
    assert "r.test(location.href)" in out
    # the match pattern became a JS regex literal
    assert "/.*:\\/\\/a\\.com\\/.*/" in out


def test_wrapped_source_multiple_matches() -> None:
    us = parse_userscript("x", Path("n.user.js"))
    us.matches = ["*://a.com/*", "*://b.com/*"]
    out = wrapped_source(us)
    assert out.count("r.test") == 1
    assert "const _ldm = [" in out
    assert out.count("/.*:") == 2


# ── load_scripts ─────────────────────────────────────────────────────


def test_load_scripts_reads_both_dirs(tmp_path, monkeypatch) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    (builtin / "b.user.js").write_text("// ==UserScript==\n// @name B\n// ==/UserScript==\n")
    (user / "u.user.js").write_text("plain")
    monkeypatch.setattr(scripts, "BUILTIN_DIR", builtin)
    monkeypatch.setattr(scripts, "USER_DIR", user)
    got = load_scripts()
    assert [(s.name, s.builtin) for s in got] == [("B", True), ("u.user", False)]


def test_load_scripts_sorted_and_skips_unreadable(tmp_path, monkeypatch) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "z.user.js").write_text("z")
    (d / "a.user.js").write_text("a")
    (d / "broken.user.js").mkdir()  # read_text raises IsADirectoryError → skipped
    monkeypatch.setattr(scripts, "BUILTIN_DIR", d)
    monkeypatch.setattr(scripts, "USER_DIR", tmp_path / "missing")
    got = load_scripts()
    assert [s.name for s in got] == ["a.user", "z.user"]


def test_load_scripts_missing_dirs_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(scripts, "BUILTIN_DIR", tmp_path / "no1")
    monkeypatch.setattr(scripts, "USER_DIR", tmp_path / "no2")
    assert load_scripts() == []


# ── ScriptEngine ─────────────────────────────────────────────────────


class _Settings:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value


class _Collection:
    def __init__(self):
        self.items = []
        self.cleared = 0

    def clear(self):
        self.cleared += 1
        self.items = []

    def insert(self, script):
        self.items.append(script)


class _Profile:
    def __init__(self):
        self.collection = _Collection()

    def scripts(self):
        return self.collection


def _qscript_factory():
    """QWebEngineScript stub producing a FRESH mock per instantiation.

    With PySide6 mocked, `QWebEngineScript()` returns the same object every
    call (the AGENTS.md MagicMock-subclass lesson) — real Qt returns a new
    QWebEngineScript. The factory keeps class-level sentinels
    (InjectionPoint.*, ScriptWorldId.*) stable while instances differ.
    """
    maker = MagicMock()
    maker.side_effect = lambda *a, **k: MagicMock()
    return maker


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(scripts, "QWebEngineScript", _qscript_factory())
    user = tmp_path / "user"
    user.mkdir()
    (user / "one.user.js").write_text(
        "// ==UserScript==\n// @name One\n// @run-at document-start\n// ==/UserScript==\n1;"
    )
    (user / "two.user.js").write_text("// ==UserScript==\n// @name Two\n// ==/UserScript==\n2;")
    monkeypatch.setattr(scripts, "BUILTIN_DIR", tmp_path / "no-builtin")
    monkeypatch.setattr(scripts, "USER_DIR", user)
    # QWebEngineScript is a MagicMock via the mocked Qt modules.
    profile = _Profile()
    settings = _Settings()
    eng = ScriptEngine(profile, settings)
    return eng, profile, settings


def test_engine_rescan_installs_enabled(engine) -> None:
    eng, profile, _ = engine
    assert [s.name for s in eng.scripts()] == ["One", "Two"]
    assert len(profile.collection.items) == 2
    installed = [i.setName.call_args[0][0] for i in profile.collection.items]
    assert installed == ["luckyd:One", "luckyd:Two"]


def test_engine_set_enabled_disables(engine) -> None:
    eng, profile, settings = engine
    one = next(s for s in eng.scripts() if s.name == "One")
    assert eng.is_enabled(one) is True
    eng.set_enabled(one, False)
    assert eng.is_enabled(one) is False
    assert settings.store["userscript_disabled"] == ["One"]
    installed = [i.setName.call_args[0][0] for i in profile.collection.items]
    assert installed == ["luckyd:Two"]


def test_engine_set_enabled_reenables(engine) -> None:
    eng, profile, _ = engine
    one = next(s for s in eng.scripts() if s.name == "One")
    eng.set_enabled(one, False)
    eng.set_enabled(one, True)
    assert eng.is_enabled(one) is True
    assert len(profile.collection.items) == 2


def test_engine_install_run_at_injection_point(engine) -> None:
    _eng, profile, _ = engine
    qmod = scripts.QWebEngineScript  # the patched factory (stable sentinels)
    by_name = {i.setName.call_args[0][0]: i for i in profile.collection.items}
    assert (
        by_name["luckyd:One"].setInjectionPoint.call_args[0][0]
        is qmod.InjectionPoint.DocumentCreation
    )
    assert (
        by_name["luckyd:Two"].setInjectionPoint.call_args[0][0] is qmod.InjectionPoint.DocumentReady
    )


def test_engine_user_dir_static() -> None:
    assert ScriptEngine.user_dir() is scripts.USER_DIR


def test_engine_is_enabled_handles_none_setting(engine) -> None:
    eng, _, settings = engine
    settings.store["userscript_disabled"] = None
    assert eng.is_enabled(eng.scripts()[0]) is True
