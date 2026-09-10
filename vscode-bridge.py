#!/usr/bin/env python3
"""LuckyD Code — VSCode bridge (thin shim over bridge.py).

Historically this was a full copy of bridge.py differing by a single
string; it now reuses bridge.main() and only overrides the ready message.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(AGENT_DIR))

import bridge

bridge.READY_MESSAGE = "LuckyD Code VSCode bridge ready"

if __name__ == "__main__":
    asyncio.run(bridge.main())
