"""Image + video generation through the Gemini API (Google AI Pro companion).

Dylan's Google AI Pro plan covers the Gemini *app*; LuckyD talks to the
Gemini *API*, which has its own free tier and pay-as-you-go billing:

- Image generation ("Nano Banana", ``gemini-2.5-flash-image``) works with a
  plain free API key from https://aistudio.google.com/apikey
- Video generation (Veo) needs a **billing-enabled** API key — every
  generation bills his Google Cloud project per second of video. The module
  says so loudly before spending anything.

Both entry points are synchronous, lazy-import ``google-genai`` (optional
dependency), and raise :class:`GeminiMediaError` with a plain-English
message on any failure.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

IMAGE_MODEL = "gemini-2.5-flash-image"  # "Nano Banana" — fast image gen/edit
IMAGE_MODEL_PRO = "gemini-3-pro-image-preview"  # "Nano Banana Pro" — higher quality
VIDEO_MODEL = "veo-3.1-generate-preview"  # Veo 3.1 — needs billing
VIDEO_MODEL_FAST = "veo-3.0-fast-generate-001"  # cheaper/faster Veo 3


class GeminiMediaError(RuntimeError):
    """Raised when image/video generation fails, with a human-readable reason."""


def _api_key(explicit: str | None = None) -> str:
    key = (
        explicit or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
    ).strip()
    if not key:
        raise GeminiMediaError(
            "No GOOGLE_API_KEY set. Get a free key at https://aistudio.google.com/apikey "
            "and put it in .env as GOOGLE_API_KEY=..."
        )
    return key


def _client(api_key: str | None = None):
    try:
        from google import genai
    except Exception as e:
        raise GeminiMediaError(
            f"google-genai is not installed. Run: pip install google-genai ({e})"
        ) from e
    return genai.Client(api_key=_api_key(api_key))


def _friendly_error(e: Exception) -> GeminiMediaError:
    msg = str(e)
    low = msg.lower()
    if "billing" in low or "billing_enabled" in low:
        return GeminiMediaError(
            "This needs a billing-enabled API key (Google Cloud billing turned on for "
            f"the project). The API said: {msg}"
        )
    if "api_key" in low and (
        "invalid" in low or "not valid" in low or "401" in low or "400" in low
    ):
        return GeminiMediaError(
            f"Your GOOGLE_API_KEY was rejected. Check it at https://aistudio.google.com/apikey — {msg}"
        )
    if "quota" in low or "429" in low or "resource_exhausted" in low:
        return GeminiMediaError(f"Free-tier quota hit — wait a bit and retry. The API said: {msg}")
    return GeminiMediaError(f"Generation failed: {msg}")


def generate_image(
    prompt: str,
    out_path: str | os.PathLike,
    model: str = IMAGE_MODEL,
    api_key: str | None = None,
) -> Path:
    """Generate an image with Nano Banana and save it to ``out_path``.

    Returns the path written. Works on the free API tier.
    """
    if not (prompt or "").strip():
        raise GeminiMediaError("Prompt is empty — describe the image you want.")
    client = _client(api_key)
    try:
        response = client.models.generate_content(model=model, contents=prompt.strip())
    except Exception as e:
        raise _friendly_error(e) from e
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, str):
                    import base64

                    data = base64.b64decode(data)
                path = Path(out_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return path
    # No image part — the model may have answered in text instead.
    text = ""
    with contextlib.suppress(Exception):
        text = (response.text or "").strip()[:300]
    raise GeminiMediaError(
        "The model didn't return an image for that prompt." + (f" It said: {text}" if text else "")
    )


def generate_video(
    prompt: str,
    out_path: str | os.PathLike,
    model: str = VIDEO_MODEL,
    api_key: str | None = None,
    poll_s: float = 10.0,
    timeout_s: float = 900.0,
) -> Path:
    """Generate a video with Veo and save it to ``out_path``.

    **This bills his Google Cloud project** — Veo is pay-per-generation even
    on the free API tier. Only call this when Dylan asked for a video.
    Blocks (polling) until the video is ready or ``timeout_s`` elapses.
    """
    if not (prompt or "").strip():
        raise GeminiMediaError("Prompt is empty — describe the video you want.")
    client = _client(api_key)
    try:
        operation = client.models.generate_videos(model=model, prompt=prompt.strip())
    except Exception as e:
        raise _friendly_error(e) from e
    deadline = time.monotonic() + timeout_s
    while not operation.done:
        if time.monotonic() > deadline:
            raise GeminiMediaError(
                f"Video generation timed out after {timeout_s:.0f}s — try again later."
            )
        time.sleep(poll_s)
        try:
            operation = client.operations.get(operation)
        except Exception as e:
            raise _friendly_error(e) from e
    if getattr(operation, "error", None):
        raise _friendly_error(RuntimeError(str(operation.error)))
    videos = (operation.response.generated_videos if operation.response else None) or []
    if not videos:
        raise GeminiMediaError(
            "Veo finished but returned no video (it may have been filtered — try a different prompt)."
        )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.files.download(file=videos[0].video, destination=str(path))
    except Exception as e:
        raise _friendly_error(e) from e
    return path
