"""Google Gemini LLM provider built on the official `google-genai` SDK.

Supports:
  - structured output via Pydantic `response_schema`
  - plain-text completion
  - Google Search grounding (returns answer text + real source URLs/titles)

All Gemini-specific logic lives here so the rest of the swarm stays backend-agnostic.
Modernized: lazy `google-genai` import (optional dep), GOOGLE_API_KEY fallback
via settings, per-role temperature, clearer missing-key error.
"""

from __future__ import annotations

import random
import re
import time
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import settings
from ..runtime.budget import BudgetExhausted, get_budget
from ..schemas import EvidenceCard
from ..tools.search_base import LLMProvider

if TYPE_CHECKING:
    pass

T = TypeVar("T", bound=BaseModel)

# Strip markdown code fences if the model wraps JSON despite schema mode.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE | re.IGNORECASE)


def _is_retryable(e: Exception) -> bool:
    """Retry on rate limits and transient server errors."""
    msg = str(e).lower()
    if "429" in msg or "resource_exhausted" in msg or "rate" in msg:
        return True
    return any(s in msg for s in ("500", "503", "502", "internal", "unavailable"))


class GeminiProvider(LLMProvider):
    provider_name = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.api_key
        if not key:
            raise RuntimeError(
                "No GEMINI_API_KEY (or GOOGLE_API_KEY) set. Set it in .env, "
                "set DRS_PROVIDER=luckyd to use LuckyD's local/multi-provider "
                "backend instead, or use the mock provider for offline tests."
            )
        try:
            from google import genai
        except Exception as e:
            raise RuntimeError(
                "google-genai is not installed. Run: pip install google-genai>=1.0.0 " f"({e})"
            ) from e
        self._client = genai.Client(api_key=key)
        self._call_counts: dict[str, int] = {}

    # -- internal helpers -------------------------------------------------
    def _model(self, role: str) -> str:
        return settings.model_for(role)

    def _cfg(self, system: str, temperature: float | None, schema: type[BaseModel] | None):
        from google.genai import types as gtypes

        kwargs: dict = {
            "system_instruction": system or None,
            "temperature": settings.temp_for("worker") if temperature is None else temperature,
        }
        # NOTE: preserves upstream behavior (worker temp default); per-role
        # temps are applied by callers passing temperature explicitly.
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        return gtypes.GenerateContentConfig(**kwargs)

    @staticmethod
    def _coerce_json(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            text = _FENCE.sub("", text).strip()
        # Some models prepend prose; grab the first {...} or [...] block.
        if not text.startswith(("{", "[")):
            m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if m:
                text = m.group(1)
        return text

    def _generate(self, role: str, system: str, user: str, cfg):
        self._call_counts[role] = self._call_counts.get(role, 0) + 1
        get_budget().record_llm()
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                return self._client.models.generate_content(
                    model=self._model(role), contents=user, config=cfg
                )
            except BudgetExhausted:
                raise
            except Exception as e:
                last_err = e
                if not _is_retryable(e) or attempt == 3:
                    break
                # exponential backoff with jitter for 429/5xx
                sleep = (2**attempt) + random.uniform(0, 1.0)
                time.sleep(min(sleep, 30.0))
        raise RuntimeError(f"Gemini call failed for role '{role}': {last_err}")

    # -- public API -------------------------------------------------------
    def structured(
        self,
        role: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = None,
    ) -> T:
        from google.genai import types as gtypes

        cfg = self._cfg(system, temperature, schema)
        resp = self._generate(role, system, user, cfg)
        raw = resp.text or ""
        cleaned = self._coerce_json(raw)
        try:
            return schema.model_validate_json(cleaned)
        except ValidationError:
            # One repair pass: route through _generate for budget tracking +
            # retry, asking the model to return strict JSON only.
            repair = self._generate(
                role,
                "Return ONLY valid JSON matching the requested schema.",
                f"Input: {cleaned[:4000]}",
                gtypes.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            return schema.model_validate_json(self._coerce_json(repair.text or ""))

    def text(
        self,
        role: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        cfg = self._cfg(system, temperature, None)
        resp = self._generate(role, system, user, cfg)
        return resp.text or ""

    def grounded(self, role: str, system: str, user: str) -> tuple[str, list[EvidenceCard]]:
        from google.genai import types as gtypes

        cfg = gtypes.GenerateContentConfig(
            system_instruction=system or None,
            temperature=settings.temp_for(role),
            tools=[gtypes.Tool(google_search=gtypes.GoogleSearch())],
        )
        resp = self._generate(role, system, user, cfg)
        text = resp.text or ""

        evidence: list[EvidenceCard] = []
        try:
            md = resp.candidates[0].grounding_metadata
            chunks = getattr(md, "grounding_chunks", None) or []
            supports = getattr(md, "grounding_supports", None) or []

            # Collect grounded text segments per chunk index for richer snippets.
            snippets: dict[int, list[str]] = {}
            for sup in supports:
                seg = getattr(sup, "segment", None)
                seg_text = getattr(seg, "text", None) if seg else None
                idxs = getattr(sup, "grounding_chunk_indices", None) or []
                if seg_text:
                    for ci in idxs:
                        snippets.setdefault(ci, []).append(seg_text)

            for i, ch in enumerate(chunks):
                web = getattr(ch, "web", None)
                if not web or not getattr(web, "uri", None):
                    continue
                snippet = " ... ".join(snippets.get(i, [])[:3])[:600]
                evidence.append(
                    EvidenceCard(
                        id=f"e{i}",
                        url=web.uri,
                        title=getattr(web, "title", "") or "",
                        snippet=snippet,
                        source_query=user[:120],
                        worker_id=role,
                        confidence=0.8,
                    )
                )
        except Exception:
            pass
        return text, evidence

    @property
    def call_counts(self) -> dict[str, int]:
        return dict(self._call_counts)
