#!/usr/bin/env python3
"""
LuckyD Code — AI coding agent with streaming and rich terminal UI.

Usage:
  lucky-code                        Interactive REPL
  lucky-code "fix the bug"          One-shot
  lucky-code --model auto --thinking
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

# Ensure the coding-agent dir is on the path
AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(AGENT_DIR))

# Load .env FIRST -- it sets CODING_AGENT_PROVIDER / *_API_KEY that
# core/providers.py reads from os.environ. Importing config before any
# other module that pulls in providers guarantees the .env provider/key win.
import config as _config  # noqa: F401  (runs load_env() on import)
from agent import CodingAgent
from config import PROJECT_DIR, get_config
from core.approval_hook import ApprovalHook
from core.hooks import get_hooks, register_plugin
from core.mcp_client import MCPManager
from core.session_store import get_session_store
from model_resolver import invalidate_cache, resolve_model
from tools.registry import registry
from ui import ui

_PROVIDER_DISPLAY_NAMES = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "ollama": "Ollama",
    "zai": "Z.ai",
    "openrouter": "OpenRouter",
    "clinepass": "ClinePass",
    "cline-usage": "Cline (usage)",
}

# ── Cline model catalogs ───────────────────────────────────────────────
# Mirror of browser/browser_core/ai_bridge.py — keep the two in sync.
# Source of truth: https://docs.cline.bot/getting-started/clinepass
# (api.cline.bot has no public model-list endpoint, so these are curated.)

# ClinePass flat-subscription models — these work with a $0 (even negative)
# credit balance; usage counts against the subscription quota.
_CLINEPASS_CATALOG = [
    "cline-pass/kimi-k3",
    "cline-pass/deepseek-v4-flash",
    "cline-pass/kimi-k2.7-code",
    "cline-pass/kimi-k2.6",
    "cline-pass/deepseek-v4-pro",
    "cline-pass/mimo-v2.5",
    "cline-pass/mimo-v2.5-pro",
    "cline-pass/minimax-m3",
    "cline-pass/qwen3.7-max",
    "cline-pass/qwen3.7-plus",
]

# Cline Usage (credit-billed / free tier) — same gateway, usage-based billing.
# Free-tier models work at $0.00 but are rate-limited; credit models deduct
# from your Cline Credits balance.
_CLINE_USAGE_CATALOG = [
    # ── Free tier (rate-limited, $0.00 — needs non-negative credit balance)
    "minimax/minimax-m2.5",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-r1",
    "meta-llama/llama-3.2-3b-instruct",
    "google/gemini-2.0-flash",
    "qwen/qwen3-8b",
    # ── Credit-billed — deduct from Cline Credits balance
    "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "mistral/mistral-large",
]

_CLINE_USAGE_FREE_TIER = frozenset(_CLINE_USAGE_CATALOG[:6])

# ClinePass has no free-tier model — every ClinePass model counts against the
# flat-subscription quota.
_CLINEPASS_FREE_TIER = frozenset()

# Aliases accepted in "/model <provider> <name>" on top of canonical names.
_PROVIDER_ALIASES = {
    "cline-pass": "clinepass",
    "cline": "cline-usage",
}


def model_catalog() -> list[dict]:
    """Tiered model catalog for ui.show_models() and the web /api/models panel.

    Returns a list of sections, each with a cost ``tier`` ("free" | "paid"),
    a human ``label``, and ``groups`` of {provider, models}. Free covers local
    Ollama + the Cline Usage rate-limited free tier; everything else is paid.
    """
    cline_free = [m for m in _CLINE_USAGE_CATALOG if m in _CLINE_USAGE_FREE_TIER]
    cline_paid = [m for m in _CLINE_USAGE_CATALOG if m not in _CLINE_USAGE_FREE_TIER]
    clinepass_paid = list(_CLINEPASS_CATALOG)
    return [
        {
            "tier": "free",
            "label": "Free — $0",
            "groups": [
                {"provider": "Ollama", "models": ["codellama", "llama3.1", "mistral", "phi3"]},
                {"provider": "Cline Usage (free tier)", "models": cline_free},
            ],
        },
        {
            "tier": "paid",
            "label": "Paid — costs money",
            "groups": [
                {"provider": "OpenAI", "models": ["gpt-4o", "gpt-4o-mini", "o1-preview", "o1-mini"]},
                {"provider": "Anthropic", "models": [
                    "claude-sonnet-4-20250514",
                    "claude-opus-4-20250514",
                    "claude-3-5-haiku-20241022",
                ]},
                {"provider": "Google", "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"]},
                {"provider": "DeepSeek", "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]},
                {"provider": "Z.ai", "models": ["glm-4.6", "glm-4.5", "glm-4.5-air"]},
                {"provider": "OpenRouter", "models": [
                    "deepseek/deepseek-chat-v3.1",
                    "anthropic/claude-sonnet-4",
                    "google/gemini-2.0-flash-001",
                ]},
                {"provider": "ClinePass (subscription)", "models": clinepass_paid},
                {"provider": "Cline Usage (credit-billed)", "models": cline_paid},
            ],
        },
    ]


def _cline_model_entries() -> list[tuple[str, str]]:
    """(provider, full model id) for every curated Cline gateway model."""
    return [("clinepass", m) for m in _CLINEPASS_CATALOG] + [
        ("cline-usage", m) for m in _CLINE_USAGE_CATALOG
    ]


def _match_cline_model(desired: str) -> tuple[str, str] | None:
    """Resolve a partial model name against the Cline catalogs.

    Returns (provider, full_model_id) on a confident match. On ambiguity or
    no match, prints the reason and returns None — never guesses a provider.
    """
    desired = desired.strip().lower()
    entries = _cline_model_entries()

    exact = [e for e in entries if e[1].lower() == desired]
    if exact:
        return exact[0]

    suffix = [e for e in entries if e[1].lower().endswith("/" + desired)]
    if len(suffix) == 1:
        return suffix[0]
    candidates = suffix

    if not candidates:
        candidates = [e for e in entries if desired in e[1].lower()]

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        ui.warn(f"Ambiguous model: {desired}  ->  matches:")
        for _p, m in candidates:
            print(f"  {m}")
        print("  Re-run with the full id, e.g. /model " + candidates[0][1])
        return None

    ui.warn(f"Unknown model: {desired}")
    print("  Pick from /model (no args), or force any model with:")
    print("    /model <provider> <name>   e.g. /model cline-usage openai/gpt-4o")
    print("  Providers: openai, anthropic, google, ollama, deepseek, zai,")
    print("             openrouter, clinepass (subscription), cline-usage (free/credits)")
    return None


def _prompt_and_save_api_key(env_var: str, provider_name: str) -> str:
    """Interactively prompt the user for an API key and persist it to .env."""
    ui.warn(f"No API key found for {provider_name}.")
    print(f"  Paste your {provider_name} API key below (or press Enter to cancel):")
    print(f"    {env_var}= ", end="")
    key = sys.stdin.readline().strip()
    if not key:
        ui.error("No key provided -- cannot continue without an API key.")
        sys.exit(1)

    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    key_prefix = f"{env_var}="
    replaced = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(key_prefix) and not stripped.startswith("#"):
            new_lines.append(f"{env_var}={key}\n")
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("\n")
        new_lines.append(f"# {provider_name}\n")
        new_lines.append(f"{env_var}={key}\n")

    env_path.write_text("".join(new_lines), encoding="utf-8")
    os.environ[env_var] = key
    ui.success(f"Saved {env_var} to .env  (available now -- no restart needed)")
    return key


def _resolve_provider(provider_hint: str | None, model_name: str) -> dict:
    """Build an LLMConfig for the requested provider+model.

    If a required API key is missing and we are in an interactive terminal,
    the user will be prompted to enter one.
    """
    cfg = get_config()

    # Map provider names to their alias + env vars
    provider_map = {
        "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"),
        "google": ("GOOGLE_API_KEY", "GOOGLE_BASE_URL", "GOOGLE_MODEL"),
        "ollama": (None, "OLLAMA_HOST", "OLLAMA_MODEL"),
        "zai": ("ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL"),
        "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL"),
        "clinepass": ("CLINEPASS_API_KEY", "CLINEPASS_BASE_URL", "CLINEPASS_MODEL"),
        # Same gateway + auth as ClinePass; only the model env var differs.
        "cline-usage": ("CLINEPASS_API_KEY", "CLINEPASS_BASE_URL", "CLINE_USAGE_MODEL"),
    }

    if provider_hint and provider_hint in provider_map:
        env_key, env_base, env_model = provider_map[provider_hint]
        api_key = os.environ.get(env_key, "") if env_key else ""
        # ClinePass: no key set -> try the logged-in Cline CLI session before
        # ever prompting for a raw API key (mirrors core/providers.py).
        if provider_hint in ("clinepass", "cline-usage") and not api_key:
            try:
                import sys as _sys
                from pathlib import Path as _Path

                try:
                    import cline_session  # type: ignore
                except ImportError:
                    bc = str(_Path(__file__).resolve().parent / "browser" / "browser_core")
                    if bc not in _sys.path:
                        _sys.path.insert(0, bc)
                    import cline_session  # type: ignore
                api_key = cline_session.fresh_token()
            except Exception as exc:
                ui.warn(f"ClinePass session unavailable ({exc}).")
        # If a key-required provider is still missing a key, prompt interactively
        if env_key and not api_key and sys.stdin.isatty():
            api_key = _prompt_and_save_api_key(
                env_key,
                _PROVIDER_DISPLAY_NAMES.get(provider_hint, provider_hint),
            )
        _base_defaults = {
            "clinepass": "https://api.cline.bot/api/v1",
            "cline-usage": "https://api.cline.bot/api/v1",
        }
        base_url = os.environ.get(env_base, "") or _base_defaults.get(provider_hint, "")
        _model_defaults = {
            "clinepass": "cline-pass/kimi-k3",
            "cline-usage": "deepseek/deepseek-chat",
        }
        resolved_model = (
            model_name
            or os.environ.get(env_model, "")
            or _model_defaults.get(provider_hint, "")
        )
        if not resolved_model:
            ui.warn(f"{provider_hint} model name required. Try: /model {provider_hint} <name>")
            return None
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": resolved_model,
            "provider": provider_hint,
            "thinking": False,
        }

    # No provider hint — try to detect from model name itself
    model_lower = (model_name or "").lower()
    for p in ("openai", "anthropic", "google", "ollama"):
        if model_lower.startswith(p):
            return _resolve_provider(p, model_name[len(p) + 1 :].strip())

    # Default: DeepSeek current config
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODING_AGENT_API_KEY", "")
    if not api_key and sys.stdin.isatty():
        api_key = _prompt_and_save_api_key("DEEPSEEK_API_KEY", "DeepSeek")
    return {
        "api_key": api_key,
        "base_url": cfg.get("base_url", "https://api.deepseek.com/v1"),
        "model": model_name or cfg.get("model", "deepseek-chat"),
        "provider": "deepseek",
        "thinking": cfg.get("thinking", False),
    }


def _switch_model(agent, provider: str | None = None, model_name: str = ""):
    """Switch agent to a new provider and/or model at runtime."""
    from llm import LLMConfig

    new_cfg = _resolve_provider(provider, model_name)
    if not new_cfg:
        return

    provider = new_cfg["provider"]
    model = new_cfg["model"]

    # Update the agent via its public API (no poking at internals)
    agent.switch_provider(
        LLMConfig(
            api_key=new_cfg["api_key"],
            base_url=new_cfg["base_url"],
            model=model,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            provider=provider,
            thinking=new_cfg.get("thinking", False),
        )
    )

    # Update the UI session info
    ui.set_session_info(
        project_name=(
            agent._project_info.name
            if agent._project_info and not agent._project_info.is_empty()
            else ""
        ),
        provider=agent.provider_name,
        model=model,
    )
    ui.success(f"Switched to {agent.provider_name} / {model}")


# ── Slash commands ────────────────────────────────────────────────────

# ── Approval callback ───────────────────────────────────────────────


def _console_approval(request) -> type(None):
    """Prompt user for tool approval in REPL. Returns None to proceed; returning a dict blocks the tool.

    Note: When using the interactive REPL, type your response when you see the box below.
    For non-interactive use, run with --auto-approve or set CODING_AGENT_AUTO_APPROVE=1.
    """
    from core.types import ToolPermissionLevel

    preview = request.tool_args.get("command", request.tool_name)[:120]
    print(f"\n  [APPROVAL] {request.tool_name}: {preview}")
    print("  " + "=" * 57)
    print("  | This tool requires your permission to execute.            |")
    print("  |                                                           |")
    print("  |  Type:  y or yes   to Approve this call                   |")
    print("  |         n or no    to Deny this call                      |")
    print("  |         a          to Always Allow (auto-approve)         |")
    print("  " + "=" * 57)
    sys.stdout.flush()
    ans = sys.stdin.readline().strip().lower() or "n"
    if ans == "a":
        for p in get_hooks().before_tool:
            if hasattr(p, "set_permission"):
                p.set_permission(request.tool_name, ToolPermissionLevel.ALWAYS_ALLOW)
                break
        print("  > Tool auto-approved for future calls")
        return None
    if ans in ("y", "yes"):
        return None
    print("  > Tool execution denied.")
    return {
        "role": "tool",
        "tool_call_id": request.call_id,
        "content": "Tool execution denied by user.",
    }

async def handle_command(agent: CodingAgent, cmd: str) -> bool:
    """
    Handle a slash command. Returns True if the REPL should exit,
    False otherwise.
    """
    cmd = cmd[1:].strip().lower()  # strip leading '/'

    if cmd in ("q", "quit", "exit"):
        ui.goodbye()
        return True

    elif cmd in ("h", "help"):
        ui.show_help()

    elif cmd == "clear":
        agent.reset()
        # Also clear the terminal screen so you truly start fresh
        os.system("cls" if os.name == "nt" else "clear")
        project_name = (
            agent._project_info.name
            if agent._project_info and not agent._project_info.is_empty()
            else ""
        )
        ui.set_session_info(
            project_name=project_name,
            provider=agent.provider_name,
            model=getattr(agent, "model", "") or "",
        )
        ui.enhanced_banner()

    elif cmd == "history":
        ui.markdown(
            f"**Conversation:** {agent.conversation_id}\n**Turns:** {agent.turn_count}\n**Messages:** {len(agent.messages)}"
        )

    elif cmd == "tools":
        ui.show_tools(sorted(registry.list_tools()))

    elif cmd == "memory":
        try:
            from memory.store import get_memory

            ui.markdown(get_memory().summarize())
        except Exception as e:
            ui.error(f"Memory error: {e}")

    elif cmd.startswith("model"):
        parts = cmd.split(maxsplit=1)
        if len(parts) > 1:
            raw = parts[1].strip()
            # Parse "provider model_id" or just "model_id"
            provider = None
            desired = raw
            for p in (
                "cline-usage", "cline-pass", "clinepass", "cline",
                "openrouter", "anthropic", "deepseek", "openai",
                "google", "ollama", "zai",
            ):
                if raw.lower().startswith(p + " "):
                    provider = _PROVIDER_ALIASES.get(p, p)
                    desired = raw[len(p) + 1 :].strip()
                    break
            if provider:
                _switch_model(agent, provider=provider, model_name=desired)
            else:
                # No provider given — resolve against the Cline catalogs so
                # "/model kimi-k3" -> clinepass, "/model deepseek-chat" ->
                # cline-usage. Unknown names are rejected, never guessed.
                hit = _match_cline_model(desired)
                if hit:
                    _switch_model(agent, provider=hit[0], model_name=hit[1])
        else:
            ui.info(f"Model: {agent.model}")
            ui.show_models(model_catalog())

    elif cmd == "refresh":
        invalidate_cache()
        _switch_model(agent, model_name="auto")
        ui.success(f"Cache cleared. Model: {agent.model}")

    elif cmd == "save":
        save_path = PROJECT_DIR / f"conversation_{agent.conversation_id}.json"
        save_path.write_text(json.dumps(agent.messages, indent=2, default=str))
        ui.success(f"Saved to: {save_path}")

    elif cmd == "cost":
        ui.markdown(agent.cost_tracker.summary())

    elif cmd == "undo":
        from core.checkpoint import get_checkpoint_manager

        cm = get_checkpoint_manager()
        diff = cm.undo_last()
        if diff:
            adds = diff.additions
            dels = diff.deletions
            ui.success(f"Undid changes to {diff.file_path} ({adds}+ / {dels}-)")
        else:
            ui.warn("Nothing to undo")

    elif cmd == "sessions":
        sessions = get_session_store().list(limit=10)
        if not sessions:
            ui.info("No saved sessions")
        else:
            parts = []
            for s in sessions:
                sid = s.get("conversation_id", "?")
                ts = (s.get("updated_at") or "")[-19:] if s.get("updated_at") else "---"
                prev = (s.get("preview") or "")[:80]
                mod = s.get("model", "?")
                parts.append(f"  {sid}  {ts}  {mod}  | {prev}")
            ui.markdown("**Recent Sessions:**\n" + "\n".join(parts))

    elif cmd.startswith("resume"):
        parts = cmd.split(maxsplit=1)
        sid = parts[1] if len(parts) > 1 else "latest"
        sess = get_session_store().latest() if sid == "latest" else get_session_store().load(sid)
        if sess:
            agent.restore_session(sess)
            prev = (sess.get("preview") or "")[:80]
            ui.success(f"Resumed: {prev}")
        else:
            ui.error(f"Session not found: {sid}")

    elif cmd == "mcp":
        mgr = getattr(agent, "_mcp_manager", None)
        if mgr and mgr.is_connected:
            ui.markdown("**MCP Status**\n" + mgr.status_report())
        else:
            ui.warn("MCP not configured or no servers connected")

    elif cmd == "version":
        ui.info("LuckyD Code 2.1.0")

    elif cmd == "":
        pass  # Empty command

    else:
        ui.warn(f"Unknown command: /{cmd}. Try /help")

    return False


# ── Main application ──────────────────────────────────────────────────


async def run_one_shot(agent: CodingAgent, message: str):
    """Single query mode with streaming."""
    agent.stream_callback = ui.stream_token
    agent.think_callback = ui.stream_think_token
    ui.start_streaming()
    result = ""
    try:
        result = await agent.run(message)
    except Exception as e:
        ui.error(f"Agent error: {e}")
        result = ""
    finally:
        ui.end_streaming()
    if result and not ui.streamed_chars:
        ui.markdown(result)


async def run_one_shot_json(agent: CodingAgent, message: str):
    """Single query mode -- clean JSON-line output for editor extensions."""
    agent.stream_callback = lambda token: (
        sys.stdout.write(json.dumps({"type": "token", "text": token}) + chr(10))
    )
    agent.think_callback = lambda token: (
        sys.stdout.write(json.dumps({"type": "thinking", "text": token}) + chr(10))
    )
    result = ""
    try:
        result = await agent.run(message)
    except Exception as e:
        json.dump({"type": "error", "text": str(e)}, sys.stdout)
        sys.stdout.write(chr(10))
        sys.stdout.flush()
        return
    finally:
        sys.stdout.flush()
    if result:
        json.dump({"type": "result", "text": result[:100000]}, sys.stdout)
        sys.stdout.write(chr(10))
    json.dump({"type": "done"}, sys.stdout)
    sys.stdout.write(chr(10))
    sys.stdout.flush()


async def run_repl(agent: CodingAgent):
    """Interactive REPL with streaming and session info."""

    # Connect MCP servers lazily in this event loop
    try:
        mcp_manager = getattr(agent, "_mcp_manager", None)
        if mcp_manager:
            n_srv = await mcp_manager.connect_all()
            if n_srv:
                from tools.mcp_tools import register_mcp_tools
                n_tools = register_mcp_tools(mcp_manager)
                print(f"  [MCP] {n_srv} server(s), {n_tools} tool(s) registered")
    except Exception as e:
        print(f"  [MCP] Connection: {e}")

    # Show enhanced banner with project info
    project_name = (
        agent._project_info.name
        if agent._project_info and not agent._project_info.is_empty()
        else ""
    )
    ui.set_session_info(
        project_name=project_name,
        provider=agent.provider_name,
        model=getattr(agent, "model", "") or "",
    )
    ui.enhanced_banner()

    while True:
        try:
            user_input = await asyncio.to_thread(ui.prompt)
        except (KeyboardInterrupt, EOFError):
            cost = agent.cost_tracker.summary() if hasattr(agent, "cost_tracker") else ""
            ui.goodbye(cost_summary=cost)
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            should_exit = await handle_command(agent, user_input)
            if should_exit:
                cost = agent.cost_tracker.summary() if hasattr(agent, "cost_tracker") else ""
                ui.goodbye(cost_summary=cost)
                break
            continue

        # Normal message — stream response
        agent.stream_callback = ui.stream_token
        agent.think_callback = ui.stream_think_token
        ui.start_streaming()
        result = ""
        try:
            result = await agent.run(user_input)
        except Exception as e:
            ui.error(f"Agent error: {e}")
            result = ""
        finally:
            ui.end_streaming()
        if result and not ui.streamed_chars:
            ui.markdown(result)


# ── "model" CLI subcommand ─────────────────────────────────────────────


def _cli_model(args):
    """luckyd-code model [list | <model-id>]

    Without arguments: show the current model from .env / resolved config.
    With 'list':       print the ClinePass + Cline Usage model catalog.
    With a model id:   validate it against the catalog (module-level
                       _CLINEPASS_CATALOG / _CLINE_USAGE_CATALOG) and write
                       the matching env vars — CLINEPASS_MODEL or
                       CLINE_USAGE_MODEL, plus CODING_AGENT_PROVIDER — to
                       .env permanently.
    """
    from config import ENV_FILE

    def _read_env_pairs() -> dict:
        pairs = {}
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                pairs[key.strip()] = val.strip().strip('"').strip("'")
        return pairs

    def _set_env_key(lines: list[str], key: str, value: str) -> list[str]:
        """Replace (in place) or append `key=value` in a list of .env lines."""
        prefix = key + "="
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(prefix) and not stripped.startswith("#"):
                lines[idx] = f"{key}={value}"
                return lines
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")
        return lines

    def _current_model():
        """Best-effort read of the active model identity."""
        pairs = _read_env_pairs()
        provider = pairs.get("CODING_AGENT_PROVIDER", "").lower()
        if provider == "cline-usage":
            if pairs.get("CLINE_USAGE_MODEL"):
                return pairs["CLINE_USAGE_MODEL"]
        elif pairs.get("CLINEPASS_MODEL"):
            return pairs["CLINEPASS_MODEL"]
        try:
            from core.providers import resolve_provider_config
            cfg = resolve_provider_config()
            return cfg.get("model", "unknown")
        except Exception:
            return "unknown"

    if not args or args == ["list"]:
        current = _current_model()
        print(f"Current model: {current}\n")
        if args == ["list"]:
            print("── ClinePass subscription models (flat rate) ─────────────────────")
            for m in _CLINEPASS_CATALOG:
                marker = " ◀ current" if m == current else ""
                print(f"  {m}{marker}")
            print("\n── Cline Usage — free tier ($0.00, rate-limited) ─────────────────")
            for m in _CLINE_USAGE_CATALOG:
                if m not in _CLINE_USAGE_FREE_TIER:
                    continue
                marker = " ◀ current" if m == current else ""
                print(f"  {m}{marker}")
            print("\n── Cline Usage — credit-billed ───────────────────────────────────")
            for m in _CLINE_USAGE_CATALOG:
                if m in _CLINE_USAGE_FREE_TIER:
                    continue
                marker = " ◀ current" if m == current else ""
                print(f"  {m}{marker}")
            print("\nSet one with:  lucky-code model <model-id>")
        else:
            print("Usage: lucky-code model list          — show full catalog")
            print("       lucky-code model <model-id>    — switch permanently")
        return

    # ── Switch model ───────────────────────────────────────────────────
    desired = args[0].strip()
    hit = _match_cline_model(desired)
    if not hit:
        print("Run: lucky-code model list  to see available models")
        return

    provider, picked = hit

    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()

    if provider == "clinepass":
        lines = _set_env_key(lines, "CLINEPASS_MODEL", picked)
        lines = _set_env_key(lines, "CODING_AGENT_PROVIDER", "clinepass")
    else:
        lines = _set_env_key(lines, "CLINE_USAGE_MODEL", picked)
        lines = _set_env_key(lines, "CODING_AGENT_PROVIDER", "cline-usage")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    if provider == "clinepass":
        tier = "ClinePass subscription"
    elif picked in _CLINE_USAGE_FREE_TIER:
        tier = "Cline Usage — free tier"
    else:
        tier = "Cline Usage — credit-billed"
    print(f"Model set to: {picked}  [{tier}]")
    print("Restart the terminal for the change to take effect.")


# ── Entry point ────────────────────────────────────────────────────────


def main():
    # Parse CLI args
    args = sys.argv[1:]

    # Dispatch "model" subcommand early (no API key needed)
    if args and args[0] == "model":
        _cli_model(args[1:])
        return

    cfg = get_config()
    model = cfg["model"]
    temperature = cfg["temperature"]
    one_shot = ""
    auto_approve = False
    resume_session_id = ""

    json_mode = False
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = resolve_model(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                preferred=args[i + 1],
                thinking=cfg.get("thinking", False),
            )
            i += 2
        elif args[i] == "--provider" and i + 1 < len(args):
            os.environ["CODING_AGENT_PROVIDER"] = args[i + 1]
            cfg = get_config()
            i += 2
        elif args[i] == "--thinking":
            os.environ["CODING_AGENT_THINKING"] = "true"
            cfg = get_config()
            model = cfg["model"]
            i += 1
        elif args[i] == "--temp" and i + 1 < len(args):
            temperature = float(args[i + 1])
            i += 2
        elif args[i] in ("-y", "--yes", "--auto-approve"):
            auto_approve = True
            os.environ["CODING_AGENT_AUTO_APPROVE"] = "1"
            i += 1
        elif args[i] == "--max-turns" and i + 1 < len(args):
            os.environ["CODING_AGENT_MAX_TURNS"] = args[i + 1]
            cfg = get_config()
            i += 2
        elif args[i] in ("-c", "--continue"):
            resume_session_id = "latest"
            i += 1
        elif args[i] == "--resume" and i + 1 < len(args):
            resume_session_id = args[i + 1]
            i += 2
        elif args[i] in ("-v", "--version"):
            print("LuckyD Code 2.1.0")
            sys.exit(0)
        elif args[i] == "--help":
            print(
                """
LuckyD Code — AI Coding Agent  v2.1.0

Usage:
  lucky-code                       Interactive REPL
  lucky-code "your query"          One-shot mode
  lucky-code -c                    Continue last session
  lucky-code --resume <id>         Resume specific session

Options:
  --model NAME       Model: auto (default), flash, pro, or specific name
  --provider NAME    Set provider: deepseek, openai, anthropic, google,
                     ollama, zai, openrouter
  --thinking         Use the thinking/reasoning model (pro)
  --temp FLOAT       Temperature (default: 0.0)
  -y, --yes          Auto-approve all tool calls (non-interactive mode)
  --max-turns N      Override max agent turns (default: 30)
  -c, --continue     Resume most recent session
  --resume <id>      Resume a specific session by ID or prefix
  --json             Structured JSON-line output (for extensions)
  -v, --version      Show version
  --help             Show this help

Environment:
  <PROVIDER>_API_KEY   Set in .env for your provider
  CODING_AGENT_PROVIDER Explicit provider override
  CODING_AGENT_AUTO_APPROVE=1   Bypass all tool approvals
"""
            )
            sys.exit(0)
        elif args[i] == "--json":
            json_mode = True
            i += 1
        else:
            one_shot = " ".join(args[i:])
            break

    # Validate API key
    provider = cfg.get("provider", "deepseek")
    if provider != "ollama" and (
        not cfg["api_key"] or cfg["api_key"] == "sk-your-api-key-here"
    ):
        from core.providers import PROVIDER_DEFAULTS, PROVIDER_NAMES

        provider_defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["deepseek"])
        env_var = provider_defaults.get("env_key") or (provider.upper() + "_API_KEY")
        display_name = PROVIDER_NAMES.get(provider, provider.title())

        if one_shot:
            # One-shot mode -- cannot interact; exit with instructions
            ui.error(f"{env_var} not set.")
            print(f"  Set it in {PROJECT_DIR / '.env'} or as an environment variable.")
            if provider == "deepseek":
                print("  Get a key: https://platform.deepseek.com/api_keys")
            elif provider in ("clinepass", "cline-usage"):
                print("  Run 'cline auth' to log into your Cline account, or")
                print("  set CLINEPASS_API_KEY in .env for a manual API key.")
            sys.exit(1)
        else:
            # REPL mode -- prompt the user interactively
            _prompt_and_save_api_key(env_var, display_name)
            # Re-resolve config with the new key and model
            cfg = get_config()
            model = cfg["model"]

    # Create agent
    agent = CodingAgent(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=model,
        temperature=temperature,
        max_tokens=cfg["max_tokens"],
    )

    # Wire approval hook (auto-approve all tools by default)
    hook = ApprovalHook(session_id=agent.conversation_id)
    hook.auto_approve_all = True
    register_plugin(hook)

    # Connect MCP servers (lazy: connected inside async loop when needed)
    mcp_manager = MCPManager()
    agent._mcp_manager = mcp_manager

    # Session resume
    if resume_session_id == "latest":
        sess = get_session_store().latest()
        if sess:
            agent.restore_session(sess)
            print(f"  [Session] Resumed: {(sess.get('preview') or '')[:60]}")
    elif resume_session_id:
        sess = get_session_store().load(resume_session_id)
        if sess:
            agent.restore_session(sess)
            print(f"  [Session] Resumed: {(sess.get('preview') or '')[:60]}")

    try:
        if one_shot:
            if json_mode:
                asyncio.run(run_one_shot_json(agent, one_shot))
            else:
                asyncio.run(run_one_shot(agent, one_shot))
        else:
            asyncio.run(run_repl(agent))
    finally:
        with contextlib.suppress(Exception):
            agent.save_session()
        try:
            loop = asyncio.new_event_loop()
            # Only close if there are connected clients
            if mcp_manager.is_connected:
                loop.run_until_complete(mcp_manager.close_all())
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
