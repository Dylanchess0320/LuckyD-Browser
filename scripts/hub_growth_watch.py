#!/usr/bin/env python3
"""Growth-watch → Hub adapter (Hub slice 5).

Consumes the JSON report printed by the cloud-side growth watch
(``luckyd-watch/check.py``: ``{"changes": [...], "current": {...}}``)
and POSTs each real change to the Hub ingest endpoint
(``POST /chat/cards`` on the browser control server), where it becomes
a dismissible card on the Chat page.

Usage:
    python check.py | python scripts/hub_growth_watch.py
    python scripts/hub_growth_watch.py --input report.json
    python scripts/hub_growth_watch.py --check-script ~/workspace/luckyd-watch/check.py

A no-change day is a no-op: exit 0, no POST, no output. The card is a
Chat-page surface, not a chat ping — nothing here messages Dylan.

Delivery needs the browser (and its control server) up on the machine
this runs on; if the Hub is unreachable the run exits 1 with a short
stderr line. Note: check.py advances its own baseline on every run,
so a failed delivery is lost, not retried — re-running the watch after
a failed post will report no change. Keep the browser up when the
watch runs, or run the adapter against a saved report.

Auth: the control server's bearer token, if one is configured, comes
from the LUCKYD_CTL_TOKEN environment variable. It is never printed.
No credentials are ever printed by this script.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HUB = "http://127.0.0.1:9777"


def _load_report(args: argparse.Namespace) -> dict:
    """Read the watch report (check.py JSON) from --check-script, --input,
    or stdin. Raises ValueError on a malformed report."""
    if args.check_script:
        try:
            proc = subprocess.run(
                [sys.executable, args.check_script],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception as exc:
            raise ValueError(f"could not run check script: {exc}") from exc
        raw = proc.stdout
        if proc.returncode != 0:
            raise ValueError(f"check script exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    elif args.input:
        try:
            raw = Path(args.input).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"could not read input: {exc}") from exc
    else:
        raw = sys.stdin.read()
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"report is not valid JSON: {exc}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("changes"), list):
        raise ValueError("report must be an object with a 'changes' list")
    return report


def _post_card(hub: str, token: str, change: dict) -> None:
    """POST one watch change to the Hub ingest endpoint. Raises on failure."""
    payload = json.dumps(change).encode("utf-8")
    req = urllib.request.Request(
        hub.rstrip("/") + "/chat/cards",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = json.loads(exc.read().decode("utf-8", "replace") or "{}").get("error", "")
        raise RuntimeError(f"Hub rejected the change ({exc.code}): {detail or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"could not reach the Hub: {exc}") from exc
    if not isinstance(body, dict) or body.get("state") != "ok":
        raise RuntimeError(
            f"Hub rejected the change: {body.get('error', body) if isinstance(body, dict) else body}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", default=DEFAULT_HUB, help="Hub control-server base URL")
    parser.add_argument(
        "--input", type=str, default=None, help="watch report JSON file (default: stdin)"
    )
    parser.add_argument(
        "--check-script",
        type=str,
        default=None,
        help="run this check.py and use its stdout as the report",
    )
    args = parser.parse_args(argv)
    try:
        report = _load_report(args)
    except ValueError as exc:
        print(f"hub_growth_watch: {exc}", file=sys.stderr)
        return 2

    changes = [c for c in report["changes"] if isinstance(c, dict)]
    if not changes:
        return 0  # no-change day: silence — no POST, no output

    token = os.environ.get("LUCKYD_CTL_TOKEN", "").strip()
    posted = 0
    for change in changes:
        try:
            _post_card(args.hub, token, change)
        except RuntimeError as exc:
            print(f"hub_growth_watch: {exc}", file=sys.stderr)
            return 1
        posted += 1
    print(f"hub_growth_watch: posted {posted} card(s) to the Hub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
