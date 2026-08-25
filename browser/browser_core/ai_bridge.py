"""Self-contained AI bridge: speaks to multiple LLM providers via httpx directly.

No dependence on the repo's `llm/` package â€” reads keys from the repo .env
(or process env). Local keyless servers (Ollama, LM Studio â€” free, unlimited,
offline) are auto-detected FIRST and need no API key at all; cloud providers
(Google Gemini free tier, Groq free tier, Z.ai, OpenRouter, DeepSeek, OpenAI,
Anthropic) act as optional boosters further down the fallback chain.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx
from browser_core import cline_session

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_HAS_STREAM_END = re.compile(r"\[DONE\]")

_PROVIDER_SPECS = [
    (
        "google",
        "GOOGLE_API_KEY",
        "gemini-3-flash-preview",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini",
    ),
    ("groq", "GROQ_API_KEY", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1", "openai"),
    ("zai", "ZAI_API_KEY", "glm-4.5-flash", "https://api.z.ai/api/paas/v4", "openai"),
    # OpenRouter free pool rotates â€” `:free` ids are $0. Defaulting free here so
    # the browser sidebar never silently spends credits: nvidia nemotron-3 ultra
    # 550b-a55b (free), or google/gemma-4-31b-it:free, nvidia/nemotron-3.5-lightning:free
    (
        "openrouter",
        "OPENROUTER_API_KEY",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "https://openrouter.ai/api/v1",
        "openai",
    ),
    (
        "deepseek",
        "DEEPSEEK_API_KEY",
        "deepseek-v4-flash",
        "https://api.deepseek.com",
        "openai",
    ),
    ("openai", "OPENAI_API_KEY", "gpt-4o", "https://api.openai.com/v1", "openai"),
    (
        "anthropic",
        "ANTHROPIC_API_KEY",
        "claude-sonnet-4-20250514",
        "https://api.anthropic.com/v1",
        "anthropic",
    ),
]

# Keyless local servers â€” probed at startup, registered before keyed clouds.
# Any OpenAI-compatible /v1 endpoint works (Ollama, LM Studio, llama.cppâ€¦).
# (name, host env override, default host, model env override)
_LOCAL_SPECS = [
    ("ollama", "OLLAMA_HOST", "http://127.0.0.1:11434", "OLLAMA_MODEL"),
    ("lmstudio", "LMSTUDIO_HOST", "http://127.0.0.1:1234", ""),
]

# Preferred local chat/agent models â€” first substring match in the server's
# installed-model list wins; otherwise the first installed model is used.
# Small CPU-friendly models rank first: most machines have no GPU.
_LOCAL_MODEL_PREF = (
    "llama3.2",
    "qwen3",
    "gemma3",
    "phi4",
    "qwen2.5",
    "llama3.3",
    "llama3.1",
    "mistral",
    "deepseek",
    "gpt-oss",
)

# Model-name families that accept image input. Vision-off is the safe
# default for unknown models: the agent then skips screenshots instead of
# dying with an HTTP 400 mid-task.
_VISION_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-5",
    "gemini",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "gemma3",
    "gemma-3",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "llama3.2-vision",
    "pixtral",
    "glm-4v",
    "glm-4.5v",
    "kimi-vl",
    "internvl",
    "phi-3-vision",
    "phi-3.5-vision",
    "phi-4-multimodal",
    "minimax-m",
)
# Substrings that veto a hint match (text-only variants of vision lines).
_VISION_EXCLUDE = ("gemma3:1b", "gemma-3-1b")

# ClinePass (Cline flat-subscription gateway) â€” OpenAI-compatible.
# Auth: CLINEPASS_API_KEY from .env, else the logged-in Cline CLI session.
_CLINEPASS_BASE = "https://api.cline.bot/api/v1"
_CLINEPASS_MODEL = "cline-pass/deepseek-v4-pro"

# Curated fallback for api.cline.bot â€” the gateway has no public model
# catalog endpoint (only /chat/completions). Sources: ClinePass docs model
# table (subscription) + Cline API docs (credit-billed), 2026-07.
_CLINEPASS_CATALOG = [
    # â”€â”€ Included in the ClinePass flat subscription â€” these work with a
    # $0 (even negative) credit balance; usage counts against the sub quota.
    "cline-pass/kimi-k3",
    "cline-pass/deepseek-v4-flash",  # fast + cheapest â€” great agent model
    "cline-pass/kimi-k2.7-code",
    "cline-pass/kimi-k2.6",
    "cline-pass/deepseek-v4-pro",
    "cline-pass/mimo-v2.5",
    "cline-pass/mimo-v2.5-pro",
    "cline-pass/minimax-m3",
    "cline-pass/qwen3.7-max",
    "cline-pass/qwen3.7-plus",
]

# Cline Usage (credit-billed / free tier) â€” same gateway, usage-based billing.
# Free-tier models work at $0.00 but are rate-limited; credit models deduct
# from your Cline Credits balance.   Sources: Cline API docs, 2026-07.
_CLINE_USAGE_MODEL = "deepseek/deepseek-chat"
_CLINE_USAGE_CATALOG = [
    # â”€â”€ Free tier (rate-limited, $0.00 â€” needs non-negative credit balance)
    "minimax/minimax-m2.5",
    "deepseek/deepseek-chat",  # DeepSeek V3 â€” fast general model
    "deepseek/deepseek-r1",  # DeepSeek R1 â€” reasoning model
    "meta-llama/llama-3.2-3b-instruct",  # Small Llama, quick responses
    "google/gemini-2.0-flash",  # Google Gemini free tier
    "qwen/qwen3-8b",  # Qwen 3 small, CPU-friendly
    # â”€â”€ Credit-billed â€” deduct from Cline Credits balance
    "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "mistral/mistral-large",
]


def _env_paths() -> list[Path]:
    """Every .env location to read, in priority order (first hit wins per key).

    Dev: the repo-root .env (ENV_PATH). Frozen onedir: this module's __file__
    lives under _internal, so ENV_PATH resolves to <install>\\.env beside the
    exe â€” while the .env the installer actually ships (and the one users are
    told to put real keys into) is _internal\\.env == sys._MEIPASS/.env.
    Reading all candidates means keys added to _internal\\.env in an installed
    app actually reach the AI bridge instead of being silently ignored.
    """
    paths = [ENV_PATH]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / ".env")
    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / ".env")
    return paths


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for path in _env_paths():
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except Exception:
        pass
    for key, value in os.environ.items():
        env[key] = value
    return env


class AIBridge:
    def __init__(self) -> None:
        env = _load_env()
        self._configs: dict[str, tuple[str, str, str, str]] = {}
        self._model_cache: dict[str, list[str]] = {}
        self._clinepass_from_session = False
        # Local keyless servers first: free + unlimited should be the default.
        self._configs.update(self._detect_local(env))
        # Remember which providers are keyless locals (used by
        # default_provider; _LOCAL_SPECS itself may change under tests).
        self._local_names = {name for name, *_ in _LOCAL_SPECS}
        # Cline/ClinePass removed 2026-08-23 at user's request â€” Google and
        # OpenRouter cover the free tier now. self._detect_clinepass(env) is
        # intentionally not called; _CLINEPASS_* constants stay defined below
        # only because fetch_models() references them as a catalog fallback
        # if a provider ever gets manually re-added.
        self._clinepass_from_session = False
        for name, key_name, model, base_url, kind in _PROVIDER_SPECS:
            key = env.get(key_name, "").strip()
            if not key:
                continue
            # Per-provider overrides, e.g. DEEPSEEK_MODEL=deepseek-v4-pro or
            # OPENAI_BASE_URL=https://opencode.ai/zen/v1 (repointing the
            # "openai" slot at a different OpenAI-compatible gateway/free
            # model). Previously only the model override was read here, so
            # a repointed base_url silently kept hitting the hardcoded
            # default host with the wrong provider's key -> guaranteed 401
            # and, for a specifically-selected provider, no fallback (the
            # fallback chain only kicks in on "auto").
            prefix = key_name.removesuffix("_API_KEY")
            model = env.get(f"{prefix}_MODEL", "").strip() or model
            base_url = env.get(f"{prefix}_BASE_URL", "").strip() or base_url
            self._configs[name] = (model, base_url, key, kind)

    def _detect_clinepass(self, env) -> None:
        """Register ClinePass subscription + Cline Usage (credit-billed/free tier).

        Both share the same auth: CLINEPASS_API_KEY from .env, else the
        logged-in Cline CLI session.  The session path registers whenever a
        login exists on disk â€” even with every access token expired.  Tokens
        are refreshed via the stored WorkOS refresh token lazily before each
        call (off the UI thread), so ClinePass and Cline Usage stay available
        with the Cline terminal closed.

        When no session exists, we still register with an empty token so that
        fetch_models() can fall back to the curated catalog.
        """
        token = env.get("CLINEPASS_API_KEY", "").strip()
        if not token and cline_session.has_session():
            self._clinepass_from_session = True
            try:
                token = cline_session.stored_token()
            except RuntimeError:
                token = ""  # expired now â€” refreshed lazily before each call
        # else: no session, token stays "" â€” catalog fallback will work
        base = env.get("CLINEPASS_BASE_URL", "").strip() or _CLINEPASS_BASE

        # â”€â”€ ClinePass (flat subscription) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        model = env.get("CLINEPASS_MODEL", "").strip() or _CLINEPASS_MODEL
        self._configs["clinepass"] = (model, base, token, "openai")

        # â”€â”€ Cline Usage (credit-billed / free tier) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        usage_model = env.get("CLINE_USAGE_MODEL", "").strip() or _CLINE_USAGE_MODEL
        self._configs["cline-usage"] = (usage_model, base, token, "openai")

    @staticmethod
    def _detect_local(env) -> dict[str, tuple[str, str, str, str]]:
        """Probe local OpenAI-compatible servers; returns keyless configs."""
        found: dict[str, tuple[str, str, str, str]] = {}
        for name, host_var, default_host, model_var in _LOCAL_SPECS:
            host = (env.get(host_var, "").strip() or default_host).rstrip("/")
            base_url = host if host.endswith("/v1") else host + "/v1"
            try:
                resp = httpx.get(base_url + "/models", timeout=1.5)
                resp.raise_for_status()
                models = [m.get("id", "") for m in resp.json().get("data", [])]
            except Exception:
                continue
            models = [m for m in models if m and "embed" not in m.lower()]
            if not models:
                continue
            model = env.get(model_var, "").strip() if model_var else ""
            if model not in models:
                model = next(
                    (m for pref in _LOCAL_MODEL_PREF for m in models if pref in m.lower()),
                    models[0],
                )
            found[name] = (model, base_url, "", "openai")
        return found

    def providers(self) -> list[str]:
        return list(self._configs)

    def default_provider(self) -> str | None:
        """Return the highest-priority available provider.

        Preference order (matches this module's docstring):
          1. Local keyless servers (Ollama, LM Studio) â€” free, unlimited,
             offline, no key or login needed
          2. cline-usage â€” Cline free tier, when auth actually exists
             (API key or a logged-in Cline CLI session)
          3. Cloud keyed providers, in _PROVIDER_SPECS order
        """
        for name in self._local_names:
            if name in self._configs:
                return name
        if "cline-usage" in self._configs and self._cline_usable():
            return "cline-usage"
        return next(iter(self._configs), None)

    def _cline_usable(self) -> bool:
        """True when Cline auth exists (API key or logged-in CLI session).

        clinepass / cline-usage are registered even with an empty token (for
        the model-catalog fallback), so presence alone is not proof of auth.
        """
        if self._clinepass_from_session:
            return True
        info = self._configs.get("cline-usage") or self._configs.get("clinepass")
        return bool(info and info[2])

    def model_for(self, provider: str) -> str:
        info = self._configs.get(provider)
        return info[0] if info else ""

    def supports_vision(self, provider: str | None) -> bool:
        """True when the provider's current model accepts image input.

        Name-family heuristics (_VISION_HINTS); unknown models get False
        so screenshots are never sent to a text-only endpoint.
        """
        model = self.model_for(provider).lower()
        if not model or any(x in model for x in _VISION_EXCLUDE):
            return False
        return any(h in model for h in _VISION_HINTS)

    def is_local(self, provider: str) -> bool:
        """True for keyless local servers (callers may shrink token budgets)."""
        info = self._configs.get(provider)
        # Key check alone is not enough: session-based clinepass can hold an
        # empty placeholder token while it waits for a lazy refresh.
        return bool(info) and info[2] == "" and provider in {name for name, *_ in _LOCAL_SPECS}

    def set_model_override(self, provider: str, model: str) -> None:
        """Switch a provider's model at runtime (sidebar model picker)."""
        info = self._configs.get(provider)
        if info is None or not model.strip():
            return
        self._configs[provider] = (model.strip(), info[1], info[2], info[3])

    def fetch_models(self, provider: str) -> list[str]:
        """Model ids available on a provider â€” live catalog when it has one
        (Ollama, LM Studio, most OpenAI-compatible hosts), curated fallback
        for ClinePass / Cline Usage (gateway exposes no catalog), current
        model otherwise."""
        info = self._configs.get(provider)
        if info is None:
            return []
        if provider in self._model_cache:
            return self._model_cache[provider]
        model, base_url, api_key, kind = info
        models: list[str] = []
        if kind == "openai":
            key = api_key
            if provider in ("clinepass", "cline-usage") and self._clinepass_from_session:
                try:
                    key = cline_session.fresh_token()
                except RuntimeError:
                    key = ""
            key = (key or "").strip()
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            try:
                resp = httpx.get(base_url + "/models", headers=headers, timeout=5.0)
                resp.raise_for_status()
                models = [
                    m["id"]
                    for m in resp.json().get("data", [])
                    if m.get("id") and "embed" not in m["id"].lower()
                ]
            except Exception:
                models = []
        if not models:
            if provider == "clinepass":
                models = list(_CLINEPASS_CATALOG)
            elif provider == "cline-usage":
                models = list(_CLINE_USAGE_CATALOG)
            else:
                models = [model]
        if model in models:
            models.remove(model)
        models.insert(0, model)  # current model first
        self._model_cache[provider] = models
        return models

    async def chat(self, messages, provider=None, on_token=None):
        order = [provider] if provider else self.providers()
        last_err: Exception | None = None
        for name in order:
            info = self._configs.get(name)
            if info is None:
                continue
            if name in ("clinepass", "cline-usage") and self._clinepass_from_session:
                # Session tokens live ~1h â€” re-read before every call;
                # fresh_token() refreshes via WorkOS when they expire.
                try:
                    token = cline_session.fresh_token()
                    info = (info[0], info[1], token, info[3])
                    self._configs[name] = info
                except RuntimeError as exc:
                    last_err = exc
                    continue
            try:
                text = await self._call(name, info, messages, on_token)
                return text, name
            except Exception as exc:
                last_err = exc
        if last_err is not None:
            raise RuntimeError(f"all providers failed â€” last error: {last_err}")
        raise RuntimeError(
            "no AI providers configured â€” install Ollama and run "
            "`ollama pull qwen3:4b`, or add cloud keys to the repo .env"
        )

    async def _call(self, name, info, messages, on_token):
        model, base_url, api_key, kind = info
        body = (
            self._body_gemini(messages)
            if kind == "gemini"
            else (
                self._body_anthropic(messages)
                if kind == "anthropic"
                else self._body_openai(messages)
            )
        )
        body["model"] = model
        headers = {"User-Agent": "LuckyDBrowser/1.0"}

        if kind == "gemini":
            url = f"{base_url}/models/{model}:streamGenerateContent?key={api_key}&alt=sse"
        elif kind == "anthropic":
            url = f"{base_url}/messages"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            url = f"{base_url}/chat/completions"
            key = (api_key or "").strip()
            if key:
                # An EMPTY / whitespace-only Authorization header ('Bearer ' /
                # 'Bearer  ') makes h11 raise "Illegal header value b'Bearer '"
                # at send time â€” never send it without a real key.
                headers["Authorization"] = f"Bearer {key}"

        text = ""
        # Local keyless models can be slow on CPU â€” allow a longer first token.
        timeout = 300.0 if not api_key else 60.0
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("POST", url, json=body, headers=headers) as resp,
        ):
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode(errors="replace")[:300]
                raise RuntimeError(f"{name} HTTP {resp.status_code}: {detail}")
            buf = ""
            async for chunk in resp.aiter_bytes():
                buf += chunk.decode(errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line or _HAS_STREAM_END.search(line):
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        data = json.loads(line)
                        delta = self._extract_delta(data, kind)
                        if delta:
                            text += delta
                            if on_token:
                                on_token(delta)
                    except json.JSONDecodeError:
                        pass
        return text

    @staticmethod
    def _text_of(content) -> str:
        """Plain-text view of a message content (str or multimodal parts)."""
        if isinstance(content, str):
            return content
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )

    @staticmethod
    def _parse_data_url(url: str) -> tuple[str, str] | None:
        """data:image/jpeg;base64,XXXX -> (mime, b64), else None."""
        if url.startswith("data:") and ";base64," in url:
            mime, _, data = url[5:].partition(";base64,")
            if mime and data:
                return mime, data
        return None

    @staticmethod
    def _parts_gemini(content) -> list:
        """Gemini parts from str or OpenAI-style multimodal content."""
        if isinstance(content, str):
            return [{"text": content}]
        parts = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                parts.append({"text": p.get("text", "")})
            elif p.get("type") == "image_url":
                img = AIBridge._parse_data_url(p.get("image_url", {}).get("url", ""))
                if img:
                    parts.append({"inlineData": {"mimeType": img[0], "data": img[1]}})
        return parts or [{"text": ""}]

    @staticmethod
    def _parts_anthropic(content):
        """Anthropic content from str or OpenAI-style multimodal content."""
        if isinstance(content, str):
            return content
        parts = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                parts.append({"type": "text", "text": p.get("text", "")})
            elif p.get("type") == "image_url":
                img = AIBridge._parse_data_url(p.get("image_url", {}).get("url", ""))
                if img:
                    parts.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img[0],
                                "data": img[1],
                            },
                        }
                    )
        return parts or ""

    @staticmethod
    def _body_gemini(messages):
        parts, system = [], ""
        for m in messages:
            if m["role"] == "system":
                system += AIBridge._text_of(m["content"]) + "\n"
            else:
                r = "user" if m["role"] in ("user", "tool") else "model"
                parts.append({"role": r, "parts": AIBridge._parts_gemini(m["content"])})
        body = {"contents": parts}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system.strip()}]}
        return body

    @staticmethod
    def _body_anthropic(messages):
        sys_text, normal = "", []
        for m in messages:
            if m["role"] == "system":
                sys_text += AIBridge._text_of(m["content"]) + "\n"
            else:
                normal.append(
                    {
                        "role": "user" if m["role"] == "user" else "assistant",
                        "content": AIBridge._parts_anthropic(m["content"]),
                    }
                )
        body = {"messages": normal, "max_tokens": 4096}
        if sys_text:
            body["system"] = sys_text.strip()
        return body

    @staticmethod
    def _body_openai(messages):
        out = []
        for m in messages:
            r = m["role"]
            out.append(
                {
                    "role": r if r in ("user", "assistant", "system") else "user",
                    "content": m["content"],
                }
            )
        return {"messages": out, "stream": True}

    @staticmethod
    def _extract_delta(data, kind):
        if kind == "gemini":
            c = data.get("candidates", [])
            if c:
                parts = c[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts)
        if kind == "anthropic":
            d = data.get("delta", {})
            t = d.get("text", "")
            if t:
                return t
            content = data.get("content", [])
            return "".join(b.get("text", "") for b in content if b.get("type") == "text")
        choices = data.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            text = delta.get("content", "")
            if text:
                return text
            msg = choices[0].get("message", {})
            return msg.get("content", "")
        return ""
