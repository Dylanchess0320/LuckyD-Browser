"""Verify the LIVE SOURCE OpenRouter path end-to-end (not the frozen exe).

Replicates core/llm_client.chat_stream's exact URL build:
    url = f"{self.base_url}/chat/completions"
"""
import sys

sys.path.insert(0, ".")
import config  # triggers .env load
from core.providers import resolve_provider_config
import httpx

cfg = resolve_provider_config("openrouter")
print("base_url   :", cfg["base_url"])
print("model      :", cfg["model"])
print("have_scheme:", cfg["base_url"].startswith(("http://", "https://")))

url = f"{cfg['base_url']}/chat/completions"
body = {
    "model": cfg["model"],
    "messages": [{"role": "user", "content": "Reply with the single number 42."}],
    "max_tokens": 16,
    "temperature": 0,
}
headers = {
    "Authorization": f"Bearer {cfg['api_key']}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://luckyd.local",
    "X-Title": "LuckyD-Code",
}

try:
    r = httpx.post(url, headers=headers, json=body, timeout=60)
    print("HTTP", r.status_code)
    print("body:", r.text[:800])
    try:
        data = r.json()
    except Exception:
        data = {}
    print("keys:", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
    content = ""
    if isinstance(data, dict):
        ch = data.get("choices") or []
        if ch:
            content = (ch[0].get("message") or {}).get("content", "")
        elif data.get("error"):
            content = "<server error: " + str(data["error"]) + ">"
    print("content:", repr(content))
    print("RESULT:", "OK" if "42" in content else "NO-42")
except Exception as e:
    print("EXC:", type(e).__name__, e)
