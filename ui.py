"""
Terminal UI — clean LuckyD Code design with Rich or ANSI fallback.
"""

from __future__ import annotations

import contextlib
import os
import platform
import re
import shutil
import sys
import time

_IS_WINDOWS = platform.system() == "Windows"
if _IS_WINDOWS:
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
else:
    msvcrt = None

# ── Windows ANSI/VT support ───────────────────────────────────────
# On Windows, ANSI escape codes only work if Virtual Terminal Processing
# is explicitly enabled on the console handle. Do this once at import time
# so both the Rich and ANSI-fallback paths render colors correctly.
if platform.system() == "Windows":
    try:
        import ctypes

        _kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        for _handle_id in (-11, -12):  # stdout, stderr
            _handle = _kernel32.GetStdHandle(_handle_id)
            _mode = ctypes.c_ulong()
            if _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode)):
                _kernel32.SetConsoleMode(_handle, _mode.value | 0x0004)
    except Exception:
        # Fall back to colorama if raw ctypes fails
        with contextlib.suppress(Exception):
            import colorama

            colorama.init()

# ── Rich detection ────────────────────────────────────────────────────

_RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.spinner import Spinner  # noqa: F401  (used to register custom spinners)
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme

    # Register a classic half-braille "processing wheel" spinner. Rich does not
    # ship a braille spinner by default, so we add one ourselves.
    #
    # IMPORTANT: Rich's Spinner.__init__ does `spinner["frames"]` and
    # `spinner["interval"]` on whatever SPINNERS[name] resolves to, so the
    # value MUST be a dict with those two keys - frames as a list of
    # single-character strings, interval in milliseconds. A tuple here (as
    # this used to be) crashes with "tuple indices must be integers or
    # slices, not str" the first time the spinner is actually used.
    try:
        from rich._spinners import SPINNERS  # internal registry
    except ImportError:  # pragma: no cover - fallback if internals move
        SPINNERS = None

    if SPINNERS is not None and "processing_wheel" not in SPINNERS:
        SPINNERS["processing_wheel"] = {
            "interval": 80,
            "frames": list("⠁⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈⠈⠉"),
        }

    _RICH_AVAILABLE = True
except ImportError:
    pass

# ── ANSI color codes (fallback) ───────────────────────────────────────

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
    "white": "\033[37m",
    "bright_white": "\033[97m",
    "gray": "\033[90m",
    "bright_blue": "\033[94m",
}

# ── Brand colors ──────────────────────────────────────────────────────

BRAND = {
    "primary": "#00E5FF",
    "dim": "#4DD0E1",
    "muted": "#64748B",
    "success": "#34D399",
    "error": "#F87171",
    "warn": "#FBBF24",
    "surface": "#1E293B",
    # Readability scheme: reasoning in blue, answers in clean white.
    "think": "#6CB6FF",
    "answer": "#FFFFFF",
}

if _RICH_AVAILABLE:
    JCODE_THEME = Theme(
        {
            "markdown.code": f"bold {BRAND['primary']}",
            "markdown.code_block": f"{BRAND['dim']}",
            "markdown.h1": f"bold {BRAND['primary']}",
            "markdown.h2": f"bold {BRAND['primary']}",
            "markdown.h3": f"bold {BRAND['dim']}",
            "markdown.link": f"underline {BRAND['primary']}",
            "markdown.item.bullet": BRAND["muted"],
            "repr.string": BRAND["success"],
            "repr.number": BRAND["warn"],
            "repr.bool_true": BRAND["success"],
            "repr.bool_false": BRAND["error"],
            "repr.none": BRAND["muted"],
        }
    )


# ── Slash commands (shared by /help and the web GUI) ─────────────────────────

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show this help"),
    ("/clear", "Clear conversation + screen"),
    ("/history", "Conversation summary"),
    ("/tools", "List available tools"),
    ("/memory", "Show stored memories"),
    ("/model", "Browse free models — fuzzy search & interactive picker"),
    ("/model <name>", "Fuzzy switch — e.g. /model nemotron, /model kimi, /model qwen"),
    ("/model <n>", "Pick by number from the catalog"),
    ("/refresh", "Refresh model cache"),
    ("/save", "Save conversation to JSON"),
    ("/cost", "Show token usage and cost"),
    ("/undo", "Undo last file change"),
    ("/sessions", "List saved sessions"),
    ("/resume", "Resume a saved session"),
    ("/mcp", "Show MCP server status"),
    ("/version", "Show version"),
    ("/quit", "Exit"),
]


class TerminalUI:
    """Clean terminal UI for LuckyD Code — Rich or ANSI fallback."""

    def __init__(self) -> None:
        self.rich = _RICH_AVAILABLE
        self._console = (
            Console(theme=JCODE_THEME, highlight=True, soft_wrap=True) if self.rich else None
        )
        self._stream_buffer = ""
        self._live: Live | None = None
        self._streaming = False
        self._thinking = False
        self._think_buffer = ""
        self._tool_count = 0
        self._last_action_time = time.time()
        self._width = shutil.get_terminal_size((100, 40)).columns
        self._session_start = time.time()
        self._cost_summary = ""
        self._project_name = ""
        self._provider_name = ""
        self._model_name = ""
        # Spinner state — shows while the agent is working before the first
        # token arrives, and again during tool-call pauses mid-stream.
        self._spinner_status = None  # rich Status or None
        self._spinner_label = ""
        self._spinner_style = "processing_wheel"  # rich spinner name (dots, line, clock…)

    # ── Helpers ────────────────────────────────────────────────────

    def _dim(self, text: str) -> str:
        return (
            f"[{BRAND['muted']}]{text}[/]" if self.rich else f"{ANSI['dim']}{text}{ANSI['reset']}"
        )

    def _primary(self, text: str) -> str:
        return (
            f"[{BRAND['primary']}]{text}[/]"
            if self.rich
            else f"{ANSI['cyan']}{text}{ANSI['reset']}"
        )

    def _sep(self, width: int | None = None) -> str:
        w = width or min(self._width - 4, 64)
        return "─" * max(w, 24)

    # ── Session info ────────────────────────────────────────────────

    def set_session_info(
        self,
        project_name: str = "",
        provider: str = "",
        cost: str = "",
        model: str = "",
    ) -> None:
        """Update session header info."""
        if project_name:
            self._project_name = project_name
        if provider:
            self._provider_name = provider
        if cost:
            self._cost_summary = cost
        if model:
            self._model_name = model

    def _session_header(self) -> str:
        """Build a compact session status line."""
        parts: list[str] = []
        if self._project_name:
            parts.append(self._project_name)
        if self._provider_name:
            label = self._provider_name
            if self._model_name:
                label = f"{label}/{self._model_name}"
            parts.append(label)
        elif self._model_name:
            parts.append(self._model_name)
        return " · ".join(parts)

    # ── Banner ─────────────────────────────────────────────────────

    def enhanced_banner(self) -> None:
        """Startup banner with project and provider context."""
        self.banner()

    def banner(self) -> None:
        """Clean startup banner."""
        header = self._session_header()
        tip = "Type a task, or /help for commands"
        agent_version = os.environ.get("LUCKYD_AGENT_VERSION", "v3.6.0").strip()
        agent_name = os.environ.get("LUCKYD_AGENT_NAME", "").strip()
        ver_label = f"{agent_version}" + (f" ({agent_name})" if agent_name else "")

        if self.rich:
            self._console.print()
            title = Text()
            title.append("  LuckyD Code", style=f"bold {BRAND['primary']}")
            title.append(f"  {ver_label}", style=BRAND["muted"])
            self._console.print(title)
            if header:
                self._console.print(f"  {self._dim(header)}")
            self._console.print(f"  {self._dim(self._sep())}")
            self._console.print(f"  {self._dim(tip)}")
            self._console.print()
        else:
            print(
                f"\n  {ANSI['bold']}{ANSI['cyan']}LuckyD Code{ANSI['reset']} {ANSI['dim']}{ver_label}{ANSI['reset']}"
            )
            if header:
                print(f"  {ANSI['dim']}{header}{ANSI['reset']}")
            print(f"  {ANSI['dim']}{self._sep()}{ANSI['reset']}")
            print(f"  {ANSI['dim']}{tip}{ANSI['reset']}\n")

    def goodbye(self, cost_summary: str = "") -> None:
        summary_parts = [f"{self._tool_count} tools"]
        if cost_summary:
            summary_parts.append(cost_summary)
        elapsed = int(time.time() - self._session_start)
        if elapsed >= 60:
            summary_parts.append(f"{elapsed // 60}m {elapsed % 60}s")
        elif elapsed > 0:
            summary_parts.append(f"{elapsed}s")
        summary = " · ".join(summary_parts)

        if self.rich:
            self._console.print()
            self._console.print(f"  {self._dim(self._sep(40))}")
            self._console.print(f"  {self._dim(summary)}")
            self._console.print(f"  {self._dim('Goodbye.')}\n")
        else:
            print(f"\n  {ANSI['dim']}{summary}{ANSI['reset']}")
            print(f"  {ANSI['dim']}Goodbye.{ANSI['reset']}\n")

    # ── Status ─────────────────────────────────────────────────────

    def _status_line(self, msg: str) -> None:
        if self.rich:
            self._console.print(f"  {self._dim(msg)}")
        else:
            print(f"  {ANSI['dim']}{msg}{ANSI['reset']}")

    # ── Spinner ────────────────────────────────────────────────────

    def start_spinner(self, label: str = "working…") -> None:
        """Show a small spinner while the agent works (pre-token or tool pauses).

        Rich's Status/Spinner touches internals (SPINNERS registry, terminal
        capability probing) that have broken before on this project - wrap it
        so a spinner glitch degrades to the plain ANSI line instead of taking
        down the whole REPL loop.
        """
        if self._spinner_status is not None:
            return  # already spinning
        self._spinner_label = label
        if self.rich:
            try:
                from rich.status import Status

                self._spinner_status = Status(
                    f"  {label}",
                    console=self._console,
                    spinner=self._spinner_style,
                    spinner_style=BRAND["think"],
                )
                self._spinner_status.start()
                return
            except Exception:
                self._spinner_status = None
                # fall through to the plain ANSI line below
        sys.stdout.write(f"  {ANSI['gray']}… {label}{ANSI['reset']}\r")
        sys.stdout.flush()

    def stop_spinner(self) -> None:
        """Stop the spinner if it is running."""
        if self._spinner_status is None:
            return
        if self.rich:
            with contextlib.suppress(Exception):
                self._spinner_status.stop()
            self._spinner_status = None
        else:
            # Clear the spinner line
            sys.stdout.write("\r" + " " * (len(self._spinner_label) + 8) + "\r")
            sys.stdout.flush()
            self._spinner_status = None

    def update_spinner(self, label: str) -> None:
        """Change the spinner label without restarting it."""
        self._spinner_label = label
        if self._spinner_status is not None and self.rich:
            with contextlib.suppress(Exception):
                self._spinner_status.update(f"  {label}")
        elif self._spinner_status is not None:
            sys.stdout.write(f"\r  {ANSI['gray']}… {label}{ANSI['reset']}\r")
            sys.stdout.flush()

    # ── Streaming ──────────────────────────────────────────────────
    @property
    def streamed_chars(self) -> int:
        """How many characters were actually streamed (not counting thinking tokens)."""
        return len(self._stream_buffer)

    def start_streaming(self) -> None:
        """Begin streaming a response (incremental plain text)."""
        self._stream_buffer = ""
        self._streaming = True
        self._thinking = False
        self._think_buffer = ""
        if self.rich and self._live:
            self._live.stop()
            self._live = None
        # Visual breathing room before the answer
        if self.rich:
            self._console.print()
        else:
            print()

    def show_question(self, question: str) -> None:
        """Echo the user question before streaming the response."""
        if self.rich:
            self._console.print(f"\n  {self._dim('you')}  [{BRAND['answer']}]{question}[/]")
        else:
            print(
                f"\n  {ANSI['dim']}you{ANSI['reset']}  "
                f"{ANSI['bright_white']}{question}{ANSI['reset']}"
            )

    def begin_thinking(self) -> None:
        """Start the thinking phase.

        The reasoning text stays hidden for a clean terminal, but a
        spinner (processing wheel) shows while the agent is thinking.
        """
        self._thinking = True
        self._think_buffer = ""
        self.start_spinner("thinking")

    def stream_think_token(self, token: str) -> None:
        """Consume a reasoning token while the thinking spinner spins."""
        if not self._thinking:
            self.begin_thinking()
        self._think_buffer += token

    def end_thinking(self) -> None:
        """Close the thinking phase — stop the spinner, render nothing else."""
        self._thinking = False
        self._think_buffer = ""
        self.stop_spinner()

    def stream_token(self, token: str) -> None:
        """Push a token directly to the terminal as plain, incremental text."""
        self.stop_spinner()
        if self._thinking:
            self.end_thinking()
        if not self._stream_buffer and not self.rich:
            # First answer token — switch the stream to clean white.
            sys.stdout.write(ANSI["bright_white"])
        self._stream_buffer += token
        sys.stdout.write(token)
        sys.stdout.flush()

    def play_done_sound(self) -> None:
        """Soft completion cue (non-blocking)."""
        try:
            if platform.system() == "Windows":
                import winsound

                winsound.MessageBeep(winsound.MB_OK)
            else:
                print("\a", end="", flush=True)
        except Exception:
            pass

    def end_streaming(self) -> None:
        """Finish the plain-text stream."""
        self._streaming = False
        if self.rich and self._live:
            self._live.stop()
            self._live = None
        # Reset the white answer color started by the first streamed token.
        if not self.rich:
            sys.stdout.write(ANSI["reset"])
            sys.stdout.flush()
        print()
        print()

    def finish_response(self, full_text: str) -> None:
        """Render a full response when streaming was not used."""
        if self._streaming:
            return
        if full_text and self.rich:
            self._console.print(Markdown(full_text), style=BRAND["answer"])
        elif full_text:
            self._ansi_markdown(full_text)

    # ── Markdown ───────────────────────────────────────────────────

    def markdown(self, text: str) -> None:
        if not text:
            return
        if self.rich:
            self._console.print(Markdown(text), style=BRAND["answer"])
        else:
            self._ansi_markdown(text)

    def _ansi_markdown(self, text: str) -> None:
        lines = text.split("\n")
        in_code_block = False
        for line in lines:
            if line.startswith("```"):
                if in_code_block:
                    in_code_block = False
                    continue
                in_code_block = True
                lang = line[3:].strip()
                print(f"{ANSI['dim']}── {lang or 'code'} ──{ANSI['reset']}")
                continue
            if in_code_block:
                print(f"  {ANSI['cyan']}{line}{ANSI['reset']}")
                continue
            if line.startswith("### "):
                print(f"\n{ANSI['bold']}{ANSI['bright_white']}{line[4:]}{ANSI['reset']}")
            elif line.startswith("## "):
                print(f"\n{ANSI['bold']}{ANSI['bright_white']}{line[3:]}{ANSI['reset']}")
            elif line.startswith("# "):
                print(f"\n{ANSI['bold']}{ANSI['cyan']}{line[2:]}{ANSI['reset']}")
            elif "**" in line:
                line = re.sub(
                    r"\*\*(.*?)\*\*",
                    f"{ANSI['bold']}\\1{ANSI['reset']}{ANSI['bright_white']}",
                    line,
                )
                print(f"{ANSI['bright_white']}{line}{ANSI['reset']}")
            elif "`" in line:
                line = re.sub(
                    r"`(.*?)`",
                    f"{ANSI['cyan']}\\1{ANSI['bright_white']}",
                    line,
                )
                print(f"{ANSI['bright_white']}{line}{ANSI['reset']}")
            elif line.strip().startswith("- "):
                print(
                    f"  {ANSI['dim']}•{ANSI['reset']} "
                    f"{ANSI['bright_white']}{line.strip()[2:]}{ANSI['reset']}"
                )
            else:
                print(f"{ANSI['bright_white']}{line}{ANSI['reset']}")

    # ── Tool calls ─────────────────────────────────────────────────

    def tool_call_start(self, tool_name: str, args: dict | None = None) -> None:
        """Show a tool is starting."""
        self._tool_count += 1
        arg_preview = ""
        if args:
            if "file_path" in args:
                arg_preview = f"  {args['file_path']}"
            elif "path" in args:
                arg_preview = f"  {args['path']}"
            elif "command" in args:
                arg_preview = f"  {str(args['command'])[:56]}"
            elif "url" in args:
                arg_preview = f"  {str(args['url'])[:56]}"
            elif "query" in args:
                arg_preview = f"  {str(args['query'])[:56]}"

        if self.rich:
            self._console.print(
                f"  {self._dim('›')} {self._primary(tool_name)}{self._dim(arg_preview)}"
            )
        else:
            print(
                f"  {ANSI['dim']}›{ANSI['reset']} "
                f"{ANSI['cyan']}{tool_name}{ANSI['reset']}"
                f"{ANSI['dim']}{arg_preview}{ANSI['reset']}"
            )

    def tool_call_result(self, tool_name: str, elapsed: float, ok: bool, preview: str) -> None:
        status = "ok" if ok else "fail"
        color = BRAND["success"] if ok else BRAND["error"]
        snippet = (preview or "").replace("\n", " ").strip()[:48]
        if self.rich:
            self._console.print(
                f"    [{color}]{status}[/] "
                f"{self._dim(f'{elapsed:.1f}s')}" + (f"  {self._dim(snippet)}" if snippet else "")
            )
        else:
            c = ANSI["green"] if ok else ANSI["red"]
            print(
                f"    {c}{status}{ANSI['reset']} "
                f"{ANSI['dim']}{elapsed:.1f}s{ANSI['reset']}"
                + (f"  {ANSI['dim']}{snippet}{ANSI['reset']}" if snippet else "")
            )

    # ── Messages ───────────────────────────────────────────────────

    def info(self, msg: str) -> None:
        self._status_line(msg)

    def warn(self, msg: str) -> None:
        if self.rich:
            self._console.print(f"  [{BRAND['warn']}]! {msg}[/]")
        else:
            print(f"  {ANSI['yellow']}! {msg}{ANSI['reset']}")

    def error(self, msg: str) -> None:
        if self.rich:
            self._console.print(f"  [{BRAND['error']}]x {msg}[/]")
        else:
            print(f"  {ANSI['red']}x {msg}{ANSI['reset']}")

    def success(self, msg: str) -> None:
        if self.rich:
            self._console.print(f"  [{BRAND['success']}]✓ {msg}[/]")
        else:
            print(f"  {ANSI['green']}✓ {msg}{ANSI['reset']}")

    # ── Help ───────────────────────────────────────────────────────

    def show_help(self) -> None:
        cmds = SLASH_COMMANDS
        if self.rich:
            table = Table(box=None, show_header=False, padding=(0, 2))
            table.add_column(style=BRAND["primary"], no_wrap=True)
            table.add_column(style="white")
            for cmd, desc in cmds:
                table.add_row(cmd, desc)
            self._console.print()
            self._console.print(f"  {self._primary('Commands')}")
            self._console.print(table)
            self._console.print()
        else:
            print(f"\n{ANSI['bold']}{ANSI['cyan']}  Commands{ANSI['reset']}")
            for cmd, desc in cmds:
                print(f"  {ANSI['cyan']}{cmd:<12}{ANSI['reset']} {desc}")
            print()

    def show_tools(self, tools: list[str]) -> None:
        if self.rich:
            self._console.print(f"\n  {self._primary('Tools')} {self._dim(f'({len(tools)})')}")
            # Compact multi-column-ish list
            for name in tools:
                self._console.print(f"    {self._dim('•')} {name}")
            self._console.print()
        else:
            print(
                f"\n  {ANSI['bold']}{ANSI['cyan']}Tools{ANSI['reset']} "
                f"{ANSI['dim']}({len(tools)}){ANSI['reset']}"
            )
            for name in tools:
                print(f"    {ANSI['dim']}•{ANSI['reset']} {name}")
            print()

    def show_models(
        self,
        sections: list[dict],
        *,
        current_model: str = "",
        current_provider: str = "",
        numbered: bool = True,
    ) -> dict[int, tuple[str, str]]:
        """Professional model catalog — Panel + Table (Rich) or boxed ANSI fallback.

        Returns a flat ``{number: (provider_id, model_id)}`` map so callers can
        resolve ``/model 12`` without re-walking the catalog. Provider id is the
        raw key (e.g. ``opencode``) derived from the group header when possible;
        otherwise the display label lower-cased.

        ``sections`` is a JSON-shaped list::
            [{"tier": "free"|"paid", "label": str,
              "groups": [{"provider": str, "models": [str, ...]}, ...]}, ...]
        Built by main.model_catalog().
        """
        # ── Build flat index ──────────────────────────────────────────
        flat: dict[int, tuple[str, str]] = {}
        idx = 0

        def _provider_key(label: str) -> str:
            low = label.lower()
            if "opencode" in low:
                return "opencode"
            if "openrouter" in low:
                return "openrouter"
            if "ollama" in low:
                return "ollama"
            if "openai" in low:
                return "opencode"
            if "z.ai" in low or low.strip().startswith("zai"):
                return "zai"
            if "groq" in low:
                return "groq"
            if "google" in low or "gemini" in low or "gemma" in low:
                return "google"
            if "cline" in low:
                return "cline-usage" if "usage" in low else "clinepass"
            if "deepseek" in low:
                return "deepseek"
            return label.split()[0].lower()

        # Pre-walk to populate flat (used by both render paths + caller)
        for section in sections or []:
            for group in section.get("groups", []) or []:
                prov_label = str(group.get("provider", ""))
                pkey = _provider_key(prov_label)
                # allow explicit provider key from catalog builder
                if group.get("provider_key"):
                    pkey = str(group["provider_key"])
                for m in group.get("models", []) or []:
                    idx += 1
                    flat[idx] = (pkey, str(m))

        cur_norm = (current_model or "").strip().lower()

        # ── Rich path ─────────────────────────────────────────────────
        if self.rich:
            try:
                from rich import box
                from rich.panel import Panel

                self._console.print()

                header_text = Text()
                header_text.append("  Model Catalog", style=f"bold {BRAND['primary']}")
                header_text.append("  ·  free · $0  ·  fuzzy search", style=BRAND["muted"])
                if cur_norm:
                    header_text.append(f"  ·  active: {current_model}", style=BRAND["muted"])
                self._console.print(header_text)

                for section in sections or []:
                    tier = section.get("tier", "free")
                    label = section.get("label", "Free")
                    # Title bar colour by tier
                    tier_color = BRAND["success"] if tier == "free" else BRAND["warn"]
                    tier_dot = "●" if tier == "free" else "○"

                    table = Table(
                        box=box.ROUNDED,
                        show_header=True,
                        header_style=f"bold {BRAND['muted']}",
                        border_style=BRAND["muted"],
                        padding=(0, 1),
                        expand=False,
                    )
                    table.add_column(
                        "#", justify="right", style=BRAND["muted"], no_wrap=True, width=4
                    )
                    table.add_column(
                        "Model",
                        style=BRAND["primary"],
                        no_wrap=False,
                        overflow="fold",
                        min_width=22,
                    )
                    table.add_column("Provider", style="white", no_wrap=True)
                    table.add_column("Status", justify="center", no_wrap=True, width=10)

                    n = 0
                    for group in section.get("groups", []) or []:
                        prov_label = str(group.get("provider", ""))
                        pkey = _provider_key(prov_label)
                        if group.get("provider_key"):
                            pkey = str(group["provider_key"])
                        # Derive availability from label's ✓ / (needs …) suffix
                        is_available = "✓" in prov_label
                        needs_key = "needs" in prov_label.lower()
                        # Strip suffix for the Provider column (keep clean name)
                        prov_clean = prov_label.replace(" ✓", "").replace("✓", "").strip()
                        # Remove trailing "(needs …)" for cleaner column but keep status icon
                        if " (needs" in prov_clean:
                            prov_clean = prov_clean.split(" (needs")[0].strip()

                        for m in group.get("models", []) or []:
                            n += 1
                            # Find global index for this model (first match)
                            g_idx = next(
                                (k for k, v in flat.items() if v[1] == m and v[0] == pkey), n
                            )
                            is_current = m.lower() == cur_norm
                            status = ""
                            status_style = BRAND["muted"]
                            if is_current:
                                status = "◀ active"
                                status_style = BRAND["success"]
                            elif is_available:
                                status = "✓ ready"
                                status_style = BRAND["success"]
                            elif needs_key:
                                status = "needs key"
                                status_style = BRAND["warn"]
                            else:
                                status = "—"

                            num_txt = Text(
                                str(g_idx),
                                style=f"bold {BRAND['success']}" if is_current else BRAND["muted"],
                            )
                            model_txt = Text(
                                m,
                                style=(
                                    f"bold {BRAND['primary']}" if is_current else BRAND["primary"]
                                ),
                            )
                            if is_current:
                                model_txt.append("  ◀", style=BRAND["success"])
                            prov_txt = Text(prov_clean, style="white")
                            status_txt = Text(status, style=status_style)
                            table.add_row(num_txt, model_txt, prov_txt, status_txt)

                    title = Text()
                    title.append(f" {tier_dot} ", style=tier_color)
                    title.append(label, style=f"bold {tier_color}")

                    panel = Panel(
                        table,
                        title=title,
                        title_align="left",
                        border_style=BRAND["muted"],
                        box=box.ROUNDED,
                        padding=(0, 1),
                    )
                    self._console.print(panel)

                # Footer hints — single dim line, no wall of text
                self._console.print(
                    f"  {self._dim('Pick:')} {self._primary('/model 12')} {self._dim('or')} {self._primary('/model nemotron')} {self._dim('· fuzzy: kimi, qwen, spark, gpt ·')} {self._primary('/model free')} {self._dim('for ready-to-use only')}"
                )
                self._console.print(
                    f"  {self._dim('Tip: just type part of the name — it knows what you want  ·  Enter without args to browse')}"
                )
                self._console.print()
                return flat
            except Exception:
                # Fall through to ANSI fallback on any Rich error
                pass

        # ── ANSI fallback ─────────────────────────────────────────────
        print(
            f"\n  {ANSI['bold']}{ANSI['cyan']}Model Catalog{ANSI['reset']}  {ANSI['dim']}· free · $0 · fuzzy search{ANSI['reset']}"
        )
        if cur_norm:
            print(f"  {ANSI['dim']}active: {current_model}{ANSI['reset']}")
        for section in sections or []:
            tier = section.get("tier", "free")
            label = section.get("label", "")
            c = ANSI["green"] if tier == "free" else ANSI["yellow"]
            dot = "●" if tier == "free" else "○"
            print(f"\n  {c}{dot} {label}{ANSI['reset']}")
            print(f"  {ANSI['dim']}{'─'*56}{ANSI['reset']}")
            print(
                f"  {ANSI['dim']} {'#':>3}  {'Model':<28} {'Provider':<18} {'Status'}{ANSI['reset']}"
            )
            print(f"  {ANSI['dim']} {'─'*3}  {'─'*28} {'─'*18} {'─'*8}{ANSI['reset']}")
            g = 0
            for group in section.get("groups", []) or []:
                prov_label = str(group.get("provider", ""))
                prov_clean = prov_label.replace(" ✓", "").replace("✓", "").strip()
                if " (needs" in prov_clean:
                    prov_clean = prov_clean.split(" (needs")[0].strip()
                is_available = "✓" in prov_label
                for m in group.get("models", []) or []:
                    g += 1
                    g_idx = next((k for k, v in flat.items() if v[1] == m), g)
                    is_cur = m.lower() == cur_norm
                    status = (
                        "◀ active"
                        if is_cur
                        else (
                            "✓ ready"
                            if is_available
                            else "needs key" if "needs" in prov_label.lower() else "—"
                        )
                    )
                    sc = ANSI["green"] if is_cur or is_available else ANSI["dim"]
                    cur_mark = f" {ANSI['green']}◀{ANSI['reset']}" if is_cur else ""
                    print(
                        f"  {ANSI['dim']}{g_idx:>3}{ANSI['reset']}  {ANSI['cyan']}{m:<28}{ANSI['reset']} {prov_clean:<18} {sc}{status}{ANSI['reset']}{cur_mark}"
                    )
        print(
            f"\n  {ANSI['dim']}Pick: {ANSI['reset']}{ANSI['cyan']}/model 12{ANSI['reset']} {ANSI['dim']}or {ANSI['reset']}{ANSI['cyan']}/model nemotron{ANSI['reset']} {ANSI['dim']}· fuzzy: kimi, qwen, spark, gpt{ANSI['reset']}"
        )
        print(
            f"  {ANSI['dim']}Tip: type part of the name — it knows what you want{ANSI['reset']}\n"
        )
        return flat

    # ── Input prompt ───────────────────────────────────────────────

    @staticmethod
    def _drain_pending_stdin(first_line: str) -> str:
        """If text was just pasted, the remaining lines are already sitting in
        the stdin buffer. Read them all and return the full multi-line text.

        Uses ``select`` with a zero timeout to detect buffered input without
        blocking. On Windows, ``select`` doesn't work on console handles, so
        we use ``msvcrt`` to detect a pending paste and then read whole lines
        from ``sys.stdin`` (which by then contains the pasted tail).
        """
        lines = [first_line]

        if _IS_WINDOWS and msvcrt is not None:
            # If kbhit() is True right after input() returned, the user pasted
            # and the tail is waiting in the console buffer.
            if not msvcrt.kbhit():
                return first_line
            # Give the console a tick to flush the full paste into stdin.
            time.sleep(0.05)
            # Read until the buffer is empty AND we've hit a line boundary.
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
                # Small grace period: if more chars arrive immediately, keep
                # reading. Otherwise we've consumed the whole paste.
                time.sleep(0.01)
                if not msvcrt.kbhit():
                    break
            return "\n".join(lines)

        # POSIX path — select on stdin works.
        try:
            import select as _select

            while _select.select([sys.stdin], [], [], 0.0)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
        except (OSError, ValueError, ImportError):
            pass

        return "\n".join(lines)

    def prompt(self) -> str:
        if self.rich:
            from rich.prompt import Prompt

            first = Prompt.ask(
                f"[{BRAND['primary']}]›[/{BRAND['primary']}]",
                console=self._console,
            )
            return self._drain_pending_stdin(first)
        try:
            first = input(f"{ANSI['bold']}{ANSI['cyan']}› {ANSI['reset']}")
        except (EOFError, KeyboardInterrupt):
            return ""
        return self._drain_pending_stdin(first)

    def prompt_text(self, prompt: str, default: str = "") -> str:
        """Prompt for a single line with custom text (Rich or ANSI fallback)."""
        if self.rich:
            try:
                from rich.prompt import Prompt

                return Prompt.ask(
                    f"[{BRAND['muted']}]{prompt}[/]",
                    default=default,
                    console=self._console,
                    show_default=False,
                )
            except Exception:
                pass
        try:
            suffix = f" [{default}]" if default else ""
            raw = input(f"{ANSI['dim']}{prompt}{suffix}: {ANSI['reset']}")
            return raw.strip() or default
        except (EOFError, KeyboardInterrupt):
            return ""


# ── Web UI (browser front end over WebSocket) ────────────────────────────────


class WebUI:
    """Drop-in replacement for TerminalUI that streams JSON events to the
    browser over a WebSocket instead of printing to the terminal.

    The event sink is attached per connection by web_gui.serve(). Every method
    is a safe no-op while no browser is connected, and safe to call from any
    thread (agent loop, hook worker threads) — the sink handles scheduling.
    """

    def __init__(self) -> None:
        self.rich = False
        self._send = None
        self._stream_buffer = ""
        self._think_buffer = ""
        self._streaming = False
        self._thinking = False
        self._tool_count = 0
        self._session_start = time.time()
        self._cost_summary = ""
        self._project_name = ""
        self._provider_name = ""
        self._model_name = ""

    # ── Connection wiring (called by web_gui) ──────────────────────

    def attach(self, send) -> None:
        """Attach the per-connection event sink: ``send(event_dict) -> None``."""
        self._send = send

    def detach(self) -> None:
        self._send = None

    def _emit(self, event: dict) -> None:
        send = self._send
        if send is None:
            return
        with contextlib.suppress(Exception):
            send(event)

    # ── Session info ─────────────────────────────────────────────

    def set_session_info(
        self,
        project_name: str = "",
        provider: str = "",
        cost: str = "",
        model: str = "",
    ) -> None:
        """Update session state and push it to the browser status bar."""
        if project_name:
            self._project_name = project_name
        if provider:
            self._provider_name = provider
        if cost:
            self._cost_summary = cost
        if model:
            self._model_name = model
        self._emit(
            {
                "type": "session",
                "project": self._project_name,
                "provider": self._provider_name,
                "model": self._model_name,
                "cost": self._cost_summary,
            }
        )

    # ── Banner / exit ────────────────────────────────────────────

    def enhanced_banner(self) -> None:
        """Startup banner → session event + tip in the browser."""
        self.set_session_info()  # re-emit current state
        self._emit(
            {"type": "status", "level": "info", "text": "Type a task, or /help for commands"}
        )

    banner = enhanced_banner

    def goodbye(self, cost_summary: str = "") -> None:
        parts = [f"{self._tool_count} tools"]
        if cost_summary:
            parts.append(cost_summary)
        elapsed = int(time.time() - self._session_start)
        if elapsed >= 60:
            parts.append(f"{elapsed // 60}m {elapsed % 60}s")
        elif elapsed > 0:
            parts.append(f"{elapsed}s")
        self._emit({"type": "goodbye", "text": " · ".join(parts)})

    # ── Streaming ────────────────────────────────────────────────

    @property
    def streamed_chars(self) -> int:
        """How many characters were actually streamed (not counting thinking)."""
        return len(self._stream_buffer)

    def start_streaming(self) -> None:
        self._stream_buffer = ""
        self._streaming = True
        self._thinking = False
        self._think_buffer = ""
        self._emit({"type": "stream_start"})

    def stream_token(self, token: str) -> None:
        if not self._streaming:
            self.start_streaming()
        self._stream_buffer += token
        self._emit({"type": "token", "text": token})

    def end_streaming(self) -> None:
        self._streaming = False
        if self._thinking:
            self.end_thinking()
        self._emit({"type": "stream_end"})

    def show_question(self, question: str) -> None:
        pass  # the browser renders its own user bubble

    def begin_thinking(self) -> None:
        self._thinking = True
        self._think_buffer = ""
        self._emit({"type": "think_start"})

    def stream_think_token(self, token: str) -> None:
        if not self._thinking:
            self.begin_thinking()
        self._think_buffer += token
        self._emit({"type": "thinking", "text": token})

    def end_thinking(self) -> None:
        self._thinking = False
        self._emit({"type": "think_end"})

    def play_done_sound(self) -> None:
        pass

    def finish_response(self, full_text: str) -> None:
        if self._streaming:
            return
        if full_text:
            self.markdown(full_text)

    # ── Markdown ─────────────────────────────────────────────────

    def markdown(self, text: str) -> None:
        if text:
            self._emit({"type": "markdown", "text": text})

    # ── Tool calls ───────────────────────────────────────────────

    @staticmethod
    def _clean_args(args: dict | None) -> dict:
        """JSON-safe copy of tool args, internal keys stripped, values truncated."""
        clean: dict = {}
        for k, v in (args or {}).items():
            if str(k).startswith("_"):
                continue
            if isinstance(v, (int, float, bool)) or v is None:
                clean[k] = v
            else:
                s = str(v)
                clean[k] = s[:400] + "…" if len(s) > 400 else s
        return clean

    def tool_call_start(self, tool_name: str, args: dict | None = None) -> None:
        self._tool_count += 1
        self._emit({"type": "tool_start", "name": tool_name, "args": self._clean_args(args)})

    def tool_call_result(self, tool_name: str, elapsed: float, ok: bool, preview: str) -> None:
        self._emit(
            {
                "type": "tool_result",
                "name": tool_name,
                "elapsed": round(float(elapsed or 0.0), 2),
                "ok": bool(ok),
                "preview": (preview or "")[:400],
            }
        )

    # ── Messages ─────────────────────────────────────────────────

    def info(self, msg: str) -> None:
        self._emit({"type": "status", "level": "info", "text": msg})

    def warn(self, msg: str) -> None:
        self._emit({"type": "status", "level": "warn", "text": msg})

    def error(self, msg: str) -> None:
        self._emit({"type": "status", "level": "error", "text": msg})

    def success(self, msg: str) -> None:
        self._emit({"type": "status", "level": "success", "text": msg})

    # ── Displays ─────────────────────────────────────────────────

    def show_help(self) -> None:
        self._emit({"type": "help", "commands": [[c, d] for c, d in SLASH_COMMANDS]})

    def show_tools(self, tools: list[str]) -> None:
        self._emit({"type": "tools", "tools": list(tools)})

    def show_models(
        self,
        sections: list[dict],
        *,
        current_model: str = "",
        current_provider: str = "",
        numbered: bool = True,
    ) -> None:
        """Send the tiered model catalog to the browser's models panel."""
        self._emit({"type": "models", "sections": sections})

    # ── Input prompt ─────────────────────────────────────────────

    def prompt(self) -> str:
        return ""  # input arrives over the WebSocket, never via stdin


# ── Global instance ──────────────────────────────────────────────────

ui = TerminalUI()
