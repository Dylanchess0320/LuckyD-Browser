"""cline_bridge.py — local OpenAI-compatible shim for the ClinePass gateway.

Purpose
-------
The frozen LuckyD Code host app (LuckyDBrowser) speaks standard OpenAI-style
`POST /v1/chat/completions` (both streaming and non-streaming). The ClinePass
gateway at https://api.cline.bot/api/v1 is *almost* OpenAI-compatible, but it
has two quirks this shim absorbs:

  1. Auth: it needs a fresh WorkOS access token pulled from the local Cline CLI
     login (no paid API key required). We reuse `cline_session.fresh_token()`.
  2. Envelope: non-streaming responses come wrapped as
     ``{"data": {...openai...}, "success": true}``. We unwrap to the inner
     OpenAI object so the host app's parser sees a normal response.

Streaming responses from the gateway are passed straight through as SSE
(`data: {json}` / `data: [DONE]`). If the gateway ever streams the wrapped
envelope, we unwrap each chunk on the fly.

Endpoints
---------
  GET  /v1/health            -> {"ok": true, "model": ..., "token_len": N}
  GET  /v1/models            -> OpenAI models list (known cline-pass ids)
  POST /v1/chat/completions  -> OpenAI chat completion (stream + non-stream)

Run
---
  uvicorn cline_bridge:app --host 127.0.0.1 --port 8317

Then point the host app at it:
  set CODING_AGENT_BASE_URL=http://127.0.0.1:8317/v1
  set CODING_AGENT_API_KEY=local          (any non-empty value; not checked)
  set CODING_AGENT_MODEL=cline-pass/kimi-k3
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# --------------------------------------------------------------------------
# Token source: reuse the existing cline_session helper from the source tree.
# --------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
for _cand in (_HERE / "browser" / "browser_core", _HERE):
    if (_cand / "cline_session.py").exists():
        sys.path.insert(0, str(_cand))
        break

import cline_session

UPSTREAM_BASE = os.environ.get("CLINE_BRIDGE_UPSTREAM", "https://api.cline.bot/api/v1")
DEFAULT_MODEL = os.environ.get("CLINE_BRIDGE_MODEL", "cline-pass/kimi-k3")

# Model ids verified live against the gateway (others 404 — dead slugs).
KNOWN_MODELS = [
    "cline-pass/kimi-k3",
    "cline-pass/deepseek-v4-flash",
]

app = FastAPI(title="ClinePass local bridge", version="1.0.0")


def _unwrap(payload: Any) -> Any:
    """Unwrap the cline.bot {"data": {...}, "success": true} envelope."""
    if isinstance(payload, dict) and "data" in payload and "choices" not in payload:
        inner = payload.get("data")
        if isinstance(inner, dict) and "choices" in inner:
            return inner
    return payload


@app.get("/v1/health")
async def health() -> JSONResponse:
    try:
        tok = cline_session.fresh_token()
        return JSONResponse({"ok": True, "model": DEFAULT_MODEL, "token_len": len(tok)})
    except Exception as e:  # pragma: no cover - diagnostic path
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.get("/v1/models")
async def models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "cline-pass"}
                for m in KNOWN_MODELS
            ],
        }
    )


def _forward_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cline_session.fresh_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "invalid JSON body"}}, status_code=400)

    body.setdefault("model", DEFAULT_MODEL)
    stream = bool(body.get("stream"))
    url = f"{UPSTREAM_BASE}/chat/completions"
    headers = _forward_headers()

    if not stream:
        return await _non_stream(url, headers, body)
    return await _stream(url, headers, body)


async def _non_stream(url: str, headers: dict, body: dict) -> JSONResponse:
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=15.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
    except Exception as e:
        return JSONResponse(
            {"error": {"message": f"upstream connection failed: {type(e).__name__}: {e}"}},
            status_code=502,
        )

    if resp.status_code != 200:
        return JSONResponse(
            {"error": {"message": f"upstream {resp.status_code}", "detail": resp.text[:500]}},
            status_code=resp.status_code,
        )

    try:
        payload = resp.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "upstream returned non-JSON", "detail": resp.text[:500]}},
            status_code=502,
        )

    return JSONResponse(_unwrap(payload))


async def _stream(url: str, headers: dict, body: dict) -> StreamingResponse:
    body["stream"] = True
    timeout = httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=5.0)

    async def gen() -> AsyncIterator[bytes]:
        client = httpx.AsyncClient(timeout=timeout)
        try:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    yield f"data: {json.dumps({'error': {'message': f'upstream {resp.status_code}', 'detail': err.decode(errors='replace')[:400]}})}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return

                buffer = ""
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield b"data: [DONE]\n\n"
                        continue

                    # Pass through normal OpenAI chunks verbatim. If the chunk
                    # is the wrapped envelope, buffer until we can unwrap it.
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        buffer += data
                        try:
                            obj = json.loads(buffer)
                            buffer = ""
                        except json.JSONDecodeError:
                            continue

                    if isinstance(obj, dict) and "choices" in obj:
                        yield f"data: {json.dumps(obj)}\n\n".encode()
                    else:
                        unwrapped = _unwrap(obj)
                        if isinstance(unwrapped, dict) and "choices" in unwrapped:
                            yield f"data: {json.dumps(unwrapped)}\n\n".encode()
        except Exception as e:  # pragma: no cover - network failure path
            yield f"data: {json.dumps({'error': {'message': f'bridge stream error: {type(e).__name__}: {e}'}})}\n\n".encode()
            yield b"data: [DONE]\n\n"
        finally:
            await client.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CLINE_BRIDGE_PORT", "8317"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
