"""LuckyD multi-provider LLM backend for Deep Research.

Wraps any LuckyD-configured provider (Ollama local free, DeepSeek, OpenAI,
OpenRouter, OpenCode Zen, Gemini via OpenAI-compat, ...) using synchronous
OpenAI-compatible ``/chat/completions`` calls.

- :meth:`text` — plain chat completion.
- :meth:`structured` — JSON-only prompting + robust coercion into a Pydantic
  schema (works even on small local models; one repair pass on failure).
- :meth:`grounded` — keyless web grounding via DDG search (URL+snippet cards)
  + a short answer draft. Gemini-native grounding lives in
  :mod:`features.deep_research.models.gemini`; this backend is the offline /
  multi-provider workhorse.

Sync httpx is used deliberately: the swarm's ``LLMProvider`` interface is
synchronous and runs inside ``asyncio.to_thread`` workers, so a blocking
client is correct here (no nested event loops).
"""

from __future__ import annotations

import json
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..tools.search_base import LLMProvider

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE | re.IGNORECASE)


def _coerce_json(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    if not text.startswith(("{", "[")):
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            text = m.group(1)
    return text


def _is_retryable_status(code: int) -> bool:
    return code in (429, 500, 502, 503, 504)


class LuckyDProvider(LLMProvider):
    """LLMProvider backed by LuckyD's resolved provider config."""

    provider_name = "luckyd"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_sec: float = 120.0,
    ) -> None:
        from ..config import settings as drs_settings

        self._settings = drs_settings
        self._model_override = model
        self._key_override = api_key
        self._base_override = base_url
        self._timeout = timeout_sec
        self._resolved: dict | None = None
        self._call_counts: dict[str, int] = {}

    # -- config ---------------------------------------------------------
    def _cfg(self) -> dict:
        if self._resolved is not None:
            return self._resolved
        try:
            from core.providers import resolve_provider_config

            resolved = resolve_provider_config()
        except Exception:
            resolved = {
                "api_key": "",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "llama3.2:3b",
                "provider": "ollama",
            }
        if self._model_override:
            resolved["model"] = self._model_override
        if self._key_override is not None:
            resolved["api_key"] = self._key_override
        if self._base_override:
            resolved["base_url"] = self._base_override
        self._resolved = resolved
        return resolved

    @property
    def model_name(self) -> str:
        return str(self._cfg().get("model", ""))

    def _temperature(self, role: str, override: float | None) -> float:
        if override is not None:
            return override
        try:
            return float(self._settings.temp_for(role))
        except Exception:
            return 0.2

    # -- transport ------------------------------------------------------
    def _chat(self, messages: list[dict], temperature: float, json_mode: bool) -> str:
        from ..runtime.budget import get_budget

        cfg = self._cfg()
        base = str(cfg.get("base_url", "")).rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        key = str(cfg.get("api_key", "") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict = {
            "model": cfg.get("model", ""),
            "messages": messages,
            "temperature": temperature,
        }
        # Most OpenAI-compat gateways (incl. Ollama) accept this; harmless
        # if ignored by stricter providers.
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        get_budget().record_llm()
        last_err: Exception | None = None
        try:
            import httpx
        except Exception as e:
            raise RuntimeError(f"httpx is required for LuckyDProvider: {e}") from e

        for attempt in range(3):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(min(2**attempt + 0.5, 10.0))
                    continue
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError(f"empty choices from {cfg.get('provider')}")
                msg = choices[0].get("message", {})
                content = msg.get("content", "") or ""
                # Some providers return content as list of parts.
                if isinstance(content, list):
                    content = "".join(
                        p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                    )
                return str(content)
            except Exception as e:
                last_err = e
                status = getattr(getattr(e, "response", None), "status_code", 0)
                if isinstance(status, int) and _is_retryable_status(status) and attempt < 2:
                    time.sleep(min(2**attempt + 0.5, 10.0))
                    continue
                # Non-retryable or last attempt: wait a beat then retry once
                # for transient connection resets.
                if attempt < 2 and "connect" in str(e).lower():
                    time.sleep(1.0)
                    continue
                if attempt >= 2:
                    break
        raise RuntimeError(
            f"LuckyDProvider chat failed (provider={cfg.get('provider')} "
            f"model={cfg.get('model')}): {last_err}"
        )

    # -- LLMProvider API -------------------------------------------------
    def text(self, role: str, system: str, user: str, temperature: float | None = None) -> str:
        temp = self._temperature(role, temperature)
        messages = [
            {"role": "system", "content": system or "You are a research assistant."},
            {"role": "user", "content": user},
        ]
        return self._chat(messages, temp, json_mode=False)

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
        raw = self._chat(messages, temp, json_mode=True)
        cleaned = _coerce_json(raw)
        try:
            return schema.model_validate_json(cleaned)
        except ValidationError:
            # One repair pass: ask for strict JSON only.
            repair_messages = [
                {
                    "role": "system",
                    "content": "Return ONLY valid JSON matching the requested schema.",
                },
                {"role": "user", "content": f"Fix this into valid JSON:\n{cleaned[:4000]}"},
            ]
            raw2 = self._chat(repair_messages, 0.0, json_mode=True)
            return schema.model_validate_json(_coerce_json(raw2))

    def grounded(self, role: str, system: str, user: str) -> tuple[str, list]:
        """Keyless grounding: DDG search cards + a drafted answer."""
        from ..runtime.budget import get_budget
        from ..schemas import EvidenceCard

        # Reuse the swarm's DDG backend (now supports ddgs + duckduckgo_search).
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
