"""In-browser terminal — WebSocket ↔ PTY bridge for the LuckyD Code CLI.

Serves a real interactive ``luckyd-cli`` terminal to a browser tab:

    [ xterm.js tab ] ──ws──▶ [ this bridge ] ──ConPTY──▶ [ luckyd-cli CLI ]

The bridge runs a small ``websockets.sync.server`` in a daemon thread. Each
connection spawns the CLI on a Windows ConPTY (via ``pywinpty``) so the
agent's Rich prompt, ANSI colours and line editing behave exactly like a real
console. Output flows PTY→client in one thread; keystrokes flow client→PTY in
another. Terminal resizes are forwarded so the CLI reflows correctly.

Security model: binds to 127.0.0.1 ONLY (same trust model as the CDP port 9222
and the Browser Control API). The terminal has full power over the machine
through the agent's shell tools, so it must never be exposed off-loopback.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9881

# Repo root: browser/browser_core/terminal_server.py → ../../..
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_COLS, _ROWS = 120, 30


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path.strip())))


def _desktop_exe() -> Path | None:
    """Locate the interactive ``luckyd-cli.exe`` on the user's real Desktop.

    Uses the Windows Known Folder API (CSIDL_DESKTOPDIRECTORY) so it works even
    when the Desktop is OneDrive-redirected (e.g. ``…\\OneDrive\\Desktop``),
    falling back to the common plain/OneDrive locations.

    NOTE: looks for ``luckyd-cli.exe`` (built from ``main.py`` via
    ``main.spec``), NOT ``luckyd-code.exe`` (built from ``web_server.py`` via
    ``luckyd-code.spec``). The latter is a headless HTTP server with no stdin
    loop -- spawning it on this ConPTY just prints a server banner and hangs
    with no prompt to type into. Only luckyd-cli.exe is a real interactive
    REPL (rich UI, /help, /tools, /model, and every registered tool
    including the multi-agent "mesh" tools in tools/agent_orchestration.py
    and tools/subagent_tool.py).
    """
    candidates: list[Path] = []
    try:  # canonical Desktop path, redirection-aware
        import ctypes

        buf = ctypes.create_unicode_buffer(520)
        # SHGetFolderPathW(hwnd, csidl=0x0010 DESKTOPDIRECTORY, token, flags, path)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
            candidates.append(Path(buf.value) / "luckyd-cli.exe")
    except Exception:
        pass
    home = Path.home()
    one_drive = os.environ.get("ONEDRIVE", "").strip()  # Windows env names are case-insensitive
    candidates += [
        home / "Desktop" / "luckyd-cli.exe",
        home / "OneDrive" / "Desktop" / "luckyd-cli.exe",
    ]
    if one_drive:
        candidates.append(Path(one_drive) / "Desktop" / "luckyd-cli.exe")
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def _python_for_scripts() -> str | None:
    """Interpreter for ``.py`` CLI overrides.

    Normally the current interpreter; when frozen (LuckyDBrowser.exe) there IS
    no embedded interpreter usable for scripts, so look for Python on PATH.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    import shutil

    return shutil.which("python") or shutil.which("py")


def _cli_command(cli_path: str = "") -> list[str]:
    """How to launch the interactive LuckyD Code CLI (no args → prompt loop).

    Resolution order (first existing wins):
      1. ``cli_path`` argument — the browser's ``terminal_cli`` setting.
      2. ``LUCKYD_CLI`` env var (a ``.py`` runs under a real interpreter).
      3. The standalone ``luckyd-cli.exe`` on the user's Desktop (their build).
      4. ``luckyd-cli.exe`` at the repo root (dev build).
      5. ``luckyd-cli.exe`` beside the frozen browser exe / its _internal
         folder (packaged installer -- see LuckyDBrowser.spec's datas).
      6. Live source: ``python main.py`` from the repo root — only when that
         file actually exists AND a Python interpreter is on PATH (dev
         checkouts only; a frozen install has neither).

    ``luckyd-code.exe`` is deliberately never a candidate here: it is the
    Harness HQ backend (built from ``web_server.py``) -- a headless HTTP
    server with no stdin loop at all. Spawning it on this ConPTY just prints
    a server banner and hangs with no prompt to type into. luckyd-cli.exe
    (built from ``main.py`` via ``main.spec``) is the real interactive REPL:
    rich UI, /help, /tools, /model, and every registered tool including the
    multi-agent "mesh" tools (AgentHandoff, TeamCreate, SendMessage,
    ReceiveMessage, ListAgents, SubAgent).

    Raises ``FileNotFoundError`` with remediation guidance when nothing is
    found — the bridge prints it in the terminal instead of spawning a
    garbage process on the PTY.
    """
    override = cli_path.strip() or os.environ.get("LUCKYD_CLI", "").strip()
    if override:
        p = _expand(override)
        if not p.is_file():
            raise FileNotFoundError(f"terminal_cli does not exist: {p}")
        # Pre-2.5.6 settings pointed here.  It is the HTTP harness, not a
        # REPL; silently using the sibling CLI keeps existing installs usable.
        if p.name.casefold() == "luckyd-code.exe":
            interactive = p.with_name("luckyd-cli.exe")
            if interactive.is_file():
                p = interactive
            else:
                raise FileNotFoundError(
                    f"terminal_cli points to {p.name}, the headless HQ server. "
                    "Set it to luckyd-cli.exe or main.py instead."
                )
        if p.suffix.lower() == ".py":
            interp = _python_for_scripts()
            if interp is None:
                raise FileNotFoundError(
                    f"terminal_cli points to a .py ({p}) but no Python "
                    "interpreter was found on PATH"
                )
            return [interp, str(p)]
        return [str(p)]
    desktop = _desktop_exe()
    if desktop is not None:
        return [str(desktop)]
    repo_exe = _REPO_ROOT / "luckyd-cli.exe"
    if repo_exe.exists():
        return [str(repo_exe)]
    if getattr(sys, "frozen", False):
        # PyInstaller extracts to _internal/ beside the running exe.
        base_dir = Path(sys.executable).resolve().parent
        for cand in (
            base_dir / "luckyd-cli.exe",
            base_dir / "_internal" / "luckyd-cli.exe",
        ):
            if cand.exists():
                return [str(cand)]
    live = _REPO_ROOT / "main.py"
    if live.exists():
        interp = _python_for_scripts()
        if interp is not None:
            return [interp, str(live)]
    raise FileNotFoundError(
        "no LuckyD Code CLI found — set the browser's terminal_cli setting "
        "(or the LUCKYD_CLI env var) to a luckyd-cli.exe or main.py, "
        "or put luckyd-cli.exe back on the Desktop"
    )


def _agent2_dir() -> Path | None:
    """Locate the standalone ``coding-agent`` checkout (the 2nd agent).

    Looks for a ``coding-agent`` folder on the user's real Desktop (Known
    Folder API, so OneDrive-redirected Desktops work), then the common
    plain/OneDrive fallbacks. This is the project the Desktop shortcut
    ``LuckyD Code.lnk`` launches via ``run.bat``.
    """
    candidates: list[Path] = []
    try:  # canonical Desktop path, redirection-aware
        import ctypes

        buf = ctypes.create_unicode_buffer(520)
        # SHGetFolderPathW(hwnd, csidl=0x0010 DESKTOPDIRECTORY, token, flags, path)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
            candidates.append(Path(buf.value) / "coding-agent")
    except Exception:
        pass
    home = Path.home()
    one_drive = os.environ.get("ONEDRIVE", "").strip()  # Windows env names are case-insensitive
    candidates += [
        home / "OneDrive" / "Desktop" / "coding-agent",
        home / "Desktop" / "coding-agent",
    ]
    if one_drive:
        candidates.append(Path(one_drive) / "Desktop" / "coding-agent")
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def _agent_cwd(cli_path: str = "") -> Path:
    """Working dir for 1st-agent sessions.

    An explicit override (``terminal_cli`` setting / ``LUCKYD_CLI`` env var)
    boots in the file's own folder — that is the agent's project workspace.
    Auto-detected CLIs keep the historical repo-root default.
    """
    override = cli_path.strip() or os.environ.get("LUCKYD_CLI", "").strip()
    if override:
        return _expand(override).parent
    return _REPO_ROOT


def _agent2_cwd(cli2_path: str = "") -> Path:
    """Working dir for 2nd-agent sessions — its own checkout (its workspace)."""
    override = cli2_path.strip() or os.environ.get("LUCKYD_CLI2", "").strip()
    if override:
        return _expand(override).parent
    return _agent2_dir() or Path.home()


def _agent2_command(cli2_path: str = "") -> list[str]:
    """How to launch the 2nd agent — the standalone coding-agent CLI.

    Resolution order (first existing wins):
      1. ``cli2_path`` argument — the browser's ``terminal_cli2`` setting.
      2. ``LUCKYD_CLI2`` env var (a ``.py`` runs under a real interpreter).
      3. Live source: ``python main.py`` from the Desktop ``coding-agent``
         checkout — the same interactive REPL the ``LuckyD Code.lnk``
         shortcut starts via ``run.bat``.

    The checkout's ``luckyd-code.exe`` is deliberately NOT auto-used: it is
    the Harness HQ backend server (built from ``web_server.py``, with the
    rich/prompt_toolkit CLI front-end excluded) — it prints a server banner
    and has no prompt to type into. Point ``terminal_cli2`` at it explicitly
    only if a server in the tab is really what you want.

    ``.bat``/``.cmd`` overrides run via ``cmd.exe /c`` (a batch file is not a
    spawnable executable), so pointing the setting straight at the shortcut's
    ``run.bat`` works exactly like double-clicking it.

    Raises ``FileNotFoundError`` with remediation guidance when nothing is
    found — the bridge prints it in the terminal instead of spawning a
    garbage process on the PTY.
    """
    override = cli2_path.strip() or os.environ.get("LUCKYD_CLI2", "").strip()
    if override:
        p = _expand(override)
        if p.suffix.lower() == ".py":
            interp = _python_for_scripts()
            if interp is None:
                raise FileNotFoundError(
                    f"terminal_cli2 points to a .py ({p}) but no Python "
                    "interpreter was found on PATH"
                )
            return [interp, str(p)]
        if p.suffix.lower() in (".bat", ".cmd"):
            return ["cmd.exe", "/c", str(p)]
        return [str(p)]
    root = _agent2_dir()
    if root is not None:
        live = root / "main.py"
        if live.exists():
            interp = _python_for_scripts()
            if interp is not None:
                return [interp, str(live)]
    raise FileNotFoundError(
        "no 2nd agent found — expected main.py in the coding-agent checkout "
        "on the Desktop; set the browser's terminal_cli2 setting (or the "
        "LUCKYD_CLI2 env var) to its main.py or run.bat"
    )


# Shells the terminal tab can spawn. "agent" is the LuckyD Code CLI (the
# classic terminal); "agent2" is the standalone coding-agent checkout on the
# Desktop (the second agent, launched by the LuckyD Code shortcut);
# "powershell"/"cmd" are plain system shells for everyday commands.
# The mesh-* entries expose the Agent Mesh CLIs (see ~/agent-mesh) as
# first-class terminal shells — each spawns the real agent CLI on its own
# ConPTY. Availability is probed with shutil.which() so agents that aren't
# installed simply don't render in the terminal page's dock.
SHELLS = (
    "agent",
    "agent2",
    "powershell",
    "cmd",
    "mesh-agy",
    "mesh-antigravity",
    "mesh-claude",
    "mesh-codex",
    "mesh-copilot",
    "mesh-qwen",
    "mesh-opencode",
    "mesh-cline",
    "mesh-openclaw",
    "mesh-dsh",
    "mesh-pi",
    "agy",
    "antigravity",
)

# Agent Mesh shells: shell name -> executable resolved on PATH.
MESH_SHELLS = {
    "mesh-agy": "agy",
    "mesh-antigravity": "agy",
    "agy": "agy",
    "antigravity": "agy",
    "mesh-claude": "claude",
    "mesh-codex": "codex",
    "mesh-copilot": "copilot",
    "mesh-qwen": "qwen",
    "mesh-opencode": "opencode",
    "mesh-cline": "cline",
    "mesh-openclaw": "openclaw",
    "mesh-dsh": "dsh",
    "mesh-pi": "pi",
}


def _find_mesh_exe(exe_name: str) -> str | None:
    """Find an agent CLI executable on PATH or in standard user locations."""
    import shutil

    found = shutil.which(exe_name)
    if found:
        return found
    if exe_name in ("agy", "antigravity"):
        for cand in (
            Path.home() / "AppData" / "Local" / "agy" / "bin" / f"{exe_name}.exe",
            Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
            Path.home() / ".gemini" / "antigravity-cli" / "bin" / "agy.exe",
        ):
            if cand.is_file():
                return str(cand)
    return None


def mesh_shells_available() -> dict[str, bool]:
    """Which Agent Mesh shells can actually spawn right now (exe on PATH)."""
    return {name: bool(_find_mesh_exe(exe)) for name, exe in MESH_SHELLS.items()}


def _mesh_shell_command(shell: str) -> list[str]:
    """Resolve a mesh-* shell to its absolute executable (allowlisted)."""
    exe_name = MESH_SHELLS[shell]
    raw = _find_mesh_exe(exe_name)
    if not raw:
        raise FileNotFoundError(
            f"Agent Mesh shell '{shell}' is not installed (missing: {exe_name})"
        )
    exe = str(Path(raw).resolve())
    # Validate it's a real file — mitigates PATH hijack TOCTOU where a fake
    # exe appears between availability check and spawn.
    if not Path(exe).is_file():
        raise FileNotFoundError(f"Agent Mesh shell '{shell}' resolved to non-file: {exe}")
    return [exe]


def _shell_command(shell: str, cli_path: str = "", cli2_path: str = "") -> list[str]:
    """Resolve a shell name to its spawn command (allowlisted — never raw input)."""
    shell = (shell or "agent").strip().lower()
    if shell == "powershell":
        return ["powershell.exe", "-NoLogo", "-NoExit"]
    if shell == "cmd":
        return ["cmd.exe"]
    if shell == "agent2":
        return _agent2_command(cli2_path)
    if shell in MESH_SHELLS:
        return _mesh_shell_command(shell)
    return _cli_command(cli_path)


def _client_options(ws) -> tuple[int, int, str]:
    """Dimensions + shell the client advertised in the WS URL query.

    The xterm page dials ``ws://host:port/?cols=C&rows=R&shell=S`` with its
    fitted size, so the PTY is BORN at the right dimensions — no boot-time
    reflow race where the CLI paints at 120x30 and then gets resized mid-draw
    (fullscreen TUIs wrap their wide rows and scroll themselves off-screen).
    Unknown shell names fall back to the agent CLI.
    """
    cols, rows, shell = _COLS, _ROWS, "agent"
    try:
        path = getattr(getattr(ws, "request", None), "path", "") or ""
        query = parse_qs(urlparse(path).query)
        cols = int(query.get("cols", [cols])[0])
        rows = int(query.get("rows", [rows])[0])
        shell = query.get("shell", [shell])[0].strip().lower()
    except (TypeError, ValueError, IndexError):
        cols, rows = _COLS, _ROWS
    if shell not in SHELLS:
        shell = "agent"
    return max(20, min(cols, 500)), max(5, min(rows, 200)), shell


def _client_token(ws) -> str:
    """Extract the browser-only WebSocket credential without logging it."""
    try:
        path = getattr(getattr(ws, "request", None), "path", "") or ""
        return str(parse_qs(urlparse(path).query).get("token", [""])[0])
    except (TypeError, ValueError, IndexError):
        return ""


def _spawn_pty(
    cli_path: str = "",
    shell: str = "agent",
    cols: int = _COLS,
    rows: int = _ROWS,
    cli2_path: str = "",
):
    """Spawn the chosen shell on a ConPTY. Returns a pywinpty PTY handle."""
    from winpty import PTY  # lazy import so the module loads without pywinpty

    pty = PTY(cols, rows)
    cmd = _shell_command(shell, cli_path, cli2_path)
    appname, rest = cmd[0], cmd[1:]
    cmdline = subprocess.list2cmdline(rest) if rest else None
    # Agent sessions boot in their own project (their workspace); system
    # shells open in the user's home directory like a fresh console would.
    if shell == "agent":
        cwd = str(_agent_cwd(cli_path))
    elif shell == "agent2":
        cwd = str(_agent2_cwd(cli2_path))
    else:
        cwd = str(Path.home())
    env = dict(os.environ)
    if shell == "agent":
        env["LUCKYD_AGENT_SLOT"] = "1"
    elif shell == "agent2":
        env["LUCKYD_AGENT_SLOT"] = "2"
    # pywinpty's PTY.spawn() expects the environment as a NUL-joined block
    # string ("name=value\0name=value\0…"), NOT a dict — passing a dict
    # raises cffi's "argument env: 'dict' object is not an instance of str",
    # which broke every terminal tab. Build the same block pywinpty's
    # PtyProcess.spawn() produces (see winpty/ptyprocess.py).
    # Sanitize: NUL bytes would truncate the block and leak/corrupt env.
    sanitized = {}
    for k, v in env.items():
        if not k or "=" in k or "\0" in k:
            continue
        kk = k.replace("\0", "")
        vv = str(v).replace("\0", "").replace("\r", "").replace("\n", "")
        sanitized[kk] = vv
    env_block = "\0".join(f"{k}={v}" for k, v in sanitized.items()) + "\0"
    pty.spawn(appname, cmdline=cmdline, cwd=cwd, env=env_block)
    return pty


class TerminalServer:
    """Owns the WebSocket server thread. One PTY per connected client."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        cli_path: str = "",
        cli2_path: str = "",
        token: str = "",
    ):
        self.host = host
        self.port = int(port)
        self.cli_path = cli_path  # explicit CLI override (browser `terminal_cli` setting)
        self.cli2_path = cli2_path  # 2nd-agent override (browser `terminal_cli2` setting)
        self._token = token
        self._server = None
        self._thread: threading.Thread | None = None
        self._clients: set = set()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _authorized(self, ws) -> bool:
        """Whether this client supplied this profile's terminal credential."""
        return bool(self._token) and hmac.compare_digest(self._token, _client_token(ws))

    def _handle(self, ws) -> None:
        """Bridge one WebSocket connection to one fresh PTY."""
        # A loopback port is reachable from every page open in the browser.
        # Require the per-profile secret that only the locally served terminal
        # page receives, before a WebSocket can create a shell with the user's
        # permissions.  An empty server token is deliberately fail-closed so
        # a direct TerminalServer use cannot accidentally expose a PTY.
        if not self._authorized(ws):
            with contextlib.suppress(Exception):
                ws.close(code=1008, reason="terminal authentication required")
            return
        try:
            cols, rows, shell = _client_options(ws)
            pty = _spawn_pty(
                self.cli_path,
                shell=shell,  # nosec B604
                cols=cols,
                rows=rows,
                cli2_path=self.cli2_path,
            )
        except Exception as exc:  # pywinpty missing or spawn failed
            # Don't leak internal paths to WS client — log detail server-side.
            with contextlib.suppress(Exception):
                print(f"[terminal] spawn failed: {exc}")
            with contextlib.suppress(Exception):
                ws.send("\r\n\x1b[31m[terminal failed to start]\x1b[0m\r\n")
                ws.close()
            return

        with self._lock:
            self._clients.add(ws)
        stop = threading.Event()

        def pump_out() -> None:
            """PTY → WebSocket."""
            try:
                while not stop.is_set():
                    if not pty.isalive():
                        break
                    data = pty.read(blocking=False)
                    if data:
                        try:
                            ws.send(data)
                        except Exception:
                            break
                    else:
                        stop.wait(0.01)
            except Exception:
                pass
            finally:
                stop.set()
                with contextlib.suppress(Exception):
                    ws.close()

        threading.Thread(target=pump_out, name="term-out", daemon=True).start()

        try:
            # WebSocket → PTY. JSON control frames handle resize; everything
            # else is raw keystroke input written straight to the console.
            for message in ws:
                text = message.decode("utf-8", "replace") if isinstance(message, bytes) else message
                if text.startswith('{"type":'):
                    obj = None
                    with contextlib.suppress(ValueError):
                        obj = json.loads(text)
                    if isinstance(obj, dict) and obj.get("type") == "resize":
                        with contextlib.suppress(Exception):
                            # pywinpty's set_size is (cols, rows) — passing
                            # (rows, cols) here shrank the ConPTY to a few
                            # dozen columns and wrapped the CLI off-screen.
                            pty.set_size(
                                max(20, min(int(obj.get("cols", _COLS)), 500)),
                                max(5, min(int(obj.get("rows", _ROWS)), 200)),
                            )
                        continue
                try:
                    pty.write(text)
                except Exception:
                    break
        except Exception:
            pass
        finally:
            stop.set()
            with self._lock:
                self._clients.discard(ws)
            with contextlib.suppress(Exception):
                pty.cancel_io()  # unblock readers; child exits with the session

    def start(self) -> bool:
        """Start serving (idempotent). Returns True when the socket is bound."""
        if self.running:
            return True
        try:
            from websockets.sync.server import serve
        except ImportError:
            return False
        try:
            # Limit WS frame size — unbounded messages can OOM via pty.write.
            self._server = serve(self._handle, self.host, self.port, max_size=1 << 20)
        except OSError:
            return False
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="terminal-ws", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            if self._server is not None:
                self._server.shutdown()
        for ws in list(self._clients):
            with contextlib.suppress(Exception):
                ws.close()
        self._clients.clear()
