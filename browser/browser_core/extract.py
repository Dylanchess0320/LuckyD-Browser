"""Structured page extraction — the Stagehand-style ``extract`` primitive.

Given an instruction ("get the product names and prices") and optionally a
JSON schema, the active page's visible text is sent to the configured AI
provider with a strict-JSON system prompt; the reply is parsed leniently
(fences, prose around the JSON, trailing commas) into real Python objects.

Pure Python — the parser is unit-testable without Qt or a live model.
"""

from __future__ import annotations

import json
import re

EXTRACTION_SYSTEM = """\
You are a data-extraction engine inside a web browser. The user gives you the
visible text of a web page and an extraction instruction.

Rules:
- Answer with ONLY a JSON value (object, array, string, number) — no prose,
  no markdown fences, no commentary.
- If a JSON schema is provided, match it exactly (keys, types, nesting).
- Missing data: use null (never invent values).
- Prefer arrays of flat objects for lists of things."""


def build_messages(instruction: str, schema: dict | None, page_text: str, page_meta: str) -> list[dict]:
    """Chat messages for one extraction request."""
    parts = [f"Page: {page_meta}" if page_meta else "", "Page text:", page_text[:9000]]
    user = "\n\n".join(p for p in parts if p)
    if schema:
        user += f"\n\nRequired JSON schema:\n{json.dumps(schema, indent=1)}"
    user += f"\n\nExtraction instruction: {instruction}"
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_loose(text: str):
    """Best-effort JSON extraction from a model reply.

    Handles: clean JSON, ```json fences, prose before/after the JSON, and a
    trailing-comma habit small models have. Returns None when nothing parses.
    """
    if not text:
        return None
    candidates = [text.strip()]
    match = _FENCE.search(text)
    if match:
        candidates.insert(0, match.group(1).strip())
    for candidate in candidates:
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed
    # Last resort: scan for the first { or [ and let raw_decode find the end.
    for start in (i for i, ch in enumerate(text) if ch in "{["):
        try:
            value, _end = json.JSONDecoder().raw_decode(text[start:])
            return value
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _try_parse(candidate: str):
    if not candidate or candidate[0] not in "{[\"-0123456789tfn":
        return None
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        pass
    # Trailing commas before } or ] — the classic small-model slip.
    cleaned = re.sub(r",(\s*[}\]])", r"\1", candidate)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
