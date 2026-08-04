"""LuckyD Harness Bridge — connects the browser to the luckyd-code.exe backend.

The exe (`luckyd-code.exe --web --port 8000`) exposes a FastAPI server with
98 tools, a memory graph, LSP, sessions, orchestration and background tasks.
This client lets the browser's AI sidebar route agent tasks through that
backend while the browser keeps using its own LLM providers (Kimi, Claude,
local Ollama, …) for chat and tab-driving.

Discovery: host/port honor LUCKYDHOST / LUCKYDPORT (the same env vars the
CLI's tools/harness_tool.py uses). The exe path honors LUCKYD_EXE, then
falls back to the repo root and to the folder of a frozen browser exe.

Robustness notes:
  * Every HTTP call builds a FRESH httpx.AsyncClient. A long-lived client
    binds to whichever asyncio event loop created it, and this bridge is
    used from several loops (the sidebar's harness worker, the app's boot
    thread, the /hq splash handler…) — reusing one across loops crashes
    the second task with "Event loop is closed".
  * Process spawn is guarded by a threading lock so two workers can never
    start duplicate exes.
  * HarnessSupervisor wraps a shared bridge for the whole app: health
    probes, cached status for the UI, and background (non-blocking) start.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

_HOST = os.environ.get("LUCKYDHOST", "127.0.0.1")
_PORT = int(os.environ.get("LUCKYDPORT", "8000") or 8000)

# One exe per machine — guards Popen across threads/workers.
_SPAWN_LOCK = threading.Lock()


def _find_exe() -> Path | None:
    """Locate the harness backend: env override, frozen exe, then live-source launcher.

    The frozen ``luckyd-code.exe`` is preferred: it is fully self-contained (no
    Python needed on the machine) and, as of this build, embeds the FIXED
    ``core/llm_client.py`` — the ``Illegal header value b'Bearer '`` crash is
    gone. The live-source launcher (``luckyd-harness.py`` → ``web_server.py``)
    remains as a developer fallback for running against a live checkout.
    """
    candidates = []
    override = os.environ.get("LUCKYD_EXE", "").strip()
    if override:
        candidates.append(Path(override))
    # Repo root: browser/browser_core/harness_bridge.py → ../../..
    repo_root = Path(__file__).resolve().parent.parent.parent
    # Prefer the self-contained frozen exe; fall back to the live-source launcher.
    candidates.append(repo_root / "luckyd-code.exe")
    candidates.append(repo_root / "luckyd-harness.py")
    if getattr(sys, "frozen", False):
        # PyInstaller extracts to _internal/ beside the exe.
        base_dir = Path(sys.executable).resolve().parent
        candidates.append(base_dir / "luckyd-code.exe")
        candidates.append(base_dir / "luckyd-harness.py")
        candidates.append(base_dir / "_internal" / "luckyd-code.exe")
        candidates.append(base_dir / "_internal" / "luckyd-harness.py")
    for path in candidates:
        if path.exists():
            return path
    return None


class HarnessBridge:
    """Async client for the luckyd-code.exe harness server."""

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or _HOST
        self.port = int(port or _PORT)
        self.base = f"http://{self.host}:{self.port}"
        self._harness_proc: subprocess.Popen | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ── lifecycle ─────────────────────────────────────────────────────
    async def connect(self, timeout: float = 10.0) -> bool:
        """Check whether the harness is reachable; True when it answers /health."""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self.base}/health", timeout=min(3.0, timeout))
                r.raise_for_status()
            self._connected = True
        except Exception:
            self._connected = False
        return self._connected

    async def start(self, wait: bool = True, timeout: float = 20.0) -> bool:
        """Launch the harness exe if it isn't already serving. True when up."""
        if await self.connect(timeout=3.0):
            return True
        exe = _find_exe()
        if exe is None:
            raise FileNotFoundError(
                "harness backend not found — set LUCKYD_EXE or start it "
                "manually: python web_server.py --web --port 8000"
            )
        with _SPAWN_LOCK:
            # Re-check inside the lock: another worker may have started it.
            if await self.connect(timeout=1.5):
                return True
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            # A .py launcher must be run through the Python interpreter; a
            # frozen .exe runs directly.
            cmd = [sys.executable, str(exe)] if exe.suffix.lower() == ".py" else [str(exe)]
            self._harness_proc = subprocess.Popen(
                [*cmd, "--web", "--port", str(self.port), "--host", self.host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        if not wait:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._harness_proc.poll() is not None:
                break  # died on startup (bad args, missing key, …)
            if await self.connect(timeout=2.0):
                return True
            await asyncio.sleep(0.5)
        return await self.connect(timeout=2.0)

    async def stop(self) -> None:
        """Terminate the exe only if THIS bridge started it."""
        if self._harness_proc is not None:
            self._harness_proc.terminate()
            try:
                self._harness_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._harness_proc.kill()
            self._harness_proc = None
        self._connected = False

    async def close(self) -> None:
        await self.stop()

    # ── low-level helpers ─────────────────────────────────────────────
    async def _get(self, path: str, timeout: float = 10.0):
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base}{path}", timeout=timeout)
            r.raise_for_status()
            if "application/json" in r.headers.get("content-type", ""):
                return r.json()
            return r.text

    async def _post(self, path: str, body: dict | None = None, timeout: float = 30.0):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base}{path}",
                json=body or {},
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            r.raise_for_status()
            if "application/json" in r.headers.get("content-type", ""):
                return r.json()
            return r.text

    # ── capabilities ──────────────────────────────────────────────────
    async def list_tools(self) -> list:
        data = await self._get("/api/tools")
        if isinstance(data, dict):
            return data.get("tools", data.get("data", []))
        return data if isinstance(data, list) else []

    async def list_models(self):
        return await self._get("/api/models")

    async def list_files(self):
        return await self._get("/api/files")

    async def read_file(self, path: str) -> str:
        data = await self._post("/api/read-file", {"path": path})
        if isinstance(data, dict):
            return data.get("content", json.dumps(data))
        return str(data)

    async def write_file(self, path: str, content: str):
        return await self._post("/api/write-file", {"path": path, "content": content})

    async def search_memory(self, query: str):
        try:
            return await self._get(f"/api/brain/search?q={query}")
        except Exception:
            return await self._post("/api/brain/search", {"query": query})

    async def get_memory_stats(self):
        try:
            return await self._get("/api/brain/stats")
        except Exception:
            return await self._get("/api/brain")

    async def list_tasks(self):
        return await self._get("/api/tasks")

    async def get_settings(self):
        return await self._get("/api/settings")

    async def get_cost(self):
        return await self._get("/api/cost")

    # ── agent execution ───────────────────────────────────────────────
    async def orchestrate(self, task: str, mode: str = "smart"):
        """Blocking researcher→coder→reviewer pipeline."""
        return await self._post("/api/orchestrate", {"task": task, "mode": mode}, timeout=300.0)

    async def parallel(self, task: str):
        return await self._post("/api/parallel", {"task": task}, timeout=300.0)

    async def start_background(self, task: str) -> str:
        """Kick off a background task; returns its task_id."""
        data = await self._post("/api/background/start", {"task": task}, timeout=30.0)
        if isinstance(data, dict):
            task_id = data.get("task_id") or data.get("id")
            if task_id:
                return str(task_id)
        raise RuntimeError(f"unexpected background-start response: {data!r}")

    async def background_status(self, task_id: str) -> dict:
        data = await self._get(f"/api/background/status/{task_id}", timeout=10.0)
        return data if isinstance(data, dict) else {}

    async def background_result(self, task_id: str):
        return await self._get(f"/api/background/result/{task_id}", timeout=30.0)

    async def list_background(self) -> list:
        """All background tasks the harness knows about (the registry)."""
        data = await self._get("/api/background", timeout=10.0)
        if isinstance(data, dict):
            return data.get("tasks", [])
        return data if isinstance(data, list) else []

    async def find_background_task(self, task_id: str) -> dict | None:
        """Look up a task in the registry — a fallback for when the direct
        /status route 404s (e.g. a harness restart wiped its in-memory dict)."""
        try:
            for task in await self.list_background():
                if isinstance(task, dict) and task.get("id") == task_id:
                    return task
        except Exception:
            pass
        return None

    # ── introspection ─────────────────────────────────────────────────
    async def get_endpoints(self) -> list[str]:
        spec = await self._get("/openapi.json")
        eps = []
        for path, methods in spec.get("paths", {}).items():
            for method in methods:
                eps.append(f"{method.upper()} {path}")
        return sorted(eps)


class HarnessSupervisor:
    """App-wide owner of the harness backend: one shared bridge, cached
    status for the UI, and thread-safe background start.

    The browser uses a single supervisor (created by BrowserApp) so the
    sidebar, the /hq gateway and the dashboard all see the same state.
    Every method is safe to call from any thread; none touch Qt.
    """

    def __init__(self, bridge: HarnessBridge | None = None):
        self.bridge = bridge or HarnessBridge()
        self.last: dict = {
            "up": False,
            "starting": False,
            "error": None,
            "tools": None,
        }
        self._start_lock = threading.Lock()

    @property
    def url(self) -> str:
        return self.bridge.base

    # ── probing (sync, fast, any thread) ──────────────────────────────
    def probe(self, timeout: float = 1.2) -> dict:
        """Quick /health probe; refreshes the cached status and returns it."""
        up = False
        try:
            r = httpx.get(f"{self.url}/health", timeout=timeout)
            up = r.status_code == 200
        except Exception:
            up = False
        self.last["up"] = up
        if not up:
            self.last["tools"] = None
        return self.status()

    def status(self) -> dict:
        """Last known state (no network) plus the harness base URL."""
        return dict(self.last, url=self.url)

    # ── starting ──────────────────────────────────────────────────────
    def start_blocking(self, timeout: float = 25.0) -> tuple[bool, str]:
        """Start the exe and wait for /health. Returns (ok, error_message).

        Runs its own asyncio loop — call from a worker thread, never the
        GUI thread.
        """
        with self._start_lock:
            self.last["starting"] = True
            self.last["error"] = None
            try:
                ok = asyncio.run(self.bridge.start(timeout=timeout))
                if ok:
                    self.last["up"] = True
                    try:
                        tools = asyncio.run(self.bridge.list_tools())
                        self.last["tools"] = len(tools) if tools else None
                    except Exception:
                        self.last["tools"] = None
                else:
                    self.last["up"] = False
                    self.last["error"] = (
                        "the harness process started but did not answer "
                        f"/health on {self.url} within {int(timeout)}s"
                    )
                return ok, (self.last["error"] or "")
            except FileNotFoundError as exc:
                self.last["up"] = False
                self.last["error"] = str(exc)
                return False, str(exc)
            except Exception as exc:  # never let the boot thread die silently
                self.last["up"] = False
                self.last["error"] = f"{type(exc).__name__}: {exc}"
                return False, self.last["error"]
            finally:
                self.last["starting"] = False

    def ensure_started(self, timeout: float = 25.0, force: bool = False) -> None:
        """Kick off a background start (idempotent — returns immediately).

        ``force=True`` skips the health probe and clears the last error —
        used by the /hq gateway's manual Retry link.
        """
        if self.last.get("starting"):
            return
        if force:
            self.last["error"] = None
        elif self.probe()["up"]:
            return
        # Set synchronously so concurrent callers don't spawn duplicate
        # boot threads before the worker gets scheduled.
        self.last["starting"] = True

        def _work() -> None:
            self.start_blocking(timeout=timeout)

        threading.Thread(target=_work, name="harness-boot", daemon=True).start()
