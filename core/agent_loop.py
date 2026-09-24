"""
Agent loop — the main agent execution loop.
Replaces agent.py's run() method with a modular architecture using:
- core/llm_client.py for LLM API calls
- core/message_builder.py for system prompt construction
- core/context_manager.py for context compaction
- core/hooks.py for before/after hooks
- core/checkpoint.py for file snapshots
- core/types.py for typed events
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from config import PROJECT_DIR, get_config
from llm import CostTracker
from memory.store import get_memory
from tools.registry import registry

from .compaction import maybe_compact
from .goals import GoalStore, goal_from_session
from .hooks import get_hooks
from .llm_client import LLMClient
from .message_builder import MessageBuilder
from .rules_loader import load_project_rules
from .session_store import get_session_store
from .trajectory import TrajectoryRecorder
from .types import (
    AgentCallbacks,
    AgentEvent,
    AgentEventType,
    HookContext,
)

if TYPE_CHECKING:
    from llm import LLMConfig


log = logging.getLogger(__name__)


#: Valid ``permission_mode`` values for CodingAgent (LuckyD 9.7).
PERMISSION_MODES = ("default", "acceptEdits", "bypassPermissions", "auto", "off")

#: Conservative read-only tool-name prefixes for the 'off' permission mode.
#: A tool whose lowercase name starts with one of these is treated as
#: read-only and stays available when every state-modifying tool is blocked.
READONLY_NAME_PREFIXES = (
    "read",
    "get",
    "list",
    "search",
    "glob",
    "grep",
    "diff",
    "show",
    "fetch",
    "find",
    "status",
    "history",
    "info",
    "view",
)

#: Tool-name substrings marking file-editing tools for 'acceptEdits' mode.
EDIT_TOOL_KEYWORDS = ("edit", "write", "apply")

#: Tools advertised on every turn whatever the task: file/shell core,
#: planning, questions, task tracking, and delegation. Everything else is
#: task-scoped (see TASK_TOOL_KEYWORDS) to keep ~14K of schemas per turn
#: from drowning the model. Names are lowercase registry ids.
ALWAYS_ADVERTISED_TOOLS = frozenset(
    {
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "bash",
        "diff",
        "enterplanmode",
        "exitplanmode",
        "askuserquestion",
        "todoread",
        "todowrite",
        "taskcreate",
        "tasklist",
        "taskget",
        "taskupdate",
        "taskstop",
        "task_output",
        "task_stop",
        "subagent",
        "delegate_task",
        "harness",
        "brief",
        "findrelevantfiles",
    }
)

#: Task-text keyword -> tool-name prefixes advertised when the keyword
#: appears in the current user message or active goal. Substring match,
#: biased toward recall: a stray extra family costs tokens, a missing
#: one costs capability. Tools matching no family are always kept, so
#: dynamic (MCP-registered) tools can never be pruned away.
TASK_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "git": ("git",),
    "commit": ("git",),
    "branch": ("git",),
    "merge": ("git",),
    "pull request": ("git",),
    "web": ("web", "http"),
    "http": ("http", "web"),
    "url": ("web", "http", "browser"),
    "browse": ("browser", "web"),
    "research": ("websearch", "webfetch", "deepresearch"),
    "browser": ("browser", "webmcp"),
    "click": ("browser",),
    "tab": ("browser",),
    "webmcp": ("webmcp",),
    "desktop": ("desktop",),
    "mouse": ("desktop",),
    "keyboard": ("desktop",),
    "window": ("desktop",),
    "clipboard": ("desktop",),
    "screenshot": ("desktop", "browser"),
    "schedule": ("schedule",),
    "cron": ("schedule",),
    "remind": ("schedule",),
    "periodic": ("schedule",),
    "skill": ("skill",),
    "marketplace": ("skill",),
    "plugin": ("skill",),
    "lsp": ("lsp",),
    "definition": ("lsp",),
    "references": ("lsp",),
    "rename": ("lsp",),
    "symbol": ("lsp",),
    "memory": ("memory",),
    "remember": ("memory",),
    "recall": ("memory",),
    "forget": ("memory",),
    "csv": ("csv",),
    "sqlite": ("sqlite",),
    "database": ("sqlite", "csv"),
    "powershell": ("powershell",),
    "process": ("process",),
    "notify": ("notify",),
    "watch": ("watch",),
    "sleep": ("sleep",),
    "what time": ("datetime",),
    "what date": ("datetime",),
    "current time": ("datetime",),
    "handoff": ("agenthandoff",),
    "team": ("teamcreate", "listagents"),
    "message": ("sendmessage", "receivemessage"),
    "graph": ("graphify",),
}


# ── Classified retry for the provider HTTP layer (LuckyD 9.7) ───────────
# Policy: retryable = HTTP 429 / 5xx / timeouts / connection errors;
# fatal (fail fast) = 400 / 401 / 403 and other client errors.
#
# NOTE: these helpers live here because this change is scoped to
# core/agent_loop.py. Wiring _with_retry around LLMClient._http_post (the
# method that POSTs to {base_url}/chat/completions) needs an edit to
# core/llm_client.py — which already retries inline via retryable_codes,
# max_retries, and Retry-After support — so that wiring is left as a
# follow-up.


def _is_retryable(exc: Exception) -> bool:
    """True when a provider-call exception is worth retrying.

    Retryable: HTTP 429, any 5xx, timeouts, connection errors.
    Fatal (fail fast): 400/401/403 and other client errors.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        return status == 429 or 500 <= status <= 599
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


def _monotonic() -> float:
    """Clock indirection for the retry budget, so tests can time-travel
    without patching the global time module (which the event loop also uses)."""
    return time.monotonic()


def _with_retry(fn, max_retries: int = 5):
    """Wrap an async provider-call callable with classified retry.

    Exponential backoff 1s -> 30s (1, 2, 4, 8, 16, then capped at 30s),
    with a total elapsed budget of 120s. Each retry is logged at INFO.
    The wrapped callable keeps its public signature.
    """

    @functools.wraps(fn)
    async def _wrapper(*args, **kwargs):
        start = _monotonic()
        attempt = 0  # retries used so far
        while True:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                if not _is_retryable(exc) or attempt >= max_retries:
                    raise
                delay = min(2.0**attempt, 30.0)
                if _monotonic() - start + delay > 120.0:
                    log.warning(
                        "retry budget exhausted (120s elapsed cap); not retrying %s",
                        type(exc).__name__,
                    )
                    raise
                log.info(
                    "retryable provider error %s; retry %d/%d in %.0fs",
                    type(exc).__name__,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                attempt += 1

    return _wrapper


MEMORY_EXTRACTION_PROMPT = """Analyze the conversation above and extract KEY facts, decisions,
preferences, and corrections that should be remembered for future sessions.

Return a JSON array of memory objects. Each object has these fields:
  - "content": The fact/knowledge to store (clear, standalone sentence)
  - "category": One of: "fact", "preference", "correction", "decision", "pattern"
  - "tags": Array of relevant tags (lowercase, underscore_separated)
  - "confidence": 1.0 (high certainty) down to 0.5 (tentative)

RULES:
- Extract user preferences explicitly stated
- Extract corrections the user made
- Extract key technical decisions
- Extract patterns in how the user works
- DON'T extract: generic conversation, code snippets, temporary state
- DON'T extract: facts already obvious from the codebase itself
- Return ONLY a JSON object like {"memories": [...]}, nothing else.
"""


class CodingAgent:
    """Production-hardened agent with streaming, hooks, checkpointing, and approval support."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        timeout_sec: int = 120,
        callbacks: AgentCallbacks | None = None,
        permission_mode: str = "auto",
    ):
        cfg = get_config()
        self.api_key = api_key or cfg["api_key"]
        self.base_url = base_url or cfg["base_url"]
        self.model = model or cfg["model"]
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.max_turns = cfg["max_turns"]
        self.max_output_chars = cfg["max_output_chars"]
        self.turn_count = 0
        self.messages: list[dict] = []
        self.conversation_id = datetime.now(timezone.utc).strftime("conv_%Y%m%d_%H%M%S")
        self.callbacks = callbacks or AgentCallbacks()

        # Permission mode (9.7): gates tool execution before the approval hook.
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(
                f"permission_mode must be one of {PERMISSION_MODES}, got {permission_mode!r}"
            )
        self.permission_mode = permission_mode

        # Opt-in reflection gate on final answers (CODING_AGENT_VERIFY=1).
        self.verify_completions = os.environ.get("CODING_AGENT_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )

        # Retry state
        self.max_retries = 3
        self.base_delay = 1.0

        # Track consecutive turns with no tool calls to detect premature stopping
        self._no_tool_call_turns: int = 0
        self._max_no_tool_call_turns: int = 5  # Auto-continue retries before giving up

        # Circuit breaker: stop early when every tool call keeps failing turn
        # after turn, instead of spinning (and billing) all the way to max_turns.
        self._consecutive_failed_tool_turns: int = 0
        self._max_consecutive_failed_tool_turns: int = int(
            os.environ.get("CODING_AGENT_MAX_FAILED_TOOL_TURNS", "4")
        )
        # Active objective (9.8) + mid-run steer/queue buffers.
        self.goals = GoalStore()
        self._pending_steer: list[str] = []
        self._pending_queue: list[str] = []
        # Signature of the last goal revision injected into the prompt, so a
        # long conversation doesn't re-announce an unchanged goal every run.
        self._goal_prompt_signature: str | None = None
        # Recently executed tools (most recent last, cap 12) — always kept
        # advertised so mid-task tools never vanish between turns.
        self._recent_tools: list[str] = []
        # Current run() user message, for task-scoped tool selection.
        self._current_task_text: str = ""

        # Cost tracking (for /cost, goodbye)
        self._cost_tracker = CostTracker()

        # Memory refresh
        self._memory_refresh_interval = 3
        self._last_memory_refresh_turn = 0
        self._memory_context: str = ""
        self._last_extraction_msg_count = 0
        self._background_tasks: set = set()

        # Provider routing
        from llm import LLMConfig, ProviderRouter

        self._provider_config = LLMConfig(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            provider=cfg.get("provider", "deepseek"),
            thinking=cfg.get("thinking", False),
        )
        self._router = ProviderRouter(self._provider_config)

        # New modular components
        self.llm_client = LLMClient(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            token_resolver=self._make_token_resolver(),
        )
        self.message_builder = MessageBuilder()
        self.hooks = get_hooks()

        # Advanced tool result processing
        try:
            from core.tool_result_handler import get_tool_result_handler

            self._result_handler = get_tool_result_handler(
                max_output_chars=self.max_output_chars,
                max_session_chars=cfg.get("max_session_chars", 100000),
                enable_continuation=cfg.get("enable_continuation", True),
                enable_truncation=cfg.get("enable_truncation", True),
                enable_backpressure=cfg.get("enable_backpressure", True),
            )
        except Exception:
            self._result_handler = None

        # Project intelligence
        try:
            from project import ProjectDetector

            self._project_info = ProjectDetector().detect(PROJECT_DIR)
        except Exception:
            self._project_info = None

    @property
    def cost_tracker(self):
        return self._router.cost_tracker

    @property
    def stream_callback(self):
        return self.callbacks.stream_token

    @stream_callback.setter
    def stream_callback(self, cb):
        self.callbacks.stream_token = cb

    @property
    def think_callback(self):
        return self.callbacks.stream_think_token

    @think_callback.setter
    def think_callback(self, cb):
        self.callbacks.stream_think_token = cb

    def _is_cline_provider(self) -> bool:
        """True when the resolved provider is one of the Cline gateways."""
        try:
            return str(getattr(self._provider_config, "provider", "")).lower() in (
                "clinepass",
                "cline-usage",
            )
        except Exception:
            return False

    # HTTP codes worth rotating away from: dead/retired model (400/404),
    # exhausted balance/quota (402), rate-limited (429), gateway down (5xx).
    # Auth failures (401/403) are excluded — another model on the same dead
    # key fails identically.
    _ROTATE_CODES = frozenset({400, 402, 404, 429, 500, 502, 503, 504})

    # Same-gateway rotation pool for api.cline.bot (9.8: these replaced the
    # retired OpenCode Zen "-free" pool). Ordered free-first.
    _CLINE_ROTATION_POOL = (
        "deepseek/deepseek-chat",
        "minimax/minimax-m2.5",
        "qwen/qwen3-8b",
        "deepseek/deepseek-v4-flash",
        "z-ai/glm-5.3-flash",
    )

    # ClinePass subscription pool for the cross-provider escape leg. These
    # bill against the flat subscription quota, so they survive a $0 (even
    # negative) Cline Credits balance that 402s every usage-billed id.
    # Curated mirror of main.py _CLINEPASS_CATALOG — keep in sync.
    _CLINEPASS_ROTATION_POOL = (
        "cline-pass/kimi-k3",
        "cline-pass/deepseek-v4-flash",
        "cline-pass/glm-5.3",
    )

    @staticmethod
    def _api_error_code(content: str) -> int | None:
        """Extract the HTTP code from an '[API Error: <code>...]' message."""
        try:
            head = content.split("]", 1)[0]
            digits = "".join(c for c in head if c.isdigit())
            return int(digits[:3]) if len(digits) >= 3 else None
        except Exception:
            return None

    def _same_provider_candidates(self) -> list[str]:
        """Working-model candidates that run on the current base_url/key."""
        # Free-tier model names always rotate through the Cline gateway pool
        # (9.8: it replaced the retired OpenCode Zen "-free" pool), no matter
        # which provider label the session started on.
        if self._is_cline_provider() or self.model.endswith("-free") or ":free" in self.model:
            pool = list(self._CLINE_ROTATION_POOL)
            try:
                from core.contributor import (
                    contributor_enabled,
                    contributor_models_enabled_only,
                )

                pool = contributor_models_enabled_only(pool)
                if contributor_enabled():
                    pool.append("muse-spark-1.2-contributor-free")
            except Exception:
                pass
            return [m for m in pool if m != self.model]
        # Other providers: a 404/400 usually means the configured model id
        # retired — retry once on the provider's current default.
        try:
            from core.providers import PROVIDER_DEFAULTS

            pname = str(getattr(self._provider_config, "provider", "")).lower()
            default = str(PROVIDER_DEFAULTS.get(pname, {}).get("default_model", ""))
            if default and default != self.model:
                return [default]
        except Exception:
            pass
        return []

    async def _try_candidate_model(self, model: str, messages, tools):
        """One rotation attempt on a same-provider model; None when still bad."""
        self.model = model
        self.llm_client.model = model
        try:
            msg = await self.llm_client.chat_stream(
                messages=messages,
                tools=tools,
                stream_callback=self.callbacks.stream_token,
                think_callback=self.callbacks.stream_think_token,
            )
        except Exception:
            return None
        if msg and not str(msg.get("content", "")).startswith("[API Error:"):
            return msg
        return None

    @staticmethod
    def _cline_gateway_usable() -> bool:
        """True when api.cline.bot auth exists (CLI session token or key)."""
        try:
            from core.providers import cline_session_token

            if cline_session_token():
                return True
        except Exception:
            pass
        return bool((os.environ.get("CLINEPASS_API_KEY", "") or "").strip())

    @staticmethod
    def _ollama_reachable() -> bool:
        """True when a local Ollama server answers (no model check)."""
        try:
            import httpx

            host = (os.environ.get("OLLAMA_HOST", "") or "http://127.0.0.1:11434").rstrip("/")
            if host.endswith("/v1"):
                host = host[: -len("/v1")]
            r = httpx.get(f"{host}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    async def _try_provider_escape(self, target: str, candidates_fn, messages, tools):
        """Switch to the target provider and try its candidates.

        Returns the first successful assistant message (winner stays pinned),
        or None when nothing worked — in which case the original provider
        config and model string are restored.
        """
        original_model = self.model
        original_config = self._provider_config
        try:
            from core.providers import build_llm_config

            self.switch_provider(build_llm_config(target))
            for alt in candidates_fn():
                print(f"\n  [AUTO-ROTATE] Escaping to {target} '{alt}'...")
                good = await self._try_candidate_model(alt, messages, tools)
                if good is not None:
                    print(f"  [AUTO-ROTATE] Succeeded on {target} '{alt}' — pinned")
                    return good
        except Exception:
            pass
        with contextlib.suppress(Exception):
            self.switch_provider(original_config)
        # switch_provider resets the model from the config object, which
        # may predate a direct ag.model assignment — restore the string.
        self.model = original_model
        with contextlib.suppress(Exception):
            self.llm_client.model = original_model
        return None

    async def _auto_rotate_model(self, failed_msg: dict, messages, tools):
        """Rotate to a working model after an [API Error].

        Returns the first successful assistant message (leaving the winner
        pinned as the active model), or None when nothing worked — in which
        case the original model/provider is restored and the caller keeps
        the original error message.
        """
        content = str(failed_msg.get("content", ""))
        code = self._api_error_code(content)
        if code in (401, 403):
            return None
        if code is not None and code not in self._ROTATE_CODES:
            return None

        original_model = self.model
        # 1) Same-provider rotation (same key, same gateway — cheapest fix).
        for alt in self._same_provider_candidates():
            print(
                f"\n  [AUTO-ROTATE] '{original_model}' unavailable"
                f"{f' (HTTP {code})' if code else ''}; trying '{alt}'..."
            )
            good = await self._try_candidate_model(alt, messages, tools)
            if good is not None:
                print(f"  [AUTO-ROTATE] Succeeded on '{alt}' — pinned for this session")
                return good
        self.model = original_model
        with contextlib.suppress(Exception):
            self.llm_client.model = original_model

        # 2) Cross-provider escape hatches, cheapest first: the whole
        # provider may be down, its key dead, or its balance exhausted
        # (402) — fail over to another usable provider instead of dying.
        # Each failed leg restores the original provider + model, so legs
        # chain safely.
        current = str(getattr(self._provider_config, "provider", "")).lower()
        cline_usable = self._cline_gateway_usable()
        if current != "cline-usage" and cline_usable:
            good = await self._try_provider_escape(
                "cline-usage",
                lambda: [m for m in self._same_provider_candidates() if m != self.model],
                messages,
                tools,
            )
            if good is not None:
                return good
        if current != "clinepass" and cline_usable:
            good = await self._try_provider_escape(
                "clinepass",
                lambda: [m for m in self._CLINEPASS_ROTATION_POOL if m != self.model],
                messages,
                tools,
            )
            if good is not None:
                return good
        if current != "ollama" and self._ollama_reachable():
            good = await self._try_provider_escape(
                "ollama",
                # Post-switch self.model is build_llm_config's pick: an
                # installed model when the server reports one, else the
                # default — exactly one attempt, no pool to burn.
                lambda: [self.model],
                messages,
                tools,
            )
            if good is not None:
                return good
        return None

    @property
    def provider_name(self) -> str:
        names = {
            "deepseek": "DeepSeek",
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "google": "Google",
            "ollama": "Ollama",
            "zai": "Z.ai",
            "groq": "Groq",
            "openrouter": "OpenRouter",
            "clinepass": "ClinePass",
            "cline-usage": "Cline (usage)",
        }
        return names.get(self._provider_config.provider, self._provider_config.provider)

    def _make_token_resolver(self):
        """Return a callable that re-reads the Cline CLI auth token on each
        request, or None for non-ClinePass providers.

        The gateway forwards our bearer token to Cline's API, which expects
        the live WorkOS session token from `cline auth`. That token expires
        (401) and Cline CLI silently rotates it, so a key captured at startup
        goes stale mid-session. Re-reading the file each request makes token
        refreshes (and even `cline auth` re-logins) take effect without
        restarting LuckyD.
        """
        try:
            if (self._provider_config.provider or "").lower() != "clinepass":
                return None
        except Exception:
            return None

        def _resolve() -> str | None:
            try:
                from llm.providers.clinepass import ClinePassProvider

                return ClinePassProvider.get_cline_token()
            except Exception:
                return None

        return _resolve

    def switch_provider(self, config: LLMConfig) -> None:
        """Switch the active LLM provider/model at runtime.

        Public API for model switching so callers (main.py, bridges) don't
        need to reach into private internals. Rebinds the provider router
        (display + cost tracking) AND the inference client (self.llm_client)
        so /model actually routes requests to the new provider — not just
        the display name.
        """
        self._provider_config = config
        self._router.switch(config)
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.llm_client = LLMClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            token_resolver=self._make_token_resolver(),
        )

    def _emit_event(self, event_type: AgentEventType, payload: dict | None = None) -> None:
        """Emit an agent event through the callbacks system."""
        if self.callbacks and self.callbacks.on_event:
            event = AgentEvent(type=event_type, payload=payload or {}, turn=self.turn_count)
            self.callbacks.on_event(event)

    def set_goal(self, text: str) -> str:
        """Set the active objective; returns the REPL confirmation text."""
        goal = self.goals.set(text)
        return f"Goal set: {goal.text}"

    def goal_status(self) -> str:
        """One-line status of the active goal (or the empty-goal message)."""
        return self.goals.describe()

    def steer(self, text: str) -> str:
        """Inject mid-run guidance for the CURRENT response."""
        cleaned = (text or "").strip()
        if not cleaned:
            return "Nothing to steer with - provide guidance text."
        self._pending_steer.append(cleaned)
        return "Steer noted - it will guide the current response."

    def queue(self, text: str) -> str:
        """Defer a follow-up for AFTER the current response finishes."""
        cleaned = (text or "").strip()
        if not cleaned:
            return "Nothing queued - provide follow-up text."
        self._pending_queue.append(cleaned)
        return "Queued - it will run after the current response."

    def _take_steer(self) -> list[str]:
        pending, self._pending_steer = self._pending_steer, []
        return pending

    def _take_queue(self) -> list[str]:
        pending, self._pending_queue = self._pending_queue, []
        return pending

    def _drain_steer_into_messages(self) -> bool:
        """Splice pending steer guidance into messages. True when added."""
        pending = self._take_steer()
        if not pending:
            return False
        guidance = "\n".join(f"[steer] {line}" for line in pending)
        self.messages.append({"role": "user", "content": guidance})
        return True

    def _maybe_inject_goal(self) -> None:
        """Announce the active goal to the model once per goal revision.

        Skips silently when no goal is set or it is paused; a cleared goal
        resets the signature so the next goal is announced fresh.
        """
        goal = self.goals.goal
        if goal is None or goal.paused or not (goal.text or "").strip():
            self._goal_prompt_signature = None
            return
        sig = f"{goal.text}\x00{goal.budget}"
        if sig == self._goal_prompt_signature:
            return
        self._goal_prompt_signature = sig
        line = f"[goal] Active objective: {goal.text.strip()}"
        if goal.budget is not None:
            line += f" (token budget: {goal.budget}, spent: {goal.spent})"
        self.messages.append({"role": "user", "content": line})

    def _build_system(self) -> str:
        """Build the system prompt with tools, project info, memories, and rules."""
        tools_desc = registry.prompt_description()
        rules = load_project_rules()
        return self.message_builder.build_system(
            provider_name=self.provider_name,
            model_name=self.model,
            tools_description=tools_desc,
            memory_context=self._memory_context,
            project_rules=rules,
        )

    @staticmethod
    def _in_plan_mode() -> bool:
        """True while the agent sits in the EnterPlanMode read-only phase."""
        try:
            from tools.plan_tools import is_plan_mode

            return bool(is_plan_mode())
        except Exception:
            return False

    def _record_tool_use(self, tool) -> None:
        """Remember an executed tool so pruning keeps it advertised."""
        name = str(getattr(tool, "name", "") or "").lower()
        if not name:
            return
        if name in self._recent_tools:
            self._recent_tools.remove(name)
        self._recent_tools.append(name)
        del self._recent_tools[:-12]

    def _select_tool_names(self, all_names: list[str] | None = None) -> list[str] | None:
        """Task-scoped tool subset for this turn's schemas.

        Returns None (no pruning) when CODING_AGENT_NO_PRUNE=1. Otherwise
        keeps the always-advertised core, keyword-matched families from
        the current task text + active goal, recently used tools, and any
        tool matching no known family (dynamic MCP tools stay safe).
        """
        if os.environ.get("CODING_AGENT_NO_PRUNE", "").lower() in ("1", "true", "yes"):
            return None
        names = list(all_names) if all_names is not None else registry.list_tools()
        goal_text = ""
        try:
            goal = self.goals.goal
            if goal is not None and not goal.paused:
                goal_text = goal.text or ""
        except Exception:
            pass
        text = f"{self._current_task_text}\n{goal_text}".lower()
        keep = set(ALWAYS_ADVERTISED_TOOLS) | set(self._recent_tools)
        family_prefixes: set[str] = set()
        for keyword, prefixes in TASK_TOOL_KEYWORDS.items():
            family_prefixes.update(prefixes)
            if keyword in text:
                keep.update(n for n in names if n.startswith(prefixes))
        # Unknown/unmapped tools are kept, never pruned.
        keep.update(
            n
            for n in names
            if n not in ALWAYS_ADVERTISED_TOOLS and not n.startswith(tuple(family_prefixes))
        )
        return sorted(n for n in keep if n in names)

    def _permission_mode_decision(self, tool, tool_name: str) -> tuple[str, str]:
        """Permission-mode gate for _execute_tool; evaluated BEFORE the approval hook.

        Returns (decision, reason) where decision is one of:
          - "allow": the mode pre-approved the call; the approval hook is skipped.
          - "defer": the mode has no opinion; before_tool hooks (incl. approval) run.
          - "block": the mode denied the call; _execute_tool returns an error.
        """
        mode = self.permission_mode
        level = getattr(tool, "permission_level", "NORMAL")
        level = level.value.upper() if hasattr(level, "value") else str(level or "NORMAL").upper()
        lname = (tool_name or "").lower()

        if level == "BLOCKED":
            return "block", "tool is BLOCKED"

        # Plan mode wins over every permission mode: exploration is
        # read-only by enforcement, not just by prompt. The plan tools
        # themselves stay callable so the agent can always exit.
        if self._in_plan_mode():
            canonical = str(getattr(tool, "name", "") or "").lower()
            if canonical in ("enterplanmode", "exitplanmode"):
                return "allow", "plan-mode tool stays available in plan mode"
            if level == "ALWAYS_ALLOW" or lname.startswith(READONLY_NAME_PREFIXES):
                return "allow", "read-only tool allowed in plan mode"
            return (
                "block",
                "plan mode is read-only: explore with read tools, "
                "then ExitPlanMode with the plan to write",
            )

        if mode == "bypassPermissions":
            return "allow", "bypassPermissions allows everything except BLOCKED tools"

        if mode == "acceptEdits":
            if level in ("ALWAYS_ALLOW", "NORMAL"):
                return "allow", "ALWAYS_ALLOW/NORMAL tools are auto-allowed"
            if any(keyword in lname for keyword in EDIT_TOOL_KEYWORDS):
                return "allow", "file-editing tool auto-allowed in acceptEdits mode"
            return "block", "not a file-editing tool; blocked in acceptEdits mode"

        if mode == "auto":
            if level in ("ALWAYS_ALLOW", "NORMAL"):
                return "allow", "ALWAYS_ALLOW/NORMAL tools are auto-allowed"
            return "defer", "REQUIRES_APPROVAL tools defer to the approval hook"

        if mode == "off":
            if level == "ALWAYS_ALLOW":
                return "allow", "ALWAYS_ALLOW tools stay available in off mode"
            if lname.startswith(READONLY_NAME_PREFIXES):
                return "allow", "read-only tool name"
            return "block", "off mode blocks every state-modifying tool"

        # 'default': current behavior — everything defers to the approval hook.
        return "defer", "deferred to the approval hook"

    @staticmethod
    def _is_approval_hook(hook) -> bool:
        """True when a before_tool hook entry is the ApprovalHook.

        Plugins register the bound method ``self.before_tool``, so unwrap to
        the plugin instance before checking its name.
        """
        target = getattr(hook, "__self__", hook)
        return getattr(target, "name", "") == "approval"

    async def _execute_tool(self, tool_name: str, tool_args: dict) -> dict:
        """Execute a tool with hook support and approval checks."""
        from tools.registry import registry

        tool = registry.get(tool_name)
        call_id = tool_args.get("_id", "unknown")

        if not tool:
            known = ", ".join(registry.list_tools())
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Error: Unknown tool '{tool_name}'. Available: {known}",
            }

        # Plan-approval gate: after ExitPlanMode, writes stay blocked until
        # the user approves and the agent calls ApprovePlan. Reads and plan
        # tools keep working so the plan can still be refined.
        if tool_name not in ("EnterPlanMode", "ExitPlanMode", "ApprovePlan"):
            try:
                from tools.plan_tools import is_awaiting_approval

                if is_awaiting_approval():
                    level = getattr(tool, "permission_level", "NORMAL")
                    level = (
                        level.value.upper()
                        if hasattr(level, "value")
                        else str(level or "NORMAL").upper()
                    )
                    if level == "REQUIRES_APPROVAL":
                        return {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": (
                                f"Error: Tool '{tool_name}' blocked: a proposed plan "
                                "is awaiting user approval. Call ApprovePlan once the "
                                "user accepts the plan."
                            ),
                        }
            except Exception:
                pass

        # Permission-mode gate (9.7): enforced BEFORE the approval hook.
        # "allow" pre-approves the call (the approval hook is skipped, other
        # before_tool hooks still run); "defer" runs the hooks as usual;
        # "block" returns an error immediately.
        decision, mode_reason = self._permission_mode_decision(tool, tool_name)
        if decision == "block":
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": (
                    f"Error: Tool '{tool_name}' blocked by permission mode "
                    f"'{self.permission_mode}': {mode_reason}."
                ),
            }

        # Run before_tool hooks (approval, checkpoint, etc.)
        ctx = HookContext(turn=self.turn_count, messages=self.messages)
        before_hooks = self.hooks.before_tool
        if decision == "allow":
            before_hooks = [h for h in before_hooks if not self._is_approval_hook(h)]
        for hook in before_hooks:
            try:
                result = await asyncio.to_thread(hook, tool_name, tool_args, ctx)
                if result is not None:
                    return result  # Hook intercepted/blocked the tool
            except Exception as e:
                print(f"\n  [HOOK ERR] before_tool hook failed: {e}")

        self._record_tool_use(tool)

        # Execute the tool with a real timeout (per-tool override | global default)
        default_tool_timeout = float(os.environ.get("CODING_AGENT_TOOL_TIMEOUT", "180"))
        tool_timeout = getattr(tool, "timeout_sec", None)
        if tool_timeout is None:
            tool_timeout = default_tool_timeout
        try:
            clean_args = {k: v for k, v in tool_args.items() if not k.startswith("_")}
            result = await asyncio.wait_for(tool.execute(**clean_args), timeout=tool_timeout)
        except asyncio.TimeoutError:
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Error: Tool execution timed out after {tool_timeout:.0f}s.",
            }
        except TypeError as e:
            result_text = (
                f"Error: Tool argument error: {e}\nExpected: {json.dumps(tool.parameters)}"
            )
            return {"role": "tool", "tool_call_id": call_id, "content": result_text}
        except Exception as e:
            traceback.print_exc()
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Error: Tool execution error: {e}",
            }

        content = result.text
        if result.error and not content.startswith("Error"):
            content = f"Error: {content}"

        # ── Advanced tool result processing pipeline ──────────────────────
        # Stage 1: Backpressure — rate limit, size cap, token budget
        # Stage 2: Truncation — semantic, diff-aware, head/tail
        # Stage 3: Continuation — multi-part results with offset/limit
        if self._result_handler is not None:
            from core.tool_result_handler import ToolResult

            tool_result = ToolResult(text=content, error=result.error)
            processed = self._result_handler.process(
                tool_result, tool_name, call_id, max_chars=self.max_output_chars
            )
            content = processed.text
            # Register continuation if result was truncated
            if len(result.text) > self.max_output_chars and not result.error:
                self._emit_event(
                    AgentEventType.TOOL_RESULT_TRUNCATED,
                    {
                        "tool": tool_name,
                        "call_id": call_id,
                        "original_size": len(result.text),
                        "truncated_size": len(content),
                        "has_continuation": self._result_handler.continuation.get_continuation(
                            call_id
                        )
                        is not None,
                    },
                )
        else:
            # Legacy simple truncation
            if len(content) > self.max_output_chars:
                content = content[: self.max_output_chars] + "\n... [output truncated]"

        if tool_name in ("Write", "Edit") and not content.startswith("Error"):
            verify_error = self._verify_python_edit(tool_args)
            if verify_error is not None:
                content = verify_error
                self._emit_event(AgentEventType.TOOL_ERROR, {"tool": tool_name, "verify": True})

        tool_result_msg = {"role": "tool", "tool_call_id": call_id, "content": content}

        # Run after_tool hooks
        for hook in self.hooks.after_tool:
            try:
                modified = await asyncio.to_thread(hook, tool_name, tool_args, tool_result_msg, ctx)
                if modified is not None:
                    tool_result_msg = modified
            except Exception as e:
                print(f"\n  [HOOK ERR] after_tool hook failed: {e}")

        return tool_result_msg

    @staticmethod
    def _verify_python_edit(tool_args: dict) -> str | None:
        """Syntax-check a just-written .py file; error text or None.

        Catches broken edits at the source so the next turn fixes real
        code instead of building on a SyntaxError. Fail-open: anything
        unexpected (missing/unreadable file) keeps the original success.
        """
        try:
            path = tool_args.get("file_path", "")
            if not isinstance(path, str) or not path.endswith(".py"):
                return None
            source = Path(path).expanduser().read_text(encoding="utf-8")
        except Exception:
            return None
        try:
            compile(source, path, "exec")
        except SyntaxError as exc:
            return (
                f"Error: {path} has a syntax error after your edit "
                f"(line {exc.lineno}: {exc.msg}). Fix it before continuing."
            )
        return None

    async def _extract_session_memories(self, user_message: str) -> None:
        """Extract key facts/preferences/corrections from conversation via LLM."""
        try:
            memory = get_memory()
        except Exception:
            return

        start = min(self._last_extraction_msg_count, len(self.messages))
        transcript_lines: list[str] = []
        for msg in self.messages[start:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or role == "system":
                continue
            if role == "tool":
                content = content[:300]
            elif role == "assistant":
                content = content[:500]
            transcript_lines.append(f"[{role}] {content}")

        if len(transcript_lines) < 2:
            return

        transcript = "\n".join(transcript_lines[-40:])
        extraction_messages = [
            {"role": "system", "content": MEMORY_EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": f"Conversation transcript:\n\n{transcript}\n\nExtract memories (JSON object only):",
            },
        ]

        try:
            result = await self._call_llm_for_extraction(extraction_messages)
            if not result:
                return
            content = result.get("content", "").strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1] if "\n" in content else content
                if content.endswith("```"):
                    content = content[:-3]
            content = content.strip()
            memories = json.loads(content)
            # json_object mode returns an object -- accept both
            if isinstance(memories, dict):
                memories = memories.get("memories", [])
            if not isinstance(memories, list):
                return
            stored = 0
            for mem in memories:
                if not isinstance(mem, dict):
                    continue
                mcontent = mem.get("content", "").strip()
                if not mcontent:
                    continue
                category = mem.get("category", "general")
                tags = mem.get("tags", [])
                await asyncio.to_thread(
                    memory.add,
                    content=f"[{category}] {mcontent}",
                    tags=[*tags, "extracted", category],
                    source=f"auto-extract-{self.conversation_id}",
                )
                stored += 1
            if stored > 0:
                print(f"  [MEM] Extracted {stored} memories from this session")
        except (json.JSONDecodeError, Exception):
            pass

    async def _call_llm_for_extraction(self, messages: list[dict]) -> dict | None:
        """Simple LLM call for memory extraction — non-streaming, minimal retry."""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        _k = (self.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }
        try:
            from core.llm_client import LLMClient

            tmp_client = LLMClient(self.api_key, self.base_url, self.model, timeout_sec=30)
            resp = await tmp_client._http_post(url, headers, payload)
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            return choice.get("message", {})
        except Exception:
            return None

    def _api_unreachable_message(self, exc: Exception | None = None) -> str:
        """Actionable message for a dead LLM endpoint.

        The old text ("[ERR] API connection failed after 3 retries.") left
        users with no idea what to check; name the provider/model and the
        three things that actually fix this.
        """
        detail = f": {exc}" if exc is not None else ""
        return (
            f"[ERR] Could not reach the {self.provider_name} API ({self.model}) "
            f"after 3 attempts{detail}\n"
            "Check: 1) your API key is set (Settings), 2) your network connection, "
            "3) the base URL if you customized it. Use /model to switch providers."
        )

    def _should_continue(self, assistant_msg: dict) -> str | None:
        """Return a continuation prompt if the agent stopped prematurely, else None.

        Detects premature-stop cases:
        1. finish_reason == "length" (output truncated by max_tokens)
        2. Content ends with a continuation phrase ("Continuing", "Let me continue", etc.)
        3. Content looks incomplete: an unclosed code block, or a trailing
           sentence that signals more work was about to happen ("Now I'll...",
           "Next, ...", ends mid-word, ends with ':' etc.)
        """
        # Case 1: truncated by token limit
        finish_reason = assistant_msg.get("_finish_reason", "") or assistant_msg.get(
            "finish_reason", ""
        )
        if finish_reason == "length":
            return (
                "Your previous response was cut off due to the token limit. "
                "Please continue from where you left off."
            )

        content = (assistant_msg.get("content") or "").strip()
        if not content:
            return None

        lower = content.lower()

        # Case 2: ends with a continuation phrase
        continuation_phrases = [
            "continuing",
            "let me continue",
            "i'll continue",
            "to be continued",
            "continuing from where",
            "let me keep going",
            "moving on to",
        ]
        tail = lower[-80:]
        for phrase in continuation_phrases:
            if phrase in tail:
                return "Please continue from where you left off."

        # Case 3: structurally incomplete output
        if self._looks_incomplete(content, lower):
            return (
                "Your response appears incomplete — you stopped before the task "
                "was finished. Please continue from where you left off and complete "
                "the remaining steps."
            )

        return None

    @staticmethod
    def _looks_incomplete(content: str, lower: str) -> bool:
        """Heuristics for output that stopped mid-task.

        Returns True when the text shows structural signs of being cut short:
        - An odd number of ``` fences (an unclosed code block)
        - A trailing phrase announcing an imminent action that never happened
        - Ending with ':' or a connector suggesting a follow-up
        """
        # Unclosed fenced code block
        if content.count("```") % 2 == 1:
            return True

        tail = lower[-120:].rstrip()

        # Trailing "I'm about to do X" signals
        imminent_action = [
            "now i'll",
            "now i will",
            "now let me",
            "next, i'll",
            "next i'll",
            "next, let me",
            "i'll now",
            "i will now",
            "let me now",
            "i'm going to",
            "i am going to",
            "let's now",
            "first, i'll",
            "then i'll",
            "then i will",
            "next step",
            "the next",
            "let me",
            "i'll",
        ]
        for phrase in imminent_action:
            if tail.endswith(phrase):
                return True

        # Ends with a colon / connector (a lead-in to something that never came)
        return bool(tail.endswith((":", "—", "-", ",", ";")))

    async def _verify_final_answer(self, request: str, content: str) -> str:
        """Reflection-gated completion: adopt the improved answer when better.

        Single score/improve/rescore pass through ReflectionEngine. Any
        failure (disabled, empty/error content, caller error) keeps the
        original answer — verification never breaks a run.
        """
        if not self.verify_completions or not (content or "").strip():
            return content
        if content.startswith("[API Error:"):
            return content
        try:
            from .reflection import reflect_on_output

            async def _caller(prompt: str) -> str:
                msg = await self.llm_client.chat_nonstreaming([{"role": "user", "content": prompt}])
                return str((msg or {}).get("content", "") or "")

            result = await reflect_on_output(request, content, llm_caller=_caller, max_iterations=1)
            if result.improved:
                print(f"\n  [VERIFY] score {result.overall_score:.2f} — adopted improved answer")
                return result.improved_output
            return content
        except Exception:
            return content

    @staticmethod
    def _parse_tool_call(tc: dict, fallback_idx: int) -> tuple[str, dict]:
        """Split one provider tool_call into (name, args with _id)."""
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        try:
            tool_args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_args = {}
        tool_args["_id"] = tc.get("id", f"call_{fallback_idx}")
        return tool_name, tool_args

    @staticmethod
    def _parallel_safe(tool_name: str) -> bool:
        """True when a call may run alongside other calls in its batch.

        Both conditions are required: the ParallelExecutor classifies the
        tool read-only AND its permission level is ALWAYS_ALLOW (so no
        interactive approval prompt can fire mid-batch). CODING_AGENT_NO_PARALLEL
        disables batching entirely.
        """
        if os.environ.get("CODING_AGENT_NO_PARALLEL", "").lower() in ("1", "true", "yes"):
            return False
        try:
            from core.parallel_executor import READ_ONLY_TOOLS
            from tools.registry import registry
        except Exception:
            return False
        if tool_name not in READ_ONLY_TOOLS:
            return False
        tool = registry.get(tool_name)
        if tool is None:
            return False
        level = getattr(tool, "permission_level", "NORMAL")
        level = level.value.upper() if hasattr(level, "value") else str(level or "NORMAL").upper()
        return level == "ALWAYS_ALLOW"

    def _batch_tool_calls(self, tool_calls: list[dict]) -> list[list[tuple[str, dict]]]:
        """Group parsed calls into order-preserving batches.

        Consecutive parallel-safe calls form one batch (run together);
        everything else runs alone, in program order — so a Read after a
        Write still sees the write. Single-call turns batch trivially.
        """
        batches: list[list[tuple[str, dict]]] = []
        current: list[tuple[str, dict]] = []
        for i, tc in enumerate(tool_calls):
            parsed = self._parse_tool_call(tc, i)
            if self._parallel_safe(parsed[0]):
                current.append(parsed)
            else:
                if current:
                    batches.append(current)
                    current = []
                batches.append([parsed])
        if current:
            batches.append(current)
        return batches

    async def _run_tool_call(self, parsed: tuple[str, dict]) -> dict:
        """Execute one parsed call with timing, events, and console status."""
        tool_name, tool_args = parsed
        start_time = time.monotonic()
        self._emit_event(AgentEventType.TOOL_START, {"tool": tool_name, "args": tool_args})
        result_msg = await self._execute_tool(tool_name, tool_args)
        elapsed = time.monotonic() - start_time
        content_preview = result_msg["content"][:100].replace("\n", " ")
        is_err = result_msg["content"].startswith("Error")
        status = "[ERR]" if is_err else "[OK]"
        print(f"  {status} [{tool_name}] {elapsed:.1f}s — {content_preview}")
        self._emit_event(
            AgentEventType.TOOL_END if not is_err else AgentEventType.TOOL_ERROR,
            {"tool": tool_name, "elapsed": elapsed, "error": is_err},
        )
        return result_msg

    async def run(self, user_message: str, max_turns: int | None = None) -> str:
        """Run the full agent loop with hooks, events, checkpointing, and memory extraction.

        Conversation history persists across calls so multi-turn conversations work.
        Use reset() (or /clear) to start a fresh conversation.
        """
        max_turns = max_turns or self.max_turns
        self.turn_count = 0
        self._current_task_text = user_message or ""

        # Fresh conversation: build system prompt and inject memories
        if not self.messages:
            self.messages.append({"role": "system", "content": self._build_system()})
            self._memory_context = ""
            self._last_memory_refresh_turn = 0
            try:
                memory = get_memory()
                memories = await asyncio.to_thread(memory.get_context, user_message, limit=3)
                if memories and "(no relevant memories)" not in memories:
                    self._memory_context = memories
                    self.messages.append(
                        {
                            "role": "system",
                            "content": f"Relevant memories from past sessions:\n{memories}",
                        }
                    )
            except Exception:
                pass

        self.messages.append({"role": "user", "content": user_message})
        self._maybe_inject_goal()
        TrajectoryRecorder.attach_if_enabled(self)
        self._emit_event(AgentEventType.SESSION_START, {"message": user_message[:100]})

        final_text = ""
        consecutive_errors = 0

        for turn in range(max_turns):
            self.turn_count = turn + 1
            self._emit_event(AgentEventType.TURN_START, {"turn": self.turn_count})
            # 9.8: consume pending steer guidance at the turn boundary so it
            # shapes the CURRENT response (a user message is already in the
            # history; appending another one here is exactly "inject a user
            # message mid-run guiding the current response").
            self._drain_steer_into_messages()

            # Context compaction first: summarize stale history (per-model
            # thresholds) while the full transcript still exists. Never raises.
            await maybe_compact(self)

            # Safety fallback: cap messages when compaction declined or
            # failed, so context can never grow without bound.
            if len(self.messages) > 40:
                from .context_manager import truncate_messages

                self.messages = truncate_messages(self.messages, max_messages=40, keep_recent=20)
                self._emit_event(
                    AgentEventType.CONTEXT_TRUNCATED, {"message_count": len(self.messages)}
                )

            # Per-turn memory refresh
            if (turn + 1) - self._last_memory_refresh_turn >= self._memory_refresh_interval:
                self._last_memory_refresh_turn = turn + 1
                try:
                    recent = " ".join(
                        m.get("content", "")[:200]
                        for m in self.messages[-6:]
                        if m.get("role") in ("user", "tool")
                    )
                    if recent:
                        refreshed = await self._refresh_memory_context(recent)
                        if refreshed:
                            self.messages.append(
                                {
                                    "role": "system",
                                    "content": f"[Updated relevant memories]\n{self._memory_context}",
                                }
                            )
                            self._emit_event(AgentEventType.MEMORY_REFRESH, {})
                except Exception:
                    pass

            # Session autosave (9.7): checkpoint the session every 5 turns.
            if self.turn_count % 5 == 0:
                with contextlib.suppress(Exception):
                    self.save_session()

            # Run before_model hooks
            ctx = HookContext(turn=self.turn_count, messages=self.messages)
            modified_messages = self.messages
            for hook in self.hooks.before_model:
                try:
                    result = hook(modified_messages, ctx)
                    if result is not None:
                        modified_messages = result
                except Exception as e:
                    print(f"\n  [HOOK ERR] before_model hook failed: {e}")

            # Call LLM
            self._emit_event(
                AgentEventType.MODEL_REQUEST, {"message_count": len(modified_messages)}
            )
            tools = registry.openai_tools(self._select_tool_names())
            try:
                assistant_msg = await self.llm_client.chat_stream(
                    messages=modified_messages,
                    tools=tools,
                    stream_callback=self.callbacks.stream_token,
                    think_callback=self.callbacks.stream_think_token,
                )
            except Exception as e:
                consecutive_errors += 1
                print(f"\n  [ERR] LLM call failed ({type(e).__name__}): {e}")
                if consecutive_errors >= 3:
                    self._emit_event(AgentEventType.ERROR, {"error": str(e)})
                    return final_text or self._api_unreachable_message(e)
                continue
            if assistant_msg is None:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self._emit_event(AgentEventType.ERROR, {"error": "API failed after 3 retries"})
                    return final_text or self._api_unreachable_message()
                continue

            # HQ auto-rotation: when the current model is dead (404/400),
            # its balance is exhausted (402), it is rate-limited (429) or
            # the gateway is down (5xx), transparently rotate to a working
            # model instead of surfacing [API Error] text. Auth failures
            # (401/403) are NOT rotated — another model on the same dead
            # key would fail identically; the error text already tells the
            # user how to re-login / fix the key.
            if isinstance(assistant_msg, dict) and assistant_msg.get("content", "").startswith(
                "[API Error:"
            ):
                _alt_msg = await self._auto_rotate_model(assistant_msg, modified_messages, tools)
                if _alt_msg is not None:
                    assistant_msg = _alt_msg

            consecutive_errors = 0
            for hook in self.hooks.after_model:
                try:
                    result = hook(assistant_msg, ctx)
                    if result is not None:
                        assistant_msg = result
                except Exception:
                    pass

            # Track token usage for cost display (and the goal budget, if any)
            usage = assistant_msg.get("_usage", {}) if isinstance(assistant_msg, dict) else {}
            if usage:
                self._cost_tracker.add_usage(usage, self.model)
                with contextlib.suppress(Exception):
                    spent_in = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
                    spent_out = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
                    self.goals.add_spent(spent_in + spent_out)
            assistant_msg = (
                assistant_msg.to_dict() if hasattr(assistant_msg, "to_dict") else assistant_msg
            )

            self.messages.append(assistant_msg)
            self._emit_event(
                AgentEventType.MODEL_RESPONSE,
                {
                    "has_content": bool(assistant_msg.get("content")),
                    "has_tool_calls": bool(assistant_msg.get("tool_calls")),
                },
            )

            content = assistant_msg.get("content", "")
            tool_calls = assistant_msg.get("tool_calls", [])

            if not tool_calls:
                # Check whether the agent stopped prematurely and should keep going
                continuation = self._should_continue(assistant_msg)
                if continuation and self._no_tool_call_turns < self._max_no_tool_call_turns:
                    self._no_tool_call_turns += 1
                    self._emit_event(
                        AgentEventType.TURN_END,
                        {"final": False, "continuation": True, "turn": self.turn_count},
                    )
                    print(
                        f"\n  [CONT] Response appears incomplete — requesting continuation "
                        f"({self._no_tool_call_turns}/{self._max_no_tool_call_turns})"
                    )
                    self.messages.append({"role": "user", "content": continuation})
                    continue

                # Genuine final answer (optionally reflection-verified)
                self._no_tool_call_turns = 0
                self._consecutive_failed_tool_turns = 0
                final_text = await self._verify_final_answer(user_message, content)
                assistant_msg["content"] = final_text
                self._emit_event(AgentEventType.TURN_END, {"final": True})
                break

            # Tool calls present — reset the no-tool-call counter
            self._no_tool_call_turns = 0

            tool_results: list = []
            for batch in self._batch_tool_calls(tool_calls):
                if len(batch) == 1:
                    tool_results.append(await self._run_tool_call(batch[0]))
                else:
                    tool_results.extend(
                        await asyncio.gather(*(self._run_tool_call(parsed) for parsed in batch))
                    )

            # Nudge the model toward a different approach when failures repeat,
            # but only once per turn: several failing tool calls in one turn
            # must not stack identical hints.
            turn_had_error = any(r.get("content", "").startswith("Error") for r in tool_results)
            hint_messages = []
            if turn_had_error and turn >= 1:
                for m in self.messages[-3:]:
                    if m.get("role") == "tool" and m.get("content", "").startswith("Error"):
                        hint_messages.append(
                            {
                                "role": "system",
                                "content": "HINT: The previous call to this tool failed. Try a different approach.",
                            }
                        )
                        break

            self.messages.extend(tool_results)
            if hint_messages:
                self.messages.extend(hint_messages)

            # Circuit breaker: every tool call failed again this turn. Stop
            # early with an actionable explanation instead of burning the
            # remaining turns (and budget) on a stuck loop.
            if turn_had_error:
                self._consecutive_failed_tool_turns += 1
                if self._consecutive_failed_tool_turns >= self._max_consecutive_failed_tool_turns:
                    last_err = (tool_results[0].get("content") or "")[:300]
                    final_text = (
                        f"[ERR] Stopped after {self._consecutive_failed_tool_turns} "
                        "consecutive turns in which every tool call failed. "
                        f"Last error: {last_err}\n"
                        "The tools kept failing, so I stopped instead of using up "
                        "the remaining turns. Try rephrasing the request, double-"
                        "check the tool arguments, or use /clear to start fresh."
                    )
                    self._emit_event(
                        AgentEventType.ERROR,
                        {"error": final_text, "stuck_loop": True},
                    )
                    break
            else:
                self._consecutive_failed_tool_turns = 0

            self._emit_event(AgentEventType.TURN_END, {"final": False})

        else:
            # Max turns reached
            if not final_text:
                self.messages.append(
                    {
                        "role": "user",
                        "content": "You've reached the maximum number of turns. Summarize what you've done and what remains.",
                    }
                )
                summary = await self.llm_client.chat_stream(self.messages)
                if summary:
                    final_text = summary.get("content", "(timeout)")

        # 9.8: queued follow-ups become the next user turn. Any queued text
        # is appended as a user message and recorded on final_text so the
        # caller can see the follow-up was accepted (the next run() call
        # continues from the queued message already in history).
        queued = self._take_queue()
        if queued:
            self.messages.append({"role": "user", "content": "\n".join(queued)})
            note = "[queued] " + " | ".join(queued)
            final_text = f"{final_text}\n{note}" if final_text else note
        # Extract long-term memories in the background (extra LLM call)
        # so the user gets the prompt back immediately.
        self._last_extraction_msg_count = len(self.messages)
        task = asyncio.create_task(self._extract_session_memories(user_message))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        self._emit_event(AgentEventType.SESSION_END, {"final_text_length": len(final_text)})
        return final_text

    async def _refresh_memory_context(self, current_query: str) -> bool:
        """Periodically re-check memory for new relevance."""
        if not self._last_memory_refresh_turn:
            return False
        try:
            memory = await asyncio.to_thread(get_memory)
            recent_text = " ".join(
                m.get("content", "")[:200]
                for m in self.messages[-4:]
                if m.get("role") in ("user", "tool")
            )
            query = f"{current_query} {recent_text}"[:500]
            new_context = await asyncio.to_thread(memory.get_context, query, 3)
            if (
                new_context
                and new_context != self._memory_context
                and "(no relevant memories)" not in new_context
            ):
                self._memory_context = new_context
                return True
        except Exception:
            pass
        return False

    def save_session(self) -> str | None:
        """Persist the current conversation to the session store.

        The 9.8 goal payload rides along (top-level ``goal`` key plus a
        ``meta.goal`` mirror) without changing the existing keys, so older
        restores keep working. Returns the file path, or None if empty.
        """
        if not self.messages:
            return None
        try:
            store = get_session_store()
            meta: dict = {"turn_count": self.turn_count}
            goal_data = self.goals.to_dict()
            if goal_data is not None:
                meta["goal"] = goal_data
            path = store.save(
                conversation_id=self.conversation_id,
                messages=self.messages,
                model=self.model,
                provider=self.provider_name,
                meta=meta,
            )
            # Extend the on-disk record with the top-level goal key as well
            # (SessionStore.save has no goal parameter by design).
            if goal_data is not None:
                try:
                    import json as _json

                    record = store.load(self.conversation_id) or {}
                    record["goal"] = goal_data
                    fpath = store._path(self.conversation_id)
                    tmp = fpath.with_suffix(".tmp")
                    tmp.write_text(_json.dumps(record, indent=2, default=str), encoding="utf-8")
                    tmp.replace(fpath)
                except Exception:
                    pass
            return str(path)
        except Exception:
            return None

    def restore_session(self, session: dict) -> None:
        """Restore messages and state from a saved session."""
        if not session:
            return
        self.messages = session.get("messages", [])
        self.conversation_id = session.get("conversation_id", self.conversation_id)
        meta = session.get("meta", {}) or {}
        self.turn_count = meta.get("turn_count", 0)
        if session.get("model"):
            self.model = session["model"]
        # 9.8: goals restore from top-level ``goal`` or the meta mirror;
        # absent on old records -> no goal (backward compatible).
        try:
            restored = goal_from_session(session)
            self.goals.restore(restored.to_dict() if restored else None)
        except Exception:
            pass

    def reset(self):
        """Reset conversation state."""
        self.messages = []
        self.turn_count = 0
        self._no_tool_call_turns = 0
        self._consecutive_failed_tool_turns = 0
        self._last_extraction_msg_count = 0
        self._last_memory_refresh_turn = 0
        self._memory_context = ""
        self.conversation_id = datetime.now(timezone.utc).strftime("conv_%Y%m%d_%H%M%S")
        from core.hooks import reset_hooks

        reset_hooks()
