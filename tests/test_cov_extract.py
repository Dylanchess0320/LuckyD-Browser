"""Coverage tests for browser_core.extract — message building + lenient JSON parsing."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.extract import (
    EXTRACTION_SYSTEM,
    _try_parse,
    build_messages,
    parse_json_loose,
)


class TestBuildMessages:
    def test_shape_without_schema_or_meta(self):
        msgs = build_messages("list names", None, "Alice Bob", "")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "system", "content": EXTRACTION_SYSTEM}
        assert msgs[1]["role"] == "user"
        user = msgs[1]["content"]
        assert "Page text:" in user
        assert "Alice Bob" in user
        assert "Extraction instruction: list names" in user
        assert "Page:" not in user  # no meta line when page_meta is empty
        assert "Required JSON schema" not in user

    def test_meta_line_included(self):
        msgs = build_messages("go", None, "text", "https://example.com — title")
        assert "Page: https://example.com — title" in msgs[1]["content"]

    def test_schema_appended(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        msgs = build_messages("go", schema, "text", "")
        user = msgs[1]["content"]
        assert "Required JSON schema:" in user
        assert '"name"' in user

    def test_page_text_truncated_at_9000(self):
        long_text = "x" * 12000
        msgs = build_messages("go", None, long_text, "")
        user = msgs[1]["content"]
        assert long_text[:9000] in user
        assert "x" * 9001 not in user

    def test_system_prompt_is_strict_json(self):
        assert "ONLY a JSON value" in EXTRACTION_SYSTEM


class TestParseJsonLoose:
    def test_clean_object(self):
        assert parse_json_loose('{"a": 1}') == {"a": 1}

    def test_clean_array(self):
        assert parse_json_loose("[1, 2, 3]") == [1, 2, 3]

    def test_scalar_values(self):
        assert parse_json_loose('"hello"') == "hello"
        assert parse_json_loose("42") == 42
        assert parse_json_loose("true") is True
        assert parse_json_loose("null") is None

    def test_empty_and_none(self):
        assert parse_json_loose("") is None
        assert parse_json_loose(None) is None
        assert parse_json_loose("   ") is None

    def test_json_fence_with_tag(self):
        assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_fence_without_tag(self):
        assert parse_json_loose("```\n[1, 2]\n```") == [1, 2]

    def test_fence_preferred_over_surrounding_text(self):
        text = 'Some prose {"wrong": true} ```json\n{"right": 1}\n``` trailing'
        assert parse_json_loose(text) == {"right": 1}

    def test_prose_before_json(self):
        assert parse_json_loose('Here you go: {"a": 1}') == {"a": 1}

    def test_trailing_comma_object(self):
        assert parse_json_loose('{"a": 1,}') == {"a": 1}

    def test_trailing_comma_array(self):
        assert parse_json_loose("[1, 2,]") == [1, 2]

    def test_trailing_comma_inside_fence(self):
        assert parse_json_loose('```json\n{"a": 1,}\n```') == {"a": 1}

    def test_raw_decode_fallback_skips_leading_prose(self):
        # Whole stripped text starts with prose, so _try_parse bails on both
        # candidates; raw_decode finds the first "{" and parses from there.
        assert parse_json_loose('result: {"a": 1} done') == {"a": 1}

    def test_garbage_returns_none(self):
        assert parse_json_loose("no json here at all") is None

    def test_unclosed_json_returns_none(self):
        assert parse_json_loose('{"a": 1') is None

    def test_multiple_braces_first_valid_wins(self):
        assert parse_json_loose("xx {oops} yy [1, 2]") == [1, 2]


class TestTryParse:
    def test_empty_candidate(self):
        assert _try_parse("") is None

    def test_bad_first_character(self):
        assert _try_parse("hello world") is None
        assert _try_parse('!{"a": 1}') is None

    def test_valid_json(self):
        assert _try_parse('{"a": 1}') == {"a": 1}

    def test_trailing_comma_cleaned(self):
        assert _try_parse('{"a": 1,}') == {"a": 1}
        assert _try_parse('{"a": [1, 2,],}') == {"a": [1, 2]}

    def test_unfixable_returns_none(self):
        assert _try_parse('{"a": }') is None
        assert _try_parse("{not json}") is None

    def test_leading_whitespace_not_stripped_here(self):
        # _try_parse inspects candidate[0]; callers strip first.
        assert _try_parse('  {"a": 1}') is None
