"""LuckyD 10.7.6: workflow AI-generation step validation (pure Python).

Pins for ``normalize_generated_steps`` — plain-English "Generate with AI"
output must become stored workflow steps under the same rules as recorded
ones (self-healing fingerprints kept, bad steps dropped, navigate URLs must
be http(s)). The Qt-dependent halves of the feature (the backend method,
the ``POST /workflow/generate`` route, Ask Lucky page detection) live in
tests/test_cov_control_server.py, which owns the Qt stub machinery — this
file deliberately imports no Qt-mocked module so it can never disturb it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
for _p in (str(_BROWSER_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.append(_p)

from browser_core.workflows import normalize_generated_steps


def test_generated_steps_accepts_list_and_wrapper():
    steps = [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "back"},
    ]
    assert normalize_generated_steps(steps) == steps
    assert normalize_generated_steps({"steps": steps}) == steps


def test_generated_steps_drops_invalid_and_keeps_good():
    raw = [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click"},  # no index -> dropped
        {"action": "teleport"},  # unknown action -> dropped
        "not-a-dict",
        {"action": "click", "index": 0},  # index 0 is valid
    ]
    out = normalize_generated_steps(raw)
    assert out == [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click", "index": 0},
    ]


def test_generated_steps_navigate_requires_http_url():
    assert normalize_generated_steps([{"action": "navigate", "url": "example.com"}]) == []
    assert normalize_generated_steps([{"action": "navigate", "url": "https://x.io"}]) == [
        {"action": "navigate", "url": "https://x.io"}
    ]


def test_generated_steps_keeps_target_fingerprint_for_self_healing():
    out = normalize_generated_steps(
        [{"action": "click", "index": 3, "target": {"tag": "button", "text": "Sign in"}}]
    )
    assert out == [
        {
            "action": "click",
            "index": 3,
            "target": {"tag": "button", "text": "Sign in"},
        }
    ]
    # A target without a tag is not a usable fingerprint — dropped.
    out2 = normalize_generated_steps([{"action": "click", "index": 1, "target": {"text": "x"}}])
    assert out2 == [{"action": "click", "index": 1}]


def test_generated_steps_garbage_input_is_empty_not_crash():
    assert normalize_generated_steps(None) == []
    assert normalize_generated_steps("[]") == []
    assert normalize_generated_steps({"steps": "nope"}) == []
    assert normalize_generated_steps([1, 2, 3]) == []


def test_generated_steps_type_step_keeps_text():
    out = normalize_generated_steps([{"action": "type", "index": 2, "text": "hello"}])
    assert out == [{"action": "type", "index": 2, "text": "hello"}]
