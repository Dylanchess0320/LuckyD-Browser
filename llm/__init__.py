"""Multi-LLM provider system — core types, cost tracking, and provider factory."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

# ── Configuration ────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 8192
    thinking: bool = False
    provider: str = "deepseek"

    @classmethod
    def from_env(cls) -> LLMConfig:
        """Auto-detect provider and config from environment variables."""
        # Check for explicit provider override
        explicit = os.environ.get("CODING_AGENT_PROVIDER", "").lower()

        if explicit == "openai" or (not explicit and os.environ.get("OPENAI_API_KEY")):
            return cls(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                provider="openai",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "anthropic" or (not explicit and os.environ.get("ANTHROPIC_API_KEY")):
            return cls(
                api_key=os.environ["ANTHROPIC_API_KEY"],
                base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
                model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                provider="anthropic",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "google" or (not explicit and os.environ.get("GOOGLE_API_KEY")):
            return cls(
                api_key=os.environ["GOOGLE_API_KEY"],
                base_url=os.environ.get(
                    "GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
                ),
                model=os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash"),
                provider="google",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "ollama" or (not explicit and os.environ.get("OLLAMA_MODEL")):
            return cls(
                base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                model=os.environ.get("OLLAMA_MODEL", "codellama"),
                provider="ollama",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "zai" or (not explicit and os.environ.get("ZAI_API_KEY")):
            return cls(
                api_key=os.environ["ZAI_API_KEY"],
                base_url=os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4"),
                model=os.environ.get("ZAI_MODEL", "glm-4.5"),
                provider="zai",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "openrouter" or (not explicit and os.environ.get("OPENROUTER_API_KEY")):
            return cls(
                api_key=os.environ["OPENROUTER_API_KEY"],
                base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                model=os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3.1"),
                provider="openrouter",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "clinepass" or (not explicit and os.environ.get("CLINEPASS_API_KEY")):
            from core.providers import resolve_provider_config

            cfg = resolve_provider_config("clinepass")
            return cls(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                provider="clinepass",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )
        if explicit == "cline-usage":
            from core.providers import resolve_provider_config

            cfg = resolve_provider_config("cline-usage")
            return cls(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                provider="cline-usage",
                temperature=float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
                max_tokens=int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
            )

        # Fallback: DeepSeek (original default)
        from config import get_config

        cfg = get_config()
        return cls(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url", "https://api.deepseek.com/v1"),
            model=cfg.get("model", "deepseek-chat"),
            provider="deepseek",
            temperature=cfg.get("temperature", 0.0),
            max_tokens=cfg.get("max_tokens", 8192),
        )


# ── Common result types ──────────────────────────────────────────────


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[dict] | None = None
    model: str = ""
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    thinking: str = ""

    def get(self, key: str, default=None):
        """Dict-like access for compatibility with code expecting dict responses."""
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        """Convert to a dict suitable for message history."""
        msg: dict = {"role": "assistant"}
        if self.content:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


# ── Cost tracking ────────────────────────────────────────────────────

MODEL_COSTS: dict[str, dict[str, float]] = {
    # DeepSeek
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "o1-mini": {"input": 1.10, "output": 4.40},
    "o1-preview": {"input": 15.00, "output": 60.00},
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    # Google
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.0-pro": {"input": 2.00, "output": 8.00},
    # Z.ai GLM (reference, verify on-platform)
    "glm-4.6": {"input": 0.60, "output": 2.20},
    "glm-4.5": {"input": 0.60, "output": 2.20},
    "glm-4.5-air": {"input": 0.20, "output": 1.10},
}


class CostTracker:
    """Tracks token usage and costs across a session."""

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self._model = ""

    def add_usage(self, usage: dict, model: str = ""):
        self._model = model or self._model
        inp = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        out = usage.get("completion_tokens", usage.get("output_tokens", 0))
        self.total_input_tokens += inp
        self.total_output_tokens += out

        costs = MODEL_COSTS.get(self._model, {})
        if costs:
            self.total_cost += (inp / 1_000_000) * costs.get("input", 0)
            self.total_cost += (out / 1_000_000) * costs.get("output", 0)

    def summary(self) -> str:
        cost_str = f"${self.total_cost:.4f}" if self.total_cost > 0 else "free"
        return f"Model: {self._model} | Tokens: {self.total_input_tokens:,} in / {self.total_output_tokens:,} out | Cost: {cost_str}"

    def reset(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cost": round(self.total_cost, 6),
            "model": self._model,
        }


# ── Base client ──────────────────────────────────────────────────────


class LLMClient(ABC):
    """Abstract base for all LLM providers."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.cost_tracker = CostTracker()

    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult: ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_think: Callable[[str], None] | None = None,
    ) -> LLMResult: ...

    @staticmethod
    def create(config: LLMConfig | None = None) -> LLMClient:
        """Factory: create the right client for the config."""
        if config is None:
            config = LLMConfig.from_env()

        if config.provider == "openai":
            from .openai_client import OpenAIClient

            return OpenAIClient(config)
        elif config.provider == "anthropic":
            from .anthropic_client import AnthropicClient

            return AnthropicClient(config)
        elif config.provider == "google":
            from .google_client import GoogleClient

            return GoogleClient(config)
        elif config.provider == "ollama":
            from .ollama_client import OllamaClient

            return OllamaClient(config)
        elif config.provider in ("zai", "openrouter", "clinepass", "cline-usage"):
            from .openai_client import OpenAIClient

            return OpenAIClient(config)
        else:
            from .deepseek_client import DeepSeekClient

            return DeepSeekClient(config)


# ── Provider Router ──────────────────────────────────────────────────


class ProviderRouter:
    """Routes between providers. Allows mid-session switching."""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()
        self._client: LLMClient | None = None

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient.create(self.config)
        return self._client

    def switch(self, config: LLMConfig):
        self.config = config
        self._client = LLMClient.create(config)

    @property
    def cost_tracker(self) -> CostTracker:
        return self.client.cost_tracker

    async def chat(self, messages, tools=None) -> LLMResult:
        return await self.client.chat(messages, tools)

    async def chat_stream(self, messages, tools=None, on_token=None, on_think=None) -> LLMResult:
        return await self.client.chat_stream(messages, tools, on_token, on_think)
