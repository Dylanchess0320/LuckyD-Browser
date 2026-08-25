"""Baseline + regression check: core.LLMClient.chat_stream on OpenRouter FREE.

Exercises the EXACT streaming path the agent uses:
    url = f"{self.base_url}/chat/completions"
"""
import asyncio
import sys

sys.path.insert(0, ".")
import config  # loads .env
from core.providers import resolve_provider_config
from core.llm_client import LLMClient
from core.context_manager import ContextManager

cfg = resolve_provider_config("openrouter")
print("base_url   :", cfg["base_url"])
print("model      :", cfg["model"])
print("have_scheme:", cfg["base_url"].startswith(("http://", "https://")))


async def main():
    cm = ContextManager()
    client = LLMClient(
        cfg["api_key"],
        cfg["base_url"],
        cfg["model"],
        context_manager=cm,
                max_tokens=256,
    )
    out: list[str] = []

    def on_token(t: str):
        out.append(t)

    def on_think(t: str):
        pass

    msg = await client.chat_stream(
        [{"role": "user", "content": "Reply with the single number 42."}],
        stream_callback=on_token,
        think_callback=on_think,
    )
    print("streamed:", repr("".join(out)))
    print("final    :", repr((msg or {}).get("content")) if msg else None)
    print("RESULT   :", "OK" if "42" in "".join(out) or "42" in str(msg) else "NO-42")


asyncio.run(main())
