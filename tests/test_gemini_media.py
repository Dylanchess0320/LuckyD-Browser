"""Tests for browser/models/gemini_media.py — the google.genai SDK is faked, no network."""

from __future__ import annotations

import sys
import types

import pytest

from browser.models import gemini_media
from browser.models.gemini_media import GeminiMediaError, generate_image, generate_video

# ── fakes ────────────────────────────────────────────────────────────────


class _FakeInline:
    def __init__(self, data: bytes):
        self.data = data
        self.mime_type = "image/png"


class _FakePart:
    def __init__(self, data: bytes | None):
        self.inline_data = _FakeInline(data) if data is not None else None


class _FakeContent:
    def __init__(self, parts):
        self.parts = parts


class _FakeCandidate:
    def __init__(self, parts):
        self.content = _FakeContent(parts)


class _FakeGenResponse:
    def __init__(self, image_bytes: bytes | None):
        self.candidates = [_FakeCandidate([_FakePart(image_bytes)])]
        self.text = "" if image_bytes else "I cannot make images."


class _FakeVideoFile:
    name = "files/video123"


class _FakeGeneratedVideo:
    def __init__(self):
        self.video = _FakeVideoFile()


class _FakeVideosResponse:
    def __init__(self):
        self.generated_videos = [_FakeGeneratedVideo()]


class _FakeOperation:
    def __init__(self):
        self.done = True
        self.error = None
        self.response = _FakeVideosResponse()


class _FakeModels:
    def __init__(self, image_bytes: bytes | None = b"\x89PNG"):
        self._image_bytes = image_bytes
        self.last_image_model = None
        self.last_video_model = None

    def generate_content(self, *, model, contents, config=None):
        self.last_image_model = model
        return _FakeGenResponse(self._image_bytes)

    def generate_videos(self, *, model, prompt=None, **kw):
        self.last_video_model = model
        return _FakeOperation()


class _FakeFiles:
    def __init__(self):
        self.downloaded_to = None

    def download(self, *, file, destination=None, config=None):
        self.downloaded_to = destination
        with open(destination, "wb") as f:
            f.write(b"FAKEVIDEO")
        return None


class _FakeClient:
    def __init__(self, image_bytes: bytes | None = b"\x89PNG"):
        self.models = _FakeModels(image_bytes)
        self.files = _FakeFiles()
        self.operations = types.SimpleNamespace(get=lambda op: op)


def _install_fake(monkeypatch, image_bytes: bytes | None = b"\x89PNG"):
    fake_client = _FakeClient(image_bytes)
    fake_genai = types.SimpleNamespace(Client=lambda api_key=None: fake_client)
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    return fake_client


# ── tests ────────────────────────────────────────────────────────────────


def test_generate_image_writes_png(tmp_path, monkeypatch):
    client = _install_fake(monkeypatch)
    out = tmp_path / "pic.png"
    result = generate_image("a red rocket", out)
    assert result == out
    assert out.read_bytes() == b"\x89PNG"
    assert client.models.last_image_model == gemini_media.IMAGE_MODEL


def test_generate_image_empty_prompt_rejected(tmp_path, monkeypatch):
    _install_fake(monkeypatch)
    with pytest.raises(GeminiMediaError, match="empty"):
        generate_image("   ", tmp_path / "x.png")


def test_generate_image_no_image_part_errors(tmp_path, monkeypatch):
    _install_fake(monkeypatch, image_bytes=None)
    with pytest.raises(GeminiMediaError, match="didn't return an image"):
        generate_image("a red rocket", tmp_path / "x.png")


def test_generate_image_missing_key(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiMediaError, match="GOOGLE_API_KEY"):
        generate_image("a red rocket", "/tmp/x.png")


def test_generate_video_downloads_file(tmp_path, monkeypatch):
    client = _install_fake(monkeypatch)
    out = tmp_path / "clip.mp4"
    result = generate_video("a rocket launch", out, poll_s=0)
    assert result == out
    assert out.read_bytes() == b"FAKEVIDEO"
    assert client.models.last_video_model == gemini_media.VIDEO_MODEL
    assert client.files.downloaded_to == str(out)


def test_generate_video_empty_prompt_rejected(tmp_path, monkeypatch):
    _install_fake(monkeypatch)
    with pytest.raises(GeminiMediaError, match="empty"):
        generate_video("", tmp_path / "x.mp4")
