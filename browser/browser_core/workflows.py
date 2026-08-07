"""Record & replay browser workflows — saved automations with self-healing.

A workflow is a named list of steps captured from Control API traffic
(``/navigate`` + ``/act``). Indexed steps (click/type/select) also store a
*fingerprint* of their target element at record time. On replay the live page
is re-snapshotted and each fingerprint is scored against the current elements
— the step lands on the best match even when the page re-rendered and every
index drifted (the "self-healing" trick from Stagehand / browser-use).

Pure Python — no Qt — so matching, validation and storage are unit-testable.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

WORKFLOWS_DIR = DATA_DIR / "workflows"
SCHEMA_VERSION = 1
MAX_STEPS = 500

# Actions that target a page element by snapshot index.
INDEXED_ACTIONS = ("click", "type", "select")
# Everything a workflow step may do.
KNOWN_ACTIONS = (*INDEXED_ACTIONS, "press", "scroll", "navigate", "back", "wait")

# Self-healing threshold: below this similarity the recorded index is used
# as-is (flagged in the replay log) instead of a guessed element.
HEAL_THRESHOLD = 0.55

# Fingerprint of ONE tagged element (record time). Uses the data-ld-agent
# attribute the snapshot JS assigns, so indices always line up.
_FINGERPRINT_JS = (
    "(() => { const e = document.querySelector('[data-ld-agent=\"{i}\"]');"
    " if (!e) return '';"
    " return JSON.stringify({"
    "  tag: e.tagName.toLowerCase(),"
    "  text: ((e.innerText || e.value || e.placeholder ||"
    "    e.getAttribute('aria-label') || e.name || '')"
    "    .replace(/\\s+/g, ' ').trim().slice(0, 80)),"
    "  el_id: e.id || '', name: e.getAttribute('name') || '',"
    "  aria: e.getAttribute('aria-label') || '',"
    "  href: e.href || '' }); })()"
)

# Fingerprints of ALL tagged elements (replay time), each with its index.
_ELEMENTS_JS = (
    "(() => {"
    " const fp = e => ({index: +e.getAttribute('data-ld-agent'),"
    "  tag: e.tagName.toLowerCase(),"
    "  text: ((e.innerText || e.value || e.placeholder ||"
    "    e.getAttribute('aria-label') || e.name || '')"
    "    .replace(/\\s+/g, ' ').trim().slice(0, 80)),"
    "  el_id: e.id || '', name: e.getAttribute('name') || '',"
    "  aria: e.getAttribute('aria-label') || '',"
    "  href: e.href || '' });"
    " return JSON.stringify([...document.querySelectorAll('[data-ld-agent]')]"
    "   .map(fp)); })()"
)


def fingerprint_js(index: int) -> str:
    return _FINGERPRINT_JS.format(i=int(index))


def elements_js() -> str:
    return _ELEMENTS_JS


def slugify(name: str) -> str:
    """Safe file/key name for a workflow."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-.")
    return slug[:60] or "workflow"


def step_record(action: dict, fingerprint: dict | None = None) -> dict | None:
    """Normalize one recorded action into a workflow step (None = skip)."""
    kind = str(action.get("action", "")).strip().lower()
    if kind not in KNOWN_ACTIONS:
        return None
    step: dict = {"action": kind}
    if kind in INDEXED_ACTIONS:
        index = int(action.get("index", -1) or -1)
        if index < 0:
            return None
        step["index"] = index
        if isinstance(fingerprint, dict) and fingerprint.get("tag"):
            step["target"] = fingerprint
    if kind in ("type", "select", "press", "scroll", "wait"):
        step["text"] = str(action.get("text", "") or "")
    if kind == "navigate":
        url = str(action.get("url", "") or "")
        if not url.startswith(("http://", "https://")):
            return None
        step["url"] = url
    return step


def score_fingerprint(saved: dict, candidate: dict) -> float:
    """Similarity of a recorded fingerprint to a live element, 0.0-1.0."""
    score = 0.0
    if saved.get("el_id") and saved["el_id"] == candidate.get("el_id"):
        score += 0.5
    if saved.get("name") and saved["name"] == candidate.get("name"):
        score += 0.35
    if saved.get("aria") and saved["aria"] == candidate.get("aria"):
        score += 0.3
    saved_text = (saved.get("text") or "").strip().lower()
    cand_text = (candidate.get("text") or "").strip().lower()
    if saved_text and cand_text:
        score += 0.4 * difflib.SequenceMatcher(None, saved_text, cand_text).ratio()
    if saved.get("href") and saved["href"] == candidate.get("href"):
        score += 0.2
    if saved.get("tag") == candidate.get("tag"):
        score += 0.15
    return min(score, 1.0)


def resolve_index(
    saved: dict | None,
    candidates: list[dict],
    recorded_index: int,
    threshold: float = HEAL_THRESHOLD,
) -> tuple[int, bool]:
    """Pick the live element index for a recorded step.

    Returns (index, healed): healed=True when the fingerprint matched a
    (possibly different) element; healed=False when we fell back to the
    recorded index untouched.
    """
    if saved:
        best_index, best_score = None, 0.0
        for cand in candidates:
            try:
                score = score_fingerprint(saved, cand)
            except Exception:
                continue
            if score > best_score:
                best_index, best_score = cand.get("index"), score
        if best_index is not None and best_score >= threshold:
            return int(best_index), True
    return int(recorded_index), False


class WorkflowRecorder:
    """Collects steps while recording is on (thread-safe — act/navigate run
    on HTTP handler threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._name: str | None = None
        self._steps: list[dict] = []

    @property
    def active(self) -> bool:
        return self._name is not None

    @property
    def name(self) -> str | None:
        return self._name

    def start(self, name: str) -> str:
        with self._lock:
            self._name = slugify(name)
            self._steps = []
            return self._name

    def add(self, step: dict | None) -> None:
        if step is None:
            return
        with self._lock:
            if self._name is not None and len(self._steps) < MAX_STEPS:
                self._steps.append(step)

    def stop(self) -> tuple[str | None, list[dict]]:
        with self._lock:
            name, self._name = self._name, None
            steps, self._steps = self._steps, []
        return name, steps

    def status(self) -> dict:
        with self._lock:
            return {"recording": self.active, "name": self._name, "steps": len(self._steps)}


class WorkflowStore:
    """JSON-file persistence for workflows (one file per workflow)."""

    def __init__(self, directory: Path | None = None):
        self._dir = Path(directory) if directory is not None else WORKFLOWS_DIR

    def _path(self, name: str) -> Path:
        return self._dir / f"{slugify(name)}.json"

    def list(self) -> list[dict]:
        rows = []
        with contextlib.suppress(Exception):
            for path in sorted(self._dir.glob("*.json")):
                with contextlib.suppress(Exception):
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                    rows.append(
                        {
                            "name": data.get("name", path.stem),
                            "steps": len(data.get("steps", [])),
                            "created": data.get("created", 0),
                        }
                    )
        return rows

    def load(self, name: str) -> dict | None:
        try:
            data = json.loads(self._path(name).read_text(encoding="utf-8-sig"))
        except Exception:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            return None
        return data

    def save(self, name: str, steps: list[dict]) -> str:
        slug = slugify(name)
        payload = {
            "version": SCHEMA_VERSION,
            "name": slug,
            "created": time.time(),
            "steps": steps[:MAX_STEPS],
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(slug).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(self._path(slug))
        return slug

    def delete(self, name: str) -> bool:
        try:
            self._path(name).unlink(missing_ok=True)
            return True
        except Exception:
            return False
