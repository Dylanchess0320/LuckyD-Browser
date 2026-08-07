"""Greasemonkey-style userscript engine built on QWebEngineScript.

Two sources:
  browser/assets/userscripts/   — built-ins shipped with the browser
  browser/data/userscripts/     — user-added scripts (editable, rescan-able)

Each .user.js file may start with a ==UserScript== metadata block:
  // ==UserScript==
  // @name        My Script
  // @match       *://*.example.com/*
  // @run-at      document-start | document-end   (default: document-end)
  // ==/UserScript==

QWebEngineScript has no per-URL filtering, so the match patterns are baked
into a small guard wrapper around the user's code. Enabled/disabled state
persists in settings.json ("userscript_disabled": [names]).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from browser_core.settings import DATA_DIR
from PySide6.QtWebEngineCore import QWebEngineScript

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "assets" / "userscripts"
USER_DIR = DATA_DIR / "userscripts"

_META_RE = re.compile(r"==UserScript==(.*?)==/UserScript==", re.DOTALL)
_TAG_RE = re.compile(r"@(\w[\w-]*)\s+(.*)")


@dataclass
class UserScript:
    path: Path
    name: str
    matches: list[str]
    run_at: str
    code: str
    builtin: bool = False


def parse_userscript(text: str, path: Path, builtin: bool = False) -> UserScript:
    name, matches, run_at = path.stem, [], "document-end"
    block = _META_RE.search(text)
    if block:
        for tag, value in _TAG_RE.findall(block.group(1)):
            value = value.strip()
            if tag == "name" and value:
                name = value
            elif tag == "match" and value:
                matches.append(value)
            elif tag == "run-at" and value in ("document-start", "document-end"):
                run_at = value
    if not matches:
        matches = ["*://*/*"]
    return UserScript(path, name, matches, run_at, text, builtin)


def _glob_to_regex(pattern: str) -> str:
    """Greasemonkey @match glob -> regex source (e.g. *://*.example.com/*)."""
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch in ".+?^${}()|[]\\/":  # "/" MUST escape — the source is wrapped in /…/
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def wrapped_source(script: UserScript) -> str:
    regexes = ", ".join("/" + _glob_to_regex(m) + "/" for m in script.matches)
    return (
        f"(() => {{ const _ldm = [{regexes}];\n"
        "if (!_ldm.some(r => r.test(location.href))) return;\n"
        f"{script.code}\n}})();"
    )


def load_scripts() -> list[UserScript]:
    scripts: list[UserScript] = []
    for directory, builtin in ((BUILTIN_DIR, True), (USER_DIR, False)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.user.js")):
            try:
                scripts.append(parse_userscript(path.read_text(encoding="utf-8"), path, builtin))
            except Exception:
                continue
    return scripts


class ScriptEngine:
    """Installs enabled userscripts into a QWebEngineProfile."""

    def __init__(self, profile, settings):
        self._profile = profile
        self._settings = settings
        self._scripts: list[UserScript] = []
        self.rescan()

    def scripts(self) -> list[UserScript]:
        return list(self._scripts)

    @staticmethod
    def user_dir() -> Path:
        return USER_DIR

    def is_enabled(self, script: UserScript) -> bool:
        disabled = self._settings.get("userscript_disabled", []) or []
        return script.name not in disabled

    def set_enabled(self, script: UserScript, enabled: bool) -> None:
        disabled = list(self._settings.get("userscript_disabled", []) or [])
        if enabled and script.name in disabled:
            disabled.remove(script.name)
        if not enabled and script.name not in disabled:
            disabled.append(script.name)
        self._settings.set("userscript_disabled", disabled)
        self.install()

    def rescan(self) -> None:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        self._scripts = load_scripts()
        self.install()

    def install(self) -> None:
        collection = self._profile.scripts()
        collection.clear()
        for script in self._scripts:
            if not self.is_enabled(script):
                continue
            qscript = QWebEngineScript()
            qscript.setName(f"luckyd:{script.name}")
            qscript.setSourceCode(wrapped_source(script))
            qscript.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            qscript.setRunsOnSubFrames(False)
            qscript.setInjectionPoint(
                QWebEngineScript.InjectionPoint.DocumentCreation
                if script.run_at == "document-start"
                else QWebEngineScript.InjectionPoint.DocumentReady
            )
            collection.insert(qscript)
