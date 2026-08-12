"""
Agent loop â€” the main agent execution loop.
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
import json
import os
import time
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config import PROJECT_DIR, get_config
from llm import CostTracker
from memory.store import get_memory
from tools.registry import registry

from .hooks import get_hooks
from .llm_client import LLMClient
from .message_builder import MessageBuilder
from .rules_loader import load_project_rules
from .session_store import get_session_store
from .types import (
    AgentCallbacks,
    AgentEvent,
    AgentEventType,
    HookContext,
)

if TYPE_CHECKING:
    from llm import LLMConfig


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

        # Retry state
        self.max_retries = 3
        self.base_delay = 1.0

        # Track consecutive turns with no tool calls to detect premature stopping
        self._no_tool_call_turns: int = 0
        self._max_no_tool_call_turns: int = 5  # Auto-continue retries before giving up

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

    @property
    def provider_name(self) -> str:
        names = {
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

        # Run before_tool hooks (approval, checkpoint, etc.)
        ctx = HookContext(turn=self.turn_count, messages=self.messages)
        for hook in self.hooks.before_tool:
            try:
                result = await asyncio.to_thread(hook, tool_name, tool_args, ctx)
                if result is not None:
                    return result  # Hook intercepted/blocked the tool
            except Exception as e:
                print(f"\n  [HOOK ERR] before_tool hook failed: {e}")

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
                "content": f"Tool execution timed out after {tool_timeout:.0f}s.",
            }
        except TypeError as e:
            result_text = f"Tool argument error: {e}\nExpected: {json.dumps(tool.parameters)}"
            return {"role": "tool", "tool_call_id": call_id, "content": result_text}
        except Exception as e:
            traceback.print_exc()
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Tool execution error: {e}",
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
        """Simple LLM call for memory extraction â€” non-streaming, minimal retry."""
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

    async def run(self, user_message: str, max_turns: int | None = None) -> str:
        """Run the full agent loop with hooks, events, checkpointing, and memory extraction.

        Conversation history persists across calls so multi-turn conversations work.
        Use reset() (or /clear) to start a fresh conversation.
        """
        max_turns = max_turns or self.max_turns
        self.turn_count = 0

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
        self._emit_event(AgentEventType.SESSION_START, {"message": user_message[:100]})

        final_text = ""
        consecutive_errors = 0

        for turn in range(max_turns):
            self.turn_count = turn + 1
            self._emit_event(AgentEventType.TURN_START, {"turn": self.turn_count})

            # Safety: cap messages to prevent context overflow
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
            tools = registry.openai_tools()
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
                    return final_text or "[ERR] API connection failed after 3 retries."
                continue
            if assistant_msg is None:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self._emit_event(AgentEventType.ERROR, {"error": "API failed after 3 retries"})
                    return final_text or "[ERR] API connection failed after 3 retries."
                continue

            consecutive_errors = 0
            for hook in self.hooks.after_model:
                try:
                    result = hook(assistant_msg, ctx)
                    if result is not None:
                        assistant_msg = result
                except Exception:
                    pass

            # Track token usage for cost display
            usage = assistant_msg.get("_usage", {}) if isinstance(assistant_msg, dict) else {}
            if usage:
                self._cost_tracker.add_usage(usage, self.model)
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

                # Genuine final answer
                self._no_tool_call_turns = 0
                final_text = content
                self._emit_event(AgentEventType.TURN_END, {"final": True})
                break

            # Tool calls present — reset the no-tool-call counter
            self._no_tool_call_turns = 0

            tool_results = []
            hint_messages = []
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    tool_args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_args = {}
                tool_args["_id"] = tc.get("id", f"call_{len(tool_results)}")
                start_time = time.monotonic()
                self._emit_event(AgentEventType.TOOL_START, {"tool": tool_name, "args": tool_args})
                result_msg = await self._execute_tool(tool_name, tool_args)
                elapsed = time.monotonic() - start_time
                content_preview = result_msg["content"][:100].replace("\n", " ")
                is_err = result_msg["content"].startswith("Error")
                status = "[ERR]" if is_err else "[OK]"
                print(f"  {status} [{tool_name}] {elapsed:.1f}s â€” {content_preview}")
                self._emit_event(
                    AgentEventType.TOOL_END if not is_err else AgentEventType.TOOL_ERROR,
                    {"tool": tool_name, "elapsed": elapsed, "error": is_err},
                )
                tool_results.append(result_msg)
                if is_err and turn >= 1:
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
        Returns the file path, or None if no messages to save."""
        if not self.messages:
            return None
        try:
            store = get_session_store()
            return str(
                store.save(
                    conversation_id=self.conversation_id,
                    messages=self.messages,
                    model=self.model,
                    provider=self.provider_name,
                    meta={"turn_count": self.turn_count},
                )
            )
        except Exception:
            return None

    def restore_session(self, session: dict) -> None:
        """Restore messages and state from a saved session."""
        if not session:
            return
        self.messages = session.get("messages", [])
        self.conversation_id = session.get("conversation_id", self.conversation_id)
        self.turn_count = session.get("meta", {}).get("turn_count", 0)
        if session.get("model"):
            self.model = session["model"]

    def reset(self):
        """Reset conversation state."""
        self.messages = []
        self.turn_count = 0
        self._last_extraction_msg_count = 0
        self._last_memory_refresh_turn = 0
        self._memory_context = ""
        self.conversation_id = datetime.now(timezone.utc).strftime("conv_%Y%m%d_%H%M%S")
        from core.hooks import reset_hooks

        reset_hooks()
