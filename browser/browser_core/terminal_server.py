"""In-browser terminal — WebSocket ↔ PTY bridge for the LuckyD Code CLI.

Serves a real interactive ``luckyd-code`` terminal to a browser tab:

    [ xterm.js tab ] ──ws──▶ [ this bridge ] ──ConPTY──▶ [ luckyd-code CLI ]

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
    """Locate ``luckyd-code.exe`` on the user's real Desktop.

    Uses the Windows Known Folder API (CSIDL_DESKTOPDIRECTORY) so it works even
    when the Desktop is OneDrive-redirected (e.g. ``…\\OneDrive\\Desktop``),
    falling back to the common plain/OneDrive locations.
    """
    candidates: list[Path] = []
    try:  # canonical Desktop path, redirection-aware
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        # SHGetFolderPathW(hwnd, csidl=0x0010 DESKTOPDIRECTORY, token, flags, path)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
            candidates.append(Path(buf.value) / "luckyd-code.exe")
    except Exception:
        pass
    home = Path.home()
    one_drive = os.environ.get("ONEDRIVE", "").strip()  # Windows env names are case-insensitive
    candidates += [
        home / "Desktop" / "luckyd-code.exe",
        home / "OneDrive" / "Desktop" / "luckyd-code.exe",
    ]
    if one_drive:
        candidates.append(Path(one_drive) / "Desktop" / "luckyd-code.exe")
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
      3. The standalone ``luckyd-code.exe`` on the user's Desktop (their build).
      4. ``luckyd-code.exe`` at the repo root (dev build).
      5. Live source: ``python main.py`` from the repo root — only when that
         file actually exists. (In the frozen app neither the repo exe nor
         ``main.py`` exists beside ``LuckyDBrowser.exe``, and the exe bundled
         in ``_internal`` is the harness SERVER, not an interactive CLI.)

    Raises ``FileNotFoundError`` with remediation guidance when nothing is
    found — the bridge prints it in the terminal instead of spawning a
    garbage process on the PTY.
    """
    override = cli_path.strip() or os.environ.get("LUCKYD_CLI", "").strip()
    if override:
        p = _expand(override)
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
    repo_exe = _REPO_ROOT / "luckyd-code.exe"
    if repo_exe.exists():
        return [str(repo_exe)]
    live = _REPO_ROOT / "main.py"
    if live.exists():
        interp = _python_for_scripts()
        if interp is not None:
            return [interp, str(live)]
    raise FileNotFoundError(
        "no LuckyD Code CLI found — set the browser's terminal_cli setting "
        "(or the LUCKYD_CLI env var) to a luckyd-code.exe or main.py, "
        "or put luckyd-code.exe back on the Desktop"
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

        buf = ctypes.create_unicode_buffer(260)
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
SHELLS = ("agent", "agent2", "powershell", "cmd")


def _shell_command(shell: str, cli_path: str = "", cli2_path: str = "") -> list[str]:
    """Resolve a shell name to its spawn command (allowlisted — never raw input)."""
    shell = (shell or "agent").strip().lower()
    if shell == "powershell":
        return ["powershell.exe", "-NoLogo", "-NoExit"]
    if shell == "cmd":
        return ["cmd.exe"]
    if shell == "agent2":
        return _agent2_command(cli2_path)
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
    pty.spawn(appname, cmdline=cmdline, cwd=cwd, env=None)
    return pty


class TerminalServer:
    """Owns the WebSocket server thread. One PTY per connected client."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        cli_path: str = "",
        cli2_path: str = "",
    ):
        self.host = host
        self.port = int(port)
        self.cli_path = cli_path  # explicit CLI override (browser `terminal_cli` setting)
        self.cli2_path = cli2_path  # 2nd-agent override (browser `terminal_cli2` setting)
        self._server = None
        self._thread: threading.Thread | None = None
        self._clients: set = set()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _handle(self, ws) -> None:
        """Bridge one WebSocket connection to one fresh PTY."""
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
            with contextlib.suppress(Exception):
                ws.send(f"\r\n\x1b[31m[terminal failed to start: {exc}]\x1b[0m\r\n")
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
            self._server = serve(self._handle, self.host, self.port)
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
