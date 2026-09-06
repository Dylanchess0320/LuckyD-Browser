"""Passage + quote extraction from fetched source documents.

Splits cleaned page text into passages, scores them for relevance to the
research question, and returns the best ones. This is what makes evidence
"backed by real text" rather than just a URL.
"""

from __future__ import annotations

import re

from ..schemas import Passage, SourceDocument

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")


def _split_passages(text: str, target_len: int = 600, max_len: int = 1200) -> list[str]:
    """Chunk text into ~target_len char passages without breaking sentences."""
    text = _WHITESPACE.sub(" ", text).strip()
    sentences = [s for s in _SENTENCE_END.split(text) if len(s) > 25]
    passages: list[str] = []
    buf = ""
    for s in sentences:
        candidate = (buf + " " + s).strip()
        if len(candidate) > max_len:
            if buf:
                passages.append(buf)
            buf = s if len(s) <= max_len else s[:max_len]
        elif len(candidate) >= target_len:
            passages.append(candidate)
            buf = ""
        else:
            buf = candidate
    if buf:
        passages.append(buf)
    return passages


def _score_passage(passage: str, question: str, focus_terms: list[str]) -> float:
    """Heuristic relevance score in [0, 1] based on term overlap + density."""
    p = passage.lower()
    q_terms = {w for w in re.findall(r"[a-z0-9]{3,}", question.lower())}
    f_terms = {w for w in (t.lower() for t in focus_terms) if len(w) >= 3}
    wanted = q_terms | f_terms
    if not wanted:
        return 0.3
    hits = sum(1 for t in wanted if t in p)
    density = hits / max(len(passage.split()), 1)
    # length-normalized overlap, bounded
    score = min(1.0, 0.25 * (hits / max(len(wanted), 1)) + 4.0 * density)
    return max(0.0, min(1.0, score))


def extract_relevant_passages(
    doc: SourceDocument,
    question: str,
    focus_terms: list[str] | None = None,
    top_k: int = 3,
    min_score: float = 0.15,
) -> list[Passage]:
    """Return the most relevant passages from a document as Passage objects."""
    if not doc.fetch_ok or not doc.text:
        return []
    focus_terms = focus_terms or []
    passages = _split_passages(doc.text)
    scored: list[tuple[float, int, str]] = []
    offset = 0
    for p in passages:
        s = _score_passage(p, question, focus_terms)
        if s >= min_score:
            scored.append((s, offset, p))
        offset += len(p)
    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    out: list[Passage] = []
    for s, off, p in scored[:top_k]:
        key = p[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(Passage(url=doc.url, quote=p, char_offset=off, score=s))
    return out
