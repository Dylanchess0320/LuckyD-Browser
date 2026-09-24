"""LuckyD Agents Bridge â€” the browser's terminal + agent mesh + free-model

switching, served standalone (no Qt, no browser window).



The LuckyD Browser's Agent Mesh terminals live in ``browser_core``:

``terminal_server`` (WebSocket -> ConPTY bridge, stdlib + websockets + winpty)

and ``terminal_page`` (the xterm.js UI pages). Both import Qt-free, so this

bridge reuses them verbatim instead of reimplementing any of it:



  * GET  /                    -> mesh_html(): four live terminal panes

                                 (Agent 1 / Agent 2 / PowerShell / CMD)

  * GET  /terminal?shell=X    -> terminal_html(): one xterm tab; X is any

                                 allowlisted shell â€” agent, agent2,

                                 powershell, cmd, or a mesh-* agent CLI

                                 (mesh-claude, mesh-codex, mesh-muse, ...)

  * GET  /static/terminal/*   -> vendored xterm.js assets

  * GET  /api/ping            -> health + which mesh shells are installed

  * GET  /api/catalog         -> free-model catalog (providers_config.json)

  * GET  /api/model           -> current provider/model pick
  * GET  /api/providers       -> health snapshot (list_providers + live pair)
  * GET  /api/best-free        -> best_free_provider() as a concrete pair

  * POST /api/model           -> switch provider/model (writes the same

                                 browser/data/settings.json the browser's

                                 AI sidebar writes; luckyd-code.exe mirrors

                                 it per request, no backend rebuild needed)



Auth model (same as the browser): a random per-boot token is handed to the

UI as an HttpOnly cookie on every HTML response; the WebSocket bridge is

fail-closed without it. Loopback bind only. CORS echoes only the app's own

origins, so foreign web pages can neither read responses nor preflight

JSON POSTs.



Usage:

  python luckyd_agents_bridge.py [--root DIR] [--http-port N] [--ws-port N]

  luckyd-agents-bridge.exe       (PyInstaller build; same args)



--root is the backend root: <root>/browser/{assets/terminal,models,data}

holds the xterm assets, the model catalog and settings.json. In dev this

defaults to the LuckyD-Browser repo root; the packaged app passes

``--root resources/backend``.

"""

from __future__ import annotations

import argparse
import contextlib
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_HTTP_PORT = 9885

DEFAULT_WS_PORT = 9886


# Origins the UI may come from: the packaged renderer (file:// => "null"

# Origin) and the Vite dev server. Anything else gets no CORS echo and no

# preflight answer.

_ALLOWED_ORIGINS = {
    "null",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}


def _default_root() -> Path:
    """Dev: the LuckyD-Browser repo root (this file lives in apps/luckyd-ui/scripts)."""

    return Path(__file__).resolve().parents[2]


def _import_browser_core(root: Path) -> bool:
    """Put <root>/browser on sys.path so ``import browser_core`` works.



    Frozen builds bundle the modules in the exe and skip this entirely.

    """

    if getattr(sys, "frozen", False):
        return True

    browser_dir = root / "browser"

    if not browser_dir.is_dir():
        return False

    if str(browser_dir) not in sys.path:
        sys.path.insert(0, str(browser_dir))

    return True


class Bridge:
    """One token, one HTTP server, one WebSocket terminal server."""

    def __init__(self, root: Path, http_port: int, ws_port: int) -> None:

        self.root = root

        self.http_port = http_port

        self.ws_port = ws_port

        self.token = secrets.token_urlsafe(32)

        # Frozen: the xterm assets + model catalog are bundled inside the exe

        # (PyInstaller datas, unpacked to sys._MEIPASS); settings.json stays

        # real data under --root so switches persist next to luckyd-code.exe.

        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

            self.assets_dir = base / "browser" / "assets" / "terminal"

            self.catalog_path = base / "browser" / "models" / "providers_config.json"

        else:
            self.assets_dir = root / "browser" / "assets" / "terminal"

            self.catalog_path = root / "browser" / "models" / "providers_config.json"

        self.settings_path = root / "browser" / "data" / "settings.json"

        self.terminals = None  # TerminalServer, started in start_terminals()

    # ── terminal server ─────────────────────────────────────────────────

    def start_terminals(self) -> bool:

        try:
            from browser_core.terminal_server import TerminalServer

        except ImportError as exc:
            print(f"[bridge] terminal_server unavailable: {exc}", flush=True)

            return False

        self.terminals = TerminalServer(host="127.0.0.1", port=self.ws_port, token=self.token)

        ok = self.terminals.start()

        if ok:
            print(f"[bridge] terminal WS on ws://127.0.0.1:{self.ws_port}", flush=True)

        else:
            print("[bridge] terminal WS failed to bind (websockets missing?)", flush=True)

        return ok

    # ── model catalog / settings ────────────────────────────────────────

    def read_catalog(self) -> dict:

        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8-sig"))

        except Exception:
            return {"ai_providers": {}}

        providers = data.get("ai_providers", {}) if isinstance(data, dict) else {}

        # Only surface what the UI needs: free models + name + env var.

        out = {}

        for pid, p in providers.items():
            if not isinstance(p, dict):
                continue

            out[pid] = {
                "name": p.get("name", pid),
                "base_url": p.get("base_url", ""),
                "env_key": p.get("env_key", ""),
                "free_models": p.get("free_models", []),
            }

        return {"ai_providers": out}

    def reload_settings(self) -> dict:
        """Live re-read of browser/data/settings.json (10.5 instant apply).

        The settings file is the single source of truth shared with the
        browser's AI sidebar; every read goes to disk so a POST /api/model
        (or an external sidebar edit) is visible on the very next GET with
        no bridge restart. Never raises — {} when unreadable.
        """

        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))

        except Exception:
            return {}

        return data if isinstance(data, dict) else {}

    def read_current_model(self) -> dict:

        data = self.reload_settings()

        provider = str(data.get("ai_provider", "") or "auto")

        overrides = data.get("ai_model_overrides", {})

        model = overrides.get(provider, "") if isinstance(overrides, dict) else ""

        return {
            "provider": provider,
            "model": model,
            "overrides": overrides if isinstance(overrides, dict) else {},
            "settings_path": str(self.settings_path),
        }

    def set_model(self, provider: str, model: str) -> dict:

        provider = (provider or "").strip().lower()

        model = (model or "").strip()

        if not provider or not model:
            raise ValueError("provider and model are both required")

        data = self.reload_settings()

        overrides = data.get("ai_model_overrides")

        if not isinstance(overrides, dict):
            overrides = {}

        overrides[provider] = model

        data["ai_provider"] = provider

        data["ai_model_overrides"] = overrides

        self.settings_path.parent.mkdir(parents=True, exist_ok=True)

        # BOM, like the browser's SettingsStore â€” providers.py strips it.

        self.settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8-sig")

        # 10.5 instant apply: re-read from disk so the POST response (and
        # every later GET) reflects exactly what is on disk — no restart.
        return self.read_current_model()

    # ── provider health snapshot (10.5) ─────────────────────────────────

    def _ensure_core_import(self) -> bool:
        """Make ``core.*`` importable (repo root on sys.path); False if absent.

        The frozen bridge bundles only browser_core, so core/providers.py may
        legitimately be missing — callers degrade to available: False.
        """

        import importlib.util

        try:
            if importlib.util.find_spec("core.providers") is not None:
                return True
            if str(self.root) not in sys.path:
                sys.path.insert(0, str(self.root))
            return importlib.util.find_spec("core.providers") is not None
        except Exception:
            return False

    def read_providers(self) -> dict:
        """Health snapshot — the same ``list_providers()`` HQ uses (10.5).

        One source of truth for "what would work right now": per-provider
        rotation order, next-in-rotation, Cline 402 TTL, and last-working
        info, plus the live answering pair.
        """

        if not self._ensure_core_import():
            return {"providers": [], "available": False}

        try:
            from core.free_rotation import get_active_pair
            from core.providers import list_providers

            live = get_active_pair()

            return {
                "providers": list_providers(),
                "active": ({"provider": live[0], "model": live[1]} if live else None),
                "available": True,
            }
        except Exception as exc:
            return {
                "providers": [],
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def read_best_free(self) -> dict:
        """The ``best_free_provider()`` pick as a concrete pair (10.5).

        Powers the Models view's one-click "Switch to best working" fix.
        """

        if not self._ensure_core_import():
            return {"provider": "", "model": "", "available": False}

        try:
            from core.free_rotation import FREE_MODEL_PRIORITY, best_free_provider
            from core.last_working import last_working_age_label

            best = best_free_provider()
            if not best:
                return {"provider": "", "model": "", "available": False}

            model = ""
            for pid, mid in FREE_MODEL_PRIORITY:
                if pid == best:
                    model = mid
                    break
            if not model:
                from core.providers import PROVIDER_DEFAULTS

                model = str((PROVIDER_DEFAULTS.get(best) or {}).get("default_model", ""))

            return {
                "provider": best,
                "model": model,
                "available": True,
                "last_working_ago": last_working_age_label(),
            }
        except Exception as exc:
            return {
                "provider": "",
                "model": "",
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }


def make_handler(bridge: Bridge):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        server_version = "LuckyDAgentsBridge/1.0"

        def log_message(self, fmt, *args):  # keep the console quiet

            pass

        def _origin(self) -> str:

            return self.headers.get("Origin", "") or ""

        def _allowed_origin(self) -> str | None:
            """Echo the Origin only when it belongs to the app/its pages."""

            origin = self._origin()

            if not origin:
                return None  # same-origin GET â€” no CORS headers needed

            if origin in _ALLOWED_ORIGINS:
                return origin

            mine = f"http://127.0.0.1:{bridge.http_port}"

            if origin == mine:
                return origin

            return None

        def _cors(self) -> None:

            origin = self._allowed_origin()

            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)

                self.send_header("Vary", "Origin")

                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

                self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send(self, code: int, body: bytes, ctype: str, cookie: bool = False) -> None:

            self.send_response(code)

            self.send_header("Content-Type", ctype)

            self.send_header("Content-Length", str(len(body)))

            self.send_header("Cache-Control", "no-store")

            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"luckyd_term={bridge.token}; Path=/; SameSite=Strict; HttpOnly",
                )

            self._cors()

            self.end_headers()

            self.wfile.write(body)

        def _send_json(self, obj, code: int = 200) -> None:

            body = json.dumps(obj, indent=2).encode()

            self._send(code, body, "application/json; charset=utf-8")

        def _send_error_json(self, code: int, msg: str) -> None:

            self._send_json({"error": msg}, code=code)

        def do_OPTIONS(self) -> None:  # preflight: only app origins answer

            if self._allowed_origin():
                self.send_response(204)

                self.send_header("Content-Length", "0")

                self._cors()

                self.end_headers()

            else:
                self.send_response(403)

                self.send_header("Content-Length", "0")

                self.end_headers()

        def do_GET(self) -> None:

            path = self.path.split("?", 1)[0]

            if path == "/api/ping":
                shells = {}

                with contextlib.suppress(Exception):
                    from browser_core import terminal_server as ts

                    shells = ts.mesh_shells_available()

                self._send_json({"ok": True, "ws_port": bridge.ws_port, "shells": shells})

                return

            if path == "/api/catalog":
                self._send_json(bridge.read_catalog())

                return

            if path == "/api/model":
                self._send_json(bridge.read_current_model())

                return

            if path == "/api/providers":
                self._send_json(bridge.read_providers())

                return

            if path == "/api/best-free":
                self._send_json(bridge.read_best_free())

                return

            if path == "/":
                try:
                    from browser_core.terminal_page import mesh_html

                    self._send(
                        200,
                        mesh_html().encode("utf-8"),
                        "text/html; charset=utf-8",
                        cookie=True,
                    )

                except ImportError:
                    self._send_error_json(500, "terminal_page unavailable")

                return

            if path == "/terminal":
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)

                shell = (q.get("shell", ["agent"])[0] or "agent").strip().lower()

                try:
                    from browser_core.terminal_page import terminal_html

                    html = terminal_html({"terminal_port": bridge.ws_port}, shell)

                except ImportError:
                    self._send_error_json(500, "terminal_page unavailable")

                    return

                except ValueError:
                    self._send_error_json(400, f"unknown shell '{shell}'")

                    return

                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", cookie=True)

                return

            if path.startswith("/static/terminal/"):
                name = path[len("/static/terminal/") :]

                safe = Path(name).name  # no traversal

                f = bridge.assets_dir / safe

                if not f.is_file():
                    self._send_error_json(404, f"no such asset {safe}")

                    return

                if safe.endswith(".css"):
                    ctype = "text/css; charset=utf-8"

                elif safe.endswith(".js"):
                    ctype = "application/javascript; charset=utf-8"

                elif safe.endswith(".woff2"):
                    ctype = "font/woff2"

                else:
                    ctype = "application/octet-stream"

                self._send(200, f.read_bytes(), ctype, cookie=True)

                return

            self._send_error_json(404, "not found")

        def do_POST(self) -> None:

            path = self.path.split("?", 1)[0]

            if path != "/api/model":
                self._send_error_json(404, "not found")

                return

            # Preflight already gates browsers; belt-and-braces the Origin.

            if self._origin() and not self._allowed_origin():
                self._send_error_json(403, "origin not allowed")

                return

            try:
                length = int(self.headers.get("Content-Length", "0") or 0)

            except ValueError:
                length = 0

            if length <= 0 or length > 64 * 1024:
                self._send_error_json(400, "bad body")

                return

            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))

            except Exception:
                self._send_error_json(400, "invalid JSON")

                return

            if not isinstance(body, dict):
                self._send_error_json(400, "invalid JSON")

                return

            try:
                result = bridge.set_model(str(body.get("provider", "")), str(body.get("model", "")))

            except ValueError as exc:
                self._send_error_json(400, str(exc))

                return

            self._send_json({"ok": True, **result})

    return Handler


def main() -> int:

    parser = argparse.ArgumentParser(description="LuckyD agents bridge")

    parser.add_argument(
        "--root",
        default=str(_default_root()),
        help="backend root (holds browser/assets|models|data)",
    )

    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)

    parser.add_argument("--ws-port", type=int, default=DEFAULT_WS_PORT)

    args = parser.parse_args()

    root = Path(args.root).resolve()

    if not _import_browser_core(root):
        print(f"[bridge] no browser/ package under {root} â€” terminal pages off", flush=True)

    bridge = Bridge(root, args.http_port, args.ws_port)

    bridge.start_terminals()

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(bridge))

    except OSError as exc:
        print(f"[bridge] HTTP bind failed on {args.http_port}: {exc}", flush=True)

        return 1

    print(f"[bridge] UI on http://127.0.0.1:{args.http_port}  (root={root})", flush=True)

    try:
        httpd.serve_forever()

    except KeyboardInterrupt:
        pass

    finally:
        with contextlib.suppress(Exception):
            if bridge.terminals is not None:
                bridge.terminals.stop()

        httpd.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
