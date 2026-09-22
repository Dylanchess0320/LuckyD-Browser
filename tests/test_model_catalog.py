"""Tests for the model_catalog logic in main.py."""

from __future__ import annotations

from main import model_catalog


def test_model_catalog_free_only_filters_missing_keys(monkeypatch):
    """Test that model_catalog(free_only=True) correctly uses os.environ to filter out providers without keys."""

    # Clear all keys first to ensure a clean state
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("CLINEPASS_API_KEY", raising=False)

    # 1. Test when no keys are present
    free_catalog_no_keys = model_catalog(free_only=True)

    assert len(free_catalog_no_keys) == 1
    free_section = free_catalog_no_keys[0]
    assert free_section["tier"] == "free"

    groups = free_section["groups"]
    provider_labels = [g["provider"] for g in groups]

    # "Ollama ✓" (local, no key needed) should be present
    assert any("Ollama" in label and "✓" in label for label in provider_labels)
    # Cline requires CLINEPASS_API_KEY, which is deleted, so it won't be available here.

    # Providers requiring keys should NOT have "✓" and should be completely excluded because free_only=True
    assert not any("OpenRouter" in label for label in provider_labels)
    assert not any("Google" in label for label in provider_labels)

    # 2. Test when a key is present
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-openrouter-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "sk-fake-google-key")

    free_catalog_with_keys = model_catalog(free_only=True)
    free_section_with_keys = free_catalog_with_keys[0]
    groups_with_keys = free_section_with_keys["groups"]
    provider_labels_with_keys = [g["provider"] for g in groups_with_keys]

    # Ollama should still be there
    assert any("Ollama" in label and "✓" in label for label in provider_labels_with_keys)

    # OpenRouter and Google should now be included and marked as available ("✓")
    assert any("OpenRouter ✓" in label for label in provider_labels_with_keys)
    assert any("Google Gemini ✓" in label for label in provider_labels_with_keys)

    # Other keyed providers without keys in env should still be excluded
    assert not any("Groq" in label for label in provider_labels_with_keys)
