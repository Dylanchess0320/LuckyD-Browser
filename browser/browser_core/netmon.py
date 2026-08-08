"""Network monitor — a live request log of the active tab over raw CDP.

Opens a dedicated websocket to the page target (same trick as screenshot.py
and cdp_driver.py), enables the Network domain, and reduces the event stream
into flat request rows. A daemon thread owns the connection; the Control API
serves rows as JSON to the /network page. HAR 1.2 export included.

The reducer (`_handle`) is pure — unit-testable without any browser.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time

import httpx
from browser_core.screenshot import CDP_HTTP, _find_target

MAX_ROWS = 500


class NetMonitor:
    """Collects Network-domain events for one page target."""

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.target = ""
        self.error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url_substr: str = "") -> None:
        self.stop()  # replace any previous session
        self._stop.clear()
        self.error = ""
        self._thread = threading.Thread(
            target=self._run, args=(url_substr,), name="netmon", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._by_id.clear()

    def rows(self, since: int = 0) -> dict:
        with self._lock:
            rows = [r for r in self._rows if r["seq"] > since]
            seq = self._seq
        return {
            "running": self.running,
            "target": self.target,
            "error": self.error,
            "seq": seq,
            "rows": rows,
        }

    def _run(self, url_substr: str) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.error = "websockets not installed"
            return
        try:
            targets = httpx.get(CDP_HTTP + "/json", timeout=3.0).json()
            target = _find_target(targets, url_substr)
            if target is None:
                self.error = "no page target found"
                return
            self.target = target.get("url", "")
            with connect(target["webSocketDebuggerUrl"], open_timeout=10.0) as ws:
                ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
                while not self._stop.is_set():
                    try:
                        raw = ws.recv(timeout=0.5)
                    except TimeoutError:
                        continue
                    with contextlib.suppress(Exception):
                        self._handle(json.loads(raw))
        except Exception as exc:
            self.error = str(exc) or repr(exc)

    # ── the reducer (pure — unit tests feed it dicts) ─────────────────

    def _handle(self, msg: dict) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}
        if method == "Network.requestWillBeSent":
            rid = str(params.get("requestId", ""))
            req = params.get("request", {}) or {}
            with self._lock:
                self._seq += 1
                row = {
                    "seq": self._seq,
                    "id": rid,
                    "method": req.get("method", "GET"),
                    "url": req.get("url", ""),
                    "type": params.get("type", "") or "",
                    "status": 0,
                    "size": 0,
                    "ms": 0,
                    "started": float(params.get("timestamp", time.time())),
                }
                self._by_id[rid] = row
                self._rows.append(row)
                if len(self._rows) > MAX_ROWS:  # drop oldest, keep the map honest
                    for stale in self._rows[: len(self._rows) - MAX_ROWS]:
                        self._by_id.pop(stale["id"], None)
                    del self._rows[: len(self._rows) - MAX_ROWS]
        elif method == "Network.responseReceived":
            row = self._by_id.get(str(params.get("requestId", "")))
            if row is not None:
                resp = params.get("response", {}) or {}
                row["status"] = int(resp.get("status", 0) or 0)
                if params.get("type"):
                    row["type"] = str(params["type"])
        elif method == "Network.loadingFinished":
            row = self._by_id.get(str(params.get("requestId", "")))
            if row is not None:
                row["size"] = int(params.get("encodedDataLength", 0) or 0)
                ended = float(params.get("timestamp", 0) or 0)
                if ended and row["started"]:
                    row["ms"] = max(0, int((ended - row["started"]) * 1000))
        elif method == "Network.loadingFailed":
            row = self._by_id.get(str(params.get("requestId", "")))
            if row is not None and not row["status"]:
                row["status"] = -1


def to_har(rows: list[dict], page_url: str = "") -> dict:
    """Minimal HAR 1.2 document from collected rows."""
    entries = []
    for row in rows:
        started = time.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(row.get("started") or time.time())
        )
        entries.append(
            {
                "startedDateTime": started,
                "time": row.get("ms", 0),
                "request": {"method": row.get("method", "GET"), "url": row.get("url", "")},
                "response": {
                    "status": max(row.get("status", 0), 0),
                    "content": {"size": row.get("size", 0)},
                },
            }
        )
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "LuckyD Browser NetMonitor", "version": "1.0"},
            "pages": [{"title": page_url, "id": "page_1"}] if page_url else [],
            "entries": entries,
        }
    }
