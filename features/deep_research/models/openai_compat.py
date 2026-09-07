"""OpenAI-compatible free-model backend for Deep Research.

Speaks plain ``POST {base_url}/chat/completions`` via sync httpx, so it runs
against any OpenAI-compatible gateway without extra SDKs (frozen-safe):

- ``opencode``   — OpenCode Zen $0 gateway (https://opencode.ai/zen/v1).
                  Verified working 2026-09-06: ``nemotron-3-ultra-free``
                  (flagship, strong reasoning) + ``nemotron-3.5-lightning-free``
                  (fast). ``ling-3.0-flash-fin-free`` answers plain prompts but
                  rejects ``response_format=json_object``, so structured calls
                  auto-retry without it.
- ``openrouter`` — OpenRouter (https://openrouter.ai/api/v1) ``:free`` models.
- ``ollama``     — local Ollama (http://127.0.0.1:11434/v1), free/unlimited.

Role routing (override with DRS_MODEL_<ROLE>, e.g. DRS_MODEL_PLANNER):
- planner / critic  -> flagship model (strongest reasoning/verification)
- worker / synthesizer / verifier -> fast model (high-volume JSON + drafting)

Resilience: per-model rotation across the backend's free pool on
401-model-unsupported / 429 / 5xx, and a json_mode fallback retry for models
that reject ``response_format``. Sync httpx is deliberate: the swarm's
``LLMProvider`` interface is synchronous and runs inside ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..tools.search_base import LLMProvider

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE | re.IGNORECASE)

# Verified-live free pools (probed against the real gateways, 2026-09-06).
# First entry = flagship (planner/critic), second = fast (worker/synthesizer).
OPENCODE_FREE_POOL = [
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "ling-3.0-flash-fin-free",
]
OPENROUTER_FREE_POOL = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "openrouter/free",
]
OLLAMA_FREE_POOL = ["llama3.2:3b"]

_BACKEND_DEFAULTS = {
    "opencode": {
        "base_url": "https://opencode.ai/zen/v1",
        "key_env": "OPENCODE_API_KEY",
        "model_env": "DRS_OPENCODE_MODEL",
        "pool": OPENCODE_FREE_POOL,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "DRS_OPENROUTER_MODEL",
        "pool": OPENROUTER_FREE_POOL,
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "key_env": "",
        "model_env": "DRS_OLLAMA_MODEL",
        "pool": OLLAMA_FREE_POOL,
    },
}


def _coerce_json(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    if not text.startswith(("{", "[")):
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            text = m.group(1)
    return text


def _is_rotation_error(status: int, body: str) -> bool:
    if status in (429, 500, 502, 503, 504):
        return True
    if status in (400, 401, 404):
        low = (body or "").lower()
        return any(
            s in low
            for s in (
                "not supported",
                "not found",
                "no longer",
                "unavailable",
                "rate limit",
                "overload",
            )
        )
    return False


class OpenAICompatProvider(LLMProvider):
    """LLMProvider over any OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        backend: str = "opencode",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_sec: float = 120.0,
    ) -> None:
        from ..config import settings as drs_settings

        self._settings = drs_settings
        backend = (backend or "opencode").lower().strip()
        if backend in ("local", "ollama-local"):
            backend = "ollama"
        if backend not in _BACKEND_DEFAULTS:
            backend = "opencode"
        self._backend = backend
        self._spec = _BACKEND_DEFAULTS[backend]
        self._model_override = model
        self._key_override = api_key
        self._base_override = base_url
        self._timeout = timeout_sec
        self._resolved: dict | None = None
        self._call_counts: dict[str, int] = {}

    @property
    def provider_name(self) -> str:
        return self._backend

    # -- config ---------------------------------------------------------
    def _pool(self) -> list[str]:
        """Free-model rotation pool: explicit env model first, then verified pool."""
        env_model = (os.getenv(self._spec["model_env"], "") or "").strip()
        pool = list(self._spec["pool"])
        if env_model and env_model not in pool:
            return [env_model, *pool]
        if env_model in pool:
            pool.remove(env_model)
            return [env_model, *pool]
        return pool

    def _model_for_role(self, role: str) -> str:
        # Explicit per-role override (DRS_MODEL_PLANNER etc.) wins — except a
        # legacy gemini id pointed at a free gateway (it doesn't exist there;
        # fall through to the verified pool instead of burning a call).
        try:
            override = (os.getenv(f"DRS_MODEL_{role.upper()}", "") or "").strip()
        except Exception:
            override = ""
        if (
            override
            and self._backend in ("opencode", "openrouter", "ollama")
            and (override.startswith("gemini-") or override.startswith("gemini/"))
        ):
            override = ""
        if override:
            return override
        pool = self._pool()
        if role in ("planner", "critic"):
            return pool[0]
        if len(pool) > 1:
            return pool[1]
        return pool[0]

    def _cfg(self) -> dict:
        if self._resolved is not None:
            return self._resolved
        key = ""
        if self._key_override is not None:
            key = self._key_override
        elif self._spec["key_env"]:
            key = os.getenv(self._spec["key_env"], "") or ""
            if not key and self._backend == "opencode":
                # Fall back to whatever LuckyD core resolved (OPENAI_BASE_URL
                # often points at the Zen gateway with its own key).
                try:
                    from core.providers import resolve_provider_config

                    for candidate in ("opencode", "openai"):
                        try:
                            cfg = resolve_provider_config(candidate)
                        except Exception:
                            continue
                        if "opencode.ai" in str(cfg.get("base_url", "")):
                            key = str(cfg.get("api_key", "") or "")
                            break
                except Exception:
                    pass
        base = (
            self._base_override
            or os.getenv(f"DRS_{self._backend.upper()}_BASE_URL", "").strip()
            or self._spec["base_url"]
        )
        if self._backend == "opencode" and not self._base_override:
            try:
                from core.providers import resolve_provider_config

                cfg = resolve_provider_config("opencode")
                if cfg.get("base_url"):
                    base = str(cfg["base_url"])
            except Exception:
                pass
        if self._backend == "ollama" and not self._base_override:
            host = (
                os.getenv("OLLAMA_HOST", "").strip() or os.getenv("DRS_OLLAMA_BASE_URL", "").strip()
            )
            if host:
                host = host.rstrip("/")
                base = host if host.endswith("/v1") else host + "/v1"
        self._resolved = {
            "api_key": key.strip(),
            "base_url": base.rstrip("/"),
            "backend": self._backend,
            "model": self._model_override or "",
        }
        return self._resolved

    @property
    def model_name(self) -> str:
        cfg = self._cfg()
        return str(cfg.get("model") or self._pool()[0])

    def _temperature(self, role: str, override: float | None) -> float:
        if override is not None:
            return override
        try:
            return float(self._settings.temp_for(role))
        except Exception:
            return 0.2

    # -- transport ------------------------------------------------------
    def _post(self, url: str, headers: dict, payload: dict):
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            return client.post(url, headers=headers, json=payload)

    def _chat(self, messages: list[dict], temperature: float, json_mode: bool, role: str) -> str:
        from ..runtime.budget import get_budget

        cfg = self._cfg()
        url = f"{cfg['base_url']}/chat/completions"
        headers = {"Content-Type": "application/json"}
        key = str(cfg.get("api_key", "") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # Extra headers recommended by OpenRouter; harmless elsewhere.
        headers.setdefault("HTTP-Referer", "https://github.com/luckyd-code")
        headers.setdefault("X-Title", "LuckyD Deep Research Swarm")

        pool = self._pool()
        # Role-preferred model first, then the rest of the pool.
        preferred = self._model_for_role(role) if not self._model_override else self._model_override
        ordered = [preferred, *[m for m in pool if m != preferred]]
        # The configured/resolved single model (LuckyD mirror) gets a slot too.
        if cfg.get("model") and cfg["model"] not in ordered:
            ordered.insert(1, str(cfg["model"]))

        get_budget().record_llm()
        last_err: Exception | None = None
        try:
            import httpx  # noqa: F401 — fail fast with a clear error when missing
        except Exception as e:
            raise RuntimeError(f"httpx is required for {self._backend} provider: {e}") from e

        for model in ordered:
            payload: dict = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            attempts = [{"json": json_mode}, {"json": False}] if json_mode else [{"json": False}]
            for attempt in attempts:
                body = dict(payload)
                if attempt["json"]:
                    body["response_format"] = {"type": "json_object"}
                try:
                    resp = self._post(url, headers, body)
                except Exception as e:
                    last_err = e
                    if "connect" in str(e).lower():
                        time.sleep(1.0)
                    continue
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception as e:
                        last_err = e
                        break  # malformed 200: try next model, not next attempt
                    choices = data.get("choices", []) if isinstance(data, dict) else []
                    if not choices:
                        last_err = RuntimeError(f"empty choices from {model}: {str(data)[:200]}")
                        break
                    msg = choices[0].get("message", {}) or {}
                    content = msg.get("content", "") or ""
                    if isinstance(content, list):
                        content = "".join(
                            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                        )
                    text = str(content).strip()
                    if text:
                        self._resolved = {**cfg, "model": model}
                        return text
                    last_err = RuntimeError(f"empty content from {model}")
                    break
                snippet = (resp.text or "")[:300]
                # json_mode rejected (some free models 400 on response_format):
                # retry the SAME model without it before rotating.
                if resp.status_code == 400 and attempt["json"]:
                    continue
                last_err = RuntimeError(
                    f"{self._backend} {model} HTTP {resp.status_code}: {snippet}"
                )
                if _is_rotation_error(resp.status_code, snippet):
                    break  # rotate to next model
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5)
                    break
                return self._raise_or_suggest(last_err)
        raise RuntimeError(
            f"{self._backend} free-model pool exhausted (tried {ordered}): {last_err}"
        )

    def _raise_or_suggest(self, err: Exception):
        raise RuntimeError(str(err))

    # -- LLMProvider API -------------------------------------------------
    def text(self, role: str, system: str, user: str, temperature: float | None = None) -> str:
        temp = self._temperature(role, temperature)
        messages = [
            {"role": "system", "content": system or "You are a research assistant."},
            {"role": "user", "content": user},
        ]
        return self._chat(messages, temp, json_mode=False, role=role)

    def structured(
        self,
        role: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = None,
    ) -> T:
        temp = self._temperature(role, temperature)
        try:
            json_schema = schema.model_json_schema()
        except Exception:
            json_schema = {}
        sys_prompt = (
            (system or "")
            + "\n\nReturn ONLY valid JSON matching this JSON Schema. "
            + "No prose, no markdown fences.\n"
            + json.dumps(json_schema)[:4000]
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ]
        raw = self._chat(messages, temp, json_mode=True, role=role)
        cleaned = _coerce_json(raw)
        try:
            return schema.model_validate_json(cleaned)
        except ValidationError:
            repair_messages = [
                {
                    "role": "system",
                    "content": "Return ONLY valid JSON matching the requested schema.",
                },
                {"role": "user", "content": f"Fix this into valid JSON:\n{cleaned[:4000]}"},
            ]
            raw2 = self._chat(repair_messages, 0.0, json_mode=True, role=role)
            return schema.model_validate_json(_coerce_json(raw2))

    def grounded(self, role: str, system: str, user: str) -> tuple[str, list]:
        """Keyless grounding: DDG search cards + a drafted answer."""
        from ..runtime.budget import get_budget
        from ..schemas import EvidenceCard

        cards: list[EvidenceCard] = []
        try:
            from ..tools.search_ddg import DDGSearch

            get_budget().record_search()
            cards = DDGSearch().search(user[:300], max_results=8)
        except Exception:
            cards = []
        if not cards:
            return "", []
        context = "\n".join(f"- {c.title} ({c.url}): {c.snippet[:250]}" for c in cards[:8])
        answer_user = (
            f"Question: {user[:500]}\n\nWeb results:\n{context}\n\n"
            "Write a 3-5 sentence grounded answer using ONLY these results."
        )
        try:
            answer = self.text(role, system, answer_user)
        except Exception:
            answer = ""
        return answer, cards

    @property
    def call_counts(self) -> dict[str, int]:
        return dict(self._call_counts)
