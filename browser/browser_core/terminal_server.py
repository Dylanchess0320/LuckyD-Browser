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

import collections
import contextlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9881

# Name of the HttpOnly session cookie the browser sets on its own profile.
# The terminal credential travels in this cookie (sent automatically on the
# WebSocket handshake) — never in the URL, where it would leak into logs,
# history, and Referer headers. (4.0)
TERM_COOKIE = "luckyd_term"

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
    if getattr(sys, "frozen", False):
        # The packaged browser must use the CLI shipped in ITS OWN build, not
        # a stale luckyd-cli.exe left on the Desktop by an older release.
        base_dir = Path(sys.executable).resolve().parent
        for cand in (
            base_dir / "luckyd-cli.exe",
            base_dir / "_internal" / "luckyd-cli.exe",
        ):
            if cand.exists():
                return [str(cand)]
    else:
        # Live checkout: current source beats every old exe (the repo-root
        # luckyd-cli.exe was routinely months behind main.py).
        live = _REPO_ROOT / "main.py"
        if live.exists():
            interp = _python_for_scripts()
            if interp is not None:
                return [interp, str(live)]
    desktop = _desktop_exe()
    if desktop is not None:
        return [str(desktop)]
    for n_cand in (
        _REPO_ROOT / "luckyd-cli.exe",
        _REPO_ROOT / "dist" / "luckyd-cli.exe",
        _REPO_ROOT / "build" / "luckyd-code.dist" / "luckyd-cli.exe",
    ):
        if n_cand.exists():
            return [str(n_cand)]
    # Last resort in a source checkout without main.py resolution.
    live = _REPO_ROOT / "main.py"
    if live.exists():
        interp = _python_for_scripts()
        if interp is not None:
            return [interp, str(live)]
    raise FileNotFoundError(
        "no LuckyD Code CLI found - set the browser's terminal_cli setting "
        "(or the LUCKYD_CLI env var) to luckyd-cli.exe or main.py, "
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
    """Working dir for 2nd-agent sessions — its own checkout (its workspace).

    Without a Desktop checkout the session falls back to the Agent 1 CLI
    (see _agent2_command), so it boots in the repo root — the same
    workspace Agent 1 uses — instead of the bare home directory.
    """
    override = cli2_path.strip() or os.environ.get("LUCKYD_CLI2", "").strip()
    if override:
        return _expand(override).parent
    return _agent2_dir() or _REPO_ROOT


def _agent2_command(cli2_path: str = "") -> list[str]:
    """How to launch the 2nd agent — the standalone coding-agent CLI.

    Resolution order (first existing wins):
      1. ``cli2_path`` argument — the browser's ``terminal_cli2`` setting.
      2. ``LUCKYD_CLI2`` env var (a ``.py`` runs under a real interpreter).
      3. Live source: ``python main.py`` from the Desktop ``coding-agent``
         checkout — the same interactive REPL the ``LuckyD Code.lnk``
         shortcut starts via ``run.bat``.
      4. Fallback: the Agent 1 CLI itself with ``--agent 2`` (10.7.6).
         Agent 2 used to hard-fail whenever the Desktop checkout was
         missing or moved, while Agent 1 — with its long fallback chain —
         kept working. Both slots run the same ``main.py`` build; the
         slot is selected by the flag (and by the LUCKYD_AGENT_SLOT=2
         env the PTY spawn sets), so the fallback is a real Agent 2.

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
        if not p.is_file():
            raise FileNotFoundError(f"terminal_cli2 does not exist: {p}")
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
    # Fallback (10.7.6): no Desktop checkout — run the Agent 1 CLI as
    # slot 2. _cli_command("") applies Agent 1's full resolution chain
    # (Desktop exe, repo builds, frozen _internal, live main.py).
    try:
        base = _cli_command("")
    except FileNotFoundError:
        base = None
    if base:
        return [*base, "--agent", "2"]
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
# Dylan 2026-09-14: DeepSeek (mesh-dsh/mesh-deepseek/deepseek/dsh) and
# Muse Spark removed from the mesh — Muse Code (mesh-muse/muse) stays.
# 9.4: Cline CLI (cline, alongside mesh-cline) kept — both Agent 1 and
# Agent 2 terminals can spawn it. Cline free tier: rotating FREE
# models in CLI/IDE.
# NOTE: Gemini CLI (mesh-gemini/gemini) was dropped from the mesh in favor of
# Antigravity (agy) for the plan/build stages (see ~/agent-mesh, v1.0.1).
# OpenCode (mesh-opencode/opencode) rides alongside MiniMax Code CLI
# (mesh-mcode/mcode) + media CLI (mesh-mmx/mmx) — and Google Jules
# (mesh-jules/jules), the async cloud coder that works repos and returns
# pull requests, rides alongside them too.
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
    "mesh-mcode",
    "mesh-mmx",
    "mesh-cline",
    "cline",
    "mesh-openclaw",
    "mesh-hermes",
    "mesh-pi",
    "mesh-grok",
    "mesh-muse",
    "mesh-jules",
    # 2026-09-24: Aider (mesh-aider/aider) — open-source git-native pair
    # programmer (auto-commits every edit, repo map, 4.1M+ installs).
    "mesh-aider",
    "aider",
    "agy",
    "antigravity",
    "grok",
    "muse",
    "mcode",
    "mmx",
    "jules",
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
    "mesh-mcode": "mcode",
    "mcode": "mcode",
    "mesh-mmx": "mmx",
    "mmx": "mmx",
    "mesh-cline": "cline",
    "cline": "cline",
    "mesh-openclaw": "openclaw",
    "mesh-hermes": "hermes",
    "mesh-pi": "pi",
    "mesh-grok": "grok",
    "grok": "grok",
    "mesh-jules": "jules",
    "jules": "jules",
    "mesh-aider": "aider",
    "aider": "aider",
}

# Extra default args per mesh shell (appended after the resolved executable).
# hermes needs its explicit `chat` subcommand — a bare `hermes`
# exits immediately on a closed stdin, which showed up as an instantly dead
# terminal pane.
MESH_SHELL_ARGS: dict[str, list[str]] = {
    "mesh-hermes": ["chat"],
}

# Native Muse Code (`muse`) has no Windows build — Meta ships macOS/Linux
# only and the installer hard-fails on Windows. The supported route on this
# machine is WSL2 + Ubuntu: `muse` lives INSIDE Linux at ~/.local/bin/muse,
# so the Windows side spawns it via wsl.exe. These shells are resolved by
# _wsl_muse_command(), not MESH_SHELLS (there is no muse.exe on PATH).
MUSE_WSL_DISTRO = "Ubuntu"
MESH_WSL_SHELLS = frozenset({"mesh-muse", "muse"})
# Login shell (`bash -lic`) so ~/.local/bin is on PATH inside Ubuntu, plus a
# friendly fallback: if muse isn't installed in Linux yet, print the install
# steps and drop to bash INSTEAD of exiting instantly (dead pane).
MUSE_WSL_BOOT = (
    "if command -v muse >/dev/null 2>&1; then exec muse; else "
    "echo ''; echo 'Muse CLI not found inside Ubuntu.'; "
    "echo 'Install it INSIDE Ubuntu with:'; "
    "echo '  curl -fsSL https://dev.meta.ai/install.sh | bash'; "
    "echo '  exec $SHELL -l'; echo '  muse login   # paste the link into Edge/Chrome'; "
    "echo ''; exec bash; fi"
)


def _find_mesh_exe(exe_name: str) -> str | None:
    """Find an agent CLI executable on PATH or in standard user locations."""
    import shutil

    found = shutil.which(exe_name)
    if found:
        return found
    if exe_name in ("agy", "antigravity"):
        # Antigravity (agy) installs to %LOCALAPPDATA%\agy\bin. (It used to also
        # be discoverable via the Gemini CLI extension layout under
        # ~/.gemini/antigravity-cli — dropped once Gemini CLI left the mesh.)
        for cand in (
            Path.home() / "AppData" / "Local" / "agy" / "bin" / f"{exe_name}.exe",
            Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
        ):
            if cand.is_file():
                return str(cand)
    if exe_name == "grok":
        # Official Windows installer drops grok.exe (and agent.exe) in
        # %USERPROFILE%\.grok\bin and adds it to User PATH — but a frozen
        # browser / stale shell may not see the updated PATH yet, so probe
        # the well-known location directly (mirrors the agy fallback above).
        # The npm alt (`npm i -g @xai-official/grok`) IS on PATH via
        # shutil.which, so only the install.ps1 location needs help here.
        for cand in (
            Path.home() / ".grok" / "bin" / "grok.exe",
            Path.home() / ".grok" / "bin" / "agent.exe",
        ):
            if cand.is_file():
                return str(cand)
    if exe_name == "cline":
        # Cline CLI: npm i -g cline. shutil.which usually finds it, but
        # frozen browsers / stale PATHs may not — probe directly.
        # Free tier: rotating FREE models in CLI/IDE (account quota-limited).
        for cand in (
            Path.home() / "AppData" / "Roaming" / "npm" / "cline.cmd",
            Path.home() / "AppData" / "Roaming" / "npm" / "cline.exe",
            Path.home() / ".npm-global" / "bin" / "cline",
        ):
            if cand.is_file():
                return str(cand)
    if exe_name == "mcode":
        # MiniMax Code CLI: npm i -g @minimax-ai/code. Probe the
        # %USERPROFILE%/.minimax-code fallback like tools/minimax_cli.
        for cand in (
            Path.home() / ".minimax-code" / ("mcode.cmd" if os.name == "nt" else "mcode"),
            Path(os.environ.get("USERPROFILE", "").strip() or str(Path.home()))
            / ".minimax-code"
            / ("mcode.cmd" if os.name == "nt" else "mcode"),
        ):
            try:
                if cand.is_file():
                    return str(cand)
            except OSError:
                pass
    if exe_name == "mmx":
        # MiniMax Media CLI: npm i -g mmx-cli. shutil.which usually finds
        # it, but a frozen browser / stale PATH may not — probe the npm
        # global locations directly (mirrors the cline fallback above).
        # Without this probe the mesh chip stayed dimmed and every spawn
        # failed with "not installed" even after a correct install.
        for cand in (
            Path.home() / "AppData" / "Roaming" / "npm" / "mmx.cmd",
            Path.home() / "AppData" / "Roaming" / "npm" / "mmx.exe",
            Path.home() / ".npm-global" / "bin" / "mmx",
            Path.home() / ".npm-global" / "bin" / "mmx.cmd",
        ):
            try:
                if cand.is_file():
                    return str(cand)
            except OSError:
                pass
    return None


def _find_wsl_exe() -> str | None:
    """Locate wsl.exe without ever invoking WSL (invoking it can hang when
    the subsystem is broken — e.g. REGDB_E_CLASSNOTREG — which would stall
    the terminal page render that calls mesh_shells_available())."""
    import shutil

    found = shutil.which("wsl")
    if found:
        return found
    sys32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "wsl.exe"
    if sys32.is_file():
        return str(sys32)
    return None


# Availability probing walks PATH for ~24 executables (plus well-known
# install dirs), and the terminal/mesh pages ask on every render — cache
# the answer briefly so page loads stop paying for the rescan. 5s is short
# enough that a just-installed agent CLI appears on the next page refresh.
_AVAIL_TTL_SEC = 5.0
_avail_cache: dict[str, bool] | None = None
_avail_cache_at = 0.0
_avail_lock = threading.Lock()


def _reset_mesh_availability_cache() -> None:
    """Drop the cached availability answer (tests, post-install refresh)."""
    global _avail_cache, _avail_cache_at
    with _avail_lock:
        _avail_cache = None
        _avail_cache_at = 0.0


def mesh_shells_available() -> dict[str, bool]:
    """Which Agent Mesh shells can actually spawn right now (exe on PATH).

    WSL-backed Muse shells report wsl.exe presence (fast, never invokes a
    possibly-broken distro). Whether `muse` exists INSIDE Ubuntu is checked
    at spawn time with a friendly install hint, so a broken/missing distro
    shows a helpful message instead of a dead pane or a hung page load.

    The probe result is cached for a few seconds (see _AVAIL_TTL_SEC);
    callers receive a copy they may freely mutate.
    """
    global _avail_cache, _avail_cache_at
    with _avail_lock:
        if _avail_cache is not None and time.monotonic() - _avail_cache_at < _AVAIL_TTL_SEC:
            return dict(_avail_cache)
    avail = {name: bool(_find_mesh_exe(exe)) for name, exe in MESH_SHELLS.items()}
    wsl_ok = bool(_find_wsl_exe())
    for shell in MESH_WSL_SHELLS:
        avail[shell] = wsl_ok
    with _avail_lock:
        _avail_cache = dict(avail)
        _avail_cache_at = time.monotonic()
    return avail


# ── Agent Mesh backend tiers & doctor (10.7.4) ────────────────────────
#
# Each canonical mesh-* backend is labeled with its billing/auth tier:
#   - "free":          $0 / included tier (CLI login or keyless)
#   - "keyed":         requires an API key in .env / environment
#   - "credit-billed": usage-billed credits or paid subscription
MESH_BACKEND_SPECS: dict[str, dict[str, object]] = {
    "mesh-agy": {
        "label": "Antigravity",
        "exe": "agy",
        "tier": "free",
        "env_keys": (),
        "install_hint": "Install Google Antigravity CLI (agy) to %LOCALAPPDATA%\\agy\\bin",
    },
    "mesh-claude": {
        "label": "Claude",
        "exe": "claude",
        "tier": "credit-billed",
        "env_keys": ("ANTHROPIC_API_KEY",),
        "install_hint": "npm i -g @anthropic-ai/claude-code",
    },
    "mesh-codex": {
        "label": "Codex",
        "exe": "codex",
        "tier": "credit-billed",
        "env_keys": ("OPENAI_API_KEY",),
        "install_hint": "npm i -g @openai/codex",
    },
    "mesh-copilot": {
        "label": "Copilot",
        "exe": "copilot",
        "tier": "credit-billed",
        "env_keys": ("GITHUB_TOKEN", "GH_TOKEN"),
        "install_hint": "gh extension install github/gh-copilot",
    },
    "mesh-qwen": {
        "label": "Qwen",
        "exe": "qwen",
        "tier": "free",
        "env_keys": (),
        "install_hint": "npm i -g @qwen-code/qwen-code",
    },
    "mesh-opencode": {
        "label": "OpenCode",
        "exe": "opencode",
        "tier": "keyed",
        "env_keys": (
            "OPENCODE_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        "install_hint": "npm i -g opencode-ai",
    },
    "mesh-mcode": {
        "label": "MiniMax Code",
        "exe": "mcode",
        "tier": "keyed",
        "env_keys": ("MINIMAX_API_KEY",),
        "install_hint": "npm i -g @minimax-ai/code",
    },
    "mesh-mmx": {
        "label": "MiniMax Media",
        "exe": "mmx",
        "tier": "keyed",
        "env_keys": ("MINIMAX_API_KEY",),
        "install_hint": "npm i -g mmx-cli",
    },
    "mesh-cline": {
        "label": "Cline",
        "exe": "cline",
        "tier": "credit-billed",
        "env_keys": ("CLINEPASS_API_KEY",),
        "install_hint": "npm i -g cline && cline auth",
    },
    "mesh-openclaw": {
        "label": "OpenClaw",
        "exe": "openclaw",
        "tier": "free",
        "env_keys": (),
        "install_hint": "npm i -g openclaw",
    },
    "mesh-hermes": {
        "label": "Hermes",
        "exe": "hermes",
        "tier": "free",
        "env_keys": (),
        "install_hint": "pip install hermes-agent",
    },
    "mesh-pi": {
        "label": "Pi",
        "exe": "pi",
        "tier": "free",
        "env_keys": (),
        "install_hint": "npm i -g @mariozechner/pi-coding-agent",
    },
    "mesh-grok": {
        "label": "Grok",
        "exe": "grok",
        "tier": "keyed",
        "env_keys": ("XAI_API_KEY",),
        "install_hint": "npm i -g @xai-official/grok",
    },
    "mesh-muse": {
        "label": "Muse Code",
        "exe": "wsl",
        "tier": "keyed",
        "env_keys": ("META_API_KEY",),
        "install_hint": "wsl --install -d Ubuntu && curl -fsSL https://dev.meta.ai/install.sh | bash",
    },
    "mesh-jules": {
        "label": "Jules",
        "exe": "jules",
        "tier": "free",
        "env_keys": ("JULES_API_KEY",),
        "install_hint": "npm i -g @google/jules",
    },
    "mesh-aider": {
        "label": "Aider",
        "exe": "aider",
        "tier": "keyed",
        "env_keys": (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY",
            "OLLAMA_MODEL",
        ),
        "install_hint": "pip install aider-chat",
    },
}


def mesh_doctor() -> list[dict[str, object]]:
    """Run a non-blocking readiness check across every Agent Mesh backend.

    Each entry reports: ``shell``, ``label``, ``exe``, ``exe_path``, ``tier``
    (``"free"`` | ``"keyed"`` | ``"credit-billed"``), ``installed``,
    ``auth_ok``, ``status`` (``"ready"`` | ``"exhausted"`` | ``"needs_auth"``
    | ``"not_installed"``), and a one-line human ``detail`` string.
    """
    cline_exhausted = False
    cline_session_ok = False
    try:
        from core.cline_credit import is_cline_credit_exhausted
        from core.providers import cline_session_token

        cline_exhausted = is_cline_credit_exhausted()
        cline_session_ok = bool(cline_session_token())
    except Exception:
        pass

    results: list[dict[str, object]] = []
    for shell, spec in MESH_BACKEND_SPECS.items():
        label = str(spec["label"])
        exe = str(spec["exe"])
        tier = str(spec["tier"])
        raw_env_keys = spec.get("env_keys")
        env_keys = tuple(raw_env_keys) if isinstance(raw_env_keys, (list, tuple)) else ()
        install_hint = str(spec.get("install_hint") or "")

        exe_path = _find_wsl_exe() if shell in MESH_WSL_SHELLS else _find_mesh_exe(exe)
        installed = bool(exe_path)
        present_keys = [k for k in env_keys if (os.environ.get(k, "") or "").strip()]

        if not installed:
            auth_ok = False
            status = "not_installed"
            detail = f"not installed — {install_hint}" if install_hint else "not installed"
        elif shell == "mesh-cline":
            has_cred = bool(present_keys) or cline_session_ok
            if cline_exhausted:
                auth_ok = False
                status = "exhausted"
                detail = "credits exhausted (HTTP 402 marker active)"
            elif has_cred:
                auth_ok = True
                status = "ready"
                src = present_keys[0] if present_keys else "cline CLI session"
                detail = f"ready ({src})"
            else:
                auth_ok = False
                status = "needs_auth"
                detail = "run `cline auth` or set CLINEPASS_API_KEY"
        elif tier == "keyed":
            if present_keys:
                auth_ok = True
                status = "ready"
                detail = f"ready ({present_keys[0]})"
            elif shell == "mesh-muse":
                # Muse Code inside WSL can also authenticate via `muse login`.
                auth_ok = True
                status = "ready"
                detail = "WSL bridge ready (uses META_API_KEY or `muse login`)"
            elif shell in ("mesh-mmx", "mesh-mcode"):
                # MiniMax CLIs authenticate with their own login flow
                # (`mmx auth login` / `mcode login`), not only an env key —
                # an installed binary is usable, so report ready.
                auth_ok = True
                status = "ready"
                login = "mmx auth login" if shell == "mesh-mmx" else "mcode login"
                detail = f"ready (MINIMAX_API_KEY or `{login}`)"
            else:
                auth_ok = False
                status = "needs_auth"
                needed = " or ".join(env_keys[:2]) if env_keys else "API key"
                detail = f"installed — set {needed} in .env"
        else:
            # Free or CLI-login-capable backends are ready once the binary is on PATH.
            auth_ok = True
            status = "ready"
            via = f" ({present_keys[0]})" if present_keys else ""
            detail = f"ready{via}"

        results.append(
            {
                "shell": shell,
                "label": label,
                "exe": exe,
                "exe_path": exe_path,
                "tier": tier,
                "installed": installed,
                "auth_ok": auth_ok,
                "ready": status == "ready",
                "status": status,
                "detail": detail,
                "install_hint": install_hint,
            }
        )
    return results


# ── PTY pump pacing ───────────────────────────────────────────────────
# Output is coalesced for up to _FLUSH_AFTER_SEC (or _FLUSH_BYTES) so a
# chatty TUI's hundreds of tiny writes become a handful of WebSocket
# frames; the idle poll backs off exponentially so a quiet terminal
# costs almost no wakeups.
_FLUSH_BYTES = 16_384
_FLUSH_AFTER_SEC = 0.008
_POLL_BASE_SEC = 0.01
_POLL_MAX_SEC = 0.05


def poll_delay_seconds(idle_streak: int) -> float:
    """Sleep between empty PTY reads: 10ms doubling to a 50ms cap."""
    streak = min(max(int(idle_streak), 0), 3)
    return min(_POLL_MAX_SEC, _POLL_BASE_SEC * (2**streak))


# ── Crash guards ──────────────────────────────────────────────────────
# Every mesh pane / terminal tab hosts a full agent CLI on a ConPTY
# inside the browser process, and sessions used to multiply without any
# limit: a mesh page (4 panes) plus a few terminal tabs could stack a
# dozen heavyweight CLI process trees (Node runtimes, language servers,
# builds) until the machine thrashed and the browser died with them.
# Cap concurrent sessions per server; extra panes get a clear "close a
# pane" message instead of a spawn that pushes the machine over.
_MAX_SESSIONS_DEFAULT = 6


def _max_sessions() -> int:
    """Concurrent PTY session cap (env LUCKYD_TERMINAL_MAX_SESSIONS)."""
    raw = os.environ.get("LUCKYD_TERMINAL_MAX_SESSIONS", "").strip()
    try:
        return max(1, int(raw)) if raw else _MAX_SESSIONS_DEFAULT
    except ValueError:
        return _MAX_SESSIONS_DEFAULT


# Output flood guard: one CLI printing at full speed (build logs, a
# recursive find, a runaway loop) used to stream into the renderer as
# fast as the pump could read — pegging CPU here and in the WebEngine
# view until the app fell over. Sustained output per session is capped
# (with a free burst for normal TUI redraws); the pump waits out the
# window, which back-pressures the ConPTY and the child exactly like a
# real terminal's flow control.
_OUT_RATE_BYTES = 512 * 1024  # sustained bytes/sec per session
_OUT_BURST_BYTES = 256 * 1024  # free burst on top of the rate


class _RateWindow:
    """Rolling 1-second byte window for one session's output throttle."""

    def __init__(self, rate: int = _OUT_RATE_BYTES, burst: int = _OUT_BURST_BYTES):
        self._allowance = rate + burst
        self._events: collections.deque[tuple[float, int]] = collections.deque()

    def _prune(self, now: float) -> None:
        cutoff = now - 1.0
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def wait_for(self, nbytes: int, now: float) -> float:
        """Seconds to wait before sending nbytes (0 when it fits)."""
        self._prune(now)
        total = sum(b for _, b in self._events)
        if total + nbytes <= self._allowance or not self._events:
            return 0.0
        return max(0.0, self._events[0][0] + 1.0 - now)

    def record(self, nbytes: int, now: float) -> None:
        self._prune(now)
        self._events.append((now, nbytes))


def _wsl_muse_command() -> list[str]:
    """Spawn command for native Muse Code inside WSL Ubuntu."""
    raw = _find_wsl_exe()
    if not raw:
        raise FileNotFoundError(
            "Agent Mesh shell 'mesh-muse' needs WSL (missing: wsl.exe). "
            "Run in admin PowerShell: wsl --install -d Ubuntu"
        )
    exe = str(Path(raw).resolve())
    if not Path(exe).is_file():
        raise FileNotFoundError(f"WSL resolved to non-file: {exe}")
    return [exe, "-d", MUSE_WSL_DISTRO, "--", "bash", "-lic", MUSE_WSL_BOOT]


def _mesh_shell_command(shell: str) -> list[str]:
    """Resolve a mesh-* shell to its spawn command (allowlisted).

    Returns [absolute exe, *default args]. Most agents are interactive CLIs
    that need no args; entries in MESH_SHELL_ARGS append fixed launcher args
    (e.g. dsh requires `--profile <name>` and dies without it). WSL-backed
    Muse shells resolve via _wsl_muse_command().
    """
    if shell in MESH_WSL_SHELLS:
        return _wsl_muse_command()
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
    return [exe, *MESH_SHELL_ARGS.get(shell, [])]


def _shell_command(shell: str, cli_path: str = "", cli2_path: str = "") -> list[str]:
    """Resolve a shell name to its spawn command (allowlisted — never raw input)."""
    shell = (shell or "agent").strip().lower()
    if shell == "powershell":
        return ["powershell.exe", "-NoLogo", "-NoExit"]
    if shell == "cmd":
        return ["cmd.exe"]
    if shell == "agent2":
        return _agent2_command(cli2_path)
    if shell in MESH_SHELLS or shell in MESH_WSL_SHELLS:
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
    """Extract the browser-only WebSocket credential without logging it.

    4.0: the credential arrives as the HttpOnly ``luckyd_term`` session
    cookie, which the browser sends automatically on the WS handshake. The
    old ``?token=`` query-param transport is gone — tokens in URLs leak into
    logs, history, and Referer headers.
    """
    try:
        headers = getattr(getattr(ws, "request", None), "headers", None) or {}
        get = getattr(headers, "get", None)
        cookie_header = get("Cookie", "") if callable(get) else ""
        for part in str(cookie_header).split(";"):
            name, _, value = part.partition("=")
            if name.strip() == TERM_COOKIE:
                return value.strip().strip('"')
    except Exception:
        pass
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
        env["LUCKYD_AGENT_NAME"] = "Agent 1"
        env["LUCKYD_AGENT_VERSION"] = "v10.7.8"
    elif shell == "agent2":
        env["LUCKYD_AGENT_SLOT"] = "2"
        env["LUCKYD_AGENT_NAME"] = "Agent 2"
        env["LUCKYD_AGENT_VERSION"] = "v10.7.8"
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
        self._server: object = None
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
        # Crash guard: refuse to spawn once the session cap is reached —
        # each session is a full agent CLI process tree hosted here, and
        # unbounded panes are what used to take the browser down.
        with self._lock:
            at_cap = len(self._clients) >= _max_sessions()
        if at_cap:
            with contextlib.suppress(Exception):
                ws.send(
                    "\r\n\x1b[31m[terminal busy — too many live sessions; "
                    "close a terminal pane and retry]\x1b[0m\r\n"
                )
                ws.close()
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
            """PTY → WebSocket (coalesced; idle polls back off).

            Small writes batch for up to _FLUSH_AFTER_SEC so a chatty TUI
            emits a handful of frames instead of hundreds; each frame is
            still sliced at 8KB on send — a single huge ws.send() can
            garble or drop data the same way a single huge pty.write()
            did on the input side (10.6.3 paste fix).
            """
            chunks: list[str] = []
            buffered = 0
            first_at: float | None = None
            idle_streak = 0
            rate = _RateWindow()

            def _flush() -> bool:
                """Send buffered output; False when the client is gone."""
                nonlocal buffered, first_at
                if not chunks:
                    return True
                payload = "".join(chunks)
                chunks.clear()
                buffered = 0
                first_at = None
                # Flood guard: wait out the rate window (stop-aware) so
                # one CLI printing at full speed can't saturate this
                # process or the renderer; the ConPTY buffer applies
                # back-pressure to the child while we wait.
                while not stop.is_set():
                    delay = rate.wait_for(len(payload), time.monotonic())
                    if delay <= 0:
                        break
                    stop.wait(min(delay, 0.05))
                rate.record(len(payload), time.monotonic())
                try:
                    # Slicing works for both str and bytes payloads.
                    for i in range(0, len(payload), 8192):
                        ws.send(payload[i : i + 8192])
                except Exception:
                    return False
                return True

            try:
                while not stop.is_set():
                    if not pty.isalive():
                        break
                    data = pty.read(blocking=False)
                    if data:
                        text_chunk = (
                            data.decode("utf-8", errors="replace")
                            if isinstance(data, bytes)
                            else str(data)
                        )
                        idle_streak = 0
                        if first_at is None:
                            first_at = time.monotonic()
                        chunks.append(text_chunk)
                        buffered += len(text_chunk)
                        if (
                            buffered >= _FLUSH_BYTES
                            or time.monotonic() - first_at >= _FLUSH_AFTER_SEC
                        ) and not _flush():
                            break
                    elif chunks:
                        # Output is pending: wait only out the rest of the
                        # coalesce window, not the idle backoff — otherwise a
                        # keystroke echo sits buffered for 20ms+.
                        remaining = _FLUSH_AFTER_SEC - (time.monotonic() - (first_at or 0))
                        if remaining > 0:
                            stop.wait(remaining)
                        elif not _flush():
                            break
                    else:
                        stop.wait(poll_delay_seconds(idle_streak))
                        idle_streak += 1
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    _flush()  # never drop the child's final output
                stop.set()
                with contextlib.suppress(Exception):
                    ws.close()

        threading.Thread(target=pump_out, name="term-out", daemon=True).start()

        try:
            # WebSocket → PTY. JSON control frames handle resize; everything
            # else is raw keystroke input written straight to the console.
            # A message is only treated as a control frame when it parses as
            # JSON *and* carries a known control type — a pasted JSON snippet
            # or keystroke burst starting with "{" must reach the shell.
            for message in ws:
                text = message.decode("utf-8", "replace") if isinstance(message, bytes) else message
                if text.lstrip().startswith("{"):
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
                    # Chunk large inputs: a single huge pty.write() garbles or
                    # truncates in ConPTY/pywinpty ("too much info corrupts").
                    # 8KB chunks flow through cleanly; the shell reassembles
                    # them. (Frontend also chunks pastes at 16KB so no single
                    # WS message approaches the 1MB frame limit.)
                    for i in range(0, len(text), 8192):
                        pty.write(text[i : i + 8192])
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
            server = serve(self._handle, self.host, self.port, max_size=1 << 20)
            self._server = server
        except OSError:
            return False
        self._thread = threading.Thread(
            target=server.serve_forever, name="terminal-ws", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            shutdown = getattr(self._server, "shutdown", None)
            if callable(shutdown):
                shutdown()
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for ws in clients:
            with contextlib.suppress(Exception):
                ws.close()
