"""LuckyD bridge provider — deep research on the AI assistant's providers.

The assistant (browser/browser_core/ai_bridge.py) owns the provider chain:
keyless locals (Ollama / LM Studio) first, then the Cline gateways
(ClinePass subscription + Cline Usage free/credit tiers, authenticated from
the logged-in Cline CLI session), then keyed clouds from .env. Since 9.8 the
deep-research swarm no longer carries its own OpenCode Zen backend — it now
borrows the assistant's resolved provider/model via the bridge handoff:
``AIBridge.provider_config(name)`` + ``AIBridge.call_sync(...)``.

Design notes:
- The swarm's ``LLMProvider`` interface is synchronous (planner/auditor run
  inside ``asyncio.to_thread``), so the provider calls the bridge's sync
  ``call_sync`` — never the async ``chat()`` (no event loop in a worker).
- Rotation: the configured bridge model is tried first; Cline gateways then
  rotate through the bridge's curated top models (the same pool the chat
  path uses). The bridge pins the winning model for the rest of the run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Import the assistant's bridge. The deep_research package lives under
# features/, the bridge under browser/browser_core — make both module search
# paths visible regardless of which package imported us first.
_PROJECT = Path(__file__).resolve().parents[2]
for _p in (_PROJECT / "browser", str(_PROJECT)):
    if str(_p) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(_p))

try:
    from browser_core import ai_bridge as _ai_bridge
except ImportError:  # imported as browser.browser_core.ai_bridge
    from browser.browser_core import ai_bridge as _ai_bridge

from features.deep_research.models.luckyd import LuckyDProvider

__all__ = ["LuckyDBridgeProvider", "default_bridge_provider"]


def _is_rotation_error(status: int, body: str) -> bool:
    """True when the failure is worth rotating to the next model."""
    if status in (429, 500, 502, 503, 504):
        return True
    low = (body or "").lower()
    return any(
        token in low for token in ("rate limit", "quota", "overloaded", "try again", "timeout")
    )


def default_bridge_provider() -> str | None:
    """The assistant's current default provider (or None when unconfigured)."""
    return _ai_bridge.AIBridge().default_provider()


class LuckyDBridgeProvider(LuckyDProvider):
    """Same settings/mirroring/role-temperature behaviour as LuckyDProvider,
    but every call is served by the AI assistant's connected provider.

    ``provider`` names one of the assistant's registered providers (see
    ``AIBridge.providers()``): "ollama"/"lmstudio" (keyless locals),
    "clinepass"/"cline-usage" (Cline gateways), or a keyed cloud. ``None``
    picks the assistant's default — the same provider the sidebar chat uses.
    """

    def __init__(self, provider: str | None = None) -> None:
        super().__init__()
        self._bridge = _ai_bridge.AIBridge()
        self._bridge_provider = provider or self._bridge.default_provider()
        self.provider_name = self._bridge_provider or "unconfigured"
        cfg = self._bridge.provider_config(self._bridge_provider or "")
        if cfg is None:
            raise RuntimeError(
                "no AI provider available for deep research — set up the AI "
                "assistant (locals, a Cline session, or a provider key in .env)"
            )
        # Rotation hint for roles without an explicit override: stay on the
        # assistant's configured model unless the caller forces one.
        self._model_override = ""

    # -- cfg / pool -------------------------------------------------------
    def _cfg(self) -> dict:
        cfg = self._bridge.provider_config(self._bridge_provider or "")
        if cfg is None:
            raise RuntimeError(
                f"provider {self._bridge_provider!r} is no longer registered with the AI assistant"
            )
        base = str(cfg.get("base_url", ""))
        kind = str(cfg.get("kind", ""))
        if kind != "openai":
            # The swarm speaks OpenAI-compatible /chat/completions only.
            # Gemini has a first-class OpenAI-compat surface; keep its key
            # usable instead of failing on the native kind.
            if self._bridge_provider == "google":
                base = "https://generativelanguage.googleapis.com/v1beta/openai"
            else:
                raise RuntimeError(
                    f"provider {self._bridge_provider!r} ({kind}) is not "
                    "OpenAI-compatible; deep research can't use it directly"
                )
        return {
            "model": str(cfg.get("model", "")),
            "base_url": base,
            "api_key": str(cfg.get("api_key", "") or ""),
            "kind": kind,
        }

    def _pool(self) -> list[str]:
        # No second config file to fall back to — the bridge already owns
        # model rotation (Cline gateway top models). The bridge's curated
        # pool doubles as the picker suggestions in the research UI.
        return []

    def _model_for_role(self, role: str) -> str:
        """Role-preferred model: explicit DRS_MODEL_<ROLE> wins, else bridge model."""
        try:
            override = (os.getenv(f"DRS_MODEL_{role.upper()}", "") or "").strip()
        except Exception:
            override = ""
        if override:
            return override
        try:
            return str(self._cfg().get("model", ""))
        except Exception:
            return ""

    # -- HTTP -------------------------------------------------------------
    def _post(self, url: str, headers: dict, payload: dict):
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            return client.post(url, headers=headers, json=payload)

    def _chat(
        self,
        messages: list[dict],
        temperature: float,
        json_mode: bool,
        role: str = "worker",
    ) -> str:
        from ..runtime.budget import get_budget

        cfg = self._cfg()
        url = f"{str(cfg.get('base_url', '')).rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        key = str(cfg.get("api_key", "") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        pool = self._pool()
        # Role-preferred model first, then the rest of the pool.
        preferred = self._model_for_role(role) if not self._model_override else self._model_override
        ordered = [preferred, *[m for m in pool if m != preferred]]
        # The configured/resolved single model (bridge mirror) gets a slot too.
        cfg_model = str(cfg.get("model", "") or "")
        if cfg_model and cfg_model not in ordered:
            ordered.insert(1, cfg_model)
        ordered = [m for m in ordered if m]

        get_budget().record_llm()
        last_err: Exception | None = None
        for model in ordered:
            payload: dict = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            attempts: list[dict[str, bool]] = (
                [{"json": json_mode}, {"json": False}] if json_mode else [{"json": False}]
            )
            for attempt in attempts:
                body = dict(payload)
                if attempt.get("json", False):
                    body["response_format"] = {"type": "json_object"}
                try:
                    resp = self._post(url, headers, body)
                except Exception as e:
                    last_err = e
                    if "connect" in str(e).lower():
                        import time

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
                if resp.status_code == 400 and attempt.get("json", False):
                    continue
                last_err = RuntimeError(
                    f"{self.provider_name} {model} HTTP {resp.status_code}: {snippet}"
                )
                if _is_rotation_error(resp.status_code, snippet):
                    break  # rotate to next model
                if resp.status_code in (429, 500, 502, 503, 504):
                    import time

                    time.sleep(1.5)
                    break
                return self._raise_or_suggest(last_err)
        # Bridge handoff: give the assistant's sync chat path one shot too —
        # it rotates through the curated Cline gateway pool and pins the
        # winning model for the rest of the run.
        try:
            text = self._bridge.call_sync(
                messages,
                provider=self._bridge_provider,
                model=None,
                temperature=temperature,
            )
            get_budget().record_llm()
            self._resolved = {**cfg, "model": str(cfg.get("model", ""))}
            return text
        except Exception as exc:
            raise RuntimeError(
                f"{self.provider_name} research pool exhausted "
                f"(tried {ordered}): {last_err}; bridge rotation: {exc}"
            ) from exc

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
        schema: Any,
        temperature: float | None = None,
    ) -> Any:
        import json as _json

        temp = self._temperature(role, temperature)
        try:
            json_schema = schema.model_json_schema()
        except Exception:
            json_schema = {}
        sys_prompt = (
            (system or "")
            + "\n\nReturn ONLY valid JSON matching this JSON Schema. "
            + "No prose, no markdown fences.\n"
            + _json.dumps(json_schema)[:4000]
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ]
        raw = self._chat(messages, temp, json_mode=True, role=role)
        return self._coerce_and_validate(raw, schema)

    def _coerce_and_validate(self, raw: str, schema: Any) -> Any:
        from features.deep_research.models.luckyd import _coerce_json

        cleaned = _coerce_json(raw)
        try:
            return schema.model_validate_json(cleaned)
        except Exception:
            repair_messages = [
                {
                    "role": "system",
                    "content": "Return ONLY valid JSON matching the requested schema.",
                },
                {"role": "user", "content": f"Fix this into valid JSON:\n{cleaned[:4000]}"},
            ]
            raw2 = self._chat(repair_messages, 0.0, json_mode=True, role="worker")
            return schema.model_validate_json(_coerce_json(raw2))

    def _raise_or_suggest(self, err: Exception) -> Any:
        raise RuntimeError(str(err))
